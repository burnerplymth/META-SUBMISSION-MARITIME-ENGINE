"""Ensemble-statistics TREE job manager.

The tree counterpart of server/ensstats/jobs.py, with the same
discipline: create() resolves the axes against the validated
disruption (engine core rules), quotes the full tree BEFORE anything
is persisted (a tree that is not launchable under its budget is
refused outright, never started), derives the deterministic job id
from the canonical request core (identical request -> same job,
idempotent re-create returns the existing job), persists a
"running" meta record, and computes on this family's bounded
worker pool (see ensstats/jobs.py BoundedJobPool — never
one thread per job; a create beyond the bound is refused with
EngineError code="rate_limited"). The worker
runs the whole tree in ONE engine.ensemble_tree.build_tree call —
every tree member is run through the same es.variant_spec /
variant_params / variant_record machinery the flat jobs use — and
publishes the tree members at completion (during the run only the
meta progress counters move; a member is served whole or not at
all, never half-built). A persisted "running" meta record with no
live worker in this process reads back as "interrupted" — a job is
never claimed to be running when it is not.

Storage is segmented (server/ensstats/tree_store.py): the job
record lives in meta.json (no member rows in it), the full stored
variant records live in per-level segment files, the light tree
entries in per-level nodes files, and each expanded member's
aggregates in its own aggregates file — so serving one member's
aggregates never loads the whole tree's outcome vectors.

Separation: tree jobs get their OWN store directory (the flat jobs'
store never sees a tree record, and vice versa). Variant runs are
computed in memory and are NEVER saved to the run store; nothing
here reads or writes the Ledger or the scorecard. The drill-down
endpoints re-derive a member's full causal chain — or, for a part
of the tree whose aggregates were not stored, its aggregates — by
replay (the engine is deterministic, so the replay is exact and
its stream hash is verified against the stored record before
anything derived from it is served).
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from .jobs import (JOB_POOL_QUEUE, JOB_POOL_WORKERS, BoundedJobPool,
                   derive_job_id)
from .tree_store import TreeJobStore

try:  # dual-import convention, as in server/api/app.py
    from engine import ensemble_stats as es
    from engine import ensemble_tree as et
    from engine.canonical import stream_hash
    from engine.engine import Engine, EngineError
except Exception:  # imported as server.ensstats.* from the repo root
    from server.engine import ensemble_stats as es  # type: ignore
    from server.engine import ensemble_tree as et  # type: ignore
    from server.engine.canonical import stream_hash  # type: ignore
    from server.engine.engine import Engine, EngineError  # type: ignore


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TreeJobNotFound(Exception):
    pass


class TreeJobNotReady(Exception):
    def __init__(self, record: dict):
        super().__init__("ensemble tree job is not complete")
        self.record = record


class TreeNodeNotFound(Exception):
    pass


class TreeVariantNotFound(Exception):
    pass


def _check_param_types(branching, depth, budget) -> None:
    """Branching / depth / budget type discipline, before the core
    validators: branching must be None, an integer, or a list/tuple
    of integers; depth and budget must be None or integers. JSON
    numbers that are not integers (2.5, and 2.0 alike), strings and
    bools are REJECTED with invalid_request, never coerced — the
    same discipline jobs.py applies to the flat jobs' count.
    et.validate_tree_params / et.validate_budget then apply the
    core's own bounds."""
    if branching is not None:
        if isinstance(branching, bool):
            raise es.EnsembleStatsError(
                f"branching must be an integer or a list of integers, "
                f"got bool: {branching!r:.80}", code="invalid_request")
        if isinstance(branching, (list, tuple)):
            for value in branching:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise es.EnsembleStatsError(
                        f"branching entries must be integers, got "
                        f"{type(value).__name__}: {value!r:.80}",
                        code="invalid_request")
        elif not isinstance(branching, int):
            raise es.EnsembleStatsError(
                f"branching must be an integer or a list of integers, "
                f"got {type(branching).__name__}: {branching!r:.80}",
                code="invalid_request")
    for name, value in (("depth", depth), ("budget", budget)):
        if value is not None and (isinstance(value, bool)
                                  or not isinstance(value, int)):
            raise es.EnsembleStatsError(
                f"{name} must be an integer, got "
                f"{type(value).__name__}: {value!r:.80}",
                code="invalid_request")


