"""Alert rule evaluator.

One pass = ``evaluate(store, facts)`` over a facts snapshot::

    facts = {
      "health":  [adapter health entries {adapter, state, ...}],
      "events":  [DisruptionEvents],
      "runs":    [run records {run_id, events: [...], ...}],
      "ledger_entries": [merged ledger entries; a graded entry carries
                         its grade under "grade"],
      "node_modes": {node_id: node mode ("sea" | "air" | "ground")},
    }

Callers gather facts from the served sources (``gather_facts`` reads
the same files the API serves, corrupt-line tolerant) and invoke a
pass on the existing seams: adapter-cycle end, run/fork completion.
The evaluator is the only writer of fired alerts + its own state.

Semantics:
  * adapter_state — level-triggered with re-arm: fires on the
    false -> true transition of its condition, and not again until
    the condition has been observed false. The condition memory is
    kept in sync even for disabled rules (a disabled rule never
    fires, and re-enabling it does not fire on a stale transition).
  * event / run_outcome / ledger — edge-triggered: a fact the
    evaluator has never processed fires every matching enabled rule
    once. Processed fact ids are tracked in the evaluator state, so
    re-running a pass over the same snapshot fires nothing twice.
    ``prime()`` marks the current snapshot processed without firing;
    the API calls it when a rule is created, so a rule never fires
    on facts that predate it.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from .store import AlertsStore, new_id, now_iso


# -- fact gathering ---------------------------------------------------------

def _read_jsonl(path: Path | None) -> list[dict]:
    if path is None or not Path(path).exists():
        return []
    rows: list[dict] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


# Incremental run-file index. gather_facts() does not re-read and
# re-parse every run file on every evaluation pass: parsed records are
# cached per absolute path, keyed by
# (mtime_ns, size): an unchanged file is served from the cache without
# touching its bytes; a changed file (either key differs) is re-parsed.
# Cached records are shared objects — callers treat facts as read-only,
# as every current caller (evaluate, watchlist view) already does.
_run_record_cache: dict[str, tuple[int, int, dict]] = {}
_run_record_cache_lock = threading.Lock()
_RUN_RECORD_CACHE_MAX = 1024


def _read_run_record(path: Path) -> dict | None:
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    with _run_record_cache_lock:
        hit = _run_record_cache.get(key)
        if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeDecodeError):
        return None
    if not (isinstance(record, dict) and record.get("run_id")):
        return None
    with _run_record_cache_lock:
        _run_record_cache[key] = (st.st_mtime_ns, st.st_size, record)
        while len(_run_record_cache) > _RUN_RECORD_CACHE_MAX:
            _run_record_cache.pop(next(iter(_run_record_cache)))
    return record


def _merge_ledger(rows: list[dict]) -> list[dict]:
    """Raw ledger.jsonl lines -> merged entries (grade attached),
    the same merge LedgerStore performs: entry lines carry entry_id;
    grade lines are {"record_type": "grade", "entry_id", "grade"}."""
    entries: dict[str, dict] = {}
    order: list[str] = []
    for row in rows:
        entry_id = row.get("entry_id")
        if not isinstance(entry_id, str) or not entry_id:
            continue
        if row.get("record_type") == "grade":
            if entry_id in entries and isinstance(row.get("grade"), dict):
                entries[entry_id]["grade"] = row["grade"]
            continue
        if entry_id not in entries:
            order.append(entry_id)
        entries[entry_id] = {**row, **({"grade": entries[entry_id]["grade"]}
                                        if entry_id in entries
                                        and "grade" in entries[entry_id] else {})}
    return [entries[eid] for eid in order]


def gather_facts(*, events_path=None, ledger_path=None, runs_dir=None,
                 graph_path=None, health=None) -> dict:
    """Build a facts snapshot from the served sources. Every source
    is optional and corrupt-tolerant: a missing or malformed source
    contributes an empty list, never an exception."""
    facts: dict = {"health": list(health or []), "events": [],
                   "runs": [], "ledger_entries": [], "node_modes": {}}
    if events_path is not None:
        facts["events"] = _read_jsonl(Path(events_path))
    if ledger_path is not None:
        facts["ledger_entries"] = _merge_ledger(_read_jsonl(Path(ledger_path)))
    if runs_dir is not None:
        runs_dir = Path(runs_dir)
        if runs_dir.exists():
            for path in sorted(runs_dir.glob("*.json")):
                record = _read_run_record(path)
                if record is not None:
                    facts["runs"].append(record)
    if graph_path is not None and Path(graph_path).exists():
        try:
            graph = json.loads(Path(graph_path).read_text(encoding="utf-8"))
            for node in graph.get("nodes", []):
                if isinstance(node, dict) and node.get("id"):
                    facts["node_modes"][node["id"]] = node.get("mode")
        except (ValueError, OSError, UnicodeDecodeError):
            pass
    return facts


# -- matching -----------------------------------------------------------------

def _adapter_matches(params: dict, health: list[dict]) -> list[dict]:
    adapter, match, state = (params.get("adapter"), params.get("match", "state_is"),
                             params.get("state"))
    entries = [h for h in health
               if adapter is None or h.get("adapter") == adapter]
    if match == "state_is":
        return [h for h in entries if h.get("state") == state]
    return [h for h in entries if h.get("state") != state]


def _event_matches(params: dict, ev: dict, node_modes: dict) -> bool:
    sev = ev.get("severity")
    if not isinstance(sev, int) or isinstance(sev, bool):
        return False
    if sev < int(params.get("min_severity", 1)):
        return False
    if params.get("node_id") is not None and ev.get("node_id") != params["node_id"]:
        return False
    if params.get("node_class") is not None \
            and node_modes.get(ev.get("node_id")) != params["node_class"]:
        return False
    if params.get("kind") is not None and ev.get("kind") != params["kind"]:
        return False
    return True


def _run_metric(record: dict, metric: str, node_id: str | None) -> float | None:
    delays = []
    for ev in record.get("events") or []:
        if not isinstance(ev, dict):
            continue
        if metric == "node_delay_d" and ev.get("node_id") != node_id:
            continue
        delay = ev.get("delay_d")
        if isinstance(delay, (int, float)) and not isinstance(delay, bool):
            delays.append(float(delay))
    if metric == "node_delay_d":
        return max(delays) if delays else None
    return max(delays) if delays else 0.0


def _fmt_d(value: float) -> str:
    return f"{value:.1f} d"


# -- evaluation -----------------------------------------------------------------

def _empty_state() -> dict:
    return {"rules": {}, "processed_event_ids": [], "processed_run_ids": [],
            "processed_ledger_entry_ids": [], "graded_ledger_entry_ids": []}


def _state_sets(state: dict) -> dict:
    return {
        "events": set(state.get("processed_event_ids") or []),
        "runs": set(state.get("processed_run_ids") or []),
        "ledger": set(state.get("processed_ledger_entry_ids") or []),
        "graded": set(state.get("graded_ledger_entry_ids") or []),
    }


def _sync_adapter_state(state: dict, rule: dict, condition: bool) -> None:
    state.setdefault("rules", {})[rule["id"]] = {"condition_true": bool(condition)}


def prime(store: AlertsStore, facts: dict) -> dict:
    """prime is the same load->save read-modify-write shape as
    evaluate, so it takes the same cross-instance flock (see evaluate)."""
    with store.locked():
        return _prime_locked(store, facts)


def _prime_locked(store: AlertsStore, facts: dict) -> dict:
    """Mark the current facts snapshot as processed WITHOUT firing,
    and sync every adapter_state rule's condition memory to the
    current board. Called when a rule is created so the rule only
    ever fires on facts that arrive after it exists."""
    state = store.load_state() or _empty_state()
    sets = _state_sets(state)
    sets["events"] |= {ev.get("id") for ev in facts.get("events", []) if ev.get("id")}
    sets["runs"] |= {r.get("run_id") for r in facts.get("runs", []) if r.get("run_id")}
    for entry in facts.get("ledger_entries", []):
        eid = entry.get("entry_id")
        if eid:
            sets["ledger"].add(eid)
            if entry.get("grade") is not None:
                sets["graded"].add(eid)
    for rule in store.list_rules():
        if rule.get("type") == "adapter_state":
            matched = _adapter_matches(rule.get("params") or {},
                                         facts.get("health", []))
            _sync_adapter_state(state, rule, bool(matched))
    _write_sets(state, sets)
    store.save_state(state)
    return state


def _write_sets(state: dict, sets: dict) -> None:
    state["processed_event_ids"] = sorted(sets["events"])
    state["processed_run_ids"] = sorted(sets["runs"])
    state["processed_ledger_entry_ids"] = sorted(sets["ledger"])
    state["graded_ledger_entry_ids"] = sorted(sets["graded"])


def evaluate(store: AlertsStore, facts: dict, now: str | None = None) -> list[dict]:
    """The whole load->fire->save cycle runs under the store's
    cross-instance flock. The API and the adapters/run_once.py cycle
    evaluate through SEPARATE AlertsStore instances on the same
    directory; unguarded, two concurrent passes could see the same
    unprocessed facts and fire the same event twice (and could crash
    sharing the deterministic state temp file). With no rules at all
    this stays a pure no-op — the cheap rules pre-check runs before
    the lock is taken, and the locked pass re-checks."""
    if not store.list_rules():
        return []
    with store.locked():
        return _evaluate_locked(store, facts, now=now)


def _evaluate_locked(store: AlertsStore, facts: dict,
                     now: str | None = None) -> list[dict]:
    """Run one evaluation pass. Returns the fired alert records (also
    appended to the store's fired feed). With no rules at all this is
    a pure no-op — no state is written — so wiring it into hot paths
    (run completion) costs nothing until a user creates a rule."""
    rules = store.list_rules()
    if not rules:
        return []
    fired_at = now or now_iso()
    state = store.load_state() or _empty_state()
    sets = _state_sets(state)
    node_modes = facts.get("node_modes", {})
    health = facts.get("health", [])
    fired: list[dict] = []

    def fire(rule: dict, fact: dict, message: str) -> None:
        record = {"id": new_id("alf"), "rule_id": rule["id"],
                  "rule_name": rule.get("name"), "type": rule.get("type"),
                  "fired_at": fired_at, "fact": fact, "message": message}
        store.append_fired(record)
        fired.append(record)

    new_events = [ev for ev in facts.get("events", [])
                  if ev.get("id") and ev["id"] not in sets["events"]]
    new_runs = [r for r in facts.get("runs", [])
                if r.get("run_id") and r["run_id"] not in sets["runs"]]
    new_entries = [e for e in facts.get("ledger_entries", [])
                   if e.get("entry_id") and e["entry_id"] not in sets["ledger"]]
    new_grades = [e for e in facts.get("ledger_entries", [])
                  if e.get("entry_id") and e.get("grade") is not None
                  and e["entry_id"] not in sets["graded"]]

    for rule in rules:
        params = rule.get("params") or {}
        rtype = rule.get("type")
        enabled = rule.get("enabled", True) is True
        if rtype == "adapter_state":
            matched = _adapter_matches(params, health)
            condition = bool(matched)
            prev = bool((state.get("rules") or {}).get(rule["id"], {})
                        .get("condition_true", False))
            if enabled and condition and not prev:
                names = [str(h.get("adapter")) for h in matched]
                fire(rule,
                     {"adapters": names, "state": params.get("state"),
                      "match": params.get("match", "state_is"),
                      "states": {h.get("adapter"): h.get("state") for h in matched}},
                     f"Adapter state condition met: {', '.join(names)} "
                     f"{'is' if params.get('match', 'state_is') == 'state_is' else 'is not'} "
                     f"{params.get('state')}")
            _sync_adapter_state(state, rule, condition)
        elif not enabled:
            continue
        elif rtype == "event":
            for ev in new_events:
                if _event_matches(params, ev, node_modes):
                    fire(rule,
                         {"event_id": ev.get("id"), "node_id": ev.get("node_id"),
                          "kind": ev.get("kind"), "severity": ev.get("severity"),
                          "status": ev.get("status"),
                          "detected_at": ev.get("detected_at")},
                         f"Disruption {ev.get('id')} severity {ev.get('severity')} "
                         f"at {ev.get('node_id')} ({ev.get('kind')})")
        elif rtype == "run_outcome":
            for record in new_runs:
                value = _run_metric(record, params.get("metric", "worst_delay_d"),
                                     params.get("node_id"))
                threshold = float(params.get("threshold_d", 0.0))
                if value is not None and value > threshold:
                    if params.get("metric") == "node_delay_d":
                        message = (f"Run {record['run_id']} delay at "
                                   f"{params.get('node_id')} {_fmt_d(value)} "
                                   f"exceeds threshold {_fmt_d(threshold)}")
                    else:
                        message = (f"Run {record['run_id']} worst added transit "
                                   f"{_fmt_d(value)} exceeds threshold "
                                   f"{_fmt_d(threshold)}")
                    fire(rule,
                         {"run_id": record["run_id"], "metric": params.get("metric"),
                          "node_id": params.get("node_id"), "value_d": value,
                          "threshold_d": threshold},
                         message)
        elif rtype == "ledger":
            if params.get("event") == "lock":
                for entry in new_entries:
                    fire(rule,
                         {"entry_id": entry.get("entry_id"),
                          "disruption_id": entry.get("disruption_id"),
                          "run_id": entry.get("run_id"),
                          "locked_at": entry.get("locked_at")},
                         f"Ledger lock {entry.get('entry_id')} recorded for "
                         f"disruption {entry.get('disruption_id')}")
            elif params.get("event") == "grade":
                for entry in new_grades:
                    grade = entry.get("grade") or {}
                    fire(rule,
                         {"entry_id": entry.get("entry_id"),
                          "disruption_id": entry.get("disruption_id"),
                          "run_id": entry.get("run_id"),
                          "graded_at": grade.get("graded_at"),
                          "delay_error_d": grade.get("delay_error_d")},
                         f"Ledger grade recorded for {entry.get('entry_id')}")

    sets["events"] |= {ev["id"] for ev in new_events}
    sets["runs"] |= {r["run_id"] for r in new_runs}
    sets["ledger"] |= {e["entry_id"] for e in new_entries}
    sets["graded"] |= {e["entry_id"] for e in new_grades}
    _write_sets(state, sets)
    store.save_state(state)
    return fired
