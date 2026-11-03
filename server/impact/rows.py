"""Flat per-place rows from an IMPACT run payload (export/report
consumer). One row per place, tagged with the place's own unit class.

LAW (enforced by the payload schema guard): there
is no total across places in different unit classes and none across
goods classes. Rows are therefore per place; a consumer may sum a column
only within one `unit_class`. This module never sums anything.
"""
from __future__ import annotations


def place_rows(run_payload: dict) -> list[dict]:
    """[{node_id, name, kind, unit_class, capacity, capacity_class,
    capacity_reason, capacity_at_risk, capacity_at_risk_unit,
    worst_magnitude, max_delay_d, hits}] sorted by (unit_class, node_id).
    A None numeric means the impact layer served an ABSENT figure for it
    (reason in the matching *_reason); it is never a zero."""
    rows = []
    for unit in run_payload["volume_at_risk"]["by_unit"]:
        for p in unit["places"]:
            cap, risk = p["capacity"], p["capacity_at_risk"]
            rows.append({
                "node_id": p["node_id"], "name": p.get("name"),
                "kind": p.get("kind"), "unit_class": unit["unit"],
                "capacity": cap.get("value"),
                "capacity_class": cap.get("class"),
                "capacity_reason": cap.get("reason"),
                "capacity_at_risk": risk.get("value"),
                "capacity_at_risk_unit": risk.get("unit"),
                "capacity_at_risk_reason": risk.get("reason"),
                "worst_magnitude": p["worst_magnitude"].get("value"),
                "max_delay_d": p["max_delay"].get("value"),
                "hits": p.get("hits"),
            })
    rows.sort(key=lambda r: (str(r["unit_class"]), r["node_id"]))
    return rows
