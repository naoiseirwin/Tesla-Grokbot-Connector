"""CLI commands other than the stdio server and the sign-in wizard."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from .config import Settings
from .crypto import generate_key
from .errors import TeslaError
from .logging_utils import configure_logging
from .partner import register_partner
from .poller import poll_once
from .runtime import build_runtime


def poll_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Snapshot vehicles that are already online")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Keep polling. The interval is at least 1 hour. A Grok Bot routine should "
        "call tesla-mcp poll once per schedule instead.",
    )
    parser.add_argument("--interval-hours", type=float, default=None)
    args = parser.parse_args(argv)
    configure_logging(stderr=True)
    settings = Settings.load()
    if not args.loop:
        _poll_and_print(settings)
        return
    requested = (
        args.interval_hours if args.interval_hours is not None else settings.poll_interval_hours
    )
    interval = max(requested, 1.0)
    if requested < 1:
        print("Poll interval raised to 1 hour.", file=sys.stderr)
    while True:
        _poll_and_print(settings)
        time.sleep(interval * 3600)


def _poll_and_print(settings: Settings) -> None:
    runtime = build_runtime(settings)
    try:
        summary = asyncio.run(poll_once(runtime))
    except TeslaError as exc:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1) from exc
    print(_json(summary))


def logout_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Delete local Tesla tokens")
    parser.add_argument(
        "--delete-history",
        action="store_true",
        help="Also delete history.db, which can contain vehicle snapshots.",
    )
    args = parser.parse_args(argv)
    settings = Settings.load()
    from .token_store import TokenStore

    removed = TokenStore(settings).delete()
    settings.state_path.unlink(missing_ok=True)
    if args.delete_history and settings.history_path.exists():
        settings.history_path.unlink()
        print(f"Deleted {settings.history_path}")
    if removed:
        print(f"Deleted {settings.token_path}")
    else:
        print("No token file to delete.")
    print(
        "Revoke the app at "
        "https://auth.tesla.com/user/revoke/consent"
        "?revoke_client_id=<your client id> "
        "or in the Tesla app under Security & Privacy → Third-Party Apps."
    )


def scopes_main(argv: list[str] | None = None) -> None:
    argparse.ArgumentParser(description="Show requested and granted scopes").parse_args(argv)
    runtime = build_runtime(Settings.load())
    print(runtime.describe_scopes())


def gen_key_main(argv: list[str] | None = None) -> None:
    argparse.ArgumentParser(description="Print a new AES token key").parse_args(argv)
    print(
        "Do not paste this key into chat. Put it in TESLA_TOKEN_KEY via a secret.", file=sys.stderr
    )
    print(generate_key())


def register_partner_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Register a partner domain with Tesla Fleet API")
    parser.add_argument("--domain", required=True, help="Domain that hosts the public key")
    args = parser.parse_args(argv)
    settings = Settings.load()
    try:
        result = asyncio.run(register_partner(settings, args.domain))
    except TeslaError as exc:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1) from exc
    print(_json(result))


def _json(payload: object) -> str:
    import json

    return json.dumps(payload, indent=2, sort_keys=True)