def _validate_params(branching, depth, budget):
    """(resolved branching list, depth, effective budget): service
    type discipline first, then the core's bounds, delegated to
    et.validate_tree_params / et.validate_budget."""
    _check_param_types(branching, depth, budget)
    if isinstance(branching, tuple):
        branching = list(branching)
    branching_list, depth_int = et.validate_tree_params(branching, depth)
    budget_int = et.validate_budget(budget)
    return branching_list, depth_int, budget_int


class EnsembleTreeJobs:
    def __init__(self, engine: Engine, store: TreeJobStore,
                 pool: BoundedJobPool | None = None):
        self._engine = engine
        self._store = store
        self._live: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._pool = pool if pool is not None else BoundedJobPool(
            name="enstree-pool")

    # ------------------------------------------------------------ quote

    def _validated_quote(self, spec, axes_requested, branching, depth,
                         budget):
        """Resolve + validate + quote, shared by quote() and
        create(): (axes, notes, branching list, depth, budget,
        core quote, axis sequence). No persistence, no runs."""
        branching_list, depth_int, budget_int = _validate_params(
            branching, depth, budget)
        axes, notes = es.resolve_axes(spec, axes_requested)
        # Re-validate the resolved list/depth through the core (the
        # resolved forms are already valid; this keeps the core the
        # single authority on bounds if its rules move).
        branching_list, depth_int = et.validate_tree_params(
            branching_list, depth_int)
        quote = et.quote(axes, branching=branching_list,
                         depth=depth_int, budget=budget_int)
        axis_sequence = et.axis_sequence(axes, branching_list)
        return (axes, notes, branching_list, depth_int, budget_int,
                quote, axis_sequence)

    def quote(self, spec: dict, axes_requested: dict | None,
              branching=None, depth=None, budget=None) -> dict:
        """The full tree quote for a request: axes resolved, params
        validated, the core's arithmetic served verbatim. No
        persistence, no runs — a quote is a computation."""
        (axes, notes, _branching, _depth, _budget, quote,
         axis_sequence) = self._validated_quote(
            spec, axes_requested, branching, depth, budget)
        return {
            "disruption": spec,
            "axes": es.axes_payload(axes),
            "axes_notes": notes,
            **quote,
            "axis_sequence": axis_sequence,
            "labelling": es.LABELLING,
        }

    # ----------------------------------------------------------- create

    def create(self, spec: dict, axes_requested: dict | None,
               branching=None, depth=None, budget=None) -> dict:
        """Resolve, quote, register and start a tree job; returns its
        summary. A tree whose quote is not launchable under its
        budget is refused (tree_too_large) BEFORE anything is
        persisted — no job directory is created for a refused tree.
        Raises EngineError (invalid_request / axis codes /
        tree_too_large) on a bad request — the caller maps it to the
        standard error shape."""
        (axes, notes, branching_list, depth_int, budget_int, quote,
         axis_sequence) = self._validated_quote(
            spec, axes_requested, branching, depth, budget)
        if not quote["launchable"]:
            raise es.EnsembleStatsError(
                et.refusal_message(quote), code="tree_too_large")
        # The budget is deliberately EXCLUDED from the id core: the
        # same tree under a different (sufficient) budget is the
        # same job.
        core = {"kind": "ensemble_stats_tree",
                "graph_hash": self._engine.graph.hash,
                "disruption": spec, "axes": axes,
                "branching": branching_list, "depth": depth_int}
        job_id = derive_job_id(core)
        with self._lock:
            live = self._live.get(job_id)
            if live is not None:
                return self.summary(live)
            stored = self._store.load_meta(job_id)
            if stored is not None and stored.get("status") == "complete":
                return self.summary(stored)
            # Any other stored record under this id is a corpse (a
            # previous process died mid-job, or the job failed): rerun
            # under the same deterministic id rather than serving it.
            # A NEW job needs a slot in this family's bounded
            # worker pool BEFORE it is registered — duplicates above
            # return the existing job and never consume one, so they
            # are never refused.
            if not self._pool.reserve():
                raise EngineError(
                    "ensemble tree job queue is full "
                    f"({JOB_POOL_WORKERS} running, {JOB_POOL_QUEUE} "
                    "queued); retry once a running job completes",
                    code="rate_limited")
            record = {
                "id": job_id,
                "kind": "ensemble_stats_tree",
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
                "branching": branching_list,
                "depth": depth_int,
                "budget": budget_int,
                "axis_sequence": axis_sequence,
                "quote": quote,
                "progress": {"done": 0,
                             "quoted": quote["total_runs_including_base"]},
                "accounting": None,
                "level_counts": None,
            }
            self._live[job_id] = record
            try:
                self._store.save_meta(self._persistable(record))
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
        branching = record["branching"]
        depth = record["depth"]
        quoted = record["quote"]["total_runs_including_base"]
        engine = Engine(self._engine.full_graph)

        def run_one(index, combination):
            run_id = f"{job_id}_v{index}"
            state = engine.run(run_id, es.variant_spec(spec, combination),
                               es.variant_params(combination))
            rec = es.variant_record(index, combination, run_id, state)
            extraction = {"outcomes": rec["outcomes"],
                          "places": rec.pop("_places"),
                          "legs": rec.pop("_legs")}
            core_record = {k: rec[k] for k in (
                "combination", "run_id", "stream_hash", "outcomes",
                "places_fired", "legs_fired")}
            return core_record, extraction

        def on_progress(*args, **kwargs):
            done = kwargs.get("done")
            if done is None and args:
                first = args[0]
                if isinstance(first, int) and not isinstance(first, bool):
                    done = first
                elif isinstance(first, dict):
                    done = first.get("done")
            if done is None:
                return
            with self._lock:
                record["progress"] = {"done": done, "quoted": quoted}

        try:
            built = et.build_tree(axes, branching, depth, spec["shape"],
                                  spec["node_id"], run_one,
                                  on_progress=on_progress)
            nodes = built["nodes"]
            accumulators = built["accumulators"]
            aggregates: dict[int, dict] = {}
            for node in nodes:
                if node.get("expanded"):
                    aggregates[node["index"]] = et.node_aggregates(
                        accumulators[node["index"]], engine.graph.nodes)
            accounting = built["accounting"]
            # Per-level member counts, level 0 (the root) included,
            # and the members grouped by level in member order.
            level_counts = [0] * (depth + 1)
            by_level: dict[int, list[dict]] = {}
            for node in nodes:
                level_counts[node["level"]] += 1
                by_level.setdefault(node["level"], []).append(node)
            # Publish order: segment files (full stored variant
            # records, without the aggregates key), light-entry
            # files, per-member aggregates, and only then the meta
            # record marked complete — members publish whole at
            # completion, never half-built.
            for level in range(depth + 1):
                rows = by_level.get(level, [])
                self._store.write_level(
                    job_id, "segments", level,
                    [{k: v for k, v in node.items() if k != "aggregates"}
                     for node in rows])
                self._store.write_level(
                    job_id, "nodes", level,
                    [self._light_entry(node) for node in rows])
            for index, payload in aggregates.items():
                self._store.save_aggregate(job_id, index, payload)
            with self._lock:
                record["status"] = "complete"
                record["completed_at"] = _now_iso()
                record["accounting"] = accounting
                record["level_counts"] = level_counts
                if isinstance(accounting, dict) \
                        and isinstance(accounting.get("run"), int):
                    record["progress"] = {"done": accounting["run"],
                                          "quoted": quoted}
                self._store.save_meta(self._persistable(record))
                # Evict the finished job from _live once its
                # meta record is persisted (see ensstats/jobs.py) — the
                # segmented store serves it from here on, and a failed
                # job's identical re-create RE-RUNS instead of pinning.
                self._live.pop(job_id, None)
        except Exception as exc:  # a variant failure fails the job openly
            code = exc.code if isinstance(exc, EngineError) else "internal_error"
            with self._lock:
                record["status"] = "failed"
                record["completed_at"] = _now_iso()
                record["error"] = {"code": code, "message": str(exc)}
                self._store.save_meta(self._persistable(record))
                # Evict on failure too (see ensstats/jobs.py).
                self._live.pop(job_id, None)

    # ------------------------------------------------------------- reads

    def _persistable(self, record: dict) -> dict:
        return {k: v for k, v in record.items()}

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            live = self._live.get(job_id)
            if live is not None:
                return live
        stored = self._store.load_meta(job_id)
        if stored is None:
            return None
        if stored.get("status") == "running":
            stored = {**stored, "status": "interrupted"}
        return stored

    def summary(self, record: dict) -> dict:
        return {
            "id": record["id"],
            "kind": record.get("kind", "ensemble_stats_tree"),
            "status": record["status"],
            "created_at": record["created_at"],
            "completed_at": record["completed_at"],
            "error": record["error"],
            "graph_hash": record["graph_hash"],
            "graph_version": record["graph_version"],
            "disruption": record["disruption"],
            "axes": es.axes_payload(record["axes"]),
            "axes_notes": record["axes_notes"],
            "branching": record["branching"],
            "depth": record["depth"],
            "budget": record.get("budget"),
            "axis_sequence": record.get("axis_sequence"),
            "quote": record.get("quote"),
            "progress": record["progress"],
            "accounting": record.get("accounting"),
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
            raise TreeJobNotFound(job_id)
        if record["status"] != "complete":
            raise TreeJobNotReady(record)
        return record

    # -------------------------------------------------------- tree views

    @staticmethod
    def _light_entry(node: dict) -> dict:
        """One tree member, light: identity, place in the tree, its
        combination and outcome vector, expansion state and fired
        counts. No aggregates, no fired id lists — counts only."""
        return {
            "index": node["index"],
            "parent": node["parent"],
            "children": node["children"],
            "level": node["level"],
            "ordinal": node["ordinal"],
            "path": node["path"],
            "axis": node["axis"],
            "value": node["value"],
            "combination": node["combination"],
            "outcomes": node["outcomes"],
            "expanded": node["expanded"],
            "pruned": node["pruned"],
            "prune_reason": node["prune_reason"],
            "subtree_run_count": node["subtree_run_count"],
            "places_fired_count": len(node.get("places_fired") or []),
            "legs_fired_count": len(node.get("legs_fired") or []),
        }

    def tree(self, job_id: str) -> dict:
        record = self._complete_record(job_id)
        level_counts = record.get("level_counts")
        n_levels = len(level_counts) if isinstance(level_counts, list) \
            else record["depth"] + 1
        entries: list[dict] = []
        for level in range(n_levels):
            entries.extend(self._store.read_level(job_id, "nodes", level))
        return {
            "id": job_id,
            "status": "complete",
            "disruption": record["disruption"],
            "axes": es.axes_payload(record["axes"]),
            "axes_notes": record["axes_notes"],
            "branching": record["branching"],
            "depth": record["depth"],
            "axis_sequence": record["axis_sequence"],
            "quote": record["quote"],
            "accounting": record["accounting"],
            "labelling": es.LABELLING,
            "nodes": entries,
        }

    def _locate(self, job_id: str, record: dict, index,
                kind: str = "segments") -> dict | None:
        """The stored row for member `index`, located from the meta
        record's level_counts via cumulative level boundaries, or
        None. Only that member's level file is read (one row of it).
        A row whose stored index does not match the requested index
        is treated as not found — a store-corruption guard, never
        served as if it were the member asked for."""
        if isinstance(index, bool) or not isinstance(index, int) \
                or index < 0:
            return None
        level_counts = record.get("level_counts")
        if not isinstance(level_counts, list):
            return None
        start = 0
        for level, count in enumerate(level_counts):
            if not isinstance(count, int) or isinstance(count, bool):
                return None
            if start <= index < start + count:
                row = self._store.read_level_row(
                    job_id, kind, level, index - start)
                if row is None or row.get("index") != index:
                    return None
                return row
            start += count
        return None

    def node(self, job_id: str, index: int) -> dict:
        record = self._complete_record(job_id)
        node = self._locate(job_id, record, index)
        aggregates = self._store.load_aggregate(job_id, index) \
            if isinstance(index, int) and not isinstance(index, bool) \
            else None
        if node is None:
            # Aggregates are stored separately from the outcome
            # vectors precisely so they can be served without the
            # segment files: if this member's aggregates are stored,
            # serve them off the light entry rather than failing
            # because a segment file is unavailable.
            if aggregates is None:
                raise TreeNodeNotFound(index)
            light = self._locate(job_id, record, index, kind="nodes")
            if light is None:
                raise TreeNodeNotFound(index)
            node = light
            basis = "stored"
            return self._node_payload(job_id, node, aggregates, basis)
        if aggregates is not None:
            basis = "stored"
        else:
            # Not stored (this part of the tree was not expanded):
            # derive the member's own aggregates by replaying its
            # run, verifying the stream hash against the stored
            # record before anything derived is served.
            combo = node["combination"]
            engine = Engine(self._engine.full_graph)
            state = engine.run(node["run_id"],
                               es.variant_spec(record["disruption"], combo),
                               es.variant_params(combo))
            replay_hash = stream_hash(state.events)
            if replay_hash != node["stream_hash"]:
                raise EngineError(
                    "re-derived chain does not match the stored record "
                    f"({replay_hash} != {node['stream_hash']})",
                    code="internal_error")
            acc = es.EnsembleAccumulator()
            acc.add(es.extract_outcomes(state))
            aggregates = et.node_aggregates(acc, engine.graph.nodes)
            basis = "replayed"
        return self._node_payload(job_id, node, aggregates, basis)

    @staticmethod
    def _node_payload(job_id: str, node: dict, aggregates,
                      basis: str) -> dict:
        return {
            "id": job_id,
            "status": "complete",
            "node": dict(node),
            "aggregates": aggregates,
            "aggregates_basis": basis,
            "variants_in_subtree": node["subtree_run_count"],
            "subtree_note": (
                "these figures cover the variants actually run in this "
                "part of the tree — the combinations under this one; "
                "parts of the tree that were not expanded are marked "
                "as not expanded, never hidden"),
            "labelling": es.LABELLING,
            "definitions": {
                k: es.DEFINITIONS[k] for k in (
                    "place_delay_contribution_d", "place_total_delay_d",
                    "leg_delay_contribution_d", "fire_share", "percentiles")
            },
        }

    def variant(self, job_id: str, index: int) -> dict:
        """One tree member's full causal chain, re-derived by replay
        (stream hash verified, exactly as the flat jobs' variant()).
        Tree members publish at completion, so — unlike the flat
        variant view — there is no partial view while running: a
        not-yet-complete job is TreeJobNotReady whatever the index."""
        record = self.get(job_id)
        if record is None:
            raise TreeJobNotFound(job_id)
        if record["status"] != "complete":
            raise TreeJobNotReady(record)
        node = self._locate(job_id, record, index)
        if node is None:
            raise TreeVariantNotFound(index)
        combo = node["combination"]
        engine = Engine(self._engine.full_graph)
        state = engine.run(node["run_id"],
                           es.variant_spec(record["disruption"], combo),
                           es.variant_params(combo))
        replay_hash = stream_hash(state.events)
        if replay_hash != node["stream_hash"]:
            raise EngineError(
                "re-derived chain does not match the stored record "
                f"({replay_hash} != {node['stream_hash']})",
                code="internal_error")
        public = {k: v for k, v in node.items() if k != "aggregates"}
        return {"variant": public, "combination": combo,
                "events": state.events}
