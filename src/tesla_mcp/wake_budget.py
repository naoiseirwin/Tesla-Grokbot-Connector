"""Local wake budget.

Tesla bills wakes and caps them at 3 per minute per device. The per-minute cap
in this process cannot be configured above that. The daily cap and minimum gap
default to 3 per day and 30 minutes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import HARD_WAKE_PER_MINUTE, Settings
from .history import HistoryStore
from .time_utils import local_day_start, utc_now
from .vin import mask_vin


@dataclass(frozen=True)
class WakeDecision:
    allowed: bool
    reason: str
    used_today: int
    remaining_today: int


class WakeBudget:
    def __init__(self, history: HistoryStore, settings: Settings) -> None:
        self.history = history
        self.settings = settings
        self.per_minute_cap = min(settings.wake_per_minute_cap, HARD_WAKE_PER_MINUTE)

    def check(self, vin: str, *, now: datetime | None = None) -> WakeDecision:
        moment = now or utc_now()
        sent = self.history.wake_times(outcome="sent")
        used_today = sum(
            1 for item in sent if item >= local_day_start(moment, self.settings.timezone)
        )
        remaining = max(self.settings.wake_daily_cap - used_today, 0)
        window = moment - timedelta(seconds=60)
        in_minute = sum(1 for item in sent if item >= window)
        if self.per_minute_cap <= 0 or in_minute >= self.per_minute_cap:
            self._record(vin, "denied_rate", moment)
            return WakeDecision(False, "per_minute", used_today, remaining)
        if sent:
            elapsed = (moment - sent[-1]).total_seconds()
            if elapsed < self.settings.wake_min_interval_seconds:
                self._record(vin, "denied_interval", moment)
                return WakeDecision(False, "interval", used_today, remaining)
        if used_today >= self.settings.wake_daily_cap:
            self._record(vin, "denied_daily", moment)
            return WakeDecision(False, "daily", used_today, remaining)
        return WakeDecision(True, "ok", used_today, remaining)

    def mark_sent(self, vin: str, *, now: datetime | None = None) -> WakeDecision:
        moment = now or utc_now()
        self._record(vin, "sent", moment)
        sent = self.history.wake_times(outcome="sent")
        used_today = sum(
            1 for item in sent if item >= local_day_start(moment, self.settings.timezone)
        )
        remaining = max(self.settings.wake_daily_cap - used_today, 0)
        return WakeDecision(True, "sent", used_today, remaining)

    def _record(self, vin: str, outcome: str, moment: datetime) -> None:
        self.history.record_wake(vin_masked=mask_vin(vin), outcome=outcome, recorded_at=moment)

    def summary(self, *, now: datetime | None = None) -> dict[str, int]:
        moment = now or utc_now()
        sent = self.history.wake_times(outcome="sent")
        used_today = sum(
            1 for item in sent if item >= local_day_start(moment, self.settings.timezone)
        )
        return {
            "used_today": used_today,
            "daily_cap": self.settings.wake_daily_cap,
            "remaining_today": max(self.settings.wake_daily_cap - used_today, 0),
            "min_interval_seconds": self.settings.wake_min_interval_seconds,
            "hard_per_minute": self.per_minute_cap,
        }
