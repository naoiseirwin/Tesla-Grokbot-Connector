"""Append-only local audit log for command attempts.

The file is JSONL. It records the attempt, the outcome, and any refusal.
It never stores a token, a client secret, or an unmasked VIN.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from tesla_mcp.config import Settings
from tesla_mcp.logging_utils import redact
from tesla_mcp.vin import mask_vin

# A VIN anywhere in a longer string, not only a value that is exactly 17 characters.
_VIN_EMBEDDED = re.compile(
    r"(?<![A-HJ-NPR-Z0-9])([A-HJ-NPR-Z0-9]{17})(?![A-HJ-NPR-Z0-9])",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\bbearer\s+\S+")
# JWT-shaped text, including a token Tesla echoes without the word "bearer".
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]+)*")
_SECRET_KEYS = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "secret",
        "password",
        "client_secret",
        "vin",
    }
)


def audit_path(settings: Settings) -> Path:
    return settings.data_dir / "command-audit.jsonl"


def scrub_text(text: str, *, secrets: Iterable[str] = ()) -> str:
    """Remove tokens and mask VINs in text that a person or a log will see.

    Known secret values are removed first, then bearer tokens and JWT-shaped
    strings, then any 17-character VIN, including one inside a longer sentence.
    """
    cleaned = text
    known = [secret for secret in secrets if isinstance(secret, str) and len(secret) >= 12]
    for secret in sorted(set(known), key=len, reverse=True):
        cleaned = cleaned.replace(secret, "[redacted]")
    cleaned = _BEARER.sub("Bearer [redacted]", cleaned)
    cleaned = _JWT.sub("[redacted]", cleaned)
    cleaned = _VIN_EMBEDDED.sub(lambda match: mask_vin(match.group(1)), cleaned)
    return redact(cleaned)


def scrub_value(value: Any, *, secrets: Iterable[str] = ()) -> Any:
    """Scrub strings inside a JSON-like value. Other types pass through."""
    if isinstance(value, str):
        return scrub_text(value, secrets=secrets)
    if isinstance(value, dict):
        return {str(key): scrub_value(item, secrets=secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_value(item, secrets=secrets) for item in value]
    return value


def public_selector(selector: str | None, *, secrets: Iterable[str] = ()) -> str:
    if selector is None or not str(selector).strip():
        return "-"
    return scrub_text(str(selector).strip(), secrets=secrets)


def public_parameters(
    parameters: dict[str, Any],
    *,
    secrets: Iterable[str] = (),
) -> dict[str, Any]:
    """Copy tool arguments, dropping secrets and masking a VIN inside a string."""
    clean: dict[str, Any] = {}
    for key, value in parameters.items():
        if str(key).lower() in _SECRET_KEYS:
            continue
        if isinstance(value, str):
            clean[str(key)] = scrub_text(value, secrets=secrets)
        elif isinstance(value, (int, float, bool)) or value is None:
            clean[str(key)] = value
    return clean


class AuditLog:
    def __init__(self, settings: Settings) -> None:
        self.path = audit_path(settings)

    def append(
        self,
        record: dict[str, Any],
        *,
        raw_vin: str | None = None,
        secrets: Iterable[str] = (),
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        # Secrets become [redacted]. VINs, including one inside a longer selector, are masked.
        line = scrub_text(line, secrets=secrets)
        data = (line + "\n").encode("utf-8")
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(self.path, 0o600)
