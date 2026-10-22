"""Opt-in command connector. Tesla and the signing proxy are mocked."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import httpx
import pytest
import respx
from fastmcp import Client

from tesla_commands.audit import audit_path
from tesla_commands.budget import CommandBudget
from tesla_commands.catalog import COMMANDS, build_body
from tesla_commands.errors import CommandRefusal
from tesla_commands.server import create_server
from tesla_mcp.auth_flow import build_authorize_url
from tesla_mcp.config import Settings
from tesla_mcp.errors import TeslaError
from tesla_mcp.fleet import FleetClient
from tesla_mcp.runtime import build_runtime
from tesla_mcp.server import create_server as create_readonly_server
from tesla_mcp.vin import mask_vin
from tests.fixtures.vehicles import vehicle_list
from tests.helpers import VIN, make_settings, seed_token, tool_json
from tests.stubs.fleet_api_stub import FleetAPIStub

SCOPES = ["openid", "offline_access", "vehicle_device_data"]
COMMAND_SCOPES = [*SCOPES, "vehicle_cmds"]
PROXY = "https://127.0.0.1:4443"
READ_ONLY_TOOLS = frozenset(
    {
        "tesla_list_vehicles",
        "tesla_get_status",
        "tesla_get_climate",
        "tesla_get_odometer",
        "tesla_get_software",
        "tesla_query_history",
        "tesla_report",
        "tesla_wake_and_refresh",
        "tesla_usage",
    }
)

# These stay out of the commands package. The test list is intentional and is
# not imported from the implementation.
EXCLUDED_COMMANDS = (
    "door_unlock",
    "actuate_trunk",
    "window_control",
    "remote_start",
    "remote_start_drive",
    "summon",
    "autopark",
    "auto_park",
    "sentry_off",
    "set_valet_mode",
    "reset_valet_pin",
    "speed_limit_activate",
    "speed_limit_deactivate",
    "speed_limit_set_limit",
    "speed_limit_clear_pin",
    "speed_limit_clear_pin_admin",
    "set_pin_to_drive",
    "reset_pin_to_drive_pin",
    "clear_pin_to_drive_admin",
    "navigation_request",
    "navigation_gps_request",
    "navigation_sc_request",
    "navigation_waypoints_request",
    "schedule_software_update",
    "cancel_software_update",
)

_HINT_KEYS = {
    "readOnlyHint": "read_only_hint",
    "idempotentHint": "idempotent_hint",
    "destructiveHint": "destructive_hint",
    "openWorldHint": "open_world_hint",
}


def _hint(tool: object, name: str) -> object:
    annotations = getattr(tool, "annotations", None)
    if annotations is None:
        return None
    keys = (name, _HINT_KEYS.get(name, name))
    if isinstance(annotations, dict):
        for key in keys:
            if key in annotations:
                return annotations[key]
        return None
    model_dump = getattr(annotations, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict):
            for key in keys:
                if key in dumped:
                    return dumped[key]
    for key in keys:
        if hasattr(annotations, key):
            return getattr(annotations, key)
    return None


def _clear_tesla_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("TESLA_"):
            monkeypatch.delenv(key, raising=False)


def command_settings(tmp_path, **overrides: object):
    data: dict[str, object] = {
        "enable_commands": True,
        "commands_dry_run": True,
        "commands_allowlist": tuple(spec.name for spec in COMMANDS.values()),
        "commands_daily_cap": 10,
        "commands_per_command_daily_cap": 3,
        "command_proxy_url": PROXY,
    }
    data.update(overrides)
    return make_settings(tmp_path, **data)


def _mock_command(
    router: respx.MockRouter, endpoint: str, payload: dict, status: int = 200
) -> None:
    router.post(url__regex=rf".*/command/{endpoint}$").mock(
        return_value=httpx.Response(status, json=payload)
    )


def _audit_text(settings) -> str:
    return audit_path(settings).read_text(encoding="utf-8")


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _audit_records(settings) -> list[dict]:
    return [json.loads(line) for line in _audit_text(settings).splitlines() if line.strip()]


async def test_commands_are_refused_unless_enabled(tmp_path):
    settings = make_settings(tmp_path)
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        async with Client(mcp) as client:
            result = tool_json(await client.call_tool("tesla_door_lock", {}))
        assert router.calls == []
    assert result["ok"] is False
    assert result["sent"] is False
    assert result["refusal_reason"] == "commands_disabled"
    assert "TESLA_ENABLE_COMMANDS" in str(result["error"])
    assert "Nothing was sent" in str(result["error"])
    record = _audit_records(settings)[0]
    assert record["result"] == "refused"
    assert record["refusal_reason"] == "commands_disabled"
    assert record["command"] == "door_lock"
    assert record["dry_run"] is True
    assert record["live"] is False
    assert "timestamp" in record


async def test_dry_run_is_the_default_and_does_not_call_tesla(tmp_path):
    settings = command_settings(tmp_path)
    assert settings.commands_dry_run is True
    record = seed_token(settings, scopes=COMMAND_SCOPES)
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        async with Client(mcp) as client:
            locked = tool_json(await client.call_tool("tesla_door_lock", {"vin": VIN}))
            limit = tool_json(await client.call_tool("tesla_set_charge_limit", {"percent": 80}))
            temps = tool_json(
                await client.call_tool(
                    "tesla_set_cabin_temperature",
                    {"driver_celsius": 21.5, "passenger_celsius": 19},
                )
            )
            seat = tool_json(
                await client.call_tool("tesla_set_seat_heater", {"seat": 1, "level": 3})
            )
            sentry = tool_json(await client.call_tool("tesla_sentry_mode_on", {}))
        assert router.calls == []
    assert locked["ok"] is True
    assert locked["sent"] is False
    assert locked["dry_run"] is True
    assert locked["would_send"] == {
        "method": "POST",
        "path": "/api/1/vehicles/{vin}/command/door_lock",
        "body": {},
    }
    assert "Nothing was sent" in str(locked["note"])
    assert limit["would_send"] == {
        "method": "POST",
        "path": "/api/1/vehicles/{vin}/command/set_charge_limit",
        "body": {"percent": 80},
    }
    assert _mapping(temps["would_send"])["body"] == {"driver_temp": 21.5, "passenger_temp": 19.0}
    assert _mapping(seat["would_send"])["body"] == {"heater": 1, "level": 3}
    assert _mapping(sentry["would_send"])["body"] == {"on": True}
    text = _audit_text(settings)
    assert VIN not in text
    assert record.access_token not in text
    assert "Bearer" not in text
    assert settings.client_secret not in text
    assert mask_vin(VIN) in text


async def test_empty_allowlist_and_missing_entries_are_refused(tmp_path):
    empty = command_settings(tmp_path, commands_allowlist=())
    seed_token(empty, scopes=COMMAND_SCOPES)
    mcp = create_server(empty)
    async with Client(mcp) as client:
        result = tool_json(await client.call_tool("tesla_door_lock", {}))
    assert result["refusal_reason"] == "not_allowlisted"
    assert result["sent"] is False

    partial = command_settings(tmp_path / "partial", commands_allowlist=("climate_on",))
    seed_token(partial, scopes=COMMAND_SCOPES)
    mcp = create_server(partial)
    async with Client(mcp) as client:
        blocked = tool_json(await client.call_tool("tesla_door_lock", {}))
        allowed = tool_json(await client.call_tool("tesla_climate_on", {}))
    assert blocked["refusal_reason"] == "not_allowlisted"
    assert allowed["ok"] is True
    assert str(_mapping(allowed["would_send"])["path"]).endswith("/command/auto_conditioning_start")


async def test_tesla_endpoint_name_in_the_allowlist_matches_the_tool(tmp_path):
    settings = command_settings(tmp_path, commands_allowlist=("auto_conditioning_start",))
    seed_token(settings, scopes=COMMAND_SCOPES)
    mcp = create_server(settings)
    async with Client(mcp) as client:
        result = tool_json(await client.call_tool("tesla_climate_on", {}))
    assert result["ok"] is True
    assert result["command"] == "climate_on"


async def test_validation_rejects_charge_limit_temperature_and_seat(tmp_path):
    settings = command_settings(tmp_path)
    seed_token(settings, scopes=COMMAND_SCOPES)
    mcp = create_server(settings)
    async with Client(mcp) as client:
        low = tool_json(await client.call_tool("tesla_set_charge_limit", {"percent": 49}))
        high = tool_json(await client.call_tool("tesla_set_charge_limit", {"percent": 101}))
        cold = tool_json(
            await client.call_tool("tesla_set_cabin_temperature", {"driver_celsius": 14.9})
        )
        hot = tool_json(
            await client.call_tool("tesla_set_cabin_temperature", {"driver_celsius": 28.1})
        )
        seat = tool_json(await client.call_tool("tesla_set_seat_heater", {"seat": 6, "level": 1}))
        level = tool_json(await client.call_tool("tesla_set_seat_heater", {"seat": 0, "level": 4}))
        ok_limit = tool_json(await client.call_tool("tesla_set_charge_limit", {"percent": 50}))
        ok_temp = tool_json(
            await client.call_tool("tesla_set_cabin_temperature", {"driver_celsius": 28})
        )
    for refused in (low, high, cold, hot, seat, level):
        assert refused["ok"] is False
        assert refused["sent"] is False
        assert refused["refusal_reason"] == "invalid_input"
        assert "Nothing was sent" in str(refused["error"])
    assert "49" in str(low["error"])
    assert "15" in str(cold["error"])
    assert _mapping(ok_limit["would_send"])["body"] == {"percent": 50}
    ok_body = _mapping(_mapping(ok_temp["would_send"])["body"])
    assert ok_body["driver_temp"] == 28.0
    assert ok_body["passenger_temp"] == 28.0
    with pytest.raises(CommandRefusal, match="whole number"):
        build_body("set_charge_limit", {"percent": 80.5})
    with pytest.raises(CommandRefusal, match="Seat"):
        build_body("seat_heater", {"seat": -1, "level": 1})
    with pytest.raises(CommandRefusal, match="true or false"):
        build_body("steering_wheel_heat", {"on": "yes"})


ALLOWED_COMMANDS = frozenset(
    {
        "climate_on",
        "climate_off",
        "set_cabin_temperature",
        "seat_heater",
        "steering_wheel_heat",
        "set_charge_limit",
        "charge_start",
        "charge_stop",
        "flash_lights",
        "honk_horn",
        "door_lock",
        "sentry_on",
    }
)


def test_excluded_commands_do_not_exist():
    names = set(COMMANDS)
    assert names == ALLOWED_COMMANDS
    assert "trigger_homelink" not in names
    endpoints = {spec.endpoint for spec in COMMANDS.values()}
    tool_names = {spec.tool_name for spec in COMMANDS.values()}
    for excluded in EXCLUDED_COMMANDS:
        assert excluded not in names
        assert excluded not in endpoints
        assert excluded not in tool_names
        assert f"tesla_{excluded}" not in tool_names
        with pytest.raises(CommandRefusal) as caught:
            build_body(excluded, {})
        assert caught.value.reason == "unknown_command"
    assert build_body("sentry_on", {"on": False}) == {"on": True}
    assert "door_unlock" not in endpoints
    assert "set_sentry_mode" in endpoints


async def test_command_server_does_not_register_excluded_tools(tmp_path):
    settings = command_settings(tmp_path)
    seed_token(settings, scopes=COMMAND_SCOPES)
    mcp = create_server(settings)
    async with Client(mcp) as client:
        tools = await client.list_tools()
    names = {tool.name for tool in tools}
    assert names == {spec.tool_name for spec in COMMANDS.values()}
    blob = " ".join(names)
    for needle in (
        "unlock",
        "trunk",
        "frunk",
        "window",
        "summon",
        "autopark",
        "valet",
        "speed_limit",
        "pin",
        "navigation",
        "software",
        "sentry_off",
        "remote_start",
    ):
        assert needle not in blob
    for tool in tools:
        description = tool.description or ""
        assert "acts on the car" in description.lower()
        assert _hint(tool, "readOnlyHint") is False
        assert _hint(tool, "destructiveHint") is True
    by_name = {tool.name: tool for tool in tools}
    assert _hint(by_name["tesla_honk_horn"], "idempotentHint") is False
    assert _hint(by_name["tesla_door_lock"], "idempotentHint") is True
    assert "does not unlock" in (by_name["tesla_door_lock"].description or "").lower()
    assert (
        "cannot turn sentry mode off" in (by_name["tesla_sentry_mode_on"].description or "").lower()
    )


async def test_missing_vehicle_cmds_scope_is_a_clear_error(tmp_path):
    settings = command_settings(tmp_path, commands_dry_run=False)
    seed_token(settings, scopes=SCOPES)
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        async with Client(mcp) as client:
            result = tool_json(await client.call_tool("tesla_door_lock", {}))
        assert router.calls == []
    assert result["ok"] is False
    assert result["sent"] is False
    assert result["refusal_reason"] == "missing_scope"
    assert "vehicle_cmds" in str(result["error"])
    assert "tesla-commands-mcp auth" in str(result["error"])
    assert "tesla-mcp auth" in str(result["error"])
    assert _audit_records(settings)[0]["refusal_reason"] == "missing_scope"


async def test_live_send_uses_the_proxy_and_not_the_fleet_command_path(tmp_path):
    settings = command_settings(tmp_path, commands_dry_run=False)
    token = seed_token(settings, scopes=COMMAND_SCOPES)
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        stub = FleetAPIStub(router)
        stub.list_vehicles(vehicle_list(state="online"))
        _mock_command(router, "door_lock", {"response": {"result": True, "reason": ""}})
        async with Client(mcp) as client:
            result = tool_json(await client.call_tool("tesla_door_lock", {}))
        command_calls = [call for call in router.calls if "/command/" in str(call.request.url)]
    assert result["ok"] is True
    assert result["sent"] is True
    assert result["dry_run"] is False
    assert result["vin_masked"] == mask_vin(VIN)
    sent_request = _mapping(result["would_send"])
    assert sent_request["path"] == f"/api/1/vehicles/{mask_vin(VIN)}/command/door_lock"
    assert sent_request["body"] == {}
    assert len(command_calls) == 1
    sent = command_calls[0].request
    assert str(sent.url) == f"{PROXY}/api/1/vehicles/{VIN}/command/door_lock"
    assert json.loads(sent.content) == {}
    assert sent.headers["authorization"].startswith("Bearer ")
    assert not stub.called_wake()
    text = _audit_text(settings)
    assert VIN not in text
    assert token.access_token not in text
    assert settings.client_secret not in text
    assert "Bearer" not in text
    saved = _audit_records(settings)[0]
    assert saved["result"] == "ok"
    assert saved["live"] is True
    assert saved["dry_run"] is False
    assert saved["vin_masked"] == mask_vin(VIN)
    assert saved["parameters"] == {}


async def test_tesla_error_text_is_returned_plainly(tmp_path):
    settings = command_settings(tmp_path, commands_dry_run=False)
    token = seed_token(settings, scopes=COMMAND_SCOPES)
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        FleetAPIStub(router).list_vehicles(vehicle_list(state="online"))
        _mock_command(
            router,
            "flash_lights",
            {"message": "vehicle command protocol required"},
            status=403,
        )
        async with Client(mcp) as client:
            unsigned = tool_json(await client.call_tool("tesla_flash_lights", {}))
    assert unsigned["ok"] is False
    assert unsigned["sent"] is True
    assert "vehicle command protocol required" in str(unsigned["error"])
    assert _audit_records(settings)[-1]["tesla_reason"] == "vehicle command protocol required"
    assert token.access_token not in _audit_text(settings)

    reasoned = command_settings(tmp_path / "reason", commands_dry_run=False)
    seed_token(reasoned, scopes=COMMAND_SCOPES)
    mcp = create_server(reasoned)
    with respx.mock(assert_all_called=False) as router:
        FleetAPIStub(router).list_vehicles(vehicle_list(state="online"))
        _mock_command(
            router,
            "door_lock",
            {"response": {"result": False, "reason": "unsigned_cmds_hardlocked"}},
        )
        async with Client(mcp) as client:
            rejected = tool_json(await client.call_tool("tesla_door_lock", {}))
    assert rejected["ok"] is False
    assert "unsigned_cmds_hardlocked" in str(rejected["error"])


async def test_per_command_and_overall_caps(tmp_path):
    settings = command_settings(
        tmp_path,
        commands_dry_run=False,
        commands_daily_cap=10,
        commands_per_command_daily_cap=1,
        commands_allowlist=("door_lock", "climate_on"),
    )
    seed_token(settings, scopes=COMMAND_SCOPES)
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        FleetAPIStub(router).list_vehicles(vehicle_list(state="online"))
        _mock_command(router, "door_lock", {"response": {"result": True, "reason": ""}})
        _mock_command(
            router, "auto_conditioning_start", {"response": {"result": True, "reason": ""}}
        )
        async with Client(mcp) as client:
            first = tool_json(await client.call_tool("tesla_door_lock", {}))
            second = tool_json(await client.call_tool("tesla_door_lock", {}))
            other = tool_json(await client.call_tool("tesla_climate_on", {}))
        door_calls = [
            call for call in router.calls if str(call.request.url).endswith("/command/door_lock")
        ]
    assert first["ok"] is True
    assert second["refusal_reason"] == "command_cap"
    assert second["sent"] is False
    assert other["ok"] is True
    assert len(door_calls) == 1

    overall = command_settings(
        tmp_path / "overall",
        commands_dry_run=False,
        commands_daily_cap=1,
        commands_per_command_daily_cap=3,
        commands_allowlist=("door_lock", "climate_on"),
    )
    seed_token(overall, scopes=COMMAND_SCOPES)
    mcp = create_server(overall)
    with respx.mock(assert_all_called=False) as router:
        FleetAPIStub(router).list_vehicles(vehicle_list(state="online"))
        _mock_command(router, "door_lock", {"response": {"result": True, "reason": ""}})
        _mock_command(
            router, "auto_conditioning_start", {"response": {"result": True, "reason": ""}}
        )
        async with Client(mcp) as client:
            sent = tool_json(await client.call_tool("tesla_door_lock", {}))
            blocked = tool_json(await client.call_tool("tesla_climate_on", {}))
        climate_calls = [
            call
            for call in router.calls
            if "/command/auto_conditioning_start" in str(call.request.url)
        ]
    assert sent["ok"] is True
    assert blocked["refusal_reason"] == "daily_cap"
    assert climate_calls == []


def test_caps_reset_after_local_midnight(tmp_path):
    settings = command_settings(
        tmp_path,
        commands_daily_cap=1,
        commands_per_command_daily_cap=1,
    )
    budget = CommandBudget(settings)
    noon = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    assert budget.reserve("door_lock", mask_vin(VIN), now=noon).allowed
    again = budget.peek("door_lock", now=noon)
    assert again.allowed is False
    assert again.reason == "command_cap"
    next_day = datetime(2026, 10, 23, 0, 5, tzinfo=UTC)
    assert budget.peek("door_lock", now=next_day).allowed


async def test_unbilled_proxy_failure_does_not_consume_the_cap(tmp_path):
    settings = command_settings(
        tmp_path,
        commands_dry_run=False,
        commands_per_command_daily_cap=1,
        commands_allowlist=("door_lock",),
    )
    seed_token(settings, scopes=COMMAND_SCOPES)
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        FleetAPIStub(router).list_vehicles(vehicle_list(state="online"))
        router.post(url__regex=r".*/command/door_lock$").mock(
            side_effect=httpx.ConnectError("down")
        )
        async with Client(mcp) as client:
            failed = tool_json(await client.call_tool("tesla_door_lock", {}))
    assert failed["ok"] is False
    assert failed["sent"] is False
    assert "proxy" in str(failed["error"]).lower()
    assert CommandBudget(settings).peek("door_lock").allowed


async def test_asleep_car_uses_the_shared_wake_cap(tmp_path):
    settings = command_settings(tmp_path, commands_dry_run=False, commands_allowlist=("door_lock",))
    seed_token(settings, scopes=COMMAND_SCOPES)
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        stub = FleetAPIStub(router)
        stub.list_vehicles(vehicle_list(state="asleep"))
        stub.wake(state="online")
        _mock_command(router, "door_lock", {"response": {"result": True, "reason": ""}})
        async with Client(mcp) as client:
            result = tool_json(await client.call_tool("tesla_door_lock", {}))
        urls = [str(call.request.url) for call in router.calls]
    assert result["ok"] is True
    assert result["woke"] is True
    assert any(url.endswith("/wake_up") for url in urls)
    wake_at = next(index for index, url in enumerate(urls) if url.endswith("/wake_up"))
    command_at = next(index for index, url in enumerate(urls) if "/command/door_lock" in url)
    assert wake_at < command_at
    runtime = build_runtime(settings)
    assert runtime.wakes.summary()["used_today"] == 1

    blocked = command_settings(
        tmp_path / "no-wake",
        commands_dry_run=False,
        commands_allowlist=("door_lock",),
        wake_daily_cap=0,
        commands_daily_cap=1,
        commands_per_command_daily_cap=1,
    )
    seed_token(blocked, scopes=COMMAND_SCOPES)
    mcp = create_server(blocked)
    with respx.mock(assert_all_called=False) as router:
        FleetAPIStub(router).list_vehicles(vehicle_list(state="asleep"))
        _mock_command(router, "door_lock", {"response": {"result": True, "reason": ""}})
        async with Client(mcp) as client:
            refused = tool_json(await client.call_tool("tesla_door_lock", {}))
        command_calls = [call for call in router.calls if "/command/" in str(call.request.url)]
    assert refused["refusal_reason"] == "wake_budget"
    assert refused["sent"] is False
    assert command_calls == []
    assert not any(str(call.request.url).endswith("/wake_up") for call in router.calls)
    assert CommandBudget(blocked).peek("door_lock").allowed

    awake = command_settings(
        tmp_path / "awake",
        commands_dry_run=False,
        commands_allowlist=("door_lock",),
        wake_daily_cap=0,
    )
    seed_token(awake, scopes=COMMAND_SCOPES)
    mcp = create_server(awake)
    with respx.mock(assert_all_called=False) as router:
        stub = FleetAPIStub(router)
        stub.list_vehicles(vehicle_list(state="online"))
        _mock_command(router, "door_lock", {"response": {"result": True, "reason": ""}})
        async with Client(mcp) as client:
            sent = tool_json(await client.call_tool("tesla_door_lock", {}))
        woke = any(str(call.request.url).endswith("/wake_up") for call in router.calls)
    assert sent["ok"] is True
    assert woke is False


async def test_live_mode_without_a_proxy_refuses_before_any_call(tmp_path):
    settings = command_settings(tmp_path, commands_dry_run=False, command_proxy_url=None)
    seed_token(settings, scopes=COMMAND_SCOPES)
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        async with Client(mcp) as client:
            result = tool_json(await client.call_tool("tesla_door_lock", {}))
        assert router.calls == []
    assert result["refusal_reason"] == "proxy_not_configured"
    assert "tesla-http-proxy" in str(result["error"])


async def test_audit_log_is_append_only_and_hides_secrets(tmp_path):
    disabled = make_settings(tmp_path)
    mcp = create_server(disabled)
    async with Client(mcp) as client:
        await client.call_tool("tesla_honk_horn", {})
    enabled = command_settings(tmp_path, commands_allowlist=("door_lock",))
    token = seed_token(enabled, scopes=COMMAND_SCOPES)
    mcp = create_server(enabled)
    async with Client(mcp) as client:
        await client.call_tool("tesla_door_lock", {"vin": VIN})
    text = _audit_text(enabled)
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    assert records[0]["command"] == "honk_horn"
    assert records[0]["refusal_reason"] == "commands_disabled"
    assert records[1]["command"] == "door_lock"
    assert records[1]["result"] == "dry_run"
    assert records[1]["parameters"] == {}
    assert VIN not in text
    assert token.access_token not in text
    mode = audit_path(enabled).stat().st_mode & 0o777
    assert mode == 0o600


async def test_read_only_server_tool_list_stays_the_same(tmp_path):
    for extra in (
        {},
        {
            "enable_commands": True,
            "commands_dry_run": False,
            "commands_allowlist": ("door_lock", "climate_on"),
            "command_proxy_url": PROXY,
        },
    ):
        settings = make_settings(tmp_path / str(len(extra)), **extra)
        seed_token(
            settings,
            scopes=[*COMMAND_SCOPES, "vehicle_location", "vehicle_charging_cmds"],
        )
        mcp = create_readonly_server(settings)
        async with Client(mcp) as client:
            names = {tool.name for tool in await client.list_tools()}
        assert names == READ_ONLY_TOOLS
        assert "tesla_door_lock" not in names


def test_read_only_auth_never_requests_vehicle_cmds(tmp_path):
    settings = make_settings(tmp_path, enable_commands=True)
    readonly = build_authorize_url(settings, "state-value")
    assert "vehicle_cmds" not in readonly
    assert "user_data" not in readonly
    opted_in = build_authorize_url(settings, "state-value", commands=True)
    assert "vehicle_cmds" in opted_in
    assert "user_data" not in opted_in
    assert "vehicle_device_data" in opted_in
    runtime = build_runtime(settings)
    assert "vehicle_cmds" not in runtime.requested()


async def test_read_only_fleet_client_rejects_command_paths(tmp_path):
    settings = make_settings(tmp_path, enable_commands=True, commands_dry_run=False)
    seed_token(settings, scopes=COMMAND_SCOPES)
    runtime = build_runtime(settings)
    async with FleetClient(runtime, allow_wake=True) as fleet:
        with pytest.raises(TeslaError, match="read-only"):
            await fleet._request(
                "POST",
                f"/api/1/vehicles/{VIN}/command/door_lock",
                vin=VIN,
            )


def test_command_env_defaults_and_bounds(monkeypatch, tmp_path):
    from tesla_commands.config import apply_command_environment

    _clear_tesla_env(monkeypatch)
    monkeypatch.setenv("TESLA_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TESLA_COMMANDS_ALLOWLIST", " Door_Lock, climate-on , door_lock ")
    monkeypatch.setenv("TESLA_COMMAND_PROXY_URL", "https://127.0.0.1:4443/")
    monkeypatch.setenv("TESLA_ENABLE_COMMANDS", "true")
    monkeypatch.setenv("TESLA_COMMANDS_DRY_RUN", "false")
    ignored = Settings.load()
    assert ignored.enable_commands is False
    assert ignored.commands_dry_run is True
    assert ignored.command_proxy_url is None
    settings = apply_command_environment(ignored)
    assert settings.enable_commands is True
    assert settings.commands_dry_run is False
    assert settings.commands_allowlist == ("door_lock", "climate_on")
    assert settings.command_proxy_url == "https://127.0.0.1:4443"
    assert settings.command_config_error is None
    monkeypatch.setenv("TESLA_COMMANDS_DAILY_CAP", "100")
    over_cap = apply_command_environment(Settings.load())
    assert over_cap.command_config_error is not None
    assert "TESLA_COMMANDS_DAILY_CAP" in over_cap.command_config_error
    assert over_cap.enable_commands is False
    assert over_cap.commands_dry_run is True
    monkeypatch.setenv("TESLA_COMMANDS_DAILY_CAP", "10")
    monkeypatch.setenv("TESLA_COMMAND_PROXY_URL", "https://commands.example")
    remote = apply_command_environment(Settings.load())
    assert remote.command_config_error is not None
    assert "this computer" in remote.command_config_error
    assert remote.command_proxy_url is None
    # The read-only loader still does not raise or keep the bad proxy.
    assert Settings.load().command_proxy_url is None
    monkeypatch.setenv("TESLA_COMMAND_PROXY_URL", "https://127.0.0.1:4443")
    monkeypatch.setenv("TESLA_COMMANDS_DAILY_CAP", "10")
    monkeypatch.setenv("TESLA_ENABLE_COMMANDS", "maybe")
    bad_flag = apply_command_environment(Settings.load())
    assert bad_flag.enable_commands is False
    assert bad_flag.command_config_error is not None
    assert "TESLA_ENABLE_COMMANDS" in bad_flag.command_config_error


def test_commands_help_does_not_start_the_server(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["tesla-commands-mcp", "--help"])
    from tesla_commands.cli import main

    main()
    captured = capsys.readouterr()
    assert "TESLA_ENABLE_COMMANDS" in captured.out
    assert "off" in captured.out.lower() or "refused" in captured.out.lower()


def test_commands_package_does_not_read_a_private_key():
    root = os.path.join(os.path.dirname(__file__), "..", "src", "tesla_commands")
    text = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(".py"):
                with open(os.path.join(dirpath, name), encoding="utf-8") as handle:
                    text.append(handle.read())
    combined = "\n".join(text)
    assert "BEGIN PRIVATE KEY" not in combined
    assert "BEGIN EC PRIVATE KEY" not in combined
    assert "private-key.pem" not in combined
    assert "LoadPrivateKey" not in combined


def test_dry_run_stays_on_unless_explicitly_false():
    from tesla_commands.config import commands_dry_run_enabled

    for value in (None, "", " ", "\t", " \t ", "maybe", "true", "yes", "on", "1", "live"):
        assert commands_dry_run_enabled(value) is True
    for value in ("false", "FALSE", " false ", "0", "no", "off"):
        assert commands_dry_run_enabled(value) is False


async def test_blank_dry_run_does_not_send(monkeypatch, tmp_path):
    from tesla_commands.config import apply_command_environment
    from tesla_mcp.crypto import generate_key

    _clear_tesla_env(monkeypatch)
    monkeypatch.setenv("TESLA_MCP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TESLA_TOKEN_KEY", generate_key())
    monkeypatch.setenv("TESLA_FLEET_API_BASE", "https://fleet.test")
    monkeypatch.setenv("TESLA_TOKEN_URL", "https://auth.test/token")
    monkeypatch.setenv("TESLA_ENABLE_COMMANDS", "true")
    monkeypatch.setenv("TESLA_COMMANDS_DRY_RUN", " \t ")
    monkeypatch.setenv("TESLA_COMMANDS_ALLOWLIST", "door_lock")
    monkeypatch.setenv("TESLA_COMMAND_PROXY_URL", PROXY)
    settings = apply_command_environment(Settings.load())
    assert settings.commands_dry_run is True
    assert settings.command_config_error is None
    seed_token(settings, scopes=COMMAND_SCOPES)
    mcp = create_server()
    with respx.mock(assert_all_called=False) as router:
        async with Client(mcp) as client:
            result = tool_json(await client.call_tool("tesla_door_lock", {}))
        assert router.calls == []
    assert result["ok"] is True
    assert result["sent"] is False
    assert result["dry_run"] is True


async def test_readonly_server_ignores_invalid_command_env(monkeypatch, tmp_path):
    _clear_tesla_env(monkeypatch)
    monkeypatch.setenv("TESLA_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TESLA_COMMANDS_DAILY_CAP", "nope")
    monkeypatch.setenv("TESLA_COMMAND_PROXY_URL", "https://user:s3cret@commands.example/hook")
    monkeypatch.setenv("TESLA_COMMANDS_DRY_RUN", "")
    monkeypatch.setenv("TESLA_ENABLE_COMMANDS", "maybe")
    loaded = Settings.load()
    assert loaded.enable_commands is False
    assert loaded.commands_dry_run is True
    assert loaded.command_proxy_url is None
    mcp = create_readonly_server()
    async with Client(mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert "tesla_list_vehicles" in names
    assert "tesla_door_lock" not in names


async def test_commands_server_refuses_invalid_config(monkeypatch, tmp_path):
    from tesla_commands.config import apply_command_environment

    _clear_tesla_env(monkeypatch)
    monkeypatch.setenv("TESLA_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TESLA_ENABLE_COMMANDS", "true")
    monkeypatch.setenv("TESLA_COMMANDS_DRY_RUN", "false")
    monkeypatch.setenv("TESLA_COMMANDS_DAILY_CAP", "nope")
    monkeypatch.setenv("TESLA_COMMANDS_ALLOWLIST", "door_lock")
    loaded = apply_command_environment(Settings.load())
    mcp = create_server()
    with respx.mock(assert_all_called=False) as router:
        async with Client(mcp) as client:
            result = tool_json(await client.call_tool("tesla_door_lock", {}))
        assert router.calls == []
    assert result["ok"] is False
    assert result["sent"] is False
    assert result["dry_run"] is True
    assert result["refusal_reason"] == "invalid_config"
    assert "TESLA_COMMANDS_DAILY_CAP" in str(result["error"])
    assert "Nothing was sent" in str(result["error"])
    assert _audit_records(loaded)[-1]["refusal_reason"] == "invalid_config"


def test_commands_auth_refuses_bad_config_without_traceback(monkeypatch, capsys, tmp_path):
    from tesla_mcp.crypto import generate_key
    from tesla_mcp.scripts.setup_auth import main as auth_main

    _clear_tesla_env(monkeypatch)
    monkeypatch.setenv("TESLA_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TESLA_CLIENT_ID", "client-id")
    monkeypatch.setenv("TESLA_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("TESLA_TOKEN_KEY", generate_key())
    monkeypatch.setenv("TESLA_ENABLE_COMMANDS", "true")
    monkeypatch.setenv("TESLA_COMMAND_PROXY_URL", "https://user:s3cret@127.0.0.1:4443")
    with pytest.raises(SystemExit) as caught:
        auth_main([], include_commands=True)
    assert caught.value.code == 1
    err = capsys.readouterr().err
    assert "TESLA_COMMAND_PROXY_URL" in err
    assert "s3cret" not in err
    assert "Traceback" not in err


async def test_tool_layer_rejects_bools_strings_and_decimals(tmp_path):
    settings = command_settings(tmp_path)
    seed_token(settings, scopes=COMMAND_SCOPES)
    mcp = create_server(settings)
    async with Client(mcp) as client:
        seat_bool = tool_json(
            await client.call_tool("tesla_set_seat_heater", {"seat": True, "level": 1})
        )
        level_string = tool_json(
            await client.call_tool("tesla_set_seat_heater", {"seat": 1, "level": "3"})
        )
        percent_string = tool_json(
            await client.call_tool("tesla_set_charge_limit", {"percent": "80"})
        )
        percent_float = tool_json(
            await client.call_tool("tesla_set_charge_limit", {"percent": 50.0})
        )
        cold = tool_json(
            await client.call_tool("tesla_set_cabin_temperature", {"driver_celsius": 14.96})
        )
        hot = tool_json(
            await client.call_tool("tesla_set_cabin_temperature", {"driver_celsius": 28.04})
        )
        temp_string = tool_json(
            await client.call_tool("tesla_set_cabin_temperature", {"driver_celsius": "21"})
        )
        wheel = tool_json(await client.call_tool("tesla_set_steering_wheel_heat", {"on": 1}))
        still_ok = tool_json(await client.call_tool("tesla_set_charge_limit", {"percent": 80}))
    for refused in (
        seat_bool,
        level_string,
        percent_string,
        percent_float,
        cold,
        hot,
        temp_string,
        wheel,
    ):
        assert refused["ok"] is False
        assert refused["sent"] is False
        assert refused["refusal_reason"] == "invalid_input"
        assert "Nothing was sent" in str(refused["error"])
        assert "would_send" not in refused
    assert "14.96" in str(cold["error"])
    assert "28.04" in str(hot["error"])
    assert "True" in str(seat_bool["error"])
    assert _mapping(still_ok["would_send"])["body"] == {"percent": 80}


async def test_command_cap_refusal_does_not_wake(tmp_path, monkeypatch):
    from tesla_commands.budget import CommandDecision

    settings = command_settings(
        tmp_path,
        commands_dry_run=False,
        commands_allowlist=("door_lock",),
    )
    seed_token(settings, scopes=COMMAND_SCOPES)

    def deny(self, command: str, vin_masked: str, *, now: object = None) -> CommandDecision:
        return CommandDecision(False, "daily_cap", 1, 0, 0, 3, None)

    monkeypatch.setattr(CommandBudget, "reserve", deny)
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        stub = FleetAPIStub(router)
        stub.list_vehicles(vehicle_list(state="asleep"))
        stub.wake(state="online")
        _mock_command(router, "door_lock", {"response": {"result": True, "reason": ""}})
        async with Client(mcp) as client:
            result = tool_json(await client.call_tool("tesla_door_lock", {}))
        urls = [str(call.request.url) for call in router.calls]
    assert result["refusal_reason"] == "daily_cap"
    assert result["sent"] is False
    assert result.get("woke") is not True
    assert urls == []


async def test_invalid_timezone_and_corrupt_history_refuse(tmp_path):
    timezone = command_settings(tmp_path / "zone", timezone="Not/AZone")
    seed_token(timezone, scopes=COMMAND_SCOPES)
    mcp = create_server(timezone)
    with respx.mock(assert_all_called=False) as router:
        async with Client(mcp) as client:
            zone_result = tool_json(await client.call_tool("tesla_door_lock", {}))
        assert router.calls == []
    assert zone_result["refusal_reason"] == "invalid_timezone"
    assert zone_result["sent"] is False
    assert "America/New_York" in str(zone_result["error"])
    assert "Nothing was sent" in str(zone_result["error"])
    assert _audit_records(timezone)[-1]["refusal_reason"] == "invalid_timezone"

    broken = command_settings(tmp_path / "broken")
    broken.history_path.parent.mkdir(parents=True, exist_ok=True)
    broken.history_path.write_bytes(b"this is not a sqlite database")
    seed_token(broken, scopes=COMMAND_SCOPES)
    mcp = create_server(broken)
    with respx.mock(assert_all_called=False) as router:
        async with Client(mcp) as client:
            history_result = tool_json(await client.call_tool("tesla_door_lock", {}))
        assert router.calls == []
    assert history_result["refusal_reason"] == "history_unreadable"
    assert history_result["sent"] is False
    assert "Nothing was sent" in str(history_result["error"])
    assert "Traceback" not in str(history_result["error"])
    assert _audit_records(broken)[-1]["refusal_reason"] == "history_unreadable"

    local = command_settings(tmp_path / "local", timezone="America/New_York")
    seed_token(local, scopes=COMMAND_SCOPES)
    mcp = create_server(local)
    async with Client(mcp) as client:
        ok = tool_json(await client.call_tool("tesla_door_lock", {}))
    assert ok["ok"] is True
    assert ok["dry_run"] is True


async def test_not_ready_writes_an_audit_line(monkeypatch, tmp_path):
    from tesla_commands.execute import perform

    _clear_tesla_env(monkeypatch)
    monkeypatch.setenv("TESLA_MCP_DATA_DIR", str(tmp_path))
    selector = f"garage {VIN} bay"
    result = await perform(None, "door_lock", {"label": selector}, vin_selector=selector)
    assert result["refusal_reason"] == "not_ready"
    assert result["sent"] is False
    assert result["ok"] is False
    blob = json.dumps(result)
    assert VIN not in blob
    assert mask_vin(VIN) in str(result["vehicle"])
    assert mask_vin(VIN) in str(result["parameters"])
    text = _audit_text(Settings.load())
    assert "not_ready" in text
    assert VIN not in text
    assert mask_vin(VIN) in text


async def test_not_ready_scrubs_opaque_saved_tokens(monkeypatch, tmp_path):
    from tesla_commands.execute import perform
    from tesla_mcp.crypto import generate_key
    from tesla_mcp.token_store import TokenRecord, TokenStore

    _clear_tesla_env(monkeypatch)
    monkeypatch.setenv("TESLA_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TESLA_TOKEN_KEY", generate_key())
    settings = Settings.load()
    access = "opaque-access-token-value"
    refresh = "opaque-refresh-token-value"
    TokenStore(settings).save(
        TokenRecord(
            access_token=access,
            refresh_token=refresh,
            expires_at=9_999_999_999,
            scopes=["vehicle_device_data"],
            region="na",
        )
    )
    selector = f"garage {access} then {refresh}"
    result = await perform(None, "door_lock", {"label": f"note {refresh}"}, vin_selector=selector)
    blob = json.dumps(result)
    assert result["refusal_reason"] == "not_ready"
    assert access not in blob
    assert refresh not in blob
    assert result["vehicle"] == "garage [redacted] then [redacted]"
    assert "[redacted]" in str(result["parameters"])
    text = _audit_text(settings)
    assert "not_ready" in text
    assert access not in text
    assert refresh not in text
    saved = _audit_records(settings)[-1]
    assert saved["vehicle"] == "garage [redacted] then [redacted]"


def test_audit_log_masks_a_vin_inside_a_longer_selector(tmp_path):
    from tesla_commands.audit import AuditLog

    settings = make_settings(tmp_path)
    secret = "opaque-access-token-value"
    jwt = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJ4In0.sig"
    AuditLog(settings).append(
        {
            "vehicle": f"garage {VIN} bay",
            "error": f"denied {secret} {jwt} vin {VIN}",
        },
        secrets=(secret,),
    )
    text = _audit_text(settings)
    assert VIN not in text
    assert secret not in text
    assert "eyJ" not in text
    assert mask_vin(VIN) in text


def test_proxy_error_text_scrubs_tokens_and_vins():
    from tesla_commands.proxy import interpret_response

    class Response:
        status_code = 403

        def json(self) -> dict[str, object]:
            return {
                "message": f"vehicle {VIN} rejected Bearer leaked-bearer-token",
                "error": "eyJhbGciOiJub25lIn0.eyJhIjoxfQ.sig",
            }

    result = interpret_response(Response())
    combined = f"{result.message} {result.tesla_reason}"
    assert VIN not in combined
    assert "eyJ" not in combined
    assert "leaked-bearer-token" not in combined
    assert mask_vin(VIN) in combined


async def test_tool_reply_scrubs_tesla_reason_token_and_vin(tmp_path):
    settings = command_settings(
        tmp_path,
        commands_dry_run=False,
        commands_allowlist=("door_lock",),
    )
    token = seed_token(settings, scopes=COMMAND_SCOPES, refresh="refresh-token-test-value")
    other_jwt = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJvdGhlciJ9.sig"
    reason = (
        f"denied {token.access_token} refresh {token.refresh_token} "
        f"Bearer leaked-bearer-token vehicle {VIN} also {other_jwt}"
    )
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        FleetAPIStub(router).list_vehicles(vehicle_list(state="online"))
        _mock_command(router, "door_lock", {"response": {"result": False, "reason": reason}})
        async with Client(mcp) as client:
            result = tool_json(await client.call_tool("tesla_door_lock", {"vin": VIN}))
    blob = json.dumps(result)
    assert token.access_token not in blob
    assert token.refresh_token not in blob
    assert VIN not in blob
    assert other_jwt not in blob
    assert "eyJ" not in blob
    assert "leaked-bearer-token" not in blob
    assert mask_vin(VIN) in str(result["tesla_reason"])
    assert mask_vin(VIN) in str(result["error"])
    assert result["vehicle"] == mask_vin(VIN)
    text = _audit_text(settings)
    assert token.access_token not in text
    assert token.refresh_token not in text
    assert VIN not in text
    assert "eyJ" not in text
    assert "leaked-bearer-token" not in text
    assert mask_vin(VIN) in text

    dry = command_settings(tmp_path / "selector", commands_allowlist=("door_lock",))
    seed_token(dry, scopes=COMMAND_SCOPES)
    selector = f"garage {VIN} bay"
    mcp = create_server(dry)
    async with Client(mcp) as client:
        named = tool_json(await client.call_tool("tesla_door_lock", {"vin": selector}))
    assert named["vehicle"] == f"garage {mask_vin(VIN)} bay"
    assert VIN not in json.dumps(named)
    dry_text = _audit_text(dry)
    assert VIN not in dry_text
    assert mask_vin(VIN) in dry_text
