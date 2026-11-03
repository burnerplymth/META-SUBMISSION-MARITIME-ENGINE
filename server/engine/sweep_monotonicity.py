"""Monotonicity sweep: a harsher shock must never make the engine report less.

Method: every seed
node in the shipped graph x losses {25, 50, 75, 100}% x durations
{7, 14, 30, 60, 90} d, capacity_cut / step shape. Adjacent pairs in
each direction are compared: a harsher shock must never LOSE a hit
node, never lose an event-carrying node, and never DECREASE any
common node's first-hit delay or max delay.

Elapsed time (`at` / total_delay_d) is a path quantity — a harsher
shock can open a shorter causal path — and is reported for honesty
but is NOT part of the gate.

Usage: python -m engine.sweep_monotonicity [--seeds N] [--json PATH]
Run from the server/ directory (engine package import root).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.engine import Engine          # noqa: E402
from engine.graph import load_graph       # noqa: E402

LOSSES = [0.25, 0.5, 0.75, 1.0]
DURS = [7, 14, 30, 60, 90]


def _run(eng: Engine, seed: str, loss: float, dur: float):
    st = eng.run("sweep", {"node_id": seed, "kind": "capacity_cut",
                           "capacity_loss": loss, "duration_d": dur,
                           "shape": "step",
                           "started_at": "2026-01-01T00:00:00Z"}, {})
    max_delay: dict[str, float] = {}
    for ev in st.events:
        n = ev["node_id"]
        max_delay[n] = max(max_delay.get(n, -1), ev["delay_d"])
    first_delay, total = {}, {}
    for nid, imp in st.hit.items():
        rec = st.impacts[imp.seq]
        first_delay[nid] = st.events[imp.seq]["delay_d"]
        total[nid] = imp.total_delay_d
    return set(st.hit), set(max_delay), first_delay, max_delay, total


def _compare(a, b, label, stats):
    hit_a, ev_a, fd_a, md_a, tot_a = a
    hit_b, ev_b, fd_b, md_b, tot_b = b
    lost_hit = hit_a - hit_b
    lost_ev = ev_a - ev_b
    if lost_hit:
        stats[f"{label}_hitset_inversion_pairs"] += 1
        stats[f"{label}_hitset_nodes_lost"] += len(lost_hit)
    if lost_ev:
        stats[f"{label}_eventset_inversion_pairs"] += 1
        stats[f"{label}_eventset_nodes_lost"] += len(lost_ev)
    for n in fd_a.keys() & fd_b.keys():
        stats[f"{label}_common_node_pairs"] += 1
        if fd_b[n] < fd_a[n] - 1e-9:
            stats[f"{label}_first_delay_decreases"] += 1
        if md_b[n] < md_a[n] - 1e-9:
            stats[f"{label}_max_delay_decreases"] += 1
        if tot_b[n] < tot_a[n] - 1e-9:
            stats[f"{label}_elapsed_decreases_not_gated"] += 1


def sweep(graph_path: Path, max_seeds: int | None = None) -> dict:
    eng = Engine(load_graph(graph_path))
    seed_ids = sorted(eng.graph.nodes)
    if max_seeds is not None:
        seed_ids = seed_ids[:max_seeds]
    results = {}
    for seed in seed_ids:
        for loss in LOSSES:
            for dur in DURS:
                results[(seed, loss, dur)] = _run(eng, seed, loss, dur)
    stats: dict[str, int] = {}
    for label in ("loss", "duration"):
        for suffix in ("hitset_inversion_pairs", "hitset_nodes_lost",
                       "eventset_inversion_pairs", "eventset_nodes_lost",
                       "common_node_pairs", "first_delay_decreases",
                       "max_delay_decreases", "elapsed_decreases_not_gated"):
            stats[f"{label}_{suffix}"] = 0
    for seed in seed_ids:
        for dur in DURS:
            for i in range(len(LOSSES) - 1):
                _compare(results[(seed, LOSSES[i], dur)],
                         results[(seed, LOSSES[i + 1], dur)], "loss", stats)
        for loss in LOSSES:
            for i in range(len(DURS) - 1):
                _compare(results[(seed, loss, DURS[i])],
                         results[(seed, loss, DURS[i + 1])], "duration", stats)
    return {"graph": str(graph_path), "seeds": len(seed_ids),
            "losses": LOSSES, "durations_d": DURS,
            "runs": len(results), **stats}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=None)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--graph", type=Path,
                    default=Path(__file__).resolve().parents[2]
                    / "data" / "graph" / "graph.json")
    args = ap.parse_args()
    out = sweep(args.graph, args.seeds)
    print(json.dumps(out, indent=1, sort_keys=True))
    if args.json:
        args.json.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n",
                             encoding="utf-8")
    gated = [v for k, v in out.items()
             if isinstance(v, int) and "not_gated" not in k
             and k not in ("seeds", "runs")
             and ("inversion" in k or "decreases" in k or "lost" in k)]
    return 1 if any(gated) else 0


if __name__ == "__main__":
    raise SystemExit(main())
