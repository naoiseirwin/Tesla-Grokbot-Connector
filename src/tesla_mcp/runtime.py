"""Process-wide handles shared by the MCP server, the poller, and the CLI."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .errors import TeslaAuthError
from .history import HistoryStore
from .logging_utils import logger
from .scopes import requested_scopes, scope_report, scopes_from_access_token
from .token_store import Exchange, TokenStore
from .wake_budget import WakeBudget


@dataclass
class AppRuntime:
    settings: Settings
    tokens: TokenStore
    history: HistoryStore
    wakes: WakeBudget
    granted_scopes: list[str] | None
    scopes_known: bool

    def requested(self) -> list[str]:
        return requested_scopes(
            location=self.settings.enable_location,
            charging_history=self.settings.enable_charging_history,
        )

    def describe_scopes(self) -> str:
        return scope_report(requested=self.requested(), granted=self.granted_scopes)

    def opt_in_available(self, enabled: bool, scope: str) -> bool:
        if not enabled:
            return False
        if (
            self.scopes_known
            and self.granted_scopes is not None
            and scope not in self.granted_scopes
        ):
            return False
        return True


def build_runtime(
    settings: Settings,
    exchange: Exchange | None = None,
    *,
    for_commands: bool = False,
) -> AppRuntime:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    history = HistoryStore(settings.history_path, required=not for_commands)
    tokens = TokenStore(settings, exchange=exchange)
    record = None
    try:
        record = tokens.load()
    except TeslaAuthError as exc:
        logger.warning("tool=startup vin=- status=token_unreadable category=-")
        logger.warning("%s", exc.message)
    granted: list[str] | None = None
    if record is not None:
        decoded = scopes_from_access_token(record.access_token)
        granted = decoded if decoded is not None else list(record.scopes)
    runtime = AppRuntime(
        settings=settings,
        tokens=tokens,
        history=history,
        wakes=WakeBudget(history, settings),
        granted_scopes=granted,
        scopes_known=record is not None,
    )
    if not for_commands:
        logger.info("tool=startup vin=- status=scopes category=-")
        for line in runtime.describe_scopes().splitlines():
            logger.info("%s", line)
    return runtime
