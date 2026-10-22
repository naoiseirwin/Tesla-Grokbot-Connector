"""Optional live smoke against a real car.

Tesla has no sandbox. This script is not part of CI. It lists vehicles and
reads status, which returns the cache when the car is asleep. It does not wake.

Set TESLA_LIVE_SMOKE=1 to run it. Expect well under $1: a handful of data calls.
"""

from __future__ import annotations

import asyncio
import os
import sys

from ..config import Settings
from ..errors import TeslaError
from ..fleet import FleetClient
from ..runtime import build_runtime
from ..tools.common import live_or_cached, status_dict
from ..vin import mask_vin


async def _smoke() -> int:
    settings = Settings.load()
    runtime = build_runtime(settings)
    async with FleetClient(runtime, allow_wake=False) as fleet:
        vehicles = await fleet.list_vehicles()
        if not vehicles:
            print("No vehicles on this account.")
            return 1
        for vehicle in vehicles:
            snapshot, freshness, observed = await live_or_cached(
                fleet, runtime, vehicle, source="smoke"
            )
            payload = status_dict(
                snapshot,
                name=vehicle.name,
                freshness=freshness,
                observed_state=observed,
            )
            print(
                f"{mask_vin(vehicle.vin)} state={observed} freshness={freshness} "
                f"battery={payload['battery_level_percent']}"
            )
    summary = runtime.wakes.summary()
    print(f"wakes_used_today={summary['used_today']}")
    return 0


def main() -> None:
    if os.environ.get("TESLA_LIVE_SMOKE") != "1":
        print(
            "Refusing to call Tesla. Set TESLA_LIVE_SMOKE=1 to run a billed smoke.", file=sys.stderr
        )
        raise SystemExit(2)
    print("Live smoke: list and status only. This does not wake the car.", file=sys.stderr)
    try:
        raise SystemExit(asyncio.run(_smoke()))
    except TeslaError as exc:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
