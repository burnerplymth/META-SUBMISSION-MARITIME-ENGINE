"""Signal + evidence shaping for Satellite Watch (code + interfaces only).

(a) candidate: a scan with REAL detection counts can be shaped into a
    MODELED congestion candidate (kind=congestion, source
    "SAT-OBSERVED (Satellite Watch)", mode MODELED)
    for the event store to mint. In fixture mode there are no counts, so this
    REFUSES - a satellite scan must never mint a signal out of imagery
    nobody has counted.

(b) evidence: a scan shapes into grade evidence
    {source, retrieved_at, mode: SNAPSHOT} (every grade
    carries evidence[] with that provenance triple). Anchorage counts
    are queue/capacity evidence - but only once a
    real detector produced them, which the detail block records.
"""
from __future__ import annotations

SAT_SOURCE = "SAT-OBSERVED (Satellite Watch)"


class SatwatchRefusal(RuntimeError):
    """Raised when a scan cannot honestly support the requested shaping."""


def _counts(scan: dict) -> dict | None:
    det = scan.get("detection") or {}
    counts = det.get("counts")
    return counts if isinstance(counts, dict) else None


def scan_to_c3_candidate(scan: dict, severity: int | None = None) -> dict:
    """Shape a counted scan into a MODELED congestion candidate.

    Refuses (SatwatchRefusal) whenever detection counts are absent -
    fixture mode included. The candidate mirrors the field shape of
    server/adapters/c3.mint_event so it can be minted unchanged; the
    observed counts ride along under ``sat_observation`` for review.
    Severity, when not supplied, follows the anchorage delta:
    >= +50% -> 4, >= +25% -> 3, >= +10% -> 2, else 1.
    """
    counts = _counts(scan)
    if counts is None or counts.get("vessels_now") is None \
            or counts.get("vessels_baseline") is None:
        raise SatwatchRefusal(
            f"no detection counts for {scan.get('node_id')!r} "
            f"(detection status={ (scan.get('detection') or {}).get('status')!r}) - "
            "refusing to mint a SAT-OBSERVED signal without a counted scan")
    now = counts["vessels_now"]
    base = counts["vessels_baseline"]
    delta = now - base
    delta_frac = (delta / base) if base else (1.0 if delta > 0 else 0.0)
    if severity is None:
        severity = 4 if delta_frac >= 0.5 else 3 if delta_frac >= 0.25 \
            else 2 if delta_frac >= 0.10 else 1
        if not base:
            # A rise from zero has no fraction to read; never 3 or 4.
            severity = min(severity, MAX_SEVERITY_FROM_ZERO_BASELINE)
    scene_now = scan.get("scene_now") or {}
    return {
        "node_id": scan["node_id"],
        "kind": "congestion",
        "severity": severity,
        "source": SAT_SOURCE,
        "mode": "MODELED",
        "status": "developing",
        "detected_at": scan["retrieved_at"],
        "retrieved_at": scan["retrieved_at"],
        "evidence_url": f"satwatch://{scan['node_id']}/{scene_now.get('id', '')}",
        "dedupe_key": f"satwatch:{scan['node_id']}:{scene_now.get('id', '')}",
        "sat_observation": {
            "vessels_now": now,
            "vessels_baseline": base,
            "delta": delta,
            "delta_frac": round(delta_frac, 4),
            "scene_now_id": scene_now.get("id"),
            "scene_baseline_id": (scan.get("scene_baseline") or {}).get("id"),
            "countable_class": "large vessels >= ~150 m LOA (10 m/px)",
            # a detector count, not a reviewed one (see the mint gate for the per-place review)
            "verification": "Detected, not verified",
            "human_reviewed": False,
        },
    }


def scan_to_c9_evidence(scan: dict) -> dict:
    """Shape a scan into grade evidence {source, retrieved_at, mode}.

    mode is SNAPSHOT: the scan is a dated observation fetched at
    retrieved_at, not a live feed and not a model output. The detail
    block names the scenes so a grader can reproduce the observation;
    ``counts_available`` is explicit so nobody mistakes imagery alone
    for a counted queue observation.
    """
    counts = _counts(scan)
    return {
        "source": scan["source"],
        "retrieved_at": scan["retrieved_at"],
        "mode": "SNAPSHOT",
        "detail": {
            "node_id": scan["node_id"],
            "adapter": "satwatch",
            "scene_now_id": (scan.get("scene_now") or {}).get("id"),
            "scene_now_date": (scan.get("scene_now") or {}).get("date"),
            "scene_baseline_id": (scan.get("scene_baseline") or {}).get("id"),
            "counts_available": counts is not None,
            "vessels_now": counts.get("vessels_now") if counts else None,
        },
    }


# ---------------------------------------------------------------------------
# Production wiring: gate -> dry-run evaluation -> MODELED mint
# ---------------------------------------------------------------------------
# signals.py and brief.py are reachable from the API (server/satwatch/routes.py):
#   GET  /satwatch/signals        dry-run evaluation of every stored scan
#   POST /satwatch/signals/mint   admin: mint the eligible ones as MODELED
#                                 events into the event store
#   POST /satwatch/scan {mint}    optional mint-after-scan
# The gate is deliberately strict because a satellite signal enters the
# same event feed the engine and alerts read. NOTHING mints automatically:
# a mint is an admin action that carries an explicit per-place review
# (place id + the scene id that was reviewed). Gate rules:
#   * counts must exist for BOTH crops (a delta needs a baseline);
#   * the counts must be ANCHORAGE-ZONE counts (count_basis
#     "anchorage_zone"). A whole-frame detector count (meta-sam, count_basis
#     "frame") includes berthed ships and is not an anchorage queue: it never
#     mints, reviewed or not;
#   * a detector whose recall is not validated (all of them today) needs the
#     human review flag, and a zero baseline needs it too (a rise from zero
#     is exactly what unvalidated noise or cloud looks like, so it needs the
#     review even from a validated detector); a rise from a zero baseline is
#     capped at severity 2;
#   * the provider must be a REAL detector (counts_are_real in the
#     registry) - the offline reference-counts replay evaluates (so the
#     UI can show what WOULD happen) but can never mint;
#   * the scene pair must be usable under the AOI cloud gate;
#   * the anchorage count must have risen: delta >= MIN_ABS_DELTA vessels
#     AND >= MIN_DELTA_FRAC (severity >= 2). A flat or falling count is
#     not a congestion signal and mints nothing.

