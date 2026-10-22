"""Settings from the environment and an optional config.env. No secrets have defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .crypto import parse_key
from .regions import AUTHORIZE_URL, DEFAULT_REDIRECT_URI, TOKEN_URL, get_region

HARD_WAKE_PER_MINUTE = 3
MIN_POLL_INTERVAL_HOURS = 1.0
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def default_data_dir() -> Path:
    configured = os.environ.get("TESLA_MCP_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    if Path("/workspace").is_dir():
        return Path("/workspace/.tesla-mcp")
    return Path.home() / ".tesla-mcp"


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    lowered = value.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ValueError(f"Expected true or false, got {value!r}.")


def _int(value: str | None, default: int) -> int:
    if value is None or value.strip() == "":
        return default
    return int(value)


def load_config_files() -> None:
    """Load config files without overriding variables already set in the process."""
    explicit = os.environ.get("TESLA_MCP_CONFIG")
    if explicit:
        load_dotenv(explicit, override=False)
    data_dir = default_data_dir()
    config_env = data_dir / "config.env"
    if config_env.is_file():
        load_dotenv(config_env, override=False)
    cwd_env = Path.cwd() / ".env"
    if cwd_env.is_file():
        load_dotenv(cwd_env, override=False)


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    region: str
    redirect_uri: str
    token_key: bytes | None
    data_dir: Path
    enable_location: bool
    enable_charging_history: bool
    wake_daily_cap: int
    wake_min_interval_seconds: int
    wake_per_minute_cap: int
    poll_interval_hours: float
    fleet_api_base: str | None
    token_url: str
    auth_url: str
    timezone: str

    @property
    def fleet_base(self) -> str:
        if self.fleet_api_base:
            return self.fleet_api_base.rstrip("/")
        return get_region(self.region).fleet_api

    @property
    def audience(self) -> str:
        """Token audience is the Fleet API base URL for this region."""
        return self.fleet_base

    @property
    def effective_poll_interval_hours(self) -> float:
        return max(self.poll_interval_hours, MIN_POLL_INTERVAL_HOURS)

    @property
    def token_path(self) -> Path:
        return self.data_dir / "tokens.json.enc"

    @property
    def history_path(self) -> Path:
        return self.data_dir / "history.db"

    @property
    def config_env_path(self) -> Path:
        return self.data_dir / "config.env"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "tokens.lock"

    @property
    def state_path(self) -> Path:
        return self.data_dir / "oauth-state"

    def require_client(self) -> None:
        from .errors import TeslaConfigError

        if not self.client_id or self.client_id == "your_client_id_here":
            raise TeslaConfigError(
                "TESLA_CLIENT_ID is not set. Put it in the environment or config.env. "
                "Do not paste it into chat."
            )
        if not self.client_secret or self.client_secret == "your_client_secret_here":
            raise TeslaConfigError(
                "TESLA_CLIENT_SECRET is not set. Put it in the environment or config.env. "
                "Do not paste it into chat."
            )

    def require_token_key(self) -> bytes:
        from .errors import TeslaConfigError

        if self.token_key is None:
            raise TeslaConfigError(
                "TESLA_TOKEN_KEY is not set. Hold the AES key in a secret or an env var, "
                "not in the repo. Run `tesla-mcp gen-key` on a private terminal if you "
                "need a new key, and do not paste the key into chat."
            )
        return self.token_key

    @classmethod
    def load(cls) -> Settings:
        load_config_files()
        region = os.environ.get("TESLA_REGION", "na")
        get_region(region)
        key_raw = (os.environ.get("TESLA_TOKEN_KEY") or "").strip()
        if not key_raw or key_raw == "your_base64_token_key_here":
            token_key = None
        else:
            token_key = parse_key(key_raw)
        per_minute = _int(os.environ.get("TESLA_WAKE_PER_MINUTE"), HARD_WAKE_PER_MINUTE)
        per_minute = min(max(per_minute, 0), HARD_WAKE_PER_MINUTE)
        daily = max(_int(os.environ.get("TESLA_WAKE_DAILY_CAP"), 3), 0)
        interval = max(_int(os.environ.get("TESLA_WAKE_MIN_INTERVAL_SECONDS"), 1800), 0)
        poll_hours = float(os.environ.get("TESLA_POLL_INTERVAL_HOURS", "2"))
        fleet_base = os.environ.get("TESLA_FLEET_API_BASE") or None
        return cls(
            client_id=os.environ.get("TESLA_CLIENT_ID", "").strip(),
            client_secret=os.environ.get("TESLA_CLIENT_SECRET", "").strip(),
            region=region.strip().lower(),
            redirect_uri=os.environ.get("TESLA_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip(),
            token_key=token_key,
            data_dir=default_data_dir(),
            enable_location=_bool(os.environ.get("TESLA_ENABLE_LOCATION"), False),
            enable_charging_history=_bool(os.environ.get("TESLA_ENABLE_CHARGING_HISTORY"), False),
            wake_daily_cap=daily,
            wake_min_interval_seconds=interval,
            wake_per_minute_cap=per_minute,
            poll_interval_hours=poll_hours,
            fleet_api_base=fleet_base.strip() if isinstance(fleet_base, str) else None,
            token_url=os.environ.get("TESLA_TOKEN_URL", TOKEN_URL).strip(),
            auth_url=os.environ.get("TESLA_AUTH_URL", AUTHORIZE_URL).strip(),
            timezone=os.environ.get("TESLA_TIMEZONE", "UTC").strip() or "UTC",
        )
