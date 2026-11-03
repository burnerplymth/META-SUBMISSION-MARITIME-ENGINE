"""Event-segment simulator.

Continuous-time, event-driven: a tick is only a sampling/accounting
step over the segment record, never the integration step — so arrival
times do not depend on tick size (hour-based and day-based ticks give
the same arrivals). Randomness is exhausted by derivation (schedules +
dwells); the simulation itself is a deterministic function of
(derivation, disruption windows, pinned weather snapshot).

Per entity: IN_PORT (spawn) -> EN_ROUTE -> QUEUED (FIFO per node,
entity-id tie-break) -> BERTHED (dwell) -> dispatch -> next leg.
Routing is ping-pong on the spawn lane, but each leg sails the lane's
TRANSIT PATH (the per-run path table in the derivation): the
movement polyline is the concatenation of the transit legs' rendered
paths, and the leg exposes the vessel to the chokepoints on that
polyline (decision points at departure and at each transit
chokepoint's nominal arrival):

- closure at a transit chokepoint -> WAIT-VS-DIVERT: a bypass is a route
  that never passes through the closed place — turn back to the last
  route node before it, then the shortest path to the destination
  avoiding all currently-closed places; qualifies at <= 2.0x the
  leg's nominal great-circle length. The leg HOLDS when the
  closure's expected remaining duration (declared expectation;
  +inf for an open-ended closure) is no longer than the diversion's
  added time over the leg's remaining nominal length, and DIVERTS
  when the expected wait is longer and a bypass qualifies; HOLD
  queues at the chokepoint approach with FIFO clearance via the
  chokepoint's berth machinery when the window ends;
- partial loss (0 < L < 1) at a transit or destination place -> the
  approach portion is slowed by (1 - PARTIAL_APPROACH_COEF * L)
  (MODELLED constant below), on top of berth scaling;
- closure at destination -> queue on arrival (unchanged);
- closure at origin -> departures held (unchanged).

Departure coupling: a berthed entity's dispatch pushes its next
departure at the berth-completion instant, so next departure =
berth completion (dwell included) whenever that is later than any
schedule notion; the spawn schedule only ever sets an entity's FIRST
departure. Queue delay therefore propagates leg to leg.

Berths per node: max(1, round(queue_cap/40)) sea /
max(1, round(queue_cap/400)) air; effective berths scale by
(1 - capacity_loss) for disruption windows
[started_at, started_at + duration_d) — closure (loss coerced to 1.0
upstream) means zero berths and held
departures: arrivals queue unbounded, and the queue is a headline
output.
"""
from __future__ import annotations

import gc
import heapq
import itertools
import math
import re
import threading
from datetime import datetime, timezone

from . import weather as weather_mod
from .derive import (
    AIR_DIVERT_HOLD_S, AIR_DIVERT_RANGE_KM, STORM_CELL_WAVE_M,
    STORM_CELL_WIND_KN, _dijkstra, air_nominal_turnaround_h,
    lane_key, movement_geometry, next_bank_epoch, next_cargo_wave_epoch,
)
from .geometry import detour_around_circle, gc_distance_km, \
    path_length_km, slerp_samples

# --- Weather factor config --------
# Sea speed: f = clamp(1 - SEA_WAVE_COEF*max(0, wave_m - SEA_WAVE_BASE)
#                        - SEA_WIND_COEF*max(0, wind_kn - SEA_WIND_BASE),
#                      SEA_FACTOR_FLOOR, 1.0)
# Air speed: f = clamp(1 - headwind_kn / AIR_WIND_DIVISOR,
#                      AIR_FACTOR_FLOOR, AIR_FACTOR_CEIL)
# Port slowdown: sea node wind > SEA_SLOW_WIND_KN or wave >
# SEA_SLOW_WAVE_M -> berth service rate x SEA_SLOW_SERVICE_RATE and
# departures held; air node wind > AIR_SLOW_WIND_KN -> dwell x
# AIR_SLOW_DWELL_MULT.
SEA_WAVE_COEF = 0.06
SEA_WAVE_BASE_M = 2.0
SEA_WIND_COEF = 0.004
SEA_WIND_BASE_KN = 34.0
SEA_FACTOR_FLOOR = 0.55
AIR_WIND_DIVISOR = 900.0
AIR_FACTOR_FLOOR = 0.85
AIR_FACTOR_CEIL = 1.10
SEA_SLOW_WIND_KN = 40.0
SEA_SLOW_WAVE_M = 3.5
SEA_SLOW_SERVICE_RATE = 0.5
AIR_SLOW_WIND_KN = 45.0
AIR_SLOW_DWELL_MULT = 1.5
MAX_DEPARTURE_HOLD_S = 72.0 * 3600.0

# --- Reaction config -----------
# Partial loss L at a transit/destination place slows the approach
# portion by (1 - PARTIAL_APPROACH_COEF * L).
PARTIAL_APPROACH_COEF = 0.5
# A diversion qualifies when its great-circle length is at most
# DIVERT_MAX_RATIO x the leg's nominal transit path length.
DIVERT_MAX_RATIO = 2.0

BERTH_DIVISOR = {"sea": 40.0, "air": 400.0}

_PRI = {"dispatch": 0, "transit": 1, "arrive": 2, "divertcheck": 2,
        "depart": 3, "recheck": 4}


def parse_ts(text: str) -> float:
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def fmt_ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def berth_count(node: dict) -> int:
    div = BERTH_DIVISOR.get(node["mode"])
    if div is None:
        return 0
    if node.get("queue_cap") is None:
        # queue_cap absent -> berths default
        # to 1 (sea) / 1 (air) — never a crash, never an invented
        # capacity. (The shipped graph serves queue_cap on every
        # place; this binds fixtures and future graph revisions.)
        return 1
    return max(1, round(float(node["queue_cap"]) / div))


def disruption_windows(disruptions: list[dict], start_epoch: float) -> list[dict]:
    """Effective specs -> [{node_id, start, end, loss}] (epochs).

    Each window also carries ``expected_end`` — the
    fleet's expected reopening instant used by the wait-vs-divert
    rule. A spec may declare ``expected_duration_d``: a number sets
    the expectation at (the spec's first window start + that many
    days); an explicit null declares the closure OPEN-ENDED
    (expected_end = +inf); when the field is absent the expectation
    is the window's own declared end.

    A spec may carry ``steps`` — a list of
    {started_at, duration_d, capacity_loss} — instead of the flat
    (duration_d, capacity_loss) pair; each step expands to a window
    with the flat form's exact semantics (a spec with ``steps``
    ignores the flat fields). Any window may declare
    ``recovery_ramp_d`` (carried as ``ramp_s``): after the window's
    end its loss contribution decays linearly to zero over the ramp
    (see _active_loss). Default 0 = off.

    A spec may carry ``known_from`` (ISO) - the
    instant the fleet LEARNS of the window. It is copied onto the
    window as epoch ``known_from`` and gates the planning
    look-ahead only (see _active_loss ``known_at``, plan_leg). Absent
    = no gate."""
    out = []
    for spec in disruptions:
        raw = []  # (start, duration_d, loss, ramp_d)
        steps = spec.get("steps")
        if steps:
            for step in steps:
                st = step.get("started_at")
                raw.append((parse_ts(st) if st else start_epoch,
                            float(step["duration_d"]),
                            float(step["capacity_loss"]),
                            float(step.get("recovery_ramp_d") or 0.0)))
        else:
            st = spec.get("started_at")
            raw.append((parse_ts(st) if st else start_epoch,
                        float(spec["duration_d"]),
                        float(spec["capacity_loss"]),
                        float(spec.get("recovery_ramp_d") or 0.0)))
        if "expected_duration_d" in spec:
            exp = spec["expected_duration_d"]
            expected_end = math.inf if exp is None else \
                min(r[0] for r in raw) + float(exp) * 86400.0
        else:
            expected_end = None  # per-window default, set below
        for wstart, dur, loss, ramp_d in raw:
            w = {"node_id": spec["node_id"], "sim_id": spec.get("sim_id"),
                 "start": wstart, "end": wstart + dur * 86400.0,
                 "loss": loss, "ramp_s": ramp_d * 86400.0,
                 "expected_end": expected_end}
            if w["expected_end"] is None:
                w["expected_end"] = w["end"]
            if spec.get("known_from") is not None:
                w["known_from"] = parse_ts(spec["known_from"])
            out.append(w)
    return out


