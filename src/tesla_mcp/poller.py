"""No-wake poller.

`GET /api/1/vehicles` already includes `state`. vehicle_data is called only
when that state is online. This module never wakes the car.
"""

from __future__ import annotations

from .fleet import FleetClient
from .logging_utils import log_event
from .runtime import AppRuntime
from .snapshot import data_endpoints, snapshot_from_vehicle_data, state_only_snapshot
from .vin import mask_vin


async def poll_once(runtime: AppRuntime) -> dict[str, object]:
    results: list[dict[str, object]] = []
    async with FleetClient(runtime, allow_wake=False) as fleet:
        vehicles = await fleet.list_vehicles()
        for vehicle in vehicles:
            if vehicle.state == "online":
                payload = await fleet.vehicle_data(
                    vehicle.vin,
                    data_endpoints(include_location=runtime.settings.enable_location),
                )
                snapshot = snapshot_from_vehicle_data(
                    vehicle.vin,
                    payload,
                    source="poll",
                    location_enabled=runtime.settings.enable_location,
                )
                snapshot.state = "online"
            else:
                snapshot = state_only_snapshot(vehicle.vin, vehicle.state, source="poll")
            runtime.history.add_snapshot(snapshot)
            log_event("poll", vehicle.vin, snapshot.state, "data")
            results.append(
                {
                    "vin_masked": mask_vin(vehicle.vin),
                    "name": vehicle.name or None,
                    "state": snapshot.state,
                    "live": snapshot.live,
                }
            )
    return {"ok": True, "vehicles": results, "woke": False}
