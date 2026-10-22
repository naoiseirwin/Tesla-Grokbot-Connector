"""Per-command and overall daily caps.

Counts live sends only. A dry run does not consume a slot. Refusals do not
either. The rows live in the same SQLite file as the wake budget.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tesla_mcp.config import Settings
from tesla_mcp.time_utils import isoformat, local_day_start, utc_now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS command_events (
    id INTEGER PRIMARY KEY,
    recorded_at TEXT NOT NULL,
    command TEXT NOT NULL,
    vin_masked TEXT NOT NULL,
    outcome TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class CommandDecision:
    allowed: bool
    reason: str
    used_today: int
    command_used_today: int
    remaining_today: int
    command_remaining_today: int
    event_id: int | None = None


class CommandBudget:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path: Path = settings.history_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
        os.chmod(self.path, 0o600)

    def peek(self, command: str, *, now: datetime | None = None) -> CommandDecision:
        moment = now or utc_now()
        with self._connect() as conn:
            return self._decide(conn, command, moment)

    def reserve(
        self,
        command: str,
        vin_masked: str,
        *,
        now: datetime | None = None,
    ) -> CommandDecision:
        """Count the command before the proxy call. Release it if Tesla did not bill it."""
        moment = now or utc_now()
        with self._immediate() as conn:
            decision = self._decide(conn, command, moment)
            if not decision.allowed:
                self._insert(
                    conn,
                    command,
                    vin_masked,
                    f"denied_{decision.reason}",
                    moment,
                )
                return decision
            event_id = self._insert(conn, command, vin_masked, "sent", moment)
            used = decision.used_today + 1
            command_used = decision.command_used_today + 1
            return CommandDecision(
                True,
                "ok",
                used,
                command_used,
                max(self.settings.commands_daily_cap - used, 0),
                max(self.settings.commands_per_command_daily_cap - command_used, 0),
                event_id,
            )

    def release(self, event_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM command_events WHERE id = ?", (event_id,))

    def _decide(
        self,
        conn: sqlite3.Connection,
        command: str,
        moment: datetime,
    ) -> CommandDecision:
        start = local_day_start(moment, self.settings.timezone)
        rows = conn.execute(
            """
            SELECT command FROM command_events
            WHERE outcome = 'sent' AND recorded_at >= ?
            """,
            (isoformat(start),),
        ).fetchall()
        used = len(rows)
        command_used = sum(1 for row in rows if str(row["command"]) == command)
        remaining = max(self.settings.commands_daily_cap - used, 0)
        command_remaining = max(self.settings.commands_per_command_daily_cap - command_used, 0)
        if command_used >= self.settings.commands_per_command_daily_cap:
            return CommandDecision(
                False,
                "command_cap",
                used,
                command_used,
                remaining,
                command_remaining,
            )
        if used >= self.settings.commands_daily_cap:
            return CommandDecision(
                False,
                "daily_cap",
                used,
                command_used,
                remaining,
                command_remaining,
            )
        return CommandDecision(
            True,
            "ok",
            used,
            command_used,
            remaining,
            command_remaining,
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _immediate(self) -> Iterator[sqlite3.Connection]:
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

    def _insert(
        self,
        conn: sqlite3.Connection,
        command: str,
        vin_masked: str,
        outcome: str,
        moment: datetime,
    ) -> int:
        cursor = conn.execute(
            """
            INSERT INTO command_events (recorded_at, command, vin_masked, outcome)
            VALUES (?, ?, ?, ?)
            """,
            (isoformat(moment), command, vin_masked, outcome),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("Command row did not receive an id.")
        return int(cursor.lastrowid)
