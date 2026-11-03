"""Helpers for minting DisruptionEvents from structured feeds.

Zero LLM calls anywhere in this path (no model is involved).
Event id shape: ``evt_<yyyymmdd>_<node>_<kind>_<dedupe-suffix>`` — the
suffix derives from the source's own occurrence id (e.g. GDACS eventid),
so the same real-world occurrence re-fetched tomorrow keeps the same id
and the store dedupes it (one id per real-world occurrence).
"""
from __future__ import annotations

import hashlib
import re

from .base import utcnow_iso


def dedupe_suffix(*parts: object) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def mint_event(*, node_id: str, kind: str, severity: int, source: str,
               evidence_url: str, dedupe_key: str,
               started_at: str | None = None, detected_at: str | None = None,
               capacity_loss: float | None = None,
               expected_duration_d: float | None = None,
               status: str = "active", mode: str = "LIVE") -> dict:
    detected = detected_at or utcnow_iso()
    day = re.sub(r"\D", "", (started_at or detected))[:8]
    ev = {
        "id": f"evt_{day}_{node_id}_{kind}_{dedupe_suffix(dedupe_key)}",
        "node_id": node_id,
        "kind": kind,
        "severity": severity,
        "detected_at": detected,
        "source": source,
        "retrieved_at": utcnow_iso(),
        "evidence_url": evidence_url,
        "status": status,
        "mode": mode,
        "dedupe_key": dedupe_key,
    }
    if started_at:
        ev["started_at"] = started_at
    if capacity_loss is not None:
        ev["capacity_loss"] = capacity_loss
    if expected_duration_d is not None:
        ev["expected_duration_d"] = expected_duration_d
    return ev
