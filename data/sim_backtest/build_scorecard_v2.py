#!/usr/bin/env python3
"""Build scorecard_v2.json + scorecard_v2.md for the extended SIM backtest
from results_v2/*_raw.json and the stored
actuals/PortWatch series. Grades follow preregistration_v2.md exactly;
the original events keep their baseline metrics, definitions and bands, and
their baseline sim values/grades are read from the published scorecard.json
(never recomputed). Every extended-run grade is recomputable from the
stored raws and series."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DAY0 = date(2024, 6, 10)


def rows_of(p: Path):
    d = json.loads(p.read_text())
    return d["rows"] if isinstance(d, dict) and "rows" in d else d


def mean(rows, a, b, field):
    v = [r[field] for r in rows if a <= r["date"][:10] <= b]
    return sum(v) / len(v)


def ratio_grade(sim: float, obs: float) -> str:
    return "HIT" if 0.5 <= sim / obs <= 2.0 else "MISS"


def pp_grade(sim: float, obs: float) -> str:
    return "HIT" if abs(sim - obs) <= 20.0 else "MISS"


def range_grade(sim: float, a: float, b: float) -> str:
    return "HIT" if a / 2 <= sim <= 2 * b else "MISS"


def figure(actuals: dict, name: str):
    for f in actuals["figures"]:
        if f["name"] == name:
            return f.get("value")
    raise KeyError(name)


def observed() -> dict:
    """Observed comparators. The original events' entries are computed
    EXACTLY as build_scorecard.py computes them (same series, same
    calendar windows), so they equal the baseline published values."""
    eg = rows_of(HERE / "ever_given/portwatch_suez_2021.json")
    base = mean(eg, "2021-03-01", "2021-03-22", "n_total")
    obs = {"ever_given": {
        "peak_queue": 422, "baseline_transits_per_day": base,
        "eg1_days_of_flow": 422 / base, "eg3_clearance_days": 5.0}}
    bal = rows_of(HERE / "baltimore/portwatch_baltimore_2024.json")
    vir = rows_of(HERE / "baltimore/portwatch_virginia_2024.json")
    bb = mean(bal, "2024-02-15", "2024-03-25", "portcalls")
    bc = mean(bal, "2024-03-27", "2024-06-09", "portcalls")
    vb = mean(vir, "2024-02-15", "2024-03-25", "portcalls")
    vc = mean(vir, "2024-03-27", "2024-06-09", "portcalls")
    ib = mean(vir, "2024-02-15", "2024-03-25", "import")
    ic = mean(vir, "2024-03-27", "2024-06-09", "import")
    srt = sorted(bal, key=lambda r: r["date"])
    rec = None
    for i, r in enumerate(srt):
        if r["date"][:10] <= "2024-06-10" or i < 6:
            continue
        if sum(x["portcalls"] for x in srt[i - 6:i + 1]) / 7 >= 0.9 * bb:
            rec = (date.fromisoformat(r["date"][:10]) - DAY0).days
            break
    ba2_calls = 100 * (vc - vb) / vb
    ba2_imports = 100 * (ic - ib) / ib
    obs["baltimore"] = {
        "ba1_drop_pct": 100 * (bc - bb) / bb,
        "ba2_change_pct": ba2_calls,
        "ba2_imports_change_pct": ba2_imports,
        "ba2v_combined_pct": 100 * ((1 + ba2_calls / 100)
                                    * (1 + ba2_imports / 100) - 1),
        "ba3_recovery_days": rec}
    rs = rows_of(HERE / "red_sea/portwatch_suez_redsea.json")
    sb = mean(rs, "2023-11-01", "2023-11-30", "n_total")
    sj = mean(rs, "2024-01-01", "2024-02-29", "n_total")
    bem = rows_of(HERE / "red_sea/portwatch_babelmandeb_redsea.json")
    bbem = mean(bem, "2023-11-01", "2023-11-30", "n_total")
    bjem = mean(bem, "2024-01-01", "2024-02-29", "n_total")
    obs["red_sea"] = {"rs1_drop_pct": 100 * (sj - sb) / sb,
                      "rs2_range_d": [7.0, 12.0],
                      "rs3_bem_drop_pct": 100 * (bjem - bbem) / bbem}
    pan = rows_of(HERE / "panama/portwatch_panama_2023-2024.json")
    pbase = mean(pan, "2023-01-01", "2023-07-29", "n_total")
    pwin = mean(pan, "2023-12-01", "2024-01-15", "n_total")
    pan_act = json.loads((HERE / "panama/actuals.json").read_text())
    excess_q = (figure(pan_act, "queue_high_2023-08-09")
                - figure(pan_act, "queue_normal_period_average"))
    obs["panama"] = {
        "pa1_drop_pct": 100 * (pwin - pbase) / pbase,
        "pa1_baseline_per_day": pbase, "pa1_window_per_day": pwin,
        "pa2_excess_vessels": excess_q,
        "pa2_days_of_flow": excess_q / pbase,
        "pa3_range_d": [5.0, 15.0]}
    la = rows_of(HERE / "la_lb/portwatch_lalb_2021-2022.json")
    lbase = mean(la, "2021-01-01", "2021-03-31", "portcalls")
    lpeak = mean(la, "2021-10-01", "2022-01-31", "portcalls")
    lcont = mean(la, "2021-01-01", "2021-03-31", "portcalls_container")
    la_act = json.loads((HERE / "la_lb/actuals.json").read_text())
    peak109 = figure(la_act, "peak_backup_2022-01-09")
    obs["la_lb"] = {
        "lb1_days_of_flow": peak109 / lcont,
        "lb1_peak_vessels": peak109,
        "lb1_container_rate": lcont,
        "lb2_change_pct": 100 * (lpeak - lbase) / lbase,
        "lb2_baseline_per_day": lbase, "lb2_peak_per_day": lpeak}
    return obs


def grade_all(raw: dict, obs: dict, v1: dict) -> list[dict]:
    m = []

    def add(event, mid, metric, sim, observed_v, band, grade,
            v2_addition=False, sim_detail="", obs_detail=""):
        e = {"event": event, "id": mid, "metric": metric, "sim": sim,
             "observed": observed_v, "band": band, "grade": grade,
             "v2_addition": v2_addition, "sim_detail": sim_detail,
             "obs_detail": obs_detail}
        if mid in v1:
            e["v1_sim"] = v1[mid]["sim"]
            e["v1_grade"] = v1[mid]["grade"]
        m.append(e)
        return e

    eg, o = raw["ever_given"], obs["ever_given"]
    add("ever_given", "EG-1",
        "Excess peak queue at chk_suez, in days of normal corridor flow",
        eg["excess_peak_queue_days_of_flow"], o["eg1_days_of_flow"],
        "ratio in [0.5, 2]",
        ratio_grade(eg["excess_peak_queue_days_of_flow"],
                    o["eg1_days_of_flow"]),
        sim_detail=(f"excess peak {eg['excess_peak_queue']} vessels ÷ "
                    f"{eg['twin_corridor_departures_per_day']:.1f} corridor "
                    "departures/day"),
        obs_detail=(f"422 vessels (SCA via Reuters) ÷ PortWatch baseline "
                    f"{o['baseline_transits_per_day']:.2f} transits/day"))
    add("ever_given", "EG-2",
        "Mean added delay at chk_suez (twin statistic)",
        eg["delay_stats"]["chk_suez"]["mean_added_delay_d"], None, "—",
        "UNGRADED",
        sim_detail=f"hold arrivals at chk_suez: "
                   f"{eg['delay_stats']['chk_suez']['hold_arrivals']}",
        obs_detail="average delay per queued vessel could not be sourced "
                   "(only forecasts and anecdotes exist); never estimated")
    add("ever_given", "EG-3",
        "Clearance days after reopening (queue back to twin level, "
        "sustained 3 ticks)",
        eg["clearance_days_sim"], o["eg3_clearance_days"],
        "ratio in [0.5, 2]",
        ratio_grade(eg["clearance_days_sim"], o["eg3_clearance_days"])
        if eg["clearance_days_sim"] is not None else "MISS",
        sim_detail=("clearance NOT reached within the run (disrupted "
                    "queue still above twin at the horizon) — the held "
                    "queue's tail outlasts the 30-tick run"
                    if eg["clearance_days_sim"] is None else ""),
        obs_detail="backlog fully cleared 2021-04-03 (Reuters/SCA); "
                   "reopening 2021-03-29")
    ba, o = raw["baltimore"], obs["baltimore"]
    add("baltimore", "BA-1",
        "Port-call drop at sea_baltimore during closure window (berth "
        "calls, disrupted vs twin)",
        ba["calls_drop_pct_sim"], o["ba1_drop_pct"], "|diff| <= 20 pp",
        pp_grade(ba["calls_drop_pct_sim"], o["ba1_drop_pct"]),
        sim_detail=(f"span days {ba['closure_day_span']}, berth means "
                    f"{ba['berth_baltimore_mean_disrupted']:.2f} vs twin "
                    f"{ba['berth_baltimore_mean_twin']:.2f}/day"))
    add("baltimore", "BA-2",
        "Diversion uplift at sea_virginia during closure window (berth "
        "calls % change vs twin) — baseline calls-only yardstick, kept for "
        "comparability",
        ba["virginia_change_pct_sim"], o["ba2_change_pct"],
        "|diff| <= 20 pp",
        pp_grade(ba["virginia_change_pct_sim"], o["ba2_change_pct"]))
    acc = ba["reassignment_accounting"].get("sea_baltimore", {})
    va_acc = acc.get("substitutes", {}).get("sea_virginia", {})
    va_vol_day = va_acc.get("volume", 0.0) / ba["span_days"]
    va_tonnage_pct = 100.0 * va_vol_day / ba["virginia_twin_tonnage_per_day"]
    ba2v_sim = 100.0 * ((1 + ba["virginia_change_pct_sim"] / 100)
                        * (1 + va_tonnage_pct / 100) - 1)
    add("baltimore", "BA-2v",
        "Virginia diversion effect, calls AND tonnage combined "
        "(extended-run addition; call-reassignment yardstick)",
        ba2v_sim, o["ba2v_combined_pct"], "|diff| <= 20 pp",
        pp_grade(ba2v_sim, o["ba2v_combined_pct"]), v2_addition=True,
        sim_detail=(f"calls {ba['virginia_change_pct_sim']:+.2f}% × "
                    f"reassigned tonnage {va_tonnage_pct:+.2f}% "
                    f"({va_acc.get('calls', 0)} reassigned calls, "
                    f"{va_acc.get('volume', 0.0):,.0f} capacity-units over "
                    f"{ba['span_days']} d ÷ twin Virginia tonnage/day "
                    f"{ba['virginia_twin_tonnage_per_day']:,.0f})"),
        obs_detail=(f"calls {o['ba2_change_pct']:+.2f}% × AIS-estimated "
                    f"import tonnage {o['ba2_imports_change_pct']:+.2f}% "
                    "(PortWatch port826, same windows)"))
    add("baltimore", "BA-3",
        "Recovery days after full reopening (berth calls >= 90% of "
        "twin, sustained 3 d)",
        ba["recovery_days_sim"], o["ba3_recovery_days"],
        "ratio in [0.5, 2]",
        ratio_grade(ba["recovery_days_sim"], o["ba3_recovery_days"])
        if ba["recovery_days_sim"] is not None else "MISS",
        sim_detail="recovery measured from the last step's end "
                   "(2024-06-10, full channel restoration)",
        obs_detail="first 7-day mean >= 90% of baseline on 2024-06-23")
    rs, o = raw["red_sea"], obs["red_sea"]
    add("red_sea", "RS-1",
        "Suez transit drop, Jan-Feb 2024 (completed non-diverted "
        "arrivals on Suez-transiting lanes, disrupted vs twin)",
        rs["suez_drop_pct_sim"], o["rs1_drop_pct"], "|diff| <= 20 pp",
        pp_grade(rs["suez_drop_pct_sim"], o["rs1_drop_pct"]),
        sim_detail=(f"completions {rs['suez_completions_disrupted']} vs "
                    f"twin {rs['suez_completions_twin']}"))
    add("red_sea", "RS-2",
        "Mean lateness of arrivals on Bab-el-Mandeb-transiting lanes, "
        "Jan-Feb 2024 (occurrence-matched vs twin)",
        rs["corridor_lateness_mean_d"], o["rs2_range_d"],
        "sim in [3.5, 24] (observed range [7, 12] widened by [0.5x, 2x])",
        range_grade(rs["corridor_lateness_mean_d"], 7.0, 12.0)
        if rs["corridor_lateness_mean_d"] is not None else "MISS",
        sim_detail=(f"over {rs['corridor_lateness_n']} matched pairs"
                    + (" — ZERO pairs: under the open-ended expectation "
                       "the exposed local-hop traffic has no certified "
                       "bypass and is HELD at the strait past the "
                       "window, so no in-window arrival exists to be "
                       "late; the statistic is undefined and prints "
                       "MISS (the conservative label), not a hit"
                       if rs["corridor_lateness_n"] == 0 else "")))
    if rs["lanes_transiting_both"] == 0:
        add("red_sea", "RS-3",
            "Exposed-subpopulation Suez flow change (extended-run addition): "
            "RS-1 extraction restricted to lanes transiting BOTH "
            "chk_babelmandeb AND chk_suez",
            None, o["rs3_bem_drop_pct"], "|diff| <= 20 pp",
            "UNDEFINED — NO EXPOSED LANES IN CENSUS", v2_addition=True,
            sim_detail=("the modelled census contains 0 lanes transiting "
                        "both straits (census audit, "
                        "census_audit.json)"),
            obs_detail="PortWatch Bab el-Mandeb n_total, Jan-Feb 2024 vs "
                       "Nov 2023")
    else:
        add("red_sea", "RS-3",
            "Exposed-subpopulation Suez flow change (extended-run addition)",
            rs["both_drop_pct_sim"], o["rs3_bem_drop_pct"],
            "|diff| <= 20 pp",
            pp_grade(rs["both_drop_pct_sim"], o["rs3_bem_drop_pct"]),
            v2_addition=True)
    pa, o = raw["panama"], obs["panama"]
    add("panama", "PA-1",
        "Transit drop %, tightest phase (completed non-diverted "
        "arrivals on chk_panama-transiting lanes, 2023-12-01 → "
        "2024-01-16, disrupted vs twin)",
        pa["panama_drop_pct_sim"], o["pa1_drop_pct"], "|diff| <= 20 pp",
        pp_grade(pa["panama_drop_pct_sim"], o["pa1_drop_pct"]),
        sim_detail=(f"completions {pa['panama_completions_disrupted']} "
                    f"vs twin {pa['panama_completions_twin']}"),
        obs_detail=(f"PortWatch Panama n_total {o['pa1_window_per_day']:.2f}"
                    f"/day (2023-12-01 → 2024-01-15) vs baseline "
                    f"{o['pa1_baseline_per_day']:.2f}/day "
                    "(2023-01-01 → 2023-07-29)"))
    paa = raw["panama_august"]
    add("panama", "PA-2",
        "Excess peak queue, days of flow (August phase run)",
        paa["excess_peak_queue_days_of_flow"], o["pa2_days_of_flow"],
        "ratio in [0.5, 2]",
        ratio_grade(paa["excess_peak_queue_days_of_flow"],
                    o["pa2_days_of_flow"]),
        sim_detail=(f"excess peak {paa['excess_peak_queue']} vessels ÷ "
                    f"{paa['twin_corridor_departures_per_day']:.2f} twin "
                    "corridor departures/day (August run)"),
        obs_detail=(f"excess queue 163 − 90 = {o['pa2_excess_vessels']} "
                    f"vessels (FreightWaves from ACP data) ÷ 32.11 "
                    "baseline transits/day"))
    add("panama", "PA-3",
        "Mean added delay at chk_panama (twin statistic, primary run)",
        pa["delay_stats"]["chk_panama"]["mean_added_delay_d"],
        o["pa3_range_d"], "sim in [2.5, 30]",
        range_grade(pa["delay_stats"]["chk_panama"]["mean_added_delay_d"],
                    5.0, 15.0),
        sim_detail=(f"hold arrivals at chk_panama: "
                    f"{pa['delay_stats']['chk_panama']['hold_arrivals']} "
                    "(counted separately, never averaged in)"),
        obs_detail="published waiting-time forecast range [5, 15] d "
                   "(Waterfront Maritime Services, 2023-11-07) — a dated "
                   "forecast range, per the registration's stated basis")
    lb, o = raw["la_lb"], obs["la_lb"]
    add("la_lb", "LB-1",
        "Excess peak queue, days of flow",
        lb["excess_peak_queue_days_of_flow"], o["lb1_days_of_flow"],
        "ratio in [0.5, 2]",
        ratio_grade(lb["excess_peak_queue_days_of_flow"],
                    o["lb1_days_of_flow"]),
        sim_detail=(f"excess peak {lb['excess_peak_queue']} vessels ÷ "
                    f"twin berth-call rate "
                    f"{lb['berth_la_mean_twin']:.2f}/day at sea_losangeles"),
        obs_detail=(f"MXSOC peak backup {o['lb1_peak_vessels']} container "
                    f"ships (2022-01-09) ÷ PortWatch baseline container "
                    f"call rate {o['lb1_container_rate']:.3f}/day"))
    add("la_lb", "LB-2",
        "Port-call change, peak window (berth calls at sea_losangeles, "
        "disrupted vs twin)",
        lb["berth_change_pct_sim"], o["lb2_change_pct"],
        "|diff| <= 20 pp",
        pp_grade(lb["berth_change_pct_sim"], o["lb2_change_pct"]),
        sim_detail=(f"berth means {lb['berth_la_mean_disrupted']:.2f} vs "
                    f"twin {lb['berth_la_mean_twin']:.2f}/day over the "
                    "run window"),
        obs_detail=(f"PortWatch port664 total calls "
                    f"{o['lb2_peak_per_day']:.3f}/day (2021-10-01 → "
                    f"2022-01-31) vs baseline {o['lb2_baseline_per_day']:.3f}"
                    "/day (Q1 2021) — a RISE, as registered"))
    add("la_lb", "LB-3",
        "Clearance days after the window",
        lb["clearance_days_sim"], None, "—", "UNGRADED",
        obs_detail="no capacity-restoration date exists (demand-side "
                   "emptying over 2022); observed milestones 232 d / "
                   "283 d / 317 d documented in the actuals file — no "
                   "like-for-like comparator, per the registration")
    return m


def main() -> int:
    raw = {n: json.loads((HERE / f"results_v2/{n}_raw.json").read_text())
           for n in ("ever_given", "baltimore", "red_sea", "panama",
                     "panama_august", "la_lb")}
    v1card = json.loads((HERE / "scorecard.json").read_text())
    v1 = {x["id"]: x for x in v1card["metrics"]}
    obs = observed()
    metrics = grade_all(raw, obs, v1)
    orig8 = [x for x in metrics
             if x["id"] in ("EG-1", "EG-2", "EG-3", "BA-1", "BA-2",
                            "BA-3", "RS-1", "RS-2")]
    held = [x for x in metrics
            if x["id"] in ("PA-1", "PA-2", "PA-3", "LB-1", "LB-2", "LB-3")]

    def agg(ms):
        g = [x for x in ms if x["grade"] in ("HIT", "MISS")]
        return {"metrics_graded": len(g),
                "hits": sum(1 for x in g if x["grade"] == "HIT"),
                "misses": sum(1 for x in g if x["grade"] == "MISS"),
                "ungraded": len(ms) - len(g)}
    card = {
        "artifact": "SIM BACKTEST SCORECARD V2",
        "grades_engine": "SIM layer (traffic / queues / diversions) — "
                         "separate from the causal-chain scorecard; "
                         "never merged",
        "v1_scorecard": "scorecard.json (baseline grades never recomputed)",
        "graph_sha256": hashlib.sha256(
            (REPO / "data/graph/graph.json").read_bytes()).hexdigest(),
        "aggregate_original8_v2": agg(orig8),
        "aggregate_original8_v1": v1card["aggregate"],
        "aggregate_heldout": agg(held),
        "metrics": metrics,
        "observed_comparators": obs,
        "raw_results": {n: f"results_v2/{n}_raw.json" for n in raw},
        "census_audit_verdict": "census weights FAITHFUL; through share "
                                "0.0 by construction of the leg census + "
                                "rendered-path transit identification; "
                                "no census data changed "
                                "(census_audit.json)",
    }
    (HERE / "scorecard_v2.json").write_text(
        json.dumps(card, indent=1, sort_keys=True) + "\n")
    (HERE / "scorecard_v2.md").write_text(render_md(card, raw))
    print(json.dumps({"original8_v2": card["aggregate_original8_v2"],
                      "heldout": card["aggregate_heldout"]}))
    for x in metrics:
        print(x["id"], x["grade"], "sim=", x["sim"], "obs=", x["observed"],
              "| v1:", x.get("v1_grade"), x.get("v1_sim"))
    return 0


def _fmt(v, unit=""):
    if v is None:
        return "—"
    if isinstance(v, list):
        return "[" + ", ".join(f"{x:g}" for x in v) + "]" + unit
    if isinstance(v, float):
        return f"{v:+.2f}{unit}" if abs(v) >= 100 else f"{v:.2f}{unit}"
    return f"{v}{unit}"


def render_md(card: dict, raw: dict) -> str:
    ms = {x["id"]: x for x in card["metrics"]}
    a8, a8v1, ah = (card["aggregate_original8_v2"],
                    card["aggregate_original8_v1"],
                    card["aggregate_heldout"])

    def row(mid: str) -> str:
        x = ms[mid]
        sim = f"**{_fmt(x['sim'])}**"
        if x.get("sim_detail"):
            sim += f" ({x['sim_detail']})"
        if "v1_sim" in x:
            sim = f"{_fmt(x['v1_sim'])} → {sim}"
        obs = f"**{_fmt(x['observed'])}**"
        if x.get("obs_detail"):
            obs += f" ({x['obs_detail']})"
        grade = f"**{x['grade']}**"
        if "v1_grade" in x:
            grade = f"{x['v1_grade']} → {grade}"
        return (f"| {mid} | {x['metric']} | {sim} | {obs} | "
                f"{x['band']} | {grade} |")
    eg, ba, rs = raw["ever_given"], raw["baltimore"], raw["red_sea"]
    pa, paa, lb = raw["panama"], raw["panama_august"], raw["la_lb"]
    acc = ba["reassignment_accounting"].get("sea_baltimore", {})
    subs = acc.get("substitutes", {})
    sub_txt = "; ".join(
        f"{k}: {v['departures']} departures, {v['calls']} calls, "
        f"{v['volume']:,.0f} capacity-units"
        for k, v in sorted(subs.items()))
    lines = [
        "# SIM BACKTEST SCORECARD V2",
        "",
        "Grades the **SIM layer** (traffic, queues, diversions) against "
        "documented historical blockages, in the extended run "
        "(wait-vs-divert, stepped shocks, call reassignment and "
        "exposed-subpopulation grading, defined in `preregistration_v2.md`). "
        "Separate from the causal-chain scorecard; the two are never merged. "
        "**The baseline scorecard (`scorecard.md` / `scorecard.json`) is "
        "unchanged and its grades are never recomputed** — baseline sim "
        "values below are quoted from it.",
        "",
        f"Pre-registration: `preregistration_v2.md`, recorded "
        f"**before any extended run and before any behaviour "
        f"change**, together with the two held-out event records and "
        f"`run_configs_v2.json`. Held-out events (Panama, LA/LB) were "
        f"never used to design or tune any mechanism; each registered "
        f"run executed **once**, on the final code. Graph SHA-256: "
        f"`{card['graph_sha256']}`. Bands as registered (ratio ∈ "
        f"[0.5, 2]; range [a, b] → sim ∈ [a/2, 2b]; share/drop "
        f"|diff| ≤ 20 pp; unsourced actual → UNGRADED).",
        "",
        "## Aggregates",
        "",
        "| Set | Graded | Hits | Misses | Ungraded / undefined |",
        "|---|---|---|---|---|",
        f"| Original 8 metrics — baseline run | {a8v1['metrics_graded']} | "
        f"{a8v1['hits']} | {a8v1['misses']} | {a8v1['ungraded']} |",
        f"| Original 8 metrics — extended run | {a8['metrics_graded']} | "
        f"{a8['hits']} | {a8['misses']} | {a8['ungraded']} |",
        f"| Held-out (Panama + LA/LB) — extended run | {ah['metrics_graded']} | "
        f"{ah['hits']} | {ah['misses']} | {ah['ungraded']} |",
        "",
        "Extended-run additions graded outside both aggregates: **BA-2v** "
        "(calls+tonnage yardstick for call reassignment) and **RS-3** "
        "(exposed-subpopulation metric — resolved UNDEFINED, see the "
        "census verdict below).",
        "",
        "## Ever Given / Suez Canal — baseline config unchanged",
        "",
        "| ID | Metric | Sim (baseline → extended) | Observed | Band | Grade "
        "(baseline → extended) |",
        "|---|---|---|---|---|---|",
        row("EG-1"), row("EG-2"), row("EG-3"),
        "",
        f"Behaviour: divert segments {len(eg['divert_segments'])}, "
        f"hold arrivals at chk_suez "
        f"{eg['delay_stats']['chk_suez']['hold_arrivals']} (baseline run: 52 "
        f"diverts, 9 holds — wait-vs-divert now holds vessels "
        f"whose bypass costs more time than the expected wait).",
        "",
        "## Baltimore / Key Bridge — stepped shock + call reassignment",
        "",
        "| ID | Metric | Sim (baseline → extended) | Observed | Band | Grade "
        "(baseline → extended) |",
        "|---|---|---|---|---|---|",
        row("BA-1"), row("BA-2"), row("BA-2v"), row("BA-3"),
        "",
        f"Reassignment accounting: {acc.get('lanes_reassigned', 0)}"
        f" of {acc.get('lanes_total', 0)} Baltimore-serving lanes "
        f"re-pointed during the span ({sub_txt}). **Zero reassigned "
        f"departures fired**: all 14 Baltimore-bound departures inside "
        f"the span were Virginia-origin legs of the Baltimore↔Virginia "
        f"shuttle — that lane is assigned to Virginia by the share "
        f"order, and a departure already AT its substitute cannot "
        f"re-point (the declared rule); the port's other "
        f"lanes carried no in-span Baltimore-bound departures (the "
        f"census gives Baltimore 14 inbound departures in 76 days). "
        f"BA-2v's tonnage component is therefore 0 and BA-2v grades "
        f"the calls-only reality. Printed as found; no re-run.",
        "",
        "## Red Sea window — open-ended expectation",
        "",
        "| ID | Metric | Sim (baseline → extended) | Observed | Band | Grade "
        "(baseline → extended) |",
        "|---|---|---|---|---|---|",
        row("RS-1"), row("RS-2"), row("RS-3"),
        "",
        f"Ungraded decomposition: completions change on "
        f"Bab-el-Mandeb-only lanes {rs['decomp_bem_only_drop_pct']}%, "
        f"on Suez-only lanes {rs['decomp_suez_only_drop_pct']}%.",
        "",
        "## Held-out: Panama Canal drought (2023–24)",
        "",
        "| ID | Metric | Sim | Observed | Band | Grade |",
        "|---|---|---|---|---|---|",
        row("PA-1"), row("PA-2"), row("PA-3"),
        "",
        f"Ungraded (primary run): queue peaks disrupted/twin "
        f"{pa['queue_peak_disrupted']}/{pa['queue_peak_twin']}; excess "
        f"peak in days of flow {pa['excess_peak_queue_days_of_flow']:.2f}"
        f" d (the PA-2 construction on the December phase — printed "
        f"ungraded per the registration); divert segments "
        f"{len(pa['divert_segments'])}; hold arrivals at chk_panama "
        f"{pa['hold_arrivals_blocked_node']}; clearance after "
        f"2024-01-16: {pa['clearance_days_sim']} d (no comparator — "
        f"the real canal stepped UP to a still-restricted level).",
        "",
        "## Held-out: LA/LB congestion (2021)",
        "",
        "| ID | Metric | Sim | Observed | Band | Grade |",
        "|---|---|---|---|---|---|",
        row("LB-1"), row("LB-2"), row("LB-3"),
        "",
        f"Ungraded: queue peaks at sea_losangeles disrupted/twin "
        f"{lb['queue_peak_disrupted']}/{lb['queue_peak_twin']}; at the "
        f"unshocked sea_longbeach "
        f"{lb['queue_peak_longbeach_disrupted']}/"
        f"{lb['queue_peak_longbeach_twin']}; mean added delay at "
        f"sea_losangeles "
        f"{lb['delay_stats']['sea_losangeles']['mean_added_delay_d']:.2f}"
        f" d. The registered 0.20 loss is a window-compressed "
        f"equivalent: the sourced sustained deficit is ≈0.08, the "
        f"balance being the pre-October queue build compressed into "
        f"the window — not a claim that real capacity "
        f"fell 20%.",
        "",
        "## Census-audit verdict",
        "",
        f"{card['census_audit_verdict']} — full audit: "
        f"`census_audit.json`. Consequence: RS-3 resolves UNDEFINED as "
        f"registered; the Red Sea miss (RS-1) is a census-structure "
        f"result, not a weights error, and no in-boundary fix exists.",
        "",
        "## Reproducibility",
        "",
        "Every extended-run grade and observed comparator is recomputed by "
        "`build_scorecard_v2.py` from the stored raws and series; the shared "
        "comparators equal the baseline run's published values.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
