"""VIN masking for logs and tool responses."""

from __future__ import annotations

import re

_VIN_IN_PATH = re.compile(r"(?<=/vehicles/)[^/?]+")


def mask_vin(vin: str | None) -> str:
    """Mask a VIN as the first three and last four characters."""
    if not vin:
        return "-"
    cleaned = vin.strip()
    if len(cleaned) < 8:
        return "•••"
    return f"{cleaned[:3]}…{cleaned[-4:]}"


def mask_path(path: str) -> str:
    """Replace a VIN embedded in a Fleet API path."""
    base = path.split("?", 1)[0]

    def _replace(match: re.Match[str]) -> str:
        return mask_vin(match.group(0))

    return _VIN_IN_PATH.sub(_replace, base)
