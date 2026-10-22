"""Charging history windows and Tesla 4xx message text. The Fleet API is mocked."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from fastmcp import Client

from tesla_mcp.errors import TeslaError
from tesla_mcp.fleet import FleetClient
from tesla_mcp.history import HistoryStore, Snapshot
from tesla_mcp.runtime import build_runtime
from tesla_mcp.server import create_server
from tesla_mcp.time_utils import parse_iso, utc_now
from tests.fixtures.vehicles import vehicle_list
from tests.helpers import VIN, make_settings, seed_token, tool_json
from tests.stubs.fleet_api_stub import FleetAPIStub

SCOPES = ["openid", "offline_access", "vehicle_device_data", "vehicle_charging_cmds"]


def _runtime(tmp_path, **overrides):
    settings = make_settings(tmp_path, enable_charging_history=True, **overrides)
    seed_token(settings, scopes=SCOPES)
    return build_runtime(settings)


def _row(start: str | None, site: str, kwh: float, cost: float = 1.0) -> dict[str, Any]:
    item: dict[str, Any] = {
        "siteLocationName": site,
        "fees": [
            {
                "feeType": "CHARGING",
                "totalDue": cost,
                "currencyCode": "EUR",
                "usageBase": kwh,
            }
        ],
    }
    if start is not None:
        item["chargeStartDateTime"] = start
    return item


def _page(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"data": list(rows)}


def _assert_windows(pairs: list[tuple[str, str]], *, start: str, end: str) -> None:
    assert pairs
    assert pairs[0][0] == start
    assert pairs[-1][1] == end
    for index, (window_start, window_end) in enumerate(pairs):
        start_at = parse_iso(window_start)
        end_at = parse_iso(window_end)
        assert start_at <= end_at
        assert end_at - start_at <= timedelta(days=365)
        if index:
            assert pairs[index - 1][1] == window_start


async def test_multi_year_range_is_split_deduped_and_sorted_newest_first(tmp_path):
    runtime = _runtime(tmp_path)
    calls: list[tuple[str, str]] = []

    def history(request: httpx.Request) -> httpx.Response:
        start = str(request.url.params["startTime"])
        end = str(request.url.params["endTime"])
        calls.append((start, end))
        assert request.url.params["vin"] == VIN
        assert request.url.params["pageSize"] == "50"
        if start == "2024-01-01T00:00:00Z":
            return httpx.Response(
                200,
                json=_page(
                    _row("2024-06-01T00:00:00Z", "Alpha", 10),
                    _row("2024-12-31T00:00:00Z", "Boundary", 5, cost=2),
                ),
            )
        if start == "2024-12-31T00:00:00Z":
            return httpx.Response(
                200,
                json=_page(
                    _row("2024-12-31T00:00:00Z", "Boundary", 5, cost=2),
                    _row("2025-03-01T00:00:00Z", "Beta", 7),
                ),
            )
        if start == "2025-12-31T00:00:00Z":
            return httpx.Response(
                200,
                json=_page(
                    _row("2025-12-31T12:00:00Z", "Gamma", 3),
                    _row(None, "Undated", 1),
                ),
            )
        raise AssertionError(f"unexpected window {start} -> {end}")

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/charging/history").mock(side_effect=history)
        async with FleetClient(runtime) as fleet:
            sessions = await fleet.charging_history(
                VIN, "2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z"
            )

    _assert_windows(
        calls,
        start="2024-01-01T00:00:00Z",
        end="2026-01-01T00:00:00Z",
    )
    assert [session["site"] for session in sessions] == [
        "Gamma",
        "Beta",
        "Boundary",
        "Alpha",
        "Undated",
    ]
    assert sessions[-1]["start"] is None
    boundary = [session for session in sessions if session["site"] == "Boundary"]
    assert len(boundary) == 1
    assert boundary[0]["kwh"] == 5


async def test_date_only_bounds_are_start_and_end_of_day_utc(tmp_path):
    runtime = _runtime(tmp_path)
    calls: list[tuple[str, str]] = []

    def history(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url.params["startTime"]), str(request.url.params["endTime"])))
        return httpx.Response(200, json=_page(_row("2025-10-23T10:00:00Z", "Day", 4)))

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/charging/history").mock(side_effect=history)
        async with FleetClient(runtime) as fleet:
            sessions = await fleet.charging_history(VIN, "2025-10-23", "2025-10-24")

    assert calls == [("2025-10-23T00:00:00Z", "2025-10-24T23:59:59Z")]
    assert sessions[0]["site"] == "Day"


async def test_full_iso_times_keep_their_clock_time_in_utc(tmp_path):
    runtime = _runtime(tmp_path)
    calls: list[tuple[str, str]] = []

    def history(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url.params["startTime"]), str(request.url.params["endTime"])))
        return httpx.Response(200, json=_page())

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/charging/history").mock(side_effect=history)
        async with FleetClient(runtime) as fleet:
            await fleet.charging_history(VIN, "2025-10-23T15:30:00Z", "2025-10-24T08:00:00+02:00")

    assert calls == [("2025-10-23T15:30:00Z", "2025-10-24T06:00:00Z")]


async def test_omitted_range_defaults_to_the_last_365_days(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    frozen = datetime(2026, 10, 22, 15, 4, 5, tzinfo=UTC)
    monkeypatch.setattr("tesla_mcp.fleet.utc_now", lambda: frozen)
    calls: list[tuple[str, str]] = []

    def history(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url.params["startTime"]), str(request.url.params["endTime"])))
        return httpx.Response(200, json=_page())

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/charging/history").mock(side_effect=history)
        async with FleetClient(runtime) as fleet:
            await fleet.charging_history(VIN)

    assert calls == [("2025-10-22T15:04:05Z", "2026-10-22T15:04:05Z")]
    assert parse_iso(calls[0][1]) - parse_iso(calls[0][0]) == timedelta(days=365)


async def test_start_after_end_is_rejected_before_any_request(tmp_path):
    runtime = _runtime(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url__regex=r".*/charging/history").mock(
            return_value=httpx.Response(200, json=_page())
        )
        async with FleetClient(runtime) as fleet:
            with pytest.raises(TeslaError, match="on or before end"):
                await fleet.charging_history(VIN, "2026-02-01", "2025-01-01")
    assert route.called is False


async def test_range_over_ten_years_is_rejected_before_any_request(tmp_path):
    runtime = _runtime(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url__regex=r".*/charging/history").mock(
            return_value=httpx.Response(200, json=_page())
        )
        async with FleetClient(runtime) as fleet:
            with pytest.raises(TeslaError, match="10 years"):
                await fleet.charging_history(VIN, "2010-01-01", "2026-01-01")
    assert route.called is False


async def test_ten_calendar_years_are_consecutive_windows(tmp_path):
    runtime = _runtime(tmp_path)
    calls: list[tuple[str, str]] = []

    def history(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url.params["startTime"]), str(request.url.params["endTime"])))
        return httpx.Response(200, json=_page())

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/charging/history").mock(side_effect=history)
        async with FleetClient(runtime) as fleet:
            await fleet.charging_history(VIN, "2016-01-01", "2026-01-01")

    _assert_windows(
        calls,
        start="2016-01-01T00:00:00Z",
        end="2026-01-01T23:59:59Z",
    )
    assert 10 <= len(calls) <= 12


async def test_http_400_includes_tesla_message(tmp_path):
    runtime = _runtime(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/charging/history").mock(
            return_value=httpx.Response(
                400,
                json={"code": 400, "message": "Date range cannot exceed 1 year"},
            )
        )
        async with FleetClient(runtime) as fleet:
            with pytest.raises(TeslaError) as caught:
                await fleet.charging_history(VIN, "2025-01-01", "2025-06-01")
    assert caught.value.status_code == 400
    assert caught.value.message == ("Tesla Fleet API HTTP 400: Date range cannot exceed 1 year.")
    assert "{" not in caught.value.message


@pytest.mark.parametrize(
    "message",
    [
        "rejected eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig",
        "access_token=super-secret-value",
    ],
)
async def test_http_400_drops_a_message_that_contains_a_secret(tmp_path, message):
    runtime = _runtime(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/api/1/vehicles(?:\?|$)").mock(
            return_value=httpx.Response(400, json={"message": message})
        )
        async with FleetClient(runtime) as fleet:
            with pytest.raises(TeslaError) as caught:
                await fleet.list_vehicles()
    assert caught.value.message == "Tesla Fleet API HTTP 400."
    assert "eyJ" not in caught.value.message
    assert "super-secret" not in caught.value.message
    assert "access_token" not in caught.value.message


async def test_http_5xx_does_not_include_the_response_message(tmp_path):
    runtime = _runtime(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/api/1/vehicles(?:\?|$)").mock(
            return_value=httpx.Response(
                503,
                json={"message": "Date range cannot exceed 1 year"},
            )
        )
        async with FleetClient(runtime) as fleet:
            with pytest.raises(TeslaError) as caught:
                await fleet.list_vehicles()
    assert caught.value.message == "Tesla Fleet API HTTP 503."


async def test_pages_inside_one_window_are_still_followed(tmp_path):
    runtime = _runtime(tmp_path)

    def history(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["pageNo"])
        if page == 1:
            rows = [
                _row(f"2025-06-01T00:{index:02d}:00Z", f"P{index}", float(index))
                for index in range(50)
            ]
            return httpx.Response(200, json={"data": rows})
        if page == 2:
            return httpx.Response(200, json=_page(_row("2025-06-02T00:00:00Z", "Last", 9)))
        raise AssertionError(f"unexpected page {page}")

    with respx.mock(assert_all_called=False) as router:
        route = router.get(url__regex=r".*/charging/history").mock(side_effect=history)
        async with FleetClient(runtime) as fleet:
            sessions = await fleet.charging_history(VIN, "2025-06-01", "2025-06-03")
    assert route.call_count == 2
    assert len(sessions) == 51
    assert sessions[0]["site"] == "Last"


async def test_tool_returns_normalized_dates(tmp_path):
    settings = make_settings(tmp_path, enable_charging_history=True)
    seed_token(settings, scopes=SCOPES)
    mcp = create_server(settings)
    calls: list[tuple[str, str]] = []

    def history(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url.params["startTime"]), str(request.url.params["endTime"])))
        return httpx.Response(200, json=_page(_row("2025-01-01T12:00:00Z", "Home", 8)))

    with respx.mock(assert_all_called=False) as router:
        FleetAPIStub(router).list_vehicles(vehicle_list())
        router.get(url__regex=r".*/charging/history").mock(side_effect=history)
        async with Client(mcp) as client:
            payload = tool_json(
                await client.call_tool(
                    "tesla_get_charging_history",
                    {"start": "2025-01-01", "end": "2025-01-02"},
                )
            )
    assert calls == [("2025-01-01T00:00:00Z", "2025-01-02T23:59:59Z")]
    assert payload["ok"] is True
    assert payload["start"] == "2025-01-01T00:00:00Z"
    assert payload["end"] == "2025-01-02T23:59:59Z"
    sessions = payload["sessions"]
    assert isinstance(sessions, list)
    assert sessions[0]["site"] == "Home"


async def test_tool_rejects_a_reversed_range_without_calling_tesla(tmp_path):
    settings = make_settings(tmp_path, enable_charging_history=True)
    seed_token(settings, scopes=SCOPES)
    mcp = create_server(settings)
    with respx.mock(assert_all_called=False) as router:
        route = router.route().mock(return_value=httpx.Response(200, json={}))
        async with Client(mcp) as client:
            payload = tool_json(
                await client.call_tool(
                    "tesla_get_charging_history",
                    {"start": "2026-05-01", "end": "2025-01-01"},
                )
            )
    assert route.called is False
    assert payload["ok"] is False
    assert "on or before end" in str(payload["error"])


async def test_charging_summary_surfaces_tesla_message(tmp_path):
    settings = make_settings(tmp_path, enable_charging_history=True)
    seed_token(settings, scopes=SCOPES)
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
        router.get(url__regex=r".*/charging/history").mock(
            return_value=httpx.Response(
                400,
                json={"code": 400, "message": "Date range cannot exceed 1 year"},
            )
        )
        async with Client(mcp) as client:
            payload = tool_json(
                await client.call_tool("tesla_report", {"type": "charging_summary"})
            )
    assert payload["ok"] is False
    assert payload["error"] == ("Tesla Fleet API HTTP 400: Date range cannot exceed 1 year.")
    assert "eyJ" not in str(payload["error"])
