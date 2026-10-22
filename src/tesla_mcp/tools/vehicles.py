"""Live vehicle reads. Each tool returns a cached snapshot when the car is not online."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context

from ..errors import TeslaError
from ..reports import weekly_mileage
from ..snapshot import data_endpoints, snapshot_from_vehicle_data
from ..time_utils import isoformat, period_window, utc_now
from ..vin import mask_vin
from .common import failure, finish, live_or_cached, require_app, select_vehicle, status_dict

_VIN_HELP = "Vehicle name or masked VIN. Omit this when the account has one car."


async def tesla_list_vehicles(ctx: Context | None = None) -> dict[str, Any]:
    """List vehicles on the Tesla account. VINs are masked. This does not wake the car."""
    try:
        fleet, _runtime = await require_app(ctx)
        vehicles = await fleet.list_vehicles()
        finish("tesla_list_vehicles", None, "ok", "data")
        return {
            "ok": True,
            "vehicles": [
                {
                    "vin_masked": mask_vin(vehicle.vin),
                    "name": vehicle.name or None,
                    "model": vehicle.model,
                    "state": vehicle.state,
                }
                for vehicle in vehicles
            ],
        }
    except TeslaError as exc:
        finish("tesla_list_vehicles", None, "error", "data")
        return failure(exc)


async def tesla_get_status(
    vin: Annotated[str | None, _VIN_HELP] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Battery, range, charging, and lock state. Live when the car is online, otherwise cached.

    Distances are miles, as returned by the Fleet API. This does not wake the car.
    """
    try:
        fleet, runtime = await require_app(ctx)
        vehicle, _all = await select_vehicle(fleet, vin)
        snapshot, freshness, observed = await live_or_cached(
            fleet, runtime, vehicle, source="status"
        )
        finish("tesla_get_status", vehicle.vin, freshness, "data")
        return status_dict(
            snapshot, name=vehicle.name, freshness=freshness, observed_state=observed
        )
    except TeslaError as exc:
        finish("tesla_get_status", vin, "error", "data")
        return failure(exc)


