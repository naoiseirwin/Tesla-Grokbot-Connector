"""Shared HTTP helpers.

Error text is a short Tesla code, or a safe `message` string from a 4xx body.
Raw bodies, tokens, and secrets are never returned.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from .logging_utils import redact

# A JWT header, a long base64 blob, or a long hex string. Ordinary sentences
# do not match. `redact` covers bearer tokens and known secret field names.
_OPAQUE_SECRET = re.compile(
    r"eyJ[A-Za-z0-9_-]{10,}"
    r"|(?:^|\s)[A-Za-z0-9+/]{32,}={0,2}(?:\s|$)"
    r"|(?:^|\s)[A-Fa-f0-9]{32,}(?:\s|$)"
)
_MAX_ERROR_MESSAGE = 200


def error_code(response: httpx.Response) -> str:
    """Short Tesla error code for messages. Not the raw body."""
    try:
        body: Any = response.json()
    except (ValueError, UnicodeError):
        return ""
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    text = ""
    if isinstance(error, str):
        text = error
    elif isinstance(error, dict):
        message = error.get("code") or error.get("message") or ""
        text = str(message)
    else:
        description = body.get("error_description")
        if isinstance(description, str):
            text = description
    cleaned = redact(" ".join(text.split()))
    if len(cleaned) > 120:
        return cleaned[:120]
    return cleaned


def safe_error_message(response: httpx.Response) -> str:
    """Return Tesla's top-level `message` when it cannot carry a secret.

    An empty string means the caller should keep the HTTP status alone.
    """
    try:
        body: Any = response.json()
    except (ValueError, UnicodeError):
        return ""
    if not isinstance(body, dict):
        return ""
    raw = body.get("message")
    if not isinstance(raw, str):
        return ""
    collapsed = " ".join(raw.split())
    if not collapsed or redact(collapsed) != collapsed or _OPAQUE_SECRET.search(collapsed):
        return ""
    if len(collapsed) > _MAX_ERROR_MESSAGE:
        return collapsed[:_MAX_ERROR_MESSAGE].rstrip() + "…"
    return collapsed
