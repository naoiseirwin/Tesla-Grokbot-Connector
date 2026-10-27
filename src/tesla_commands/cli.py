"""Command-line entry point for the optional commands connector."""

from __future__ import annotations

import sys

HELP = """
tesla-commands-mcp sends a short list of Tesla vehicle commands.

It is a separate program from tesla-mcp. The read-only connector is unchanged.

  tesla-commands-mcp         Run the stdio MCP server
  tesla-commands-mcp auth    Sign in, and request vehicle_cmds only when
                             TESLA_ENABLE_COMMANDS=true

Commands are refused unless TESLA_ENABLE_COMMANDS=true.
TESLA_COMMANDS_DRY_RUN defaults to true: the server describes the request
and does not call Tesla. Dry-run turns off only for false, 0, no, or off.
An empty TESLA_COMMANDS_ALLOWLIST allows nothing.

Read docs/COMMANDS.md before you turn a live command on.
""".strip()


def main() -> None:
    args = sys.argv[1:]
    if not args:
        from .server import main as server_main

        server_main()
        return
    if args[0] in {"-h", "--help"}:
        print(HELP)
        return
    if args[0].startswith("-"):
        from .server import main as server_main

        server_main()
        return
    if args[0] == "auth":
        from tesla_mcp.scripts.setup_auth import main as auth_main

        auth_main(args[1:], include_commands=True)
        return
    print(HELP, file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
