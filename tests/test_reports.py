"""Report maths over local snapshots."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tesla_mcp.history import Snapshot
from tesla_mcp.reports import (
    low_charge,
    normalized_range,
    query_series,
    range_trend,
    weekly_mileage,
)
from tesla_mcp.snapshot import coarse_coordinate, snapshot_from_vehicle_data
from tests.fixtures.vehicles import vehicle_data

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def _snap(**kwargs: object) -> Snapshot:
    base = Snapshot(
        vin="5YJ3E1EA7KF000001",
        recorded_at=NOW,
        state="online",
        live=True,
        source="test",
    )
    for key, value in kwargs.items():
        setattr(base, key, value)
    return base


def test_weekly_mileage_interpolates_gaps():
    readings = [
        _snap(recorded_at=datetime(2026, 10, 5, 12, tzinfo=UTC), odometer_mi=1000),
        _snap(recorded_at=datetime(2026, 10, 7, 12, tzinfo=UTC), odometer_mi=1100),
        _snap(recorded_at=datetime(2026, 10, 8, 12, tzinfo=UTC), odometer_mi=1110),
    ]
    report = weekly_mileage(
        readings,
        start=datetime(2026, 10, 5, tzinfo=UTC),
        end=datetime(2026, 10, 12, tzinfo=UTC),
        tzname="UTC",
    )
    by_day = {row["date"]: row["miles"] for row in report["days"]}  # type: ignore[index]
    assert by_day["2026-10-05"] == 0
    assert by_day["2026-10-06"] == 50
    assert by_day["2026-10-07"] == 50
    assert by_day["2026-10-08"] == 10
    assert by_day["2026-10-09"] is None
    assert report["total_miles"] == 110


def test_range_trend_normalizes_to_full_charge():
    assert normalized_range(200, 80) == 250
    snapshots = [
        _snap(
            recorded_at=NOW - timedelta(days=2),
            battery_level=80,
            battery_range_mi=200,
        ),
        _snap(
            recorded_at=NOW - timedelta(days=12),
            battery_level=50,
            battery_range_mi=100,
        ),
        _snap(
            recorded_at=NOW - timedelta(days=40),
            battery_level=100,
            battery_range_mi=300,
        ),
        _snap(recorded_at=NOW - timedelta(days=1), battery_level=0, battery_range_mi=10),
    ]
    report = range_trend(snapshots, now=NOW)
    assert report["window_30_day"]["normalized_range_miles"] == 225  # type: ignore[index]
    assert report["window_30_day"]["samples"] == 2  # type: ignore[index]
    assert report["window_90_day"]["normalized_range_miles"] == 250  # type: ignore[index]
    assert report["window_90_day"]["samples"] == 3  # type: ignore[index]


def test_low_charge_alerts_only_when_unplugged_and_flags_stale_snapshots():
    fresh = low_charge(
        _snap(battery_level=25, plugged_in=False, recorded_at=NOW - timedelta(hours=1)),
        now=NOW,
    )
    plugged = low_charge(
        _snap(battery_level=25, plugged_in=True, recorded_at=NOW - timedelta(hours=1)),
        now=NOW,
    )
    stale = low_charge(
        _snap(battery_level=25, plugged_in=False, recorded_at=NOW - timedelta(hours=7)),
        now=NOW,
    )
    assert fresh["alert"] is True
    assert fresh["stale"] is False
    assert plugged["alert"] is False
    assert stale["alert"] is True
    assert stale["stale"] is True
    assert "6 hours" in str(stale["message"])
    assert "not woken" in str(stale["message"])


def test_query_series_averages_a_day():
    snapshots = [
        _snap(recorded_at=datetime(2026, 10, 1, 8, tzinfo=UTC), battery_level=40),
        _snap(recorded_at=datetime(2026, 10, 1, 20, tzinfo=UTC), battery_level=60),
        _snap(recorded_at=datetime(2026, 10, 2, 8, tzinfo=UTC), battery_level=80),
    ]
    rows = query_series(snapshots, metric="battery_level", bucket="day", tzname="UTC")
    assert rows[0]["value"] == 50
    assert rows[1]["value"] == 80


def test_snapshot_stores_coarse_location_only_when_enabled():
    payload = vehicle_data(latitude=53.3498, longitude=-6.2603)
    hidden = snapshot_from_vehicle_data(
        "5YJ3E1EA7KF000001", payload, source="test", location_enabled=False
    )
    shown = snapshot_from_vehicle_data(
        "5YJ3E1EA7KF000001", payload, source="test", location_enabled=True
    )
    assert hidden.latitude is None
    assert shown.latitude == coarse_coordinate(53.3498) == 53.35
    assert shown.longitude == coarse_coordinate(-6.2603) == -6.26
    assert shown.battery_level == 72
    assert shown.locked is True
    assert shown.software_version == "2026.20.3"
    assert shown.plugged_in is False
