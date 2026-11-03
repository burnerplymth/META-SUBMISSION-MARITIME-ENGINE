"""Scenario object: normalisation, per-shock verdicts, overlap and span.

A scenario is an ordered list of shocks on one timeline. Each shock is
validated by the engine's own shock book (``validate_disruption``), once
per target (the chain engine sees the CORE places, the simulator sees all
places), so the builder reports a verdict per shock and per target and
never invents a rule of its own for what the engines accept.

What the targets do with a list of shocks:

* chain engine: one root shock per run. A scenario is therefore a SET of
  independent runs, one per shock, each exactly the run ``POST /run``
  gives that shock alone. Interaction between shocks is not modelled and
  the combined table is a labelled superposition (APPROXIMATED).
* simulator: every shock is a native capacity window; windows at one
  place combine by maximum loss, windows at different places are
  independent. Step shape only.

Nothing here touches the engine's propagation. Pure functions.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

try:
    from engine.canonical import canonical_json, sha256_hex
    from engine.constants import const
    from engine.engine import DISRUPTION_KINDS, SHAPES, EngineError, validate_disruption
except ImportError:  # imported as server.scenario.* from the repo root
    from server.engine.canonical import canonical_json, sha256_hex  # type: ignore
    from server.engine.constants import const  # type: ignore
    from server.engine.engine import (DISRUPTION_KINDS, SHAPES, EngineError,  # type: ignore
                                      validate_disruption)

MAX_SHOCKS = 12
MAX_NAME = 60
MAX_NOTE = 140
MAX_NOTES = 280
OFFSET_MAX_D = 365.0
OFFSET_DECIMALS = 4
SIM_HORIZON_D = 180          # sim tick=day, MAX_TICKS["day"]
SIM_DEFAULT_SEED = 1
DEFAULT_START_AT = "2026-11-02T00:00:00Z"
# start_at window: the years 2000 to 2099. Outside it strftime/fromtimestamp raise (year < 1 or
# > 9999) or print an unpadded year, and a scenario that far from today is not a plan.
START_MIN_YEAR = 2000
START_MAX_YEAR = 2099
START_MIN_EPOCH = datetime(START_MIN_YEAR, 1, 1, tzinfo=timezone.utc).timestamp()
START_MAX_EPOCH = datetime(START_MAX_YEAR + 1, 1, 1, tzinfo=timezone.utc).timestamp()
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
SCENARIO_ID_RE = re.compile(r"^scn_[0-9a-f]{16}$")
TARGETS = ("chain", "sim")
SHOCK_KEYS = {"id", "place_id", "kind", "start_offset_d", "start_at", "duration_d",
              "capacity_loss", "shape", "note"}
SCENARIO_KEYS = {"name", "start_at", "shocks", "notes"}


class ScenarioError(Exception):
    """A structural refusal (malformed input): the standard 422 error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def parse_iso(text: str) -> float:
    """ISO-8601 -> UTC epoch seconds (naive = UTC). ValueError on junk."""
    if not isinstance(text, str) or len(text) > 40:
        raise ValueError("not a timestamp")
    s = text.strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_start(text: str, what: str = "start_at") -> float:
    """parse_iso plus the year window. ScenarioError (a 422) on junk or an out-of-window date."""
    try:
        epoch = parse_iso(text)
    except (ValueError, TypeError, OverflowError, OSError):
        raise ScenarioError("invalid_request", f"{what} is not a valid ISO-8601 timestamp") from None
    if not START_MIN_EPOCH <= epoch < START_MAX_EPOCH:
        raise ScenarioError("param_out_of_bounds",
                            f"{what} must fall in the years {START_MIN_YEAR} to {START_MAX_YEAR}")
    return epoch


