"""FORK: historical event(s) -> the exact request bodies for the two
simulators, plus a ``forked_from`` lineage block.

This module BUILDS requests; it never runs a simulation. Two targets:

  * POST /run  (chain): one body per event,
    ``{"source": "scenario", "scenario": {"disruption": {...}}, "params": {...}}``
    - the disruption is the event in the engine's spec vocabulary
    (node_id, kind, capacity_loss, duration_d, shape=step, started_at =
    the historical start). The chain engine is cheap and deterministic, so
    the route may create the run for you (``chain.create``).
  * POST /sim/run (sim v1): ONE body for the whole set,
    ``{"disruptions": [...], "tick": "day", "ticks": N, "seed": 42,
    "start_at": <lead-in days before the earliest event>, "weather": false}``.
    The lead-in gives the sim a quiet baseline to compare against (the
    realism tester needs it), and weather is OFF by default because no
    historical weather snapshot exists in the sim - turning it on would
    pin TODAY's forecast to a past date. The sim run itself is expensive:
    the route returns the ready body and leaves POSTing it to the caller.

The lineage block says exactly what was forked: event ids, their source
and mode, the window, and the label that everything downstream is
SIMULATED from a recorded (SNAPSHOT) or inferred (MODELED) event.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

from .util import (HistoryError, canonical, iso_day, parse_day, sha256_hex,
                   utcnow_iso)

SIM_TICK_SECONDS_DAY = 86400
SIM_MAX_DAY_TICKS = 180          # mirrors server/sim/state.py MAX_TICKS["day"]
SIM_SPEC_KEYS = ("node_id", "kind", "capacity_loss", "duration_d",
                 "started_at", "shape")
DEFAULT_LEAD_IN_D = 14
DEFAULT_TAIL_D = 14
DEFAULT_SEED = 42
MAX_EVENTS = 8
DEFAULT_DURATION_D = 7.0          # the engine's own default; flagged when used

FORK_LABEL = ("FORKED FROM HISTORY — the request below is derived from a "
              "recorded (SNAPSHOT) or inferred (MODELED) event; whatever "
              "runs from it is SIMULATED and must be labelled so.")


class ForkLog:
    """Tiny on-disk lineage log: ``<data>/history_forks/<lineage_id>.json``
    = {forked_from, chain_run_ids}. Written only when a fork created a
    chain run, so the realism tester can show where a run came from."""

    def __init__(self, root):
        from pathlib import Path
        self.root = Path(root)

    def save(self, forked_from: dict, run_ids: list[str]) -> None:
        import json
        import os
        import tempfile
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{forked_from['lineage_id']}.json"
        doc = {"forked_from": forked_from, "chain_run_ids": sorted(run_ids)}
        fd, tmp = tempfile.mkstemp(dir=str(self.root), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, sort_keys=True, indent=1)
        os.replace(tmp, path)

    def find_by_event(self, node_id: str, started_at: str) -> dict | None:
        """The lineage of a fork that contained this event (matched on
        place + start): lets a sim run, whose id the fork cannot know in
        advance, still show where it came from."""
        import json
        if not self.root.is_dir():
            return None
        for p in sorted(self.root.glob("fork_*.json")):
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for ev in (doc.get("forked_from") or {}).get("events", []):
                if ev.get("node_id") == node_id and ev.get("started_at") == started_at:
                    return {**doc["forked_from"], "matched_by": "event (place + start)"}
        return None

    def find(self, run_id: str) -> dict | None:
        import json
        if not self.root.is_dir():
            return None
        for p in sorted(self.root.glob("fork_*.json")):
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if run_id in (doc.get("chain_run_ids") or []):
                return doc.get("forked_from")
        return None


def _ts(v) -> str:
    if isinstance(v, str) and "T" in v:
        return v
    return parse_day(v).isoformat() + "T00:00:00Z"


def event_to_disruption(ev: dict) -> tuple[dict, list[str]]:
    """Event -> engine/sim disruption spec (+ notes on every default
    or approximation applied; nothing is silently invented)."""
    notes: list[str] = []
    spec: dict = {"node_id": ev["node_id"], "kind": ev["kind"],
                  "shape": "step"}
    if ev["kind"] == "closure":
        spec["capacity_loss"] = 1.0
    elif ev.get("capacity_loss") is not None:
        spec["capacity_loss"] = float(ev["capacity_loss"])
    elif ev["kind"] == "capacity_cut":
        notes.append("capacity_cut event without a capacity_loss: the "
                     "engine will refuse it (missing_capacity_loss)")
    else:
        notes.append(f"no capacity_loss on this {ev['kind']} event: the "
                     "engine's default capacity loss applies (not a "
                     "sourced figure)")
    dur = ev.get("expected_duration_d")
    if dur is None and ev.get("expires_at") and ev.get("started_at"):
        dur = (datetime.fromisoformat(_ts(ev["expires_at"]).replace("Z", "+00:00"))
               - datetime.fromisoformat(ev["started_at"].replace("Z", "+00:00"))
               ).total_seconds() / 86400.0
    if dur is None:
        dur = DEFAULT_DURATION_D
        notes.append(f"event states no duration: duration_d defaulted to "
                     f"{DEFAULT_DURATION_D} d (the engine's own default)")
    spec["duration_d"] = round(max(float(dur), 0.01), 4)
    spec["started_at"] = ev["started_at"]
    if ev.get("mode") == "MODELED":
        notes.append("event is MODELED (inferred or modeled-basis): its "
                     "capacity_loss is not a sourced figure")
    return spec, notes


def _lineage_event(ev: dict) -> dict:
    out = {"event_id": ev["id"], "source": ev.get("source"),
           "mode": ev.get("mode"), "node_id": ev["node_id"],
           "kind": ev["kind"], "started_at": ev["started_at"],
           "provenance": ev.get("provenance")}
    if ev.get("replay_id"):
        out["replay_id"] = ev["replay_id"]
    return out


def build_fork(events: list[dict], options: dict | None = None, *,
               window: dict | None = None, validate_chain=None,
               validate_sim=None, chain_run_id=None) -> dict:
    """Build the fork. ``validate_chain(spec)`` / ``validate_sim(spec)``
    (optional) return ``None`` when the engine accepts the spec or an
    ``{"code", "message"}`` dict when it refuses it; ``chain_run_id(spec,
    params)`` (optional) returns the deterministic run id POST /run would
    assign."""
    options = options or {}
    if not events:
        raise HistoryError("no events selected to fork", "no_events")
    if len(events) > MAX_EVENTS:
        raise HistoryError(f"at most {MAX_EVENTS} events per fork",
                           "too_many_events")
    chain_opts = options.get("chain") or {}
    sim_opts = options.get("sim") or {}
    if not isinstance(chain_opts, dict) or not isinstance(sim_opts, dict):
        raise HistoryError("options.chain and options.sim must be objects")
    params = chain_opts.get("params") or {}
    if not isinstance(params, dict):
        raise HistoryError("options.chain.params must be an object")
    lead = sim_opts.get("lead_in_days", DEFAULT_LEAD_IN_D)
    tail = sim_opts.get("tail_days", DEFAULT_TAIL_D)
    seed = sim_opts.get("seed", DEFAULT_SEED)
    weather = sim_opts.get("weather", False)
    for name, v, lo in (("lead_in_days", lead, 0), ("tail_days", tail, 0)):
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= 120:
            raise HistoryError(f"options.sim.{name} must be an integer 0..120")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise HistoryError("options.sim.seed must be an integer")
    if not isinstance(weather, bool):
        raise HistoryError("options.sim.weather must be a boolean")

    chain_items = []
    sim_specs = []
    all_notes: list[str] = []
    for ev in events:
        spec, notes = event_to_disruption(ev)
        body = {"source": "scenario", "scenario": {"disruption": spec}}
        if params:
            body["params"] = params
        err = validate_chain(spec) if validate_chain else None
        item = {"event_id": ev["id"], "request": body,
                "validation": ({"ok": True} if not err else
                               {"ok": False, "code": err["code"],
                                "message": err["message"]}),
                "notes": notes}
        if chain_run_id and not err:
            try:
                item["expected_run_id"] = chain_run_id(spec, params)
            except Exception:  # pragma: no cover - id derivation is best-effort
                pass
        chain_items.append(item)
        sim_specs.append(spec)
        all_notes.extend(f"{ev['id']}: {n}" for n in notes)

    starts = sorted(parse_day(s["started_at"]) for s in sim_specs)
    first_start = datetime.fromisoformat(
        min(s["started_at"] for s in sim_specs).replace("Z", "+00:00"))
    start_at = (first_start.replace(hour=0, minute=0, second=0, microsecond=0)
                - timedelta(days=lead))
    ends = []
    for s in sim_specs:
        st = datetime.fromisoformat(s["started_at"].replace("Z", "+00:00"))
        ends.append(st + timedelta(days=float(s["duration_d"])))
    last_end = max(ends)
    span_days = math.ceil((last_end - start_at).total_seconds() / 86400.0) + tail
    ticks = max(1, min(span_days, SIM_MAX_DAY_TICKS))
    sim_notes = []
    if "ticks" in sim_opts:
        t = sim_opts["ticks"]
        if isinstance(t, bool) or not isinstance(t, int) or not 1 <= t <= SIM_MAX_DAY_TICKS:
            raise HistoryError(
                f"options.sim.ticks must be an integer 1..{SIM_MAX_DAY_TICKS} "
                "(day tick; the sim rejects, never clamps)")
        ticks = t
    elif span_days > SIM_MAX_DAY_TICKS:
        sim_notes.append(
            f"the event window plus lead-in and tail is {span_days} days; "
            f"the sim's day-tick maximum is {SIM_MAX_DAY_TICKS}, so the "
            "built run covers the first "
            f"{SIM_MAX_DAY_TICKS} days from start_at. The disruption keeps "
            "its full duration_d (it simply outlasts the horizon).")
    if not weather:
        sim_notes.append(
            "weather is OFF: the sim has no historical weather snapshot, "
            "and enabling it would pin today's forecast to a past date "
            "(same discipline as the sim backtest)")
    else:
        sim_notes.append(
            "weather=true was requested: the sim will pin the CURRENT "
            "forecast, which is NOT the weather of the historical dates; "
            "treat the result as a what-if, not a backplay")
    sim_body = {
        "disruptions": [{k: s[k] for k in SIM_SPEC_KEYS if k in s}
                        for s in sim_specs],
        "tick": "day", "ticks": ticks, "seed": seed,
        "start_at": start_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "weather": weather,
        # vessels sail the land-checked lane polylines, not great-circle
        # chords (58.7% of en-route vessel samples sit on land without it)
        "lane_geometry": True,
    }
    sim_err = None
    if validate_sim:
        for s in sim_body["disruptions"]:
            sim_err = validate_sim(s)
            if sim_err:
                break
    lineage_key = canonical({
        "events": sorted(e["id"] for e in events),
        "chain_params": params, "sim": {"lead": lead, "tail": tail,
                                        "seed": seed, "weather": weather,
                                        "ticks": sim_opts.get("ticks")}})
    forked_from = {
        "kind": "history_fork",
        "lineage_id": "fork_" + sha256_hex(lineage_key)[:16],
        "label": FORK_LABEL,
        "events": [_lineage_event(e) for e in events],
        "window": window or {"from": iso_day(starts[0]),
                             "to": iso_day((last_end).date())},
        "built_at": utcnow_iso(),
        "simulated_outputs_are": "SIMULATED (MODELED engine/sim output); "
                                 "inputs are HISTORICAL (SNAPSHOT/MODELED)",
    }
    return {
        "forked_from": forked_from,
        "chain_requests": chain_items,
        "sim_request": {
            "request": sim_body,
            "validation": ({"ok": True} if not sim_err else
                           {"ok": False, "code": sim_err["code"],
                            "message": sim_err["message"]}),
            "horizon": {"start_at": sim_body["start_at"], "ticks": ticks,
                        "ends_at": (start_at + timedelta(days=ticks)).strftime(
                            "%Y-%m-%dT%H:%M:%SZ"),
                        "lead_in_days": lead},
            "notes": sim_notes,
            "run_with": "POST /sim/run (create-then-poll: GET /sim/run/{id}/status)",
        },
        "notes": all_notes,
        "next": ("POST each chain_requests[].request to /run, POST "
                 "sim_request.request to /sim/run, then GET "
                 "/realism?run=<run_id> to grade the outcome against the "
                 "observed history (BACKTEST)."),
    }
