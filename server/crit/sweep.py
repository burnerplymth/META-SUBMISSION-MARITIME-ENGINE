"""CRIT sweep: close each CORE place with the standard disruption,
run the chain engine, score by places hit and total delay added, rank.

Method: for every place whose served tier is "core",
one engine run with the standard disruption below. Score fields are
read straight off the run's event stream: places_hit = distinct places
in the stream, total_delay_d = sum of event delay_d, events = stream
length. Ranking: places_hit desc, total_delay_d desc, place_id asc
(deterministic). The sweep is a pure read over the engine — it never
persists runs to the run store and never mutates the graph.

Stored artifact: data/criticality/crit-<version>-<graph_hash8>.json
so a sweep is versioned to exactly the graph it was computed on; a
graph change makes the stored sweep stale by name, and the API says so.

Artifact version (the <version> filename component + the stored
"version" field): SWEEP_VERSION is the sweep generation. A new
generation gets its own file name, so a sweep on the same graph
never overwrites another generation's file. The stored
artifact also stamps the engine constants-registry hash it was
computed under. load_sweep resolves the current generation's file.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

# Sweep generation (part of the stored artifact's file name).
SWEEP_VERSION = "1.1"

STANDARD_DISRUPTION = {
    "kind": "closure",
    "capacity_loss": 1.0,
    "duration_d": 30.0,
    "shape": "step",
    "started_at": "2024-01-01T00:00:00Z",
}

METHOD = ("one engine run per CORE place with the standard disruption "
          "(closure, capacity_loss 1.0, 30d, step); places_hit = "
          "distinct places in the run's event stream; total_delay_d = "
          "sum of event delay_d over the stream")


def core_place_ids(graph) -> list[str]:
    """CORE places = served tier 'core'. A graph version that serves
    no tier field at all (the engine fixture) has no core/shell split
    to honour, so every place is swept and its row carries tier as
    served (absent). The production graph serves tiers, so the sweep
    there covers exactly the 200 CORE places."""
    core = sorted(nid for nid, n in graph.nodes.items()
                  if n.get("tier") == "core")
    if core:
        return core
    if any(n.get("tier") for n in graph.nodes.values()):
        return core
    return sorted(graph.nodes)


def sweep_row(engine, place_id: str) -> dict:
    node = engine.graph.nodes[place_id]
    spec = {**STANDARD_DISRUPTION, "node_id": place_id}
    state = engine.run(f"crit_{place_id}", spec, {})
    places = {ev["node_id"] for ev in state.events}
    return {
        "place_id": place_id,
        "name": node.get("name"),
        "kind": node.get("kind"),
        "places_hit": len(places),
        "total_delay_d": round(sum(float(ev["delay_d"]) for ev in state.events), 6),
        "events": len(state.events),
    }


def run_sweep(engine, place_ids: list[str] | None = None,
              progress=None) -> dict:
    """progress(done, total) is called after each place when given."""
    graph = engine.graph
    ids = place_ids if place_ids is not None else core_place_ids(graph)
    started = time.time()
    rows = []
    for i, pid in enumerate(ids):
        rows.append(sweep_row(engine, pid))
        if progress is not None:
            progress(i + 1, len(ids))
    rows.sort(key=lambda r: (-r["places_hit"], -r["total_delay_d"], r["place_id"]))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    try:
        from engine.constants import CONSTANTS_HASH
    except Exception:
        CONSTANTS_HASH = None
    return {
        "version": SWEEP_VERSION,
        "constants_hash": CONSTANTS_HASH,
        "graph_version": graph.version,
        "graph_hash": graph.hash,
        "method": METHOD,
        "standard_disruption": STANDARD_DISRUPTION,
        "places_swept": len(rows),
        "elapsed_s": round(time.time() - started, 3),
        "rows": rows,
    }


def sweep_filename(sweep: dict) -> str:
    return f"crit-{sweep['version']}-{sweep['graph_hash'][:8]}.json"


def store_sweep(sweep: dict, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / sweep_filename(sweep)
    # Per-writer unique temp name (pid + uuid), never the
    # shared deterministic .json.tmp.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(sweep, fh, sort_keys=True)
    tmp.replace(path)
    return path


def load_sweep(out_dir: str | Path, graph) -> dict | None:
    """The stored sweep of the CURRENT generation for exactly this
    graph, or None (an older generation's file does not load)."""
    path = Path(out_dir) / f"crit-{SWEEP_VERSION}-{graph.hash[:8]}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None
