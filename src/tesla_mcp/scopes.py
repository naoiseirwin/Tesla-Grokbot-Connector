"""OAuth scopes. Command and profile scopes are never requested."""

from __future__ import annotations

import base64
import json
from typing import Any

BASE_SCOPES: tuple[str, ...] = ("openid", "offline_access", "vehicle_device_data")
LOCATION_SCOPE = "vehicle_location"
CHARGING_HISTORY_SCOPE = "vehicle_charging_cmds"
VEHICLE_COMMANDS_SCOPE = "vehicle_cmds"

# vehicle_cmds is vehicle commands. user_data exposes home address and contact info.
# The read-only connector never requests either. The commands connector may
# request vehicle_cmds only when the caller passes commands=True.
FORBIDDEN_SCOPES: frozenset[str] = frozenset({VEHICLE_COMMANDS_SCOPE, "user_data"})

ALLOWED_SCOPES: frozenset[str] = frozenset({*BASE_SCOPES, LOCATION_SCOPE, CHARGING_HISTORY_SCOPE})


def requested_scopes(
    *,
    location: bool,
    charging_history: bool,
    commands: bool = False,
) -> list[str]:
    """Scopes to send on the authorize URL.

    Location and charging history are off unless the operator opted in.
    Charging history uses vehicle_charging_cmds, which also permits charge
    control at Tesla's API. The read-only process still has no charge-control call.

    `commands=True` adds vehicle_cmds. The read-only sign-in does not pass
    that flag. user_data is never requested.
    """
    scopes = list(BASE_SCOPES)
    if location:
        scopes.append(LOCATION_SCOPE)
    if charging_history:
        scopes.append(CHARGING_HISTORY_SCOPE)
    if not commands:
        refused = FORBIDDEN_SCOPES.intersection(scopes)
        if refused or not set(scopes).issubset(ALLOWED_SCOPES):
            raise ValueError("Refusing to request a scope outside the read-only allow list.")
        return scopes
    scopes.append(VEHICLE_COMMANDS_SCOPE)
    allowed = ALLOWED_SCOPES | {VEHICLE_COMMANDS_SCOPE}
    if "user_data" in scopes or not set(scopes).issubset(allowed):
        raise ValueError("Refusing to request a scope outside the allow list.")
    return scopes


def decode_jwt_payload(token: str) -> dict[str, Any] | None:
    """Decode a JWT payload without verifying the signature.

    The payload is only used to read the granted scope list. It is not a
    trust decision about who signed the token.
    """
    parts = token.split(".")
    if len(parts) < 2 or not parts[1]:
        return None
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def scopes_from_access_token(token: str) -> list[str] | None:
    """Return granted scopes, or None when the token is not a readable JWT."""
    payload = decode_jwt_payload(token)
    if payload is None:
        return None
    claim = payload.get("scp", payload.get("scope"))
    if claim is None:
        return []
    if isinstance(claim, str):
        return [part for part in claim.split() if part]
    if isinstance(claim, list):
        return [str(part) for part in claim if str(part)]
    return []


def scope_report(
    *,
    requested: list[str],
    granted: list[str] | None,
) -> str:
    """Human-readable scope check for startup and `tesla-mcp scopes`."""
    lines = [f"Requested: {' '.join(requested)}"]
    if granted is None:
        lines.append("Granted: unknown until `tesla-mcp auth` stores a token.")
        return "\n".join(lines)
    lines.append(f"Granted: {' '.join(granted) if granted else '(none)'}")
    missing = [scope for scope in requested if scope not in granted]
    if missing:
        lines.append(
            "Missing: "
            + " ".join(missing)
            + ". Run `tesla-mcp auth` again. The authorize URL sets prompt_missing_scopes."
        )
    extra = [scope for scope in granted if scope not in requested]
    if extra:
        lines.append("Also granted, unused by this server: " + " ".join(extra))
    if "vehicle_cmds" in granted:
        lines.append(
            "vehicle_cmds is granted. The read-only server does not call command endpoints. "
            "The optional commands connector uses this scope only when TESLA_ENABLE_COMMANDS=true."
        )
    if CHARGING_HISTORY_SCOPE in granted:
        lines.append(
            "vehicle_charging_cmds is granted. Tesla treats that scope as charge control "
            "as well as charging history. This server exposes history only, and only when "
            "TESLA_ENABLE_CHARGING_HISTORY is true."
        )
    return "\n".join(lines)
