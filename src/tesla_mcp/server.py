"""Stdio MCP server. There is no HTTP transport in this project."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext

from .config import Settings
from .fleet import FleetClient
from .logging_utils import configure_logging
from .runtime import AppRuntime, build_runtime
from .scopes import CHARGING_HISTORY_SCOPE, LOCATION_SCOPE
from .tools.history import tesla_query_history, tesla_report
from .tools.vehicles import (
    tesla_get_charging_history,
    tesla_get_climate,
    tesla_get_location,
    tesla_get_odometer,
    tesla_get_software,
    tesla_get_status,
    tesla_list_vehicles,
)
from .tools.wake import tesla_usage, tesla_wake_and_refresh


class StdioMiddleware(Middleware):
    """Attach a Fleet client to each tool call. The poller uses its own client."""

    def __init__(self, runtime: AppRuntime) -> None:
        self.runtime = runtime

    async def on_call_tool(self, context: MiddlewareContext, call_next: Callable[..., Any]) -> Any:
        async with FleetClient(self.runtime, allow_wake=True) as fleet:
            if context.fastmcp_context:
                # The client and runtime are not JSON-serializable. Request-scoped
                # state lives only for this tool call.
                await context.fastmcp_context.set_state("fleet", fleet, serializable=False)
                await context.fastmcp_context.set_state("app", self.runtime, serializable=False)
            return await call_next(context)


def create_server(settings: Settings | None = None) -> FastMCP:
    """Build the read-only server. Opt-in tools are omitted when their scope is off."""
    configure_logging()
    runtime = build_runtime(settings or Settings.load())
    mcp = FastMCP("Tesla")
    mcp.add_middleware(StdioMiddleware(runtime))
    _register_tools(mcp, runtime)
    _register_prompts(mcp)
    return mcp


def _annotations(*, idempotent: bool = True, open_world: bool = True) -> dict[str, bool]:
    return {
        "readOnlyHint": True,
        "idempotentHint": idempotent,
        "openWorldHint": open_world,
    }


def _register_tools(mcp: FastMCP, runtime: AppRuntime) -> None:
    read = _annotations()
    local = _annotations(open_world=False)
    mcp.tool(annotations=read)(tesla_list_vehicles)
    mcp.tool(annotations=read)(tesla_get_status)
    mcp.tool(annotations=read)(tesla_get_climate)
    mcp.tool(annotations=read)(tesla_get_odometer)
    mcp.tool(annotations=read)(tesla_get_software)
    mcp.tool(annotations=local)(tesla_query_history)
    mcp.tool(annotations=read)(tesla_report)
    mcp.tool(annotations=_annotations(idempotent=False))(tesla_wake_and_refresh)
    mcp.tool(annotations=local)(tesla_usage)
    if runtime.opt_in_available(runtime.settings.enable_location, LOCATION_SCOPE):
        mcp.tool(annotations=read)(tesla_get_location)
    if runtime.opt_in_available(runtime.settings.enable_charging_history, CHARGING_HISTORY_SCOPE):
        mcp.tool(annotations=read)(tesla_get_charging_history)


def _register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt()
    async def weekly_mileage_report() -> str:  # type: ignore[reportUnusedFunction]
        """Summarize miles driven over the last 7 days from local snapshots."""
        return (
            "Use tesla_report with type weekly_mileage. "
            "Do not call tesla_wake_and_refresh. "
            "If days are missing, say the car was asleep and the gap was interpolated."
        )

    @mcp.prompt()
    async def range_trend_report() -> str:  # type: ignore[reportUnusedFunction]
        """Compare recent normalized battery range with the 90-day baseline."""
        return (
            "Use tesla_report with type range_trend. "
            "Normalized range is rated miles scaled to 100% charge. "
            "Do not wake the car."
        )

    @mcp.prompt()
    async def low_charge_check() -> str:  # type: ignore[reportUnusedFunction]
        """Check the latest snapshot for a low, unplugged battery. Does not wake the car."""
        return (
            "Use tesla_report with type low_charge. "
            "If the snapshot is older than 6 hours, say so. "
            "Do not call tesla_wake_and_refresh."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Tesla MCP server (stdio)")
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
