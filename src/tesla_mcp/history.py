"""Local SQLite history. The poller and live reads append snapshots. Nothing here wakes the car."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .time_utils import isoformat, parse_iso

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    vin TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    state TEXT NOT NULL,
    live INTEGER NOT NULL,
    source TEXT NOT NULL,
    battery_level REAL,
    battery_range_mi REAL,
    est_battery_range_mi REAL,
    charging_state TEXT,
    charge_limit_soc REAL,
    charge_power_kw REAL,
    minutes_to_full REAL,
    plugged_in INTEGER,
    locked INTEGER,
    inside_temp_c REAL,
    outside_temp_c REAL,
    climate_on INTEGER,
    driver_temp_c REAL,
    passenger_temp_c REAL,
    odometer_mi REAL,
    software_version TEXT,
    software_update_status TEXT,
    software_update_version TEXT,
    latitude REAL,
    longitude REAL
);
CREATE INDEX IF NOT EXISTS snapshots_vin_time ON snapshots (vin, recorded_at);

CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY,
    recorded_at TEXT NOT NULL,
    category TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    http_status INTEGER,
    vin_masked TEXT,
    estimated_usd REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS wake_events (
    id INTEGER PRIMARY KEY,
    recorded_at TEXT NOT NULL,
    vin_masked TEXT NOT NULL,
    outcome TEXT NOT NULL
);
"""


@dataclass
class Snapshot:
    vin: str
    recorded_at: datetime
    state: str
    live: bool
    source: str
    battery_level: float | None = None
    battery_range_mi: float | None = None
    est_battery_range_mi: float | None = None
    charging_state: str | None = None
    charge_limit_soc: float | None = None
    charge_power_kw: float | None = None
    minutes_to_full: float | None = None
    plugged_in: bool | None = None
    locked: bool | None = None
    inside_temp_c: float | None = None
    outside_temp_c: float | None = None
    climate_on: bool | None = None
    driver_temp_c: float | None = None
    passenger_temp_c: float | None = None
    odometer_mi: float | None = None
    software_version: str | None = None
    software_update_status: str | None = None
    software_update_version: str | None = None
    latitude: float | None = None
    longitude: float | None = None


