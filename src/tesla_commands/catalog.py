"""The v1 command list.

Tier 1 and tier 2 only: climate, charge limit and start/stop, lights, horn,
lock, and sentry mode on. Unlock, trunks, windows, remote start, summon,
sentry off, valet, speed limit, PINs, navigation, and software updates are
not commands this package can build.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tesla_commands.errors import CommandRefusal

MIN_CHARGE_PERCENT = 50
MAX_CHARGE_PERCENT = 100
MIN_CABIN_C = 15.0
MAX_CABIN_C = 28.0
MIN_SEAT_LEVEL = 0
MAX_SEAT_LEVEL = 3

# Tesla's remote_seat_heater_request documents heater indexes 0 through 5.
SEAT_NAMES: dict[int, str] = {
    0: "front left",
    1: "front right",
    2: "rear left",
    3: "rear center",
    4: "rear right",
    5: "third row",
}

BodyBuilder = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class CommandSpec:
    name: str
    tool_name: str
    endpoint: str
    summary: str
    idempotent: bool
    build: BodyBuilder


def _empty(_parameters: dict[str, Any]) -> dict[str, Any]:
    return {}


def _sentry_on(_parameters: dict[str, Any]) -> dict[str, Any]:
    """Sentry can only be turned on. There is no off command in this package."""
    return {"on": True}


def _whole_number(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def _celsius(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CommandRefusal(
            "invalid_input",
            f"{label} must be a temperature in Celsius between "
            f"{MIN_CABIN_C:g} and {MAX_CABIN_C:g}. Nothing was sent to the car.",
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise CommandRefusal(
            "invalid_input",
            f"{label} must be a real temperature between "
            f"{MIN_CABIN_C:g} and {MAX_CABIN_C:g} °C. Nothing was sent to the car.",
        )
    number = round(float(value), 1)
    if number < MIN_CABIN_C or number > MAX_CABIN_C:
        raise CommandRefusal(
            "invalid_input",
            f"Cabin temperature must be between {MIN_CABIN_C:g} and {MAX_CABIN_C:g} °C, "
            "which is the range the car's climate control accepts. "
            f"{number:g} is outside that range. Nothing was sent to the car.",
        )
    return number


def _temps(parameters: dict[str, Any]) -> dict[str, Any]:
    driver = _celsius(parameters.get("driver_celsius"), "driver_celsius")
    passenger_raw = parameters.get("passenger_celsius")
    passenger = driver if passenger_raw is None else _celsius(passenger_raw, "passenger_celsius")
    return {"driver_temp": driver, "passenger_temp": passenger}


def _seat(parameters: dict[str, Any]) -> dict[str, Any]:
    seat = _whole_number(parameters.get("seat"))
    level = _whole_number(parameters.get("level"))
    if seat is None or seat not in SEAT_NAMES:
        names = ", ".join(f"{index} {name}" for index, name in SEAT_NAMES.items())
        raise CommandRefusal(
            "invalid_input",
            "Seat must be a whole number from 0 to 5 "
            f"({names}). {parameters.get('seat')!r} is not one of those seats. "
            "Nothing was sent to the car.",
        )
    if level is None or level < MIN_SEAT_LEVEL or level > MAX_SEAT_LEVEL:
        raise CommandRefusal(
            "invalid_input",
            "Seat heater level must be a whole number from 0 (off) to 3. "
            f"{parameters.get('level')!r} is outside that range. Nothing was sent to the car.",
        )
    return {"heater": seat, "level": level}


def _wheel(parameters: dict[str, Any]) -> dict[str, Any]:
    turned_on = parameters.get("on")
    if not isinstance(turned_on, bool):
        raise CommandRefusal(
            "invalid_input",
            "Steering wheel heat needs on set to true or false. Nothing was sent to the car.",
        )
    return {"on": turned_on}


def _charge_limit(parameters: dict[str, Any]) -> dict[str, Any]:
    percent = _whole_number(parameters.get("percent"))
    if percent is None or percent < MIN_CHARGE_PERCENT or percent > MAX_CHARGE_PERCENT:
        raise CommandRefusal(
            "invalid_input",
            "Charge limit must be a whole number from "
            f"{MIN_CHARGE_PERCENT} to {MAX_CHARGE_PERCENT} percent. "
            f"{parameters.get('percent')!r} is outside Tesla's allowed range. "
            "Nothing was sent to the car.",
        )
    return {"percent": percent}


def _spec(
    name: str,
    endpoint: str,
    summary: str,
    build: BodyBuilder,
    *,
    idempotent: bool = True,
    tool_name: str | None = None,
) -> CommandSpec:
    return CommandSpec(
        name=name,
        tool_name=tool_name or f"tesla_{name}",
        endpoint=endpoint,
        summary=summary,
        idempotent=idempotent,
        build=build,
    )


COMMANDS: dict[str, CommandSpec] = {
    spec.name: spec
    for spec in (
        _spec(
            "climate_on",
            "auto_conditioning_start",
            "turn the cabin climate on",
            _empty,
        ),
        _spec(
            "climate_off",
            "auto_conditioning_stop",
            "turn the cabin climate off",
            _empty,
        ),
        _spec(
            "set_cabin_temperature",
            "set_temps",
            "set the cabin temperature in Celsius",
            _temps,
            tool_name="tesla_set_cabin_temperature",
        ),
        _spec(
            "seat_heater",
            "remote_seat_heater_request",
            "set a seat heater level",
            _seat,
            tool_name="tesla_set_seat_heater",
        ),
        _spec(
            "steering_wheel_heat",
            "remote_steering_wheel_heater_request",
            "turn the steering wheel heater on or off",
            _wheel,
            tool_name="tesla_set_steering_wheel_heat",
        ),
        _spec(
            "set_charge_limit",
            "set_charge_limit",
            "set the battery charge limit",
            _charge_limit,
            tool_name="tesla_set_charge_limit",
        ),
        _spec("charge_start", "charge_start", "start charging", _empty),
        _spec("charge_stop", "charge_stop", "stop charging", _empty),
        _spec(
            "flash_lights",
            "flash_lights",
            "flash the headlights",
            _empty,
            idempotent=False,
        ),
        _spec(
            "honk_horn",
            "honk_horn",
            "honk the horn",
            _empty,
            idempotent=False,
        ),
        _spec("door_lock", "door_lock", "lock the doors", _empty),
        _spec(
            "sentry_on",
            "set_sentry_mode",
            "turn sentry mode on",
            _sentry_on,
            tool_name="tesla_sentry_mode_on",
        ),
    )
}


def allowlist_aliases() -> dict[str, str]:
    """Map allowlist tokens (friendly name, tool name, or Tesla endpoint) to a command."""
    mapping: dict[str, str] = {}
    for spec in COMMANDS.values():
        mapping[spec.name] = spec.name
        mapping[spec.endpoint] = spec.name
        mapping[spec.tool_name] = spec.name
    return mapping


ALIASES: dict[str, str] = allowlist_aliases()


def command_allowed(allowlist: tuple[str, ...], command: str) -> bool:
    allowed = {ALIASES.get(item, item) for item in allowlist}
    return command in allowed and command in COMMANDS


def unknown_allowlist_entries(allowlist: tuple[str, ...]) -> list[str]:
    return [item for item in allowlist if item not in ALIASES]


def build_body(command: str, parameters: dict[str, Any]) -> dict[str, Any]:
    spec = COMMANDS.get(command)
    if spec is None:
        raise CommandRefusal(
            "unknown_command",
            f"{command} is not a command this program can send. Nothing was sent to the car.",
        )
    return spec.build(parameters)


def would_send(endpoint: str, body: dict[str, Any], vin: str | None) -> dict[str, Any]:
    """The HTTP request a live send would make. The VIN in the path is masked."""
    from tesla_mcp.vin import mask_path

    if vin:
        path = mask_path(f"/api/1/vehicles/{vin}/command/{endpoint}")
    else:
        path = f"/api/1/vehicles/{{vin}}/command/{endpoint}"
    return {"method": "POST", "path": path, "body": body}
