"""Deterministic timeline arrangement: a structured rule in, new shock starts out.

No model, no randomness, no clock. The agent translates a sentence into
one of these rules and the server applies it, so the same rule over the
same shocks always gives the same starts. Durations are never changed;
only start offsets move. Starts are snapped to ``snap_d`` (default 0.25 d)
and the snap is reported. Nothing is clamped: a rule that would put a
start outside [0, 365] d is refused with the shock and the value.

Rules (``kind``):

  stagger        Lay shocks end to end in their current order (start, then
                 list position). ``gap_d`` after the previous END (default 0),
                 or after the previous START with ``reference: "start"``.
                 The first shock keeps its start unless ``from_d`` is given.
  sequence       As stagger, in the order of ``order`` (shock ids). Shocks
                 not named follow in their current order.
  cluster        Pull starts together so all lie within ``within_d`` days of
                 the earliest start. Starts are scaled toward the earliest
                 start; a spread already inside the window is unchanged.
  align_start    Every shock starts at ``day``.
  spread_evenly  Spread starts evenly across ``window_d`` days from the
                 earliest start (or ``from_d``), in the current order.
  shift          Add ``delta_d`` to every start (or to ``ids`` only).
"""
from __future__ import annotations

import math

from .model import (MAX_SHOCKS, OFFSET_MAX_D, ScenarioError, ID_RE, overlaps, r4, span)

RULE_KINDS = ("stagger", "sequence", "cluster", "align_start", "spread_evenly", "shift")
RULE_KEYS = {"kind", "gap_d", "reference", "order", "from_d", "within_d", "day", "window_d",
             "delta_d", "ids", "snap_d"}
DEFAULT_SNAP_D = 0.25


def rules_spec() -> dict:
    """What the agent and the UI may send."""
    return {
        "kinds": list(RULE_KINDS),
        "snap_d": {"default": DEFAULT_SNAP_D, "min": 0.01, "max": 1.0},
        "params": {
            "stagger": {"gap_d": "number, -365 to 365, default 0",
                        "reference": "end (default) or start of the previous shock",
                        "from_d": "optional start of the first shock"},
            "sequence": {"order": "shock ids, first to last", "gap_d": "as stagger",
                         "reference": "as stagger", "from_d": "as stagger"},
            "cluster": {"within_d": "number, 0 to 365"},
            "align_start": {"day": "number, 0 to 365"},
            "spread_evenly": {"window_d": "number, 0 to 365", "from_d": "optional"},
            "shift": {"delta_d": "number, -365 to 365", "ids": "optional shock ids"},
        },
    }


def _number(rule: dict, key: str, lo: float, hi: float, default=None):
    v = rule.get(key, default)
    if v is None:
        raise ScenarioError("invalid_rule", f"{key} is required")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ScenarioError("invalid_rule", f"{key} must be a number")
    if not (lo <= v <= hi):
        raise ScenarioError("invalid_rule", f"{key} out of bounds [{lo:g}, {hi:g}]: {v}")
    return float(v)


def _snap(x: float, snap: float) -> float:
    return r4(round(x / snap) * snap)


def validate_rule(rule) -> dict:
    if not isinstance(rule, dict):
        raise ScenarioError("invalid_rule", "rule must be an object")
    extra = sorted(set(rule) - RULE_KEYS)
    if extra:
        raise ScenarioError("invalid_rule", f"unknown rule keys: {extra}")
    kind = rule.get("kind")
    if kind not in RULE_KINDS:
        raise ScenarioError("invalid_rule", f"kind must be one of {list(RULE_KINDS)}")
    out = {"kind": kind, "snap_d": _number(rule, "snap_d", 0.01, 1.0, DEFAULT_SNAP_D)}
    if kind in ("stagger", "sequence"):
        out["gap_d"] = _number(rule, "gap_d", -OFFSET_MAX_D, OFFSET_MAX_D, 0.0)
        ref = rule.get("reference", "end")
        if ref not in ("end", "start"):
            raise ScenarioError("invalid_rule", "reference must be 'end' or 'start'")
        out["reference"] = ref
        if "from_d" in rule:
            out["from_d"] = _number(rule, "from_d", 0.0, OFFSET_MAX_D)
    if kind == "sequence":
        order = rule.get("order")
        if (not isinstance(order, list) or not order or len(order) > MAX_SHOCKS
                or any(not isinstance(x, str) or not ID_RE.match(x) for x in order)
                or len(set(order)) != len(order)):
            raise ScenarioError("invalid_rule", "order must be a list of distinct shock ids")
        out["order"] = list(order)
    if kind == "cluster":
        out["within_d"] = _number(rule, "within_d", 0.0, OFFSET_MAX_D)
    if kind == "align_start":
        out["day"] = _number(rule, "day", 0.0, OFFSET_MAX_D)
    if kind == "spread_evenly":
        out["window_d"] = _number(rule, "window_d", 0.0, OFFSET_MAX_D)
        if "from_d" in rule:
            out["from_d"] = _number(rule, "from_d", 0.0, OFFSET_MAX_D)
    if kind == "shift":
        out["delta_d"] = _number(rule, "delta_d", -OFFSET_MAX_D, OFFSET_MAX_D)
        ids = rule.get("ids")
        if ids is not None:
            if (not isinstance(ids, list) or not ids or len(ids) > MAX_SHOCKS
                    or any(not isinstance(x, str) or not ID_RE.match(x) for x in ids)):
                raise ScenarioError("invalid_rule", "ids must be a list of shock ids")
            out["ids"] = list(ids)
    return out