def _active_loss(windows: list[dict], node_id: str, t: float,
                 known_at: float | None = None) -> float:
    """Capacity loss at ``node_id`` at instant ``t``. ``known_at``
    (planning look-ahead only): windows whose ``known_from`` lies
    after that instant are invisible - a plan made before a shock is
    announced cannot price it in. None = no gating (every event-time
    evaluation, and every window without ``known_from``)."""
    loss = 0.0
    for w in windows:
        if w["node_id"] != node_id:
            continue
        if known_at is not None and w.get("known_from", -math.inf) > known_at:
            continue
        if w["start"] <= t < w["end"]:
            loss = max(loss, w["loss"])
        else:
            ramp = w.get("ramp_s") or 0.0
            if ramp > 0.0 and w["end"] <= t < w["end"] + ramp:
                loss = max(loss,
                           w["loss"] * (1.0 - (t - w["end"]) / ramp))
    return loss


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def sea_speed_factor(w: dict | None) -> float:
    if not w:
        return 1.0
    wave = w.get("wave_m") or 0.0
    wind = w.get("wind_kn") or 0.0
    return _clamp(1.0 - SEA_WAVE_COEF * max(0.0, wave - SEA_WAVE_BASE_M)
                  - SEA_WIND_COEF * max(0.0, wind - SEA_WIND_BASE_KN),
                  SEA_FACTOR_FLOOR, 1.0)


def air_speed_factor(w: dict | None, bearing: float) -> float:
    if not w or w.get("wind_kn") is None or w.get("wind_dir") is None:
        return 1.0
    # wind_dir is the direction the wind blows FROM; the tailwind
    # component along the leg bearing flips its sign into headwind.
    tailwind = float(w["wind_kn"]) * math.cos(
        math.radians(float(w["wind_dir"]) + 180.0 - bearing))
    headwind = -tailwind
    return _clamp(1.0 - headwind / AIR_WIND_DIVISOR,
                  AIR_FACTOR_FLOOR, AIR_FACTOR_CEIL)


def _port_slowdown(snapshot, node_id: str, mode: str, t: float,
                   storm: bool = False) -> bool:
    # Storm cell over the place: the cell's stated conditions
    # (wind 50 kn, wave 6 m) trip the same slowdown thresholds.
    if storm:
        if mode == "sea":
            return STORM_CELL_WIND_KN > SEA_SLOW_WIND_KN or \
                STORM_CELL_WAVE_M > SEA_SLOW_WAVE_M
        if mode == "air":
            return STORM_CELL_WIND_KN > AIR_SLOW_WIND_KN
        return False
    w = weather_mod.node_weather_at(snapshot, node_id, t)
    if not w:
        return False
    if mode == "sea":
        return (w.get("wind_kn") or 0.0) > SEA_SLOW_WIND_KN or \
               (w.get("wave_m") or 0.0) > SEA_SLOW_WAVE_M
    if mode == "air":
        return (w.get("wind_kn") or 0.0) > AIR_SLOW_WIND_KN
    return False


_HOUR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}")


