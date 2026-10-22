"""Fleet API regions. China uses a separate developer application."""

from __future__ import annotations

from dataclasses import dataclass

TOKEN_URL = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
AUTHORIZE_URL = "https://auth.tesla.com/oauth2/v3/authorize"
DEFAULT_REDIRECT_URI = "http://localhost:8765/callback"
DEFAULT_CALLBACK_PORT = 8765


@dataclass(frozen=True)
class Region:
    code: str
    fleet_api: str
    label: str


REGIONS: dict[str, Region] = {
    "na": Region(
        code="na",
        fleet_api="https://fleet-api.prd.na.vn.cloud.tesla.com",
        label="North America and Asia-Pacific (except China)",
    ),
    "eu": Region(
        code="eu",
        fleet_api="https://fleet-api.prd.eu.vn.cloud.tesla.com",
        label="Europe, Middle East, and Africa",
    ),
    "cn": Region(
        code="cn",
        fleet_api="https://fleet-api.prd.cn.vn.cloud.tesla.cn",
        label="China (separate app on developer.tesla.cn)",
    ),
}


def get_region(code: str) -> Region:
    key = code.strip().lower()
    region = REGIONS.get(key)
    if region is None:
        known = ", ".join(sorted(REGIONS))
        raise ValueError(f"TESLA_REGION must be one of {known}. Got {code!r}.")
    return region
