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


# Tesla rejects GET /api/1/dx/charging/history when the range is over one year.
# Each window is 365 days so a leap day cannot push one request over that limit.
CHARGING_HISTORY_WINDOW = timedelta(days=365)
# Ten calendar years, including leap days and a date-only end at 23:59:59 UTC.
CHARGING_HISTORY_MAX_SPAN = timedelta(days=365 * 10 + 4)
_MAX_CHARGING_WINDOWS = 12

_BOUND_ERROR = "Use a date (YYYY-MM-DD) or an ISO 8601 time, for example 2025-10-23T00:00:00Z."
_ORDER_ERROR = "start must be on or before end."
_SPAN_ERROR = "Charging history covers at most 10 years in one request. Narrow start and end."


def parse_charging_bound(value: str, *, is_end: bool) -> datetime:
    """Parse a date or an ISO 8601 time into UTC.

    A date-only start is 00:00:00Z that day. A date-only end is 23:59:59Z.
    A full timestamp keeps its clock time and is converted to UTC.
    """
    text = value.strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            day = date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(_BOUND_ERROR) from exc
        hour, minute, second = (23, 59, 59) if is_end else (0, 0, 0)
        return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=UTC)
    try:
        parsed = parse_iso(text)
    except ValueError as exc:
        raise ValueError(_BOUND_ERROR) from exc
    return parsed.replace(microsecond=0)


def charging_history_bounds(
    start: str | None,
    end: str | None,
    *,
    now: datetime,
) -> tuple[datetime, datetime]:
    """Resolve a charging-history range.

    End defaults to `now`. Start defaults to 365 days before end.
    A blank string is an error, so an empty value is not treated as "now".
    """
    end_text = _present_bound(end)
    start_text = _present_bound(start)
    if end_text is None:
        end_at = _as_utc_seconds(now)
    else:
        end_at = parse_charging_bound(end_text, is_end=True)
    if start_text is None:
        start_at = end_at - CHARGING_HISTORY_WINDOW
    else:
        start_at = parse_charging_bound(start_text, is_end=False)
    if start_at > end_at:
        raise ValueError(_ORDER_ERROR)
    if end_at - start_at > CHARGING_HISTORY_MAX_SPAN:
        raise ValueError(_SPAN_ERROR)
    return start_at, end_at


def charging_history_windows(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Split [start, end] into consecutive windows of at most 365 days.

    Windows meet at the boundary instant and do not otherwise overlap.
    A session that lands on that instant can appear in two replies; callers
    drop duplicates.
    """
    if start > end:
        raise ValueError(_ORDER_ERROR)
    if end - start > CHARGING_HISTORY_MAX_SPAN:
        raise ValueError(_SPAN_ERROR)
    windows: list[tuple[datetime, datetime]] = []
    cursor = start
    while True:
        if len(windows) >= _MAX_CHARGING_WINDOWS:
            raise ValueError(_SPAN_ERROR)
        window_end = min(cursor + CHARGING_HISTORY_WINDOW, end)
        windows.append((cursor, window_end))
        if window_end >= end:
            return windows
        cursor = window_end


def _present_bound(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        raise ValueError(_BOUND_ERROR)
    return text


def _as_utc_seconds(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).replace(microsecond=0)


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
