"""One-time Tesla sign-in on this computer."""

from __future__ import annotations

import argparse
import asyncio
import sys
import webbrowser

from ..auth_flow import (
    CallbackServer,
    build_authorize_url,
    finish_login,
    new_state,
    parse_callback_input,
    write_oauth_state,
)
from ..config import Settings
from ..errors import TeslaAuthError, TeslaConfigError
from ..regions import DEFAULT_CALLBACK_PORT

_BANNER = """
GrokBot: this wizard runs on the cloud computer.
Use Take over for Tesla login or MFA.
Never paste the authorization code, tokens, or client secret into chat.
""".strip()


def main(argv: list[str] | None = None, *, include_commands: bool = False) -> None:
    parser = argparse.ArgumentParser(description="Sign in to Tesla Fleet API")
    parser.add_argument(
        "--stdio", action="store_true", help="Accepted for compatibility. This is the only mode."
    )
    parser.add_argument(
        "--paste", action="store_true", help="Paste a code instead of using the localhost callback"
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Print the URL instead of opening a browser"
    )
    parser.add_argument("--callback-port", type=int, default=DEFAULT_CALLBACK_PORT)
    parser.add_argument(
        "--code", help="Authorization code. For a non-interactive terminal on this computer."
    )
    parser.add_argument("--state", help="OAuth state returned with the code")
    parser.add_argument("--redirect-url", help="Full callback URL containing code and state")
    args = parser.parse_args(argv)

    print(_BANNER)
    print()
    try:
        settings = Settings.load()
        settings.require_client()
        settings.require_token_key()
    except (TeslaConfigError, TeslaAuthError) as exc:
        print(exc.message if isinstance(exc, TeslaAuthError) else exc.message, file=sys.stderr)
        raise SystemExit(1) from exc

    # The read-only wizard passes include_commands=False and never reads command
    # variables. The commands wizard loads them and stops, without a traceback,
    # when one of them is invalid.
    if include_commands:
        from tesla_commands.config import apply_command_environment

        settings = apply_command_environment(settings)
        if settings.command_config_error:
            print(settings.command_config_error, file=sys.stderr)
            raise SystemExit(1)

    # The read-only wizard never asks for vehicle_cmds. The commands wizard
    # still only asks when TESLA_ENABLE_COMMANDS is on.
    request_commands = include_commands and settings.enable_commands
    if include_commands and not settings.enable_commands:
        print(
            "TESLA_ENABLE_COMMANDS is not true, so this sign-in will not request "
            "vehicle_cmds. Set TESLA_ENABLE_COMMANDS=true and run "
            "`tesla-commands-mcp auth` again before a command can be sent."
        )
    elif request_commands:
        print(
            "This sign-in will request vehicle_cmds. That scope lets an app command "
            "the car. The read-only tesla-mcp server still has no command tools."
        )

    completing = bool(args.redirect_url or args.code)
    if not completing:
        state = new_state()
        write_oauth_state(settings, state)
        url = build_authorize_url(settings, state, commands=request_commands)
        print("Open this URL on this computer if the browser does not:")
        print(url)
        print()

    try:
        if completing:
            code, presented_state = _code_from_args(args)
            if presented_state is None:
                presented_state = args.state
            if not presented_state:
                raise TeslaAuthError("The state value is missing. Paste the full callback URL.")
        elif args.paste:
            code, presented_state = _prompt_for_code()
        else:
            if not args.no_browser:
                webbrowser.open(url)
            code, presented_state = _wait_for_browser(args.callback_port)
        record = asyncio.run(finish_login(settings, code, presented_state))
    except (TeslaAuthError, TeslaConfigError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"Signed in. Tokens saved to {settings.token_path} (mode 600).")
    print(
        "Granted scopes: "
        + (" ".join(record.scopes) if record.scopes else "(not listed in the token)")
    )
    if "vehicle_charging_cmds" in record.scopes:
        print(
            "vehicle_charging_cmds is granted. Tesla includes charge control in that scope. "
            "The read-only server does not expose charge control."
        )
    if request_commands and "vehicle_cmds" not in record.scopes:
        print(
            "The token does not include vehicle_cmds. Commands will be refused until "
            "Tesla grants that scope. Enable it on the developer app, then run "
            "`tesla-commands-mcp auth` again."
        )
    elif request_commands:
        print(
            "vehicle_cmds is granted. Commands stay in dry-run unless "
            "TESLA_COMMANDS_DRY_RUN is false, 0, no, or off. "
            "Read docs/COMMANDS.md before that."
        )
    print("Next: add the stdio command in Grok Bot. See README.")


def _code_from_args(args: argparse.Namespace) -> tuple[str, str | None]:
    if args.redirect_url:
        return parse_callback_input(args.redirect_url)
    if args.code:
        return args.code, args.state
    raise TeslaAuthError("No authorization code was provided.")


def _prompt_for_code() -> tuple[str, str]:
    print("Paste the code or the full callback URL into this terminal. Do not paste it into chat.")
    try:
        entered = input("Code or callback URL: ").strip()
    except EOFError as exc:
        raise TeslaAuthError("No code was entered.") from exc
    code, state = parse_callback_input(entered)
    if state is None:
        try:
            state = input("State: ").strip()
        except EOFError as exc:
            raise TeslaAuthError("No state was entered.") from exc
    if not state:
        raise TeslaAuthError("The state value is missing.")
    return code, state


def _wait_for_browser(port: int) -> tuple[str, str]:
    try:
        server = CallbackServer("127.0.0.1", port)
    except OSError as exc:
        raise TeslaAuthError(
            f"Could not listen on 127.0.0.1:{port}. "
            "Re-run `tesla-mcp auth --paste` if this port is in use."
        ) from exc
    server.start()
    try:
        return server.wait(timeout=300)
    finally:
        server.close()
