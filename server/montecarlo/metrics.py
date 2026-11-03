"""Per-run outputs read from the engine's chain event stream.

Only the public event list is read (engine-agnostic: rev 1, rev 2 and the
profiles all emit the same event shape). Nothing here reaches into engine
internals.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

METRICS = (
    ("worst_added_transit_d", "d"),
    ("total_added_delay_d", "d"),
    ("places_affected_count", "places"),
    ("chain_length_events", "events"),
    ("queue_peak_delay_d", "d"),
)
METRIC_NAMES = tuple(m for m, _ in METRICS)
UNITS = dict(METRICS)
NOT_DEFINED = (
    {"metric": "queue_peak_vessels",
     "reason": "the engine's event stream carries waits in days, not a queue length in vessels"},
    {"metric": "volume_at_risk_teu_day",
     "reason": "the engine's event stream carries no TEU volume per event"},
)


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


# The engine's own default start (engine.engine._parse_ts(None)).
DEFAULT_START_AT = "2026-01-01T00:00:00Z"


def extract(events: list, started_at: str | None = None) -> tuple[dict, dict]:
    """(outputs, places) from one run's events.

    ``started_at`` is the disruption's start (the validated spec's ``started_at``; None means the
    engine's default start). Event timestamps are arrivals: the root event sits at the start plus the
    root's own wait, so the time origin must be the disruption start and not the first event.

    outputs: the metric values, rounded to 6 decimals.
    places:  {place_id: (first_arrival_day, largest_delay_d)} for the places that carry a delay
             (largest delay_d > 0); the arrival is in days since the disruption start. A place
             whose events all have delay 0 (a cost leaf, a pass-through) is not "affected" and
             is left out; ``chain_length_events`` still counts every event.
    """
    t0 = _ts(started_at or DEFAULT_START_AT)
    placed: dict[str, list] = {}
    worst = 0.0
    queue_peak = 0.0
    for ev in events:
        d = float(ev.get("delay_d") or 0.0)
        t = (_ts(ev["at"]) - t0).total_seconds() / 86400.0
        cur = placed.get(ev["node_id"])
        if cur is None:
            placed[ev["node_id"]] = [t, d]
        else:
            cur[0] = min(cur[0], t)
            cur[1] = max(cur[1], d)
        if d > worst:
            worst = d
        if ev.get("channel") == "queue" and d > queue_peak:
            queue_peak = d
    places = {p: (round(v[0], 6), round(v[1], 6)) for p, v in placed.items() if v[1] > 0.0}
    outputs = {
        "worst_added_transit_d": round(worst, 6),
        "total_added_delay_d": round(math.fsum(v[1] for v in places.values()), 6),
        "places_affected_count": len(places),
        "chain_length_events": len(events),
        "queue_peak_delay_d": round(queue_peak, 6),
    }
    return outputs, places
