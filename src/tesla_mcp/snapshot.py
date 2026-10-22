"""Turn Fleet API vehicle payloads into snapshots. Location is stored coarse, and only if enabled."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .history import Snapshot
from .time_utils import utc_now

DATA_ENDPOINTS = ("charge_state", "climate_state", "vehicle_state")
LOCATION_ENDPOINT = "location_data"
ALLOWED_DATA_ENDPOINTS = frozenset({*DATA_ENDPOINTS, LOCATION_ENDPOINT})


def data_endpoints(*, include_location: bool) -> list[str]:
    endpoints: list[str] = list(DATA_ENDPOINTS)
    if include_location:
        endpoints.append(LOCATION_ENDPOINT)
    return endpoints


def coarse_coordinate(value: float) -> float:
    """Round to 0.01 degrees, about 1.1 km of latitude."""
    return round(value * 100) / 100


def _num(value: object) -> float | None:
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


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def plugged_in(charge: dict[str, Any]) -> bool | None:
    state = charge.get("charging_state")
    if state is None:
        return None
    return str(state).strip().lower() not in {"disconnected", "null", ""}


def minutes_to_full(charge: dict[str, Any]) -> float | None:
    minutes = _num(charge.get("minutes_to_full_charge"))
    if minutes is not None:
        return minutes
    hours = _num(charge.get("time_to_full_charge"))
    if hours is None:
        return None
    return hours * 60


def snapshot_from_vehicle_data(
    vin: str,
    payload: dict[str, Any],
    *,
    source: str,
    location_enabled: bool,
    recorded_at: datetime | None = None,
) -> Snapshot:
    body = payload.get("response") if isinstance(payload.get("response"), dict) else payload
    if not isinstance(body, dict):
        body = {}
    charge = body.get("charge_state") if isinstance(body.get("charge_state"), dict) else {}
    climate = body.get("climate_state") if isinstance(body.get("climate_state"), dict) else {}
    vehicle = body.get("vehicle_state") if isinstance(body.get("vehicle_state"), dict) else {}
    assert isinstance(charge, dict)
    assert isinstance(climate, dict)
    assert isinstance(vehicle, dict)
    software = vehicle.get("software_update")
    update_status = None
    update_version = None
    if isinstance(software, dict):
        update_status = _text(software.get("status"))
        update_version = _text(software.get("version"))
    elif isinstance(software, str):
        update_status = software.strip() or None
    latitude = None
    longitude = None
    if location_enabled:
        latitude, longitude = _coarse_location(body)
    locked = vehicle.get("locked")
    climate_on = climate.get("is_climate_on")
    return Snapshot(
        vin=vin,
        recorded_at=recorded_at or utc_now(),
        state=_text(body.get("state")) or "online",
        live=True,
        source=source,
        battery_level=_num(charge.get("battery_level")),
        battery_range_mi=_num(charge.get("battery_range")),
        est_battery_range_mi=_num(charge.get("est_battery_range")),
        charging_state=_text(charge.get("charging_state")),
        charge_limit_soc=_num(charge.get("charge_limit_soc")),
        charge_power_kw=_num(charge.get("charger_power")),
        minutes_to_full=minutes_to_full(charge),
        plugged_in=plugged_in(charge),
        locked=bool(locked) if isinstance(locked, bool) else None,
        inside_temp_c=_num(climate.get("inside_temp")),
        outside_temp_c=_num(climate.get("outside_temp")),
        climate_on=bool(climate_on) if isinstance(climate_on, bool) else None,
        driver_temp_c=_num(climate.get("driver_temp_setting")),
        passenger_temp_c=_num(climate.get("passenger_temp_setting")),
        odometer_mi=_num(vehicle.get("odometer")),
        software_version=_text(vehicle.get("car_version")),
        software_update_status=update_status,
        software_update_version=update_version,
        latitude=latitude,
        longitude=longitude,
    )


def raw_location(body: dict[str, Any]) -> tuple[float | None, float | None]:
    """Latitude and longitude from drive_state, then location_data. Not rounded."""
    drive = body.get("drive_state") if isinstance(body.get("drive_state"), dict) else {}
    location = body.get("location_data") if isinstance(body.get("location_data"), dict) else {}
    assert isinstance(drive, dict)
    assert isinstance(location, dict)
    lat = _num(drive.get("latitude"))
    lon = _num(drive.get("longitude"))
    if lat is None or lon is None:
        lat = _num(location.get("latitude"))
        lon = _num(location.get("longitude"))
    if lat is None or lon is None:
        return None, None
    return lat, lon


def _coarse_location(body: dict[str, Any]) -> tuple[float | None, float | None]:
    lat, lon = raw_location(body)
    if lat is None or lon is None:
        return None, None
    return coarse_coordinate(lat), coarse_coordinate(lon)


def state_only_snapshot(
    vin: str,
    state: str,
    *,
    source: str,
    recorded_at: datetime | None = None,
) -> Snapshot:
    return Snapshot(
        vin=vin,
        recorded_at=recorded_at or utc_now(),
        state=state,
        live=False,
        source=source,
    )
