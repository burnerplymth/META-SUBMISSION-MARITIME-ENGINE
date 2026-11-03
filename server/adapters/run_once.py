"""One ingestion cycle: fetch every adapter, route records into the store.

    python3 -m server.adapters.run_once          (from repo root)
    SUPERMAP_BLOCK_NETWORK=1 python3 -m server.adapters.run_once

Routing: records that are DisruptionEvents (``evt_`` id) go to the
EventStore (validated + deduped into data/events.jsonl); everything else
goes to the RecordStore (data/records.jsonl) under its record_type.
Prints a JSON summary incl. the observed adapter health board. Exit code is 0
even when adapters are DOWN — a degraded board is a valid outcome, and
the summary says so truthfully.

Isolation: fetches run CONCURRENTLY (one worker per
adapter) under a per-adapter deadline (``SUPERMAP_ADAPTER_TIMEOUT_S``,
default 90 s), and routing stays sequential afterwards in registry
order, so the summary shape is unchanged. Diagnosis: the adapters'
own timeouts are 30–120 s per attempt, with retries and paged queries
on top; run SEQUENTIALLY with the network unreachable, one cycle's
fetches alone could take many minutes, and for that whole time the
cycle would share the serving process. A fetch
that exceeds its deadline is ABANDONED for this cycle (truthful
error entry + empty-snapshot discipline, exactly like an
AdapterError): the worker cannot be killed, but it is a fresh
adapter instance per cycle, holds only its own per-instance lock,
and ends at its own socket timeouts without blocking the cycle, the
next cycle, or the request path. Snapshot writes use per-writer
unique temp names (adapters/base.py), so an abandoned fetch saving
its pack cannot collide with a later cycle's save of the same pack.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import sys
from pathlib import Path

from .base import AdapterError
from .nodes import NodeIndex
from .registry import build_default_registry, health_report
try:
    from ..store.store import EventStore, ProvenanceError, RecordStore, ValidationError
except ImportError:
    from store.store import EventStore, ProvenanceError, RecordStore, ValidationError  # type: ignore

DEFAULT_ADAPTER_TIMEOUT_S = 90.0


def _adapter_timeout_s() -> float:
    try:
        value = float(os.environ.get(
            "SUPERMAP_ADAPTER_TIMEOUT_S", DEFAULT_ADAPTER_TIMEOUT_S))
    except ValueError:
        return DEFAULT_ADAPTER_TIMEOUT_S
    return value if value > 0 else DEFAULT_ADAPTER_TIMEOUT_S


def _fetch_all(adapters, timeout_s: float) -> dict:
    """Fetch every adapter concurrently -> {name: FetchResult}.

    AdapterError failures and deadline overruns are recorded on the
    returned ``errors`` dict ({name: message}) instead of raising;
    any OTHER exception re-raises, exactly as the old sequential
    cycle propagated unexpected errors."""
    results: dict = {}
    errors: dict = {}
    if not adapters:
        return results, errors
    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=len(adapters), thread_name_prefix="supermap-fetch")
    try:
        futures = {pool.submit(a.fetch): a for a in adapters}
        done, not_done = concurrent.futures.wait(
            futures, timeout=timeout_s)
        for fut in not_done:
            adapter = futures[fut]
            errors[adapter.name] = (
                f"fetch exceeded the {timeout_s:g}s per-adapter "
                "deadline (SUPERMAP_ADAPTER_TIMEOUT_S); abandoned for "
                "this cycle — the fetch continues in the background "
                "until its own socket timeouts end it")
        for fut in done:
            adapter = futures[fut]
            try:
                results[adapter.name] = fut.result()
            except AdapterError as exc:
                errors[adapter.name] = str(exc)
        return results, errors
    finally:
        # Never wait for abandoned fetches: shutdown(wait=True) would
        # re-serialise the cycle behind exactly the hang the deadline
        # exists to bound.
        pool.shutdown(wait=False)


def run_cycle(adapter_timeout_s: float | None = None) -> dict:
    build_default_registry()
    idx = NodeIndex.load()
    events = EventStore(known_node_ids=set(idx.nodes))
    records = RecordStore()
    summary: dict = {"adapters": {}}
    from .registry import all_adapters
    adapters = all_adapters()
    timeout_s = (adapter_timeout_s if adapter_timeout_s is not None
                 else _adapter_timeout_s())
    results, fetch_errors = _fetch_all(adapters, timeout_s)
    for adapter in adapters:
        entry = {"fetched": 0, "events_written": 0, "records_written": 0,
                 "rejected": 0, "error": None}
        if adapter.name in fetch_errors:
            exc = fetch_errors[adapter.name]
            entry["error"] = exc
            # First-ever failure: leave an explicit EMPTY last-good pack
            # (zero records + reason) so the kill-network demo can serve
            # labelled SNAPSHOT instead of nothing.
            adapter.write_empty_snapshot(
                f"no successful live fetch yet; last error: {exc}")
            summary["adapters"][adapter.name] = entry
            continue
        result = results[adapter.name]
        entry["fetched"] = len(result.records)
        entry["mode"] = result.provenance["mode"]
        c3_events, other = [], []
        for rec in result.records:
            (c3_events if str(rec.get("id", "")).startswith("evt_") else other).append(rec)
        for ev in c3_events:
            try:
                if events.append_event(ev):
                    entry["events_written"] += 1
            except (ProvenanceError, ValidationError) as exc:
                entry["rejected"] += 1
                entry.setdefault("rejections", []).append(str(exc))
        if other:
            from collections import defaultdict
            by_type: dict[str, list] = defaultdict(list)
            for rec in other:
                by_type[rec.get("record_type", adapter.name)].append(rec)
            for rtype, group in by_type.items():
                try:
                    entry["records_written"] += records.append_records(
                        rtype, group, result.provenance)
                except ProvenanceError as exc:
                    entry["rejected"] += len(group)
                    entry["error"] = str(exc)
        summary["adapters"][adapter.name] = entry
    summary["money_probe"] = apply_money_health_probe()
    summary["health"] = health_report()
    summary["events_total"] = len(events)
    summary["autolock"] = apply_autolock(events)
    summary["ledger_cycle"] = apply_ledger_cycle(events)
    summary["alerts"] = apply_alerts(events, summary["health"])
    summary["backup"] = apply_backup(events)
    return summary


MONEY_PROBE_TIMEOUT_S = 20.0
EDGAR_PROBE_CIK = 1654126   # ZIM Integrated Shipping Services (20-F filer)


def _money_probes() -> dict:
    """{health-only adapter name: zero-arg callable doing ONE cheap live
    request}. The result of a probe is discarded: the money layer is
    quarantined (nothing is stored, nothing reaches the engine) — the
    probe exists only so the health board states the adapter's real,
    observed condition instead of a permanent never-run DOWN."""
    def frankfurter(adapter):
        adapter.fetch_latest(symbols=("EUR",))

    def stooq(adapter):
        adapter.fetch_history("ZIM")

    def edgar(adapter):
        try:
            adapter.submissions(EDGAR_PROBE_CIK)
        except Exception as exc:
            if type(exc).__name__ == "MissingEdgarUserAgentError":
                adapter._note = (
                    "NEEDS-CONFIG: set SUPERMAP_EDGAR_UA to a declared "
                    "User-Agent with the operator's real contact (SEC "
                    "fair access); no request was made")
            raise
    return {"frankfurter": frankfurter, "stooq": stooq, "edgar": edgar}


def apply_money_health_probe(timeout_s: float = MONEY_PROBE_TIMEOUT_S) -> dict:
    """Cycle-end hook: the money adapters are
    health-only registrations that only change state when something calls
    them, and ``build_default_registry()`` makes fresh instances every
    cycle — so without this they read DOWN forever. One cheap keyless
    probe per adapter (concurrent, bounded by ``timeout_s``) so the board
    reports the observed condition with the true failure in the note.
    Behind SUPERMAP_MONEY_PROBE (default ON, =0 disables); skipped under
    SUPERMAP_BLOCK_NETWORK. Failures are reported, never raised."""
    if os.environ.get("SUPERMAP_MONEY_PROBE", "1") == "0":
        return {"enabled": False}
    if os.environ.get("SUPERMAP_BLOCK_NETWORK") == "1":
        return {"enabled": True, "skipped": "network blocked", "results": {}}
    from . import registry as _registry
    jobs = {}
    for name, probe in _money_probes().items():
        try:
            jobs[name] = (_registry.get(name), probe)
        except KeyError:
            continue
    results: dict = {}
    if not jobs:
        return {"enabled": True, "results": results}
    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=len(jobs), thread_name_prefix="supermap-money-probe")
    try:
        futures = {pool.submit(probe, adapter): name
                   for name, (adapter, probe) in jobs.items()}
        done, not_done = concurrent.futures.wait(futures, timeout=timeout_s)
        for fut in not_done:
            results[futures[fut]] = f"probe exceeded {timeout_s:g}s; abandoned"
        for fut in done:
            name = futures[fut]
            try:
                fut.result()
                results[name] = "ok"
            except Exception as exc:
                results[name] = f"{type(exc).__name__}"
    finally:
        pool.shutdown(wait=False)
    return {"enabled": True, "results": results}


def apply_backup(events) -> dict:
    """Cycle-end hook: off-host snapshot of the critical stores
    (server/backup/snapshot.py). Env-gated + throttled inside
    apply_backup itself; a backup failure never breaks the cycle —
    it is reported in the summary, like the hooks above."""
    try:
        try:
            from ..backup.snapshot import apply_backup as _apply_backup
        except ImportError:
            from backup.snapshot import apply_backup as _apply_backup  # type: ignore
        return _apply_backup(data_dir=events.path.parent)
    except Exception as exc:
        return {"enabled": None, "error": f"backup failed: {exc}",
                "snapshot": None}


def apply_alerts(events, health) -> dict:
    """Cycle-end hook: alert-rule evaluation (server/alerts), run
    after autolock so ledger rules observe this cycle's locks in the
    same pass. Facts are the cycle's own: the adapter health board,
    the full event store, the run store, the ledger, the graph —
    read from the same data directory the event store lives in.
    Behind SUPERMAP_ALERTS (default ON, =0 disables), mirroring
    SUPERMAP_AUTOLOCK. A failure here never breaks the ingestion
    cycle: it is reported in the summary instead."""
    if os.environ.get("SUPERMAP_ALERTS", "1") == "0":
        print("[alerts] disabled by SUPERMAP_ALERTS=0; no rules evaluated")
        return {"enabled": False, "fired_count": 0, "fired": []}
    try:
        try:
            from ..alerts.evaluator import evaluate, gather_facts
            from ..alerts.store import AlertsStore
        except ImportError:
            from alerts.evaluator import evaluate, gather_facts  # type: ignore
            from alerts.store import AlertsStore  # type: ignore
    except Exception as exc:  # pragma: no cover - import failure path
        print(f"[alerts] unavailable: {exc}")
        return {"enabled": None, "error": f"alerts unavailable: {exc}",
                "fired_count": 0, "fired": []}
    data_dir = events.path.parent
    try:
        store = AlertsStore(data_dir / "alerts")
        facts = gather_facts(
            events_path=events.path,
            ledger_path=data_dir / "ledger.jsonl",
            runs_dir=Path(os.environ.get(
                "SUPERMAP_RUNS_DIR", data_dir / "runs")),
            graph_path=data_dir / "graph" / "graph.json",
            health=health)
        fired = evaluate(store, facts)
        for record in fired:
            print(f"[alerts] fired {record['id']} "
                  f"(rule {record['rule_id']}): {record['message']}")
        return {"enabled": True, "fired_count": len(fired),
                "fired": [r["id"] for r in fired]}
    except Exception as exc:
        print(f"[alerts] failed: {exc}")
        return {"enabled": True, "error": str(exc),
                "fired_count": 0, "fired": []}


# Judgements the autolock cycle has already paid for, kept between cycles so a
# low-confidence event is not classified (and billed) again every 15 minutes.
_AUTOLOCK_MEMO: dict = {}


def apply_autolock(events) -> dict:
    """Cycle-end hook: ledger auto-lock (server/llm/autolock.py,
    policy autolock-v1). Behind SUPERMAP_AUTOLOCK (default OFF, =1 enables).
    Every decision is printed by run_autolock's logger and returned in the
    cycle summary — no silent locking. A failure here never breaks the
    ingestion cycle: it is reported in the summary instead."""
    if os.environ.get("SUPERMAP_LEDGER_AUTOLOCK", "0").strip() == "1":
        # The rate-limited ledger cycle (apply_ledger_cycle) owns locking
        # while its switch is on; running both would bypass its daily cap.
        return {"enabled": None, "superseded_by": "ledger_cycle",
                "locked_count": 0, "decisions": []}
    try:
        try:
            from ..llm.autolock import autolock_enabled, run_autolock
        except ImportError:
            from llm.autolock import autolock_enabled, run_autolock  # type: ignore
    except Exception as exc:  # pragma: no cover - import failure path
        print(f"[autolock] unavailable: {exc}")
        return {"enabled": None, "error": f"autolock unavailable: {exc}",
                "locked_count": 0, "decisions": []}
    if not autolock_enabled():
        print("[autolock] off (set SUPERMAP_AUTOLOCK=1 to enable); no events evaluated")
        return {"enabled": False, "locked_count": 0, "decisions": []}
    try:
        return run_autolock(
            events.read_all(),
            ledger_path=events.path.parent / "ledger.jsonl",
            runs_dir=Path(os.environ.get(
                "SUPERMAP_RUNS_DIR", events.path.parent / "runs")),
            memo=_AUTOLOCK_MEMO, logger=print)
    except Exception as exc:
        print(f"[autolock] failed: {exc}")
        return {"enabled": True, "error": str(exc),
                "locked_count": 0, "decisions": []}


def apply_ledger_cycle(events) -> dict:
    """Cycle-end hook: the rate-limited Ledger auto-lock + auto-grade cycle
    (server/llm/ledger_jobs.py). OFF by default; SUPERMAP_LEDGER_AUTOLOCK=1
    turns it on. Runs on the ingestion executor (never the event loop),
    single-flight, bounded (daily lock cap, grades per cycle, the history
    layer's request budget). Failures are reported in the summary and never
    break the ingestion cycle."""
    if os.environ.get("SUPERMAP_LEDGER_AUTOLOCK", "0").strip() != "1":
        return {"enabled": False}
    try:
        try:
            from ..llm.autograde import PortWatchEvidence
            from ..llm.ledger_jobs import run_ledger_cycle
            from ..history.service import HistoryService
        except ImportError:
            from llm.autograde import PortWatchEvidence  # type: ignore
            from llm.ledger_jobs import run_ledger_cycle  # type: ignore
            from history.service import HistoryService  # type: ignore
        data_dir = events.path.parent
        evidence = PortWatchEvidence(HistoryService(data_dir=data_dir))
        return run_ledger_cycle(
            events.read_all(), evidence,
            ledger_path=data_dir / "ledger.jsonl",
            runs_dir=Path(os.environ.get("SUPERMAP_RUNS_DIR", data_dir / "runs")),
            logger=print)
    except Exception as exc:
        print(f"[ledger-cycle] failed: {exc}")
        return {"enabled": True, "error": str(exc)}


if __name__ == "__main__":
    print(json.dumps(run_cycle(), indent=1))
    sys.exit(0)
