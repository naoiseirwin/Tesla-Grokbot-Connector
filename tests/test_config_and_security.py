"""Configuration defaults, the static command check, and secret hygiene."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tesla_mcp.config import (
    DEFAULT_COMMANDS_DAILY_CAP,
    DEFAULT_COMMANDS_PER_COMMAND_DAILY_CAP,
    HARD_WAKE_PER_MINUTE,
    Settings,
)
from tesla_mcp.crypto import generate_key, parse_key
from tesla_mcp.partner import normalize_domain
from tesla_mcp.regions import REGIONS
from tesla_mcp.scopes import FORBIDDEN_SCOPES, requested_scopes
from tesla_mcp.vin import mask_path, mask_vin

ROOT = Path(__file__).resolve().parents[1]
# The scan guards the read-only package. The optional commands package lives
# beside it and is the only place command endpoints are allowed.
SRC = ROOT / "src" / "tesla_mcp"

FORBIDDEN_ENDPOINT_STRINGS = (
    "door_lock",
    "door_unlock",
    "honk_horn",
    "flash_lights",
    "auto_conditioning_start",
    "auto_conditioning_stop",
    "charge_start",
    "charge_stop",
    "charge_max_range",
    "set_charge_limit",
    "actuate_trunk",
    "window_control",
    "remote_start_drive",
    "trigger_homelink",
    "set_sentry_mode",
    "tesla-http-proxy",
    "tesla_http_proxy",
    "/command/",
)


def test_defaults_are_read_only(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("TESLA_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TESLA_MCP_DATA_DIR", str(tmp_path))
    settings = Settings.load()
    assert settings.region == "na"
    assert settings.enable_location is False
    assert settings.enable_charging_history is False
    assert settings.wake_daily_cap == 3
    assert settings.wake_min_interval_seconds == 1800
    assert settings.wake_per_minute_cap == HARD_WAKE_PER_MINUTE
    assert settings.redirect_uri == "http://localhost:8765/callback"
    assert settings.fleet_base == REGIONS["na"].fleet_api
    assert settings.token_key is None
    assert settings.enable_commands is False
    assert settings.commands_dry_run is True
    assert settings.commands_allowlist == ()
    assert settings.commands_daily_cap == DEFAULT_COMMANDS_DAILY_CAP
    assert settings.commands_per_command_daily_cap == DEFAULT_COMMANDS_PER_COMMAND_DAILY_CAP
    assert settings.command_proxy_url is None
    assert settings.command_proxy_insecure is False


def _clear_tesla_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("TESLA_"):
            monkeypatch.delenv(key, raising=False)


_REJECTED_ENDPOINTS = (
    "https://evil.example/token",
    "https://tesla.com.evil.example/oauth2/v3/authorize",
    "https://auth.tesla.com.evil.example/oauth2/v3/authorize",
    "https://evil-tesla.com/oauth2/v3/token",
    "https://nottesla.com/",
    "http://auth.tesla.com/oauth2/v3/authorize",
    "http://fleet-api.prd.na.vn.cloud.tesla.com",
    "http://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token",
    "https://auth.tesla.com@evil.example/oauth2/v3/token",
    "https://user:not-a-secret@auth.tesla.com/oauth2/v3/token",
    "https://127.0.0.1/token",
    "https://anything.tesla.com",
    "https://fleet-api.prd.na.vn.cloud.tesla.com:8443",
    "https://fleet-api.prd.na.vn.cloud.tesla.com:0",
    "https://fleet-api.prd.na.vn.cloud.tesla.com:99999",
    "https://auth.tesla.com:8443/oauth2/v3/authorize",
    "https://auth.tesla.com:0/oauth2/v3/authorize",
    "https://auth.tesla.com:99999/oauth2/v3/authorize",
    "https://evil.com;.tesla.com",
    "https://evil.com,.tesla.com",
    "https://auth.tesla.com./oauth2/v3/authorize",
    "https://auth.tesla.com../oauth2/v3/authorize",
    "https://fleet-api.prd.na.vn.cloud.tesla.com..",
    "https://8.8.8.8/oauth2/v3/token",
    "https://[::1]/oauth2/v3/token",
    "https://[2001:db8::1]/oauth2/v3/authorize",
    "https://\u0430uth.tesla.com/oauth2/v3/authorize",
    "https://auth.xn--tesla-j5a.com/oauth2/v3/authorize",
    "https://xn--uth-5cd.tesla.com/oauth2/v3/authorize",
    "https://auth\uff0etesla\uff0ecom/oauth2/v3/authorize",
    "https://auth.tesla.com/oauth2/v3/authorize?next=1",
    "https://auth.tesla.com/oauth2/v3/authorize#fragment",
    "https://fleet-api.prd.cn.vn.cloud.tesla.cn",
    "https://EVIL.EXAMPLE/token",
)


@pytest.mark.parametrize("name", ["TESLA_FLEET_API_BASE", "TESLA_TOKEN_URL", "TESLA_AUTH_URL"])
@pytest.mark.parametrize("value", _REJECTED_ENDPOINTS)
def test_tesla_endpoint_overrides_reject_foreign_hosts(monkeypatch, tmp_path, name, value):
    _clear_tesla_env(monkeypatch)
    monkeypatch.setenv("TESLA_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        Settings.load()


def test_region_default_endpoints_are_accepted(monkeypatch, tmp_path):
    from tesla_mcp.regions import AUTHORIZE_URL, TOKEN_URL

    _clear_tesla_env(monkeypatch)
    monkeypatch.setenv("TESLA_MCP_DATA_DIR", str(tmp_path))
    for code, region in REGIONS.items():
        monkeypatch.setenv("TESLA_REGION", code)
        monkeypatch.delenv("TESLA_FLEET_API_BASE", raising=False)
        monkeypatch.delenv("TESLA_TOKEN_URL", raising=False)
        monkeypatch.delenv("TESLA_AUTH_URL", raising=False)
        settings = Settings.load()
        assert settings.fleet_base == region.fleet_api
        assert settings.token_url == TOKEN_URL
        assert settings.auth_url == AUTHORIZE_URL
        monkeypatch.setenv("TESLA_FLEET_API_BASE", region.fleet_api + "/")
        monkeypatch.setenv("TESLA_TOKEN_URL", TOKEN_URL)
        monkeypatch.setenv("TESLA_AUTH_URL", AUTHORIZE_URL)
        explicit = Settings.load()
        assert explicit.fleet_api_base == region.fleet_api.rstrip("/")
        assert explicit.token_url == TOKEN_URL
        assert explicit.auth_url == AUTHORIZE_URL


def test_uppercase_hosts_and_port_443_canonicalize(monkeypatch, tmp_path):
    from tesla_mcp.regions import AUTHORIZE_URL, TOKEN_URL

    _clear_tesla_env(monkeypatch)
    monkeypatch.setenv("TESLA_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TESLA_REGION", "na")
    monkeypatch.setenv("TESLA_AUTH_URL", "https://AUTH.TESLA.COM:443/oauth2/v3/authorize")
    monkeypatch.setenv(
        "TESLA_TOKEN_URL", "https://FLEET-AUTH.PRD.VN.CLOUD.TESLA.COM:443/oauth2/v3/token"
    )
    monkeypatch.setenv(
        "TESLA_FLEET_API_BASE",
        "https://FLEET-API.PRD.NA.VN.CLOUD.TESLA.COM:443/",
    )
    settings = Settings.load()
    assert settings.auth_url == AUTHORIZE_URL
    assert settings.token_url == TOKEN_URL
    assert settings.fleet_api_base == REGIONS["na"].fleet_api
    assert settings.auth_url == "https://auth.tesla.com/oauth2/v3/authorize"


def test_tesla_cn_fleet_host_requires_the_cn_region(monkeypatch, tmp_path):
    from tesla_mcp.regions import AUTHORIZE_URL, TOKEN_URL

    cn = REGIONS["cn"].fleet_api
    eu = REGIONS["eu"].fleet_api
    _clear_tesla_env(monkeypatch)
    monkeypatch.setenv("TESLA_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TESLA_REGION", "na")
    monkeypatch.setenv("TESLA_FLEET_API_BASE", cn)
    with pytest.raises(ValueError, match="TESLA_FLEET_API_BASE"):
        Settings.load()
    monkeypatch.setenv("TESLA_FLEET_API_BASE", eu)
    with pytest.raises(ValueError, match="TESLA_FLEET_API_BASE"):
        Settings.load()
    monkeypatch.setenv("TESLA_REGION", "cn")
    monkeypatch.setenv("TESLA_FLEET_API_BASE", cn + "/")
    loaded = Settings.load()
    assert loaded.fleet_api_base == cn
    assert loaded.token_url == TOKEN_URL
    assert loaded.auth_url == AUTHORIZE_URL
    assert "tesla.cn" not in loaded.token_url
    assert "tesla.cn" not in loaded.auth_url
    monkeypatch.setenv(
        "TESLA_TOKEN_URL",
        "https://fleet-auth.prd.vn.cloud.tesla.cn/oauth2/v3/token",
    )
    with pytest.raises(ValueError, match="TESLA_TOKEN_URL"):
        Settings.load()
    monkeypatch.setenv("TESLA_TOKEN_URL", TOKEN_URL)
    monkeypatch.setenv("TESLA_AUTH_URL", "https://auth.tesla.cn/oauth2/v3/authorize")
    with pytest.raises(ValueError, match="TESLA_AUTH_URL"):
        Settings.load()


def test_httpx_clients_disable_redirects_and_env_proxy():
    import ast

    found: list[str] = []
    for path in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"Client", "AsyncClient"}:
                continue
            flags: dict[str, object] = {}
            for keyword in node.keywords:
                if isinstance(keyword.value, ast.Constant):
                    flags[keyword.arg or ""] = keyword.value.value
            found.append(str(path.relative_to(ROOT)))
            assert flags.get("follow_redirects") is False, path
            assert flags.get("trust_env") is False, path
    assert set(found) == {
        "src/tesla_commands/proxy.py",
        "src/tesla_mcp/auth_flow.py",
        "src/tesla_mcp/fleet.py",
        "src/tesla_mcp/partner.py",
    }


async def test_fleet_client_ignores_proxy_and_ssl_env(monkeypatch, tmp_path):
    from tesla_mcp.fleet import FleetClient
    from tesla_mcp.runtime import build_runtime
    from tests.helpers import make_settings

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("SSL_CERT_FILE", "/tmp/not-a-real-cert.pem")
    runtime = build_runtime(make_settings(tmp_path))
    async with FleetClient(runtime) as fleet:
        client = fleet._client
        assert client is not None
        assert client.follow_redirects is False
        assert client.trust_env is False
        assert client._mounts == {}


def test_cwd_dotenv_is_ignored(monkeypatch, tmp_path):
    _clear_tesla_env(monkeypatch)
    # load_dotenv writes os.environ directly. Restore that snapshot so a
    # planted .env cannot leak into later tests.
    snapshot = {key: os.environ.get(key) for key in os.environ if key.startswith("TESLA_")}
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "TESLA_REGION=cn\n"
        "TESLA_CLIENT_ID=from-cwd-dotenv\n"
        "TESLA_TOKEN_URL=https://evil.example/token\n"
        "TESLA_FLEET_API_BASE=https://evil.example\n"
        "TESLA_AUTH_URL=http://auth.tesla.com/oauth2/v3/authorize\n",
        encoding="utf-8",
    )
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.env").write_text(
        "TESLA_REGION=eu\nTESLA_CLIENT_ID=from-config-env\n",
        encoding="utf-8",
    )
    try:
        os.environ["TESLA_MCP_DATA_DIR"] = str(data)
        settings = Settings.load()
        assert settings.region == "eu"
        assert settings.client_id == "from-config-env"
        assert settings.fleet_api_base is None
        assert settings.fleet_base == REGIONS["eu"].fleet_api
        assert "evil.example" not in settings.token_url
        assert settings.auth_url.startswith("https://auth.tesla.com")
        os.environ["TESLA_REGION"] = "na"
        assert Settings.load().region == "na"
        # Already-set process variables win. Drop the ones config.env just
        # loaded so the explicit file can supply the client id, and config.env
        # can still supply the region. The cwd .env must not supply either.
        os.environ.pop("TESLA_CLIENT_ID", None)
        os.environ.pop("TESLA_REGION", None)
        explicit = tmp_path / "explicit.env"
        explicit.write_text("TESLA_CLIENT_ID=from-explicit\n", encoding="utf-8")
        os.environ["TESLA_MCP_CONFIG"] = str(explicit)
        chosen = Settings.load()
        assert chosen.client_id == "from-explicit"
        assert chosen.region == "eu"
    finally:
        for key in list(os.environ):
            if key.startswith("TESLA_") and key not in snapshot:
                del os.environ[key]
        for key, value in snapshot.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_wake_daily_cap_and_interval_are_bounded(monkeypatch, tmp_path):
    _clear_tesla_env(monkeypatch)
    monkeypatch.setenv("TESLA_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TESLA_WAKE_DAILY_CAP", "10")
    monkeypatch.setenv("TESLA_WAKE_MIN_INTERVAL_SECONDS", "86400")
    settings = Settings.load()
    assert settings.wake_daily_cap == 10
    assert settings.wake_min_interval_seconds == 86400
    monkeypatch.setenv("TESLA_WAKE_DAILY_CAP", "11")
    with pytest.raises(ValueError, match="TESLA_WAKE_DAILY_CAP"):
        Settings.load()
    monkeypatch.setenv("TESLA_WAKE_DAILY_CAP", "3")
    monkeypatch.setenv("TESLA_WAKE_MIN_INTERVAL_SECONDS", "86401")
    with pytest.raises(ValueError, match="TESLA_WAKE_MIN_INTERVAL_SECONDS"):
        Settings.load()


def test_wake_per_minute_cannot_exceed_teslas_cap(monkeypatch, tmp_path):
    monkeypatch.setenv("TESLA_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TESLA_WAKE_PER_MINUTE", "99")
    monkeypatch.setenv("TESLA_POLL_INTERVAL_HOURS", "0.25")
    monkeypatch.setenv("TESLA_ENABLE_CHARGING_HISTORY", "false")
    settings = Settings.load()
    assert settings.wake_per_minute_cap == 3
    assert settings.effective_poll_interval_hours == 1


def test_regions_and_requested_scopes():
    assert set(REGIONS) == {"na", "eu", "cn"}
    assert "cn" in REGIONS["cn"].fleet_api
    scopes = requested_scopes(location=False, charging_history=False)
    assert FORBIDDEN_SCOPES.isdisjoint(scopes)
    assert "vehicle_charging_cmds" not in scopes
    assert "vehicle_location" not in scopes


def test_mask_vin_and_path():
    assert mask_vin("5YJ3E1EA7KF000001") == "5YJ…0001"
    assert mask_path("/api/1/vehicles/5YJ3E1EA7KF000001/vehicle_data") == (
        "/api/1/vehicles/5YJ…0001/vehicle_data"
    )


def test_domain_normalizer():
    assert normalize_domain("https://naoiseirwin.github.io/foo") == "naoiseirwin.github.io"


def test_generated_key_round_trips():
    key = generate_key()
    assert len(parse_key(key)) == 32


def test_src_has_no_command_endpoints_except_wake_up():
    offenders: list[str] = []
    wake_files: list[str] = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(ROOT))
        if "wake_up" in text:
            wake_files.append(relative)
        for forbidden in FORBIDDEN_ENDPOINT_STRINGS:
            if forbidden in text:
                offenders.append(f"{relative}: {forbidden}")
    assert offenders == []
    assert sorted(wake_files) == [
        "src/tesla_mcp/fleet.py",
        "src/tesla_mcp/tools/wake.py",
    ]


def test_repo_has_no_private_keys():
    skip = {".venv", ".git", "__pycache__", ".pytest_cache", ".ruff_cache", "dist", "build"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in skip for part in path.parts):
            continue
        if path.suffix in {".pem", ".key"}:
            raise AssertionError(f"unexpected key file: {path}")
        if "tests" in path.parts:
            continue
        if "src" in path.parts or path.suffix in {
            ".py",
            ".md",
            ".html",
            ".yml",
            ".toml",
            ".example",
        }:
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "BEGIN PRIVATE KEY" not in text
            assert "BEGIN EC PRIVATE KEY" not in text
            assert "BEGIN OPENSSH PRIVATE KEY" not in text


def test_live_smoke_refuses_without_an_explicit_flag():
    env = os.environ.copy()
    env.pop("TESLA_LIVE_SMOKE", None)
    result = subprocess.run(
        [sys.executable, "-m", "tesla_mcp.scripts.live_smoke"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "TESLA_LIVE_SMOKE" in result.stderr


def test_keypair_script_refuses_the_git_repo(tmp_path):
    script = ROOT / "scripts" / "generate-keypair.sh"
    inside = subprocess.run(
        [str(script), str(ROOT / "not-a-key-dir")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert inside.returncode == 1
    outside = tmp_path / "keys"
    if os.environ.get("OPENSSL_AVAILABLE", "1") == "0":
        pytest.skip("openssl disabled")
    generated = subprocess.run(
        [str(script), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr
    assert (outside / "public-key.pem").is_file()
    assert (outside / "private-key.pem").is_file()
    assert "BEGIN PUBLIC KEY" in (outside / "public-key.pem").read_text(encoding="utf-8")
    mode = (outside / "private-key.pem").stat().st_mode & 0o777
    assert mode == 0o600


def test_configure_logging_hides_httpx_urls_that_contain_a_vin(caplog):
    import logging

    import httpx

    from tesla_mcp.logging_utils import configure_logging, log_event

    vin = "5YJ3E1EA7KF000001"
    configure_logging()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING

    def _ok(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": {}})

    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.INFO)
    try:
        with caplog.at_level(logging.DEBUG):
            url = f"https://127.0.0.1:4443/api/1/vehicles/{vin}/command/door_lock"
            with httpx.Client(transport=httpx.MockTransport(_ok)) as client:
                client.post(url)
            log_event("tesla_door_lock", vin, "ok", "command")
    finally:
        root.setLevel(previous)

    assert vin not in caplog.text
    assert mask_vin(vin) in caplog.text
