"""Command tools. Each description says the call acts on the car.

The host shows that description on the approval prompt. The tool still
refuses the call unless commands are enabled, the command is allowlisted,
and dry-run is off.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context

from .execute import perform

_VIN = "Vehicle name or masked VIN. Omit this when the account has one car."


async def tesla_climate_on(
    vin: Annotated[str | None, _VIN] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """This acts on the car: turn the cabin climate on.

    Nothing is sent unless vehicle commands are enabled, climate_on is on
    TESLA_COMMANDS_ALLOWLIST, and dry-run is off.
    """
    return await perform(ctx, "climate_on", {}, vin_selector=vin)


async def tesla_climate_off(
    vin: Annotated[str | None, _VIN] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """This acts on the car: turn the cabin climate off.

    Nothing is sent unless vehicle commands are enabled, climate_off is on
    TESLA_COMMANDS_ALLOWLIST, and dry-run is off.
    """
    return await perform(ctx, "climate_off", {}, vin_selector=vin)


async def tesla_set_cabin_temperature(
    driver_celsius: Annotated[
        Any,
        "Driver-side cabin temperature in Celsius. Allowed range is 15 to 28, "
        "checked before rounding. Numeric strings and booleans are refused.",
    ],
    passenger_celsius: Annotated[
        Any,
        "Passenger-side temperature in Celsius. Omit to use the driver temperature.",
    ] = None,
    vin: Annotated[str | None, _VIN] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """This acts on the car: set the cabin temperature in Celsius.

    Temperatures outside 15 to 28 °C are refused. Nothing is sent unless
    vehicle commands are enabled, set_cabin_temperature is on
    TESLA_COMMANDS_ALLOWLIST, and dry-run is off.
    """
    return await perform(
        ctx,
        "set_cabin_temperature",
        {"driver_celsius": driver_celsius, "passenger_celsius": passenger_celsius},
        vin_selector=vin,
    )


async def tesla_set_seat_heater(
    seat: Annotated[
        Any,
        "Seat id 0-5 as a JSON integer: 0 front left, 1 front right, 2 rear left, "
        "3 rear center, 4 rear right, 5 third row. Booleans, decimals, and numeric "
        "strings are refused.",
    ],
    level: Annotated[
        Any,
        "Heater level 0 (off) through 3, as a JSON integer. Booleans, decimals, "
        "and numeric strings are refused.",
    ],
    vin: Annotated[str | None, _VIN] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """This acts on the car: set a seat heater.

    Seat ids outside 0-5 and levels outside 0-3 are refused. Nothing is sent
    unless vehicle commands are enabled, seat_heater is on
    TESLA_COMMANDS_ALLOWLIST, and dry-run is off.
    """
    return await perform(
        ctx,
        "seat_heater",
        {"seat": seat, "level": level},
        vin_selector=vin,
    )


async def tesla_set_steering_wheel_heat(
    on: Annotated[
        Any,
        "True turns the steering wheel heater on. False turns it off. "
        "Numbers and strings are refused.",
    ],
    vin: Annotated[str | None, _VIN] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """This acts on the car: turn the steering wheel heater on or off.

    Nothing is sent unless vehicle commands are enabled, steering_wheel_heat
    is on TESLA_COMMANDS_ALLOWLIST, and dry-run is off.
    """
    return await perform(ctx, "steering_wheel_heat", {"on": on}, vin_selector=vin)


async def tesla_set_charge_limit(
    percent: Annotated[
        Any,
        "Charge limit from 50 to 100 as a JSON integer. Booleans, decimals, "
        "and numeric strings are refused.",
    ],
    vin: Annotated[str | None, _VIN] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """This acts on the car: set the battery charge limit.

    Values outside 50 to 100 percent are refused. Nothing is sent unless
    vehicle commands are enabled, set_charge_limit is on
    TESLA_COMMANDS_ALLOWLIST, and dry-run is off.
    """
    return await perform(ctx, "set_charge_limit", {"percent": percent}, vin_selector=vin)


async def tesla_charge_start(
    vin: Annotated[str | None, _VIN] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """This acts on the car: start charging.

    Nothing is sent unless vehicle commands are enabled, charge_start is on
    TESLA_COMMANDS_ALLOWLIST, and dry-run is off.
    """
    return await perform(ctx, "charge_start", {}, vin_selector=vin)


async def tesla_charge_stop(
    vin: Annotated[str | None, _VIN] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """This acts on the car: stop charging.

    Nothing is sent unless vehicle commands are enabled, charge_stop is on
    TESLA_COMMANDS_ALLOWLIST, and dry-run is off.
    """
    return await perform(ctx, "charge_stop", {}, vin_selector=vin)


async def tesla_flash_lights(
    vin: Annotated[str | None, _VIN] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """This acts on the car: flash the headlights.

    Nothing is sent unless vehicle commands are enabled, flash_lights is on
    TESLA_COMMANDS_ALLOWLIST, and dry-run is off.
    """
    return await perform(ctx, "flash_lights", {}, vin_selector=vin)


async def tesla_honk_horn(
    vin: Annotated[str | None, _VIN] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """This acts on the car: honk the horn.

    Nothing is sent unless vehicle commands are enabled, honk_horn is on
    TESLA_COMMANDS_ALLOWLIST, and dry-run is off.
    """
    return await perform(ctx, "honk_horn", {}, vin_selector=vin)


async def tesla_door_lock(
    vin: Annotated[str | None, _VIN] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """This acts on the car: lock the doors.

    This does not unlock the car. Nothing is sent unless vehicle commands are
    enabled, door_lock is on TESLA_COMMANDS_ALLOWLIST, and dry-run is off.
    """
    return await perform(ctx, "door_lock", {}, vin_selector=vin)


async def tesla_sentry_mode_on(
    vin: Annotated[str | None, _VIN] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """This acts on the car: turn sentry mode on.

    This cannot turn sentry mode off. Nothing is sent unless vehicle commands
    are enabled, sentry_on is on TESLA_COMMANDS_ALLOWLIST, and dry-run is off.
    """
    return await perform(ctx, "sentry_on", {}, vin_selector=vin)


TOOL_FUNCTIONS = (
    tesla_climate_on,
    tesla_climate_off,
    tesla_set_cabin_temperature,
    tesla_set_seat_heater,
    tesla_set_steering_wheel_heat,
    tesla_set_charge_limit,
    tesla_charge_start,
    tesla_charge_stop,
    tesla_flash_lights,
    tesla_honk_horn,
    tesla_door_lock,
    tesla_sentry_mode_on,
)
