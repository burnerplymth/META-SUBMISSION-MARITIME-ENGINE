"""Ensemble-statistics EXPLORATIONS (interactive deepening).

An exploration is a saved record whose members are ordinary flat
ensemble-statistics jobs (server/ensstats/jobs.py). It supports the
interactive-deepening workflow: run a flat ensemble, open one of its
variants, and deepen from that variant — a new flat job that holds
every axis at the chosen variant's combination except the next
not-yet-varied axis, which is swept over caller-supplied values.
Each expansion is again an ordinary flat job, so it can itself be
deepened, one axis at a time, until every axis has been varied along
that path.

The record stores pointers + display metadata ONLY (the root job
id, the expansions' parent / variant / child links, names and
timestamps) — never aggregates or variant data. Aggregates stay with
the flat jobs: a member's detail entry points at the flat results
route that serves them, and variant combinations in a breadcrumb
are re-derived through the flat jobs' replay-verified variant()
exactly as the flat drill-down serves them.

Persistence is the flat jobs' own store discipline, rooted at a
dedicated subdirectory (<ensemble_stats_dir>/explorations): the flat
store's listing globs only its own directory's *.json files
(non-recursive), so exploration files never appear as flat jobs and
flat job files never appear as explorations.

Vocabulary: display text reuses the engine layer's axis
descriptions and labelling verbatim (places and legs, never the
developer words), like every other ensemble payload.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from .jobs import JobNotFound, JobNotReady
from .store import EnsembleStatsStore

try:  # dual-import convention, as in server/api/app.py
    from engine import ensemble_stats as es
    from engine.canonical import canonical_json, sha256_hex
except Exception:  # imported as server.ensstats.* from the repo root
    from server.engine import ensemble_stats as es  # type: ignore
    from server.engine.canonical import canonical_json, sha256_hex  # type: ignore


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ExplorationNotFound(Exception):
    pass


class EnsembleExplorations:
    """Saved explorations over a flat EnsembleStatsJobs service.

    ``store`` is an EnsembleStatsStore rooted at the explorations
    directory; records are one JSON file per exploration, exactly
    the flat store's discipline."""

    def __init__(self, flat_jobs, store: EnsembleStatsStore):
        self._flat = flat_jobs
        self._store = store
        self._lock = threading.Lock()

    # ---------------------------------------------------------- create

    def create(self, name, root_job_id) -> dict:
        """Create (and persist) an exploration rooted at an existing
        flat job. Raises JobNotFound when the root job does not
        exist, EnsembleStatsError on a malformed name."""
        if name is not None and not isinstance(name, str):
            raise es.EnsembleStatsError(
                "name must be a string", code="invalid_request")
        if self._flat.get(root_job_id) is None:
            raise JobNotFound(root_job_id)
        if name is None or not name.strip():
            name = "Untitled exploration"
        created_at = _now_iso()
        core = {"root_job_id": root_job_id, "name": name,
                "created_at": created_at}
        exp_id = "exp_" + sha256_hex(canonical_json(core))[:16]
        with self._lock:
            existing = self._store.load(exp_id)
            if existing is not None:
                return existing
            record = {
                "id": exp_id,
                "kind": "ensemble_exploration",
                "name": name,
                "root_job_id": root_job_id,
                "created_at": created_at,
                "updated_at": created_at,
                "expansions": [],
            }
            self._store.save(record)
            return record

    # ------------------------------------------------------------- reads

    def get(self, exp_id) -> dict | None:
        if not isinstance(exp_id, str):
            return None
        return self._store.load(exp_id)

    def list_records(self) -> list[dict]:
        records = []
        for exp_id in self._store.list_ids():
            record = self._store.load(exp_id)
            if record is not None:
                records.append(record)
        records.sort(key=lambda r: r["created_at"], reverse=True)
        return records

    def list_entries(self) -> list[dict]:
        """The list payload: identity, name and counts only."""
        out = []
        for record in self.list_records():
            expansions = record.get("expansions") or []
            out.append({
                "id": record["id"],
                "name": record["name"],
                "root_job_id": record["root_job_id"],
                "created_at": record["created_at"],
                "updated_at": record["updated_at"],
                "expansions": len(expansions),
                "members": 1 + len(expansions),
            })
        return out

    # ------------------------------------------------------------ rename

    def rename(self, exp_id, name) -> dict:
        if not isinstance(name, str) or not name.strip():
            raise es.EnsembleStatsError(
                "name must be a non-empty string", code="invalid_request")
        with self._lock:
            record = self.get(exp_id)
            if record is None:
                raise ExplorationNotFound(exp_id)
            record["name"] = name
            record["updated_at"] = _now_iso()
            self._store.save(record)
            return record

    # ------------------------------------------------------- path helpers

    def _member_ids(self, record: dict) -> list[str]:
        return [record["root_job_id"]] + [
            e["child_job_id"] for e in record.get("expansions") or []]

    def _path_expansions(self, record: dict, target_job_id) -> list[dict]:
        """The expansion hops from the root to ``target_job_id``, in
        order ([] for the root itself; [] as a fallback when the
        target is not reachable, which membership checks rule out
        for the callers that rely on the path)."""
        if target_job_id == record["root_job_id"]:
            return []
        adjacency: dict[str, list[dict]] = {}
        for exp in record.get("expansions") or []:
            adjacency.setdefault(exp["parent_job_id"], []).append(exp)

        def walk(job_id, path, seen):
            for exp in adjacency.get(job_id, []):
                child = exp["child_job_id"]
                if child in seen:
                    continue
                if child == target_job_id:
                    return path + [exp]
                found = walk(child, path + [exp], seen | {child})
                if found is not None:
                    return found
            return None

        return walk(record["root_job_id"], [],
                    {record["root_job_id"]}) or []

    def _varied_axes(self, job_id) -> list[str]:
        record = self._flat.get(job_id)
        if record is None:
            return []
        axes = record.get("axes") or {}
        return [a for a in es.AXIS_ORDER if len(axes.get(a) or []) > 1]

    def _varied_union_along_path(self, record: dict,
                                 target_job_id) -> set:
        job_ids = [record["root_job_id"]] + [
            e["child_job_id"]
            for e in self._path_expansions(record, target_job_id)]
        union: set = set()
        for job_id in job_ids:
            union.update(self._varied_axes(job_id))
        return union

    def _next_axis(self, record: dict, target_job_id):
        union = self._varied_union_along_path(record, target_job_id)
        for axis in es.AXIS_ORDER:
            if axis not in union:
                return axis
        return None

    # ------------------------------------------------------------ expand

    def expand(self, exp_id, parent_job_id, variant_index,
               values) -> dict:
        """Deepen one member: a new flat job fixing every axis at the
        chosen parent variant's combination except the next axis
        not yet varied along the path to the parent, which is swept
        over ``values``. Deterministic: the child is an ordinary
        flat job, so the same (parent, variant, values) always
        derives the same child job id, and re-expanding the same
        triple returns the existing expansion without appending."""
        record = self.get(exp_id)
        if record is None:
            raise ExplorationNotFound(exp_id)
        if parent_job_id not in self._member_ids(record):
            raise es.EnsembleStatsError(
                "parent job is not part of this exploration",
                code="invalid_request")
        parent = self._flat.get(parent_job_id)
        if parent is None:
            raise JobNotFound(parent_job_id)
        if parent["status"] != "complete":
            raise JobNotReady(parent)
        if isinstance(variant_index, bool) \
                or not isinstance(variant_index, int):
            raise es.EnsembleStatsError(
                "variant_index must be an integer", code="invalid_request")
        combo = self._flat.variant(
            parent_job_id, variant_index)["combination"]
        next_axis = self._next_axis(record, parent_job_id)
        if next_axis is None:
            raise es.EnsembleStatsError(
                "every axis has already been varied along this path; "
                "there is no next axis to deepen",
                code="no_next_axis")
        if not isinstance(values, list) \
                or not (1 <= len(values) <= es.MAX_VARIANTS):
            raise es.EnsembleStatsError(
                f"values must be an array of 1 to {es.MAX_VARIANTS} "
                f"levels for the next axis", code="invalid_request")
        axes_requested = {}
        for axis in es.AXIS_ORDER:
            axes_requested[axis] = values if axis == next_axis \
                else [combo[axis]]
        child_summary = self._flat.create(
            parent["disruption"], axes_requested, count=len(values))
        child_id = child_summary["id"]
        child_record = self._flat.get(child_id)
        if child_record is not None:
            grid = es.grid_size(child_record["axes"])
            count_note = child_record["count_note"]
        else:  # pragma: no cover - create() just registered the job
            grid = child_summary["grid_size"]
            count_note = child_summary["count_note"]
        quote = {
            "axis": next_axis,
            "axis_description": es.AXIS_DESCRIPTIONS[next_axis],
            "values_count": len(values),
            "grid_size": grid,
            "count_note": count_note,
        }
        with self._lock:
            # Re-read under the lock: the stored expansions are the
            # dedupe authority (another expand may have landed while
            # the child job was being created).
            record = self.get(exp_id)
            if record is None:
                raise ExplorationNotFound(exp_id)
            for exp in record.get("expansions") or []:
                if exp["parent_job_id"] == parent_job_id \
                        and exp["parent_variant_index"] == variant_index \
                        and exp["child_job_id"] == child_id:
                    return {"exploration_id": exp_id, "expansion": exp,
                            "child": child_summary, "quote": quote}
            expansion = {
                "parent_job_id": parent_job_id,
                "parent_variant_index": variant_index,
                "child_job_id": child_id,
                "axis": next_axis,
                "created_at": _now_iso(),
            }
            record.setdefault("expansions", []).append(expansion)
            record["updated_at"] = _now_iso()
            self._store.save(record)
            return {"exploration_id": exp_id, "expansion": expansion,
                    "child": child_summary, "quote": quote}

    # ------------------------------------------------------------ detail

    def _breadcrumb(self, record: dict, target_job_id) -> list[dict]:
        steps = []
        for exp in self._path_expansions(record, target_job_id):
            try:
                combo = self._flat.variant(
                    exp["parent_job_id"],
                    exp["parent_variant_index"])["combination"]
            except Exception:
                # The parent job is missing or its variant can no
                # longer be re-derived: the hop is still stated,
                # with no combination claimed for it.
                combo = None
            steps.append({
                "job_id": exp["child_job_id"],
                "parent_job_id": exp["parent_job_id"],
                "variant_index": exp["parent_variant_index"],
                "combination": combo,
                "axis": exp["axis"],
                "axis_description": es.AXIS_DESCRIPTIONS.get(exp["axis"]),
            })
        return steps

    def detail(self, exp_id) -> dict:
        record = self.get(exp_id)
        if record is None:
            raise ExplorationNotFound(exp_id)
        expansions = record.get("expansions") or []
        members = [(record["root_job_id"], None)]
        members += [(e["child_job_id"], e) for e in expansions]
        nodes = []
        for job_id, exp in members:
            flat_record = self._flat.get(job_id)
            nodes.append({
                "job_id": job_id,
                "parent_job_id": exp["parent_job_id"] if exp else None,
                "parent_variant_index":
                    exp["parent_variant_index"] if exp else None,
                "status": flat_record["status"] if flat_record else None,
                "summary": self._flat.summary(flat_record)
                if flat_record is not None else None,
                "varied_axes": self._varied_axes(job_id),
                "next_axis": self._next_axis(record, job_id),
                "aggregates_ref": f"/ensemble-stats/{job_id}/results",
                "breadcrumb": self._breadcrumb(record, job_id),
            })
        return {
            "id": record["id"],
            "name": record["name"],
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
            "root_job_id": record["root_job_id"],
            "expansions": expansions,
            "nodes": nodes,
            "labelling": es.LABELLING,
        }
