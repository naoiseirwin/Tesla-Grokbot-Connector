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

_VIN_LIKE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.IGNORECASE)
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


def public_selector(selector: str | None) -> str:
    if selector is None or not str(selector).strip():
        return "-"
    text = str(selector).strip()
    if _VIN_LIKE.match(text):
        return mask_vin(text)
    return text


def public_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Copy tool arguments, dropping secrets and masking a VIN-shaped string."""
    clean: dict[str, Any] = {}
    for key, value in parameters.items():
        if str(key).lower() in _SECRET_KEYS:
            continue
        if isinstance(value, str) and _VIN_LIKE.match(value.strip()):
            clean[str(key)] = mask_vin(value.strip())
        elif isinstance(value, (str, int, float, bool)) or value is None:
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
        if raw_vin and len(raw_vin.strip()) >= 8:
            line = line.replace(raw_vin.strip(), mask_vin(raw_vin))
        for secret in secrets:
            if isinstance(secret, str) and len(secret) >= 12:
                line = line.replace(secret, "[redacted]")
        line = redact(line)
        data = (line + "\n").encode("utf-8")
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(self.path, 0o600)
