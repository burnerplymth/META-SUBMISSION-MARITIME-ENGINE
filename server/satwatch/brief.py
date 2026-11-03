"""Change-intel diff + template brief renderer for Satellite Watch.

No LLM is called here, ever: the Spark intel brief goes through the
LLM provider module. This module produces
the structured diff the brief consumes and a deterministic template
rendering of it. In fixture mode (no detector counts) the diff is all
nulls and the rendering states the honest position - imagery dates and
'detection awaits Meta API key' - with no fabricated counts or verdicts.
"""
from __future__ import annotations

from typing import Optional

# every detector count wears this until a human reviews that place (nothing sets human_reviewed yet)
from .detected import VERIFICATION  # noqa: E402  (one string for every SAM-derived count)


def build_change_intel(scan: dict) -> dict:
    """Structured now-vs-baseline diff for one scan record.

    Fixture mode: vessels_now / vessels_baseline / delta are null and
    status is the detection status (AWAITING_META_KEY). With real
    counts, delta = now - baseline and status is OK.
    """
    det = scan.get("detection") or {}
    counts = det.get("counts") if isinstance(det.get("counts"), dict) else None
    if counts is None or counts.get("vessels_now") is None \
            or counts.get("vessels_baseline") is None:
        return {
            "node_id": scan["node_id"],
            "vessels_now": None,
            "vessels_baseline": None,
            "delta": None,
            "status": det.get("status", "AWAITING_META_KEY"),
            "imagery_dates": scan.get("imagery_dates", {}),
            "provider": det.get("provider"),
            "provider_label": det.get("provider_label"),
        }
    now = counts["vessels_now"]
    base = counts["vessels_baseline"]
    return {
        "node_id": scan["node_id"],
        "vessels_now": now,
        "vessels_baseline": base,
        "delta": now - base,
        "status": "OK",
        "verification": VERIFICATION,
        "human_reviewed": False,
        "count_basis": counts.get("count_basis"),
        "imagery_dates": scan.get("imagery_dates", {}),
        "provider": det.get("provider"),
        "provider_label": det.get("provider_label"),
    }


def render_brief(scan: dict, intel: Optional[dict] = None) -> str:
    """Render the template SAT INTEL brief for a scan (no LLM)."""
    intel = intel if intel is not None else build_change_intel(scan)
    dates = intel.get("imagery_dates") or {}
    lines = [
        f"SAT INTEL - {scan['name']} ({scan['node_id']})",
        f"Source: {scan['source']} - {scan['resolution_m']} m/px, Sentinel-2 L2A",
        f"Imagery current as of {dates.get('now')}; baseline {dates.get('baseline')}",
        f"Scenes: now {(scan.get('scene_now') or {}).get('id')} / "
        f"baseline {(scan.get('scene_baseline') or {}).get('id')}",
        f"Retrieved: {scan['retrieved_at']} - revisit is ~5 days; this is a "
        "strategic layer, never live.",
    ]
    if intel["status"] != "OK" or intel["vessels_now"] is None:
        lines += [
            "Counts: NOT RUN - detection awaits Meta API key.",
            "No change verdict is offered until a real detector has counted "
            "both scenes; imagery alone is not a count.",
        ]
    else:
        basis = intel.get("count_basis")
        if basis == "anchorage_zone":
            what = "Vessels at anchorage (>= ~150 m LOA)"
        elif basis == "frame":
            what = ("Detections in the whole frame (berthed ships included; "
                    "not an anchorage count)")
        else:
            what = "Detections (count basis not stated)"
        lines += [
            f"Counted by: {intel.get('provider_label') or intel.get('provider')}",
            f"{VERIFICATION}: the counts below are what the detector found, not a reviewed count of vessels.",
            f"{what}: now {intel['vessels_now']}, "
            f"baseline {intel['vessels_baseline']}, delta {intel['delta']:+d}.",
            "Arithmetic only: the counts and their difference; no verdict is offered.",
        ]
    return "\n".join(lines) + "\n"
