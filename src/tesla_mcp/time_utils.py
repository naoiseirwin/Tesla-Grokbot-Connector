"""UTC timestamps and local calendar windows."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    return datetime.now(UTC)


def isoformat(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception as exc:
        raise ValueError(f"Unknown timezone {name!r}.") from exc


def as_local(moment: datetime, tzname: str) -> datetime:
    return moment.astimezone(zone(tzname))


def day_bounds(day_text: str, tzname: str) -> tuple[datetime, datetime]:
    """Inclusive calendar date, interpreted in `tzname`, as a UTC half-open range."""
    day = date.fromisoformat(day_text)
    start = datetime(day.year, day.month, day.day, tzinfo=zone(tzname))
    end = start + timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)


def local_day_start(moment: datetime, tzname: str) -> datetime:
    local = as_local(moment, tzname)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC)


def period_window(period: str, now: datetime, tzname: str) -> tuple[datetime, datetime]:
    """Return a UTC [start, end) window for 7d, 30d, or this_week (Monday start)."""
    key = period.strip().lower()
    local_now = as_local(now, tzname)
    end_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    if key == "7d":
        start_local = end_local - timedelta(days=7)
    elif key == "30d":
        start_local = end_local - timedelta(days=30)
    elif key == "this_week":
        monday = end_local - timedelta(days=1)
        start_local = (monday - timedelta(days=monday.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end_local = start_local + timedelta(days=7)
    else:
        raise ValueError("period must be 7d, 30d, or this_week.")
    return start_local.astimezone(UTC), end_local.astimezone(UTC)
