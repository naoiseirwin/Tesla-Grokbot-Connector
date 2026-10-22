"""The poller never wakes. The wake tool spends a local budget first."""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import respx
from fastmcp import Client

from tesla_mcp.history import Snapshot
from tesla_mcp.poller import poll_once
from tesla_mcp.runtime import build_runtime
from tesla_mcp.server import create_server
from tesla_mcp.time_utils import utc_now
from tests.fixtures.vehicles import vehicle_list
from tests.helpers import VIN, make_settings, seed_token, tool_json
from tests.stubs.fleet_api_stub import FleetAPIStub


async def test_poll_asleep_records_state_and_does_not_wake(tmp_path):
    settings = make_settings(tmp_path)
    seed_token(settings)
    runtime = build_runtime(settings)
    with respx.mock(assert_all_called=False) as router:
        stub = FleetAPIStub(router)
        stub.list_vehicles(vehicle_list(state="asleep"))
        summary = await poll_once(runtime)
        assert stub.called_wake() is False
        assert not any("vehicle_data" in str(call.request.url) for call in router.calls)
    assert summary["woke"] is False
    latest = runtime.history.latest(VIN)
    assert latest is not None
    assert latest.state == "asleep"
    assert latest.live is False
    assert runtime.wakes.summary()["used_today"] == 0


async def test_poll_online_reads_data_without_waking(tmp_path):
    settings = make_settings(tmp_path, enable_location=True)
    seed_token(
        settings, scopes=["openid", "offline_access", "vehicle_device_data", "vehicle_location"]
    )
    runtime = build_runtime(settings)
    with respx.mock(assert_all_called=False) as router:
        stub = FleetAPIStub(router)
        stub.list_vehicles(vehicle_list(state="online"))
        stub.vehicle_data()
        await poll_once(runtime)
        assert stub.called_wake() is False
        data_call = next(call for call in router.calls if "vehicle_data" in str(call.request.url))
        assert "location_data" in str(data_call.request.url)
        assert "charge_state" in str(data_call.request.url)
    latest = runtime.history.latest(VIN)
    assert latest is not None
    assert latest.live is True
    assert latest.battery_level == 72
    assert latest.latitude == 53.35


async def test_wake_requires_confirm_and_does_not_call_tesla(tmp_path):
    settings = make_settings(tmp_path)
    seed_token(settings)
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        async with Client(mcp) as client:
            result = await client.call_tool("tesla_wake_and_refresh", {"confirm": False})
        payload = tool_json(result)
        assert router.calls == []
    assert payload["ok"] is False
    assert payload["error_type"] == "confirmation_required"


async def test_wake_budget_blocks_the_fourth_wake_in_a_minute(tmp_path):
    settings = make_settings(
        tmp_path,
        wake_daily_cap=100,
        wake_min_interval_seconds=0,
        wake_per_minute_cap=99,
    )
    seed_token(settings)
    runtime = build_runtime(settings)
    now = utc_now()
    for offset in (30, 20, 10):
        runtime.history.record_wake(
            vin_masked="5YJ…0001",
            outcome="sent",
            recorded_at=now - timedelta(seconds=offset),
        )
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        FleetAPIStub(router).list_vehicles()
        wake = router.post(url__regex=r".*/wake_up").mock(return_value=httpx.Response(200, json={}))
        async with Client(mcp) as client:
            result = await client.call_tool("tesla_wake_and_refresh", {"confirm": True})
    payload = tool_json(result)
    assert payload["error_type"] == "wake_budget"
    assert "per minute" in str(payload["error"])
    assert wake.called is False
    assert runtime.wakes.per_minute_cap == 3


async def test_wake_daily_cap_and_interval(tmp_path):
    settings = make_settings(tmp_path, wake_daily_cap=3, wake_min_interval_seconds=1800)
    seed_token(settings)
    runtime = build_runtime(settings)
    now = utc_now()
    runtime.history.record_wake(
        vin_masked="5YJ…0001",
        outcome="sent",
        recorded_at=now - timedelta(minutes=10),
    )
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        FleetAPIStub(router).list_vehicles()
        async with Client(mcp) as client:
            early = tool_json(await client.call_tool("tesla_wake_and_refresh", {"confirm": True}))
    assert early["error_type"] == "wake_budget"
    assert "seconds" in str(early["error"])

    for hours in (6, 5, 4):
        runtime.history.record_wake(
            vin_masked="5YJ…0001",
            outcome="sent",
            recorded_at=now - timedelta(hours=hours),
        )
    with respx.mock(assert_all_called=False) as router:
        FleetAPIStub(router).list_vehicles()
        async with Client(mcp) as client:
            capped = tool_json(await client.call_tool("tesla_wake_and_refresh", {"confirm": True}))
    # The recent wake still trips the interval before the daily cap is considered.
    assert capped["error_type"] == "wake_budget"


