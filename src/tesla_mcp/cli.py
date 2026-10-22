"""Command-line entry point for the Tesla MCP connector."""

from __future__ import annotations

import sys

HELP = """
tesla-mcp is a read-only Tesla Fleet API server for Grok Bot.

  tesla-mcp                  Run the stdio MCP server
  tesla-mcp auth             One-time sign-in (localhost callback)
  tesla-mcp auth --paste     Paste the code from the fallback page into this terminal
  tesla-mcp poll             Snapshot cars that are already online. Never wakes.
  tesla-mcp scopes           Show requested and granted scopes
  tesla-mcp logout           Delete the encrypted token file
  tesla-mcp register-partner --domain <domain>
  tesla-mcp gen-key          Print a new token encryption key (do not paste it into chat)

There is no vehicle-command tool on this server, no virtual key, and no public server.
Optional commands are a separate program, tesla-commands-mcp, and are off by default.
See docs/COMMANDS.md.
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

    command = args[0]
    rest = args[1:]
    if command == "auth":
        from .scripts.setup_auth import main as auth_main

        auth_main(rest)
        return
    if command == "poll":
        from .commands import poll_main

        poll_main(rest)
        return
    if command == "logout":
        from .commands import logout_main

        logout_main(rest)
        return
    if command == "scopes":
        from .commands import scopes_main

        scopes_main(rest)
        return
    if command == "gen-key":
        from .commands import gen_key_main

        gen_key_main(rest)
        return
    if command == "register-partner":
        from .commands import register_partner_main

        register_partner_main(rest)
        return
    print(HELP, file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