MIN_ABS_DELTA = 3
MIN_DELTA_FRAC = 0.10
MAX_SEVERITY_FROM_ZERO_BASELINE = 2
ANCHORAGE_BASIS = "anchorage_zone"


def _provider_is_real(provider: str | None) -> bool:
    try:
        from .detect import REGISTRY
    except ImportError:  # pragma: no cover - loose-script import
        from detect import REGISTRY  # type: ignore
    spec = REGISTRY.get(provider or "")
    return bool(spec and spec.counts_are_real)


def _recall_validated(provider: str | None) -> bool:
    try:
        from .detect import REGISTRY
    except ImportError:  # pragma: no cover - loose-script import
        from detect import REGISTRY  # type: ignore
    spec = REGISTRY.get(provider or "")
    return bool(spec and spec.recall_validated)


def evaluate_signal(scan: dict, *, reviewed: bool = False) -> dict:
    """Would this scan mint a MODELED congestion event, and if not, exactly
    why. Never raises for an ordinary scan. ``reviewed`` is True only when a
    human has reviewed THIS scan (the admin mint route passes it); the dry
    run (GET /satwatch/signals) leaves it False, so it shows the honest
    answer for an unreviewed scan."""
    det = scan.get("detection") or {}
    reasons: list[str] = []
    candidate = None
    counts = _counts(scan)
    if counts is None or counts.get("vessels_now") is None \
            or counts.get("vessels_baseline") is None:
        reasons.append("no_counts_for_both_crops")
    else:
        if not _provider_is_real(det.get("provider")):
            reasons.append("provider_not_a_real_detector")
        # Never lifted by review: a different quantity than an anchorage queue.
        if counts.get("count_basis") != ANCHORAGE_BASIS:
            reasons.append("count_basis_not_anchorage_zone")
        validated = _recall_validated(det.get("provider"))
        if not validated and not reviewed:
            reasons.append("recall_not_validated_needs_review")
        if counts["vessels_baseline"] == 0 and not reviewed \
                and "recall_not_validated_needs_review" not in reasons:
            reasons.append("zero_baseline_needs_review")
        if not scan.get("usable_pair"):
            reasons.append("pair_not_usable_under_cloud_gate")
        delta = counts["vessels_now"] - counts["vessels_baseline"]
        base = counts["vessels_baseline"]
        frac = (delta / base) if base else (1.0 if delta > 0 else 0.0)
        if delta < MIN_ABS_DELTA or frac < MIN_DELTA_FRAC:
            reasons.append("delta_below_threshold")
        try:
            candidate = scan_to_c3_candidate(scan)
        except SatwatchRefusal:
            candidate = None
    return {
        "node_id": scan.get("node_id"),
        "would_mint": not reasons,
        "reasons": reasons,
        "human_review_required": True,   # a mint is never automatic
        "provider": det.get("provider"),
        "provider_label": det.get("provider_label"),
        "candidate": candidate,
        "thresholds": {"min_abs_delta": MIN_ABS_DELTA, "min_delta_frac": MIN_DELTA_FRAC},
        "c9_evidence": scan_to_c9_evidence(scan),
    }


def _ts_seconds(iso: str | None) -> str | None:
    """'2026-10-19T08:29:39.024000Z' -> '2026-10-19T08:29:39Z' (format)."""
    if not iso or len(iso) < 19:
        return None
    return iso[:19] + "Z"


def mint_modeled_event(scan: dict, store, *, reviewed: bool = False,
                       allow_unready: bool = False) -> dict:
    """Mint one MODELED event from a counted scan into ``store`` (an
    EventStore). Returns {minted: bool, event, reasons}. Refuses (no
    write) unless ``evaluate_signal`` says would_mint; ``reviewed`` must be
    True for an unvalidated detector (the admin route sets it only from an
    explicit per-place review payload). ``allow_unready`` exists for the minting
    plumbing only and is never set by the API."""
    ev_eval = evaluate_signal(scan, reviewed=reviewed)
    if not ev_eval["would_mint"] and not allow_unready:
        return {"minted": False, "event": None, "reasons": ev_eval["reasons"]}
    cand = ev_eval["candidate"]
    if cand is None:
        return {"minted": False, "event": None, "reasons": ev_eval["reasons"]}
    try:
        from ..adapters.c3 import mint_event
    except ImportError:  # server/ on sys.path
        from adapters.c3 import mint_event  # type: ignore
    scene_now = scan.get("scene_now") or {}
    event = mint_event(
        node_id=cand["node_id"], kind="congestion", severity=cand["severity"],
        source=SAT_SOURCE, evidence_url=cand["evidence_url"],
        dedupe_key=cand["dedupe_key"],
        started_at=_ts_seconds(scene_now.get("datetime")),
        detected_at=_ts_seconds(cand["detected_at"]),
        status="developing", mode="MODELED")
    event["sat_observation"] = cand["sat_observation"]
    appended = store.append_event(event)
    return {"minted": bool(appended), "event": event,
            "reasons": [] if appended else ["duplicate_occurrence"]}
