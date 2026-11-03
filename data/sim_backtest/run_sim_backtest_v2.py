#!/usr/bin/env python3
"""Extended SIM backtest runner — implements ONLY the extractions registered
in preregistration_v2.md (registered before the extended mechanisms
were built). Same discipline as the baseline run: shipped sim, seed 42, day ticks, weather
off, twin-paired; every graded quantity stored in results_v2/<event>_raw.json
so each grade can be recomputed from these files alone
(+ the stored actuals/series). The original events reuse the baseline
extraction code paths verbatim; Baltimore's window/recovery span comes
from the registered steps (first step start -> last step end).

Usage: .venv/bin/python data/sim_backtest/run_sim_backtest_v2.py [event ...]
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
RESULTS = HERE / "results_v2"


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


def completions(sim: dict, lanes: set, w0: float, w1: float,
                avoid_nodes: tuple) -> int:
    """The RS-1 completion extraction, generalized: arrived (berth-
    reached), non-hold arrivals on `lanes` inside [w0, w1), excluding
    arrivals marked diverted with an avoided node in `avoid_nodes`."""
    n = 0
    for _, s in segs(sim, "arrive"):
        if s["lane"] not in lanes:
            continue
        if not (w0 <= s["t"] < w1):
            continue
        if s.get("hold"):
            continue
        if s.get("diverted") and s.get("avoided") in avoid_nodes:
            continue
        n += 1
    return n


def run_event(name: str, cfg: dict, graph: dict) -> dict:
    start = parse_ts(cfg["start"])
    ticks = int(cfg["ticks"])
    end = start + ticks * DAY
    disruptions = cfg["disruption"]
    node = cfg["graph_node"]
    d = derive(graph, 42, start, end)
    dis = simulate(graph, d, disruptions, None, start, DAY, ticks)
    twin = simulate(graph, d, [], None, start, DAY, ticks)

    raw: dict = {"event": name, "start_at": cfg["start"],
                 "ticks": ticks, "seed": 42, "node": node,
                 "disruptions": disruptions,
                 "roster_entities": len(d["entities"])}
    raw["queue_disrupted"] = dis["node_series"][node]["queue"]
    raw["queue_twin"] = twin["node_series"][node]["queue"]
    raw["queue_peak_disrupted"] = max(raw["queue_disrupted"])
    raw["queue_peak_twin"] = max(raw["queue_twin"])
    raw["excess_peak_queue"] = (raw["queue_peak_disrupted"]
                                - raw["queue_peak_twin"])
    stats = added_delay_by_node(dis, twin)
    raw["delay_stats"] = {n: stats[n] for n in sorted(stats)}
    raw["divert_segments"] = [
        {"t": s["t"], "node": s["node"], "lane": s["lane"],
         "avoided": s.get("avoided")}
        for _, s in segs(dis, "divert")]
    raw["hold_arrivals_blocked_node"] = (
        stats.get(node, {}).get("hold_arrivals", 0))
    raw["reassignment_accounting"] = dis.get("reassignment_accounting", {})

    if name == "ever_given":
        lanes = corridor_lanes(d, "chk_suez")
        dep = sum(1 for _, s in segs(twin, "depart")
                  if s["lane"] in lanes and s["t"] >= start)
        raw["twin_corridor_departures"] = dep
        raw["twin_corridor_departures_per_day"] = dep / ticks
        raw["excess_peak_queue_days_of_flow"] = (
            raw["excess_peak_queue"]
            / raw["twin_corridor_departures_per_day"])
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
        # Span from the registered steps: first step start -> last
        # step end (preregistration_v2.md §2; the baseline flat span rule
        # applied to the stepped form).
        steps = disruptions[0]["steps"]
        span_start = min(parse_ts(s["started_at"]) for s in steps)
        span_end = max(parse_ts(s["started_at"])
                       + float(s["duration_d"]) * DAY for s in steps)
        ws = day_index(span_start, start)
        we = day_index(span_end, start)
        raw["closure_day_span"] = [ws, we]

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
        # BA-2v tonnage basis (preregistration_v2.md §2): the twin's
        # Virginia daily tonnage = twin berth calls/day x mean lane
        # capacity share of Virginia-serving lanes x Virginia's
        # capacity. Stored as components so the grade can be recomputed.
        va_lanes = [ln for ln in d["lanes"]
                    if ln["mode"] == "sea" and not ln["flow_only"]
                    and "sea_virginia" in (ln["from"], ln["to"])]
        raw["virginia_mean_lane_share"] = (
            sum(ln["capacity_share"] for ln in va_lanes) / len(va_lanes))
        va_node = next(n for n in graph["nodes"]
                       if n["id"] == "sea_virginia")
        raw["virginia_capacity"] = float(va_node["capacity"])
        raw["virginia_twin_tonnage_per_day"] = (
            raw["berth_virginia_mean_twin"]
            * raw["virginia_mean_lane_share"] * raw["virginia_capacity"])
        raw["span_days"] = we - ws
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
        avoid = ("chk_suez", "chk_babelmandeb")
        c_dis = completions(dis, suez_lanes, w0, w1, avoid)
        c_twin = completions(twin, suez_lanes, w0, w1, avoid)
        raw["suez_completions_disrupted"] = c_dis
        raw["suez_completions_twin"] = c_twin
        raw["suez_drop_pct_sim"] = 100.0 * (c_dis - c_twin) / c_twin
        # RS-3 (registered exposed-subpopulation addition) + ungraded decomposition.
        both = suez_lanes & bem_lanes
        raw["lanes_transiting_suez"] = len(suez_lanes)
        raw["lanes_transiting_bem"] = len(bem_lanes)
        raw["lanes_transiting_both"] = len(both)
        if both:
            b_dis = completions(dis, both, w0, w1, avoid)
            b_twin = completions(twin, both, w0, w1, avoid)
            raw["both_completions_disrupted"] = b_dis
            raw["both_completions_twin"] = b_twin
            raw["both_drop_pct_sim"] = (
                100.0 * (b_dis - b_twin) / b_twin if b_twin else None)
        else:
            raw["both_drop_pct_sim"] = None
        for label, lset in (("bem_only", bem_lanes - suez_lanes),
                            ("suez_only", suez_lanes - bem_lanes)):
            x_dis = completions(dis, lset, w0, w1, avoid)
            x_twin = completions(twin, lset, w0, w1, avoid)
            raw[f"decomp_{label}_disrupted"] = x_dis
            raw[f"decomp_{label}_twin"] = x_twin
            raw[f"decomp_{label}_drop_pct"] = (
                100.0 * (x_dis - x_twin) / x_twin if x_twin else None)
        # RS-2 (baseline extraction, verbatim)
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

    if name in ("panama", "panama_august"):
        lanes = corridor_lanes(d, "chk_panama")
        dep = sum(1 for _, s in segs(twin, "depart")
                  if s["lane"] in lanes and s["t"] >= start)
        raw["twin_corridor_departures"] = dep
        raw["twin_corridor_departures_per_day"] = dep / ticks
        raw["excess_peak_queue_days_of_flow"] = (
            raw["excess_peak_queue"]
            / raw["twin_corridor_departures_per_day"])
        if name == "panama":
            w0 = parse_ts("2023-12-01T00:00:00Z")
            w1 = parse_ts("2024-01-16T00:00:00Z")
            avoid = ("chk_panama",)
            c_dis = completions(dis, lanes, w0, w1, avoid)
            c_twin = completions(twin, lanes, w0, w1, avoid)
            raw["panama_completions_disrupted"] = c_dis
            raw["panama_completions_twin"] = c_twin
            raw["panama_drop_pct_sim"] = (
                100.0 * (c_dis - c_twin) / c_twin if c_twin else None)
            # Ungraded print: clearance after the window (the real
            # canal stepped UP to a still-restricted level rather
            # than reopening — no comparator was proposed).
            w = disruptions[0]
            wend = parse_ts(w["started_at"]) + float(w["duration_d"]) * DAY
            raw["window_end_epoch"] = wend
            clr = None
            for k in range(ticks - 2):
                tick_t = start + k * DAY
                if tick_t <= wend:
                    continue
                if all(raw["queue_disrupted"][k + j]
                       <= raw["queue_twin"][k + j] for j in range(3)):
                    clr = (tick_t - wend) / DAY
                    break
            raw["clearance_days_sim"] = clr

    if name == "la_lb":
        def berth_by_day(sim: dict, nd: str = "sea_losangeles") -> list[int]:
            out = [0] * ticks
            for _, s in segs(sim, "berth"):
                if s["node"] == nd and s["t"] >= start:
                    k = day_index(s["t"], start)
                    if 0 <= k < ticks:
                        out[k] += 1
            return out

        raw["berth_la_disrupted"] = berth_by_day(dis)
        raw["berth_la_twin"] = berth_by_day(twin)
        # Ungraded prints (preregistration_v2.md §5.2): the unshocked
        # complex node's queue peaks and berth-call change, and the
        # LB-3 clearance value (no observed comparator exists).
        raw["queue_longbeach_disrupted"] = \
            dis["node_series"]["sea_longbeach"]["queue"]
        raw["queue_longbeach_twin"] = \
            twin["node_series"]["sea_longbeach"]["queue"]
        raw["queue_peak_longbeach_disrupted"] = max(
            raw["queue_longbeach_disrupted"])
        raw["queue_peak_longbeach_twin"] = max(raw["queue_longbeach_twin"])
        lb_d = berth_by_day(dis, "sea_longbeach")
        lb_t = berth_by_day(twin, "sea_longbeach")
        raw["berth_lb_disrupted"] = lb_d
        raw["berth_lb_twin"] = lb_t
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
        # Registered peak window [2021-10-01, 2022-01-31), truncated
        # at the run end (preregistration_v2.md §5.2, LB-2) — for
        # this run that is the whole run; LB-1's twin berth-call rate
        # is the same window's mean ("same run").
        ws = day_index(parse_ts("2021-10-01T00:00:00Z"), start)
        we = min(day_index(parse_ts("2022-01-31T00:00:00Z"), start), ticks)
        raw["peak_window_day_span"] = [ws, we]

        def wmean(arr: list[int]) -> float:
            seg = arr[ws:we]
            return sum(seg) / len(seg)
        raw["berth_la_mean_disrupted"] = wmean(raw["berth_la_disrupted"])
        raw["berth_la_mean_twin"] = wmean(raw["berth_la_twin"])
        raw["berth_change_pct_sim"] = 100.0 * (
            raw["berth_la_mean_disrupted"] - raw["berth_la_mean_twin"]
        ) / raw["berth_la_mean_twin"]
        raw["excess_peak_queue_days_of_flow"] = (
            raw["excess_peak_queue"] / raw["berth_la_mean_twin"])
    return raw


def main() -> int:
    cfgs = json.loads((HERE / "run_configs_v2.json").read_text())["events"]
    graph = json.loads((REPO / "data/graph/graph.json").read_text())
    RESULTS.mkdir(exist_ok=True)
    wanted = sys.argv[1:] or list(cfgs)
    for name in wanted:
        raw = run_event(name, cfgs[name], graph)
        (RESULTS / f"{name}_raw.json").write_text(
            json.dumps(raw, indent=1, sort_keys=True) + "\n")
        print(f"{name}: done -> results_v2/{name}_raw.json", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
