"""Request canonicalisation, run identity, execution, caching, jobs.

A run id is sha256 of the canonical journal (request + profile hash +
registry hash + model version), so the same request on the same profile
and model always has the same id, and a stored journal reproduces the run
exactly. Results are cached in memory (LRU); only journals are persisted.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import output, scenario
from .classes import PortSimError
from .core import RunParams, simulate
from .profile import PortSpec, Sources, build_spec
from .registry import (MODEL_LABEL, MODEL_VERSION, REGISTRY, REGISTRY_HASH,
                       VARIANTS, canonical, plug, sha256)

JOURNAL_SCHEMA = "portsim-journal/1"
# Journals are written for every distinct valid request (a stranger can mint
# many); keep the newest N so the directory cannot grow without bound.
JOURNAL_KEEP_DEFAULT = 500
BODY_KEYS = {"days", "start_at", "scenario", "variant", "arrival_shape",
             "anchor", "warmup_days"}
ANCHORS = ("engine2", "structural")
SHAPES = ("even", "diurnal")


class Run:
    def __init__(self, run_id, journal, spec, result, tl, summ, built_s):
        self.id, self.journal, self.spec, self.result = run_id, journal, spec, result
        self.timeline, self.summary, self.built_s = tl, summ, built_s


def _int(body, key, default, lo, hi):
    v = body.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int):
        raise PortSimError("invalid_request", f"{key} must be an integer")
    if not lo <= v <= hi:
        raise PortSimError("invalid_request",
                           f"{key}={v} is outside {lo}..{hi}; refused, not clamped")
    return v


def parse_start_at(v):
    if v is None:
        return None
    if not isinstance(v, str):
        raise PortSimError("invalid_request", "start_at must be an ISO 8601 UTC string")
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise PortSimError("invalid_request", f"start_at not ISO 8601: {v!r}")
    if dt.tzinfo is None:
        raise PortSimError("invalid_request", "start_at needs a timezone (use Z)")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tier_block(spec: PortSpec) -> dict:
    core = spec.tier == "core"
    return {
        "tier": spec.tier,
        "grade": ("CORE place" if core else "SHELL place: VISUALISATION ONLY"),
        "scorecard": {"minted": False,
                      "reason": "the port twin is a SIMULATED operational "
                                "picture; no figure here enters the backtest "
                                "scorecard" + ("" if core else
                                               ", and SHELL places are never "
                                               "calibration-grade")}}


class PortSimService:
    def __init__(self, sources: Sources, store_dir=None, cache_size: int = 24,
                 workers: int = 2):
        self.sources = sources
        self.store_dir = Path(store_dir) if store_dir else None
        self._cache: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        self._jobs: dict = {}
        self._pool = None
        self._workers = workers
        self.cache_size = cache_size

    # ---------------------------------------------------------- canonical
    def canonicalise(self, place_id: str, body, extra: dict | None = None):
        """Validate a request and return (spec, journal). `extra` (library
        use) may carry daily_calls."""
        if body is None:
            body = {}
        if not isinstance(body, dict):
            raise PortSimError("invalid_request", "body must be an object")
        unknown = set(body) - BODY_KEYS
        if unknown:
            raise PortSimError("invalid_request",
                               f"unknown key(s) {sorted(unknown)}; allowed "
                               f"{sorted(BODY_KEYS)}")
        spec = build_spec(place_id, self.sources)
        lim = plug("run_limits")
        days = _int(body, "days", 7, 1, lim["days_max"])
        warm = _int(body, "warmup_days", lim["warmup_days_default"], 0,
                    lim["warmup_days_max"])
        variant = body.get("variant", "base")
        if variant not in VARIANTS:
            raise PortSimError("invalid_request",
                               f"variant must be one of {list(VARIANTS)}")
        shape = body.get("arrival_shape", "even")
        if shape not in SHAPES:
            raise PortSimError("invalid_request",
                               f"arrival_shape must be one of {list(SHAPES)}")
        anchor = body.get("anchor", "engine2")
        if anchor not in ANCHORS:
            raise PortSimError("invalid_request",
                               f"anchor must be one of {list(ANCHORS)}")
        start_at = parse_start_at(body.get("start_at"))
        sc = scenario.validate(body.get("scenario"), spec, days)
        journal = {
            "schema": JOURNAL_SCHEMA, "model_version": MODEL_VERSION,
            "registry_hash": REGISTRY_HASH, "place_id": place_id,
            "profile_hash": spec.profile_hash, "profile_kind": spec.profile_kind,
            "days": days, "warmup_days": warm, "start_at": start_at,
            "variant": variant, "arrival_shape": shape, "anchor": anchor,
            "scenario": sc}
        if extra and extra.get("daily_calls"):
            journal["daily_calls"] = {k: [round(float(x), 6) for x in v]
                                      for k, v in sorted(extra["daily_calls"].items())}
        return spec, journal

    @staticmethod
    def run_id(journal: dict) -> str:
        return "ps_" + sha256(canonical(journal))[:20]

    @staticmethod
    def estimate_vessels(spec: PortSpec, journal: dict) -> float:
        lam = sum(spec.calls_per_day.values())
        mult = 1.0
        for it in journal["scenario"]:
            if it["type"] == "surge":
                mult = max(mult, it["factor"])
            elif it["type"] == "chain_path" and it.get("arrival_mult"):
                mult = max(mult, max(it["arrival_mult"]))
        return lam * mult * (journal["days"] + journal["warmup_days"])

    def is_small(self, spec, journal) -> bool:
        return self.estimate_vessels(spec, journal) <= plug("run_limits")["sync_vessel_budget"]

    # ------------------------------------------------------------ execute
    def execute(self, spec: PortSpec, journal: dict) -> Run:
        t0 = time.perf_counter()
        ctl = scenario.compile_controls(journal["scenario"], spec)
        params = RunParams(days=journal["days"], warmup_days=journal["warmup_days"],
                           variant=journal["variant"],
                           arrival_shape=journal["arrival_shape"],
                           anchor=journal["anchor"],
                           daily_calls=journal.get("daily_calls"))
        res = simulate(spec, params, ctl)
        tl = output.timeline(res)
        summ = output.summary(res, tl)
        return Run(self.run_id(journal), journal, spec, res, tl, summ,
                   time.perf_counter() - t0)

    # -------------------------------------------------------------- cache
    def _put(self, run: Run):
        with self._lock:
            self._cache[run.id] = run
            self._cache.move_to_end(run.id)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)

    def cached(self, run_id: str):
        with self._lock:
            r = self._cache.get(run_id)
            if r is not None:
                self._cache.move_to_end(run_id)
            return r

    def _journal_keep(self) -> int:
        try:
            return max(1, int(os.environ.get("SUPERMAP_PORTSIM_RUNS_MAX",
                                             JOURNAL_KEEP_DEFAULT)))
        except ValueError:
            return JOURNAL_KEEP_DEFAULT

    def _persist(self, journal: dict, run_id: str):
        if self.store_dir is None:
            return
        try:
            self.store_dir.mkdir(parents=True, exist_ok=True)
            target = self.store_dir / f"{run_id}.json"
            if target.exists():
                return
            tmp = self.store_dir / f".{run_id}.tmp"
            tmp.write_text(canonical(journal), encoding="utf-8")
            os.replace(tmp, target)
            self._trim_journals(target)
        except OSError:
            pass

    def _trim_journals(self, keep_path: Path):
        """Delete the oldest journals beyond the cap (never the one just
        written). A journal gone from disk only means a cold GET of that id
        after a restart is a 404; the run is rebuilt from its request."""
        limit = self._journal_keep()
        files = []
        for p in self.store_dir.glob("*.json"):
            try:
                files.append((p.stat().st_mtime, p))
            except OSError:
                continue
        if len(files) <= limit:
            return
        files.sort(key=lambda e: e[0], reverse=True)
        for _mt, p in files[limit:]:
            if p == keep_path:
                continue
            try:
                p.unlink()
            except OSError:
                pass

    def _load_journal(self, run_id: str):
        if self.store_dir is None:
            return None
        try:
            return json.loads((self.store_dir / f"{run_id}.json").read_text("utf-8"))
        except (OSError, ValueError):
            return None

    # ----------------------------------------------------------- run / job
    def create(self, place_id: str, body, extra=None, force_async=False):
        """Returns (status, run_id, Run|None). status: 'complete'|'building'."""
        spec, journal = self.canonicalise(place_id, body, extra)
        rid = self.run_id(journal)
        hit = self.cached(rid)
        if hit is not None:
            return "complete", rid, hit
        self._persist(journal, rid)
        if self.is_small(spec, journal) and not force_async:
            run = self.execute(spec, journal)
            self._put(run)
            return "complete", rid, run
        with self._lock:
            job = self._jobs.get(rid)
            if job is not None and job["status"] == "building":
                return "building", rid, None
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=self._workers)
            if sum(1 for j in self._jobs.values() if j["status"] == "building") >= 8:
                raise PortSimError("rate_limited",
                                   "port-sim build queue is full; retry once a "
                                   "running build completes", 429)
            self._jobs[rid] = {"status": "building", "error": None}
            self._pool.submit(self._job, rid, spec, journal)
        return "building", rid, None

    def _job(self, rid, spec, journal):
        try:
            run = self.execute(spec, journal)
            self._put(run)
            st, err = "complete", None
        except PortSimError as exc:
            st, err = "failed", {"code": exc.code, "message": exc.message}
        except Exception as exc:  # pragma: no cover - defensive
            st, err = "failed", {"code": "sim_build_failed", "message": str(exc)[:300]}
        with self._lock:
            self._jobs[rid] = {"status": st, "error": err}

    def job(self, rid):
        with self._lock:
            return dict(self._jobs.get(rid) or {})

    def get(self, place_id: str, rid: str) -> Run:
        """The run for an id (cache, else rebuilt from its stored journal)."""
        run = self.cached(rid)
        if run is not None:
            if run.journal["place_id"] != place_id:
                raise PortSimError("unknown_sim_run",
                                   f"sim run {rid} belongs to another place", 404)
            return run
        job = self.job(rid)
        if job.get("status") == "building":
            raise PortSimError("sim_not_ready", f"sim run {rid} is still building", 409)
        if job.get("status") == "failed":
            e = job["error"]
            raise PortSimError(e["code"], e["message"], 500)
        journal = self._load_journal(rid)
        if journal is None or journal.get("place_id") != place_id:
            raise PortSimError("unknown_sim_run", f"unknown sim run: {rid!r}", 404)
        spec = build_spec(place_id, self.sources)
        if (spec.profile_hash != journal["profile_hash"]
                or journal["registry_hash"] != REGISTRY_HASH
                or journal["model_version"] != MODEL_VERSION):
            raise PortSimError(
                "run_not_reproducible",
                "the profile or model changed since this run was made; the "
                "stored journal no longer reproduces it. Re-post the request "
                "for a new run id.", 409)
        run = self.execute(spec, journal)
        self._put(run)
        return run

    def baseline_of(self, run: Run) -> Run:
        """The same request without a scenario (computed once, cached)."""
        j = dict(run.journal)
        j["scenario"] = []
        rid = self.run_id(j)
        hit = self.cached(rid)
        if hit is not None:
            return hit
        base = self.execute(run.spec, j)
        self._persist(j, rid)
        self._put(base)
        return base

    def verify(self, run: Run) -> dict:
        """Replay from the journal, bypassing every cache."""
        spec = build_spec(run.journal["place_id"], self.sources)
        fresh = self.execute(spec, run.journal)
        same = fresh.summary["result_digest"] == run.summary["result_digest"]
        return {"run_id": run.id, "identical": same,
                "result_digest": run.summary["result_digest"],
                "replayed_digest": fresh.summary["result_digest"],
                "replay_seconds": round(fresh.built_s, 3),
                "profile_hash_now": spec.profile_hash,
                "profile_hash_journal": run.journal["profile_hash"]}

    # --------------------------------------------------------- provenance
    def provenance(self, run: Run) -> dict:
        spec, j = run.spec, run.journal
        used = ["mu_max_ratio", "vessel_classes", "service_variants",
                "movement_times_s", "arrival_shapes", "run_limits",
                "berth_geometry", "anchorage_default"]
        if "container" in spec.calls_per_day:
            used += ["crane_rate_teu_per_h", "yard", "teu_per_call_fallback",
                     "teu_per_call_bounds"]
        if any(k != "container" for k in spec.calls_per_day):
            used += ["bulk_rates_t_per_h", "default_tonnes_per_call"]
        used = sorted(set(used))
        return {
            "label": "SIMULATED",
            "basis": "MODELLED",
            "model": MODEL_LABEL, "model_version": MODEL_VERSION,
            "registry_hash": REGISTRY_HASH,
            "determinism": "no random numbers; integer-second clock; fixed "
                           "arrival curve, class mix and quantile service times",
            "profile": {"kind": spec.profile_kind, "hash": spec.profile_hash,
                        "source": spec.profile_source,
                        "geometry_hash": spec.geometry_hash,
                        "notes": spec.notes},
            "anchor": {"mode": j["anchor"], "report": run.result.anchor_report,
                       "meaning": "engine2: berth service times are scaled so "
                                  "that berth capacity = mu_max_ratio x baseline "
                                  "calls, matching the chain engine's place-level "
                                  "capacity; structural: cited productivity "
                                  "used as is"},
            "baseline_state": output.baseline_state(run.result.anchor_report),
            "variant": j["variant"],
            "plugins_used": [{k: v for k, v in p.items()
                              if k in ("name", "value", "unit", "status", "source",
                                       "replace_with")}
                             for p in REGISTRY if p["name"] in used],
            "oversize_calls_accommodated": run.result.oversize,
            "market_data_inputs": "none",
            **tier_block(spec)}
