"""Local history and reports. Charging summary is the only report that can call Tesla."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastmcp import Context

from ..errors import TeslaError
from ..reports import (
    charging_totals,
    low_charge,
    percent_change,
    query_series,
    range_trend,
    weekly_mileage,
)
from ..scopes import CHARGING_HISTORY_SCOPE
from ..time_utils import as_local, day_bounds, isoformat, period_window, utc_now
from ..vehicles import Vehicle
from ..vin import mask_vin
from .common import finish, require_app, select_vehicle

_CHARGING_OFF = (
    "Charging history is off. It requires the vehicle_charging_cmds scope, which also "
    "lets an app start and stop charging at Tesla's API. This server does not expose "
    "charge control. Set TESLA_ENABLE_CHARGING_HISTORY=true and run `tesla-mcp auth` "
    "again if you want this report."
)


async def tesla_query_history(
    metric: Annotated[
        str,
        "battery_level, battery_range, odometer, inside_temp, or outside_temp.",
    ],
    start: Annotated[str, "Start date (YYYY-MM-DD), inclusive, in TESLA_TIMEZONE."],
    end: Annotated[str, "End date (YYYY-MM-DD), inclusive, in TESLA_TIMEZONE."],
    bucket: Annotated[str, "hour, day, or week."] = "day",
    vin: Annotated[str | None, "Vehicle name or masked VIN. Omit when there is one car."] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Time series from the local snapshot database. Does not call Tesla or wake the car."""
    try:
        fleet, runtime = await require_app(ctx)
        vehicle = await _vehicle_for_history(fleet, runtime, vin)
        start_at, _start_end = day_bounds(start, runtime.settings.timezone)
        _end_start, end_at = day_bounds(end, runtime.settings.timezone)
        if end_at <= start_at:
            raise TeslaError("end must be on or after start.")
        rows = runtime.history.between(vehicle.vin, start_at, end_at)
        series = query_series(rows, metric=metric, bucket=bucket, tzname=runtime.settings.timezone)
        finish("tesla_query_history", vehicle.vin, "ok", None)
        return {
            "ok": True,
            "vin_masked": mask_vin(vehicle.vin),
            "metric": metric,
            "bucket": bucket,
            "series": series,
        }
    except (TeslaError, ValueError) as exc:
        message = exc.message if isinstance(exc, TeslaError) else str(exc)
        finish("tesla_query_history", vin, "error", None)
        return {"ok": False, "error": message, "error_type": type(exc).__name__}


async def tesla_report(
    type: Annotated[
        str,
        "weekly_mileage, range_trend, charging_summary, or low_charge.",
    ],
    vin: Annotated[str | None, "Vehicle name or masked VIN. Omit when there is one car."] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """A ready-to-read summary. Local reports do not wake the car.

    weekly_mileage uses the last 7 days of odometer snapshots.
    range_trend compares 30-day and 90-day normalized rated range.
    low_charge alerts below 30% when unplugged, and says so if the snapshot is older than 6 hours.
    charging_summary calls Tesla only when charging history is opted in.
    """
    try:
        fleet, runtime = await require_app(ctx)
        vehicle = await _vehicle_for_history(fleet, runtime, vin)
        kind = type.strip().lower()
        now = utc_now()
        if kind == "weekly_mileage":
            start, end = period_window("7d", now, runtime.settings.timezone)
            report = weekly_mileage(
                runtime.history.odometer_readings(vehicle.vin),
                start=start,
                end=end,
                tzname=runtime.settings.timezone,
            )
        elif kind == "range_trend":
            start = now - timedelta(days=90)
            report = range_trend(
                runtime.history.between(vehicle.vin, start, now + timedelta(days=1)),
                now=now,
            )
        elif kind == "low_charge":
            report = low_charge(runtime.history.latest(vehicle.vin), now=now)
        elif kind == "charging_summary":
            report = await _charging_summary(fleet, runtime, vehicle.vin, now)
        else:
            raise TeslaError(
                "type must be weekly_mileage, range_trend, charging_summary, or low_charge."
            )
        finish("tesla_report", vehicle.vin, "ok", "data" if kind == "charging_summary" else None)
        return {
            "ok": True,
            "type": kind,
            "vin_masked": mask_vin(vehicle.vin),
            "report": report,
        }
    except (TeslaError, ValueError) as exc:
        message = exc.message if isinstance(exc, TeslaError) else str(exc)
        finish("tesla_report", vin, "error", None)
        return {"ok": False, "error": message, "error_type": exc.__class__.__name__}


async def _vehicle_for_history(fleet: Any, runtime: Any, selector: str | None) -> Vehicle:
    """Use the local snapshot VIN when it is unambiguous, so reports stay off the network."""
    known = _match_known_vin(runtime.history.distinct_vins(), selector)
    if known is not None:
        return Vehicle(vin=known, name="", state="unknown", model=None)
    vehicle, _all = await select_vehicle(fleet, selector)
    return vehicle


def _match_known_vin(vins: list[str], selector: str | None) -> str | None:
    if not vins:
        return None
    if selector is None or not selector.strip():
        return vins[0] if len(vins) == 1 else None
    wanted = selector.strip().lower().replace("...", "…")
    matches = [
        vin
        for vin in vins
        if vin.lower() == wanted or mask_vin(vin).lower() == wanted or vin.lower().endswith(wanted)
    ]
    if len(matches) == 1:
        return matches[0]
    return None


async def _charging_summary(fleet: Any, runtime: Any, vin: str, now: datetime) -> dict[str, Any]:
    if not runtime.settings.enable_charging_history:
        raise TeslaError(_CHARGING_OFF)
    granted = runtime.granted_scopes or []
    if runtime.scopes_known and CHARGING_HISTORY_SCOPE not in granted:
        raise TeslaError(_CHARGING_OFF)
    current_start, current_end, previous_start, previous_end = _month_pair(
        now, runtime.settings.timezone
    )
    current_sessions = await fleet.charging_history(
        vin, isoformat(current_start), isoformat(current_end)
    )
    previous_sessions = await fleet.charging_history(
        vin, isoformat(previous_start), isoformat(previous_end)
    )
    current = charging_totals(current_sessions)
    previous = charging_totals(previous_sessions)
    return {
        "month": as_local(current_start, runtime.settings.timezone).strftime("%Y-%m"),
        "previous_month": as_local(previous_start, runtime.settings.timezone).strftime("%Y-%m"),
        "current": current,
        "previous": previous,
        "kwh_change_percent": percent_change(_number(current["kwh"]), _number(previous["kwh"])),
        "cost_change_percent": percent_change(_number(current["cost"]), _number(previous["cost"])),
        "note": (
            "Sessions come from the charging history API. "
            "Home charging coverage depends on that API. The car was not woken."
        ),
    }


def _month_pair(now: datetime, tzname: str) -> tuple[datetime, datetime, datetime, datetime]:
    local = as_local(now, tzname)
    first_this = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    current_end = first_this
    current_start = (first_this - timedelta(days=1)).replace(day=1)
    previous_end = current_start
    previous_start = (current_start - timedelta(days=1)).replace(day=1)
    return (
        current_start.astimezone(UTC),
        current_end.astimezone(UTC),
        previous_start.astimezone(UTC),
        previous_end.astimezone(UTC),
    )


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)
