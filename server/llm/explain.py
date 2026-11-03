"""Explain: run_id -> narrative of the chain.

The chain is fetched in the stored-run shapes ({run_id, status, event_count,
stream_hash, events, edges}) through an injectable fetcher (a
fake can be injected; the default fetcher reads the stored run records under data/runs/
read-only, mirroring what GET /chain/:run_id serves). LLM output is never
an engine input.

Caching (hard rule): narratives are cached. A second call for
the same cache key returns cached=true at cost $0 and makes NO provider
call. Cache store choice: a JSON file, <data root>/llm/explain_cache.json
(runtime writes; see paths.py). The tracked server/llm/explain_cache.json
is a read-only seed consulted on a miss, never written.

The key is not the bare
stream_hash, so explain() and narrate() on the same chain never serve each
other's text and entries do not carry across transport/model switches. The
key is a hash of
(stream_hash, prompt_name, prompt_version, model, transport). Seed entries
keyed by a bare hash never match a key, so they miss and are evicted by the
cap below.
Writes are flock-protected and atomic via a UNIQUE tmp name
(pid + uuid) + replace.
Size cap: EXPLAIN_CACHE_MAX_ENTRIES = 512 entries, oldest evicted
first (insertion order) — a sane bound for a demo-scale narrative
cache whose entries are a few hundred bytes each.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

try:  # The ONE materialise_edges (cycle-free leaf)
    from ..chain_edges import materialise_edges
except Exception:  # imported as a top-level package (server/ on sys.path)
    from chain_edges import materialise_edges  # type: ignore

from . import grounding
from . import provenance as prov
from .explain_check import check_narrative, render_template
from .paths import SEED_EXPLAIN_CACHE, default_explain_cache_path
from .provider import LLMProvider

MODULE_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODULE_DIR.parents[1]
# The tracked server/llm/explain_cache.json is a READ-ONLY seed.
# Runtime writes go to <data root>/llm/explain_cache.json (paths.py);
# SUPERMAP_LLM_EXPLAIN_CACHE_PATH / SUPERMAP_EXPLAIN_CACHE_PATH override it (scratch runs).
DEFAULT_CACHE_PATH = SEED_EXPLAIN_CACHE  # kept for importers; read-only

# Cache size bound (see module docstring); oldest-first eviction.
EXPLAIN_CACHE_MAX_ENTRIES = 512


def canonical_stream_hash(events: list[dict]) -> str:
    """SHA-256 over canonical JSON of the event array."""
    canon = json.dumps(events, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def default_chain_fetcher(run_id: str) -> dict:
    """Read a stored run record (data/runs/<run_id>.json) in chain-payload shape."""
    if (not isinstance(run_id, str) or not run_id or len(run_id) > 128
            or "/" in run_id or "\\" in run_id or ".." in run_id
            or "\x00" in run_id):
        raise LookupError(
            f"unknown run_id: {str(run_id)[:64]!r} (no stored run record)")
    path = REPO_ROOT / "data" / "runs" / f"{run_id}.json"
    if not path.exists():
        raise LookupError(f"unknown run_id: {run_id} (no stored run record)")
    rec = json.loads(path.read_text(encoding="utf-8"))
    events = rec.get("events", [])
    edges = materialise_edges(
        events,
        rec.get("journal", {}).get("disruption", {}).get("node_id", ""))
    return {
        "run_id": rec.get("run_id", run_id),
        "status": rec.get("status", "complete"),
        "event_count": len(events),
        "stream_hash": rec.get("stream_hash") or canonical_stream_hash(events),
        "events": events,
        "edges": edges,
    }


class Explainer:
    def __init__(self, provider: LLMProvider | None = None,
                 chain_fetcher: Callable[[str], dict] | None = None,
                 cache_path: Path | None = None):
        self.provider = provider or LLMProvider.from_env()
        self.fetch_chain = chain_fetcher or default_chain_fetcher
        env_path = (os.environ.get("SUPERMAP_LLM_EXPLAIN_CACHE_PATH")
                    or os.environ.get("SUPERMAP_EXPLAIN_CACHE_PATH"))
        self.cache_path = Path(cache_path or env_path or default_explain_cache_path())
        # Seed fixtures are consulted only for the default runtime cache
        # (an explicit path or the env override is a self-contained cache).
        self.seed_path = SEED_EXPLAIN_CACHE if (cache_path is None and not env_path) else None

    def _cache_key(self, stream_hash: str, prompt_name: str) -> str:
        """Cache key: hash of (stream_hash, prompt_name,
        prompt_version, model, transport) — two surfaces over one chain,
        or one surface across a model/transport switch, never collide."""
        parts = [stream_hash, prompt_name,
                 self.provider.prompt_version(prompt_name),
                 self.provider.model, self.provider.transport_name]
        return hashlib.sha256(
            json.dumps(parts, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    def _cache(self) -> dict:
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _seed(self) -> dict:
        if self.seed_path is None:
            return {}
        try:
            data = json.loads(self.seed_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @contextmanager
    def _cache_lock(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(str(self.cache_path) + ".lock")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _write_cache_file(self, cache: dict) -> None:
        """Evict to the cap (oldest first) and write atomically via a
        UNIQUE tmp name + replace. CALLER MUST HOLD _cache_lock."""
        while len(cache) > EXPLAIN_CACHE_MAX_ENTRIES:
            cache.pop(next(iter(cache)))
        tmp = self.cache_path.with_name(
            f".{self.cache_path.name}.{os.getpid()}."
            f"{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(cache, indent=2, sort_keys=True),
                       encoding="utf-8")
        tmp.replace(self.cache_path)

    def _save_cache(self, cache: dict) -> None:
        with self._cache_lock():
            self._write_cache_file(cache)

    def _store_entry(self, key: str, value: dict) -> None:
        """Locked read-modify-write of one entry, so concurrent
        explainers cannot clobber each other's inserts."""
        with self._cache_lock():
            cache = self._cache()
            cache[key] = value
            self._write_cache_file(cache)

    def cost_preview(self, run_id: str) -> dict:
        chain = self.fetch_chain(run_id)
        return self.provider.cost_preview(
            "explain", {"run_id": run_id,
                        "stream_hash": chain["stream_hash"],
                        "events": chain["events"]})

    def explain(self, run_id: str, prompt_name: str = "explain") -> dict:
        chain = self.fetch_chain(run_id)
        stream_hash = chain["stream_hash"]
        key = self._cache_key(stream_hash, prompt_name)
        cache = self._cache()
        hit = cache.get(key)
        if hit is None:
            hit = self._seed().get(key)
        if hit is not None:
            return {"run_id": run_id, "stream_hash": stream_hash,
                    "narrative": hit["narrative"], "model": hit["model"],
                    "narrative_source": hit.get("narrative_source", "model"),
                    "post_check": hit.get("post_check", "PASS"),
                    "cost_usd": 0.0, "cached": True,
                    "transport": hit.get("transport", self.provider.transport_name),
                    "provenance": prov.make(
                        model=hit["model"],
                        transport_raw=hit.get("transport",
                                              self.provider.transport_name),
                        prompt_name=prompt_name,
                        prompt_version=hit.get(
                            "prompt_version",
                            self.provider.prompt_version(prompt_name)),
                        cost_usd=0.0, cached=True) | {
                        "narrative_source": hit.get("narrative_source", "model"),
                        "post_check": hit.get("post_check", "PASS")}}
        payload = {"run_id": run_id, "stream_hash": stream_hash,
                   "events": chain["events"]}
        res = self.provider.complete(prompt_name, payload)
        # Edge-out discipline: the model may only restate the chain. A
        # number or node the step records do not contain voids the text;
        # the deterministic template is served instead (TEMPLATE chip).
        ok, _reason = check_narrative(res["text"], chain)
        if ok:
            # A graph place NAMED in prose (not only an id token) that the
            # chain does not contain also voids the text.
            in_chain = {ev.get("node_id") for ev in chain.get("events") or []}
            nodes = grounding.graph_places()
            if nodes and grounding.foreign_place_names(
                    res["text"], in_chain, nodes):
                ok, _reason = False, "place_not_in_chain"
        narrative = res["text"] if ok else render_template(chain)
        source = "model" if ok else "template"
        post_check = "PASS" if ok else "TEMPLATE_FALLBACK"
        self._store_entry(key, {
            "run_id": run_id, "stream_hash": stream_hash,
            "prompt_name": prompt_name,
            "prompt_version": res["prompt_version"],
            "narrative": narrative, "model": res["model"],
            "narrative_source": source, "post_check": post_check,
            "transport": res["transport"]})
        return {"run_id": run_id, "stream_hash": stream_hash,
                "narrative": narrative, "model": res["model"],
                "narrative_source": source, "post_check": post_check,
                "cost_usd": res["cost_usd"], "cached": False,
                "transport": res["transport"],
                "provenance": {**prov.from_result(res, prompt_name),
                               "narrative_source": source,
                               "post_check": post_check}}

    def narrate(self, run_id: str) -> dict:
        """Director-style narration (narrate prompt); same cache rule."""
        return self.explain(run_id, prompt_name="narrate")
