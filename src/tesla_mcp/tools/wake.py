"""The only tool that can wake a car. It is budget-gated and requires confirm=true."""

from __future__ import annotations

from datetime import UTC
from typing import Annotated, Any

from fastmcp import Context

from ..errors import TeslaError
from ..pricing import PRICE_USD, money
from ..runtime import AppRuntime
from ..snapshot import data_endpoints, snapshot_from_vehicle_data
from ..time_utils import as_local, utc_now
from .common import failure, finish, require_app, select_vehicle, status_dict


async def tesla_wake_and_refresh(
    confirm: Annotated[bool, "Must be true. A wake is billed and brings the car online."] = False,
    vin: Annotated[
        str | None, "Vehicle name or masked VIN. Omit this when there is one car."
    ] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Wake the car, then store a fresh snapshot. Refuses unless confirm is true.

    Default budget: at most one wake every 30 minutes and 3 wakes per day.
    A hard cap of 3 wakes per minute cannot be raised. This is the only wake path.
    """
    if confirm is not True:
        finish("tesla_wake_and_refresh", vin, "denied_confirm", None)
        return {
            "ok": False,
            "error_type": "confirmation_required",
            "error": "Pass confirm=true to wake the car. Wakes are billed at about $0.02 each.",
        }
    try:
        fleet, runtime = await require_app(ctx)
        vehicle, _all = await select_vehicle(fleet, vin)
        decision = runtime.wakes.reserve(vehicle.vin)
        if not decision.allowed:
            finish("tesla_wake_and_refresh", vehicle.vin, f"denied_{decision.reason}", None)
            summary = runtime.wakes.summary()
            return {
                "ok": False,
                "error_type": "wake_budget",
                "error": _budget_message(
                    decision.reason,
                    runtime.settings.wake_min_interval_seconds,
                    runtime.settings.wake_daily_cap,
                    runtime.settings.timezone,
                    runtime.wakes.per_minute_cap,
                ),
                "wakes_used_today": summary["used_today"],
                "wakes_remaining_today": summary["remaining_today"],
                "daily_cap": summary["daily_cap"],
            }
        try:
            body = await fleet.wake_up(vehicle.vin)
        except TeslaError as exc:
            _release_unbilled(runtime, decision.event_id, exc)
            finish("tesla_wake_and_refresh", vehicle.vin, "error", "wake")
            return failure(exc)
        accounted = decision
        response = body.get("response") if isinstance(body.get("response"), dict) else {}
        state = response.get("state") if isinstance(response, dict) else None
        if state != "online":
            meta = await fleet.get_vehicle(vehicle.vin)
            state = meta.state
        if state != "online":
            finish("tesla_wake_and_refresh", vehicle.vin, "pending", "wake")
            return {
                "ok": False,
                "error_type": "wake_pending",
                "error": "The wake was sent, but the car is not online yet. It was not woken again.",
                "wakes_used_today": accounted.used_today,
                "wakes_remaining_today": accounted.remaining_today,
                "daily_cap": runtime.settings.wake_daily_cap,
            }
        payload = await fleet.vehicle_data(vehicle.vin, data_endpoints(include_location=False))
        snapshot = snapshot_from_vehicle_data(
            vehicle.vin,
            payload,
            source="wake",
            location_enabled=False,
        )
        snapshot.state = "online"
        runtime.history.add_snapshot(snapshot)
        finish("tesla_wake_and_refresh", vehicle.vin, "ok", "wake")
        status = status_dict(snapshot, name=vehicle.name, freshness="live", observed_state="online")
        status["wakes_used_today"] = accounted.used_today
        status["wakes_remaining_today"] = accounted.remaining_today
        status["daily_cap"] = runtime.settings.wake_daily_cap
        return status
    except TeslaError as exc:
        finish("tesla_wake_and_refresh", vin, "error", "wake")
        return failure(exc)


async def tesla_usage(ctx: Context | None = None) -> dict[str, Any]:
    """Calls recorded this month, estimated cost, and the wake budget. Local only."""
    try:
        _fleet, runtime = await require_app(ctx)
        now = utc_now()
        month_local = as_local(now, runtime.settings.timezone).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        month_start = month_local.astimezone(UTC)
        rows = runtime.history.usage_since(month_start)
        counts = {"data": 0, "wake": 0, "command": 0}
        estimated = 0.0
        for row in rows:
            category = str(row["category"])
            counts[category] = counts.get(category, 0) + 1
            estimated += float(row["estimated_usd"])
        summary = runtime.wakes.summary(now=now)
        finish("tesla_usage", None, "ok", None)
        return {
            "ok": True,
            "month": month_local.strftime("%Y-%m"),
            "calls": counts,
            "estimated_usd": money(estimated),
            "unit_prices_usd": {
                "data": money(PRICE_USD["data"]),
                "wake": money(PRICE_USD["wake"]),
                "command": money(PRICE_USD["command"]),
            },
            "wake_budget": summary,
            "note": (
                "Estimates treat vehicle reads and charging history as data calls. "
                "Authentication calls are not counted. HTTP 500 and above are not billed. "
                "Set a billing limit in the Tesla developer dashboard."
            ),
        }
    except TeslaError as exc:
        finish("tesla_usage", None, "error", None)
        return failure(exc)


def _release_unbilled(runtime: AppRuntime, event_id: int | None, exc: TeslaError) -> None:
    """Keep the reservation when Tesla billed the wake. HTTP 500+ and network errors are free."""
    if event_id is None:
        return
    if exc.status_code is None or exc.status_code >= 500:
        runtime.wakes.release(event_id)


def _budget_message(
    reason: str, interval: int, daily_cap: int, tzname: str, per_minute: int
) -> str:
    if reason == "per_minute":
        return (
            f"Wake blocked by the hard limit of {per_minute} wakes per minute. "
            "That limit cannot be raised."
        )
    if reason == "interval":
        return f"Wake blocked because the last wake was under {interval} seconds ago."
    if reason == "daily":
        return (
            f"Wake blocked because today's cap of {daily_cap} wakes is used. "
            f"The cap resets at midnight ({tzname})."
        )
    return "Wake blocked by the local budget."
