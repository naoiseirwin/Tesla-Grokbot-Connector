"""Decide whether a command may run, then dry-run or send it.

Order: unknown command, master switch, input check, allowlist, OAuth scope,
daily caps, dry-run, proxy, reserve a command slot, wake (shared with the
read-only wake cap), send. The slot is reserved before a wake so a command
the cap refuses does not wake the car. Every attempt is appended to the
audit log. A dry run does not call Tesla.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastmcp import Context

from tesla_mcp.config import Settings
from tesla_mcp.errors import TeslaAuthError, TeslaError
from tesla_mcp.fleet import FleetClient
from tesla_mcp.logging_utils import log_event
from tesla_mcp.pricing import price_for
from tesla_mcp.runtime import AppRuntime
from tesla_mcp.scopes import VEHICLE_COMMANDS_SCOPE, scopes_from_access_token
from tesla_mcp.time_utils import isoformat, utc_now, zone
from tesla_mcp.tools.common import require_app, select_vehicle
from tesla_mcp.vin import mask_path, mask_vin

from .audit import AuditLog, public_parameters, public_selector, scrub_text, scrub_value
from .budget import CommandBudget, CommandDecision
from .catalog import COMMANDS, CommandSpec, build_body, command_allowed, would_send
from .errors import CommandRefusal
from .proxy import ProxyTransportError, require_proxy, send_command

_DISABLED = (
    "Vehicle commands are off. Nothing was sent to the car. "
    "Set TESLA_ENABLE_COMMANDS=true to allow this program to accept commands. "
    "They still only describe the request until TESLA_COMMANDS_DRY_RUN=false. "
    "Read docs/COMMANDS.md before turning a command on."
)
_DRY_NOTE = (
    "Dry run. Nothing was sent to Tesla or to the car. The request below is what would be sent."
)
_LIVE_NOTE = "The command was sent through the local signing proxy."
_MISSING_SCOPE = (
    "This sign-in does not include vehicle_cmds, so Tesla would reject a command. "
    "Set TESLA_ENABLE_COMMANDS=true and run `tesla-commands-mcp auth` on this computer. "
    "The read-only `tesla-mcp auth` command does not request vehicle_cmds. "
    "Nothing was sent to the car."
)
_HISTORY_UNREADABLE = (
    "The local history file could not be read, so the command was refused. "
    "Nothing was sent to the car."
)
_TIMEZONE_INVALID = (
    "TESLA_TIMEZONE is not a valid timezone, so the command was refused. "
    "Use an IANA name such as America/New_York. Nothing was sent to the car."
)


async def perform(
    ctx: Context | None,
    command: str,
    parameters: dict[str, Any],
    *,
    vin_selector: str | None = None,
) -> dict[str, Any]:
    try:
        fleet, runtime = await require_app(ctx)
    except TeslaError as exc:
        message = scrub_text(exc.message)
        _audit_not_ready(command, parameters, vin_selector, message)
        return {
            "ok": False,
            "sent": False,
            "dry_run": True,
            "command": command,
            "parameters": public_parameters(parameters),
            "vehicle": public_selector(vin_selector),
            "error": message,
            "error_type": type(exc).__name__,
            "refusal_reason": "not_ready",
        }
    audit = AuditLog(runtime.settings)
    try:
        _refuse_if_unusable(runtime)
        budget = CommandBudget(runtime.settings)
        return await _perform(
            fleet,
            runtime,
            audit,
            budget,
            command,
            parameters,
            vin_selector=vin_selector,
        )
    except CommandRefusal as exc:
        return _finish(
            runtime,
            audit,
            command=command,
            parameters=parameters,
            vin_selector=vin_selector,
            ok=False,
            sent=False,
            result="refused",
            refusal_reason=exc.reason,
            error=exc.message,
        )
    except sqlite3.Error:
        return _finish(
            runtime,
            audit,
            command=command,
            parameters=parameters,
            vin_selector=vin_selector,
            ok=False,
            sent=False,
            result="refused",
            refusal_reason="history_unreadable",
            error=_HISTORY_UNREADABLE,
        )


async def _perform(
    fleet: FleetClient,
    runtime: AppRuntime,
    audit: AuditLog,
    budget: CommandBudget,
    command: str,
    parameters: dict[str, Any],
    *,
    vin_selector: str | None,
) -> dict[str, Any]:
    settings = runtime.settings
    spec = COMMANDS.get(command)
    if spec is None:
        raise CommandRefusal(
            "unknown_command",
            f"{command} is not a command this program can send. Nothing was sent to the car.",
        )
    if not settings.enable_commands:
        raise CommandRefusal("commands_disabled", _DISABLED)
    body = build_body(command, parameters)
    if not command_allowed(settings.commands_allowlist, command):
        listed = (
            ", ".join(settings.commands_allowlist) if settings.commands_allowlist else "(empty)"
        )
        raise CommandRefusal(
            "not_allowlisted",
            f"{command} is not on TESLA_COMMANDS_ALLOWLIST. The list is {listed}. "
            "An empty list allows nothing. Nothing was sent to the car.",
        )
    _require_scope(runtime)
    preview = budget.peek(command)
    if not preview.allowed:
        reason, message = _cap_refusal(preview, settings, command)
        raise CommandRefusal(reason, message)
    if settings.commands_dry_run:
        return _finish(
            runtime,
            audit,
            command=command,
            parameters=parameters,
            vin_selector=vin_selector,
            ok=True,
            sent=False,
            result="dry_run",
            would=would_send(spec.endpoint, body, None),
            note=_DRY_NOTE,
            spec=spec,
        )
    return await _send_live(
        fleet,
        runtime,
        audit,
        budget,
        spec,
        parameters,
        body,
        vin_selector=vin_selector,
    )


async def _send_live(
    fleet: FleetClient,
    runtime: AppRuntime,
    audit: AuditLog,
    budget: CommandBudget,
    spec: CommandSpec,
    parameters: dict[str, Any],
    body: dict[str, Any],
    *,
    vin_selector: str | None,
) -> dict[str, Any]:
    require_proxy(runtime.settings)
    # Hold the command slot before any wake. A cap refusal must not wake the car.
    decision = budget.reserve(spec.name, public_selector(vin_selector))
    if not decision.allowed:
        reason, message = _cap_refusal(decision, runtime.settings, spec.name)
        return _finish(
            runtime,
            audit,
            command=spec.name,
            parameters=parameters,
            vin_selector=vin_selector,
            ok=False,
            sent=False,
            result="refused",
            refusal_reason=reason,
            error=message,
            spec=spec,
        )
    event_id = decision.event_id
    try:
        vehicle, _all = await select_vehicle(fleet, vin_selector)
    except TeslaError as exc:
        _release(budget, event_id)
        return _finish(
            runtime,
            audit,
            command=spec.name,
            parameters=parameters,
            vin_selector=vin_selector,
            ok=False,
            sent=False,
            result="error",
            error=exc.message,
            error_type=type(exc).__name__,
            spec=spec,
        )
    woke = False
    try:
        if vehicle.state != "online":
            woke = await _wake_for_command(fleet, runtime, vehicle)
    except CommandRefusal as exc:
        _release(budget, event_id)
        return _finish(
            runtime,
            audit,
            command=spec.name,
            parameters=parameters,
            vin_selector=vin_selector,
            ok=False,
            sent=False,
            result="refused",
            refusal_reason=exc.reason,
            error=exc.message,
            raw_vin=vehicle.vin,
            woke=woke,
            spec=spec,
        )
    except TeslaError as exc:
        _release(budget, event_id)
        return _finish(
            runtime,
            audit,
            command=spec.name,
            parameters=parameters,
            vin_selector=vin_selector,
            ok=False,
            sent=False,
            result="error",
            error=exc.message,
            error_type=type(exc).__name__,
            raw_vin=vehicle.vin,
            woke=woke,
            http_status=exc.status_code,
            spec=spec,
        )

    try:
        token = await runtime.tokens.get_access_token()
    except TeslaError as exc:
        _release(budget, event_id)
        return _finish(
            runtime,
            audit,
            command=spec.name,
            parameters=parameters,
            vin_selector=vin_selector,
            ok=False,
            sent=False,
            result="error",
            error=exc.message,
            error_type=type(exc).__name__,
            raw_vin=vehicle.vin,
            woke=woke,
            spec=spec,
        )
    planned = would_send(spec.endpoint, body, vehicle.vin)
    try:
        result = await send_command(
            runtime.settings,
            token,
            vehicle.vin,
            spec.endpoint,
            body,
        )
    except ProxyTransportError as exc:
        _release(budget, event_id)
        return _finish(
            runtime,
            audit,
            command=spec.name,
            parameters=parameters,
            vin_selector=vin_selector,
            ok=False,
            sent=False,
            result="error",
            error=exc.message,
            error_type="proxy_unreachable",
            would=planned,
            raw_vin=vehicle.vin,
            woke=woke,
            secrets=(token, runtime.settings.client_secret),
            spec=spec,
        )
    except CommandRefusal as exc:
        _release(budget, event_id)
        return _finish(
            runtime,
            audit,
            command=spec.name,
            parameters=parameters,
            vin_selector=vin_selector,
            ok=False,
            sent=False,
            result="refused",
            refusal_reason=exc.reason,
            error=exc.message,
            would=planned,
            raw_vin=vehicle.vin,
            woke=woke,
            secrets=(token, runtime.settings.client_secret),
            spec=spec,
        )

    if event_id is not None and not result.billed:
        _release(budget, event_id)
    try:
        runtime.history.record_usage(
            category="command",
            endpoint=mask_path(f"/api/1/vehicles/{vehicle.vin}/command/{spec.endpoint}"),
            http_status=result.http_status,
            vin_masked=mask_vin(vehicle.vin),
            estimated_usd=price_for("command", result.http_status),
        )
    except sqlite3.Error:
        # The command already reached Tesla. Losing the usage row must not
        # turn the reply into a claim that nothing was sent.
        pass
    if not result.ok:
        return _finish(
            runtime,
            audit,
            command=spec.name,
            parameters=parameters,
            vin_selector=vin_selector,
            ok=False,
            sent=True,
            result="error",
            error=result.message,
            error_type="tesla",
            would=planned,
            http_status=result.http_status,
            tesla_reason=result.tesla_reason,
            raw_vin=vehicle.vin,
            woke=woke,
            secrets=(token, runtime.settings.client_secret),
            spec=spec,
        )
    return _finish(
        runtime,
        audit,
        command=spec.name,
        parameters=parameters,
        vin_selector=vin_selector,
        ok=True,
        sent=True,
        result="ok",
        would=planned,
        note=_LIVE_NOTE,
        http_status=result.http_status,
        tesla_reason=result.tesla_reason,
        raw_vin=vehicle.vin,
        woke=woke,
        secrets=(token, runtime.settings.client_secret),
        spec=spec,
    )


async def _wake_for_command(fleet: FleetClient, runtime: AppRuntime, vehicle: Any) -> bool:
    """Wake an asleep car through the existing wake budget. Returns True if a wake was sent."""
    decision = runtime.wakes.reserve(vehicle.vin)
    if not decision.allowed:
        raise CommandRefusal("wake_budget", _wake_message(decision.reason, runtime))
    try:
        body = await fleet.wake_up(vehicle.vin)
    except TeslaError as exc:
        if decision.event_id is not None and (exc.status_code is None or exc.status_code >= 500):
            runtime.wakes.release(decision.event_id)
        raise
    response = body.get("response") if isinstance(body.get("response"), dict) else {}
    state = response.get("state") if isinstance(response, dict) else None
    if state != "online":
        meta = await fleet.get_vehicle(vehicle.vin)
        state = meta.state
    if state != "online":
        raise CommandRefusal(
            "wake_pending",
            "The car was asked to wake, but it is not online yet. The command was not sent. "
            "That wake still counts toward today's wake cap.",
        )
    return True


def _wake_message(reason: str, runtime: AppRuntime) -> str:
    settings = runtime.settings
    if reason == "per_minute":
        detail = (
            f"the hard limit of {runtime.wakes.per_minute_cap} wakes per minute. "
            "That limit cannot be raised."
        )
    elif reason == "interval":
        detail = f"the last wake was under {settings.wake_min_interval_seconds} seconds ago."
    elif reason == "daily":
        detail = (
            f"today's cap of {settings.wake_daily_cap} wakes is used. "
            f"The cap resets at midnight ({settings.timezone})."
        )
    else:
        detail = "the local wake budget."
    return (
        "The car is not online, and waking it was refused: "
        f"{detail} Wakes from a command count toward the same cap as "
        "tesla_wake_and_refresh. The command was not sent."
    )


def _refuse_if_unusable(runtime: AppRuntime) -> None:
    """Stop before any Tesla call when local config or history cannot be trusted."""
    problem = runtime.settings.command_config_error
    if problem:
        raise CommandRefusal("invalid_config", problem)
    if not runtime.history.available:
        raise CommandRefusal("history_unreadable", _HISTORY_UNREADABLE)
    try:
        zone(runtime.settings.timezone)
    except ValueError:
        raise CommandRefusal("invalid_timezone", _TIMEZONE_INVALID) from None


def _audit_not_ready(
    command: str,
    parameters: dict[str, Any],
    vin_selector: str | None,
    message: str,
) -> None:
    """Write the not_ready refusal. A missing client still leaves an audit line."""
    try:
        settings = Settings.load()
        AuditLog(settings).append(
            {
                "timestamp": isoformat(utc_now()),
                "command": command,
                "parameters": public_parameters(parameters),
                "vehicle": public_selector(vin_selector),
                "vin_masked": "-",
                "dry_run": True,
                "live": False,
                "result": "refused",
                "refusal_reason": "not_ready",
                "error": scrub_text(message),
                "http_status": None,
                "tesla_reason": None,
                "would_send": None,
                "woke": None,
            }
        )
    except (OSError, ValueError, TypeError, TeslaError):
        return


def _release(budget: CommandBudget, event_id: int | None) -> None:
    if event_id is None:
        return
    try:
        budget.release(event_id)
    except sqlite3.Error:
        return


def _require_scope(runtime: AppRuntime) -> None:
    try:
        record = runtime.tokens.load()
    except TeslaAuthError as exc:
        raise CommandRefusal("not_signed_in", exc.message) from exc
    if record is None:
        raise CommandRefusal(
            "not_signed_in",
            "No Tesla tokens stored. Set TESLA_ENABLE_COMMANDS=true and run "
            "`tesla-commands-mcp auth` on this computer. "
            "`tesla-mcp auth` does not request vehicle_cmds. Nothing was sent to the car.",
        )
    decoded = scopes_from_access_token(record.access_token)
    granted = decoded if decoded is not None else list(record.scopes)
    if VEHICLE_COMMANDS_SCOPE not in granted:
        raise CommandRefusal("missing_scope", _MISSING_SCOPE)


def _cap_refusal(decision: CommandDecision, settings: Any, command: str) -> tuple[str, str]:
    if decision.reason == "command_cap":
        return (
            "command_cap",
            f"{command} was refused because it has already been sent "
            f"{decision.command_used_today} times today. The per-command cap is "
            f"{settings.commands_per_command_daily_cap}. It resets at midnight "
            f"({settings.timezone}). Nothing was sent to the car.",
        )
    return (
        "daily_cap",
        "The command was refused because "
        f"{decision.used_today} commands were already sent today. The daily cap is "
        f"{settings.commands_daily_cap}. It resets at midnight ({settings.timezone}). "
        "Nothing was sent to the car.",
    )


def _finish(
    runtime: AppRuntime,
    audit: AuditLog,
    *,
    command: str,
    parameters: dict[str, Any],
    vin_selector: str | None,
    ok: bool,
    sent: bool,
    result: str,
    refusal_reason: str | None = None,
    error: str | None = None,
    error_type: str | None = None,
    would: dict[str, Any] | None = None,
    note: str | None = None,
    http_status: int | None = None,
    tesla_reason: str | None = None,
    raw_vin: str | None = None,
    woke: bool | None = None,
    secrets: tuple[str, ...] = (),
    spec: CommandSpec | None = None,
) -> dict[str, Any]:
    known = _secret_values(runtime, secrets)
    error_text = scrub_text(error, secrets=known) if error else None
    reason_text = scrub_text(tesla_reason, secrets=known) if tesla_reason else None
    note_text = scrub_text(note, secrets=known) if note else None
    parameters_public = public_parameters(parameters, secrets=known)
    vehicle = public_selector(vin_selector, secrets=known)
    would_public = scrub_value(would, secrets=known) if would is not None else None
    vin_masked = mask_vin(raw_vin) if raw_vin else "-"
    record: dict[str, Any] = {
        "timestamp": isoformat(utc_now()),
        "command": command,
        "parameters": parameters_public,
        "vehicle": vehicle,
        "vin_masked": vin_masked,
        "dry_run": runtime.settings.commands_dry_run,
        "live": sent,
        "result": result,
        "refusal_reason": refusal_reason,
        "error": error_text,
        "http_status": http_status,
        "tesla_reason": reason_text,
        "would_send": would_public,
        "woke": woke,
    }
    audit.append(record, raw_vin=raw_vin, secrets=known)
    tool = spec.tool_name if spec is not None else command
    log_event(tool, raw_vin, result, "command" if sent else None)
    payload: dict[str, Any] = {
        "ok": ok,
        "sent": sent,
        "dry_run": runtime.settings.commands_dry_run,
        "command": command,
        "parameters": parameters_public,
        "vehicle": vehicle,
        "vin_masked": vin_masked,
    }
    if refusal_reason:
        payload["refusal_reason"] = refusal_reason
        payload["error_type"] = "refused"
    if error_text:
        payload["error"] = error_text
        if not refusal_reason:
            payload["error_type"] = error_type or "error"
    if would_public is not None:
        payload["would_send"] = would_public
    if note_text:
        payload["note"] = note_text
    if reason_text:
        payload["tesla_reason"] = reason_text
    if woke:
        payload["woke"] = True
    if http_status is not None:
        payload["http_status"] = http_status
    return payload


def _secret_values(runtime: AppRuntime, extra: tuple[str, ...]) -> tuple[str, ...]:
    """Token values to strip from replies. Short strings are left alone."""
    found: list[str] = [runtime.settings.client_secret, *extra]
    try:
        record = runtime.tokens.load()
    except TeslaError:
        record = None
    if record is not None:
        found.extend([record.access_token, record.refresh_token])
    unique = {item for item in found if isinstance(item, str) and len(item) >= 12}
    return tuple(unique)
