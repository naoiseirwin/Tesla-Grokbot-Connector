"""One-time partner registration. This does not read or write a private key."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from .auth_flow import exchange_client_credentials
from .config import Settings
from .errors import TeslaError


def normalize_domain(value: str) -> str:
    raw = value.strip()
    if "://" in raw:
        parsed = urlparse(raw)
        host = parsed.hostname or ""
    else:
        host = raw.split("/")[0]
    host = host.strip(".").lower()
    if not host or " " in host or "." not in host:
        raise TeslaError(
            "Pass the site domain only, for example naoiseirwin.github.io. "
            "It must match the root of an allowed origin on the Tesla app."
        )
    return host


async def register_partner(settings: Settings, domain: str) -> dict[str, object]:
    """Register the partner domain in the configured region and read back the public key."""
    settings.require_client()
    host = normalize_domain(domain)
    token = await exchange_client_credentials(settings)
    access = token.get("access_token")
    if not isinstance(access, str) or not access:
        raise TeslaError("Partner token request did not return an access token.")
    headers = {"Authorization": f"Bearer {access}"}
    base = settings.fleet_base
    async with httpx.AsyncClient(timeout=30.0) as client:
        registered = await client.post(
            f"{base}/api/1/partner_accounts",
            headers=headers,
            json={"domain": host},
        )
        checked = await client.get(
            f"{base}/api/1/partner_accounts/public_key",
            headers=headers,
            params={"domain": host},
        )
    preview = ""
    try:
        body = checked.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        response = body.get("response")
        if isinstance(response, dict):
            key = response.get("public_key")
            if isinstance(key, str):
                preview = key.splitlines()[0]
        elif isinstance(response, str):
            preview = response.splitlines()[0]
    return {
        "domain": host,
        "region": settings.region,
        "fleet_api": base,
        "register_status": registered.status_code,
        "public_key_status": checked.status_code,
        "public_key_preview": preview,
        "note": (
            "Register once per region. The public key must stay hosted at "
            f"https://{host}/.well-known/appspecific/com.tesla.3p.public-key.pem. "
            "This command does not use the private key."
        ),
    }
