"""Fleet API payloads for tests. Field names follow the public vehicle_data shape."""

from __future__ import annotations

from typing import Any

from tests.helpers import VIN


def vehicle_list(
    *,
    state: str = "online",
    vin: str = VIN,
    name: str = "Blue",
    extra: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    vehicles = [
        {
            "id": 1,
            "vehicle_id": 1,
            "vin": vin,
            "display_name": name,
            "state": state,
        }
    ]
    if extra:
        vehicles.extend(extra)
    return {"response": vehicles, "count": len(vehicles)}


def vehicle_data(
    *,
    vin: str = VIN,
    latitude: float = 53.3498,
    longitude: float = -6.2603,
) -> dict[str, Any]:
    return {
        "response": {
            "vin": vin,
            "state": "online",
            "charge_state": {
                "battery_level": 72,
                "battery_range": 210.5,
                "est_battery_range": 200.0,
                "charging_state": "Disconnected",
                "charge_limit_soc": 80,
                "charger_power": 0,
                "time_to_full_charge": 0,
            },
            "climate_state": {
                "inside_temp": 21.5,
                "outside_temp": 12.0,
                "is_climate_on": False,
                "driver_temp_setting": 21.0,
                "passenger_temp_setting": 21.0,
            },
            "vehicle_state": {
                "locked": True,
                "odometer": 12345.6,
                "car_version": "2026.20.3",
                "software_update": {"status": "", "version": ""},
            },
            "drive_state": {"latitude": latitude, "longitude": longitude},
        }
    }


def charging_page() -> dict[str, Any]:
    return {
        "data": [
            {
                "chargeStartDateTime": "2026-09-02T10:00:00Z",
                "siteLocationName": "Example Supercharger",
                "fees": [
                    {
                        "feeType": "CHARGING",
                        "totalDue": 12.5,
                        "currencyCode": "EUR",
                        "usageBase": 40.0,
                    }
                ],
            }
        ],
        "totalResults": 1,
    }