def fmt_iso(epoch: float) -> str:
    return datetime.fromtimestamp(round(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def r4(x: float) -> float:
    return round(float(x), OFFSET_DECIMALS)


def rules() -> dict:
    """The bounds the builder enforces (a superset of /shocks/rules)."""
    return {
        "max_shocks": MAX_SHOCKS,
        "kinds": sorted(DISRUPTION_KINDS),
        "shapes": sorted(SHAPES),
        "start_offset_d": {"min": 0.0, "max": OFFSET_MAX_D},
        "duration_d": {"min_exclusive": 0.0, "max": const("duration_max_d")},
        "capacity_loss": {"min_exclusive": 0.0, "max": 1.0,
                          "closure_forces": const("closure_capacity_loss")},
        "start_at": {"min_year": START_MIN_YEAR, "max_year": START_MAX_YEAR},
        "defaults": {"start_at": DEFAULT_START_AT,
                     "capacity_loss": const("default_capacity_loss"),
                     "duration_d": const("default_duration_d")},
        "targets": list(TARGETS),
        "sim": {"tick": "day", "horizon_d": SIM_HORIZON_D, "shapes": ["step"],
                "seed": SIM_DEFAULT_SEED},
        "name_max": MAX_NAME, "note_max": MAX_NOTE, "notes_max": MAX_NOTES,
        "semantics": semantics(),
    }


def semantics() -> dict:
    """Exact versus approximated, per target, as codes and short labels."""
    return {
        "chain": {
            "unit": "one independent run per shock",
            "exact": ["each run is the run of that shock alone",
                      "run ids and stream hashes match a single-shock run",
                      "start time shifts event timestamps only"],
            "approximated": ["combined table: largest delay by place, and a sum over shocks "
                             "whose windows overlap at that place (never over shocks apart in time)",
                             "no interaction between shocks"],
            "not_supported": ["two root shocks in one run",
                              "a fork that adds a second place"],
        },
        "sim": {
            "unit": "one run, every shock a native window",
            "exact": ["windows at different places are independent",
                      "windows at one place combine by maximum loss",
                      "start and duration are honoured to the second"],
            "approximated": [],
            "not_supported": ["ramp and pulse shapes",
                              "horizon beyond 180 days"],
        },
    }


def _verdict(ok: bool, code: str | None = None, reason: str | None = None) -> dict:
    return {"ok": ok, "code": code, "reason": reason}


def _place_name(graph, pid) -> str | None:
    node = graph.nodes.get(pid) if isinstance(pid, str) else None
    return node.get("name") if node else None


def normalise_scenario(raw) -> dict:
    """Structural normalisation. Returns {name, start_at, start_at_defaulted,
    notes, shocks:[raw shock dicts with ids and numeric offsets]}.
    Raises ScenarioError on malformed input. Domain checks (places, bounds)
    are the per-shock verdicts, not exceptions."""
    if not isinstance(raw, dict):
        raise ScenarioError("invalid_request", "scenario must be an object")
    extra = sorted(set(raw) - SCENARIO_KEYS)
    if extra:
        raise ScenarioError("invalid_request", f"unknown scenario keys: {extra}")
    name = raw.get("name", "")
    if not isinstance(name, str) or len(name) > MAX_NAME:
        raise ScenarioError("invalid_request", f"name must be a string of at most {MAX_NAME} characters")
    notes = raw.get("notes", "")
    if not isinstance(notes, str) or len(notes) > MAX_NOTES:
        raise ScenarioError("invalid_request", f"notes must be a string of at most {MAX_NOTES} characters")
    shocks_in = raw.get("shocks")
    if not isinstance(shocks_in, list):
        raise ScenarioError("invalid_request", "shocks must be a list")
    if not shocks_in:
        raise ScenarioError("empty_scenario", "a scenario needs at least one shock")
    if len(shocks_in) > MAX_SHOCKS:
        raise ScenarioError("too_many_shocks", f"at most {MAX_SHOCKS} shocks per scenario")
    start_in = raw.get("start_at")
    if start_in is not None and not isinstance(start_in, str):
        raise ScenarioError("invalid_request", "start_at must be an ISO-8601 string")
    base_epoch = None
    if start_in is not None:
        base_epoch = parse_start(start_in, "start_at")
    # absolute per-shock starts need a base: the earliest one when none is given
    abs_starts = []
    shocks = []
    seen = set()
    for i, s in enumerate(shocks_in):
        tag = f"shocks[{i}]"
        if not isinstance(s, dict):
            raise ScenarioError("invalid_request", f"{tag} must be an object")
        extra = sorted(set(s) - SHOCK_KEYS)
        if extra:
            raise ScenarioError("invalid_request", f"{tag}: unknown keys {extra}")
        sid = s.get("id")
        if sid is None:
            n = 1
            while f"s{n}" in seen or any(isinstance(x, dict) and x.get("id") == f"s{n}" for x in shocks_in):
                n += 1
            sid = f"s{n}"
        if not isinstance(sid, str) or not ID_RE.match(sid):
            raise ScenarioError("invalid_request", f"{tag}.id must match [A-Za-z0-9_-]{{1,24}}")
        if sid in seen:
            raise ScenarioError("duplicate_shock_id", f"{tag}.id {sid!r} is used twice")
        seen.add(sid)
        note = s.get("note", "")
        if not isinstance(note, str) or len(note) > MAX_NOTE:
            raise ScenarioError("invalid_request", f"{tag}.note must be a string of at most {MAX_NOTE} characters")
        if "start_offset_d" in s and "start_at" in s:
            raise ScenarioError("invalid_request", f"{tag}: give start_offset_d or start_at, not both")
        offset = s.get("start_offset_d")
        sa = s.get("start_at")
        if sa is not None:
            if not isinstance(sa, str):
                raise ScenarioError("invalid_request", f"{tag}.start_at must be an ISO-8601 string")
            abs_starts.append((i, parse_start(sa, f"{tag}.start_at")))
        elif offset is None:
            offset = 0.0
        shocks.append({"id": sid, "place_id": s.get("place_id"), "kind": s.get("kind"),
                       "start_offset_d": offset, "duration_d": s.get("duration_d"),
                       "capacity_loss": s.get("capacity_loss"), "shape": s.get("shape"),
                       "note": note})
    defaulted = False
    if abs_starts and base_epoch is None:
        base_epoch = min(e for _, e in abs_starts)
    for i, e in abs_starts:
        shocks[i]["start_offset_d"] = r4((e - base_epoch) / 86400.0)
    if base_epoch is None:
        base_epoch = parse_iso(DEFAULT_START_AT)
        defaulted = True
    return {"name": name.strip(), "start_at": fmt_iso(base_epoch), "start_at_defaulted": defaulted,
            "notes": notes.strip(), "shocks": shocks}


def check_shock(raw: dict, graph_chain, graph_sim, chain_rev_graph=None) -> dict:
    """Verdicts of one shock against the shock book, per target."""
    off = raw["start_offset_d"]
    off_bad = None
    if not _num(off):
        off_bad = ("invalid_request", "start_offset_d must be a number")
    elif not (0.0 <= off <= OFFSET_MAX_D):
        off_bad = ("param_out_of_bounds", f"start_offset_d out of bounds [0, {OFFSET_MAX_D:g}]: {off}")
    spec_in = {"node_id": raw["place_id"], "kind": raw["kind"]}
    for k in ("duration_d", "capacity_loss", "shape"):
        if raw.get(k) is not None:
            spec_in[k] = raw[k]
    if raw.get("kind") == "closure" and "capacity_loss" not in spec_in:
        # the engine forces a closure's loss; it only insists the field is present
        spec_in["capacity_loss"] = const("closure_capacity_loss")
    out = {"id": raw["id"], "place_id": raw["place_id"],
           "place_name": _place_name(graph_sim, raw["place_id"]),
           "verdicts": {}, "spec": None}
    for target, graph in (("chain", chain_rev_graph or graph_chain), ("sim", graph_sim)):
        if off_bad:
            out["verdicts"][target] = _verdict(False, *off_bad)
            continue
        try:
            spec = validate_disruption(graph, spec_in)
        except EngineError as exc:
            out["verdicts"][target] = _verdict(False, exc.code, str(exc))
            continue
        if target == "sim":
            if spec["shape"] != "step":
                out["verdicts"][target] = _verdict(
                    False, "invalid_shape", f"sim supports step-shape shocks only; got {spec['shape']!r}")
                continue
            if off + spec["duration_d"] > SIM_HORIZON_D:
                out["verdicts"][target] = _verdict(
                    False, "span_exceeds_horizon",
                    f"start + duration {off + spec['duration_d']:g} d exceeds the {SIM_HORIZON_D} d sim horizon")
                continue
        out["verdicts"][target] = _verdict(True)
        if out["spec"] is None:
            out["spec"] = {"place_id": spec["node_id"], "kind": spec["kind"],
                           "capacity_loss": spec["capacity_loss"], "duration_d": spec["duration_d"],
                           "shape": spec["shape"]}
    out["accepted"] = any(v["ok"] for v in out["verdicts"].values())
    return out


def windows(shocks: list[dict]) -> list[dict]:
    return [{"id": s["id"], "place_id": s["place_id"], "start_d": r4(s["start_offset_d"]),
             "end_d": r4(s["start_offset_d"] + s["duration_d"])} for s in shocks]


def overlaps(shocks: list[dict]) -> list[dict]:
    """Pairs of shocks whose windows overlap in time (strictly), in list order."""
    out = []
    w = windows(shocks)
    for i in range(len(w)):
        for j in range(i + 1, len(w)):
            a, b = w[i], w[j]
            lo, hi = max(a["start_d"], b["start_d"]), min(a["end_d"], b["end_d"])
            if lo < hi:
                out.append({"a": a["id"], "b": b["id"], "overlap_d": r4(hi - lo),
                            "same_place": a["place_id"] == b["place_id"]})
    return out


def span(shocks: list[dict]) -> dict:
    if not shocks:
        return {"first_start_d": 0.0, "last_end_d": 0.0, "total_span_d": 0.0}
    w = windows(shocks)
    lo, hi = min(x["start_d"] for x in w), max(x["end_d"] for x in w)
    return {"first_start_d": lo, "last_end_d": hi, "total_span_d": r4(hi - lo)}


def validate_scenario(raw, graph_chain, graph_sim, chain_rev_graph=None) -> dict:
    """Full validation. Raises ScenarioError only for malformed input."""
    norm = normalise_scenario(raw)
    rows = [check_shock(s, graph_chain, graph_sim, chain_rev_graph) for s in norm["shocks"]]
    accepted = {t: all(r["verdicts"][t]["ok"] for r in rows) for t in TARGETS}
    stored = []
    for s, r in zip(norm["shocks"], rows):
        if r["spec"] is None:
            continue
        stored.append({"id": s["id"], "place_id": r["spec"]["place_id"], "kind": r["spec"]["kind"],
                       "start_offset_d": r4(s["start_offset_d"]),
                       "duration_d": r["spec"]["duration_d"],
                       "capacity_loss": r["spec"]["capacity_loss"], "shape": r["spec"]["shape"],
                       "note": s["note"]})
    complete = len(stored) == len(rows)
    result = {
        "name": norm["name"], "start_at": norm["start_at"],
        "start_at_defaulted": norm["start_at_defaulted"], "notes": norm["notes"],
        "shocks": rows,
        "accepted": accepted,
        "runnable": complete and any(accepted.values()),
        "normalised": ({"name": norm["name"], "start_at": norm["start_at"], "shocks": stored,
                        "notes": norm["notes"]} if complete else None),
    }
    if complete:
        ordered = list(stored)
        result["overlaps"] = overlaps(ordered)
        result["span"] = span(ordered)
        for r, s in zip(rows, ordered):
            r["window"] = {"start_d": r4(s["start_offset_d"]),
                           "end_d": r4(s["start_offset_d"] + s["duration_d"]),
                           "start_at": fmt_iso(parse_iso(norm["start_at"]) + s["start_offset_d"] * 86400.0),
                           "end_at": fmt_iso(parse_iso(norm["start_at"])
                                             + (s["start_offset_d"] + s["duration_d"]) * 86400.0)}
    else:
        result["overlaps"], result["span"] = [], None
    return result


def scenario_id(normalised: dict) -> str:
    return "scn_" + sha256_hex(canonical_json(normalised))[:16]


def chain_order(scn: dict) -> list[dict]:
    """Shocks in run order: by start, ties by list position (stable)."""
    idx = {s["id"]: i for i, s in enumerate(scn["shocks"])}
    return sorted(scn["shocks"], key=lambda s: (s["start_offset_d"], idx[s["id"]]))
