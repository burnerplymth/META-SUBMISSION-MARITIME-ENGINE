"""Pure ``state(tick)`` over a stored sim run record.

No hidden server state: the record's segment lists + node_series +
pinned weather snapshot fully determine every tick's state. Positions
interpolate along the lane's rendered path (the slerp samples in the
derivation — what is drawn is what moves), rounded to 5 dp, and the
response carries ``state_hash`` = sha256 over the canonical quantized
state, so two processes computing the same tick must produce identical
bytes.

EN_ROUTE entries additionally carry ``dest`` and
``eta_at`` (the arrival segment that ends the current leg — stored
facts, not projections) and ``bearing_deg`` (direction of travel at the
entity's current position along the lane's rendered path, in the
direction of travel). All three are set on the entry before the payload
is hashed, so they are covered by ``state_hash``. Non-EN_ROUTE entries
carry none of them.

Positions interpolate along the
SEGMENT's path — the depart/divert segment's ``path`` id resolves in
the run's path table (derivation ``paths`` + the record's ``aux_paths``
for diversions / hold resumes); a segment without a path falls back to
the lane's rendered path. EN_ROUTE entries on a diverted leg additionally
carry ``diverted: true``.

Air entries additionally carry
``cls`` ("cargo" | "passenger", from the derivation). Entity entries
in BOTH modes carry ``late_min`` whenever the segment record
carries the leg's scheduled arrival (stamped at departure from the
air slot chain; for sea the schedule is the actual
departure + nominal leg duration): projected for EN_ROUTE (stored
or planned arrival vs scheduled arrival), realised for QUEUED /
BERTHED (actual arrival vs scheduled arrival). It is a stored-facts
difference — pre-hash, deterministic. EN_ROUTE entries on a
non-revenue repositioning leg carry ``repositioning: true``.
A record without schedule stamps yields entries with no ``late_min``.

Entity entries additionally carry the
derivation's generated identity (MODELLED) — sea entries gain
``vessel_name`` + ``vessel_class``, air entries gain ``tail`` +
``aircraft_type`` — copied from the derivation entity like ``cls``
and set before the payload hash, so ``state_hash`` covers them.
"""
from __future__ import annotations

try:
    from engine.canonical import canonical_json, sha256_hex
except Exception:  # imported as server.sim.* from the repo root instead
    from server.engine.canonical import canonical_json, sha256_hex  # type: ignore

from . import weather as weather_mod
from .geometry import bearing_deg, point_at_fraction
from .simulate import disruption_windows, fmt_ts, parse_ts

TICK_SECONDS = {"hour": 3600, "day": 86400, "week": 604800, "month": 2592000}
DEFAULT_TICKS = {"hour": 168, "day": 60, "week": 26, "month": 12}
MAX_TICKS = {"hour": 384, "day": 180, "week": 52, "month": 24}

# Fraction of the rendered path stepped ahead of (or, at the path end,
# behind) the current position when sampling the leg bearing.
_BEARING_STEP_FRAC = 0.001


def _leg_bearing(pts: list, frac: float) -> float | None:
    """Bearing of travel at ``frac`` along the directed path ``pts``.

    Samples the current point and a small step ahead in the direction
    of travel; at the very end of the path the step is taken behind
    the current point instead so the bearing still points along the
    direction of travel. ``None`` when the path has no extent.
    """
    if frac >= 1.0:
        a = point_at_fraction(pts, max(0.0, frac - _BEARING_STEP_FRAC))
        b = point_at_fraction(pts, frac)
    else:
        a = point_at_fraction(pts, frac)
        b = point_at_fraction(pts, min(1.0, frac + _BEARING_STEP_FRAC))
    if a == b:
        return None
    return round(bearing_deg(a, b), 1)


