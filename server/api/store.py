"""Run store: JSON files under data/runs/, one per run.

File layout: data/runs/<run_id>.json =
  {run_id, status, event_count, stream_hash, journal, events, impacts}

The JOURNAL is complete: {kind, graph_hash, graph_version, disruption
(effective spec), params, node_overrides, + for forks: parent_run_id,
at_seq, overrides, parent_journal}. A run replays from its journal alone
(replay_journal below) and must reproduce the identical stream_hash —
the replay is byte-identical.

run_id is deterministic: "run_" + sha256(canonical journal core)[:16].
Identical inputs therefore yield the identical run (idempotent POST /run,
refork identity), which is what makes the hash guarantees checkable.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from engine.canonical import canonical_json, sha256_hex, stream_hash
from engine.engine import Engine, RunState
from engine.fork import fork_events


class StoreFull(RuntimeError):
    """The run store holds ``SUPERMAP_RUNS_MAX`` runs: a NEW run is refused
    (the API answers 507 ``store_full``). Existing runs stay readable and an
    existing id can be re-saved."""


DEFAULT_RUNS_MAX = 5000


def runs_max() -> int:
    """Most stored chain runs (SUPERMAP_RUNS_MAX). Each distinct POST /run or
    /fork adds a ~5 KB file that is kept for ever, and /runs, /llm/ask and the
    report centre read every file per call."""
    try:
        return max(1, int(os.environ.get("SUPERMAP_RUNS_MAX", DEFAULT_RUNS_MAX)))
    except ValueError:
        return DEFAULT_RUNS_MAX


def derive_run_id(journal_core: dict) -> str:
    return "run_" + sha256_hex(canonical_json(journal_core))[:16]


class RunStore:
    def __init__(self, runs_dir: str | Path):
        self.dir = Path(runs_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        # A run_id is an opaque id, never a path. Anything that could
        # traverse (separators, "..", NUL, empty) is a bad run_id.
        if (not isinstance(run_id, str) or not run_id
                or "/" in run_id or "\\" in run_id or ".." in run_id
                or "\x00" in run_id):
            raise ValueError("bad run_id")
        p = self.dir / f"{run_id}.json"
        # Containment backstop: the resolved file must sit directly in
        # the runs dir, whatever the id contained.
        if p.resolve().parent != self.dir.resolve():
            raise ValueError("bad run_id")
        return p

    def _admit(self) -> None:
        """Raise StoreFull when a new run would exceed the quota."""
        limit = runs_max()
        n = 0
        with os.scandir(self.dir) as it:
            for entry in it:
                if entry.name.endswith(".json") and not entry.name.startswith("."):
                    n += 1
                    if n >= limit:
                        raise StoreFull(
                            f"the run store holds {limit} runs, its limit; "
                            "existing runs are still served")

    def exists(self, run_id: str) -> bool:
        # Read-side lookups never raise on a bad id: an id that
        # cannot name a stored run simply does not exist. OSError covers
        # e.g. an over-long id whose filename the OS rejects outright.
        try:
            return self._path(run_id).exists()
        except (ValueError, OSError):
            return False

    def save(self, run_id: str, journal: dict, state: RunState) -> dict:
        record = {
            "run_id": run_id,
            "status": "complete",
            "event_count": len(state.events),
            "stream_hash": stream_hash(state.events),
            "journal": journal,
            "events": state.events,
            "impacts": {str(k): v for k, v in state.impacts.items()},
            # WHYNOT (additive sibling key; stream_hash covers events
            # only, so stored hashes are unchanged by its presence).
            "not_hit": getattr(state, "not_hit", []),
        }
        # Per-writer unique temp name (pid + uuid): a shared
        # deterministic .json.tmp would let concurrent writers consume each
        # other's temp file (FileNotFoundError on replace).
        _path = self._path(run_id)
        if not _path.exists():
            self._admit()
        tmp = _path.with_name(f"{_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(record, fh, sort_keys=True)
        tmp.replace(self._path(run_id))
        return record

    def load(self, run_id: str) -> dict | None:
        # Read-side lookups never raise on a bad id: a traversal
        # or otherwise unusable id resolves to "no such run" (None),
        # which the API maps to the standard 404 error shape — never a
        # bare 500 from an unhandled ValueError.
        try:
            p = self._path(run_id)
        except (ValueError, OSError):
            return None
        try:
            if not p.exists():
                return None
        except OSError:
            return None
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)


def state_from_record(record: dict) -> RunState:
    state = RunState()
    state.events = record["events"]
    state.impacts = {int(k): v for k, v in record["impacts"].items()}
    state.emitted = {(ev["node_id"], ev["channel"]) for ev in record["events"]}
    return state


def replay_journal(engine: Engine, journal: dict, run_id: str) -> RunState:
    """Rebuild a run's event stream from its journal alone (no store reads).
    A journal stamped engine_rev=2 replays under ENGINE REV 2."""
    if journal.get("engine_rev") == 2:
        from engine2.journal import get_engine2, replay_journal2
        return replay_journal2(get_engine2(engine.full_graph), journal, run_id)
    if journal["kind"] == "run":
        return engine.run(run_id, journal["disruption"], journal["params"],
                          journal.get("node_overrides") or {})
    if journal["kind"] == "fork":
        parent = replay_journal(engine, journal["parent_journal"],
                                journal["parent_run_id"])
        return fork_events(engine, parent, journal["parent_journal"]["disruption"],
                           journal["parent_journal"]["params"], journal["at_seq"],
                           journal["overrides"], run_id)
    raise ValueError(f"unknown journal kind: {journal['kind']}")
