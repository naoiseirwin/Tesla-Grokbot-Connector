"""Local wake budget.

Tesla bills wakes and caps them at 3 per minute per device. The per-minute cap
in this process cannot be configured above that. The daily cap and minimum gap
default to 3 wakes in a rolling 24-hour window and 30 minutes apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import HARD_WAKE_PER_MINUTE, Settings
from .history import HistoryStore
from .time_utils import budget_window_start, utc_now
from .vin import mask_vin

_DENIED_OUTCOME = {
    "per_minute": "denied_rate",
    "interval": "denied_interval",
    "daily": "denied_daily",
}


@dataclass(frozen=True)
class WakeDecision:
    allowed: bool
    reason: str
    used_today: int
    remaining_today: int
    event_id: int | None = None


class WakeBudget:
    def __init__(self, history: HistoryStore, settings: Settings) -> None:
        self.history = history
        self.settings = settings
        self.per_minute_cap = min(settings.wake_per_minute_cap, HARD_WAKE_PER_MINUTE)

    def reserve(self, vin: str, *, now: datetime | None = None) -> WakeDecision:
        """Count the wake before the HTTP call, under a write lock.

        A crash after Tesla accepts the wake still counts it. Call `release`
        only when the response was not billed (network failure or HTTP 500+).
        """
        moment = now or utc_now()
        with self.history.immediate() as conn:
            sent = self.history.read_wake_times(conn, outcome="sent")
            decision = self._decide(sent, moment)
            if not decision.allowed:
                self.history.insert_wake(
                    conn,
                    vin_masked=mask_vin(vin),
                    outcome=_DENIED_OUTCOME[decision.reason],
                    recorded_at=moment,
                )
                return decision
            event_id = self.history.insert_wake(
                conn,
                vin_masked=mask_vin(vin),
                outcome="sent",
                recorded_at=moment,
            )
            used = decision.used_today + 1
            remaining = max(self.settings.wake_daily_cap - used, 0)
            return WakeDecision(True, "ok", used, remaining, event_id)

    def release(self, event_id: int) -> None:
        """Drop a reservation Tesla did not bill."""
        self.history.delete_wake(event_id)

    def _decide(self, sent: list[datetime], moment: datetime) -> WakeDecision:
        window_start = budget_window_start(moment)
        used_today = sum(1 for item in sent if item >= window_start)
        remaining = max(self.settings.wake_daily_cap - used_today, 0)
        window = moment - timedelta(seconds=60)
        in_minute = sum(1 for item in sent if item >= window)
        if self.per_minute_cap <= 0 or in_minute >= self.per_minute_cap:
            return WakeDecision(False, "per_minute", used_today, remaining)
        if sent:
            elapsed = (moment - sent[-1]).total_seconds()
            if elapsed < self.settings.wake_min_interval_seconds:
                return WakeDecision(False, "interval", used_today, remaining)
        if used_today >= self.settings.wake_daily_cap:
            return WakeDecision(False, "daily", used_today, remaining)
        return WakeDecision(True, "ok", used_today, remaining)

    def summary(self, *, now: datetime | None = None) -> dict[str, int]:
        moment = now or utc_now()
        sent = self.history.wake_times(outcome="sent")
        window_start = budget_window_start(moment)
        used_today = sum(1 for item in sent if item >= window_start)
        return {
            "used_today": used_today,
            "daily_cap": self.settings.wake_daily_cap,
            "remaining_today": max(self.settings.wake_daily_cap - used_today, 0),
            "min_interval_seconds": self.settings.wake_min_interval_seconds,
            "hard_per_minute": self.per_minute_cap,
        }
