"""The demo sim run: one fixed request, hence one fixed run id.

The demo and recorded stack use ONE anchor, ``DEMO_START_AT``, instead of the
current UTC hour, so the default run id is stable and the default run is a
cache hit (each distinct run is 75-330 MB and 40-120 CPU-seconds). The run it
names is built once at boot (SUPERMAP_SIM_DEMO_PREBUILD=1, set in render.yaml;
the record is too large to ship in the repository) and PINNED, so the store's
eviction never removes it.

The request below is the demo configuration: the Suez closure opening
three days after the start for ten days, day tick x 30, seed 42, weather off,
lane geometry on. ``web/app/src/store.tsx`` ``defaultSimRequest`` carries the
same constants.
"""
from __future__ import annotations

DEMO_START_AT = "2026-10-26T00:00:00Z"
DEMO_CLOSURE_OPENS_AT = "2026-10-29T00:00:00Z"   # DEMO_START_AT + 3 days
DEMO_PLACE = "chk_suez"


def demo_request() -> dict:
    """The POST /sim/run body of the demo run (fresh dict each call)."""
    return {
        "disruptions": [{
            "node_id": DEMO_PLACE, "kind": "closure", "capacity_loss": 1.0,
            "duration_d": 10, "shape": "step",
            "started_at": DEMO_CLOSURE_OPENS_AT}],
        "tick": "day", "ticks": 30, "seed": 42, "start_at": DEMO_START_AT,
        "weather": False, "lane_geometry": True,
    }
