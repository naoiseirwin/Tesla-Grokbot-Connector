"""401, 408, 421, and 429 mapping. The Fleet API is mocked."""

from __future__ import annotations

from datetime import UTC

import httpx
import pytest
import respx

from tesla_mcp.errors import (
    TeslaAsleepError,
    TeslaAuthError,
    TeslaError,
    TeslaRateLimitError,
    TeslaRegionError,
)
from tesla_mcp.fleet import FleetClient
from tesla_mcp.runtime import build_runtime
from tests.fixtures.vehicles import vehicle_list
from tests.helpers import FLEET, TOKEN_URL, make_settings, seed_token


def _runtime(tmp_path, **overrides):
    settings = make_settings(tmp_path, **overrides)
    seed_token(settings)
    return build_runtime(settings)


async def test_login_required_does_not_refresh(tmp_path):
    runtime = _runtime(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/api/1/vehicles(?:\?|$)").mock(
            return_value=httpx.Response(401, json={"error": "login_required"})
        )
        token = router.post(TOKEN_URL).mock(
            return_value=httpx.Response(500, json={"error": "should-not-be-called"})
        )
        async with FleetClient(runtime) as fleet:
            with pytest.raises(TeslaAuthError, match="tesla-mcp auth"):
                await fleet.list_vehicles()
    assert token.called is False
    rows = runtime.history.usage_since(_epoch())
    assert rows[0]["http_status"] == 401


async def test_other_401_refreshes_once(tmp_path):
    runtime = _runtime(tmp_path)
    seen: list[int] = []

    def vehicles(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        if len(seen) == 1:
            return httpx.Response(401, json={"error": "expired"})
        return httpx.Response(200, json=vehicle_list())

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/api/1/vehicles(?:\?|$)").mock(side_effect=vehicles)
        router.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "access-refreshed",
                    "refresh_token": "refresh-rotated",
                    "expires_in": 28800,
                },
            )
        )
        async with FleetClient(runtime) as fleet:
            vehicles_out = await fleet.list_vehicles()
    assert len(vehicles_out) == 1
    stored = runtime.tokens.load()
    assert stored is not None
    assert stored.refresh_token == "refresh-rotated"


async def test_408_is_asleep_and_billed(tmp_path):
    runtime = _runtime(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/api/1/vehicles/").mock(return_value=httpx.Response(408))
        async with FleetClient(runtime) as fleet:
            with pytest.raises(TeslaAsleepError, match="not woken|No wake"):
                await fleet.get_vehicle("5YJ3E1EA7KF000001")
    rows = runtime.history.usage_since(_epoch())
    assert rows[0]["category"] == "data"
    assert rows[0]["estimated_usd"] == pytest.approx(0.002)
    assert "5YJ3E1EA7KF000001" not in str(rows[0]["endpoint"])


async def test_421_names_the_region_and_does_not_retry(tmp_path):
    runtime = _runtime(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url__regex=r".*/api/1/vehicles(?:\?|$)").mock(
            return_value=httpx.Response(421)
        )
        async with FleetClient(runtime) as fleet:
            with pytest.raises(TeslaRegionError, match="na"):
                await fleet.list_vehicles()
    assert route.call_count == 1


async def test_429_retries_once_when_retry_after_is_short(tmp_path):
    runtime = _runtime(tmp_path)
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    responses = [
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(200, json=vehicle_list()),
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/api/1/vehicles(?:\?|$)").mock(side_effect=handler)
        async with FleetClient(runtime, sleep=sleep) as fleet:
            found = await fleet.list_vehicles()
    assert sleeps == [1]
    assert len(found) == 1


async def test_429_does_not_loop_on_a_long_retry_after(tmp_path):
    runtime = _runtime(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url__regex=r".*/api/1/vehicles(?:\?|$)").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "30"})
        )
        async with FleetClient(runtime) as fleet:
            with pytest.raises(TeslaRateLimitError) as caught:
                await fleet.list_vehicles()
    assert caught.value.retry_after == 30
    assert route.call_count == 1


async def test_blocked_command_path_is_not_requested(tmp_path):
    runtime = _runtime(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        route = router.route().mock(return_value=httpx.Response(200, json={}))
        async with FleetClient(runtime) as fleet:
            with pytest.raises(TeslaError, match="Blocked"):
                await fleet._request("POST", "/api/1/vehicles/abc/command/door_lock")
    assert route.called is False


async def test_wake_is_refused_when_the_client_disables_it(tmp_path):
    runtime = _runtime(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        route = router.post(url__regex=r".*/wake_up").mock(
            return_value=httpx.Response(200, json={})
        )
        async with FleetClient(runtime, allow_wake=False) as fleet:
            with pytest.raises(TeslaError, match="disabled"):
                await fleet.wake_up("5YJ3E1EA7KF000001")
    assert route.called is False


async def test_http_500_is_not_priced(tmp_path):
    runtime = _runtime(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/api/1/vehicles(?:\?|$)").mock(return_value=httpx.Response(503))
        async with FleetClient(runtime) as fleet:
            with pytest.raises(TeslaError):
                await fleet.list_vehicles()
    rows = runtime.history.usage_since(_epoch())
    assert rows[0]["estimated_usd"] == 0


def _epoch():
    from datetime import datetime

    return datetime(2020, 1, 1, tzinfo=UTC)


def test_fleet_base_constant_is_used():
    assert FLEET.startswith("https://")