def _flag(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def _unflag(value: int | None) -> bool | None:
    if value is None:
        return None
    return bool(value)


class HistoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
        os.chmod(path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_snapshot(self, snapshot: Snapshot) -> None:
        columns = (
            "vin",
            "recorded_at",
            "state",
            "live",
            "source",
            "battery_level",
            "battery_range_mi",
            "est_battery_range_mi",
            "charging_state",
            "charge_limit_soc",
            "charge_power_kw",
            "minutes_to_full",
            "plugged_in",
            "locked",
            "inside_temp_c",
            "outside_temp_c",
            "climate_on",
            "driver_temp_c",
            "passenger_temp_c",
            "odometer_mi",
            "software_version",
            "software_update_status",
            "software_update_version",
            "latitude",
            "longitude",
        )
        values = (
            snapshot.vin,
            isoformat(snapshot.recorded_at),
            snapshot.state,
            1 if snapshot.live else 0,
            snapshot.source,
            snapshot.battery_level,
            snapshot.battery_range_mi,
            snapshot.est_battery_range_mi,
            snapshot.charging_state,
            snapshot.charge_limit_soc,
            snapshot.charge_power_kw,
            snapshot.minutes_to_full,
            _flag(snapshot.plugged_in),
            _flag(snapshot.locked),
            snapshot.inside_temp_c,
            snapshot.outside_temp_c,
            _flag(snapshot.climate_on),
            snapshot.driver_temp_c,
            snapshot.passenger_temp_c,
            snapshot.odometer_mi,
            snapshot.software_version,
            snapshot.software_update_status,
            snapshot.software_update_version,
            snapshot.latitude,
            snapshot.longitude,
        )
        placeholders = ", ".join("?" for _ in columns)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO snapshots ({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )

    def distinct_vins(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT DISTINCT vin FROM snapshots ORDER BY vin").fetchall()
        return [str(row["vin"]) for row in rows]

    def latest(self, vin: str) -> Snapshot | None:
        """Newest snapshot that has vehicle data, else the newest state-only row.

        Asleep polls store a newer row with no battery or location. That row must
        not hide the last real snapshot from status, reports, or the cache.
        """
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM snapshots
                WHERE vin = ? AND ({_TELEMETRY_PRESENT})
                ORDER BY recorded_at DESC, id DESC
                LIMIT 1
                """,
                (vin,),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """
                    SELECT * FROM snapshots
                    WHERE vin = ?
                    ORDER BY recorded_at DESC, id DESC
                    LIMIT 1
                    """,
                    (vin,),
                ).fetchone()
        return _row_to_snapshot(row) if row is not None else None

    def latest_location(self, vin: str) -> Snapshot | None:
        """Newest snapshot that actually stored coordinates."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM snapshots
                WHERE vin = ? AND latitude IS NOT NULL AND longitude IS NOT NULL
                ORDER BY recorded_at DESC, id DESC
                LIMIT 1
                """,
                (vin,),
            ).fetchone()
        return _row_to_snapshot(row) if row is not None else None

    def between(self, vin: str, start: datetime, end: datetime) -> list[Snapshot]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM snapshots
                WHERE vin = ? AND recorded_at >= ? AND recorded_at < ?
                ORDER BY recorded_at ASC, id ASC
                """,
                (vin, isoformat(start), isoformat(end)),
            ).fetchall()
        return [_row_to_snapshot(row) for row in rows]

    def odometer_readings(self, vin: str) -> list[Snapshot]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM snapshots
                WHERE vin = ? AND odometer_mi IS NOT NULL
                ORDER BY recorded_at ASC, id ASC
                """,
                (vin,),
            ).fetchall()
        return [_row_to_snapshot(row) for row in rows]

    def record_usage(
        self,
        *,
        category: str,
        endpoint: str,
        http_status: int | None,
        vin_masked: str,
        estimated_usd: float,
        recorded_at: datetime | None = None,
    ) -> None:
        from .time_utils import utc_now

        moment = recorded_at or utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO usage_events
                    (recorded_at, category, endpoint, http_status, vin_masked, estimated_usd)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (isoformat(moment), category, endpoint, http_status, vin_masked, estimated_usd),
            )

    def usage_since(self, start: datetime) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT category, endpoint, http_status, vin_masked, estimated_usd
                    FROM usage_events
                    WHERE recorded_at >= ?
                    """,
                    (isoformat(start),),
                )
            )

    def record_wake(self, *, vin_masked: str, outcome: str, recorded_at: datetime) -> None:
        with self._connect() as conn:
            _insert_wake(conn, vin_masked=vin_masked, outcome=outcome, recorded_at=recorded_at)

    def delete_wake(self, event_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM wake_events WHERE id = ?", (event_id,))

    def insert_wake(
        self,
        conn: sqlite3.Connection,
        *,
        vin_masked: str,
        outcome: str,
        recorded_at: datetime,
    ) -> int:
        return _insert_wake(conn, vin_masked=vin_masked, outcome=outcome, recorded_at=recorded_at)

    def read_wake_times(self, conn: sqlite3.Connection, *, outcome: str = "sent") -> list[datetime]:
        return _wake_times(conn, outcome=outcome)

    def wake_times(self, *, outcome: str = "sent") -> list[datetime]:
        with self._connect() as conn:
            return _wake_times(conn, outcome=outcome)

    @contextmanager
    def immediate(self) -> Iterator[sqlite3.Connection]:
        """Exclusive write transaction. Commits before the caller does any network I/O."""
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
        finally:
            conn.close()


_TELEMETRY_PRESENT = """
battery_level IS NOT NULL
OR battery_range_mi IS NOT NULL
OR est_battery_range_mi IS NOT NULL
OR odometer_mi IS NOT NULL
OR inside_temp_c IS NOT NULL
OR outside_temp_c IS NOT NULL
OR software_version IS NOT NULL
OR charging_state IS NOT NULL
OR latitude IS NOT NULL
"""


def _insert_wake(
    conn: sqlite3.Connection,
    *,
    vin_masked: str,
    outcome: str,
    recorded_at: datetime,
) -> int:
    cursor = conn.execute(
        "INSERT INTO wake_events (recorded_at, vin_masked, outcome) VALUES (?, ?, ?)",
        (isoformat(recorded_at), vin_masked, outcome),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("Wake row did not receive an id.")
    return int(cursor.lastrowid)


def _wake_times(conn: sqlite3.Connection, *, outcome: str) -> list[datetime]:
    rows = conn.execute(
        "SELECT recorded_at FROM wake_events WHERE outcome = ? ORDER BY recorded_at ASC",
        (outcome,),
    ).fetchall()
    return [parse_iso(str(row["recorded_at"])) for row in rows]


def _row_to_snapshot(row: sqlite3.Row) -> Snapshot:
    return Snapshot(
        vin=str(row["vin"]),
        recorded_at=parse_iso(str(row["recorded_at"])),
        state=str(row["state"]),
        live=bool(row["live"]),
        source=str(row["source"]),
        battery_level=row["battery_level"],
        battery_range_mi=row["battery_range_mi"],
        est_battery_range_mi=row["est_battery_range_mi"],
        charging_state=row["charging_state"],
        charge_limit_soc=row["charge_limit_soc"],
        charge_power_kw=row["charge_power_kw"],
        minutes_to_full=row["minutes_to_full"],
        plugged_in=_unflag(row["plugged_in"]),
        locked=_unflag(row["locked"]),
        inside_temp_c=row["inside_temp_c"],
        outside_temp_c=row["outside_temp_c"],
        climate_on=_unflag(row["climate_on"]),
        driver_temp_c=row["driver_temp_c"],
        passenger_temp_c=row["passenger_temp_c"],
        odometer_mi=row["odometer_mi"],
        software_version=row["software_version"],
        software_update_status=row["software_update_status"],
        software_update_version=row["software_update_version"],
        latitude=row["latitude"],
        longitude=row["longitude"],
    )