def _read_shocks(shocks) -> list[dict]:
    if not isinstance(shocks, list) or not shocks:
        raise ScenarioError("invalid_request", "shocks must be a non-empty list")
    if len(shocks) > MAX_SHOCKS:
        raise ScenarioError("too_many_shocks", f"at most {MAX_SHOCKS} shocks per scenario")
    out, seen = [], set()
    for i, s in enumerate(shocks):
        if not isinstance(s, dict):
            raise ScenarioError("invalid_request", f"shocks[{i}] must be an object")
        sid = s.get("id")
        if not isinstance(sid, str) or not ID_RE.match(sid):
            raise ScenarioError("invalid_request", f"shocks[{i}].id must match [A-Za-z0-9_-]{{1,24}}")
        if sid in seen:
            raise ScenarioError("duplicate_shock_id", f"shocks[{i}].id {sid!r} is used twice")
        seen.add(sid)
        for k in ("start_offset_d", "duration_d"):
            v = s.get(k)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ScenarioError("invalid_request", f"shocks[{i}].{k} must be a number")
        if s["duration_d"] <= 0 or s["start_offset_d"] < 0:
            raise ScenarioError("invalid_request", f"shocks[{i}]: duration_d must be above 0 and start_offset_d at least 0")
        place = s.get("place_id")
        out.append({"id": sid, "place_id": place if isinstance(place, str) else "",
                    "start_offset_d": float(s["start_offset_d"]), "duration_d": float(s["duration_d"])})
    return out


def organise(shocks, rule) -> dict:
    """Apply a structured rule. Returns the new starts, a per-shock diff and
    overlap and span before and after. Raises ScenarioError on refusal."""
    rule = validate_rule(rule)
    rows = _read_shocks(shocks)
    snap = rule["snap_d"]
    pos = {s["id"]: i for i, s in enumerate(rows)}
    by_id = {s["id"]: s for s in rows}
    by_start = sorted(rows, key=lambda s: (s["start_offset_d"], pos[s["id"]]))
    new: dict[str, float] = {s["id"]: s["start_offset_d"] for s in rows}
    kind = rule["kind"]

    if kind in ("stagger", "sequence"):
        if kind == "sequence":
            for sid in rule["order"]:
                if sid not in by_id:
                    raise ScenarioError("unknown_shock", f"order names an unknown shock: {sid!r}")
            named = [by_id[x] for x in rule["order"]]
            rest = [s for s in by_start if s["id"] not in set(rule["order"])]
            ordered = named + rest
        else:
            ordered = by_start
        anchor = rule.get("from_d", by_start[0]["start_offset_d"])
        prev = None
        for s in ordered:
            if prev is None:
                start = anchor
            else:
                base = new[prev["id"]] + (prev["duration_d"] if rule["reference"] == "end" else 0.0)
                start = base + rule["gap_d"]
            new[s["id"]] = _snap(start, snap)
            prev = s
    elif kind == "cluster":
        anchor = by_start[0]["start_offset_d"]
        spread = max(s["start_offset_d"] for s in rows) - anchor
        k = 1.0 if spread <= rule["within_d"] or spread == 0 else rule["within_d"] / spread
        for s in rows:
            new[s["id"]] = _snap(anchor + (s["start_offset_d"] - anchor) * k, snap)
    elif kind == "align_start":
        for s in rows:
            new[s["id"]] = _snap(rule["day"], snap)
    elif kind == "spread_evenly":
        anchor = rule.get("from_d", by_start[0]["start_offset_d"])
        n = len(by_start)
        for i, s in enumerate(by_start):
            new[s["id"]] = _snap(anchor + (rule["window_d"] * i / (n - 1) if n > 1 else 0.0), snap)
    elif kind == "shift":
        ids = rule.get("ids")
        if ids is not None:
            for sid in ids:
                if sid not in by_id:
                    raise ScenarioError("unknown_shock", f"ids names an unknown shock: {sid!r}")
        for s in rows:
            if ids is None or s["id"] in ids:
                new[s["id"]] = _snap(s["start_offset_d"] + rule["delta_d"], snap)

    for sid, v in new.items():
        if not (0.0 <= v <= OFFSET_MAX_D):
            raise ScenarioError("offset_out_of_bounds",
                                f"shock {sid!r} would start at {v:g} d, outside [0, {OFFSET_MAX_D:g}] (refused, never clamped)")

    before = [{**s} for s in rows]
    after = [{**s, "start_offset_d": new[s["id"]]} for s in rows]
    diff = [{"id": s["id"], "from_d": r4(s["start_offset_d"]), "to_d": new[s["id"]],
             "moved": r4(s["start_offset_d"]) != new[s["id"]]} for s in rows]
    return {
        "rule": rule,
        "shocks": [{"id": s["id"], "start_offset_d": s["start_offset_d"]} for s in after],
        "diff": diff,
        "moved": sum(1 for d in diff if d["moved"]),
        "overlaps_before": overlaps(before), "overlaps_after": overlaps(after),
        "span_before": span(before), "span_after": span(after),
        "snap_d": snap,
    }
