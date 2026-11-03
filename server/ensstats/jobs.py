"""Ensemble-statistics job manager.

Lifecycle: create() resolves the axes against the validated
disruption (engine core rules), derives the deterministic job id
from the canonical request core (identical request -> same job,
idempotent re-create returns the existing job, mirroring the sim
store), persists a "running" record, and computes on the family's
bounded worker pool (JOB_POOL_WORKERS daemon threads plus a
bounded queue — never one thread per job; a create beyond the bound
is refused with EngineError code="rate_limited"). The worker
aggregates INCREMENTALLY (flat memory) and
persists the completed record with per-variant compact records,
aggregate results, and the per-branch view. A persisted "running"
record with no live worker in this process reads back as
"interrupted" — a job is never claimed to be running when it is
not.

Separation: variant runs are computed in memory and are NEVER saved
to the run store; nothing here reads or writes the Ledger or the
scorecard. The drill-down endpoint re-derives a variant's full
causal chain by replay (the engine is deterministic, so the replay
is exact and its stream hash is verified against the stored record
before the chain is served).
"""
from __future__ import annotations

import os
import queue
import threading
from datetime import datetime, timezone

from .store import EnsembleStatsStore

try:  # dual-import convention, as in server/api/app.py
    from engine import ensemble_stats as es
    from engine.canonical import canonical_json, sha256_hex, stream_hash
    from engine.engine import Engine, EngineError
except Exception:  # imported as server.ensstats.* from the repo root
    from server.engine import ensemble_stats as es  # type: ignore
    from server.engine.canonical import canonical_json, sha256_hex, stream_hash  # type: ignore
    from server.engine.engine import Engine, EngineError  # type: ignore


# Time cap: MAX_VARIANTS alone allows 10 000 rev-1 runs, minutes to hours on a large
# cascade. A job is refused when its quote (planned variants x S_PER_VARIANT) is over the cap. The
# per-variant figure is the worst rev-1 run measured for Monte Carlo (montecarlo.jobs.DEFAULT_S_PER_RUN).
S_PER_VARIANT = 0.35
DEFAULT_MAX_SECONDS = 900.0


def _max_seconds() -> float:
    try:
        return max(1.0, float(os.environ.get("SUPERMAP_ENS_MAX_SECONDS", DEFAULT_MAX_SECONDS)))
    except ValueError:
        return DEFAULT_MAX_SECONDS


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def derive_job_id(core: dict) -> str:
    return "ens_" + sha256_hex(canonical_json(core))[:16]


# Job execution runs on a BOUNDED worker pool,
# never one thread per job. Each runner family owns one pool: a fixed
# number of worker threads plus a bounded number of queued jobs; a
# submission beyond that bound is refused (the managers raise
# EngineError code="rate_limited", which the API maps to HTTP 429 in
# the standard error envelope) instead of growing threads.
JOB_POOL_WORKERS = 2
JOB_POOL_QUEUE = 8


class BoundedJobPool:
    """Fixed-size worker pool with a bounded outstanding-job count.

    reserve() claims one of (workers + queue) slots without blocking
    and returns False when the pool is saturated; the caller that
    claimed a slot hands the callable to submit_reserved(). A slot is
    released by the worker when the job finishes, so the number of
    live worker threads never grows with the number of submissions.
    """

    def __init__(self, workers: int = JOB_POOL_WORKERS,
                 queue_size: int = JOB_POOL_QUEUE, name: str = "jobpool"):
        self._slots = threading.BoundedSemaphore(workers + queue_size)
        self._queue: queue.Queue = queue.Queue()
        self._threads = []
        for i in range(workers):
            worker = threading.Thread(target=self._loop,
                                      name=f"{name}-{i}", daemon=True)
            worker.start()
            self._threads.append(worker)

    def _loop(self) -> None:
        while True:
            fn = self._queue.get()
            try:
                fn()
            finally:
                self._slots.release()
                self._queue.task_done()

    def reserve(self) -> bool:
        return self._slots.acquire(blocking=False)

    def submit_reserved(self, fn) -> None:
        self._queue.put(fn)

    def release(self) -> None:
        """Give back a reserved slot whose job was never submitted
        (registration failed after the reservation)."""
        self._slots.release()


class JobNotFound(Exception):
    pass


class JobNotReady(Exception):
    def __init__(self, record: dict):
        super().__init__("ensemble statistics job is not complete")
        self.record = record


class BranchNotFound(Exception):
    pass


class VariantNotFound(Exception):
    pass


