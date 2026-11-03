"""PORTS-STATUS: the mechanical, published status/severity derivation.

Rule id PORTS-STATUS, version 1.1.0 (1.1.0: the level words describe a
deviation from a baseline and do not read as alarms: IN RANGE / DEVIATION
LOW / DEVIATION MID / DEVIATION HIGH; each component also names WHAT deviates). The rule is DATA (RULE below) and
is served verbatim in every ports payload; this module only applies it.
No prose judgement, no forecast: a place's level is the highest level
reached by any component that could be evaluated from served data.

Levels: 0 IN RANGE, 1 DEVIATION LOW, 2 DEVIATION MID, 3 DEVIATION HIGH. A place with no
evaluable component reads NO DATA (level None) — never IN RANGE.

Components
  PS-1  sea port activity ratio. ratio = (30-day mean port calls/day x
        365) / annual vessel-call baseline (PortWatch ports database).
        Evaluated only when the window has >= min_days_present days and
        the baseline has >= min_baseline_calls calls.
  PS-2  ground border wait. Worst commercial-lane wait (minutes) over
        the place's crossings that report a wait. Absolute thresholds.
  PS-3  active events. Highest severity (1-5) among the place's active
        events in the events feed.
  Chokepoint transits and air-hub aircraft counts have no published
  baseline in the repository, so they are NOT evaluated (listed under
  not_evaluated with that reason), never guessed.
"""
from __future__ import annotations

RULE = {
    "id": "PORTS-STATUS",
    "version": "1.1.0",
    "levels": [
        {"level": 0, "label": "IN RANGE"},
        {"level": 1, "label": "DEVIATION LOW"},
        {"level": 2, "label": "DEVIATION MID"},
        {"level": 3, "label": "DEVIATION HIGH"},
    ],
    "no_data_label": "NO DATA",
    "combine": "max over evaluated components",
    "limits": [
        "PS-1 compares two PortWatch tables built differently (30-day "
        "window mean vs the ports database's annual count): a low ratio "
        "flags a place for review, it is not a closure claim.",
        "Status is derived from served data only and is never a forecast.",
    ],
    "interval_semantics": "min inclusive, max exclusive, null = unbounded",
    "components": {
        "PS-1": {
            "applies_to": "kind=port",
            "metric": "activity_ratio",
            "formula": "portcalls_per_day.total * 365 / vessel_count_total",
            "min_days_present": 20,
            "min_baseline_calls": 100,
            "level_labels": {0: "ACTIVITY NEAR BASELINE",
                             1: "ACTIVITY BELOW BASELINE",
                             2: "LOW ACTIVITY VS BASELINE",
                             3: "LOW ACTIVITY VS BASELINE"},
            "intervals": [
                {"level": 0, "min": 0.75, "max": None},
                {"level": 1, "min": 0.50, "max": 0.75},
                {"level": 2, "min": 0.25, "max": 0.50},
                {"level": 3, "min": None, "max": 0.25},
            ],
        },
        "PS-2": {
            "applies_to": "kind=ground_node",
            "metric": "worst_commercial_wait_min",
            "level_labels": {0: "WAIT WITHIN RANGE", 1: "WAIT ABOVE RANGE",
                             2: "LONG WAIT", 3: "LONG WAIT"},
            "intervals": [
                {"level": 0, "min": None, "max": 30},
                {"level": 1, "min": 30, "max": 60},
                {"level": 2, "min": 60, "max": 120},
                {"level": 3, "min": 120, "max": None},
            ],
        },
        "PS-3": {
            "applies_to": "all kinds",
            "metric": "max_active_event_severity",
            "level_labels": {1: "ACTIVE EVENT", 2: "ACTIVE EVENT",
                             3: "ACTIVE EVENT"},
            "below_range": "severity under 3 assigns no level: the "
                           "component is not evaluated (events can raise "
                           "a level, never establish IN RANGE)",
            "intervals": [
                {"level": 1, "min": 3, "max": 4},
                {"level": 2, "min": 4, "max": 5},
                {"level": 3, "min": 5, "max": None},
            ],
        },
    },
}

