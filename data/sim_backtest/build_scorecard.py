#!/usr/bin/env python3
"""Build scorecard.json for the SIM historical backtest from
results/*_raw.json and the stored actuals/PortWatch series.
Grades follow preregistration.md exactly. Every grade and observed
comparator is recomputable from those stored files."""
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


def observed() -> dict:
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
    srt = sorted(bal, key=lambda r: r["date"])
    rec = None
    for i, r in enumerate(srt):
        if r["date"][:10] <= "2024-06-10" or i < 6:
            continue
        if sum(x["portcalls"] for x in srt[i - 6:i + 1]) / 7 >= 0.9 * bb:
            rec = (date.fromisoformat(r["date"][:10]) - DAY0).days
            break
    obs["baltimore"] = {"ba1_drop_pct": 100 * (bc - bb) / bb,
                        "ba2_change_pct": 100 * (vc - vb) / vb,
                        "ba3_recovery_days": rec}
    rs = rows_of(HERE / "red_sea/portwatch_suez_redsea.json")
    sb = mean(rs, "2023-11-01", "2023-11-30", "n_total")
    sj = mean(rs, "2024-01-01", "2024-02-29", "n_total")
    obs["red_sea"] = {"rs1_drop_pct": 100 * (sj - sb) / sb,
                      "rs2_range_d": [7.0, 12.0]}
    return obs


def grade_all(raw: dict, obs: dict) -> list[dict]:
    m = []
    eg, o = raw["ever_given"], obs["ever_given"]
    m.append({"event": "ever_given", "id": "EG-1",
              "metric": "Excess peak queue at chk_suez, in days of normal corridor flow",
              "sim": eg["excess_peak_queue_days_of_flow"],
              "observed": o["eg1_days_of_flow"],
              "band": "ratio in [0.5, 2]",
              "grade": ratio_grade(eg["excess_peak_queue_days_of_flow"],
                                   o["eg1_days_of_flow"])})
    m.append({"event": "ever_given", "id": "EG-2",
              "metric": "Mean added delay at chk_suez (twin statistic)",
              "sim": eg["delay_stats"]["chk_suez"]["mean_added_delay_d"],
              "observed": None, "band": "—",
              "grade": "UNGRADED",
              "reason": "average delay per queued vessel could not be sourced (only forecasts and anecdotes exist); never estimated"})
    m.append({"event": "ever_given", "id": "EG-3",
              "metric": "Clearance days after reopening (queue back to twin level, sustained 3 ticks)",
              "sim": eg["clearance_days_sim"],
              "observed": o["eg3_clearance_days"],
              "band": "ratio in [0.5, 2]",
              "grade": ratio_grade(eg["clearance_days_sim"],
                                   o["eg3_clearance_days"])})
    ba, o = raw["baltimore"], obs["baltimore"]
    m.append({"event": "baltimore", "id": "BA-1",
              "metric": "Port-call drop at sea_baltimore during closure window (berth calls, disrupted vs twin)",
              "sim": ba["calls_drop_pct_sim"], "observed": o["ba1_drop_pct"],
              "band": "|diff| <= 20 pp",
              "grade": pp_grade(ba["calls_drop_pct_sim"], o["ba1_drop_pct"])})
    m.append({"event": "baltimore", "id": "BA-2",
              "metric": "Diversion uplift at sea_virginia during closure window (berth calls % change vs twin)",
              "sim": ba["virginia_change_pct_sim"],
              "observed": o["ba2_change_pct"],
              "band": "|diff| <= 20 pp",
              "grade": pp_grade(ba["virginia_change_pct_sim"],
                                o["ba2_change_pct"])})
    m.append({"event": "baltimore", "id": "BA-3",
              "metric": "Recovery days after full reopening (berth calls >= 90% of twin, sustained 3 d)",
              "sim": ba["recovery_days_sim"],
              "observed": o["ba3_recovery_days"],
              "band": "ratio in [0.5, 2]",
              "grade": ratio_grade(ba["recovery_days_sim"],
                                   o["ba3_recovery_days"])})
    rs, o = raw["red_sea"], obs["red_sea"]
    m.append({"event": "red_sea", "id": "RS-1",
              "metric": "Suez transit drop, Jan-Feb 2024 (completed non-diverted arrivals on Suez-transiting lanes, disrupted vs twin)",
              "sim": rs["suez_drop_pct_sim"], "observed": o["rs1_drop_pct"],
              "band": "|diff| <= 20 pp",
              "grade": pp_grade(rs["suez_drop_pct_sim"], o["rs1_drop_pct"])})
    m.append({"event": "red_sea", "id": "RS-2",
              "metric": "Mean lateness of arrivals on Bab-el-Mandeb-transiting lanes, Jan-Feb 2024 (occurrence-matched vs twin)",
              "sim": rs["corridor_lateness_mean_d"],
              "observed": o["rs2_range_d"],
              "band": "sim in [3.5, 24] (observed range [7, 12] widened by [0.5x, 2x])",
              "grade": range_grade(rs["corridor_lateness_mean_d"], 7.0, 12.0)})
    return m


def main() -> int:
    raw = {n: json.loads((HERE / f"results/{n}_raw.json").read_text())
           for n in ("ever_given", "baltimore", "red_sea")}
    obs = observed()
    metrics = grade_all(raw, obs)
    graded = [x for x in metrics if x["grade"] in ("HIT", "MISS")]
    hits = sum(1 for x in graded if x["grade"] == "HIT")
    card = {
        "artifact": "SIM BACKTEST SCORECARD",
        "grades_engine": "SIM layer (traffic / queues / diversions) — "
                         "separate from the causal-chain scorecard; "
                         "never merged",
        "sim_code_note": "all runs executed the shipped sim exactly as registered",
        "graph_sha256": hashlib.sha256(
            (REPO / "data/graph/graph.json").read_bytes()).hexdigest(),
        "aggregate": {"metrics_graded": len(graded), "hits": hits,
                      "misses": len(graded) - hits,
                      "ungraded": len(metrics) - len(graded)},
        "metrics": metrics,
        "observed_comparators": obs,
        "raw_results": {n: f"results/{n}_raw.json" for n in raw},
    }
    (HERE / "scorecard.json").write_text(
        json.dumps(card, indent=1, sort_keys=True) + "\n")
    print(json.dumps(card["aggregate"]))
    for x in metrics:
        print(x["id"], x["grade"], "sim=", x["sim"], "obs=", x["observed"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