class EnsembleStatsJobs:
    def __init__(self, engine: Engine, store: EnsembleStatsStore,
                 pool: BoundedJobPool | None = None, max_seconds: float | None = None):
        self.max_seconds = float(max_seconds) if max_seconds else _max_seconds()
        self._engine = engine
        self._store = store
        self._live: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._pool = pool if pool is not None else BoundedJobPool(
            name="ensstats-pool")

    # ---------------------------------------------------------- create

    def create(self, spec: dict, axes_requested: dict | None,
               count: int) -> dict:
        """Resolve, register and start a job; returns its summary.
        Raises EngineError (invalid_request / axis codes) on a bad
        request — the caller maps it to the standard error shape."""
        if isinstance(count, bool) or not isinstance(count, int) \
                or not (1 <= count <= es.MAX_VARIANTS):
            raise EngineError(
                f"count must be an integer in [1, {es.MAX_VARIANTS}]",
                code="invalid_request")
        axes, notes = es.resolve_axes(spec, axes_requested)
        total = es.grid_size(axes)
        planned = min(count, total)
        quote = planned * S_PER_VARIANT
        if quote > self.max_seconds:
            fit = int(self.max_seconds / S_PER_VARIANT)
            raise EngineError(
                f"estimated {quote:.0f} s for {planned} variants is above the cap of "
                f"{self.max_seconds:g} s (SUPERMAP_ENS_MAX_SECONDS); the largest job is about "
                f"{fit} variants", code="ensemble_stats_too_large")
        flat_indices = es.select_flat_indices(axes, count, spec["shape"])
        core = {"kind": "ensemble_stats",
                "graph_hash": self._engine.graph.hash,
                "disruption": spec, "axes": axes, "count": count}
        job_id = derive_job_id(core)
        with self._lock:
            live = self._live.get(job_id)
            if live is not None:
                return self.summary(live)
            stored = self._store.load(job_id)
            if stored is not None and stored.get("status") == "complete":
                return self.summary(stored)
            # Any other stored record under this id is a corpse (a
            # previous process died mid-job, or the job failed): rerun
            # under the same deterministic id rather than serving it.
            # A NEW job needs a pool slot BEFORE it is
            # registered — duplicates above return the existing job
            # and never consume one, so they are never refused.
            if not self._pool.reserve():
                raise EngineError(
                    "ensemble statistics job queue is full "
                    f"({JOB_POOL_WORKERS} running, {JOB_POOL_QUEUE} "
                    "queued); retry once a running job completes",
                    code="rate_limited")
            count_note = None
            if count > total:
                count_note = (
                    f"the declared axes define {total} distinct "
                    f"variants; {count} were requested, so the full "
                    f"set of {total} runs — combinations are never "
                    f"repeated to reach a count")
            record = {
                "id": job_id,
                "kind": "ensemble_stats",
                "status": "running",
                "created_at": _now_iso(),
                "completed_at": None,
                "error": None,
                "graph_hash": self._engine.graph.hash,
                "served_graph_hash": self._engine.full_graph.hash,
                "graph_version": self._engine.graph.version,
                "disruption": spec,
                "axes": axes,
                "axes_notes": notes,
                "grid_size": total,
                "variants_requested": count,
                "variants_planned": planned,
                "count_note": count_note,
                "flat_indices": flat_indices,
                "progress": {"done": 0, "planned": planned},
                "variants": [],
                "results": None,
                "branches": None,
            }
            self._live[job_id] = record
            try:
                self._store.save(self._persistable(record))
            except Exception:
                del self._live[job_id]
                self._pool.release()
                raise
        self._pool.submit_reserved(lambda: self._run(job_id))
        return self.summary(record)

    # ------------------------------------------------------------ worker

    def _run(self, job_id: str) -> None:
        with self._lock:
            record = self._live[job_id]
        spec = record["disruption"]
        axes = record["axes"]
        flat_indices = record["flat_indices"]
        engine = Engine(self._engine.full_graph)
        acc = es.EnsembleAccumulator()
        variants: list[dict] = []
        try:
            for index, flat in enumerate(flat_indices):
                combo = es.combination_at(axes, flat)
                run_id = f"{job_id}_v{index}"
                state = engine.run(run_id, es.variant_spec(spec, combo),
                                   es.variant_params(combo))
                rec = es.variant_record(index, combo, run_id, state)
                acc.add({"outcomes": rec["outcomes"],
                         "places": rec.pop("_places"),
                         "legs": rec.pop("_legs")})
                variants.append(rec)
                with self._lock:
                    record["progress"] = {"done": index + 1,
                                           "planned": len(flat_indices)}
                    record["variants"] = variants
            nodes = engine.graph.nodes
            results = {
                "variants_run": acc.variants,
                "outcomes": acc.outcome_block(),
                "places_reached_any": len(acc.places),
                "legs_fired_any": len(acc.legs),
                "base_variant": {
                    "index": 0,
                    "combination": variants[0]["combination"],
                    "outcomes": variants[0]["outcomes"],
                    "stream_hash": variants[0]["stream_hash"],
                } if variants else None,
            }
            branches = {
                "places": acc.place_branches(nodes),
                "legs": acc.leg_branches(nodes),
            }
            with self._lock:
                record["status"] = "complete"
                record["completed_at"] = _now_iso()
                record["results"] = results
                record["branches"] = branches
                record["variants"] = variants
                self._store.save(self._persistable(record))
                # Evict the finished job from _live once it is
                # persisted — the on-disk store serves it from here on
                # (duplicate creates included), so _live does not grow
                # and a FAILED record does not pin the job.
                self._live.pop(job_id, None)
        except Exception as exc:  # a variant failure fails the job openly
            code = exc.code if isinstance(exc, EngineError) else "internal_error"
            with self._lock:
                record["status"] = "failed"
                record["completed_at"] = _now_iso()
                record["error"] = {"code": code, "message": str(exc)}
                self._store.save(self._persistable(record))
                # Evict on failure too — a re-POST of the same
                # request then finds only the stored failed record,
                # which create() treats as a corpse and RE-RUNS.
                self._live.pop(job_id, None)

    # ------------------------------------------------------------- reads

    def _persistable(self, record: dict) -> dict:
        return {k: v for k, v in record.items()}

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            live = self._live.get(job_id)
            if live is not None:
                return live
        stored = self._store.load(job_id)
        if stored is None:
            return None
        if stored.get("status") == "running":
            stored = {**stored, "status": "interrupted"}
        return stored

    def summary(self, record: dict) -> dict:
        return {
            "id": record["id"],
            "status": record["status"],
            "created_at": record["created_at"],
            "completed_at": record["completed_at"],
            "error": record["error"],
            "graph_hash": record["graph_hash"],
            "graph_version": record["graph_version"],
            "disruption": record["disruption"],
            "axes": es.axes_payload(record["axes"]),
            "axes_notes": record["axes_notes"],
            "grid_size": record["grid_size"],
            "variants_requested": record["variants_requested"],
            "variants_planned": record["variants_planned"],
            "count_note": record["count_note"],
            "progress": record["progress"],
            "labelling": es.LABELLING,
        }

    def list_summaries(self, limit: int = 50) -> list[dict]:
        records = []
        seen = set()
        with self._lock:
            live = list(self._live.values())
        for record in live:
            seen.add(record["id"])
            records.append(record)
        for job_id in self._store.list_ids():
            if job_id not in seen:
                stored = self.get(job_id)
                if stored is not None:
                    records.append(stored)
        records.sort(key=lambda r: r["created_at"], reverse=True)
        return [self.summary(r) for r in records[:limit]]

    def _complete_record(self, job_id: str) -> dict:
        record = self.get(job_id)
        if record is None:
            raise JobNotFound(job_id)
        if record["status"] != "complete":
            raise JobNotReady(record)
        return record

    def results(self, job_id: str) -> dict:
        record = self._complete_record(job_id)
        return {
            "id": job_id,
            "status": "complete",
            "completed_at": record["completed_at"],
            "disruption": record["disruption"],
            "axes": es.axes_payload(record["axes"]),
            "axes_notes": record["axes_notes"],
            "grid_size": record["grid_size"],
            "variants_requested": record["variants_requested"],
            "count_note": record["count_note"],
            "labelling": es.LABELLING,
            "definitions": es.DEFINITIONS,
            **record["results"],
        }

    def branches(self, job_id: str) -> dict:
        record = self._complete_record(job_id)
        return {
            "id": job_id,
            "status": "complete",
            "variants_run": record["results"]["variants_run"],
            "labelling": es.LABELLING,
            "definitions": {
                k: es.DEFINITIONS[k] for k in (
                    "place_delay_contribution_d", "place_total_delay_d",
                    "leg_delay_contribution_d", "fire_share", "percentiles")
            },
            **record["branches"],
        }

    def branch_detail(self, job_id: str, branch_key: str) -> dict:
        record = self._complete_record(job_id)
        branches = record["branches"]
        found = None
        for entry in branches["places"] + branches["legs"]:
            if entry["branch"] == branch_key:
                found = entry
                break
        if found is None:
            raise BranchNotFound(branch_key)
        indices = []
        for rec in record["variants"]:
            fired = rec["places_fired"] if branch_key.startswith("place:") \
                else rec["legs_fired"]
            key = branch_key.split(":", 1)[1]
            if key in fired:
                indices.append(rec["index"])
        return {
            "id": job_id,
            "branch": found,
            "variants_fired": indices,
            "variants_run": record["results"]["variants_run"],
            "labelling": es.LABELLING,
        }

    def variant(self, job_id: str, index: int) -> dict:
        record = self.get(job_id)
        if record is None:
            raise JobNotFound(job_id)
        if not (0 <= index < record["variants_planned"]):
            raise VariantNotFound(index)
        done = record["progress"]["done"] if record["status"] == "running" \
            else record["variants_planned"]
        if record["status"] in ("running", "interrupted") and index >= done:
            raise JobNotReady(record)
        if record["status"] == "failed":
            raise JobNotReady(record)
        stored = next((v for v in record["variants"] if v["index"] == index),
                      None)
        if stored is None:
            raise JobNotReady(record)
        combo, state = es.replay_variant(
            Engine(self._engine.full_graph), record["disruption"],
            record["axes"], record["flat_indices"], index, stored["run_id"])
        replay_hash = stream_hash(state.events)
        if replay_hash != stored["stream_hash"]:
            raise EngineError(
                "re-derived chain does not match the stored record "
                f"({replay_hash} != {stored['stream_hash']})",
                code="internal_error")
        return {"variant": stored, "combination": combo,
                "events": state.events}
