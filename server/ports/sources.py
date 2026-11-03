"""Read-only data access for the ports tracker and the report/export
centre: graph (cached by file stat), folded events, satwatch scans,
money exposures, scorecard, ledger, run records. Every loader is
corrupt-tolerant (a bad input degrades to absent, never an exception
and never invented data).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .content_scope import guard_exposures

try:  # server/ on sys.path (the API's import convention)
    from store.store import _fold_events as _store_fold_events
except Exception:  # pragma: no cover - imported as server.* instead
    try:
        from server.store.store import _fold_events as _store_fold_events
    except Exception:
        _store_fold_events = None

TICK_SECONDS = {"hour": 3600, "day": 86400, "week": 604800,
                "month": 2592000}
ACTIVE_STATUSES = ("active", "developing")


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@dataclass
class Ctx:
    """Everything a request needs, resolved from the app's own paths."""
    graph_path: Path
    snapshots_dir: Path
    events_path: Path
    satwatch_dir: Path
    impact_dir: Path
    exposures_path: Path
    scorecard_path: Path
    ledger_path: Path
    run_store: object = None
    sim_store: object = None
    adapter_registry: object = None
    engine: object = None
    now_fn: Callable[[], datetime] = field(
        default=lambda: datetime.now(timezone.utc))

    @property
    def now(self) -> datetime:
        return self.now_fn()


def ctx_from_app(app) -> Ctx:
    st = app.state
    data_root = Path(getattr(st, "data_root"))
    ledger = getattr(st, "ledger_path", None) or (data_root / "ledger.jsonl")
    return Ctx(
        graph_path=Path(st.graph_path),
        snapshots_dir=Path(st.impact_snapshots_dir),
        events_path=Path(st.events_path),
        satwatch_dir=Path(st.satwatch_dir),
        impact_dir=Path(st.impact_data_dir),
        exposures_path=Path(st.exposures_path),
        scorecard_path=Path(st.scorecard_path),
        ledger_path=Path(ledger),
        run_store=getattr(st, "store", None),
        sim_store=getattr(st, "sim_store", None),
        adapter_registry=getattr(st, "adapter_registry", None),
        engine=getattr(st, "engine", None),
        now_fn=getattr(st, "ports_now", None)
        or (lambda: datetime.now(timezone.utc)),
    )


# ---- graph (cached by stat) --------------------------------------------
_GRAPH_CACHE: dict = {}


class GraphView:
    def __init__(self, doc: dict, file_sha256: str):
        self.version = doc.get("version")
        self.nodes = {n["id"]: n for n in doc.get("nodes", [])
                      if isinstance(n, dict) and n.get("id")}
        self.edges = [e for e in doc.get("edges", []) if isinstance(e, dict)]
        self.file_sha256 = file_sha256
        self.raw = doc
        self._adj = None
        self._engine_hash = None

    @property
    def engine_hash(self) -> str | None:
        """The engine's own hash of this map (what a run journal records
        as graph_hash), computed with the engine's canonical function."""
        if self._engine_hash is None:
            try:
                from engine.graph import graph_hash
                self._engine_hash = graph_hash(self.raw)
            except Exception:
                self._engine_hash = ""
        return self._engine_hash or None

    def legs_of(self, place_id: str) -> list[dict]:
        if self._adj is None:
            adj: dict = {}
            for e in self.edges:
                adj.setdefault(e.get("from"), []).append(e)
                adj.setdefault(e.get("to"), []).append(e)
            self._adj = adj
        return self._adj.get(place_id, [])


def load_graph(path: Path) -> GraphView | None:
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    hit = _GRAPH_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        raw = path.read_bytes()
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    view = GraphView(doc, hashlib.sha256(raw).hexdigest())
    _GRAPH_CACHE.clear()
    _GRAPH_CACHE[key] = view
    return view


# ---- events ---------------------------------------------------------------
def read_events(path: Path, now: datetime) -> list[dict] | None:
    """Folded events (lifecycle fold + expiry-derived resolved), or
    None when the store is absent/unreadable (distinct from an empty
    store: absence is NOT SERVED, emptiness is a real zero)."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    rows: list = []
    if path.suffix == ".jsonl":
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                rows.append(rec)
    else:
        try:
            doc = json.loads(text) if text.strip() else []
        except ValueError:
            return None
        rows = [r for r in (doc.get("events", []) if isinstance(doc, dict)
                            else doc) if isinstance(r, dict)]
    if _store_fold_events is not None:
        try:
            return _store_fold_events(rows, now=now)
        except Exception:
            pass
    return rows


# ---- satwatch -------------------------------------------------------------
def read_satwatch(dirpath: Path) -> dict:
    """node_id -> scan document."""
    out: dict = {}
    d = Path(dirpath)
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json"), key=lambda q: q.name):
        doc = load_json(p)
        if isinstance(doc, dict) and doc.get("node_id"):
            doc = {**doc, "_file": p.name}
            out[doc["node_id"]] = doc
    return out


# ---- money exposures ------------------------------------------------------
def read_exposures(path: Path) -> dict | None:
    doc = load_json(path)
    if isinstance(doc, dict) and isinstance(doc.get("mappings"), list):
        # content scope: out-of-scope filing excerpts are withheld here, for
        # the board, the report sections and the exports alike
        return guard_exposures(doc)
    return None


# ---- ledger ---------------------------------------------------------------
def read_ledger(path: Path) -> tuple[list[dict], bool]:
    p = Path(path)
    if not p.exists():
        return [], False
    out = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    except OSError:
        return [], False
    # merged view (as llm.ledger): a {"record_type": "grade"} line
    # attaches to its entry as entry["grade"]; the first grade wins
    grades: dict = {}
    for rec in out:
        if rec.get("record_type") == "grade" and rec.get("entry_id"):
            grades.setdefault(rec["entry_id"], rec.get("grade"))
    entries = []
    for rec in out:
        if rec.get("record_type") == "grade" or not rec.get("entry_id"):
            continue
        entries.append({**rec, "grade": grades.get(rec["entry_id"])})
    return entries, True
