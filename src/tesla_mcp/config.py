"""Settings from the process environment and an explicit config file.

No secrets have defaults. A `.env` file in the working directory is not read.
Fleet, token, and authorize URLs must each be one exact https Tesla endpoint.
The Fleet host depends on `TESLA_REGION`. Token and sign-in stay on tesla.com
in every region. `tesla.cn` is only the China Fleet host, and only when the
region is `cn`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from .crypto import parse_key
from .regions import AUTHORIZE_URL, DEFAULT_REDIRECT_URI, TOKEN_URL, get_region

HARD_WAKE_PER_MINUTE = 3
MIN_POLL_INTERVAL_HOURS = 1.0
DEFAULT_COMMANDS_DAILY_CAP = 10
MAX_COMMANDS_DAILY_CAP = 40
DEFAULT_COMMANDS_PER_COMMAND_DAILY_CAP = 3
MAX_COMMANDS_PER_COMMAND_DAILY_CAP = 10
DEFAULT_WAKE_DAILY_CAP = 3
MAX_WAKE_DAILY_CAP = 10
DEFAULT_WAKE_MIN_INTERVAL_SECONDS = 1800
MAX_WAKE_MIN_INTERVAL_SECONDS = 24 * 60 * 60
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# Exact hosts from regions.py. Token and sign-in stay on tesla.com in every
# region, including cn. tesla.cn is only the China Fleet API host.
_TOKEN_HOST = "fleet-auth.prd.vn.cloud.tesla.com"
_AUTH_HOST = "auth.tesla.com"
_TOKEN_PATH = "/oauth2/v3/token"
_AUTH_PATH = "/oauth2/v3/authorize"
_FLEET_HOSTS = {
    "na": "fleet-api.prd.na.vn.cloud.tesla.com",
    "eu": "fleet-api.prd.eu.vn.cloud.tesla.com",
    "cn": "fleet-api.prd.cn.vn.cloud.tesla.cn",
}
_LDH_LABEL = re.compile(r"^[a-z0-9-]{1,63}$")
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


def _bounded_int(value: str | None, default: int, *, name: str, upper: int) -> int:
    try:
        parsed = _int(value, default)
    except ValueError:
        raise ValueError(f"{name} must be a whole number from 0 to {upper}.") from None
    if parsed < 0 or parsed > upper:
        raise ValueError(f"{name} must be a whole number from 0 to {upper}. Got {parsed}.")
    return parsed


def _csv(value: str | None) -> tuple[str, ...]:
    if value is None or not value.strip():
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for part in value.split(","):
        name = part.strip().lower().replace("-", "_")
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return tuple(names)


def _proxy_url(value: str | None, *, allow_remote: bool) -> str | None:
    """Base URL of the local signing proxy. Empty means live sends are refused."""
    if value is None or not value.strip():
        return None
    url = value.strip().rstrip("/")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError(
            "TESLA_COMMAND_PROXY_URL must be an http or https URL, "
            "for example https://127.0.0.1:4443."
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "TESLA_COMMAND_PROXY_URL must not include a username, password, query, or fragment."
        )
    remote = host not in _LOOPBACK_HOSTS
    if remote and not allow_remote:
        raise ValueError(
            "TESLA_COMMAND_PROXY_URL must point at this computer "
            "(localhost, 127.0.0.1, or ::1). The access token is sent to that URL. "
            "Set TESLA_COMMAND_PROXY_ALLOW_REMOTE=true only if you accept that."
        )
    if remote and parsed.scheme != "https":
        raise ValueError(
            "TESLA_COMMAND_PROXY_URL must use https when it is not on this computer. "
            "The access token is sent to this URL and must not travel in cleartext."
        )
    return url


def _ldh_hostname(host: str) -> bool:
    """True for LDH labels only, with no trailing dot and no empty label.

    ``urlparse`` keeps a trailing dot and leaves ``;`` and ``,`` inside a
    label. Neither form is one of the hosts below.
    """
    if not host or host.endswith(".") or ".." in host:
        return False
    return all(_LDH_LABEL.fullmatch(label) is not None for label in host.split("."))


def _allowed_endpoint(kind: str, region: str) -> tuple[str, str]:
    """The only host and path for this setting and region.

    Token and authorize hosts are the same in every region, including China.
    The Fleet host is that region's Fleet API host. ``tesla.cn`` appears only
    there. These strings are the hosts in ``regions.py``.
    """
    key = region.strip().lower()
    if kind == "token":
        return _TOKEN_HOST, _TOKEN_PATH
    if kind == "auth":
        return _AUTH_HOST, _AUTH_PATH
    if kind == "fleet":
        host = _FLEET_HOSTS.get(key)
        if host is None:
            raise ValueError(f"TESLA_REGION must be one of na, eu, cn. Got {region!r}.")
        return host, ""
    raise ValueError(f"Unknown Tesla endpoint {kind!r}.")


def require_tesla_https_url(value: str, *, name: str, kind: str, region: str) -> str:
    """Return one exact Tesla https endpoint, rebuilt from its parsed parts.

    The raw string is not returned. An explicit port 443 is accepted and then
    omitted, so it matches the defaults in ``regions.py``. Any other port, a
    trailing dot, a username, a query, or a fragment is refused. A host that
    is not the one host for this setting and region is refused, including
    other ``tesla.com`` names, ``tesla.cn`` outside the China Fleet host, and
    lookalikes.
    """
    raw = value.strip()
    if not raw or any(ord(char) < 33 or ord(char) == 127 for char in raw):
        raise ValueError(f"{name} must be the https Tesla endpoint for this setting.")
    parsed = urlparse(raw)
    if parsed.scheme != "https":
        raise ValueError(f"{name} must be an https URL.")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ValueError(f"{name} must not include a username or password.")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{name} must not include a query or fragment.")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError(f"{name} must use port 443.") from None
    if port not in (None, 443):
        raise ValueError(f"{name} must use port 443.")
    host = parsed.hostname or ""
    if not _ldh_hostname(host):
        raise ValueError(f"{name} must be the https Tesla endpoint for this setting.")
    expected_host, expected_path = _allowed_endpoint(kind, region)
    path = parsed.path.rstrip("/")
    if host != expected_host or path != expected_path:
        if expected_path:
            shown = f"https://{expected_host}{expected_path}"
        else:
            shown = f"https://{expected_host}"
        raise ValueError(
            f"{name} must be {shown} for TESLA_REGION={region.strip().lower()}. "
            "Other hosts are refused."
        )
    if expected_path:
        return f"https://{expected_host}{expected_path}"
    return f"https://{expected_host}"


def insecure_proxy_problem(url: str | None, insecure: bool) -> str | None:
    """Refuse TLS verification off unless the proxy is on this computer.

    The access token is sent to the proxy. Skipping certificate checks for a
    host that is not loopback would let that host collect the token.
    """
    if not insecure or not url:
        return None
    host = (urlparse(url).hostname or "").lower()
    if host in _LOOPBACK_HOSTS:
        return None
    return (
        "TESLA_COMMAND_PROXY_INSECURE is only allowed when TESLA_COMMAND_PROXY_URL "
        "points at this computer (localhost, 127.0.0.1, or ::1)."
    )


def load_config_files() -> None:
    """Load explicit config files. Never reads a `.env` from the working directory.

    Process environment variables win. ``TESLA_MCP_CONFIG`` is loaded first,
    then ``<data dir>/config.env``. Neither overrides a variable that is already set.
    """
    explicit = os.environ.get("TESLA_MCP_CONFIG")
    if explicit:
        load_dotenv(explicit, override=False)
    data_dir = default_data_dir()
    config_env = data_dir / "config.env"
    if config_env.is_file():
        load_dotenv(config_env, override=False)


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
    # Command fields are ignored by the read-only server. Defaults keep commands off.
    enable_commands: bool = False
    commands_dry_run: bool = True
    commands_allowlist: tuple[str, ...] = ()
    commands_daily_cap: int = DEFAULT_COMMANDS_DAILY_CAP
    commands_per_command_daily_cap: int = DEFAULT_COMMANDS_PER_COMMAND_DAILY_CAP
    command_proxy_url: str | None = None
    command_proxy_ca: str | None = None
    command_proxy_insecure: bool = False
    command_proxy_allow_remote: bool = False
    # Set by the commands server when a command variable is invalid.
    # The read-only server leaves this empty and does not read those variables.
    command_config_error: str | None = None

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
        daily = _bounded_int(
            os.environ.get("TESLA_WAKE_DAILY_CAP"),
            DEFAULT_WAKE_DAILY_CAP,
            name="TESLA_WAKE_DAILY_CAP",
            upper=MAX_WAKE_DAILY_CAP,
        )
        interval = _bounded_int(
            os.environ.get("TESLA_WAKE_MIN_INTERVAL_SECONDS"),
            DEFAULT_WAKE_MIN_INTERVAL_SECONDS,
            name="TESLA_WAKE_MIN_INTERVAL_SECONDS",
            upper=MAX_WAKE_MIN_INTERVAL_SECONDS,
        )
        poll_hours = float(os.environ.get("TESLA_POLL_INTERVAL_HOURS", "2"))
        region_name = region.strip().lower()
        fleet_raw = (os.environ.get("TESLA_FLEET_API_BASE") or "").strip()
        if fleet_raw:
            fleet_base = require_tesla_https_url(
                fleet_raw,
                name="TESLA_FLEET_API_BASE",
                kind="fleet",
                region=region_name,
            )
        else:
            fleet_base = None
            require_tesla_https_url(
                get_region(region).fleet_api,
                name="TESLA_FLEET_API_BASE",
                kind="fleet",
                region=region_name,
            )
        token_url = require_tesla_https_url(
            os.environ.get("TESLA_TOKEN_URL", TOKEN_URL),
            name="TESLA_TOKEN_URL",
            kind="token",
            region=region_name,
        )
        auth_url = require_tesla_https_url(
            os.environ.get("TESLA_AUTH_URL", AUTHORIZE_URL),
            name="TESLA_AUTH_URL",
            kind="auth",
            region=region_name,
        )
        # Command variables (TESLA_ENABLE_COMMANDS, TESLA_COMMANDS_*, TESLA_COMMAND_PROXY_*)
        # are intentionally not read here. A bad value in the shared config.env must not
        # stop the read-only server. tesla_commands.config loads them for the commands server.
        return cls(
            client_id=os.environ.get("TESLA_CLIENT_ID", "").strip(),
            client_secret=os.environ.get("TESLA_CLIENT_SECRET", "").strip(),
            region=region_name,
            redirect_uri=os.environ.get("TESLA_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip(),
            token_key=token_key,
            data_dir=default_data_dir(),
            enable_location=_bool(os.environ.get("TESLA_ENABLE_LOCATION"), False),
            enable_charging_history=_bool(os.environ.get("TESLA_ENABLE_CHARGING_HISTORY"), False),
            wake_daily_cap=daily,
            wake_min_interval_seconds=interval,
            wake_per_minute_cap=per_minute,
            poll_interval_hours=poll_hours,
            fleet_api_base=fleet_base,
            token_url=token_url,
            auth_url=auth_url,
            timezone=os.environ.get("TESLA_TIMEZONE", "UTC").strip() or "UTC",
        )
