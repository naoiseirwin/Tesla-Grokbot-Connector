"""MCP tool surface: read-only annotations, scope gating, cached status, usage."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
import respx
from fastmcp import Client

from tesla_mcp.history import HistoryStore, Snapshot
from tesla_mcp.server import create_server
from tesla_mcp.time_utils import utc_now
from tests.fixtures.vehicles import vehicle_data, vehicle_list
from tests.helpers import VIN, make_settings, seed_token, tool_json
from tests.stubs.fleet_api_stub import FleetAPIStub

SCOPES = ["openid", "offline_access", "vehicle_device_data"]


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


async def test_default_tools_hide_location_and_charging_history(tmp_path):
    settings = make_settings(tmp_path)
    seed_token(settings, scopes=[*SCOPES, "vehicle_location", "vehicle_charging_cmds"])
    mcp = create_server(settings)
    async with Client(mcp) as client:
        tools = await client.list_tools()
    names = {tool.name for tool in tools}
    assert "tesla_get_location" not in names
    assert "tesla_get_charging_history" not in names
    assert names >= {
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
    by_name = {tool.name: tool for tool in tools}
    for tool in tools:
        assert _hint(tool, "readOnlyHint") is True
    assert _hint(by_name["tesla_wake_and_refresh"], "idempotentHint") is False


async def test_granted_scope_without_the_flag_still_hides_the_tool(tmp_path):
    settings = make_settings(tmp_path, enable_location=False)
    seed_token(settings, scopes=[*SCOPES, "vehicle_location"])
    mcp = create_server(settings)
    async with Client(mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert "tesla_get_location" not in names


async def test_missing_scope_hides_an_opt_in_tool(tmp_path):
    settings = make_settings(tmp_path, enable_location=True, enable_charging_history=True)
    seed_token(settings, scopes=SCOPES)
    mcp = create_server(settings)
    async with Client(mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert "tesla_get_location" not in names
    assert "tesla_get_charging_history" not in names


async def test_status_uses_cache_when_asleep_and_live_data_when_online(tmp_path):
    settings = make_settings(tmp_path)
    seed_token(settings)
    history = HistoryStore(settings.history_path)
    history.add_snapshot(
        Snapshot(
            vin=VIN,
            recorded_at=utc_now() - timedelta(hours=1),
            state="online",
            live=True,
            source="poll",
            battery_level=40,
            battery_range_mi=120,
            plugged_in=False,
            locked=True,
        )
    )
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        stub = FleetAPIStub(router)
        stub.list_vehicles(vehicle_list(state="asleep"))
        async with Client(mcp) as client:
            cached = tool_json(await client.call_tool("tesla_get_status", {}))
        assert not any("vehicle_data" in str(call.request.url) for call in router.calls)
        stub.list_vehicles(vehicle_list(state="online"))
        stub.vehicle_data()
        async with Client(mcp) as client:
            live = tool_json(await client.call_tool("tesla_get_status", {}))
    assert cached["freshness"] == "cached"
    assert cached["state"] == "asleep"
    assert cached["battery_level_percent"] == 40
    assert "latitude" not in cached
    assert live["freshness"] == "live"
    assert live["battery_level_percent"] == 72
    assert live["locked"] is True
    assert "latitude" not in live
    assert live["vin_masked"] == "5YJ…0001"


async def test_location_is_coarse_by_default_and_logs_omit_coordinates(tmp_path, caplog):
    settings = make_settings(tmp_path, enable_location=True)
    seed_token(settings, scopes=[*SCOPES, "vehicle_location"])
    mcp = create_server(settings)
    caplog.set_level("INFO", logger="tesla_mcp")
    with respx.mock(assert_all_called=False) as router:
        stub = FleetAPIStub(router)
        stub.list_vehicles()
        stub.vehicle_data(vehicle_data(latitude=53.3498, longitude=-6.2603))
        async with Client(mcp) as client:
            coarse = tool_json(await client.call_tool("tesla_get_location", {}))
            precise = tool_json(
                await client.call_tool("tesla_get_location", {"precision": "precise"})
            )
    assert coarse["precision"] == "coarse"
    assert coarse["latitude"] == 53.35
    assert precise["latitude"] == pytest.approx(53.3498)
    assert "53.3498" not in caplog.text
    stored = HistoryStore(settings.history_path).latest(VIN)
    assert stored is not None
    assert stored.latitude == 53.35


async def test_charging_summary_does_not_call_tesla_when_disabled(tmp_path):
    settings = make_settings(tmp_path, enable_charging_history=False)
    seed_token(settings)
    HistoryStore(settings.history_path).add_snapshot(
        Snapshot(
            vin=VIN,
            recorded_at=utc_now(),
            state="asleep",
            live=False,
            source="poll",
            battery_level=50,
        )
    )
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        async with Client(mcp) as client:
            payload = tool_json(
                await client.call_tool("tesla_report", {"type": "charging_summary"})
            )
        assert router.calls == []
    assert payload["ok"] is False
    assert "vehicle_charging_cmds" in str(payload["error"])


async def test_list_vehicles_masks_the_vin(tmp_path):
    settings = make_settings(tmp_path)
    seed_token(settings)
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        FleetAPIStub(router).list_vehicles()
        async with Client(mcp) as client:
            payload = tool_json(await client.call_tool("tesla_list_vehicles", {}))
    assert payload["vehicles"][0]["vin_masked"] == "5YJ…0001"  # type: ignore[index]
    assert VIN not in json.dumps(payload)


async def test_usage_counts_recorded_calls(tmp_path):
    settings = make_settings(tmp_path)
    seed_token(settings)
    history = HistoryStore(settings.history_path)
    history.record_usage(
        category="data",
        endpoint="/api/1/vehicles",
        http_status=200,
        vin_masked="",
        estimated_usd=0.002,
        recorded_at=utc_now(),
    )
    history.record_usage(
        category="wake",
        endpoint="/api/1/vehicles/5YJ…0001/wake_up",
        http_status=200,
        vin_masked="5YJ…0001",
        estimated_usd=0.02,
        recorded_at=utc_now(),
    )
    mcp = create_server(settings)
    async with Client(mcp) as client:
        payload = tool_json(await client.call_tool("tesla_usage", {}))
    calls = payload["calls"]
    assert calls["data"] == 1  # type: ignore[index]
    assert calls["wake"] == 1  # type: ignore[index]
    assert calls["command"] == 0  # type: ignore[index]
    assert payload["estimated_usd"] == "$0.0220"
    assert payload["wake_budget"]["daily_cap"] == 3  # type: ignore[index]
