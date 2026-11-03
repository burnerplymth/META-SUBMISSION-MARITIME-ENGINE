"""CSV / JSON export of a finished job and of a compare (pure, deterministic).

Every CSV row carries the mandatory label in its last column, so a number
never leaves the server without it. Cells hold only numbers, fixed metric
names, job-derived hashes and the label: no user-supplied text, so nothing
here can start with a spreadsheet formula character.
"""
from __future__ import annotations

import csv
import io

from . import LABEL
from .metrics import METRIC_NAMES, UNITS

INPUT_NAMES = ("duration_d", "capacity_loss", "queue_sensitivity")
BAND_KEYS = ("mean", "std", "min", "max", "p05", "p10", "p25", "p50", "p75", "p90", "p95")


def _csv(header: list, rows: list) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return v


def samples_csv(rec: dict) -> str:
    header = ["i", *INPUT_NAMES, *METRIC_NAMES, "stream_hash", "label"]
    rows = []
    for r in rec["rows"]:
        rows.append([r["i"], *[_cell(r["inputs"].get(k)) for k in INPUT_NAMES],
                     *[_cell(r["outputs"].get(m)) for m in METRIC_NAMES],
                     r["stream_hash"], LABEL])
    return _csv(header, rows)


def bands_csv(rec: dict) -> str:
    header = ["metric", "unit", "n", *BAND_KEYS, "label"]
    metrics = rec["results"]["metrics"]
    rows = [[m, UNITS[m], metrics[m]["n"], *[_cell(metrics[m].get(k)) for k in BAND_KEYS], LABEL]
            for m in METRIC_NAMES]
    return _csv(header, rows)


def job_json(view: dict, rec: dict) -> dict:
    """The whole finished job: the view (request, inputs, results) plus rows."""
    return {"label": LABEL, "job_id": view["job_id"], "status": view["status"],
            "request": view["request"], "engine": view["engine"], "inputs": view["inputs"],
            "pair_with": view.get("pair_with"), "results": view["results"],
            "samples": rec["rows"]}


def compare_csv(cmp: dict) -> str:
    header = ["metric", "unit", "paired", "n_a", "n_b",
              "a_p10", "a_p50", "a_p90", "a_mean", "b_p10", "b_p50", "b_p90", "b_mean",
              "delta_p50", "delta_p90", "overlap", "threshold",
              "b_higher_k", "pairs_n", "b_higher_share", "shift_low", "shift_high", "label"]
    rows = []
    for m in METRIC_NAMES:
        e = cmp["metrics"][m]
        sw, ci = e.get("share_b_worse"), e.get("shift_ci")
        rows.append([m, e["unit"], _cell(cmp["paired"]), cmp["n_a"], cmp["n_b"],
                     *[e["a"][k] for k in ("p10", "p50", "p90", "mean")],
                     *[e["b"][k] for k in ("p10", "p50", "p90", "mean")],
                     e["delta_p50"], e["delta_p90"], e["overlap"], cmp["threshold"],
                     _cell(sw and sw["k"]), _cell(sw and sw["n"]), _cell(sw and sw["share"]),
                     _cell(ci and ci["low"]), _cell(ci and ci["high"]), LABEL])
    return _csv(header, rows)
