"""Shared helpers for the CONTEXT feeds (EIA, UN Comtrade).

Context-feed doctrine (market quarantine):
  * these feeds are READ-ONLY CONTEXT for the UI and reports. Nothing here
    is an engine input and nothing may enter a run journal. This package
    imports nothing from server/engine, server/sim or server/api, and no
    engine/sim module imports it;
  * every figure is labelled ``figure_label = "SOURCED"`` (an observed
    figure from the named publisher) and every record carries the
    provenance envelope;
  * no forward price claim and no summed "trade at risk" headline is
    ever produced — records are individual sourced series values.

Cache discipline: the registry builds fresh adapter instances every
cycle, so an in-memory cache would never hit. The cache is therefore the
adapter's own last-good pack (data/snapshots/<name>.json): if that pack
was written by a LIVE fetch younger than the adapter's TTL it is served
as-is, with its ORIGINAL ``retrieved_at`` and ``mode=LIVE`` (it is the
live data, honestly dated), and no upstream call is made.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from . import base as _base
from .base import FetchResult, _parse_iso

FIGURE_LABEL = "SOURCED"
QUARANTINE_NOTE = ("market-context feed: read-only context for the UI and "
                   "reports; never an engine input, never in a run journal")


def context_fields() -> dict:
    """Fields every context-feed record carries."""
    return {"figure_label": FIGURE_LABEL, "context_only": True}


def fresh_live_pack(adapter, ttl_s: float,
                    now: Optional[datetime] = None) -> Optional[FetchResult]:
    """Last-good pack as a FetchResult iff a LIVE fetch wrote it less than
    ``ttl_s`` ago, else None. Reads ``base.SNAPSHOT_DIR`` at call time."""
    path = _base.SNAPSHOT_DIR / f"{adapter.name}.json"
    try:
        pack = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    prov = pack.get("provenance") or {}
    if prov.get("mode") != "LIVE" or not pack.get("records"):
        return None
    retrieved = _parse_iso(prov.get("retrieved_at"))
    if retrieved is None:
        return None
    age = ((now or datetime.now(timezone.utc)) - retrieved).total_seconds()
    if age < 0 or age >= ttl_s:
        return None
    return FetchResult(records=[dict(r) for r in pack["records"]],
                       provenance=dict(prov))


def to_float(value) -> Optional[float]:
    """Finite float or None (EIA serves numbers as strings, nulls as
    None/empty). Never coerces a missing value to zero."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f
