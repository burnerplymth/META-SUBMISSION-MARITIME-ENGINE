"""Ledger AUTO-GRADER (rule autograde-pw-v1): grade matured ledger entries from
IMF PortWatch evidence, deterministically. No LLM anywhere in this path.

Ledger grading rule (ledger.py GRADING_RULE_V1): a predicted step is a HIT when
outcome evidence shows an effect at the predicted node_id within
[0.5 x predicted delay_d, 2 x predicted delay_d] (from the disruption start),
backed by queue/capacity evidence; otherwise MISSED. This module only builds
the OBSERVATIONS; the grade itself is ledger.grade_chain, unchanged, written
once per entry through LedgerStore.grade_entry (the single grade write the
contract allows). Existing entries are never edited or deleted.

OBSERVATION RULE autograde-pw-v1 (per node of the locked chain, seq >= 1):
  * the node's PortWatch daily series (transits at a chokepoint, port calls
    at a port) is read from 35 days before the disruption start to the
    entry's stored window_end;
  * baseline B = mean of the 28 days before the start day (>= 14 days
    present AND B >= 5 per day, else the node is UNGRADEABLE - an
    observation without evidence, never a hit, never a miss);
  * EFFECT = the first day d in [start day, window_end] such that d, d+1
    and d+2 are each >= 20 percent below B (series_math.onset_day, the
    same onset rule as realism-v1). observed_delay_d = d - start in days
    (floored at 0); observed_at = the END of day d+2 (the moment the third
    low day is complete, i.e. when the effect becomes knowable), so the
    lock-before-knowable gate  locked_at < observed_at <= window_end  is
    evaluated on when the evidence could first exist;
  * NO EFFECT through window_end: an evidence-backed observation with
    observed_delay_d = NO_EFFECT_DELAY_D (a sentinel far outside any
    window) and observed_at = window_end - the step is MISSED by the fixed
    rule, with the PortWatch series as its evidence;
  * a node with no PortWatch series (rail, border, air) or an unserved
    series gets an evidence-less observation: UNGRADEABLE.
An entry is graded only when it has MATURED: now >= window_end +
PORTWATCH_LAG_D (PortWatch daily rows trail real time by about a week). If
no step has evidence, grade_entry refuses to publish and the entry stays
unresolved (reported by /llm/ledger/stats).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .ledger import (LedgerError, LedgerStore, _disruption_start, _fmt_ts,
                     _ts, _window_end)

try:  # server/ on sys.path
    from history.series_math import baseline as series_baseline, onset_day
    from history.util import add_days, iso_day
except ImportError:  # pragma: no cover - repo-root identity
    from server.history.series_math import (  # type: ignore
        baseline as series_baseline, onset_day)
    from server.history.util import add_days, iso_day  # type: ignore

RULE_ID = "autograde-pw-v1"
PORTWATCH_LAG_D = 8
NO_EFFECT_DELAY_D = 1.0e6
BASELINE_DAYS = 28
BASELINE_MIN_DAYS = 14
BASELINE_MIN_LEVEL = 5.0
EFFECT_DROP = -0.20
EFFECT_SUSTAIN_DAYS = 3
MAX_GRADES_PER_CYCLE = 5


class PortWatchEvidence:
    """Real evidence provider: IMF PortWatch daily series through the
    history layer (cached, budgeted). ``series(node_id, start, end)``
    returns {"served": bool, "series": {day: value}, "provenance": {...},
    "metric": str} or {"served": False, "reason": str}. Any object with the same
    method can be substituted."""

    def __init__(self, history_service):
        self.svc = history_service

    def series(self, node_id: str, start, end) -> dict:
        res = self.svc.portwatch.observations(node_id, "activity", start, end)
        if not res.get("served"):
            return {"served": False, "reason": res.get("reason", "not_served")}
        return {"served": True, "metric": res["metric"],
                "series": {p["date"]: p["value"] for p in res["series"]},
                "provenance": dict(res["provenance"])}


def _end_of_day(d) -> str:
    return iso_day(d) + "T23:59:59Z"


def has_gradable_steps(entry: dict) -> bool:
    """The ledger never grades the root step (seq 0); an entry locked before
    the minimum chain length was enforced can hold a one-step chain, which carries nothing gradable."""
    return any(e.get("seq", 0) >= 1 for e in entry.get("predicted_chain") or [])


def matured(entry: dict, now: datetime) -> bool:
    wend = entry.get("window_end")
    try:
        wend_dt = _ts(wend) if wend else _window_end(entry["predicted_chain"])
    except Exception:
        wend_dt = None
    if wend_dt is None:
        return False
    return now >= wend_dt + timedelta(days=PORTWATCH_LAG_D)


def build_observations(entry: dict, evidence) -> tuple[list[dict], list[dict]]:
    """(observations for LedgerStore.grade_entry, per-node notes). Pure given
    the evidence provider."""
    chain = entry["predicted_chain"]
    start = _disruption_start(chain)
    wend = _ts(entry["window_end"]) if entry.get("window_end") else _window_end(chain)
    if start is None or wend is None:
        raise LedgerError("entry has no derivable disruption start / window end")
    start_day = start.date()
    wend_day = wend.date()
    obs, notes = [], []
    for node in sorted({e["node_id"] for e in chain if e.get("seq", 0) >= 1}):
        res = evidence.series(node, add_days(start_day, -35), wend_day)
        base_obs = {"id": f"pw:{node}", "node_id": node}
        if not res.get("served"):
            obs.append({**base_obs, "observed_delay_d": 0.0, "observed_at": _fmt_ts(wend)})
            notes.append({"node_id": node, "outcome": "ungradeable",
                          "reason": f"no PortWatch series ({res.get('reason')})"})
            continue
        ser = res["series"]
        b, n = series_baseline(ser, start_day, BASELINE_DAYS, BASELINE_MIN_DAYS)
        if b is None or b < BASELINE_MIN_LEVEL:
            obs.append({**base_obs, "observed_delay_d": 0.0, "observed_at": _fmt_ts(wend)})
            notes.append({"node_id": node, "outcome": "ungradeable",
                          "reason": ("insufficient baseline days" if b is None
                                     else f"low count: baseline {b:.2f}/day < {BASELINE_MIN_LEVEL}")})
            continue
        prov = res["provenance"]
        src = (f"IMF PortWatch daily {res.get('metric')} for {node} "
               f"({RULE_ID}: first {EFFECT_SUSTAIN_DAYS} consecutive days >= "
               f"{abs(EFFECT_DROP) * 100:.0f} percent below the {BASELINE_DAYS}-day "
               f"pre-event baseline {b:.2f}/day)")
        common = {**base_obs, "source": src, "retrieved_at": prov.get("retrieved_at"),
                  "mode": prov.get("mode", "SNAPSHOT")}
        od = onset_day(ser, start_day, wend_day, b, EFFECT_DROP, EFFECT_SUSTAIN_DAYS)
        if od is None:
            obs.append({**common, "observed_delay_d": NO_EFFECT_DELAY_D,
                        "observed_at": _fmt_ts(wend),
                        "source": src + " - NO EFFECT observed through the window close"})
            notes.append({"node_id": node, "outcome": "no_effect"})
        else:
            delay = max(0.0, (datetime(od.year, od.month, od.day, tzinfo=timezone.utc)
                              - start).total_seconds() / 86400.0)
            obs.append({**common, "observed_delay_d": round(delay, 6),
                        "observed_at": _end_of_day(add_days(od, EFFECT_SUSTAIN_DAYS - 1))})
            notes.append({"node_id": node, "outcome": "effect",
                          "observed_delay_d": round(delay, 6), "first_low_day": iso_day(od)})
    return obs, notes


def grade_matured(ledger: LedgerStore, evidence, *, now: datetime,
                  max_grades: int = MAX_GRADES_PER_CYCLE, logger=None) -> dict:
    """Grade up to ``max_grades`` matured, ungraded, not-superseded entries,
    oldest first. Each grade is the fixed-rule grade over autograde-pw-v1
    observations. A refusal (no evidence, lock-before-knowable) is recorded
    and the entry is left untouched."""
    entries = ledger.entries()
    superseded = {e["supersedes"] for e in entries if e.get("supersedes")}
    graded, refused, waiting, no_steps = [], [], [], []
    attempts = 0
    for e in sorted(entries, key=lambda x: (x["locked_at"], x["entry_id"])):
        if "grade" in e or e["entry_id"] in superseded:
            continue
        if not has_gradable_steps(e):
            no_steps.append(e["entry_id"])      # earlier one-step chain
            continue
        if not matured(e, now):
            waiting.append(e["entry_id"])
            continue
        if len(graded) >= max_grades or attempts >= 3 * max_grades:
            break
        attempts += 1
        try:
            obs, notes = build_observations(e, evidence)
            grade = ledger.grade_entry(e["entry_id"], obs, graded_at=_fmt_ts(now))
        except LedgerError as exc:
            refused.append({"entry_id": e["entry_id"], "reason": str(exc)})
            if logger:
                logger(f"[autograde] {e['entry_id']} not graded: {exc}")
            continue
        except Exception as exc:   # an evidence outage never kills the cycle
            refused.append({"entry_id": e["entry_id"], "reason": f"{type(exc).__name__}: {exc}"})
            if logger:
                logger(f"[autograde] {e['entry_id']} evidence error: {exc}")
            continue
        graded.append({"entry_id": e["entry_id"], "hit_steps": grade["hit_steps"],
                       "missed_steps": grade["missed_steps"],
                       "ungradeable_steps": grade["ungradeable_steps"], "notes": notes})
        if logger:
            logger(f"[autograde] GRADED {e['entry_id']} hits={grade['hit_steps']} "
                   f"missed={grade['missed_steps']} ungradeable={grade['ungradeable_steps']}")
    return {"rule": RULE_ID, "graded_count": len(graded), "graded": graded,
            "refused": refused, "not_yet_matured": waiting,
            "no_gradable_steps": no_steps}
