"""Ledger AUTO-LOCK + AUTO-GRADE cycle job, and the read-only stats view.

OFF by default: SUPERMAP_LEDGER_AUTOLOCK=1 turns the cycle on. It is hosted by
the existing ingestion cycle (server/adapters/run_once.py, which scripts/
serve_api.py runs single-flight on its dedicated executor, off the event
loop) and adds, on top of the autolock-v1 policy of llm/autolock.py:

  * a DAILY rate limit: at most SUPERMAP_LEDGER_MAX_PER_DAY (default 5)
    locks per UTC day, counted from the ledger itself;
  * OCCURRENCE dedupe: one lock per (node_id, kind, UTC day of start). The
    first event of an occurrence in store order represents it; a sibling
    event (a feed that splits one advisory into several ids) is skipped as
    duplicate_occurrence. autolock-v1 already dedupes by disruption_id;
  * the AUTO-GRADER (llm/autograde.py): matured entries are graded from
    PortWatch evidence under the ledger grading rule, deterministic, no LLM;
  * single-flight: a second concurrent call returns {"skipped":
    "already_running"} instead of overlapping.

Locking itself is run_autolock (llm/autolock.py): CORE places only, the
classifier gate, the rev 1 engine run saved to the runs store with the
derivation journaled, ledger.lock with locked_at = the lock moment. Nothing
here edits or deletes an existing ledger entry.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from .autograde import (MAX_GRADES_PER_CYCLE, PORTWATCH_LAG_D, grade_matured,
                        has_gradable_steps, matured)
from .autolock import MAX_LOCKS_PER_CYCLE, run_autolock
from .ledger import LedgerStore, _fmt_ts, _ts

ENV_SWITCH = "SUPERMAP_LEDGER_AUTOLOCK"
ENV_MAX_PER_DAY = "SUPERMAP_LEDGER_MAX_PER_DAY"
DEFAULT_MAX_PER_DAY = 5

_single_flight = threading.Lock()


def ledger_cycle_enabled(environ: dict | None = None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(ENV_SWITCH, "0")).strip() == "1"


def max_per_day(environ: dict | None = None) -> int:
    env = os.environ if environ is None else environ
    try:
        return max(0, int(env.get(ENV_MAX_PER_DAY, DEFAULT_MAX_PER_DAY)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_PER_DAY


def locks_on_day(ledger: LedgerStore, now: datetime) -> int:
    day = now.astimezone(timezone.utc).date().isoformat()
    return sum(1 for e in ledger.entries() if str(e.get("locked_at", ""))[:10] == day)


def occurrence_key(event: dict) -> tuple:
    when = event.get("started_at") or event.get("detected_at") or ""
    return (event.get("node_id"), event.get("kind"), str(when)[:10])


def _locked_occurrences(ledger: LedgerStore, events: list[dict]) -> set:
    by_id = {e.get("id"): e for e in events if isinstance(e, dict)}
    keys = set()
    for entry in ledger.entries():
        ev = by_id.get(entry["disruption_id"])
        if ev is not None:
            keys.add(occurrence_key(ev))
    return keys


def run_ledger_cycle(events: list[dict], evidence, *, now: datetime | None = None,
                     ledger_path: Path | None = None, runs_dir: Path | None = None,
                     graph_path: Path | None = None, page_path: Path | None = None,
                     classifier=None, environ: dict | None = None,
                     logger=None) -> dict:
    """One cycle: rate-limited auto-lock, then auto-grade. ``evidence`` is
    an autograde evidence provider (PortWatchEvidence or a fake)."""
    if not ledger_cycle_enabled(environ):
        return {"enabled": False, "reason": f"{ENV_SWITCH} is not 1"}
    if not _single_flight.acquire(blocking=False):
        return {"enabled": True, "skipped": "already_running"}
    try:
        now_dt = now or datetime.now(timezone.utc)
        ledger = LedgerStore(Path(ledger_path)) if ledger_path is not None else LedgerStore()
        summary: dict = {"enabled": True, "at": _fmt_ts(now_dt)}

        # ---- lock phase
        cap_day = max_per_day(environ)
        used = locks_on_day(ledger, now_dt)
        remaining = max(0, cap_day - used)
        summary["daily_cap"] = {"max_per_day": cap_day, "locked_today_before": used,
                                "remaining": remaining}
        decisions = []
        seen = _locked_occurrences(ledger, events)
        locked_ids = {e["disruption_id"] for e in ledger.entries()}
        batch_seen: set = set()
        candidates = []
        for ev in events:
            if not isinstance(ev, dict) or not ev.get("id"):
                candidates.append(ev)       # run_autolock records malformed events
                continue
            key = occurrence_key(ev)
            if ev["id"] not in locked_ids and key in batch_seen:
                seen.add(key)    # a sibling of an earlier event of this cycle
            batch_seen.add(key)
            if ev["id"] not in locked_ids and key in seen:
                decisions.append({"disruption_id": ev["id"], "decision": "skipped",
                                  "reason": "duplicate_occurrence",
                                  "detail": f"occurrence {key} already locked"})
                if logger:
                    logger(f"[ledger-cycle] skipped {ev['id']}: duplicate_occurrence {key}")
                continue
            candidates.append(ev)
        if remaining <= 0:
            lock_summary = {"policy": "autolock-v1", "evaluated": 0, "locked_count": 0,
                            "locked_entry_ids": [], "decisions": [],
                            "skipped_all": "daily_cap_reached"}
            if logger:
                logger(f"[ledger-cycle] daily cap {cap_day} reached; no locks this cycle")
        else:
            lock_summary = run_autolock(
                candidates, ledger_path=ledger.path, runs_dir=runs_dir,
                graph_path=graph_path, page_path=page_path, classifier=classifier,
                now=now_dt, cap=min(MAX_LOCKS_PER_CYCLE, remaining), logger=logger)
        lock_summary["decisions"] = decisions + lock_summary.get("decisions", [])
        summary["lock"] = lock_summary

        # ---- grade phase
        try:
            summary["grade"] = grade_matured(ledger, evidence, now=now_dt,
                                             max_grades=MAX_GRADES_PER_CYCLE, logger=logger)
        except Exception as exc:   # grading never undoes a lock or kills the cycle
            summary["grade"] = {"error": f"{type(exc).__name__}: {exc}"}
        return summary
    finally:
        _single_flight.release()


def ledger_stats(ledger_path: Path | None = None, *, now: datetime | None = None,
                 environ: dict | None = None) -> dict:
    """Read-only counts for GET /llm/ledger/stats. LEDGER numbers only
    (never merged with BACKTEST figures); superseded entries are listed
    but not scored, as in LedgerStore.accuracy()."""
    now_dt = now or datetime.now(timezone.utc)
    ledger = LedgerStore(Path(ledger_path)) if ledger_path is not None else LedgerStore()
    entries = ledger.entries()
    superseded = {e["supersedes"] for e in entries if e.get("supersedes")}
    live = [e for e in entries if e["entry_id"] not in superseded]
    graded = [e for e in live if e.get("grade")]
    open_entries = [e for e in live if not e.get("grade")]
    # Earlier one-step chains carry no gradable step (seq 0 is never
    # graded): counted apart, never "overdue".
    no_steps = [e for e in open_entries if not has_gradable_steps(e)]
    unresolved = [e for e in open_entries if has_gradable_steps(e)]
    hit = sum(len(e["grade"].get("hit_steps", [])) for e in graded)
    miss = sum(len(e["grade"].get("missed_steps", [])) for e in graded)
    ungr = sum(len(e["grade"].get("ungradeable_steps", [])) for e in graded)
    oldest = None
    if unresolved:
        o = min(unresolved, key=lambda e: (e["locked_at"], e["entry_id"]))
        age = (now_dt - _ts(o["locked_at"])).total_seconds() / 86400.0
        oldest = {"entry_id": o["entry_id"], "disruption_id": o["disruption_id"],
                  "locked_at": o["locked_at"], "window_end": o.get("window_end"),
                  "age_days": round(age, 3), "matured": matured(o, now_dt)}
    acc = ledger.accuracy()
    return {
        "source": "LEDGER (live)",
        "label": "LEDGER numbers only; never merged with BACKTEST figures.",
        "generated_at": _fmt_ts(now_dt),
        "entries_total": len(entries),
        "entries_superseded": len(superseded & {e["entry_id"] for e in entries}),
        "locked": len(live),
        "graded": len(graded),
        "unresolved": len(unresolved),
        "no_gradable_steps": len(no_steps),
        "matured_awaiting_grade": sum(1 for e in unresolved if matured(e, now_dt)),
        "steps": {"hit": hit, "missed": miss, "ungradeable": ungr,
                  "accuracy": acc.get("accuracy")},
        "oldest_unresolved": oldest,
        "lock_before_knowable_violations": len(ledger.validate_lock_before_knowable()),
        "autolock": {"ledger_cycle_enabled": ledger_cycle_enabled(environ),
                     "env": ENV_SWITCH, "max_per_day": max_per_day(environ),
                     "locked_today": locks_on_day(ledger, now_dt),
                     "grader_rule": "autograde-pw-v1",
                     "portwatch_lag_days": PORTWATCH_LAG_D},
        "torn_tail_dropped": bool(acc.get("torn_tail_dropped")),
    }
