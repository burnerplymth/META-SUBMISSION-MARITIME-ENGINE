"""Undisrupted-twin delay accounting + chain consistency.

The twin is the SAME run with an empty disruption set — same graph,
same derivation (derivation never depends on disruptions), same seed,
same weather snapshot. ``added_delay_by_node`` compares the disrupted
simulation's leg arrivals against the twin's, per place: for each
(entity, place) pair, arrivals are matched by occurrence order and
the lateness (disrupted epoch - twin epoch) is averaged over matched
arrivals. Hold arrivals at a chokepoint (a reaction with no twin
counterpart — the twin vessel sails through) are counted separately
as ``hold_arrivals``, never silently dropped and never averaged in.

Choice: the twin simulation runs AT RUN CREATION
inside ``runner.build_record`` and only the per-node statistics are
stored on the record (``sim_delay_by_node``) — state reads and run
detail reads never re-simulate. When no linked chain run exists, nothing
here changes any payload.

``consistency_block`` is a pure function of the stored sim record +
stored chain run records (read-only): it reuses the markers link
rule (``markers._chain_matches``) to find the linked chain run, then
places, per affected place, the chain's delay (days, the run's max
``delay_d`` at that place) beside the sim's mean added delay, with a
divergence flag when the ratio sim/chain falls outside [0.5, 2.0].
Divergence is printed, never corrected; the chain remains the unit
of truth and the sim figure is labelled MODELLED. No chain-store writes, ever.
"""
from __future__ import annotations

from .markers import _chain_matches

DIVERGENCE_LO = 0.5
DIVERGENCE_HI = 2.0


def _leg_arrivals(sim: dict) -> dict:
    """(entity, node) -> ordered epochs of leg (non-hold) arrivals."""
    out: dict[tuple, list[float]] = {}
    for eid, evs in (sim.get("arrival_events") or {}).items():
        for ev in evs:
            if ev.get("hold"):
                continue
            out.setdefault((eid, ev["node"]), []).append(float(ev["t"]))
    for v in out.values():
        v.sort()
    return out


def added_delay_by_node(disrupted: dict, twin: dict,
                        core_nodes: set | None = None) -> dict:
    """Per-node sim delay statistics: disrupted vs undisrupted twin.

    Returns {node_id: {mean_added_delay_d, matched_arrivals,
    unmatched_arrivals, hold_arrivals}} — only nodes with at least
    one disrupted leg arrival or hold are present.

    When ``core_nodes`` is given (runner.py passes the run graph's CORE
    place set), statistics are computed over CORE scope ONLY — arrivals at
    shell places never enter the aggregates (the delay a shell queue
    imposes on a core-bound vessel still lands here, at the CORE endpoint
    where that vessel arrives). ``core_nodes=None`` keeps the unfiltered
    behaviour for callers without a run graph."""
    twin_arr = _leg_arrivals(twin)
    stats: dict[str, dict] = {}
    seen: dict[tuple, int] = {}
    for eid, evs in (disrupted.get("arrival_events") or {}).items():
        for ev in evs:
            node = ev["node"]
            if core_nodes is not None and node not in core_nodes:
                continue
            s = stats.setdefault(node, {"total_lateness_s": 0.0,
                                        "matched_arrivals": 0,
                                        "unmatched_arrivals": 0,
                                        "hold_arrivals": 0})
            if ev.get("hold"):
                s["hold_arrivals"] += 1
                continue
            key = (eid, node)
            idx = seen.get(key, 0)
            seen[key] = idx + 1
            twins = twin_arr.get(key) or []
            if idx < len(twins):
                s["total_lateness_s"] += float(ev["t"]) - twins[idx]
                s["matched_arrivals"] += 1
            else:
                s["unmatched_arrivals"] += 1
    out: dict[str, dict] = {}
    for node, s in stats.items():
        matched = s["matched_arrivals"]
        out[node] = {
            "mean_added_delay_d": (round(s["total_lateness_s"] / matched
                                         / 86400.0, 6) if matched else None),
            "matched_arrivals": matched,
            "unmatched_arrivals": s["unmatched_arrivals"],
            "hold_arrivals": s["hold_arrivals"],
            "label": "MODELLED",
        }
    return out


def consistency_block(record: dict, chain_runs: list[dict] | tuple) -> dict | None:
    """The consistency block for one sim record, or None when
    no chain run links to the sim run's disruption set (the markers
    link rule) — absence is normal and changes nothing."""
    journal = record.get("journal") or {}
    matched = [r for r in chain_runs if _chain_matches(journal, r)]
    if not matched:
        return None
    run = sorted(matched, key=lambda r: r.get("run_id") or "")[0]
    chain_delay: dict[str, float] = {}
    for ev in run.get("events") or []:
        node = ev.get("node_id")
        delay = ev.get("delay_d")
        if node is None or not isinstance(delay, (int, float)):
            continue
        chain_delay[node] = max(chain_delay.get(node, float("-inf")),
                                float(delay))
    sim_stats = record.get("sim_delay_by_node") or {}
    nodes_out = []
    for node in sorted(chain_delay):
        cdelay = chain_delay[node]
        s = sim_stats.get(node) or {}
        sim_mean = s.get("mean_added_delay_d")
        ratio = None
        divergent = None
        if sim_mean is not None and cdelay > 0.0:
            ratio = round(sim_mean / cdelay, 6)
            divergent = not (DIVERGENCE_LO <= ratio <= DIVERGENCE_HI)
        elif sim_mean is not None and cdelay == 0.0:
            divergent = sim_mean > 0.5
        nodes_out.append({
            "node_id": node,
            "chain_delay_d": cdelay,
            "sim_mean_added_delay_d": sim_mean,
            "sim_matched_arrivals": s.get("matched_arrivals", 0),
            "sim_hold_arrivals": s.get("hold_arrivals", 0),
            "ratio": ratio,
            "divergent": divergent,
            "sim_label": "MODELLED",
        })
    return {
        "linked_run_id": run.get("run_id"),
        "nodes": nodes_out,
        "basis": ("chain delay_d = the linked chain run's max delay at the "
                  "place (unit of truth); sim figure = mean added leg-"
                  "arrival delay vs the run's undisrupted twin, "
                  "MODELLED; divergence flagged outside ratio "
                  f"[{DIVERGENCE_LO}, {DIVERGENCE_HI}], printed never "
                  "corrected"),
    }
