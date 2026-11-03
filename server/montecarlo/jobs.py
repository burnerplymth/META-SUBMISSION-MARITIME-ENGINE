"""Monte Carlo job manager.

Reuses the ensemble-statistics infrastructure (server/ensstats): the
bounded worker pool (BoundedJobPool: fixed worker threads plus a
bounded queue, never a thread per job), the deterministic job id from the
canonical request core (identical request -> same job; a repeat never
consumes a slot), the one-JSON-file-per-job store with atomic writes, and
the "interrupted" rule (a persisted "running" record with no live worker in
this process reads back as "interrupted").

Sampling happens here, outside the engine: one private random.Random(seed)
per job, one draw per input per simulation in a fixed order. Each
simulation is one ordinary engine run. Aggregation is incremental: per
simulation only the compact outputs and a (first-arrival, delay) pair per
affected place are kept; the full chain of a run is dropped as soon as its
outputs are read, so memory does not grow with chain length.

Wall-clock time is used for ONE thing only: the cost quote's per-run
average (never in a result, an id or a sample).
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import os
import re
import threading
import time
from datetime import datetime, timezone

from . import LABEL, SCHEMA, aggregate, engines, metrics, sampling
from . import compare as compare_mod
from .sampling import INPUT_ORDER, EngineError, MCError
from .store import MonteCarloStore

try:  # dual-import convention, as in server/api/app.py
    from ensstats.jobs import BoundedJobPool
    from engine.canonical import canonical_json, sha256_hex, stream_hash
except Exception:  # imported as server.montecarlo.* from the repo root
    from server.ensstats.jobs import BoundedJobPool  # type: ignore
    from server.engine.canonical import canonical_json, sha256_hex, stream_hash  # type: ignore

MC_POOL_WORKERS = 2
MC_POOL_QUEUE = 4            # a batch (base + 4 variants) must fit in one go
MAX_VARIANTS = 4
JOB_ID_RE = re.compile(r"^mc_[0-9a-f]{16}$")
VARIANT_NAME_RE = re.compile(r"^[A-Za-z0-9 _.\-]{1,40}$")
OVERRIDE_KEYS = frozenset({"disruption", "engine_rev", "profile", "distributions"})

# Conservative seconds per simulation before this process has measured one
# (TEMPORARY: the worst case measured per engine over Suez, Panama and
# Shanghai disruptions, rounded up). A
# measured average replaces it after the first job.
DEFAULT_S_PER_RUN = {"1": 0.35, "2": 0.30, "2c": 0.45, "2d": 4.0, "2e": 4.0}
_EMA = 0.5


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _merge_request(base: dict, ov: dict, name: str) -> dict:
    """The base request with one variant's overrides applied (disruption and
    distributions merge key by key; a place override replaces the base place)."""
    out = copy.deepcopy(base)
    for key in ("engine_rev", "profile"):
        if key in ov:
            out[key] = ov[key]
    if "disruption" in ov:
        if not isinstance(ov["disruption"], dict):
            raise MCError(f"variant {name!r}: overrides.disruption must be an object")
        dis = dict(out.get("disruption") or {}) if isinstance(out.get("disruption"), dict) else {}
        if {"node_id", "place_id", "place"} & set(ov["disruption"]):
            for alias in ("node_id", "place_id", "place"):
                dis.pop(alias, None)
        dis.update(ov["disruption"])
        out["disruption"] = dis
    if "distributions" in ov:
        if not isinstance(ov["distributions"], dict):
            raise MCError(f"variant {name!r}: overrides.distributions must be an object")
        dist = dict(out.get("distributions") or {}) \
            if isinstance(out.get("distributions"), dict) else {}
        dist.update(ov["distributions"])
        out["distributions"] = dist
    return out


def _is_plain_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def owner_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class JobForbidden(Exception):
    """Deleting a job needs the creator's token (none stored = not deletable through the API)."""


class JobNotFound(Exception):
    pass


class JobNotReady(Exception):
    def __init__(self, record: dict):
        super().__init__("monte carlo job is not done")
        self.record = record


def derive_job_id(core: dict) -> str:
    return "mc_" + sha256_hex(canonical_json(core))[:16]


