"""Command settings.

The read-only server never calls this module. `Settings.load` leaves every
command field at its safe default, so a bad value in the shared config.env
cannot stop `tesla-mcp`. This loader is what `tesla-commands-mcp` uses.
Invalid values do not raise. They come back as `command_config_error`, and
the commands server refuses every command. Fleet, token, and authorize URLs
are checked in `Settings.load`, which both servers call first. A host that is
not the exact endpoint for that setting and region raises there, before this
function runs.
"""

from __future__ import annotations

import os
from dataclasses import replace

from tesla_mcp.config import (
    DEFAULT_COMMANDS_DAILY_CAP,
    DEFAULT_COMMANDS_PER_COMMAND_DAILY_CAP,
    MAX_COMMANDS_DAILY_CAP,
    MAX_COMMANDS_PER_COMMAND_DAILY_CAP,
    Settings,
    _bool,
    _bounded_int,
    _csv,
    _proxy_url,
    insecure_proxy_problem,
)

# Only these turn dry-run off. Blank, whitespace, and anything else stay in dry-run.
_DRY_RUN_OFF = frozenset({"0", "false", "no", "off"})


def commands_dry_run_enabled(value: str | None) -> bool:
    """Return True unless the value is an explicit false.

    `True` means dry-run (nothing is sent). Unset, blank, whitespace, and
    unrecognized text all stay in dry-run. `false`, `0`, `no`, and `off` turn
    it off.
    """
    if value is None:
        return True
    lowered = value.strip().lower()
    if lowered in _DRY_RUN_OFF:
        return False
    return True


def apply_command_environment(settings: Settings) -> Settings:
    """Read command variables onto a copy of `settings`. Never raises."""
    problems: list[str] = []
    enable = _flag("TESLA_ENABLE_COMMANDS", False, problems)
    dry_run = commands_dry_run_enabled(os.environ.get("TESLA_COMMANDS_DRY_RUN"))
    allow_remote = _flag("TESLA_COMMAND_PROXY_ALLOW_REMOTE", False, problems)
    insecure = _flag("TESLA_COMMAND_PROXY_INSECURE", False, problems)
    daily = _cap(
        "TESLA_COMMANDS_DAILY_CAP",
        DEFAULT_COMMANDS_DAILY_CAP,
        MAX_COMMANDS_DAILY_CAP,
        problems,
    )
    per_command = _cap(
        "TESLA_COMMANDS_PER_COMMAND_DAILY_CAP",
        DEFAULT_COMMANDS_PER_COMMAND_DAILY_CAP,
        MAX_COMMANDS_PER_COMMAND_DAILY_CAP,
        problems,
    )
    proxy = _proxy(allow_remote, problems)
    insecure_problem = insecure_proxy_problem(proxy, insecure)
    if insecure_problem:
        problems.append(insecure_problem)
    ca_file = (os.environ.get("TESLA_COMMAND_PROXY_CA") or "").strip()
    if problems:
        # Fail closed: commands stay off, dry-run stays on, and there is no proxy.
        detail = " ".join(problems)
        if "Nothing was sent" not in detail:
            detail = f"{detail} Nothing was sent to the car."
        return replace(
            settings,
            enable_commands=False,
            commands_dry_run=True,
            commands_allowlist=(),
            commands_daily_cap=DEFAULT_COMMANDS_DAILY_CAP,
            commands_per_command_daily_cap=DEFAULT_COMMANDS_PER_COMMAND_DAILY_CAP,
            command_proxy_url=None,
            command_proxy_ca=None,
            command_proxy_insecure=False,
            command_proxy_allow_remote=False,
            command_config_error=detail,
        )
    return replace(
        settings,
        enable_commands=enable,
        commands_dry_run=dry_run,
        commands_allowlist=_csv(os.environ.get("TESLA_COMMANDS_ALLOWLIST")),
        commands_daily_cap=daily,
        commands_per_command_daily_cap=per_command,
        command_proxy_url=proxy,
        command_proxy_ca=ca_file or None,
        command_proxy_insecure=insecure,
        command_proxy_allow_remote=allow_remote,
        command_config_error=None,
    )


def _flag(name: str, default: bool, problems: list[str]) -> bool:
    raw = os.environ.get(name)
    try:
        return _bool(raw, default)
    except ValueError:
        shown = raw.strip() if isinstance(raw, str) else ""
        if len(shown) > 40:
            shown = "an unrecognized value"
        problems.append(f"{name} must be true or false. Got {shown!r}.")
        return default


def _cap(name: str, default: int, upper: int, problems: list[str]) -> int:
    try:
        return _bounded_int(os.environ.get(name), default, name=name, upper=upper)
    except ValueError as exc:
        problems.append(str(exc))
        return default


def _proxy(allow_remote: bool, problems: list[str]) -> str | None:
    try:
        return _proxy_url(os.environ.get("TESLA_COMMAND_PROXY_URL"), allow_remote=allow_remote)
    except ValueError as exc:
        problems.append(str(exc))
        return None
