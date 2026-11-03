"""Causal ensembles.

Parameter-variant runs of the SAME disruption: the grid below is fixed and
deterministic (no sampling, no RNG). Output is a RANKED list of recurring
bottleneck dominoes: how many of the variant runs each node was hit in
(a count over an enumerated grid — never a percentage presented as a
forecast probability), with first-hit positions. Ranking: hit count desc,
then mean first-hit seq asc, then node_id asc (fully deterministic).
"""
from __future__ import annotations

from .engine import Engine, validate_disruption

QS_GRID = (0.5, 1.0, 2.0)
THR_GRID = (0.10, 0.15, 0.25)
DURATION_MULTS = (0.5, 1.0, 2.0)


def run_ensemble(engine: Engine, disruption: dict, run_id_prefix: str = "ens") -> dict:
    spec = validate_disruption(engine.graph, disruption)
    stats: dict[str, dict] = {}
    n_variants = 0
    for qs in QS_GRID:
        for thr in THR_GRID:
            for mult in DURATION_MULTS:
                n_variants += 1
                variant = dict(spec)
                variant["duration_d"] = spec["duration_d"] * mult
                state = engine.run(f"{run_id_prefix}_{n_variants}", variant,
                                   {"queue_sensitivity": qs, "hit_threshold": thr})
                first_seq: dict[str, int] = {}
                for ev in state.events:
                    first_seq.setdefault(ev["node_id"], ev["seq"])
                for node_id, seq in first_seq.items():
                    st = stats.setdefault(node_id, {"node_id": node_id, "hits": 0,
                                                    "first_seqs": []})
                    st["hits"] += 1
                    st["first_seqs"].append(seq)
    ranked = []
    for st in stats.values():
        seqs = sorted(st["first_seqs"])
        ranked.append({
            "node_id": st["node_id"],
            "hits": st["hits"],               # count of variant runs, NOT a probability
            "variants": n_variants,
            "first_seq_min": seqs[0],
            "first_seq_mean": round(sum(seqs) / len(seqs), 3),
        })
    ranked.sort(key=lambda r: (-r["hits"], r["first_seq_mean"], r["node_id"]))
    return {"variants": n_variants, "ranked_bottlenecks": ranked}
