#!/usr/bin/env python3
"""SIM historical backtest runner — implements ONLY the extraction
definitions registered in preregistration.md (registered before this
script was first run). Shipped sim, shipped constants, seed 42, day
ticks, weather off. Writes results/<event>_raw.json with every quantity
the scorecard grades on, so each grade can be recomputed from these
files alone (+ the stored actuals/series).

Usage: .venv/bin/python data/sim_backtest/run_sim_backtest.py [event ...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "server"))

from sim.derive import derive                      # noqa: E402
from sim.simulate import simulate, parse_ts        # noqa: E402
from sim.twin import added_delay_by_node           # noqa: E402

DAY = 86400
HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def corridor_lanes(derivation: dict, node: str) -> set[str]:
    """Lanes whose derivation transit path includes `node`
    (the shipped demo run's identification method)."""
    out = set()
    for ln in derivation["lanes"]:
        p = derivation["paths"].get(ln["key"])
        if p and any(tr["node"] == node for tr in p["transits"]):
            out.add(ln["key"])
    return out


def segs(sim: dict, kind: str):
    for eid, evs in sim["segments"].items():
        for s in evs:
            if s["kind"] == kind:
                yield eid, s


def day_index(t: float, start: float) -> int:
    return int((t - start) // DAY)


def run_event(name: str, cfg: dict, graph: dict) -> dict:
    start = parse_ts(cfg["start_at"])
    ticks = int(cfg["ticks"])
    end = start + ticks * DAY
    disruptions = cfg["disruptions"]
    node = cfg["graph_node"]
    d = derive(graph, 42, start, end)
    dis = simulate(graph, d, disruptions, None, start, DAY, ticks)
    twin = simulate(graph, d, [], None, start, DAY, ticks)

    raw: dict = {"event": name, "start_at": cfg["start_at"],
                 "ticks": ticks, "seed": 42, "node": node,
                 "disruptions": disruptions,
                 "roster_entities": len(d["entities"])}
    # queue series (full arrays stored; grades are recomputed from these)
    raw["queue_disrupted"] = dis["node_series"][node]["queue"]
    raw["queue_twin"] = twin["node_series"][node]["queue"]
    raw["queue_peak_disrupted"] = max(raw["queue_disrupted"])
    raw["queue_peak_twin"] = max(raw["queue_twin"])
    raw["excess_peak_queue"] = (raw["queue_peak_disrupted"]
                                - raw["queue_peak_twin"])
    # twin delay statistics (shipped function, as the demo run calls it)
    stats = added_delay_by_node(dis, twin)
    raw["delay_stats"] = {n: stats[n] for n in sorted(stats)}
    # divert segments + hold arrivals at the blocked node
    raw["divert_segments"] = [
        {"t": s["t"], "node": s["node"], "lane": s["lane"],
         "avoided": s.get("avoided")}
        for _, s in segs(dis, "divert")]
    raw["hold_arrivals_blocked_node"] = (
        stats.get(node, {}).get("hold_arrivals", 0))

    if name == "ever_given":
        lanes = corridor_lanes(d, "chk_suez")
        dep = sum(1 for _, s in segs(twin, "depart")
                  if s["lane"] in lanes and s["t"] >= start)
        raw["twin_corridor_departures"] = dep
        raw["twin_corridor_departures_per_day"] = dep / ticks
        raw["excess_peak_queue_days_of_flow"] = (
            raw["excess_peak_queue"]
            / raw["twin_corridor_departures_per_day"])
        # clearance: first tick after window end, dis queue <= twin
        # queue sustained 3 ticks
        w = disruptions[0]
        wend = parse_ts(w["started_at"]) + float(w["duration_d"]) * DAY
        raw["window_end_epoch"] = wend
        clr = None
        for k in range(ticks - 2):
            tick_t = start + k * DAY
            if tick_t <= wend:
                continue
            if all(raw["queue_disrupted"][k + j] <= raw["queue_twin"][k + j]
                   for j in range(3)):
                clr = (tick_t - wend) / DAY
                break
        raw["clearance_days_sim"] = clr

    if name == "baltimore":
        def berth_by_day_node(sim: dict, nd: str) -> list[int]:
            out = [0] * ticks
            for _, s in segs(sim, "berth"):
                if s["node"] == nd and s["t"] >= start:
                    k = day_index(s["t"], start)
                    if 0 <= k < ticks:
                        out[k] += 1
            return out

        for label, sim in (("disrupted", dis), ("twin", twin)):
            raw[f"berth_baltimore_{label}"] = berth_by_day_node(
                sim, "sea_baltimore")
            raw[f"berth_virginia_{label}"] = berth_by_day_node(
                sim, "sea_virginia")
        w = disruptions[0]
        ws = day_index(parse_ts(w["started_at"]), start)
        we = day_index(parse_ts(w["started_at"])
                       + float(w["duration_d"]) * DAY, start)
        raw["closure_day_span"] = [ws, we]  # [first, last) day indices
        def wmean(arr: list[int]) -> float:
            seg = arr[ws:we]
            return sum(seg) / len(seg)
        raw["berth_baltimore_mean_disrupted"] = wmean(
            raw["berth_baltimore_disrupted"])
        raw["berth_baltimore_mean_twin"] = wmean(raw["berth_baltimore_twin"])
        raw["calls_drop_pct_sim"] = 100.0 * (
            raw["berth_baltimore_mean_disrupted"]
            - raw["berth_baltimore_mean_twin"]) / raw["berth_baltimore_mean_twin"]
        raw["berth_virginia_mean_disrupted"] = wmean(
            raw["berth_virginia_disrupted"])
        raw["berth_virginia_mean_twin"] = wmean(raw["berth_virginia_twin"])
        raw["virginia_change_pct_sim"] = 100.0 * (
            raw["berth_virginia_mean_disrupted"]
            - raw["berth_virginia_mean_twin"]) / raw["berth_virginia_mean_twin"]
        # recovery: first day after window end with berth >= 90% of
        # twin same-day, sustained 3 days
        rec = None
        for k in range(we, ticks - 2):
            if all(raw["berth_baltimore_disrupted"][k + j]
                   >= 0.9 * raw["berth_baltimore_twin"][k + j]
                   for j in range(3)):
                rec = k - we
                break
        raw["recovery_days_sim"] = rec

    if name == "red_sea":
        suez_lanes = corridor_lanes(d, "chk_suez")
        bem_lanes = corridor_lanes(d, "chk_babelmandeb")
        w0 = parse_ts("2024-01-01T00:00:00Z")
        w1 = parse_ts("2024-03-01T00:00:00Z")

        def suez_completions(sim: dict) -> int:
            n = 0
            for _, s in segs(sim, "arrive"):
                if s["lane"] not in suez_lanes:
                    continue
                if not (w0 <= s["t"] < w1):
                    continue
                if s.get("hold"):
                    continue
                if s.get("diverted") and s.get("avoided") in (
                        "chk_suez", "chk_babelmandeb"):
                    continue
                n += 1
            return n

        c_dis, c_twin = suez_completions(dis), suez_completions(twin)
        raw["suez_completions_disrupted"] = c_dis
        raw["suez_completions_twin"] = c_twin
        raw["suez_drop_pct_sim"] = 100.0 * (c_dis - c_twin) / c_twin
        # corridor arrival lateness (demo-run occurrence matching,
        # generalized to Bab-el-Mandeb-transiting lanes, pooled over
        # destinations; disrupted arrival inside the metric window)
        eids = {e["id"] for e in d["entities"] if e["lane"] in bem_lanes}

        def occ(sim: dict) -> dict:
            out: dict = {}
            for eid, evs in sim["arrival_events"].items():
                if eid not in eids:
                    continue
                per: dict[str, list[float]] = {}
                for ev in evs:
                    if not ev["hold"]:
                        per.setdefault(ev["node"], []).append(ev["t"])
                out[eid] = per
            return out

        od, ot = occ(dis), occ(twin)
        pairs = []
        for eid, per in od.items():
            tper = ot.get(eid, {})
            for nd, ts in per.items():
                tt = tper.get(nd, [])
                for i, t in enumerate(ts):
                    if i < len(tt) and w0 <= t < w1:
                        pairs.append((t - tt[i]) / DAY)
        raw["corridor_lateness_pairs_d"] = [round(p, 6) for p in pairs]
        raw["corridor_lateness_n"] = len(pairs)
        raw["corridor_lateness_mean_d"] = (
            sum(pairs) / len(pairs) if pairs else None)
        raw["corridor_legs_departed_window"] = sum(
            1 for _, s in segs(dis, "depart")
            if s["lane"] in bem_lanes and w0 <= s["t"] < w1)
    return raw


def main() -> int:
    cfgs = json.loads((HERE / "run_configs.json").read_text())["events"]
    graph = json.loads((REPO / "data/graph/graph.json").read_text())
    RESULTS.mkdir(exist_ok=True)
    wanted = sys.argv[1:] or list(cfgs)
    for name in wanted:
        raw = run_event(name, cfgs[name], graph)
        (RESULTS / f"{name}_raw.json").write_text(
            json.dumps(raw, indent=1, sort_keys=True) + "\n")
        print(f"{name}: done -> results/{name}_raw.json", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
