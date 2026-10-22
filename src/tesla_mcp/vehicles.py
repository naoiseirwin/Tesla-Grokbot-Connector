"""Vehicle list parsing and selection. Tool output uses a masked VIN."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import TeslaError
from .vin import mask_vin


@dataclass(frozen=True)
class Vehicle:
    vin: str
    name: str
    state: str
    model: str | None


def parse_vehicle(item: dict[str, Any]) -> Vehicle:
    vin = item.get("vin")
    if not isinstance(vin, str) or not vin:
        raise TeslaError("Tesla returned a vehicle without a VIN.")
    model = item.get("model") or item.get("car_type")
    name = item.get("display_name")
    state = item.get("state")
    return Vehicle(
        vin=vin,
        name=name.strip() if isinstance(name, str) else "",
        state=state.strip() if isinstance(state, str) and state.strip() else "unknown",
        model=model.strip() if isinstance(model, str) and model.strip() else None,
    )


def parse_vehicle_list(payload: dict[str, Any]) -> list[Vehicle]:
    body = payload.get("response", payload.get("vehicles"))
    if isinstance(body, dict):
        return [parse_vehicle(body)]
    if not isinstance(body, list):
        return []
    vehicles: list[Vehicle] = []
    for item in body:
        if isinstance(item, dict):
            vehicles.append(parse_vehicle(item))
    return vehicles


def public_vehicle(vehicle: Vehicle) -> dict[str, str | None]:
    return {
        "vin_masked": mask_vin(vehicle.vin),
        "name": vehicle.name or None,
        "model": vehicle.model,
        "state": vehicle.state,
    }


def resolve_vehicle(vehicles: list[Vehicle], selector: str | None) -> Vehicle:
    if not vehicles:
        raise TeslaError("No vehicles on this Tesla account.")
    if selector is None or not selector.strip():
        if len(vehicles) == 1:
            return vehicles[0]
        labels = ", ".join(vehicle.name or mask_vin(vehicle.vin) for vehicle in vehicles)
        raise TeslaError(f"More than one vehicle ({labels}). Pass the name or masked VIN.")
    wanted = selector.strip().lower().replace("...", "…")
    for vehicle in vehicles:
        if vehicle.vin.lower() == wanted:
            return vehicle
        if mask_vin(vehicle.vin).lower() == wanted:
            return vehicle
        if vehicle.name and vehicle.name.lower() == wanted:
            return vehicle
    matches = [vehicle for vehicle in vehicles if vehicle.vin.lower().endswith(wanted)]
    if len(matches) == 1:
        return matches[0]
    raise TeslaError("No vehicle matched that name or VIN.")
