"""Authorization-code sign-in against fleet-auth.

The localhost listener is http://localhost:8765/callback. A paste fallback
accepts the code and state from a static callback page. The callback handler
does not log the request line, because that line contains the code.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
from collections.abc import Awaitable, Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx

from .config import Settings
from .errors import TeslaAuthError, TeslaConfigError
from .http_util import error_code
from .scopes import requested_scopes, scopes_from_access_token
from .token_store import ASSUMED_EXPIRES_IN, TokenRecord, TokenStore

STATE_MAX_AGE_SECONDS = 600
CodeExchange = Callable[[str], Awaitable[dict[str, Any]]]


def build_authorize_url(settings: Settings, state: str) -> str:
    settings.require_client()
    scopes = requested_scopes(
        location=settings.enable_location,
        charging_history=settings.enable_charging_history,
    )
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.client_id,
            "redirect_uri": settings.redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "prompt_missing_scopes": "true",
            "locale": "en-US",
        },
        quote_via=quote,
    )
    return f"{settings.auth_url}?{query}"


def new_state() -> str:
    return secrets.token_urlsafe(32)


def write_oauth_state(settings: Settings, state: str) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(settings.data_dir, 0o700)
    blob = json.dumps({"state": state, "created_at": time.time()}).encode("utf-8")
    path = settings.state_path
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, blob)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def consume_oauth_state(
    settings: Settings, presented: str, *, max_age: float = STATE_MAX_AGE_SECONDS
) -> None:
    path = settings.state_path
    if not path.is_file():
        raise TeslaAuthError("No OAuth state is pending. Start `tesla-mcp auth` again.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    finally:
        path.unlink(missing_ok=True)
    if not isinstance(data, dict):
        raise TeslaAuthError("OAuth state file was unreadable. Start `tesla-mcp auth` again.")
    expected = str(data.get("state") or "")
    created = float(data.get("created_at") or 0)
    if time.time() - created > max_age:
        raise TeslaAuthError("OAuth state expired. Start `tesla-mcp auth` again.")
    if not expected or not presented or not hmac.compare_digest(expected, presented):
        raise TeslaAuthError("OAuth state did not match. Start `tesla-mcp auth` again.")


def parse_callback_input(text: str) -> tuple[str, str | None]:
    raw = text.strip()
    if not raw:
        raise TeslaAuthError("No authorization code was provided.")
    if raw.startswith("http://") or raw.startswith("https://") or "code=" in raw:
        if "://" in raw:
            query = urlparse(raw).query
        elif "?" in raw:
            query = raw.split("?", 1)[1]
        else:
            query = raw.lstrip("?")
        parsed = parse_qs(query)
        code = (parsed.get("code") or [""])[0]
        state = (parsed.get("state") or [None])[0]
        if not code:
            raise TeslaAuthError("Could not find the authorization code in that redirect.")
        return code, state
    return raw, None


class CallbackServer:
    """Localhost listener. `log_message` is a no-op so the code stays out of stderr."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self._result: dict[str, str | None] = {"code": None, "state": None, "error": None}
        self._done = threading.Event()
        handler = self._handler_class()
        self._httpd = ThreadingHTTPServer((host, port), handler)
        self.port = int(self._httpd.server_address[1])
        self._thread: threading.Thread | None = None

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib API
                parsed = urlparse(self.path)
                if parsed.path != "/callback":
                    body = b"Not found.\n"
                    self.send_response(404)
                    self._finish(body)
                    return
                query = parse_qs(parsed.query)
                server._result["error"] = (query.get("error") or [None])[0]
                server._result["code"] = (query.get("code") or [None])[0]
                server._result["state"] = (query.get("state") or [None])[0]
                body = (
                    b"Tesla connector: sign-in captured on this computer. "
                    b"You can close this tab. Do not paste the code into chat.\n"
                )
                self.send_response(200)
                self._finish(body)
                server._done.set()

            def _finish(self, body: bytes) -> None:
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        return Handler

    def start(self) -> None:
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def wait(self, timeout: float) -> tuple[str, str]:
        if not self._done.wait(timeout):
            raise TeslaAuthError(
                "Timed out waiting for http://localhost:"
                f"{self.port}/callback. Re-run `tesla-mcp auth --paste` and paste the code "
                "into this terminal, not into chat."
            )
        if self._result["error"]:
            raise TeslaAuthError("Tesla did not return an authorization code.")
        code = self._result["code"]
        state = self._result["state"]
        if not code or not state:
            raise TeslaAuthError("The callback was missing the code or the state.")
        return code, state

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


async def post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, data=data)
    if response.status_code >= 400:
        code = error_code(response)
        if response.status_code == 401 or code == "login_required":
            raise TeslaAuthError("Tesla returned 401 login_required. Run `tesla-mcp auth` again.")
        detail = f" ({code})" if code else ""
        raise TeslaAuthError(f"Token request failed with HTTP {response.status_code}{detail}.")
    try:
        body: Any = response.json()
    except ValueError as exc:
        raise TeslaAuthError("Token response was not JSON.") from exc
    if not isinstance(body, dict) or not isinstance(body.get("access_token"), str):
        raise TeslaAuthError("Token response did not include an access token.")
    return body


async def exchange_authorization_code(settings: Settings, code: str) -> dict[str, Any]:
    settings.require_client()
    return await post_form(
        settings.token_url,
        {
            "grant_type": "authorization_code",
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "code": code,
            "audience": settings.audience,
            "redirect_uri": settings.redirect_uri,
        },
    )


async def exchange_refresh_token(settings: Settings, refresh_token: str) -> dict[str, Any]:
    settings.require_client()
    return await post_form(
        settings.token_url,
        {
            "grant_type": "refresh_token",
            "client_id": settings.client_id,
            "refresh_token": refresh_token,
        },
    )


async def exchange_client_credentials(settings: Settings) -> dict[str, Any]:
    settings.require_client()
    return await post_form(
        settings.token_url,
        {
            "grant_type": "client_credentials",
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "scope": "openid",
            "audience": settings.audience,
        },
    )


async def finish_login(
    settings: Settings,
    code: str,
    state: str,
    *,
    exchange: CodeExchange | None = None,
) -> TokenRecord:
    """Check state, exchange the code, and only then keep the tokens.

    The access token is not sent to Fleet API during sign-in.
    """
    consume_oauth_state(settings, state)
    try:
        settings.require_client()
        settings.require_token_key()
    except TeslaConfigError as exc:
        raise TeslaAuthError(exc.message) from exc
    body = (
        await exchange(code)
        if exchange is not None
        else await exchange_authorization_code(settings, code)
    )
    access = body.get("access_token")
    refresh = body.get("refresh_token")
    if not isinstance(access, str) or not isinstance(refresh, str) or not access or not refresh:
        raise TeslaAuthError("Tesla did not return an access token and a refresh token.")
    try:
        lifetime = float(body.get("expires_in") or ASSUMED_EXPIRES_IN)
    except (TypeError, ValueError):
        lifetime = float(ASSUMED_EXPIRES_IN)
    scopes = scopes_from_access_token(access) or []
    record = TokenRecord(
        access_token=access,
        refresh_token=refresh,
        expires_at=time.time() + lifetime,
        scopes=scopes,
        region=settings.region,
    )
    TokenStore(settings).save(record)
    return record
