"""Configuration defaults, the static command check, and secret hygiene."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tesla_mcp.config import HARD_WAKE_PER_MINUTE, Settings
from tesla_mcp.crypto import generate_key, parse_key
from tesla_mcp.partner import normalize_domain
from tesla_mcp.regions import REGIONS
from tesla_mcp.scopes import FORBIDDEN_SCOPES, requested_scopes
from tesla_mcp.vin import mask_path, mask_vin

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

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
