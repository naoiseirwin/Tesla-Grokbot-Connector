"""Shared tool helpers. Live reads never wake the car."""

from __future__ import annotations

from typing import Any

from fastmcp import Context

from ..errors import TeslaAsleepError, TeslaError
from ..fleet import FleetClient
from ..history import Snapshot
from ..logging_utils import log_event
from ..runtime import AppRuntime
from ..snapshot import data_endpoints, snapshot_from_vehicle_data, state_only_snapshot
from ..time_utils import isoformat
from ..vehicles import Vehicle, resolve_vehicle
from ..vin import mask_vin


def failure(exc: TeslaError) -> dict[str, Any]:
    return {"ok": False, "error": exc.message, "error_type": type(exc).__name__}


async def require_app(ctx: Context | None) -> tuple[FleetClient, AppRuntime]:
    if ctx is None:
        raise TeslaError("Tool called without an MCP context.")
    try:
        fleet = await ctx.get_state("fleet")
        app = await ctx.get_state("app")
    except Exception as exc:
        raise TeslaError("Tesla client is not available.") from exc
    if not isinstance(fleet, FleetClient) or not isinstance(app, AppRuntime):
        raise TeslaError("Tesla client is not available.")
    return fleet, app


async def select_vehicle(fleet: FleetClient, selector: str | None) -> tuple[Vehicle, list[Vehicle]]:
    vehicles = await fleet.list_vehicles()
    return resolve_vehicle(vehicles, selector), vehicles


async def live_or_cached(
    fleet: FleetClient,
    runtime: AppRuntime,
    vehicle: Vehicle,
    *,
    source: str,
    include_location: bool = False,
) -> tuple[Snapshot, str, str]:
    """Return snapshot, freshness (live|cached), and the state observed now.

    The vehicle list already carries `state`, so an asleep car is not asked for
    vehicle_data. HTTP 408 falls back to the cache and does not wake the car.
    """
    observed = vehicle.state
    if observed != "online":
        cached = runtime.history.latest(vehicle.vin)
        if cached is not None:
            return cached, "cached", observed
        return state_only_snapshot(vehicle.vin, observed, source=source), "cached", observed
    try:
        payload = await fleet.vehicle_data(
            vehicle.vin,
            data_endpoints(include_location=include_location and runtime.settings.enable_location),
        )
    except TeslaAsleepError:
        cached = runtime.history.latest(vehicle.vin)
        if cached is not None:
            return cached, "cached", "asleep"
        return state_only_snapshot(vehicle.vin, "asleep", source=source), "cached", "asleep"
    snapshot = snapshot_from_vehicle_data(
        vehicle.vin,
        payload,
        source=source,
        location_enabled=include_location and runtime.settings.enable_location,
    )
    snapshot.state = observed
    runtime.history.add_snapshot(snapshot)
    return snapshot, "live", observed


def status_dict(
    snapshot: Snapshot,
    *,
    name: str,
    freshness: str,
    observed_state: str,
) -> dict[str, Any]:
    has_data = snapshot.battery_level is not None or snapshot.odometer_mi is not None
    note = None
    if freshness != "live":
        if has_data:
            note = "Cached snapshot. The car was not woken."
        else:
            note = (
                "The car is not online and there is no local snapshot yet. The car was not woken."
            )
    return {
        "ok": True,
        "vin_masked": mask_vin(snapshot.vin),
        "name": name or None,
        "freshness": freshness,
        "state": observed_state,
        "as_of": isoformat(snapshot.recorded_at) if has_data or freshness == "live" else None,
        "battery_level_percent": snapshot.battery_level,
        "battery_range_miles": snapshot.battery_range_mi,
        "est_battery_range_miles": snapshot.est_battery_range_mi,
        "charging_state": snapshot.charging_state,
        "charge_limit_percent": snapshot.charge_limit_soc,
        "charge_power_kw": snapshot.charge_power_kw,
        "minutes_to_full_charge": snapshot.minutes_to_full,
        "plugged_in": snapshot.plugged_in,
        "locked": snapshot.locked,
        "note": note,
    }


def finish(tool: str, vin: str | None, status: str, category: str | None = None) -> None:
    log_event(tool, vin, status, category)