def _snapshot_hour_buckets(snapshot) -> tuple | None:
    """(first, last) UTC hour bucket (epoch // 3600) spanned by any
    point series of the snapshot, or None when it cannot be read as
    plain ISO hours. weather.hour_index answers None for an hour
    outside a series' [first, last] hour, so an hour outside the span
    of ALL series has no weather anywhere."""
    if not snapshot:
        return None
    lists = []
    for entry in (snapshot.get("nodes") or {}).values():
        lists.append(entry.get("times"))
        lists.append(entry.get("wave_times"))
    for entry in (snapshot.get("lane_midpoints") or {}).values():
        lists.append(entry.get("times"))
    firsts, lasts = [], []
    for times in lists:
        if not times:
            continue
        firsts.append(times[0][:13])
        lasts.append(times[-1][:13])
    if not firsts:
        return None
    lo, hi = min(firsts), max(lasts)
    if not (_HOUR_RE.match(lo) and _HOUR_RE.match(hi)):
        return None
    try:
        def bucket(text):
            return int(datetime.strptime(text, "%Y-%m-%dT%H").replace(
                tzinfo=timezone.utc).timestamp() // 3600)
        return bucket(lo), bucket(hi)
    except ValueError:
        return None


_GC_LOCK = threading.Lock()
_GC_DEPTH = [0]
_GC_WAS_ENABLED = [False]


class _gc_paused:
    """Pause the cyclic collector for the duration of a simulation.

    A simulation allocates millions of small
    dicts and tuples that stay alive until it returns; every gen-2
    collection walks all of them and finds nothing to free (measured
    about 20% of the CPU of a build). Pausing the collector changes no
    value. Re-entrant and thread-safe (the API runs two builds on
    worker threads): the collector is re-enabled when the last
    simulation in the process finishes, and only if it was enabled
    when the first one started."""

    def __enter__(self):
        with _GC_LOCK:
            if _GC_DEPTH[0] == 0:
                _GC_WAS_ENABLED[0] = gc.isenabled()
                gc.disable()
            _GC_DEPTH[0] += 1
        return self

    def __exit__(self, *exc):
        with _GC_LOCK:
            _GC_DEPTH[0] -= 1
            if _GC_DEPTH[0] == 0 and _GC_WAS_ENABLED[0]:
                gc.enable()
        return False


class _NullEvents:
    """Stand-in for a node's event list when the node series is not
    wanted (``light`` runs): accepts appends, keeps nothing."""
    __slots__ = ()

    def append(self, _item) -> None:
        return None


_NULL_EVENTS = _NullEvents()


def simulate(graph_data: dict, derivation: dict, disruptions: list[dict],
             snapshot: dict | None, start_epoch: float,
             tick_s: int, ticks: int, airspace: list[dict] | None = None,
             storm_cells: list[dict] | None = None,
             trim_before: float | None = None,
             light: bool = False) -> dict:
    """Run the event simulation. Returns {segments, node_series,
    arrivals, arrival_events, aux_paths} — segments per entity (event
    lists), node_series per node per tick, per-entity arrival epochs,
    per-entity arrival events (node + hold flag, for twin-delay
    accounting), and the run's auxiliary path table (diversion and
    hold-resume polylines the segment record references by id).

    ``airspace`` / ``storm_cells`` are the SIM-native
    shock constructs (validated by the /sim/run route; the /run
    contract never sees them). Airspace entries {anchor, radius_km,
    started_at, duration_d, sim_id}: places inside the circle are
    closed for the window (synthetic closure windows, all modes) and
    air legs intersecting the circle reroute around it. Storm cells
    {from_node, to_node, radius_km, started_at, duration_d, sim_id}:
    the centre travels the great circle from start place to end place
    across the window; places/legs within radius at time t take the
    weather factors at the cell's stated conditions.

    Both of the following are optional and off by default:

    ``trim_before`` (epoch): store only what ``runner._trim_segments``
    would keep of each entity's pre-``trim_before`` events (the open
    episode at that instant and everything after), discarding the
    spin-up as it is produced instead of afterwards. The node series
    is still computed from every event. ``_trim_segments`` applied to
    the untrimmed result gives exactly this result.

    ``light``: the caller wants only ``arrival_events`` / ``arrivals``
    (the undisrupted twin). Segment records, node events and the node
    series are not built (``segments`` holds empty lists,
    ``node_series`` is empty); every simulated time is unchanged."""
    with _gc_paused():
        return _simulate(graph_data, derivation, disruptions, snapshot,
                         start_epoch, tick_s, ticks, airspace,
                         storm_cells, trim_before, light)


def _simulate(graph_data, derivation, disruptions, snapshot, start_epoch,
              tick_s, ticks, airspace, storm_cells, trim_before, light):
    from .geometry import bearing_deg

    nodes = {n["id"]: n for n in graph_data["nodes"]}
    lanes = {ln["key"]: ln for ln in derivation["lanes"]}
    paths_table = derivation.get("paths") or {}
    # lane geometry mode (opt-in, journaled): sea lanes without a land-checked polyline carry no vessel and
    # are never a transit, a bypass or a substitute; a diversion's turn-back runs along the lane polyline
    lane_mode = bool(derivation.get("lane_geometry"))

    def _no_lane_geometry(ln: dict) -> bool:
        return lane_mode and ln["mode"] == "sea" and \
            (ln.get("lane_geometry") or {}).get("status") != "lane"
    windows = disruption_windows(disruptions, start_epoch)
    horizon_end = start_epoch + tick_s * ticks
    # Air module state: integrator hubs (from derivation) drive
    # cargo turnarounds + night-sort slot quantisation.
    integrator_set = set((derivation.get("air") or {}).get(
        "integrator_hubs") or [])
    # SIM-native constructs, resolved once (deterministic).
    airspace_zones: list[dict] = []
    for ent in airspace or []:
        a_start = parse_ts(ent["started_at"]) if ent.get("started_at") \
            else start_epoch
        anchor = ent["anchor"]
        zone = {"anchor": anchor,
                "center": (float(nodes[anchor]["lat"]),
                           float(nodes[anchor]["lon"])),
                "radius_km": float(ent["radius_km"]),
                "start": a_start,
                "end": a_start + float(ent["duration_d"]) * 86400.0,
                "sim_id": ent.get("sim_id")}
        if ent.get("known_from") is not None:
            zone["known_from"] = parse_ts(ent["known_from"])
        airspace_zones.append(zone)
        # Places inside the circle are closed for the window —
        # synthetic closure windows through the same machinery as
        # served disruptions (berths 0, departures held, arrivals
        # queue/divert per mode).
        for nid, n in nodes.items():
            if gc_distance_km(zone["center"],
                              (float(n["lat"]), float(n["lon"]))) \
                    <= zone["radius_km"]:
                aw = {"node_id": nid, "sim_id": zone["sim_id"],
                      "start": zone["start"], "end": zone["end"],
                      "loss": 1.0, "airspace": True}
                if "known_from" in zone:
                    aw["known_from"] = zone["known_from"]
                windows.append(aw)
    storm_tracks: list[dict] = []

    def _storm_center(cell: dict, t: float) -> tuple:
        frac = 0.0 if cell["end"] <= cell["start"] else max(
            0.0, min(1.0, (t - cell["start"]) / (cell["end"] - cell["start"])))
        from .geometry import point_at_fraction
        return point_at_fraction(cell["track_pts"], frac)

    for ent in storm_cells or []:
        c_start = parse_ts(ent["started_at"]) if ent.get("started_at") \
            else start_epoch
        a = (float(nodes[ent["from_node"]]["lat"]),
             float(nodes[ent["from_node"]]["lon"]))
        b = (float(nodes[ent["to_node"]]["lat"]),
             float(nodes[ent["to_node"]]["lon"]))
        track = {
            "from": ent["from_node"], "to": ent["to_node"],
            "radius_km": float(ent["radius_km"]),
            "start": c_start,
            "end": c_start + float(ent["duration_d"]) * 86400.0,
            "sim_id": ent.get("sim_id"), "track_pts": slerp_samples(a, b)}
        if ent.get("known_from") is not None:
            track["known_from"] = parse_ts(ent["known_from"])
        storm_tracks.append(track)

    def _storm_at_node(node_id: str, t: float) -> bool:
        """True when a storm cell covers the place at time t."""
        for cell in storm_tracks:
            if cell["start"] <= t < cell["end"]:
                if gc_distance_km(_storm_center(cell, t),
                                  node_coords[node_id]) \
                        <= cell["radius_km"]:
                    return True
        return False
    base_berths = {nid: berth_count(n) for nid, n in nodes.items()}
    node_coords = {nid: (float(n["lat"]), float(n["lon"]))
                   for nid, n in nodes.items()}
    chokepoints_by_mode: dict[str, set] = {}
    for n in graph_data["nodes"]:
        if n.get("kind") == "chokepoint":
            chokepoints_by_mode.setdefault(n["mode"], set()).add(n["id"])
    def _turnback_pts(a: str, b: str, mode: str) -> list:
        """The polyline of the turn-back from a to b (a diversion at a closed approach). Chord arc as
        ever; in lane geometry mode the lane polyline between the two places when one exists."""
        if lane_mode and mode == "sea":
            fwd = lanes.get(lane_key("sea", a, b))
            if fwd is not None and (fwd.get("lane_geometry") or {}).get("status") == "lane":
                return [tuple(q) for q in fwd["path"]]
            rev = lanes.get(lane_key("sea", b, a))
            if rev is not None and (rev.get("lane_geometry") or {}).get("status") == "lane":
                return [tuple(q) for q in reversed(rev["path"])]
        return slerp_samples(node_coords[a], node_coords[b])

    # Diversion search graph: directed legs weighted by great-circle
    # distance (the lanes' direct distances), per mode.
    divert_adj: dict[str, dict] = {}
    for ln in derivation["lanes"]:
        if ln["flow_only"]:
            continue
        # Services layer (opt-in): a CREATED service lane is a through
        # service leg, not a bypass link; its weight here would be its
        # great-circle length although it sails the Suez or Cape route.
        if (ln.get("service") or {}).get("created"):
            continue
        if _no_lane_geometry(ln):
            continue
        divert_adj.setdefault(ln["mode"], {}).setdefault(ln["from"], []).append(
            (ln["to"], float(ln["direct_distance_km"])))
    for mode in divert_adj:
        for frm in divert_adj[mode]:
            divert_adj[mode][frm] = sorted(set(divert_adj[mode][frm]))
    # Bypass certification: a lane may serve on a bypass
    # only if its own transit path does not transit any
    # currently-closed place (the sim's own exposure record for that
    # lane — a lane routed through the closure is not an alternative
    # to it). Filtered adjacencies are cached per (mode, closed set);
    # the closed set changes only at window edges, so few are built.
    lane_transit_nodes = {key: {t["node"] for t in p.get("transits", [])}
                          for key, p in paths_table.items()}
    _adj_cache: dict = {}

    def search_adj(mode: str, closed: set) -> dict:
        ck = (mode, frozenset(closed))
        adj = _adj_cache.get(ck)
        if adj is None:
            adj = {}
            for frm, lst in divert_adj.get(mode, {}).items():
                keep = [(to, w) for (to, w) in lst
                        if not (lane_transit_nodes.get(
                            lane_key(mode, frm, to), set()) & closed)]
                if keep:
                    adj[frm] = keep
            _adj_cache[ck] = adj
        return adj

    # Call reassignment: a sea spec may name substitute
    # places with shares of the closed place's lanes; plans are built
    # once here: {closed_node: {"span": (t0, t1), "lane_map":
    # {lane_key: substitute}}}. Lane j of n (canonical key order) is
    # assigned by its quantile (j + 0.5)/n against the substitutes'
    # cumulative shares; lanes beyond the total share keep sailing
    # to the closed place. Accounting (departures, landed calls,
    # modelled tonnage) accumulates on the run output.
    reassign_plans: dict[str, dict] = {}
    reassignment_accounting: dict[str, dict] = {}
    for spec in disruptions:
        ra = spec.get("reassign")
        x_node = spec.get("node_id")
        if not ra or x_node not in nodes \
                or nodes[x_node].get("mode") != "sea":
            continue
        spec_windows = [w for w in windows if w["node_id"] == x_node
                        and w["sim_id"] == spec.get("sim_id")]
        if not spec_windows:
            continue
        span = (min(w["start"] for w in spec_windows),
                max(w["end"] for w in spec_windows))
        cands = sorted((ln for ln in derivation["lanes"]
                        if ln["mode"] == "sea" and not ln["flow_only"]
                        and x_node in (ln["from"], ln["to"])),
                       key=lambda ln: ln["key"])
        subs = ra.get("substitutes") or []
        lane_map: dict[str, str] = {}
        if cands:
            cum = 0.0
            for sub in subs:
                lo, hi = cum, cum + float(sub["share"])
                for j, ln in enumerate(cands):
                    if lo <= (j + 0.5) / len(cands) < hi:
                        lane_map[ln["key"]] = sub["node_id"]
                cum = hi
        reassign_plans[x_node] = {"span": span, "lane_map": lane_map}
        reassignment_accounting[x_node] = {
            "span": [span[0], span[1]],
            "lanes_total": len(cands),
            "lanes_reassigned": len(lane_map),
            "substitutes": {sub["node_id"]: {"departures": 0,
                                              "calls": 0,
                                              "volume": 0.0}
                            for sub in subs}}

    # --- Output-identical fast paths ---------------------------------
    # Windows indexed by place: ``active_loss`` is _active_loss over
    # exactly the windows of one place, in list order (the other
    # windows are skipped by its first test), so it returns the same
    # float; a place with no window answers 0.0 without a scan.
    _wby: dict[str, list] = {}
    for _w in windows:
        _wby.setdefault(_w["node_id"], []).append(_w)
    _windowed_nodes = [nid for nid in nodes if nid in _wby]

    def active_loss(node_id: str, t: float,
                    known_at: float | None = None) -> float:
        ws = _wby.get(node_id)
        if ws is None:
            return 0.0
        loss = 0.0
        for w in ws:
            if known_at is not None and \
                    w.get("known_from", -math.inf) > known_at:
                continue
            if w["start"] <= t < w["end"]:
                loss = max(loss, w["loss"])
            else:
                ramp = w.get("ramp_s") or 0.0
                if ramp > 0.0 and w["end"] <= t < w["end"] + ramp:
                    loss = max(loss,
                               w["loss"] * (1.0 - (t - w["end"]) / ramp))
        return loss

    def closed_places(t: float) -> set:
        """{places with effective loss >= 1.0 at t}, inserted in the
        graph's node order (a place with no window never qualifies)."""
        return {nid for nid in _windowed_nodes
                if active_loss(nid, t) >= 1.0}

    # Weather lookups are pure functions of (place or lane, UTC hour):
    # memoised per hour bucket. A time within a millisecond of an hour
    # boundary skips the memo (datetime.fromtimestamp rounds to the
    # microsecond there), so the memo can never answer for a different
    # hour than the direct call would.
    _MISS = object()
    _wx_on = bool(snapshot)
    _lane_wx_memo: dict = {}
    _slow_memo: dict = {}
    _leg_memo: dict = {}
    _stable_points: set = set()
    _plan_memo: dict = {}
    _storm_slow = {
        "sea": STORM_CELL_WIND_KN > SEA_SLOW_WIND_KN
        or STORM_CELL_WAVE_M > SEA_SLOW_WAVE_M,
        "air": STORM_CELL_WIND_KN > AIR_SLOW_WIND_KN}
    _cell_step_s = weather_mod.CELL_STEP_S
    _floor = math.floor

    def _hour_bucket(t: float):
        fl = _floor(t / 3600.0)
        r = t - fl * 3600.0
        if r < 0.001 or r > 3599.999:
            return None
        return fl

    _span = _snapshot_hour_buckets(snapshot) if _wx_on else None

    def lane_wx(lane_key: str, node_id: str, t: float):
        if not _wx_on:
            return None
        hb = _hour_bucket(t)
        if hb is None:
            return weather_mod.lane_weather_at(snapshot, lane_key,
                                               node_id, t)
        if _span is not None and (hb < _span[0] or hb > _span[1]):
            return None
        key = (lane_key, node_id, hb)
        got = _lane_wx_memo.get(key, _MISS)
        if got is _MISS:
            got = weather_mod.lane_weather_at(snapshot, lane_key,
                                              node_id, t)
            _lane_wx_memo[key] = got
        return got

    def leg_wx_sample(points: list, t: float):
        """weather_mod.leg_sample for a leg polyline; memoised per
        6-hour cell step when the polyline is one of the shared
        per-lane lists (never mutated, alive for the whole run)."""
        if id(points) not in _stable_points:
            return weather_mod.leg_sample(snapshot, points, t)
        key = (id(points), _floor(t / _cell_step_s))
        got = _leg_memo.get(key, _MISS)
        if got is _MISS:
            got = weather_mod.leg_sample(snapshot, points, t)
            _leg_memo[key] = got
        return got

    def port_slow(node_id: str, mode: str, t: float) -> bool:
        """_port_slowdown(snapshot, ..., t) or _port_slowdown(
        snapshot, ..., t, storm=_storm_at_node(...)): the second call
        with storm False repeats the first, with storm True it is the
        storm cell's constant verdict for the mode."""
        if _wx_on:
            hb = _hour_bucket(t)
            if hb is None:
                if _port_slowdown(snapshot, node_id, mode, t):
                    return True
            elif _span is not None and (hb < _span[0] or hb > _span[1]):
                pass
            else:
                key = (node_id, mode, hb)
                got = _slow_memo.get(key)
                if got is None:
                    got = _port_slowdown(snapshot, node_id, mode, t)
                    _slow_memo[key] = got
                if got:
                    return True
        if storm_tracks and _storm_at_node(node_id, t):
            return _storm_slow.get(mode, False)
        return False

    def eff_berths(node_id: str, t: float) -> int:
        loss = active_loss(node_id, t)
        if loss <= 0.0:
            return base_berths[node_id]
        return int(math.floor(base_berths[node_id] * (1.0 - loss) + 1e-9))

    segments: dict[str, list[dict]] = {}
    node_events: dict[str, list[tuple]] = (
        {nid: _NULL_EVENTS for nid in nodes} if light
        else {nid: [] for nid in nodes})
    arrivals: dict[str, list[float]] = {}
    arrival_events: dict[str, list[dict]] = {}
    aux_paths: dict[str, dict] = {}
    aux_counter = [0]
    ent_state: dict[str, dict] = {}
    heap: list[tuple] = []
    _next_seq = itertools.count(1).__next__
    _heappush = heapq.heappush
    _PRI_OF = _PRI

    def push(t: float, kind: str, eid: str, payload: dict) -> None:
        _heappush(heap, (t, _PRI_OF[kind], eid, _next_seq(), kind, payload))

    if light:
        def record(eid: str, t: float, kind: str, node: str, lane: str,
                   peer: str | None, **extra) -> None:
            return None
    elif trim_before is None:
        def record(eid: str, t: float, kind: str, node: str, lane: str,
                   peer: str | None, **extra) -> None:
            seg = {"t": round(t, 6), "kind": kind, "node": node,
                   "lane": lane, "peer": peer}
            if extra:
                seg.update(extra)
            segments[eid].append(seg)
    else:
        def record(eid: str, t: float, kind: str, node: str, lane: str,
                   peer: str | None, **extra) -> None:
            # runner._trim_segments, online: before trim_before keep
            # only the latest event (plus the arrival just before it
            # when it is a berth); after it keep everything.
            seg = {"t": round(t, 6), "kind": kind, "node": node,
                   "lane": lane, "peer": peer}
            if extra:
                seg.update(extra)
            lst = segments[eid]
            if seg["t"] <= trim_before:
                if kind == "berth" and lst and lst[-1]["kind"] == "arrive":
                    del lst[:-1]
                else:
                    lst.clear()
                lst.append(seg)
            else:
                lst.append(seg)

    def register_aux(eid: str, geom: dict, nodes_seq: list) -> str:
        aux_counter[0] += 1
        pid = f"aux_{eid}_{aux_counter[0]}"
        aux_paths[pid] = {
            "nodes": list(nodes_seq),
            "points": [[round(float(lat), 5), round(float(lon), 5)]
                       for lat, lon in geom["points"]],
            "length_km": round(float(geom["length_km"]), 6),
            "gc_km": round(float(geom["gc_km"]), 6),
            "transits": geom["transits"],
        }
        return pid

    _geom_memo: dict = {}
    _bearing_memo: dict = {}

    def geom_for_leg(from_node: str, dest: str, mode: str):
        """(geom, path_id, path_rev) for a fresh ping-pong leg: the
        directed lane's transit path when it exists, else the spawn
        lane's path sailed in reverse.

        Pure function of (from_node, dest, mode): the converted
        polyline and the reversed-transit table are built once per
        key and shared by reference (no consumer mutates a leg's
        points or transits; ``nodes`` stays a fresh list per leg),
        so every leg carries exactly the values the per-leg
        rebuild produced."""
        memo = _geom_memo.get((from_node, dest, mode))
        if memo is None:
            memo = _geom_build(from_node, dest, mode)
            _geom_memo[(from_node, dest, mode)] = memo
            # The memo keeps these lists alive for the whole run, so
            # their ids cannot be reused by another object.
            _stable_points.add(id(memo[3]))
            _stable_points.add(id(memo[4]))
        nodes_t, length_km, gc_km, transits, points, path_id, path_rev = memo
        return ({"nodes": list(nodes_t), "length_km": length_km,
                 "gc_km": gc_km, "transits": transits,
                 "points": points}, path_id, path_rev)

    def _geom_build(from_node: str, dest: str, mode: str):
        fwd_key = lane_key(mode, from_node, dest)
        if fwd_key in paths_table:
            p = paths_table[fwd_key]
            return (tuple(p["nodes"]), p["length_km"], p["gc_km"],
                    p["transits"], [tuple(q) for q in p["points"]],
                    fwd_key, False)
        rev_key = lane_key(mode, dest, from_node)
        p = paths_table[rev_key]
        rev_points = [tuple(q) for q in reversed(p["points"])]
        transits = []
        for tr in reversed(p["transits"]):
            # Sample nearest the chokepoint on the reversed polyline
            # (used only if the vessel later holds at this transit).
            clat, clon = node_coords[tr["node"]]
            idx = min(range(len(rev_points)),
                      key=lambda i: (rev_points[i][0] - clat) ** 2
                      + (rev_points[i][1] - clon) ** 2)
            transits.append({"node": tr["node"],
                             "frac": round(1.0 - tr["frac"], 6),
                             "sample_idx": idx,
                             "next_node": tr.get("prev_node"),
                             "prev_node": tr.get("next_node")})
        transits.sort(key=lambda t: (t["frac"], t["node"]))
        return (tuple(reversed(p["nodes"])), p["length_km"], p["gc_km"],
                transits, [tuple(q) for q in reversed(p["points"])],
                rev_key, True)

    def _advance_slot(st: dict, dest: str) -> None:
        """Schedule chain (air): the NEXT leg's scheduled
        departure slot = this leg's scheduled arrival + the nominal
        turnaround at ``dest``, quantised at ``dest`` (passenger
        banks at the home hub; cargo evening waves at integrator
        hubs). The chain is a pure function of the initial slot +
        nominal leg durations — actual delays never rewrite it, so
        late_min measures reality against a fixed schedule
        and slack (nominal minus drawn turnaround) absorbs delay."""
        if st["mode"] != "air":
            return
        raw = st["sched_arr"] + air_nominal_turnaround_h(
            st["cls"], dest in integrator_set) * 3600.0
        lon = node_coords[dest][1]
        if st["cls"] == "passenger" and dest == st["home_hub"]:
            raw = next_bank_epoch(raw, lon)
        elif st["cls"] == "cargo" and dest in integrator_set:
            raw = next_cargo_wave_epoch(raw, lon)
        st["pending_next_slot"] = raw

    def plan_leg(eid: str, st: dict, t: float, from_node: str,
                 dest: str, geom: dict, path_id: str,
                 diverted: bool = False, avoided: str | None = None,
                 sched_mode: str = "new") -> tuple:
        """Plan a leg's portions (decision point to decision point)
        and schedule its first event. Portion durations divide the
        portion's movement length by speed x weather factor, further
        slowed by the partial-loss approach factor of the
        portion's endpoint evaluated at its planned arrival.

        ``sched_mode``: "new" — a fresh leg: the caller has set
        st["sched_dep"]; the scheduled arrival is sched_dep + the
        nominal (weather-free) leg duration, and the air slot chain
        advances from it. "keep" — a continuation of the current leg
        (diversion / hold resume): the scheduled arrival stands.
        Returns (path_id, total_planned_s) — the path id may become
        an aux id when an airspace reroute bends the leg."""
        lane = lanes[st["lane"]]
        # Storm-cell field: when the pinned snapshot carries
        # cells, the leg's weather is the cell field's governing
        # sample over the leg's polyline points (weather.py's
        # leg_sample — the only cell-aware surface; no cell identity
        # reaches this module). A None from leg_sample (no cells /
        # no point sampled) falls back to the point-sample path, and
        # a snapshot without cells takes that path exactly as before.
        leg_wx = None
        if snapshot is not None and snapshot.get("cells"):
            leg_wx = leg_wx_sample(geom["points"], t)
        if leg_wx is not None:
            w = leg_wx["sample"]
        else:
            w = lane_wx(st["lane"], from_node, t)
        bearing = _bearing_memo.get((from_node, dest))
        if bearing is None:
            bearing = bearing_deg(node_coords[from_node],
                                  node_coords[dest])
            _bearing_memo[(from_node, dest)] = bearing
        if st["mode"] == "sea":
            factor = sea_speed_factor(w)
        else:
            factor = air_speed_factor(w, bearing)
        # Storm cell: a leg whose midpoint sits inside a cell
        # at the leg's mid-time takes the weather factors at the cell's
        # stated conditions, on top of the pinned-snapshot factor.
        if storm_tracks and geom["points"]:
            est_s = geom["length_km"] / st["speed_kmh"] * 3600.0 \
                if geom["length_km"] > 0 else 0.0
            mid_t = t + est_s / 2.0
            mid_pt = geom["points"][len(geom["points"]) // 2]
            for cell in storm_tracks:
                if cell.get("known_from", -math.inf) <= t and \
                        cell["start"] <= mid_t < cell["end"] and \
                        gc_distance_km(_storm_center(cell, mid_t), mid_pt) \
                        <= cell["radius_km"]:
                    if st["mode"] == "sea":
                        factor *= sea_speed_factor(
                            {"wind_kn": STORM_CELL_WIND_KN,
                             "wave_m": STORM_CELL_WAVE_M})
                    else:
                        factor *= air_speed_factor(
                            {"wind_kn": STORM_CELL_WIND_KN,
                             "wind_dir": bearing}, bearing)
                    break
        # Cell bend (BOTH modes): when the governing cell
        # sample is severe for the mode (slowdown thresholds:
        # sea wind > 40 kn or wave > 3.5 m; air wind > 45 kn), the
        # leg detours around the governing point's cell — geometry
        # replaced + registered as an aux path, mirroring the
        # airspace reroute below. One bend per leg: a leg bent here
        # skips the airspace detour.
        cell_bent = False
        if leg_wx is not None and leg_wx.get("bend") is not None:
            gw = w or {}
            if st["mode"] == "sea":
                severe = (gw.get("wind_kn") or 0.0) > SEA_SLOW_WIND_KN \
                    or (gw.get("wave_m") or 0.0) > SEA_SLOW_WAVE_M
            elif st["mode"] == "air":
                severe = (gw.get("wind_kn") or 0.0) > AIR_SLOW_WIND_KN
            else:
                severe = False
            if severe:
                bend = leg_wx["bend"]
                det = detour_around_circle(
                    [tuple(p) for p in geom["points"]],
                    (bend["lat"], bend["lon"]), bend["radius_km"])
                if det is not None:
                    geom = {"nodes": geom["nodes"],
                            "length_km": path_length_km(det),
                            "gc_km": geom["gc_km"],
                            "transits": geom["transits"],
                            "points": det}
                    path_id = register_aux(eid, geom, geom["nodes"])
                    cell_bent = True
        # Airspace closure: an air leg whose polyline
        # intersects an active circle reroutes around it — the leg
        # geometry becomes the tangent detour (registered as an aux
        # path, so the drawn route bends) and portion durations
        # below rescale by the new path length at the same speed.
        if not cell_bent and st["mode"] == "air" and airspace_zones:
            est_s = geom["length_km"] / (st["speed_kmh"] * factor) \
                * 3600.0 if geom["length_km"] > 0 else 0.0
            for zone in airspace_zones:
                if zone.get("known_from", -math.inf) <= t and \
                        t < zone["end"] and t + est_s > zone["start"]:
                    det = detour_around_circle(
                        [tuple(p) for p in geom["points"]],
                        zone["center"], zone["radius_km"])
                    if det is not None:
                        new_len = path_length_km(det)
                        geom = {"nodes": geom["nodes"],
                                "length_km": new_len,
                                "gc_km": geom["gc_km"],
                                "transits": geom["transits"],
                                "points": det}
                        path_id = register_aux(eid, geom, geom["nodes"])
                    break
        if sched_mode == "new":
            nominal_s = geom["length_km"] / st["speed_kmh"] * 3600.0 \
                if geom["length_km"] > 0 else 0.0
            st["sched_arr"] = st["sched_dep"] + nominal_s
            _advance_slot(st, dest)
        # A plan whose places carry no window at all is a pure
        # function of the (shared, never mutated) transit list, the
        # destination, the path length, the speed and the weather
        # factor: built once, shared read-only by every such leg.
        pkey = None
        hit = None
        if id(geom["transits"]) in _stable_points:
            pkey = (id(geom["transits"]), dest, geom["length_km"],
                    st["speed_kmh"], factor)
            hit = _plan_memo.get(pkey)
        if hit is not None:
            plan, elapsed = hit
        else:
            points = [(tr["frac"], tr["node"], False, tr)
                      for tr in geom["transits"]]
            points.append((1.0, dest, True, None))
            plan = []
            elapsed = 0.0
            prev_frac = 0.0
            windowed = False
            for frac, node, is_dest, tr in points:
                portion_km = (frac - prev_frac) * geom["length_km"]
                base_s = portion_km / (st["speed_kmh"] * factor) * 3600.0 \
                    if geom["length_km"] > 0 else 0.0
                if node in _wby:
                    windowed = True
                loss = active_loss(node, t + elapsed + base_s, known_at=t)
                af = 1.0 - PARTIAL_APPROACH_COEF * loss \
                    if 0.0 < loss < 1.0 else 1.0
                plan.append({"frac": frac, "node": node,
                             "is_dest": is_dest,
                             "dur": base_s / af,
                             "next_node": (tr or {}).get("next_node"),
                             "prev_node": (tr or {}).get("prev_node"),
                             "sample_idx": (tr or {}).get("sample_idx", 0)})
                elapsed += plan[-1]["dur"]
                prev_frac = frac
            if pkey is not None and not windowed:
                _plan_memo[pkey] = (plan, elapsed)
        st["leg"] = {"dest": dest, "geom": geom, "path_id": path_id,
                     "plan": plan, "idx": 0, "depart_t": t,
                     "diverted": diverted, "avoided": avoided,
                     "gc_km": geom["gc_km"],
                     "sched_arr": st.get("sched_arr"),
                     "planned_arr": t + elapsed}
        first = plan[0]
        push(t + first["dur"], "arrive" if first["is_dest"] else "transit",
             eid, {"node": first["node"]})
        return path_id, elapsed

    berthed: dict[str, int] = {nid: 0 for nid in nodes}
    queues: dict[str, list] = {nid: [] for nid in nodes}  # heap (t, eid)
    queued_at: dict[str, float] = {}

    def try_fill(node_id: str, t: float) -> None:
        while queues[node_id] and berthed[node_id] < eff_berths(node_id, t):
            _qt, eid = heapq.heappop(queues[node_id])
            st = ent_state[eid]
            record(eid, t, "berth", node_id, st["lane"], None)
            node_events[node_id].append((t, "berth"))
            berthed[node_id] += 1
            if st.get("resume") is not None:
                st["resume_berthed"] = True
            # An air entity's berth dwell IS its turnaround,
            # by class and place (cargo sort at integrator hubs uses
            # the hub band drawn at derivation). Sea dwell unchanged.
            dwell_h = st["dwell_h"]
            if st["mode"] == "air" and st.get("cls") == "cargo" \
                    and node_id in integrator_set:
                dwell_h = st.get("dwell_hub_h", dwell_h)
            mult = 1.0
            loss = active_loss(node_id, t)
            if 0.0 < loss < 1.0:
                mult *= 1.0 / (1.0 - loss)  # berth service rate scaled
            if port_slow(node_id, st["mode"], t):
                mult *= (1.0 / SEA_SLOW_SERVICE_RATE if st["mode"] == "sea"
                         else AIR_SLOW_DWELL_MULT)
            push(t + dwell_h * 3600.0 * mult, "dispatch", eid,
                 {"node": node_id})

    def start_departure(eid: str, st: dict, t: float, node_id: str,
                        payload: dict) -> None:
        """Shared departure path for fresh and resumed legs, after
        the closure/slowdown holds in the depart handler pass."""
        resume = payload.get("resume")
        if resume is not None:
            pid, _dur = plan_leg(eid, st, t, node_id, resume["dest"],
                                 resume["geom"], resume["path_id"],
                                 sched_mode="keep")
            record(eid, t, "depart", node_id, st["lane"], resume["dest"],
                   path=pid, resumed=True,
                   sched_arr=st.get("sched_arr"),
                   planned_arr=st["leg"]["planned_arr"])
            node_events[node_id].append((t, "depart"))
            return
        if payload.get("slot") is not None:
            st["slot"] = payload["slot"]
        repositioning = False
        reassign_from = None
        if st["mode"] == "air":
            # Rotations / recovery: the destination is the
            # next place on the aircraft's rotation, or — after a
            # diversion — the recovery place the diversion set up.
            if st.get("spawn_pending"):
                dest = st["away"]
                st["on_arrive_leg_idx"] = st["spawn_on_arrive"]
                st["spawn_pending"] = False
                recovery_leg = False
            elif st.get("recovery") is not None:
                dest = st["recovery"]["dest"]
                st["on_arrive_leg_idx"] = st["recovery"]["then_leg_idx"]
                st["recovery"] = None
                recovery_leg = True
            else:
                cycle = st["cycle"]
                dest = cycle[(st["leg_idx"] + 1) % len(cycle)]
                st["on_arrive_leg_idx"] = (st["leg_idx"] + 1) % len(cycle)
                recovery_leg = False
            st["cur_dest"] = dest
            if lane_key("air", node_id, dest) in lanes or \
                    lane_key("air", dest, node_id) in lanes:
                # An existing route (either served direction — the
                # leg geometry resolves forward or reversed, as
                # always in this simulator).
                geom, path_id, path_rev = geom_for_leg(node_id, dest, "air")
            else:
                # No served route either way: a non-revenue
                # repositioning leg on the direct great circle
                # (MODELLED, carries no goods).
                pts = slerp_samples(node_coords[node_id],
                                    node_coords[dest])
                geom = {"nodes": [node_id, dest],
                        "length_km": path_length_km(pts),
                        "gc_km": path_length_km(pts),
                        "transits": [], "points": pts}
                path_id = register_aux(eid, geom, [node_id, dest])
                path_rev = False
                repositioning = True
        else:
            dest = st["away"] if node_id == st["home"] else st["home"]
            plan_ra = reassign_plans.get(dest)
            if plan_ra is not None \
                    and plan_ra["span"][0] <= t < plan_ra["span"][1]:
                sub = plan_ra["lane_map"].get(st["lane"])
                if sub is not None and sub != node_id:
                    ok = False
                    if (lane_key("sea", node_id, sub) in lanes
                        and not _no_lane_geometry(lanes[lane_key("sea", node_id, sub)])) or \
                            (lane_key("sea", sub, node_id) in lanes
                             and not _no_lane_geometry(lanes[lane_key("sea", sub, node_id)])):
                        geom, path_id, path_rev = geom_for_leg(
                            node_id, sub, "sea")
                        ok = True
                    else:
                        # No direct lane: route via the diversion
                        # machinery, never through the closed place.
                        closed_now = closed_places(t)
                        dist, path = _dijkstra(
                            search_adj("sea", closed_now), node_id,
                            targets={sub}, blocked=closed_now | {dest})
                        cand = path.get(sub)
                        if cand is not None and len(cand) >= 2:
                            geom = movement_geometry(
                                list(cand), lanes, "sea", node_coords,
                                chokepoints_by_mode.get("sea", set()))
                            path_id = register_aux(eid, geom,
                                                   list(cand))
                            path_rev = False
                            ok = True
                    if ok:
                        reassign_from = dest
                        dest = sub
                        reassignment_accounting[reassign_from][
                            "substitutes"][sub]["departures"] += 1
            if reassign_from is None:
                if lane_key("sea", node_id, dest) in lanes or \
                        lane_key("sea", dest, node_id) in lanes:
                    geom, path_id, path_rev = geom_for_leg(
                        node_id, dest, st["mode"])
                else:
                    # Repositioning after a reassignment landing: no
                    # served lane connects the substitute to the
                    # ping-pong destination — sail the direct arc
                    # (MODELLED, as air repositioning).
                    pts = slerp_samples(node_coords[node_id],
                                        node_coords[dest])
                    geom = {"nodes": [node_id, dest],
                            "length_km": path_length_km(pts),
                            "gc_km": path_length_km(pts),
                            "transits": [], "points": pts}
                    path_id = register_aux(eid, geom, [node_id, dest])
                    path_rev = False
                    repositioning = True
        # Schedule stamps: scheduled departure is the air slot
        # (the sea schedule is its actual departure — sea departures
        # are dispatch-driven with no slot chain).
        st["sched_dep"] = st["slot"] if st["mode"] == "air" else t
        pid, _dur = plan_leg(eid, st, t, node_id, dest, geom, path_id)
        if reassign_from is not None:
            st["leg"]["reassigned_from"] = reassign_from
        extra = {"path": pid, "sched_arr": st.get("sched_arr"),
                 "planned_arr": st["leg"]["planned_arr"]}
        if reassign_from is not None:
            extra["reassigned"] = True
            extra["reassigned_from"] = reassign_from
        if path_rev:
            extra["path_rev"] = True
        if repositioning:
            extra["repositioning"] = True
        record(eid, t, "depart", node_id, st["lane"], dest, **extra)
        node_events[node_id].append((t, "depart"))

    def _open_air_places(t: float, exclude: str) -> list:
        """[(distance_km, place_id)] for air places OPEN at t
        (effective loss < 1.0), nearest first, id tie-break —
        deterministic."""
        out = []
        for nid, n in nodes.items():
            if n["mode"] != "air" or nid == exclude:
                continue
            if active_loss(nid, t) >= 1.0:
                continue
            out.append((gc_distance_km(node_coords[exclude],
                                       node_coords[nid]), nid))
        out.sort()
        return out

    def _do_arrival(eid: str, st: dict, t: float, node_id: str) -> None:
        """Normal leg arrival: record, queue for a berth (FIFO)."""
        leg = st["leg"]
        extra = {}
        if leg is not None and leg.get("diverted"):
            extra = {"diverted": True, "avoided": leg.get("avoided")}
        if leg is not None and leg.get("reassigned_from"):
            # A reassigned arrival lands at the
            # substitute; account the call and the modelled volume
            # (the vessel's lane capacity share x the closed place's
            # daily capacity — the graph's own capacity basis).
            extra["reassigned"] = True
            extra["reassigned_from"] = leg["reassigned_from"]
            acc = reassignment_accounting[leg["reassigned_from"]][
                "substitutes"][node_id]
            acc["calls"] += 1
            acc["volume"] += float(
                lanes[st["lane"]].get("capacity_share") or 0.0) * float(
                nodes[leg["reassigned_from"]].get("capacity") or 0.0)
        if leg is not None and leg.get("sched_arr") is not None:
            extra["sched_arr"] = leg["sched_arr"]
        record(eid, t, "arrive", node_id, st["lane"], None, **extra)
        node_events[node_id].append((t, "arrive"))
        arrivals.setdefault(eid, []).append(round(t, 6))
        arrival_events.setdefault(eid, []).append(
            {"t": round(t, 6), "node": node_id, "hold": False})
        st["leg"] = None
        st["resume"] = None
        if st["mode"] == "air" and st.get("on_arrive_leg_idx") is not None:
            st["leg_idx"] = st["on_arrive_leg_idx"]
            st["on_arrive_leg_idx"] = None
        queued_at[eid] = t
        heapq.heappush(queues[node_id], (t, eid))
        node_events[node_id].append((t, "queue_join"))
        try_fill(node_id, t)

    def _do_air_divert(eid: str, st: dict, t: float, missed: str,
                       target: str) -> None:
        """An aircraft whose destination place is closed
        diverts to ``target``. The rotation then resumes from the
        diversion place toward its next scheduled place (recovery
        bookkeeping): the place AFTER the missed destination in the
        rotation cycle, unless the diversion place IS that place."""
        if st.get("recovery") is not None:
            if target == st["recovery"]["dest"]:
                st["on_arrive_leg_idx"] = st["recovery"]["then_leg_idx"]
                st["recovery"] = None
            else:
                st["on_arrive_leg_idx"] = None
        else:
            dest_idx = st.get("on_arrive_leg_idx")
            cycle = st["cycle"]
            if dest_idx is None:
                dest_idx = cycle.index(st["cur_dest"])
            nxt = (dest_idx + 1) % len(cycle)
            if target == cycle[nxt]:
                st["on_arrive_leg_idx"] = nxt
            else:
                st["recovery"] = {"dest": cycle[nxt], "then_leg_idx": nxt}
                st["on_arrive_leg_idx"] = None
        if lane_key("air", missed, target) in lanes or \
                lane_key("air", target, missed) in lanes:
            geom, pid0, _rev = geom_for_leg(missed, target, "air")
        else:
            pts = slerp_samples(node_coords[missed], node_coords[target])
            geom = {"nodes": [missed, target],
                    "length_km": path_length_km(pts),
                    "gc_km": path_length_km(pts),
                    "transits": [], "points": pts}
            pid0 = register_aux(eid, geom, [missed, target])
        pid, _dur = plan_leg(eid, st, t, missed, target, geom, pid0,
                             diverted=True, avoided=missed,
                             sched_mode="keep")
        _advance_slot(st, target)
        st["cur_dest"] = target
        record(eid, t, "divert", missed, st["lane"], target,
               path=pid, avoided=missed, diverted=True,
               sched_arr=st.get("sched_arr"),
               planned_arr=st["leg"]["planned_arr"])

    def on_transit(t: float, eid: str, payload: dict) -> None:
        st = ent_state[eid]
        leg = st["leg"]
        step = leg["plan"][leg["idx"]]
        node_id = payload["node"]
        loss = active_loss(node_id, t)
        if loss < 1.0:
            # Proceed: partial loss on this approach was already
            # priced into the portion at planning time.
            leg["idx"] += 1
            nxt = leg["plan"][leg["idx"]]
            push(t + nxt["dur"], "arrive" if nxt["is_dest"] else "transit",
                 eid, {"node": nxt["node"]})
            return
        dest = leg["dest"]
        # WAIT-VS-DIVERT. The vessel stands at the
        # closed place's approach, on the origin side. A bypass is a
        # route that never passes through the closed place: turn back
        # to the last route node before it (prev), then the shortest
        # path prev -> dest avoiding every currently-closed place.
        # (Searching from the closed place itself would let a "bypass"
        # leave via the place's own far-side leg — Suez ->
        # Port Said is the canal's own north leg — a route that does
        # not avoid the closure, with a fictitious added time.) The
        # bypass qualifies at <= DIVERT_MAX_RATIO x the leg's nominal
        # great-circle length. The fleet
        # HOLDS when the closure's expected remaining duration (the
        # windows' expected_end: declared expectation, +inf when a
        # spec declares the closure open-ended) is no longer than the
        # diversion's added time over the leg's remaining nominal
        # length, both at the entity's nominal speed; it DIVERTS when
        # the expected wait is longer and a bypass qualifies.
        closed = closed_places(t)
        diverted = False
        if dest not in closed:
            prev = step.get("prev_node") or leg["geom"]["nodes"][0]
            if prev != node_id and prev not in closed:
                dist, path = _dijkstra(search_adj(st["mode"], closed),
                                       prev, targets={dest},
                                       blocked=closed)
                cand = path.get(dest)
                if cand is not None and len(cand) >= 2:
                    turnback_km = gc_distance_km(node_coords[node_id],
                                                 node_coords[prev])
                    bypass_km = turnback_km + dist[dest]
                    if bypass_km <= DIVERT_MAX_RATIO * leg["gc_km"]:
                        remaining_km = (1.0 - step["frac"]) * leg["gc_km"]
                        added_s = max(0.0, bypass_km - remaining_km) \
                            / st["speed_kmh"] * 3600.0
                        exp_ends = [w.get("expected_end", w["end"])
                                    for w in windows
                                    if w["node_id"] == node_id
                                    and w["start"] <= t < w["end"]
                                    and w["loss"] >= 1.0]
                        expected_s = (max(exp_ends) - t) \
                            if exp_ends else 0.0
                        if expected_s > added_s:
                            tb_pts = _turnback_pts(node_id, prev, st["mode"])
                            sub = movement_geometry(
                                list(cand), lanes, st["mode"],
                                node_coords,
                                chokepoints_by_mode.get(st["mode"], set()))
                            tb_len = path_length_km(tb_pts)
                            total_len = tb_len + sub["length_km"]
                            transits = [{
                                "node": tr["node"],
                                "frac": round(
                                    (tb_len + tr["frac"]
                                     * sub["length_km"]) / total_len, 6)
                                if total_len > 0 else tr["frac"],
                                "sample_idx": (len(tb_pts) - 1)
                                + tr.get("sample_idx", 0),
                                "next_node": tr.get("next_node"),
                                "prev_node": tr.get("prev_node")}
                                for tr in sub["transits"]]
                            geom = {"nodes": [node_id] + list(cand),
                                    "length_km": total_len,
                                    "gc_km": turnback_km + sub["gc_km"],
                                    "transits": transits,
                                    "points": list(tb_pts)
                                    + [tuple(p) for p in sub["points"][1:]]}
                            pid0 = register_aux(eid, geom, geom["nodes"])
                            pid, _dur = plan_leg(
                                eid, st, t, node_id, dest, geom, pid0,
                                diverted=True, avoided=node_id,
                                sched_mode="keep")
                            record(eid, t, "divert", node_id,
                                   st["lane"], dest, path=pid,
                                   avoided=node_id, diverted=True,
                                   sched_arr=st.get("sched_arr"),
                                   planned_arr=st["leg"]["planned_arr"])
                            diverted = True
        if diverted:
            return
        # HOLD: queue at the chokepoint approach; clearance via the
        # chokepoint's berth machinery (berths are 0 while closed, so
        # the vessel waits for the window-end recheck, FIFO).
        geom = leg["geom"]
        idx0 = step.get("sample_idx", 0)
        pts = list(geom["points"][idx0:]) if idx0 > 0 else list(geom["points"])
        if len(pts) < 2:
            pts = list(geom["points"][-2:])
        f0 = step["frac"]
        # Resume node sequence: the chokepoint, then the nominal
        # path's nodes from the nominal next node onward.
        gnodes = geom["nodes"]
        if step.get("next_node") in gnodes:
            resume_nodes = [node_id] + gnodes[gnodes.index(step["next_node"]):]
        else:
            resume_nodes = [node_id, dest]
        if len(resume_nodes) > 1 and resume_nodes[0] == resume_nodes[1]:
            resume_nodes = resume_nodes[1:]
        rem_transits = []
        for tr in geom["transits"]:
            if tr["frac"] > f0:
                rem_transits.append({
                    "node": tr["node"],
                    "frac": round((tr["frac"] - f0) / (1.0 - f0), 6)
                    if f0 < 1.0 else tr["frac"],
                    "sample_idx": max(0, tr.get("sample_idx", 0) - idx0),
                    "next_node": tr.get("next_node"),
                    "prev_node": tr.get("prev_node")
                    if tr.get("prev_node") in resume_nodes else node_id})
        resume_geom = {"nodes": resume_nodes,
                       "length_km": path_length_km(pts),
                       "gc_km": geom["gc_km"] * (1.0 - f0),
                       "transits": rem_transits, "points": pts}
        pid = register_aux(eid, resume_geom, resume_geom["nodes"])
        st["resume"] = {"dest": dest, "geom": resume_geom, "path_id": pid}
        st["leg"] = None
        record(eid, t, "arrive", node_id, st["lane"], None,
               hold=True, avoided=node_id)
        node_events[node_id].append((t, "arrive"))
        arrivals.setdefault(eid, []).append(round(t, 6))
        arrival_events.setdefault(eid, []).append(
            {"t": round(t, 6), "node": node_id, "hold": True})
        queued_at[eid] = t
        heapq.heappush(queues[node_id], (t, eid))
        node_events[node_id].append((t, "queue_join"))
        try_fill(node_id, t)

    for ent in derivation["entities"]:
        st = {
            "lane": ent["lane"], "mode": ent["mode"],
            "home": ent["from"], "away": ent["to"],
            "speed_kmh": ent["speed_kmh"], "dwell_h": ent["dwell_h"],
            "at": ent["from"], "leg": None, "resume": None,
            "sched_dep": None, "sched_arr": None,
            "pending_next_slot": None, "slot": None,
        }
        if ent["mode"] == "air":
            # Rotation state: cycle = the closed rotation
            # without its repeated final hub; leg_idx = the pattern
            # leg the aircraft flies NEXT from its current place.
            cycle = list(ent["rotation"][:-1])
            st.update({
                "cls": ent["cls"], "home_hub": ent["home_hub"],
                "dwell_hub_h": ent.get("dwell_hub_h"),
                "cycle": cycle, "cur_dest": ent["to"],
                "slot": ent["first_departure_epoch"],
                "spawn_pending": True, "recovery": None,
                "on_arrive_leg_idx": None,
            })
            # The spawn leg's pattern position: if the spawn leg is
            # a rotation leg, arrival advances past it; else the
            # aircraft reaches its home hub on the spawn leg and the
            # rotation starts at leg 0 (the patterns are built from
            # the spawn lane's endpoints, so the non-pattern case is
            # exactly spoke -> hub).
            spawn_on_arrive = 0
            for j in range(len(cycle)):
                if cycle[j] == ent["from"] and \
                        cycle[(j + 1) % len(cycle)] == ent["to"]:
                    spawn_on_arrive = (j + 1) % len(cycle)
                    break
            st["spawn_on_arrive"] = spawn_on_arrive
            st["leg_idx"] = spawn_on_arrive
        ent_state[ent["id"]] = st
        segments[ent["id"]] = []
        push(ent["first_departure_epoch"], "depart", ent["id"],
             {"node": ent["from"]})
    # Berth-count changes at disruption window edges get a recheck so
    # queued entities berth when a window lifts.
    for w in windows:
        edges = [w["start"], w["end"]]
        ramp = w.get("ramp_s") or 0.0
        if ramp > 0.0 and w["loss"] > 0.0:
            # During a recovery ramp the berth count
            # steps up as the loss decays — book a recheck at each
            # analytic crossing so queued entities berth as service
            # restores, not only at tick boundaries.
            base = base_berths[w["node_id"]]
            for k in range(1, base):
                if k > base * (1.0 - w["loss"]):
                    frac = 1.0 - (1.0 - k / base) / w["loss"]
                    if 0.0 < frac < 1.0:
                        edges.append(w["end"] + ramp * frac)
        for t_edge in edges:
            if start_epoch - derivation.get("preroll_s", 0.0) <= t_edge <= horizon_end:
                push(t_edge, "recheck", "", {"node": w["node_id"]})

    while heap:
        t, _pri, eid, _c, kind, payload = heapq.heappop(heap)
        if t > horizon_end:
            break
        if kind == "recheck":
            try_fill(payload["node"], t)
            continue
        st = ent_state[eid]
        node_id = payload["node"]
        if kind == "depart":
            # Closure at origin: departures held until the window ends.
            if active_loss(node_id, t) >= 1.0:
                ends = [w["end"] for w in _wby[node_id]
                        if w["start"] <= t < w["end"]]
                # At a ramp's exact start instant the
                # decayed loss still reads 1.0 while no flat window
                # covers t — re-check one second later, when the
                # ramp has brought the loss below 1.0.
                push(min(ends) if ends else t + 1.0, "depart", eid,
                     payload)
                continue
            # Sea port slowdown: departures held hour by hour.
            if st["mode"] == "sea" and (
                    port_slow(node_id, "sea", t)):
                if t - payload.get("held_since", t) < MAX_DEPARTURE_HOLD_S:
                    push(t + 3600.0, "depart", eid,
                         {**payload, "held_since": payload.get("held_since", t)})
                    continue
            start_departure(eid, st, t, node_id, payload)
        elif kind == "transit":
            on_transit(t, eid, payload)
        elif kind == "arrive":
            if st["mode"] == "air" and \
                    active_loss(node_id, t) >= 1.0:
                # An aircraft arriving at a place that is
                # closed DIVERTS — nearest open place within the
                # stated range; else it holds airborne for
                # AIR_DIVERT_HOLD_S, then diverts to the nearest
                # open place regardless of range (divertcheck).
                cands = _open_air_places(t, node_id)
                in_range = [c for c in cands
                            if c[0] <= AIR_DIVERT_RANGE_KM]
                if in_range:
                    _do_air_divert(eid, st, t, node_id, in_range[0][1])
                    continue
                if cands:
                    push(t + AIR_DIVERT_HOLD_S, "divertcheck", eid,
                         {"node": node_id})
                    continue
                # No open air place anywhere: fall through and queue
                # at the closed place (the only honest option left).
            _do_arrival(eid, st, t, node_id)
        elif kind == "divertcheck":
            # Hold expiry: land if the place reopened, else
            # divert to the nearest open place regardless of range;
            # if literally none is open, hold another interval.
            if active_loss(node_id, t) < 1.0:
                _do_arrival(eid, st, t, node_id)
            else:
                cands = _open_air_places(t, node_id)
                if cands:
                    _do_air_divert(eid, st, t, node_id, cands[0][1])
                else:
                    push(t + AIR_DIVERT_HOLD_S, "divertcheck", eid,
                         {"node": node_id})
        elif kind == "dispatch":
            record(eid, t, "dispatch", node_id, st["lane"], None)
            node_events[node_id].append((t, "dispatch"))
            berthed[node_id] -= 1
            try_fill(node_id, t)
            resume = st.pop("resume", None) if st.pop("resume_berthed", False) else None
            # Departure rule, both modes: next departure =
            # max(schedule slot, berth completion + turnaround).
            # The dispatch instant IS berth completion (turnaround
            # included); sea has no slot chain, so its departure is
            # the dispatch instant exactly as before. Air waits for
            # its slot when the slot is later (bank/wave spacing).
            slot = st.get("pending_next_slot") \
                if st["mode"] == "air" else None
            if slot is not None and slot > t:
                push(slot, "depart", eid,
                     {"node": node_id, "resume": resume, "slot": slot})
            else:
                push(t, "depart", eid,
                     {"node": node_id, "resume": resume, "slot": slot})

    node_series = {} if light else _build_node_series(
        node_events, nodes, start_epoch, tick_s, ticks)
    # The closures above form reference cycles holding the whole run
    # state; with the collector paused they would outlive this call.
    heap.clear()
    ent_state.clear()
    node_events.clear()
    queues.clear()
    return {"segments": segments, "node_series": node_series,
            "arrivals": arrivals, "arrival_events": arrival_events,
            "aux_paths": aux_paths,
            "reassignment_accounting": reassignment_accounting}


def _build_node_series(node_events: dict, nodes: dict, start_epoch: float,
                       tick_s: int, ticks: int) -> dict:
    """Per node per tick: queue, berthed, cumulative queue-hours,
    cumulative arrivals/departures — sampled at tick boundaries from
    the continuous event record."""
    series: dict[str, dict] = {}
    for nid in nodes:
        evs = sorted(node_events.get(nid, []))
        queue = berthed_n = arr = dep = 0
        queue_hours = 0.0
        last_t: float | None = None  # queue-hours integrate from the
        # first node event (pre-roll included) forward.
        out = {"queue": [], "berthed": [], "queue_hours_cum": [],
               "arrivals": [], "departures": []}
        ei = 0
        for k in range(ticks):
            tick_t = start_epoch + k * tick_s
            while ei < len(evs) and evs[ei][0] <= tick_t:
                et, ekind = evs[ei]
                if last_t is not None:
                    queue_hours += queue * (et - last_t) / 3600.0
                last_t = et
                if ekind == "queue_join":
                    queue += 1
                elif ekind == "berth":
                    queue -= 1
                    berthed_n += 1
                elif ekind == "dispatch":
                    berthed_n -= 1
                elif ekind == "arrive":
                    arr += 1
                elif ekind == "depart":
                    dep += 1
                ei += 1
            if last_t is not None:
                queue_hours += queue * (tick_t - last_t) / 3600.0
            last_t = tick_t
            out["queue"].append(queue)
            out["berthed"].append(berthed_n)
            out["queue_hours_cum"].append(round(queue_hours, 6))
            out["arrivals"].append(arr)
            out["departures"].append(dep)
        series[nid] = out
    return series
