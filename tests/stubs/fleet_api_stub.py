"""respx routes for the Fleet API. Nothing here is a live Tesla call."""

from __future__ import annotations

from typing import Any

import httpx
import respx

from tests.fixtures.vehicles import charging_page, vehicle_data, vehicle_list


class FleetAPIStub:
    def __init__(self, router: respx.MockRouter) -> None:
        self.router = router

    def list_vehicles(self, payload: dict[str, Any] | None = None, status_code: int = 200) -> None:
        self.router.get(url__regex=r".*/api/1/vehicles(?:\?|$)").mock(
            return_value=httpx.Response(status_code, json=payload or vehicle_list())
        )

    def vehicle_data(self, payload: dict[str, Any] | None = None, status_code: int = 200) -> None:
        self.router.get(url__regex=r".*/vehicle_data").mock(
            return_value=httpx.Response(status_code, json=payload or vehicle_data())
        )

    def wake(self, state: str = "online", status_code: int = 200) -> None:
        self.router.post(url__regex=r".*/wake_up$").mock(
            return_value=httpx.Response(
                status_code,
                json={"response": {"state": state}},
            )
        )

    def charging(self, payload: dict[str, Any] | None = None) -> None:
        self.router.get(url__regex=r".*/charging/history").mock(
            return_value=httpx.Response(200, json=payload or charging_page())
        )

    def called_wake(self) -> bool:
        return any("/wake_up" in str(call.request.url) for call in self.router.calls)
