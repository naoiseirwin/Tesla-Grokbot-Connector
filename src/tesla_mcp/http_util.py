"""Shared HTTP helpers. Error text is the Tesla error code only, never a raw body."""

from __future__ import annotations

from typing import Any

import httpx


def error_code(response: httpx.Response) -> str:
    try:
        body: Any = response.json()
    except (ValueError, UnicodeError):
        return ""
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        message = error.get("message") or error.get("code") or ""
        return str(message)
    description = body.get("error_description")
    if isinstance(description, str):
        return description
    return ""
