"""Logs name the tool, a masked VIN, the status, and the cost category.

They do not include tokens, raw Fleet API bodies, or coordinates.
"""

from __future__ import annotations

import logging
import re
import sys

logger = logging.getLogger("tesla_mcp")

_SECRET_RE = re.compile(
    r"(?i)(bearer\s+)\S+"
    r"|((?:refresh_token|access_token|client_secret|id_token|TESLA_TOKEN_KEY)"
    r"(?:['\"]?\s*[:=]\s*['\"]?))[^\s,'\"&]+"
)
_COORD_RE = re.compile(r"(?i)(latitude|longitude|lat|lon)(['\"]?\s*[:=]\s*['\"]?)-?\d+(?:\.\d+)?")


def redact(text: str) -> str:
    cleaned = _SECRET_RE.sub(lambda match: f"{match.group(1) or match.group(2)}[redacted]", text)
    return _COORD_RE.sub(lambda match: f"{match.group(1)}=[redacted]", cleaned)


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(arg) if isinstance(arg, str) else arg for arg in record.args)
        return True


def configure_logging(*, stderr: bool = False) -> None:
    logger.setLevel(logging.INFO)
    if not any(isinstance(item, RedactFilter) for item in logger.filters):
        logger.addFilter(RedactFilter())
    if stderr and not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.addFilter(RedactFilter())
        logger.addHandler(handler)


def log_event(tool: str, vin: str | None, status: str, category: str | None = None) -> None:
    """One structured line. `vin` may be raw; it is masked here."""
    from .vin import mask_vin

    logger.info(
        "tool=%s vin=%s status=%s category=%s",
        tool,
        mask_vin(vin) if vin else "-",
        status,
        category or "-",
    )
