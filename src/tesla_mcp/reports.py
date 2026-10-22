"""Report maths over local snapshots. These functions do not call Tesla."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from .history import Snapshot
from .time_utils import as_local, isoformat

LOW_CHARGE_PERCENT = 30.0
LOW_CHARGE_MAX_AGE = timedelta(hours=6)


def normalized_range(battery_range_mi: float, soc_percent: float) -> float:
    """Rated range scaled to a full charge: range / (SoC / 100)."""
    return battery_range_mi * 100.0 / soc_percent


def weekly_mileage(
    snapshots: list[Snapshot],
    *,
    start: datetime,
    end: datetime,
    tzname: str,
) -> dict[str, object]:
    """Spread each odometer increase evenly across the days until the next reading.

    Same-calendar-day gaps stay on that day. A gap that crosses midnight is
    split across the later days only: the day of the earlier reading keeps the
    miles already counted, and the increase is attributed from the next day
    through the day of the later reading. Days after the last reading are null.
    """
    readings = [item for item in snapshots if item.odometer_mi is not None]
    readings.sort(key=lambda item: item.recorded_at)
    start_day = as_local(start, tzname).date()
    end_day = as_local(end - timedelta(microseconds=1), tzname).date()
    shares: dict[date, float] = defaultdict(float)
    decreased = False
    for earlier, later in zip(readings, readings[1:], strict=False):
        assert earlier.odometer_mi is not None
        assert later.odometer_mi is not None
        delta = later.odometer_mi - earlier.odometer_mi
        if delta < 0:
            decreased = True
            continue
        if delta == 0:
            continue
        first_day = as_local(earlier.recorded_at, tzname).date()
        second_day = as_local(later.recorded_at, tzname).date()
        if first_day == second_day:
            touched = [first_day]
        else:
            span_days = (second_day - first_day).days
            touched = [first_day + timedelta(days=offset) for offset in range(1, span_days + 1)]
        share = delta / len(touched)
        for day in touched:
            if start_day <= day <= end_day:
                shares[day] += share
    days: list[dict[str, object]] = []
    cursor = start_day
    last_reading_day = as_local(readings[-1].recorded_at, tzname).date() if readings else None
    first_reading_day = as_local(readings[0].recorded_at, tzname).date() if readings else None
    while cursor <= end_day:
        if not readings or last_reading_day is None or first_reading_day is None:
            miles: float | None = None
        elif cursor > last_reading_day or cursor < first_reading_day:
            miles = None
        else:
            miles = round(shares.get(cursor, 0.0), 3)
        days.append({"date": cursor.isoformat(), "miles": miles})
        cursor += timedelta(days=1)
    known = [row["miles"] for row in days if isinstance(row["miles"], (int, float))]
    total = round(sum(float(item) for item in known), 3) if known else None
    note = (
        "Distance is the odometer increase between snapshots, spread across the days in each gap. "
        "Days after the last snapshot are unknown. This report does not wake the car."
    )
    if decreased:
        note += " An odometer decrease was ignored."
    if not readings:
        note = "No odometer snapshots in the local history yet. This report does not wake the car."
    return {
        "start": start_day.isoformat(),
        "end": end_day.isoformat(),
        "total_miles": total,
        "days": days,
        "note": note,
    }


def range_trend(snapshots: list[Snapshot], *, now: datetime) -> dict[str, object]:
    points: list[tuple[datetime, float]] = []
    for item in snapshots:
        if (
            item.battery_level is not None
            and item.battery_level > 0
            and item.battery_range_mi is not None
        ):
            points.append(
                (item.recorded_at, normalized_range(item.battery_range_mi, item.battery_level))
            )

    def _mean(days: int) -> dict[str, object]:
        cutoff = now - timedelta(days=days)
        values = [value for recorded, value in points if recorded >= cutoff]
        average = round(sum(values) / len(values), 2) if values else None
        return {"days": days, "samples": len(values), "normalized_range_miles": average}

    recent = _mean(30)
    quarter = _mean(90)
    return {
        "normalized_range": "battery_range_miles * 100 / state_of_charge",
        "window_30_day": recent,
        "window_90_day": quarter,
        "note": (
            "Normalized range is rated range scaled to 100% SoC. "
            "A drop can be degradation or temperature. This report does not wake the car."
        ),
    }


def low_charge(snapshot: Snapshot | None, *, now: datetime) -> dict[str, object]:
    if snapshot is None or snapshot.battery_level is None:
        return {
            "alert": False,
            "stale": True,
            "battery_level_percent": None,
            "plugged_in": None,
            "as_of": isoformat(snapshot.recorded_at) if snapshot else None,
            "message": "No battery snapshot is stored. The car was not woken.",
        }
    age = now - snapshot.recorded_at.astimezone(UTC)
    stale = age > LOW_CHARGE_MAX_AGE
    unplugged = snapshot.plugged_in is False
    alert = snapshot.battery_level < LOW_CHARGE_PERCENT and unplugged
    if alert:
        message = f"Battery is {snapshot.battery_level:g}% and the car is not plugged in."
    elif snapshot.battery_level < LOW_CHARGE_PERCENT and snapshot.plugged_in:
        message = f"Battery is {snapshot.battery_level:g}% and the car is plugged in."
    else:
        message = f"Battery is {snapshot.battery_level:g}%."
    if stale:
        hours = round(age.total_seconds() / 3600, 1)
        message += f" Snapshot is {hours} hours old (older than 6 hours)."
    message += " The car was not woken."
    return {
        "alert": alert,
        "stale": stale,
        "battery_level_percent": snapshot.battery_level,
        "plugged_in": snapshot.plugged_in,
        "as_of": isoformat(snapshot.recorded_at),
        "message": message,
    }


def charging_totals(sessions: list[dict[str, object]]) -> dict[str, object]:
    kwh = 0.0
    cost = 0.0
    currencies: set[str] = set()
    for session in sessions:
        session_kwh = session.get("kwh")
        session_cost = session.get("cost")
        if isinstance(session_kwh, (int, float)):
            kwh += float(session_kwh)
        if isinstance(session_cost, (int, float)):
            cost += float(session_cost)
        currency = session.get("currency")
        if isinstance(currency, str) and currency:
            currencies.add(currency)
    currency_label: str | None
    if len(currencies) == 1:
        currency_label = next(iter(currencies))
    elif not currencies:
        currency_label = None
    else:
        currency_label = "mixed"
    return {
        "sessions": len(sessions),
        "kwh": round(kwh, 3),
        "cost": round(cost, 2),
        "currency": currency_label,
    }


def percent_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


METRIC_FIELDS = {
    "battery_level": "battery_level",
    "battery_range": "battery_range_mi",
    "odometer": "odometer_mi",
    "inside_temp": "inside_temp_c",
    "outside_temp": "outside_temp_c",
}


def query_series(
    snapshots: list[Snapshot],
    *,
    metric: str,
    bucket: str,
    tzname: str,
) -> list[dict[str, object]]:
    field = METRIC_FIELDS.get(metric)
    if field is None:
        known = ", ".join(sorted(METRIC_FIELDS))
        raise ValueError(f"metric must be one of {known}.")
    if bucket not in {"hour", "day", "week"}:
        raise ValueError("bucket must be hour, day, or week.")
    grouped: dict[datetime, list[float]] = defaultdict(list)
    for item in snapshots:
        raw = getattr(item, field)
        if raw is None:
            continue
        local = as_local(item.recorded_at, tzname)
        if bucket == "hour":
            key = local.replace(minute=0, second=0, microsecond=0)
        elif bucket == "day":
            key = local.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            monday = local.date() - timedelta(days=local.weekday())
            key = datetime(monday.year, monday.month, monday.day, tzinfo=local.tzinfo)
        grouped[key].append(float(raw))
    rows: list[dict[str, object]] = []
    previous: float | None = None
    for key in sorted(grouped):
        values = grouped[key]
        if metric == "odometer":
            current = values[-1]
            delta = None if previous is None else round(current - previous, 3)
            previous = current
            rows.append(
                {
                    "bucket": key.isoformat(),
                    "odometer_miles": current,
                    "delta_miles": delta,
                }
            )
        else:
            rows.append(
                {
                    "bucket": key.isoformat(),
                    "value": round(sum(values) / len(values), 3),
                    "samples": len(values),
                }
            )
    return rows
