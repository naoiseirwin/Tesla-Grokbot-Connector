"""Shared HTTP helpers. Error text is the Tesla error code only, never a raw body."""

from __future__ import annotations

from typing import Any

import httpx

from .logging_utils import redact


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
