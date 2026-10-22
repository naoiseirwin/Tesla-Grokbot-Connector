"""Stdio MCP server for opt-in vehicle commands.

Installing or running `tesla-mcp` does not load this module. Add
`tesla-commands-mcp` as its own connector.
"""

from __future__ import annotations

import argparse
from typing import Any

from fastmcp import FastMCP

from tesla_mcp.config import Settings
from tesla_mcp.logging_utils import configure_logging, logger
from tesla_mcp.runtime import build_runtime
from tesla_mcp.scopes import VEHICLE_COMMANDS_SCOPE
from tesla_mcp.server import StdioMiddleware

from .catalog import COMMANDS, unknown_allowlist_entries
from .config import apply_command_environment
from .tools import TOOL_FUNCTIONS


def create_server(settings: Settings | None = None) -> FastMCP:
    """Build the commands server. Tools are registered even when commands are off.

    A call still refuses until TESLA_ENABLE_COMMANDS=true. Dry-run is the default.
    Passing no settings reads command variables. The read-only server does not.
    """
    configure_logging()
    loaded = settings if settings is not None else apply_command_environment(Settings.load())
    runtime = build_runtime(loaded, for_commands=True)
    _log_mode(runtime)
    mcp = FastMCP("Tesla Commands")
    mcp.add_middleware(StdioMiddleware(runtime))
    for spec in COMMANDS.values():
        fn = _tool_by_name(spec.tool_name)
        mcp.tool(
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": spec.idempotent,
                "openWorldHint": True,
            }
        )(fn)
    return mcp


def _granted_scope_names(runtime: Any) -> list[str] | None:
    if not runtime.scopes_known or runtime.granted_scopes is None:
        return None
    return list(runtime.granted_scopes)


def _tool_by_name(name: str) -> Any:
    for fn in TOOL_FUNCTIONS:
        if fn.__name__ == name:
            return fn
    raise RuntimeError(f"Missing tool function for {name}.")


def _log_mode(runtime: Any) -> None:
    settings = runtime.settings
    listed = ",".join(settings.commands_allowlist) if settings.commands_allowlist else "(none)"
    logger.info(
        "tool=commands_startup vin=- status=enabled_%s dry_run_%s category=-",
        str(settings.enable_commands).lower(),
        str(settings.commands_dry_run).lower(),
    )
    granted = _granted_scope_names(runtime)
    if granted is None:
        logger.info("vehicle_cmds: no token stored yet")
    elif VEHICLE_COMMANDS_SCOPE in granted:
        logger.info("vehicle_cmds: granted")
    else:
        logger.info("vehicle_cmds: missing; commands will be refused until tesla-commands-mcp auth")
    logger.info("commands_allowlist=%s", listed)
    if settings.command_config_error:
        logger.warning("commands refused: %s", settings.command_config_error)
    unknown = unknown_allowlist_entries(settings.commands_allowlist)
    if unknown:
        logger.warning(
            "Ignoring TESLA_COMMANDS_ALLOWLIST entries this program cannot send: %s",
            ", ".join(unknown),
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Opt-in Tesla vehicle commands (stdio). "
            "Refuses every command unless TESLA_ENABLE_COMMANDS=true. "
            "Dry-run is the default."
        )
    )
    parser.add_argument(
        "--transport",
        choices=["stdio"],
        default="stdio",
        help="Only stdio is supported. This server does not listen on a public port.",
    )
    parser.parse_args()
    configure_logging(stderr=True)
    create_server().run()


if __name__ == "__main__":
    main()
