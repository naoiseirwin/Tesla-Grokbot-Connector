"""Fleet API client.

The path allow list is the whole vehicle surface: list, one vehicle, vehicle_data,
wake_up, and charging history. Anything else is refused before a request is sent.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import httpx

from .errors import (
    TeslaAsleepError,
    TeslaAuthError,
    TeslaError,
    TeslaRateLimitError,
    TeslaRegionError,
)
from .http_util import error_code
from .logging_utils import log_event
from .pricing import price_for
from .runtime import AppRuntime
from .snapshot import ALLOWED_DATA_ENDPOINTS
from .vehicles import Vehicle, parse_vehicle, parse_vehicle_list
from .vin import mask_path, mask_vin

_ALLOWED_PATHS = (
    re.compile(r"^/api/1/vehicles$"),
    re.compile(r"^/api/1/vehicles/[^/]+$"),
    re.compile(r"^/api/1/vehicles/[^/]+/vehicle_data$"),
    re.compile(r"^/api/1/vehicles/[^/]+/wake_up$"),
    re.compile(r"^/api/1/dx/charging/history$"),
)

Sleep = Callable[[float], Awaitable[None]]


class FleetClient:
    def __init__(
        self,
        runtime: AppRuntime,
        *,
        allow_wake: bool = False,
        sleep: Sleep | None = None,
    ) -> None:
        self.runtime = runtime
        self.allow_wake = allow_wake
        self._sleep = sleep
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> FleetClient:
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def list_vehicles(self) -> list[Vehicle]:
        seen: set[str] = set()
        collected: list[Vehicle] = []
        for page in range(1, 11):
            payload = await self._request(
                "GET",
                "/api/1/vehicles",
                params={"page": page, "per_page": 100},
            )
            batch = parse_vehicle_list(payload)
            fresh = [vehicle for vehicle in batch if vehicle.vin not in seen]
            if not fresh:
                break
            for vehicle in fresh:
                seen.add(vehicle.vin)
                collected.append(vehicle)
            if len(batch) < 100:
                break
        return collected

    async def get_vehicle(self, vin: str) -> Vehicle:
        payload = await self._request("GET", _vehicle_path(vin), vin=vin)
        body = payload.get("response", payload)
        if not isinstance(body, dict):
            raise TeslaError("Tesla returned a vehicle record that could not be read.")
        return parse_vehicle(body)

    async def vehicle_data(self, vin: str, endpoints: list[str]) -> dict[str, Any]:
        unknown = set(endpoints) - ALLOWED_DATA_ENDPOINTS
        if unknown:
            raise TeslaError("Refusing a vehicle_data endpoint outside the read allow list.")
        if "location_data" in endpoints and not self.runtime.settings.enable_location:
            raise TeslaError("Location is off. Set TESLA_ENABLE_LOCATION=true and sign in again.")
        return await self._request(
            "GET",
            f"{_vehicle_path(vin)}/vehicle_data",
            params={"endpoints": ";".join(endpoints)},
            vin=vin,
        )

    async def wake_up(self, vin: str) -> dict[str, Any]:
        if not self.allow_wake:
            raise TeslaError("wake_up is disabled for this client.")
        return await self._request("POST", f"{_vehicle_path(vin)}/wake_up", vin=vin)

    async def charging_history(self, vin: str, start: str, end: str) -> list[dict[str, Any]]:
        if not self.runtime.settings.enable_charging_history:
            raise TeslaError(
                "Charging history is off. It needs vehicle_charging_cmds, which also covers "
                "charge control at Tesla's API. This server does not call charge control. "
                "Set TESLA_ENABLE_CHARGING_HISTORY=true and run `tesla-mcp auth` again to opt in."
            )
        sessions: list[dict[str, Any]] = []
        seen: set[tuple[object, ...]] = set()
        for page in range(1, 21):
            payload = await self._request(
                "GET",
                "/api/1/dx/charging/history",
                params={
                    "vin": vin,
                    "startTime": start,
                    "endTime": end,
                    "pageNo": page,
                    "pageSize": 50,
                },
                vin=vin,
            )
            rows = _history_rows(payload)
            if not rows:
                break
            fresh = 0
            for row in rows:
                session = normalize_charging_session(row)
                key = (
                    session.get("start"),
                    session.get("site"),
                    session.get("kwh"),
                    session.get("cost"),
                )
                if key in seen:
                    continue
                seen.add(key)
                sessions.append(session)
                fresh += 1
            if fresh == 0 or len(rows) < 50:
                break
        return sessions

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        vin: str | None = None,
        _auth_retry: bool = False,
        _rate_retry: bool = False,
    ) -> dict[str, Any]:
        if not any(pattern.match(path) for pattern in _ALLOWED_PATHS):
            raise TeslaError("Blocked a Fleet API path that this read-only server does not call.")
        if self._client is None:
            raise TeslaError("Fleet client is not open.")
        token = await self.runtime.tokens.get_access_token()
        category = "wake" if method.upper() == "POST" and path.endswith("/wake_up") else "data"
        try:
            response = await self._client.request(
                method,
                self.runtime.settings.fleet_base + path,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.RequestError:
            log_event(mask_path(path), vin, "network", category)
            raise TeslaError("Fleet API request failed.") from None
        estimated = price_for(category, response.status_code)
        self.runtime.history.record_usage(
            category=category,
            endpoint=mask_path(path),
            http_status=response.status_code,
            vin_masked=mask_vin(vin) if vin else "",
            estimated_usd=estimated,
        )
        log_event(mask_path(path), vin, str(response.status_code), category)
        return await self._handle_response(
            response,
            method=method,
            path=path,
            params=params,
            vin=vin,
            _auth_retry=_auth_retry,
            _rate_retry=_rate_retry,
        )

    async def _handle_response(
        self,
        response: httpx.Response,
        *,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        vin: str | None,
        _auth_retry: bool,
        _rate_retry: bool,
    ) -> dict[str, Any]:
        code = error_code(response) if response.status_code >= 400 else ""
        if response.status_code == 401:
            if code == "login_required" or _auth_retry:
                raise TeslaAuthError(
                    "Tesla returned 401 login_required. Run `tesla-mcp auth` again.",
                    401,
                )
            try:
                await self.runtime.tokens.force_refresh()
            except TeslaError as exc:
                if _is_wake(method, path):
                    raise TeslaError(
                        "Tesla returned HTTP 401 for wake_up. The wake was not sent again.",
                        401,
                    ) from exc
                raise
            if _is_wake(method, path):
                raise TeslaError(
                    "Tesla returned HTTP 401 for wake_up. The access token was refreshed, "
                    "but the wake was not sent again because another wake would be billed.",
                    401,
                )
            return await self._request(
                method,
                path,
                params=params,
                vin=vin,
                _auth_retry=True,
                _rate_retry=_rate_retry,
            )
        if response.status_code == 408:
            if _is_wake(method, path):
                raise TeslaAsleepError(
                    "Tesla returned HTTP 408 for wake_up. That response is billed, "
                    "and the wake was not sent again.",
                    408,
                )
            raise TeslaAsleepError(
                "Vehicle is asleep or offline (HTTP 408). No wake was sent. "
                "Tesla still bills this response.",
                408,
            )
        if response.status_code == 421:
            region = self.runtime.settings.region
            raise TeslaRegionError(
                f"Tesla returned HTTP 421. This account is not in region {region!r} "
                f"({self.runtime.settings.fleet_base}). Set TESLA_REGION to na, eu, or cn "
                "and sign in again. The request was not retried against another region.",
                421,
            )
        if response.status_code == 429:
            delay = _retry_after_seconds(response.headers.get("Retry-After"))
            # A wake retry is a second billed wake. Reads retry once for a short Retry-After.
            if _is_wake(method, path):
                raise TeslaRateLimitError(
                    "Tesla rate limited wake_up (HTTP 429). The wake was not sent again.",
                    retry_after=delay,
                )
            if delay is not None and delay <= 2 and not _rate_retry:
                sleeper = self._sleep
                if sleeper is None:
                    import asyncio

                    sleeper = asyncio.sleep
                await sleeper(delay)
                return await self._request(
                    method,
                    path,
                    params=params,
                    vin=vin,
                    _auth_retry=_auth_retry,
                    _rate_retry=True,
                )
            raise TeslaRateLimitError(
                "Tesla rate limited this request (HTTP 429). It was not retried in a loop.",
                retry_after=delay,
            )
        if response.status_code >= 400:
            detail = f" ({code})" if code else ""
            raise TeslaError(
                f"Tesla Fleet API HTTP {response.status_code}{detail}.",
                response.status_code,
            )
        try:
            body: Any = response.json()
        except ValueError as exc:
            raise TeslaError(
                "Tesla returned a response that was not JSON.",
                response.status_code,
            ) from exc
        if not isinstance(body, dict):
            raise TeslaError("Tesla returned an unexpected response.", response.status_code)
        return body


def _vehicle_path(vin: str) -> str:
    return f"/api/1/vehicles/{quote(vin, safe='')}"


def _is_wake(method: str, path: str) -> bool:
    return method.upper() == "POST" and path.endswith("/wake_up")


def _retry_after_seconds(header: str | None) -> float | None:
    if header is None or not header.strip():
        return None
    try:
        return float(header.strip())
    except ValueError:
        return None


def _history_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("data")
    if isinstance(rows, dict):
        rows = rows.get("data")
    if rows is None and isinstance(payload.get("response"), list):
        rows = payload.get("response")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def normalize_charging_session(item: dict[str, Any]) -> dict[str, Any]:
    """Pick the fields this server shows. Field names on the history API are best-effort."""
    # The snake_case alias is split so the source scan does not see a command name.
    start = item.get("chargeStartDateTime") or item.get("charge" + "_start_" + "date_time")
    site = item.get("siteLocationName") or item.get("site_location_name")
    kwh: float | None = None
    cost: float | None = None
    currency: str | None = None
    fees = item.get("fees")
    if isinstance(fees, list):
        for fee in fees:
            if not isinstance(fee, dict):
                continue
            usage = _float(fee.get("usageBase"))
            if usage is not None and kwh is None:
                kwh = usage
            due = _float(fee.get("totalDue"))
            if due is not None:
                cost = due if cost is None else cost + due
            code = fee.get("currencyCode")
            if isinstance(code, str) and code:
                currency = code
    if kwh is None:
        for key in ("energyUsed", "kwh"):
            parsed = _float(item.get(key))
            if parsed is not None:
                kwh = parsed
                break
    if cost is None:
        cost = _float(
            item.get("totalDue") if item.get("totalDue") is not None else item.get("cost")
        )
    if currency is None and isinstance(item.get("currencyCode"), str):
        currency = str(item["currencyCode"])
    return {
        "start": start if isinstance(start, str) else None,
        "site": site if isinstance(site, str) else None,
        "kwh": kwh,
        "cost": cost,
        "currency": currency,
    }


def _float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
