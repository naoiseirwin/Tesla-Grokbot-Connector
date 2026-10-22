"""Send one command through Tesla's official HTTP proxy.

The proxy (tesla-http-proxy from github.com/teslamotors/vehicle-command) holds
the private key and signs the Vehicle Command Protocol message. This process
only forwards the REST call and the OAuth bearer token. It does not read the
private key and it does not implement the signature.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from tesla_mcp.config import Settings

from .audit import scrub_text
from .errors import CommandRefusal

_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class ProxyResult:
    http_status: int | None
    ok: bool
    billed: bool
    tesla_reason: str
    message: str


class ProxyTransportError(Exception):
    """The proxy never answered. The command cap for this attempt should be released."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def require_proxy(settings: Settings) -> str:
    """Return the proxy base URL, or refuse with a plain reason."""
    url = settings.command_proxy_url
    if not url:
        raise CommandRefusal(
            "proxy_not_configured",
            "Dry-run is off, but TESLA_COMMAND_PROXY_URL is not set. "
            "Live commands are signed by Tesla's official tesla-http-proxy on this "
            "computer. This program does not sign commands itself. Nothing was sent. "
            "See docs/COMMANDS.md.",
        )
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.username or parsed.password:
        raise CommandRefusal(
            "proxy_not_configured",
            "TESLA_COMMAND_PROXY_URL must not contain a username or password. Nothing was sent.",
        )
    if host not in _LOOPBACK and not settings.command_proxy_allow_remote:
        raise CommandRefusal(
            "proxy_not_configured",
            "TESLA_COMMAND_PROXY_URL must point at a proxy on this computer "
            "(localhost or 127.0.0.1). The access token is sent there. Nothing was sent.",
        )
    if settings.command_proxy_ca:
        ca_path = Path(settings.command_proxy_ca).expanduser()
        if not ca_path.is_file():
            raise CommandRefusal(
                "proxy_not_configured",
                "TESLA_COMMAND_PROXY_CA does not point at a certificate file "
                f"({ca_path}). Nothing was sent.",
            )
    return url.rstrip("/")


def _verify(settings: Settings) -> bool | str:
    if settings.command_proxy_insecure:
        return False
    if settings.command_proxy_ca:
        return str(Path(settings.command_proxy_ca).expanduser())
    return True


def _json_body(response: Any) -> dict[str, Any] | None:
    try:
        body = response.json()
    except (ValueError, UnicodeError, AttributeError):
        return None
    if isinstance(body, dict):
        return body
    return None


def _string_field(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _error_field(body: dict[str, Any]) -> str:
    error = body.get("error")
    if isinstance(error, str):
        return " ".join(error.split())
    if isinstance(error, dict):
        for key in ("message", "code"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())
    description = body.get("error_description")
    if isinstance(description, str):
        return " ".join(description.split())
    return ""


def _tesla_text(response: Any) -> str:
    """Tesla's sentence, with tokens and VINs removed. Never the raw body."""
    body = _json_body(response)
    detail = _string_field(body, "message") if body else ""
    code = _error_field(body) if body else ""
    if detail and code and code not in detail:
        text = f"{detail} ({code})"
    else:
        text = detail or code
    text = scrub_text(text)
    if not text:
        text = f"HTTP {response.status_code}"
    if len(text) > 200:
        return text[:200].rstrip() + "…"
    return text


def interpret_response(response: Any) -> ProxyResult:
    """Turn a proxy or Fleet API response into a plain result.

    Tesla's own words are kept when they are safe to show. A 200 with
    `result: false` is a refusal, and `reason` is the text the car sent.
    """
    status = int(response.status_code)
    if status >= 400:
        text = _tesla_text(response)
        return ProxyResult(
            http_status=status,
            ok=False,
            billed=status < 500,
            tesla_reason=text,
            message=f"Tesla refused the command: {text}",
        )
    try:
        payload = response.json()
    except ValueError:
        return ProxyResult(
            http_status=status,
            ok=False,
            billed=True,
            tesla_reason="",
            message="Tesla returned a response that was not JSON.",
        )
    if not isinstance(payload, dict):
        return ProxyResult(
            http_status=status,
            ok=False,
            billed=True,
            tesla_reason="",
            message="Tesla returned an unexpected response.",
        )
    inner = payload.get("response")
    reason = ""
    if isinstance(inner, dict) and isinstance(inner.get("reason"), str):
        reason = scrub_text(" ".join(inner["reason"].split()))
    if isinstance(inner, dict) and inner.get("result") is False:
        shown = reason or "command rejected"
        return ProxyResult(
            http_status=status,
            ok=False,
            billed=True,
            tesla_reason=shown,
            message=f"Tesla refused the command: {shown}",
        )
    return ProxyResult(
        http_status=status,
        ok=True,
        billed=True,
        tesla_reason=reason,
        message="",
    )


async def send_command(
    settings: Settings,
    token: str,
    vin: str,
    endpoint: str,
    body: dict[str, Any],
) -> ProxyResult:
    """POST the command to the local proxy. The proxy signs it."""
    import httpx

    base = require_proxy(settings)
    path = f"/api/1/vehicles/{quote(vin, safe='')}/command/{endpoint}"
    try:
        async with httpx.AsyncClient(timeout=60.0, verify=_verify(settings)) as client:
            response = await client.post(
                base + path,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
    except httpx.RequestError:
        raise ProxyTransportError(
            "The signing proxy could not be reached. Nothing was confirmed by the car. "
            "Check that tesla-http-proxy is running and TESLA_COMMAND_PROXY_URL matches it. "
            "See docs/COMMANDS.md."
        ) from None
    return interpret_response(response)