def state_at(record: dict, tick: int) -> dict:
    journal = record["journal"]
    tick_s = TICK_SECONDS[journal["tick"]]
    start_epoch = parse_ts(journal["start_at"])
    tick_time = start_epoch + tick * tick_s
    lanes = {ln["key"]: ln for ln in record["derivation"]["lanes"]}
    # Path table: derivation transit paths + the record's aux
    # paths (diversions / hold resumes), one id space.
    path_table: dict = {}
    path_table.update(record["derivation"].get("paths") or {})
    path_table.update(record.get("aux_paths") or {})

    def segment_points(ev: dict, lane: dict) -> list:
        """The directed polyline a depart/divert segment sails: its
        stored path (reversed when the segment says so), else the
        fallback — the lane's rendered path in travel
        direction."""
        pid = ev.get("path")
        entry = path_table.get(pid) if pid else None
        if entry is not None:
            pts = [tuple(p) for p in entry["points"]]
            return list(reversed(pts)) if ev.get("path_rev") else pts
        base = [tuple(p) for p in lane["path"]]
        return base if ev["node"] == lane["from"] else list(reversed(base))

    # Lane geometry runs (opt-in): a leg whose arrival lies past the stored horizon has no arrive segment;
    # the vessel is then placed by its planned arrival (a projection, so no eta_at is served for it)
    # instead of at the end of the lane. Records without the journal key use that placement.
    lane_mode = bool(journal.get("lane_geometry"))
    segments = record["segments"]
    entities_out: list[dict] = []
    for ent in record["derivation"]["entities"]:
        eid = ent["id"]
        evs = segments.get(eid, [])
        if not evs or evs[0]["t"] > tick_time:
            continue  # not yet created at this tick
        lane = lanes[ent["lane"]]
        state = None
        node = None
        lon = lat = None
        queue_wait_s = 0.0
        queued_since = None
        # Current-leg facts for EN_ROUTE entries: destination
        # node + arrival time of the arrival segment ending the leg,
        # and the directed rendered path + fraction for the bearing.
        leg_dest = leg_eta = leg_pts = leg_frac = None
        leg_diverted = False
        # Lateness tracking (stored facts only): the current
        # leg's scheduled arrival (stamped on its depart/divert
        # segment), the projected lateness while EN_ROUTE, and the
        # realised lateness once the leg has arrived.
        sched_arr = None
        enroute_late_min = None
        ground_late_min = None
        leg_repositioning = False
        for i, ev in enumerate(evs):
            if ev["t"] > tick_time:
                break
            if ev["kind"] in ("depart", "divert"):
                # An anchor event: the leg (re)starts here, on this
                # segment's path. The leg ends at the NEXT arrival
                # segment, skipping any divert anchors in between
                # (they re-anchor the same leg when their turn comes).
                if ev.get("sched_arr") is not None:
                    sched_arr = ev["sched_arr"]
                leg_repositioning = ev["kind"] == "depart" and \
                    bool(ev.get("repositioning"))
                nxt = next((e for e in evs[i + 1:] if e["kind"] == "arrive"),
                           None)
                if nxt is not None and nxt["t"] > tick_time:
                    frac = (tick_time - ev["t"]) / (nxt["t"] - ev["t"]) \
                        if nxt["t"] > ev["t"] else 1.0
                    pts = segment_points(ev, lane)
                    lat, lon = point_at_fraction(pts, frac)
                    state, node = "EN_ROUTE", None
                    leg_dest, leg_eta = nxt["node"], nxt["t"]
                    leg_pts, leg_frac = pts, frac
                    # A depart anchors a fresh (undiverted) leg; a
                    # divert anchor means the leg is diverted from
                    # here to its arrival.
                    leg_diverted = ev["kind"] == "divert"
                    enroute_late_min = (nxt["t"] - sched_arr) / 60.0 \
                        if sched_arr is not None else None
                elif nxt is not None:
                    continue  # arrival processed in its own iteration
                else:  # leg still open past the record (horizon edge)
                    pts = segment_points(ev, lane)
                    planned_open = ev.get("planned_arr")
                    if lane_mode and planned_open is not None \
                            and planned_open > max(ev["t"], tick_time):
                        open_frac = (tick_time - ev["t"]) / (planned_open - ev["t"])
                        lat, lon = point_at_fraction(pts, open_frac)
                    else:
                        open_frac = 1.0
                        lat, lon = point_at_fraction(pts, 1.0)
                    state, node = "EN_ROUTE", None
                    # No arrival segment is stored for this leg, so its
                    # destination is the depart segment's peer and there
                    # is no stored arrival time to report as eta_at.
                    leg_dest, leg_eta = ev.get("peer"), None
                    leg_pts, leg_frac = pts, open_frac
                    leg_diverted = ev["kind"] == "divert"
                    planned = ev.get("planned_arr")
                    enroute_late_min = (planned - sched_arr) / 60.0 \
                        if planned is not None and sched_arr is not None \
                        else None
            elif ev["kind"] == "arrive":
                state, node = "QUEUED", ev["node"]
                queued_since = ev["t"]
                lat = lon = None
                leg_dest = leg_eta = leg_pts = leg_frac = None
                leg_diverted = False
                leg_repositioning = False
                enroute_late_min = None
                arr_sched = ev.get("sched_arr")
                if arr_sched is None:
                    arr_sched = sched_arr
                ground_late_min = (ev["t"] - arr_sched) / 60.0 \
                    if arr_sched is not None else None
            elif ev["kind"] == "berth":
                if queued_since is not None:
                    queue_wait_s += ev["t"] - queued_since
                    queued_since = None
                state, node = "BERTHED", ev["node"]
                leg_dest = leg_eta = leg_pts = leg_frac = None
                leg_diverted = False
                leg_repositioning = False
            elif ev["kind"] == "dispatch":
                state, node = "IN_PORT", ev["node"]
                leg_dest = leg_eta = leg_pts = leg_frac = None
                leg_diverted = False
                leg_repositioning = False
                ground_late_min = None
        if state is None:
            continue
        if queued_since is not None:
            queue_wait_s += tick_time - queued_since
        entry: dict = {"id": eid, "mode": ent["mode"], "lane": ent["lane"],
                       "state": state}
        # Air entities carry their class.
        if ent.get("cls"):
            entry["cls"] = ent["cls"]
        # Generated identity (MODELLED) rides the entry
        # exactly like cls — copied from the derivation entity and set
        # before the payload hash below, so state_hash covers it
        # (additive). Records without identity fields yield entries
        # that simply omit them.
        if ent.get("vessel_name"):
            entry["vessel_name"] = ent["vessel_name"]
            entry["vessel_class"] = ent["vessel_class"]
        if ent.get("tail"):
            entry["tail"] = ent["tail"]
            entry["aircraft_type"] = ent["aircraft_type"]
        # Rotation lateness in minutes — projected
        # while EN_ROUTE, realised while QUEUED/BERTHED.
        if state == "EN_ROUTE" and enroute_late_min is not None:
            entry["late_min"] = round(enroute_late_min, 1)
        elif state in ("QUEUED", "BERTHED") and ground_late_min is not None:
            entry["late_min"] = round(ground_late_min, 1)
        if state == "EN_ROUTE" and leg_repositioning:
            entry["repositioning"] = True
        if node is not None:
            entry["node"] = node
            pos = _node_position(record, node, lane)
            lat, lon = pos
        entry["lon"] = round(float(lon), 5) if lon is not None else None
        entry["lat"] = round(float(lat), 5) if lat is not None else None
        if state == "EN_ROUTE" and leg_dest is not None:
            # Additive fields, set before the payload (and thus
            # state_hash) is built below — EN_ROUTE entries only.
            entry["dest"] = leg_dest
            if leg_eta is not None:
                entry["eta_at"] = fmt_ts(leg_eta)
            if leg_pts is not None and leg_frac is not None:
                bearing = _leg_bearing(leg_pts, leg_frac)
                if bearing is not None:
                    entry["bearing_deg"] = bearing
            # The entity is sailing a diverted leg.
            if leg_diverted:
                entry["diverted"] = True
        if state == "QUEUED" or queue_wait_s > 0:
            entry["queue_wait_h"] = round(queue_wait_s / 3600.0, 6)
        entities_out.append(entry)
    entities_out.sort(key=lambda e: e["id"])

    snapshot = record.get("weather")
    nodes_out: list[dict] = []
    for nid in sorted(record["node_series"]):
        s = record["node_series"][nid]
        nentry: dict = {"id": nid, "queue": s["queue"][tick],
                        "berthed": s["berthed"][tick],
                        "arrived": s["arrivals"][tick],
                        "departed": s["departures"][tick]}
        w = weather_mod.node_weather_at(snapshot, nid, tick_time)
        if w is not None:
            entry_w = (snapshot.get("nodes") or {}).get(nid) or {}
            idx = weather_mod.hour_index(entry_w.get("times", []), tick_time)
            nentry["weather"] = {
                "wind_kn": w["wind_kn"], "wave_m": w["wave_m"],
                "valid_at": entry_w["times"][idx] if idx is not None else None,
                "source": snapshot.get("source", "Open-Meteo"),
            }
        nodes_out.append(nentry)

    windows = disruption_windows(journal["disruptions"], start_epoch)
    active = [w["sim_id"] for w in windows
              if w["start"] <= tick_time < w["end"] and w["sim_id"]]
    # SIM-native shock windows are active on the
    # same [start, start + duration) rule and surface in the same
    # active set (additive ids a1…/s1… in the journal).
    for key in ("airspace", "storm_cells"):
        for ent in journal.get(key) or []:
            wstart = parse_ts(ent["started_at"]) if ent.get("started_at") \
                else start_epoch
            if wstart <= tick_time < wstart + float(ent["duration_d"]) \
                    * 86400.0 and ent.get("sim_id"):
                active.append(ent["sim_id"])
    payload: dict = {
        "sim_run_id": record["sim_run_id"], "tick": tick,
        "sim_time": fmt_ts(tick_time),
        "entities": entities_out, "nodes": nodes_out,
        "disruptions_active": active,
    }
    # When the pinned snapshot carries the storm-cell field,
    # the payload gains the cell display set for the tick's step
    # (weather.py's cells_at_step — bboxes + graded samples only; no
    # cell identity crosses into the payload). Both keys are ABSENT
    # for weather-less and point-only runs, whose payloads are
    # unchanged. Set before the hash,
    # so state_hash covers them like every other payload field.
    if snapshot is not None and snapshot.get("cells"):
        cells_view = weather_mod.cells_at_step(snapshot, tick_time)
        payload["weather_cells"] = cells_view["cells"]
        payload["weather_step"] = cells_view["step"]
    payload["state_hash"] = sha256_hex(canonical_json(payload))
    return payload


def _node_position(record: dict, node_id: str, lane: dict) -> tuple[float, float]:
    """A node's coordinates, read off the lane path endpoints (the
    record is self-contained: paths carry endpoint coordinates)."""
    # Prefer the dedicated node index stored on the record.
    idx = record.get("node_positions") or {}
    if node_id in idx:
        return float(idx[node_id][0]), float(idx[node_id][1])
    if lane["from"] == node_id:
        p = lane["path"][0]
        return float(p[0]), float(p[1])
    p = lane["path"][-1]
    return float(p[0]), float(p[1])