async def test_wake_success_records_budget_and_snapshot(tmp_path):
    settings = make_settings(tmp_path, wake_min_interval_seconds=0, wake_daily_cap=3)
    seed_token(settings)
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        stub = FleetAPIStub(router)
        stub.list_vehicles()
        stub.wake()
        stub.vehicle_data()
        async with Client(mcp) as client:
            payload = tool_json(await client.call_tool("tesla_wake_and_refresh", {"confirm": True}))
    assert payload["ok"] is True
    assert payload["freshness"] == "live"
    assert payload["wakes_used_today"] == 1
    assert payload["wakes_remaining_today"] == 2
    assert payload["battery_level_percent"] == 72
    assert "latitude" not in payload
    runtime = build_runtime(settings)
    assert runtime.wakes.summary()["used_today"] == 1


async def test_asleep_poll_does_not_hide_the_last_battery_reading(tmp_path):
    settings = make_settings(tmp_path)
    seed_token(settings)
    runtime = build_runtime(settings)
    runtime.history.add_snapshot(
        Snapshot(
            vin=VIN,
            recorded_at=utc_now() - timedelta(hours=2),
            state="online",
            live=True,
            source="poll",
            battery_level=18,
            plugged_in=False,
        )
    )
    with respx.mock(assert_all_called=False) as router:
        FleetAPIStub(router).list_vehicles(vehicle_list(state="asleep"))
        await poll_once(runtime)
    latest = runtime.history.latest(VIN)
    assert latest is not None
    assert latest.battery_level == 18
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        FleetAPIStub(router).list_vehicles(vehicle_list(state="asleep"))
        async with Client(mcp) as client:
            status = tool_json(await client.call_tool("tesla_get_status", {}))
            report = tool_json(await client.call_tool("tesla_report", {"type": "low_charge"}))
    assert status["battery_level_percent"] == 18
    assert status["freshness"] == "cached"
    assert report["report"]["alert"] is True  # type: ignore[index]


async def test_billed_wake_failure_consumes_budget_and_is_not_retried(tmp_path):
    settings = make_settings(tmp_path, wake_min_interval_seconds=1800, wake_daily_cap=3)
    seed_token(settings)
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        FleetAPIStub(router).list_vehicles()
        wake = router.post(url__regex=r".*/wake_up$").mock(return_value=httpx.Response(408))
        async with Client(mcp) as client:
            failed = tool_json(await client.call_tool("tesla_wake_and_refresh", {"confirm": True}))
            blocked = tool_json(await client.call_tool("tesla_wake_and_refresh", {"confirm": True}))
    assert wake.call_count == 1
    assert failed["ok"] is False
    assert blocked["error_type"] == "wake_budget"
    assert build_runtime(settings).wakes.summary()["used_today"] == 1


async def test_unbilled_wake_failure_releases_the_reservation(tmp_path):
    settings = make_settings(tmp_path, wake_min_interval_seconds=0, wake_daily_cap=3)
    seed_token(settings)
    seen: list[int] = []

    def wake(_request: httpx.Request) -> httpx.Response:
        seen.append(build_runtime(settings).wakes.summary()["used_today"])
        if len(seen) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"response": {"state": "online"}})

    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        stub = FleetAPIStub(router)
        stub.list_vehicles()
        stub.vehicle_data()
        router.post(url__regex=r".*/wake_up$").mock(side_effect=wake)
        async with Client(mcp) as client:
            failed = tool_json(await client.call_tool("tesla_wake_and_refresh", {"confirm": True}))
            again = tool_json(await client.call_tool("tesla_wake_and_refresh", {"confirm": True}))
    assert seen[0] == 1
    assert failed["ok"] is False
    assert again["ok"] is True
    assert build_runtime(settings).wakes.summary()["used_today"] == 1


async def test_wake_401_refreshes_and_does_not_send_a_second_wake(tmp_path):
    from tests.helpers import TOKEN_URL

    settings = make_settings(tmp_path, wake_min_interval_seconds=0)
    seed_token(settings)
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        FleetAPIStub(router).list_vehicles()
        wake = router.post(url__regex=r".*/wake_up$").mock(
            return_value=httpx.Response(
                401, json={"error": "expired", "access_token": "should-not-leak"}
            )
        )
        refresh = router.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "access-refreshed",
                    "refresh_token": "refresh-rotated",
                    "expires_in": 28800,
                },
            )
        )
        async with Client(mcp) as client:
            payload = tool_json(await client.call_tool("tesla_wake_and_refresh", {"confirm": True}))
    assert wake.call_count == 1
    assert refresh.called
    assert payload["ok"] is False
    assert "should-not-leak" not in json.dumps(payload)
    assert "not sent again" in str(payload["error"])
    assert build_runtime(settings).wakes.summary()["used_today"] == 1


def test_reserve_allows_only_one_of_two_threads(tmp_path):
    import threading

    settings = make_settings(tmp_path, wake_daily_cap=1, wake_min_interval_seconds=0)
    runtime = build_runtime(settings)
    barrier = threading.Barrier(2)
    results: list[bool] = []

    def attempt() -> None:
        barrier.wait()
        results.append(runtime.wakes.reserve(VIN).allowed)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [False, True]
    assert runtime.wakes.summary()["used_today"] == 1


def test_poller_source_does_not_name_wake_up():
    from pathlib import Path

    text = Path("src/tesla_mcp/poller.py").read_text(encoding="utf-8")
    assert "wake_up" not in text