LABELS = {t["level"]: t["label"] for t in RULE["levels"]}


def _level(component: str, value: float) -> int:
    """Interval lookup: min inclusive, max exclusive, None = unbounded."""
    for iv in RULE["components"][component]["intervals"]:
        lo, hi = iv["min"], iv["max"]
        if (lo is None or value >= lo) and (hi is None or value < hi):
            return iv["level"]
    raise ValueError(f"no interval for {component}={value}")


def _basis_label(component: str, level: int) -> str:
    """What deviates, in words that make no closure claim."""
    return RULE["components"][component]["level_labels"][level]


def ratio_inputs(port_calls_per_day, days_present, baseline_calls):
    """(ratio|None, reason|None) per PS-1's evaluability conditions."""
    comp = RULE["components"]["PS-1"]
    if port_calls_per_day is None or baseline_calls is None:
        return None, "port-call window mean or annual baseline not served"
    if (days_present or 0) < comp["min_days_present"]:
        return None, (f"window has {days_present} days present "
                      f"(< {comp['min_days_present']})")
    if baseline_calls < comp["min_baseline_calls"]:
        return None, (f"annual baseline {baseline_calls} calls is below "
                      f"the {comp['min_baseline_calls']}-call floor")
    return (port_calls_per_day * 365.0) / float(baseline_calls), None


def derive(kind: str, *, activity_ratio=None, ratio_reason=None,
           worst_wait_min=None, wait_reason=None,
           max_event_severity=None) -> dict:
    """Apply the rule. Returns {level, label, rule_id, rule_version,
    basis[], not_evaluated[]}."""
    basis, skipped = [], []

    if kind == "port":
        if activity_ratio is None:
            skipped.append({"component": "PS-1",
                            "reason": ratio_reason or "not served"})
        else:
            lv = _level("PS-1", activity_ratio)
            basis.append({"component": "PS-1", "metric": "activity_ratio",
                          "value": round(activity_ratio, 4), "level": lv,
                          "label": _basis_label("PS-1", lv)})
    elif kind == "ground_node":
        if worst_wait_min is None:
            skipped.append({"component": "PS-2",
                            "reason": wait_reason or "not served"})
        else:
            lv = _level("PS-2", worst_wait_min)
            basis.append({"component": "PS-2",
                          "metric": "worst_commercial_wait_min",
            "level_labels": {0: "WAIT WITHIN RANGE", 1: "WAIT ABOVE RANGE",
                             2: "LONG WAIT", 3: "LONG WAIT"},
                          "value": worst_wait_min, "level": lv,
                          "label": _basis_label("PS-2", lv)})
    elif kind == "chokepoint":
        skipped.append({"component": "ACTIVITY-BASELINE",
                        "reason": "no published baseline for strait "
                                  "transits in the repository"})
    elif kind == "air_hub":
        skipped.append({"component": "ACTIVITY-BASELINE",
                        "reason": "no published baseline for hub aircraft "
                                  "counts in the repository"})

    if max_event_severity is not None and max_event_severity >= 3:
        lv = _level("PS-3", float(max_event_severity))
        basis.append({"component": "PS-3",
                      "metric": "max_active_event_severity",
                      "value": max_event_severity, "level": lv,
                      "label": _basis_label("PS-3", lv)})
    else:
        skipped.append({"component": "PS-3",
                        "reason": "no active event of severity 3 or more "
                                  "at this place (events can raise a "
                                  "level, never establish IN RANGE)"})

    level = max((b["level"] for b in basis), default=None)
    return {"level": level,
            "label": LABELS[level] if level is not None
            else RULE["no_data_label"],
            "rule_id": RULE["id"], "rule_version": RULE["version"],
            "basis": basis, "not_evaluated": skipped}
