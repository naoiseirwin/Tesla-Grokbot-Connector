"""Shared test helpers. Tokens here are fixtures, not live credentials."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from tesla_mcp.config import Settings
from tesla_mcp.scopes import BASE_SCOPES
from tesla_mcp.token_store import TokenRecord, TokenStore

VIN = "5YJ3E1EA7KF000001"
FLEET = "https://fleet.test"
TOKEN_URL = "https://auth.test/token"


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    data: dict[str, object] = {
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "region": "na",
        "redirect_uri": "http://localhost:8765/callback",
        "token_key": b"k" * 32,
        "data_dir": tmp_path / "data",
        "enable_location": False,
        "enable_charging_history": False,
        "wake_daily_cap": 3,
        "wake_min_interval_seconds": 1800,
        "wake_per_minute_cap": 3,
        "poll_interval_hours": 2,
        "fleet_api_base": FLEET,
        "token_url": TOKEN_URL,
        "auth_url": "https://auth.test/authorize",
        "timezone": "UTC",
    }
    data.update(overrides)
    return Settings(**data)  # type: ignore[arg-type]


def make_jwt(scopes: list[str]) -> str:
    def encode(payload: dict[str, object]) -> str:
        raw = json.dumps(payload).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    header = encode({"alg": "none", "typ": "JWT"})
    body = encode({"scp": scopes, "exp": 9999999999})
    return f"{header}.{body}.sig"


def seed_token(
    settings: Settings,
    scopes: list[str] | None = None,
    *,
    refresh: str = "refresh-token-test",
    expires_at: float | None = None,
) -> TokenRecord:
    granted = list(scopes or BASE_SCOPES)
    record = TokenRecord(
        access_token=make_jwt(granted),
        refresh_token=refresh,
        expires_at=time.time() + 3600 if expires_at is None else expires_at,
        scopes=granted,
        region=settings.region,
    )
    TokenStore(settings).save(record)
    return record


def tool_json(result: object) -> dict[str, object]:
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    content = getattr(result, "content", None)
    if isinstance(content, list) and content:
        text = getattr(content[0], "text", None)
        if isinstance(text, str):
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    raise AssertionError(f"Could not read tool payload from {result!r}")