class MonteCarloJobs:
    def __init__(self, graph, core_graph, store: MonteCarloStore,
                 pool: BoundedJobPool | None = None, *, max_n: int | None = None,
                 max_seconds: float | None = None, global_cap: int | None = None,
                 client_cap: int | None = None, resolver=None):
        self._graph = graph
        self._core = core_graph
        self._store = store
        self._live: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._pool = pool if pool is not None else BoundedJobPool(
            workers=MC_POOL_WORKERS, queue_size=MC_POOL_QUEUE, name="montecarlo-pool")
        self.max_n = max_n or _env_int("SUPERMAP_MC_MAX_N", sampling.N_MAX_HARD)
        self.max_seconds = float(max_seconds or _env_int("SUPERMAP_MC_MAX_SECONDS", 900))
        self.global_cap = global_cap or _env_int("SUPERMAP_MC_GLOBAL_CAP",
                                                 MC_POOL_WORKERS + MC_POOL_QUEUE)
        self.client_cap = client_cap or _env_int("SUPERMAP_MC_CLIENT_CAP", 1)
        self._resolver = resolver or engines.resolve
        self._perf: dict = {}
        self._cmp_cache: dict = {}
        self._revs = (1, 2) if engines.rev2_available() else (1,)

    # ------------------------------------------------------------ quote
    def s_per_run(self, key: str, node_id: str) -> tuple[float, str]:
        with self._lock:
            for k in ((key, node_id), (key, None)):
                if k in self._perf:
                    return self._perf[k], "measured"
        return DEFAULT_S_PER_RUN.get(key, 0.5), "default"

    def _note_perf(self, key: str, node_id: str, per_run: float) -> None:
        with self._lock:
            for k in ((key, node_id), (key, None)):
                old = self._perf.get(k)
                self._perf[k] = per_run if old is None else _EMA * per_run + (1 - _EMA) * old
            if len(self._perf) > 512:
                self._perf.clear()

    def quote(self, req: dict) -> dict:
        key = engines.engine_key(req["engine_rev"], req["profile"])
        s, basis = self.s_per_run(key, req["disruption"]["node_id"])
        return {"n": req["n"], "est_seconds": round(req["n"] * s, 1),
                "s_per_run": round(s, 4), "basis": basis}

    # ----------------------------------------------------------- create
    def _resolve_pair(self, body):
        """(body without pair_with, partner id or None): common random numbers
        take the partner's seed (and n, unless given and equal)."""
        if not isinstance(body, dict) or "pair_with" not in body:
            return body, None
        partner = body["pair_with"]
        if not isinstance(partner, str) or not JOB_ID_RE.match(partner):
            raise MCError("pair_with must be a montecarlo job id", code="montecarlo_not_found")
        rec = self.get(partner)
        if rec is None:
            raise MCError(f"unknown montecarlo job: {partner!r}", code="montecarlo_not_found")
        req = rec["request"]
        out = {k: v for k, v in body.items() if k != "pair_with"}
        if "seed" in out and not (_is_plain_int(out["seed"]) and abs(out["seed"]) == req["seed"]):
            raise MCError("seed differs from pair_with's seed; leave seed out to inherit it")
        if "n" in out and out["n"] != req["n"]:
            raise MCError("n differs from pair_with's n; leave n out to inherit it")
        out["seed"], out["n"] = req["seed"], req["n"]
        return out, partner

    def _prepare(self, body) -> dict:
        body, partner = self._resolve_pair(body)
        req = sampling.parse_request(body, self._core, max_n=self.max_n,
                                     engine_revs_available=self._revs)
        quote = self.quote(req)
        if quote["est_seconds"] > self.max_seconds:
            fit = max(0, int(self.max_seconds / max(quote["s_per_run"], 1e-9)))
            raise MCError(
                f"estimated {quote['est_seconds']} s for n={req['n']} is above the cap of "
                f"{self.max_seconds:g} s (SUPERMAP_MC_MAX_SECONDS); the largest n for this "
                f"engine is about {fit}", code="montecarlo_too_large")
        try:
            _engine, identity = self._resolver(self._graph, req["engine_rev"], req["profile"])
        except Exception as exc:
            raise MCError(f"engine unavailable: {str(exc)[:120]}") from None
        core = {"kind": "monte_carlo", "schema": SCHEMA, "graph_hash": self._graph.hash,
                "engine": identity, "disruption": req["disruption"], "n": req["n"],
                "seed": req["seed"],
                "inputs": [{k: s[k] for k in ("name", "dist", "low", "mode", "high")}
                           for s in req["inputs"]]}
        return {"id": derive_job_id(core), "req": req, "identity": identity,
                "quote": quote, "pair_with": partner}

    def _is_new(self, job_id: str) -> bool:
        """Caller holds the lock."""
        if job_id in self._live:
            return False
        stored = self._store.load(job_id)
        return not (stored is not None and stored.get("status") == "done")

    def _existing(self, job_id: str):
        """Caller holds the lock: the live or finished record, else None."""
        live = self._live.get(job_id)
        if live is not None:
            return live
        stored = self._store.load(job_id)
        if stored is not None and stored.get("status") == "done":
            return stored
        return None

    def _admit(self, prepared: list, client: str, batch: bool, owner: str = "") -> list:
        """Register and start the new jobs among ``prepared`` as ONE unit
        (all or nothing); returns the head of each, in order."""
        starts = []
        with self._lock:
            heads, fresh, seen = [], [], set()
            for p in prepared:
                ex = self._existing(p["id"])
                if ex is not None:
                    heads.append(self._head(ex))
                else:
                    if p["id"] not in seen:
                        fresh.append(p)
                        seen.add(p["id"])
                    heads.append(None)
            need = len(fresh)
            if need:
                if len(self._live) + need > self.global_cap:
                    raise MCError("the simulation queue is full "
                                  f"({self.global_cap} jobs running or queued); retry once "
                                  "one completes", code="rate_limited")
                mine = sum(1 for r in self._live.values() if r.get("_owner") == client) \
                    if client else 0
                if client and (mine >= self.client_cap or (batch and mine > 0)):
                    raise MCError("this client already has a simulation running; "
                                  "wait for it to finish or cancel it", code="rate_limited")
                reserved = 0
                for _ in fresh:
                    if not self._pool.reserve():
                        for _r in range(reserved):
                            self._pool.release()
                        raise MCError("the simulation queue is full; retry once one completes",
                                      code="rate_limited")
                    reserved += 1
                made = {}
                try:
                    for p in fresh:
                        req = p["req"]
                        record = {
                            "id": p["id"], "kind": "monte_carlo", "schema": SCHEMA,
                            "status": "running", "created_at": _now_iso(),
                            "completed_at": None, "error": None, "label": LABEL,
                            "graph_hash": self._graph.hash,
                            "request": {"disruption": req["disruption"],
                                        "engine_rev": req["engine_rev"],
                                        "profile": req["profile"], "n": req["n"],
                                        "seed": req["seed"]},
                            "engine": p["identity"], "inputs": req["inputs"],
                            "quote": p["quote"], "pair_with": p["pair_with"],
                            "progress": {"done": 0, "n": req["n"]},
                            "rows": [], "results": None,
                            "_owner": client, "_cancel": threading.Event(), "_places": {},
                        }
                        if owner:                    # only the digest is stored (key survives _persistable)
                            record["owner"] = owner_digest(owner)
                        self._store.save(self._persistable(record))
                        made[p["id"]] = record
                        self._live[p["id"]] = record
                except Exception:
                    for jid in made:
                        self._live.pop(jid, None)
                    for _r in range(reserved):
                        self._pool.release()
                    raise
                starts = list(made.values())
                self._store.prune(protect=set(self._live))
            for k, p in enumerate(prepared):
                if heads[k] is None:
                    heads[k] = self._head(self._live[p["id"]])
                    if owner:                        # shown once, to the creator only
                        heads[k]["owner_token"] = owner
        for record in starts:
            self._pool.submit_reserved(lambda r=record: self._run(r))
        return heads

    def create(self, body, client: str = "", owner: str = "") -> dict:
        """Validate, quote, register and start a job (or return the existing
        one). Raises MCError (an EngineError) on a bad or refused request."""
        p = self._prepare(body)
        return self._admit([p], client, batch=False, owner=owner)[0]

    def create_batch(self, body, client: str = "", owner: str = "") -> dict:
        """A base job and up to MAX_VARIANTS variants as paired jobs (same
        seed and n); all or nothing."""
        if not isinstance(body, dict) or set(body) - {"base", "variants"}:
            raise MCError("body must be {base, variants}")
        base = body.get("base")
        variants = body.get("variants")
        if not isinstance(base, dict):
            raise MCError("base must be a request object")
        if "pair_with" in base:
            raise MCError("base cannot use pair_with")
        if not isinstance(variants, list) or not 1 <= len(variants) <= MAX_VARIANTS:
            raise MCError(f"variants must be a list of 1 to {MAX_VARIANTS} entries")
        names = ["base"]
        base_p = self._prepare(base)
        pins = {"n": base_p["req"]["n"], "seed": base_p["req"]["seed"]}
        prepared = [base_p]
        for v in variants:
            if not isinstance(v, dict) or set(v) - {"name", "overrides"}:
                raise MCError("each variant must be {name, overrides}")
            name = v.get("name")
            if not isinstance(name, str) or not VARIANT_NAME_RE.match(name) or name == "base" \
                    or name in names:
                raise MCError("variant name must be 1 to 40 characters (letters, digits, space, "
                              "_ - .), unique, and not 'base'")
            ov = v.get("overrides", {})
            if not isinstance(ov, dict) or set(ov) - OVERRIDE_KEYS:
                bad = "n and seed cannot be overridden: the jobs share them" \
                    if isinstance(ov, dict) and set(ov) & {"n", "seed"} \
                    else f"overrides may only hold {sorted(OVERRIDE_KEYS)}"
                raise MCError(f"variant {name!r}: {bad}")
            merged = _merge_request(base, ov, name)
            merged.update(pins)
            try:
                prepared.append(self._prepare(merged))
            except EngineError as exc:
                raise MCError(f"variant {name!r}: {exc}", code=exc.code) from None
            names.append(name)
        heads = self._admit(prepared, client, batch=True, owner=owner)
        return {"label": LABEL, "paired": True, "seed": pins["seed"], "n": pins["n"],
                "jobs": [{"name": nm, **h} for nm, h in zip(names, heads)]}

    def _head(self, rec: dict) -> dict:
        return {"job_id": rec["id"], "status": rec["status"], "quote": rec["quote"]}

    # ----------------------------------------------------------- worker
    def _run(self, rec: dict) -> None:
        job_id = rec["id"]
        req = rec["request"]
        n = req["n"]
        inputs = rec["inputs"]
        key = engines.engine_key(req["engine_rev"], req["profile"])
        try:
            engine, _identity = self._resolver(self._graph, req["engine_rev"], req["profile"])
            rng = sampling.make_rng(req["seed"])
            base = req["disruption"]
            rows: list = []
            places: dict = {}
            started = time.perf_counter()
            checkpoint = max(1, n // 10)
            for i in range(n):
                if rec["_cancel"].is_set():
                    return
                drawn = sampling.sample_inputs(rng, inputs)
                spec = {**base, "duration_d": drawn["duration_d"],
                        "capacity_loss": drawn["capacity_loss"]}
                state = engine.run(f"{job_id}_s{i}", spec,
                                   {"queue_sensitivity": drawn["queue_sensitivity"]})
                outputs, placed = metrics.extract(state.events, base.get("started_at"))
                rows.append({"i": i, "inputs": {k: drawn[k] for k in INPUT_ORDER},
                             "outputs": outputs, "stream_hash": stream_hash(state.events)})
                for p, pair in placed.items():
                    places.setdefault(p, []).append(pair)
                del state, placed
                with self._lock:
                    rec["progress"] = {"done": i + 1, "n": n}
                    rec["rows"] = rows
                    rec["_places"] = places
                    if (i + 1) % checkpoint == 0 and i + 1 < n \
                            and self._live.get(job_id) is rec:
                        self._store.save(self._persistable(rec))
            per_run = (time.perf_counter() - started) / n
            names = {p: self._graph.nodes.get(p, {}).get("name", p) for p in places}
            results, extras = aggregate.finalize(n, req["seed"], inputs, rows, places, names)
            self._note_perf(key, base["node_id"], per_run)
            with self._lock:
                if self._live.get(job_id) is not rec:
                    return                          # cancelled while finishing
                rec["status"] = "done"
                rec["completed_at"] = _now_iso()
                rec["results"] = results
                rec["place_table"] = extras["place_table"]
                rec["series_raw"] = extras["series_raw"]
                rec["quote"] = {**rec["quote"], "s_per_run_measured": round(per_run, 4)}
                self._store.save(self._persistable(rec))
                self._live.pop(job_id, None)
        except Exception as exc:                    # a failed run fails the job openly
            code = getattr(exc, "code", None) or "internal_error"
            with self._lock:
                if self._live.get(job_id) is not rec:
                    return
                rec["status"] = "failed"
                rec["completed_at"] = _now_iso()
                rec["error"] = {"code": code, "message": str(exc)[:300]}
                try:
                    self._store.save(self._persistable(rec))
                finally:
                    self._live.pop(job_id, None)

    # ------------------------------------------------------------ reads
    @staticmethod
    def _persistable(rec: dict) -> dict:
        out = {k: v for k, v in rec.items() if not k.startswith("_")}
        if out["status"] != "done":
            out["rows"] = []                      # progress checkpoints stay small
        return out

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            live = self._live.get(job_id)
            if live is not None:
                return live
        stored = self._store.load(job_id)
        if stored is None or stored.get("kind") != "monte_carlo":
            return None
        if stored.get("status") == "running":
            stored = {**stored, "status": "interrupted"}
        return stored

    def view(self, rec: dict, with_results: bool = True) -> dict:
        with self._lock:
            progress = dict(rec["progress"])
            status = rec["status"]
            results = rec.get("results")
        out = {
            "job_id": rec["id"], "status": status, "progress": progress,
            "label": LABEL, "request": rec["request"], "engine": rec["engine"],
            "inputs": rec["inputs"], "quote": rec["quote"], "error": rec.get("error"),
            "created_at": rec.get("created_at"), "pair_with": rec.get("pair_with"),
        }
        if with_results:
            out["results"] = results if status == "done" else None
        return out

    def list_jobs(self, limit: int = 50) -> list:
        """Newest first. Finished and orphaned jobs come from the store's small heads (never the full
        files); a persisted "running" head with no live worker reads as "interrupted", as in ``get``."""
        with self._lock:
            live = list(self._live.values())
        seen = {r["id"] for r in live}
        records = list(live)
        for head in self._store.list_heads():
            if head["id"] in seen or head.get("kind") != "monte_carlo":
                continue
            if head.get("status") == "running":
                head = {**head, "status": "interrupted"}
            records.append(head)
        records.sort(key=lambda r: (r.get("created_at") or "", r["id"]), reverse=True)
        return [self.view(r, with_results=False) for r in records[:limit]]

    def samples(self, job_id: str, offset: int, limit: int) -> dict:
        rec = self.get(job_id)
        if rec is None:
            raise JobNotFound(job_id)
        if rec["status"] != "done":
            raise JobNotReady(rec)
        rows = rec["rows"]
        return {"job_id": job_id, "total": len(rows), "offset": offset, "limit": limit,
                "rows": rows[offset:offset + limit]}

    def get_done(self, job_id: str) -> dict:
        rec = self.get(job_id)
        if rec is None:
            raise JobNotFound(job_id)
        if rec["status"] != "done":
            raise JobNotReady(rec)
        return rec

    def compare(self, a_id: str, b_id: str, threshold: float = 0.0) -> dict:
        recs = []
        for jid in (a_id, b_id):
            rec = self.get(jid)
            if rec is None:
                raise JobNotFound(jid)
            if rec["status"] != "done":
                raise JobNotReady(rec)
            recs.append(rec)
        key = (a_id, b_id, threshold)
        with self._lock:
            hit = self._cmp_cache.get(key)
        if hit is not None:
            return hit
        out = compare_mod.compare(recs[0], recs[1], threshold)
        with self._lock:
            if len(self._cmp_cache) >= 16:
                self._cmp_cache.pop(next(iter(self._cmp_cache)))
            self._cmp_cache[key] = out
        return out

    def delete(self, job_id: str, token: str = "") -> bool:
        """Cancel and remove a job on behalf of its creator. JobNotFound if unknown; JobForbidden
        without the matching token. A job created before ownership existed has no owner and cannot
        be deleted here (retention prunes it)."""
        rec = self.get(job_id)
        if rec is None:
            raise JobNotFound(job_id)
        stored = rec.get("owner")
        if not token or not isinstance(stored, str) or not hmac.compare_digest(stored, owner_digest(token)):
            raise JobForbidden(job_id)
        return self.cancel(job_id)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            rec = self._live.pop(job_id, None)
            if rec is not None:
                rec["_cancel"].set()
        removed = self._store.delete(job_id) if self._store.exists(job_id) else False
        if rec is None and not removed:
            raise JobNotFound(job_id)
        # a worker may have saved between pop and delete; make sure it is gone
        if self._store.exists(job_id):
            self._store.delete(job_id)
        return True