async def tesla_get_climate(
    vin: Annotated[str | None, _VIN_HELP] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Inside and outside temperature in Celsius, and whether climate is on. Does not wake the car."""
    try:
        fleet, runtime = await require_app(ctx)
        vehicle, _all = await select_vehicle(fleet, vin)
        snapshot, freshness, observed = await live_or_cached(
            fleet, runtime, vehicle, source="climate"
        )
        finish("tesla_get_climate", vehicle.vin, freshness, "data")
        return {
            "ok": True,
            "vin_masked": mask_vin(vehicle.vin),
            "name": vehicle.name or None,
            "freshness": freshness,
            "state": observed,
            "as_of": isoformat(snapshot.recorded_at),
            "inside_temp_c": snapshot.inside_temp_c,
            "outside_temp_c": snapshot.outside_temp_c,
            "climate_on": snapshot.climate_on,
            "driver_set_temp_c": snapshot.driver_temp_c,
            "passenger_set_temp_c": snapshot.passenger_temp_c,
        }
    except TeslaError as exc:
        finish("tesla_get_climate", vin, "error", "data")
        return failure(exc)


async def tesla_get_odometer(
    vin: Annotated[str | None, _VIN_HELP] = None,
    period: Annotated[str | None, "Optional window: 7d, 30d, or this_week."] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Odometer in miles, plus distance over a period from local snapshots. Does not wake the car."""
    try:
        fleet, runtime = await require_app(ctx)
        vehicle, _all = await select_vehicle(fleet, vin)
        snapshot, freshness, observed = await live_or_cached(
            fleet, runtime, vehicle, source="odometer"
        )
        payload: dict[str, Any] = {
            "ok": True,
            "vin_masked": mask_vin(vehicle.vin),
            "name": vehicle.name or None,
            "freshness": freshness,
            "state": observed,
            "as_of": isoformat(snapshot.recorded_at) if snapshot.odometer_mi is not None else None,
            "odometer_miles": snapshot.odometer_mi,
        }
        if period:
            start, end = period_window(period, utc_now(), runtime.settings.timezone)
            history = runtime.history.odometer_readings(vehicle.vin)
            payload["period"] = weekly_mileage(
                history, start=start, end=end, tzname=runtime.settings.timezone
            )
        finish("tesla_get_odometer", vehicle.vin, freshness, "data")
        return payload
    except (TeslaError, ValueError) as exc:
        message = exc.message if isinstance(exc, TeslaError) else str(exc)
        finish("tesla_get_odometer", vin, "error", "data")
        return {"ok": False, "error": message, "error_type": type(exc).__name__}


async def tesla_get_software(
    vin: Annotated[str | None, _VIN_HELP] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Installed software version and pending update status. Does not wake the car.

    Field names follow vehicle_state.car_version and vehicle_state.software_update.
    A missing value means this firmware did not return that field.
    """
    try:
        fleet, runtime = await require_app(ctx)
        vehicle, _all = await select_vehicle(fleet, vin)
        snapshot, freshness, observed = await live_or_cached(
            fleet, runtime, vehicle, source="software"
        )
        finish("tesla_get_software", vehicle.vin, freshness, "data")
        return {
            "ok": True,
            "vin_masked": mask_vin(vehicle.vin),
            "name": vehicle.name or None,
            "freshness": freshness,
            "state": observed,
            "as_of": isoformat(snapshot.recorded_at),
            "software_version": snapshot.software_version,
            "update_status": snapshot.software_update_status,
            "update_version": snapshot.software_update_version,
        }
    except TeslaError as exc:
        finish("tesla_get_software", vin, "error", "data")
        return failure(exc)


async def tesla_get_location(
    vin: Annotated[str | None, _VIN_HELP] = None,
    precision: Annotated[str, "coarse (about 1 km, the default) or precise."] = "coarse",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Vehicle location. Requires the vehicle_location opt-in. Coarse unless precision is precise.

    A live read can show a location icon in the car. Precise coordinates are not stored.
    History keeps coarse coordinates only. This does not wake the car.
    """
    try:
        fleet, runtime = await require_app(ctx)
        if not runtime.settings.enable_location:
            raise TeslaError("Location is off. Set TESLA_ENABLE_LOCATION=true and sign in again.")
        mode = precision.strip().lower()
        if mode not in {"coarse", "precise"}:
            raise TeslaError("precision must be coarse or precise.")
        vehicle, _all = await select_vehicle(fleet, vin)
        if vehicle.state != "online":
            cached = runtime.history.latest(vehicle.vin)
            finish("tesla_get_location", vehicle.vin, "cached", "data")
            return _location_payload(
                vehicle_vin=vehicle.vin,
                name=vehicle.name,
                freshness="cached",
                state=vehicle.state,
                latitude=cached.latitude if cached else None,
                longitude=cached.longitude if cached else None,
                as_of=isoformat(cached.recorded_at) if cached else None,
                precision="coarse",
                note="Cached coarse location. The car was not woken.",
            )
        payload = await fleet.vehicle_data(
            vehicle.vin,
            data_endpoints(include_location=True),
        )
        stored = snapshot_from_vehicle_data(
            vehicle.vin,
            payload,
            source="location",
            location_enabled=True,
        )
        stored.state = vehicle.state
        runtime.history.add_snapshot(stored)
        latitude = stored.latitude
        longitude = stored.longitude
        note = "Coarse coordinates are rounded to about 1 km. A location icon may show in the car."
        if mode == "precise":
            latitude, longitude = _precise_location(payload)
            note = (
                "Precise coordinates are in this response only. "
                "History stores the coarse pair. A location icon may show in the car."
            )
        finish("tesla_get_location", vehicle.vin, "live", "data")
        return _location_payload(
            vehicle_vin=vehicle.vin,
            name=vehicle.name,
            freshness="live",
            state=vehicle.state,
            latitude=latitude,
            longitude=longitude,
            as_of=isoformat(stored.recorded_at),
            precision=mode,
            note=note,
        )
    except TeslaError as exc:
        finish("tesla_get_location", vin, "error", "data")
        return failure(exc)


async def tesla_get_charging_history(
    start: Annotated[str, "Start date or time (YYYY-MM-DD)."],
    end: Annotated[str, "End date or time (YYYY-MM-DD)."],
    vin: Annotated[str | None, _VIN_HELP] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Charging sessions from Fleet API history. Off unless TESLA_ENABLE_CHARGING_HISTORY is set.

    The Tesla scope vehicle_charging_cmds also allows charge control. This server
    does not start, stop, or schedule charging. Whether home charging is included
    depends on Tesla's history API.
    """
    try:
        fleet, runtime = await require_app(ctx)
        vehicle, _all = await select_vehicle(fleet, vin)
        sessions = await fleet.charging_history(vehicle.vin, start, end)
        finish("tesla_get_charging_history", vehicle.vin, "ok", "data")
        return {
            "ok": True,
            "vin_masked": mask_vin(vehicle.vin),
            "start": start,
            "end": end,
            "sessions": sessions,
        }
    except TeslaError as exc:
        finish("tesla_get_charging_history", vin, "error", "data")
        return failure(exc)


def _location_payload(
    *,
    vehicle_vin: str,
    name: str,
    freshness: str,
    state: str,
    latitude: float | None,
    longitude: float | None,
    as_of: str | None,
    precision: str,
    note: str,
) -> dict[str, Any]:
    return {
        "ok": True,
        "vin_masked": mask_vin(vehicle_vin),
        "name": name or None,
        "freshness": freshness,
        "state": state,
        "precision": precision,
        "latitude": latitude,
        "longitude": longitude,
        "as_of": as_of,
        "note": note,
    }


def _precise_location(payload: dict[str, Any]) -> tuple[float | None, float | None]:
    body = payload.get("response") if isinstance(payload.get("response"), dict) else payload
    if not isinstance(body, dict):
        return None, None
    drive = body.get("drive_state") if isinstance(body.get("drive_state"), dict) else {}
    assert isinstance(drive, dict)
    lat = drive.get("latitude")
    lon = drive.get("longitude")
    if (
        isinstance(lat, (int, float))
        and isinstance(lon, (int, float))
        and not isinstance(lat, bool)
    ):
        return float(lat), float(lon)
    return None, None
