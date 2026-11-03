"""LANE PLANNER: origin to destination routes as RANGES.

A pure function of (graph, sidecars, request). It composes existing public
functions only and changes nothing in server/engine or server/engine2:

  * routes and their geometry: the graph's own legs (great-circle chords
    between place coordinates, the same geometry the engine-rev-2 routes table
    uses), the services sidecar's rotations (engine2.routes table entries) and
    the extra sidecar's two-way Red Sea corridor and Panama and Cape lanes,
    all through engine2.journal.get_extra_engine2 / get_routed_engine2;
  * queue wait: ENGINE REV 2 run under the chosen disruption, one run per
    named variant (no sampling, no RNG);
  * base sea-lane days: route length times the leg's own days-per-km (the
    engine's time basis), then named speed variants.

Ranges are built ONLY from the named variants listed in ``variants`` of the
response: LOW is the smallest and HIGH the largest value over the variants,
MEDIAN is the BASE variant. So LOW <= MEDIAN <= HIGH by construction. Every
number block carries a label (MODELLED, SIMULATED, SOURCED) and source ids.

No accuracy claim is made or implied: the planner reports what the model
returns, and flags where its route lengths fall outside a cited real-world
range.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import math
import threading

try:
    from engine.canonical import canonical_json
    from engine.engine import EngineError, core_subgraph, validate_disruption
    from engine.ensemble import DURATION_MULTS
    from engine2 import Engine2
    from engine2.plugins import ENGINE_LABEL, PLUGINS_HASH
except Exception:  # pragma: no cover - imported as server.* from the repo root
    from server.engine.canonical import canonical_json  # type: ignore
    from server.engine.engine import EngineError, core_subgraph, validate_disruption  # type: ignore
    from server.engine.ensemble import DURATION_MULTS  # type: ignore
    from server.engine2 import Engine2  # type: ignore
    from server.engine2.plugins import ENGINE_LABEL, PLUGINS_HASH  # type: ignore

from . import refs
from . import lanes as lane_mod

SCHEMA = "lanes.plan/1"
EARTH_R_KM = 6371.0088
RUN_EPOCH = "2026-01-01T00:00:00Z"      # engine runs need a clock; no output depends on it
DURATION_MAX_D = 3650.0

# Sail-speed variants: the sim's sea speed classes {23, 21, 19, 16} kn
# (server/sim/derive.py SPEEDS, MODELLED); 20 kn is the service speed the graph
# builder uses for generated legs (data/graph/build_graph.py, 889 km/day). The
# graph's own days-per-km already includes port time, so a variant scales it by
# BASE / speed: FAST 23 kn, BASE 20 kn, SLOW 16 kn.
BASE_KN = 20.0
SAIL_VARIANTS = (("SPEED-FAST", 23.0), ("SPEED-BASE", 20.0), ("SPEED-SLOW", 16.0))
MAX_DETOUR_RATIO = 2.0                  # corridor candidates longer than this x the shortest are not offered
MAX_GRAPH_ALTERNATES = 3
CORRIDORS = ("chk_suez", "chk_panama", "chk_capeofgoodhope")
ENGINE_ERRORS = {"unknown_node": "disruption_place_not_modelled"}

DEFAULT_SERVICES_NOTE = "all three eras; unverified and unstated calls excluded"

# What the two queue blocks of a route mean (response key ``queue_measures``). The old ``queue`` block is the
# run PEAK; the wait a vessel meets on reaching a place is ``queue_at_arrival``.
QUEUE_RULE_PEAK = ("run peak: the largest wait of the run at any step, max over the places of the route; "
                   "not the wait at arrival")
QUEUE_RULE_ARRIVAL = ("wait at arrival: the wait a vessel meets at each place of the route on its modelled arrival day "
                      "(sail days from departure, earlier waits not added), max over the places of the route; "
                      "range over the named speed and queue variants; never above the run peak")
# A shell place is served through the nearest core place the graph joins to it by a sea leg. An access leg longer
# than this is flagged in ``checks`` (shown, never hidden). TEMPORARY: no sourced figure exists for it.
SHELL_ACCESS_LIMIT_KM = 1000.0
QUEUE_MEASURES = {"queue": "run peak: the largest wait of the run at any step; not the wait at arrival",
                  "queue_at_arrival": "wait a vessel meets on reaching each place on its modelled arrival day"}


class LaneRefusal(Exception):
    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


# ----------------------------------------------------------------- helpers
def _r(x, nd=3):
    return None if x is None else round(float(x), nd)


def _gc_km(a, b) -> float:
    la1, lo1, la2, lo2 = (math.radians(v) for v in (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2.0 * EARTH_R_KM * math.asin(min(1.0, math.sqrt(h)))


def _sha(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _place_row(node: dict) -> dict:
    return {"id": node["id"], "name": node["name"], "kind": node["kind"], "mode": node["mode"],
            "tier": node.get("tier", "core"), "country": node.get("country"),
            "lat": node["lat"], "lon": node["lon"]}


# ----------------------------------------------------------------- network
class _Net:
    """Sea legs of the CORE view, sailed either way (as the routes table does)."""

    def __init__(self, core):
        self.nodes = core.nodes
        self.xy = {k: (float(n["lat"]), float(n["lon"])) for k, n in core.nodes.items()}
        adj: dict[str, set] = {}
        self.days: dict[tuple, float] = {}
        for e in core.edges:
            if e["mode"] != "sea":
                continue
            u, v = e["from"], e["to"]
            adj.setdefault(u, set()).add(v)
            adj.setdefault(v, set()).add(u)
            self.days[(u, v)] = float(e["base_transit_d"])
        self.adj = {u: sorted(v) for u, v in adj.items()}
        self._kmc: dict = {}

    def chord(self, a: str, b: str) -> float:
        return _gc_km(self.xy[a], self.xy[b])

    def add_place(self, node: dict) -> None:
        """A place outside the CORE view (a shell endpoint) that only the access legs of a route touch."""
        self.nodes = dict(self.nodes)
        self.nodes[node["id"]] = node
        self.xy[node["id"]] = (float(node["lat"]), float(node["lon"]))

    def undirected_days(self, a: str, b: str):
        vals = [self.days[k] for k in ((a, b), (b, a)) if k in self.days]
        return min(vals) if vals else None

    def _dijkstra(self, x: str, y: str, weight):
        best = {x: 0.0}
        heap = [(0.0, (x,), x)]
        done = set()
        while heap:
            d, p, u = heapq.heappop(heap)
            if u in done:
                continue
            done.add(u)
            if u == y:
                return d, p
            for v in self.adj.get(u, []):
                w = weight(u, v)
                if w is None:
                    continue
                nd = d + w
                if nd < best.get(v, float("inf")) - 1e-9:
                    best[v] = nd
                    heapq.heappush(heap, (nd, p + (v,), v))
        return None

    def km_path(self, x: str, y: str):
        key = (x, y)
        if key not in self._kmc:
            self._kmc[key] = self._dijkstra(x, y, self.chord)
        return self._kmc[key]

    def days_path(self, x: str, y: str):
        return self._dijkstra(x, y, self.undirected_days)

    def leg_km(self, path) -> list:
        return [self.chord(a, b) for a, b in zip(path, path[1:])]


# ------------------------------------------------------------- shell places
def shell_anchor(graph, pid: str, only: str | None = None):
    """The nearest CORE sea place that the graph joins to the shell place ``pid`` by a sea leg, with the great-circle
    distance of that leg and the graph's own transit days for it; None when there is none. ``only`` restricts the
    search to one core place (used to compare candidates). Ties go to the lower place id."""
    nodes = graph.nodes
    n = nodes[pid]
    here = (float(n["lat"]), float(n["lon"]))
    best = None
    for e in list(graph.outbound(pid)) + list(graph.inbound(pid)):
        if e["mode"] != "sea":
            continue
        o = e["to"] if e["from"] == pid else e["from"]
        on = nodes.get(o)
        if o == pid or on is None or on.get("tier", "core") != "core" or on["mode"] != "sea":
            continue
        if only is not None and o != only:
            continue
        cand = (_gc_km(here, (float(on["lat"]), float(on["lon"]))), o, float(e["base_transit_d"]))
        if best is None or cand[:2] < best[:2] or (cand[:2] == best[:2] and cand[2] < best[2]):
            best = cand
    if best is None:
        return None
    return {"id": best[1], "name": nodes[best[1]]["name"], "km": best[0], "leg_days": best[2]}


# ------------------------------------------------------------- the request
def parse_body(body, scenarios) -> dict:
    if not isinstance(body, dict):
        raise LaneRefusal("invalid_request", "request body must be a JSON object")
    bad = sorted(set(body) - {"schema", "origin", "destination", "disruption", "departs_at", "extra", "shell_access"})
    if bad:
        raise LaneRefusal("invalid_request", f"unknown keys: {bad}")
    if body.get("schema") not in (None, SCHEMA):
        raise LaneRefusal("invalid_request", f"schema must be {SCHEMA!r}")
    out = {}
    for k in ("origin", "destination"):
        v = body.get(k)
        if not isinstance(v, str) or not v or len(v) > 128:
            raise LaneRefusal("invalid_request", f"{k} must be a place id")
        out[k] = v
    extra = body.get("extra", True)
    if not isinstance(extra, bool):
        raise LaneRefusal("invalid_request", "extra must be a boolean")
    out["extra"] = extra
    shell = body.get("shell_access", False)
    if not isinstance(shell, bool):
        raise LaneRefusal("invalid_request", "shell_access must be a boolean")
    if shell:
        out["shell_access"] = True          # present only when asked, so every other plan keeps its inputs digest
    dep = body.get("departs_at")
    if dep is not None:
        from datetime import datetime
        if not isinstance(dep, str):
            raise LaneRefusal("invalid_request", "departs_at must be an ISO-8601 string")
        try:
            datetime.fromisoformat(dep.replace("Z", "+00:00"))
        except ValueError:
            raise LaneRefusal("invalid_request", "departs_at is not a valid ISO-8601 timestamp") from None
    out["departs_at"] = dep
    out["disruption"] = _parse_disruption(body.get("disruption"), scenarios)
    return out


def _num(v, name, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not (lo < float(v) <= hi):
        raise LaneRefusal("invalid_disruption", f"{name} must be a number in ({lo}, {hi}]")
    return float(v)


def _parse_disruption(d, scenarios):
    if d is None:
        return None
    if not isinstance(d, dict):
        raise LaneRefusal("invalid_request", "disruption must be null or an object")
    allowed = {"scenario_id", "place_id", "kind", "capacity_loss", "duration_d", "shape"}
    bad = sorted(set(d) - allowed)
    if bad:
        raise LaneRefusal("invalid_request", f"unknown disruption keys: {bad}")
    sid = d.get("scenario_id")
    if sid is not None:
        if not isinstance(sid, str):
            raise LaneRefusal("invalid_request", "scenario_id must be a string")
        lib = {s["id"]: s for s in (scenarios or [])}
        s = lib.get(sid)
        if s is None:
            raise LaneRefusal("unknown_scenario", f"unknown scenario_id: {sid!r}")
        kind = s["kind"]
        loss = 1.0 if kind == "closure" else float(s.get("default_capacity_loss", 0.5))
        out = {"scenario_id": sid, "place_id": s["place_id"], "kind": kind,
               "capacity_loss": loss, "duration_d": float(s["default_duration_d"]), "shape": "step"}
        for k in ("capacity_loss", "duration_d"):
            if k in d:
                out[k] = _num(d[k], k, 0.0, 1.0 if k == "capacity_loss" else DURATION_MAX_D)
        if "kind" in d or "place_id" in d or "shape" in d:
            raise LaneRefusal("invalid_request", "a scenario_id disruption takes only capacity_loss and duration_d")
        if kind == "closure":
            out["capacity_loss"] = 1.0
        return out
    for k in ("place_id", "kind", "duration_d"):
        if k not in d:
            raise LaneRefusal("invalid_request", f"disruption.{k} is required")
    if not isinstance(d["place_id"], str) or not isinstance(d["kind"], str):
        raise LaneRefusal("invalid_request", "disruption.place_id and kind must be strings")
    kind = d["kind"]
    loss = 1.0 if kind == "closure" else _num(d.get("capacity_loss", 0.5), "capacity_loss", 0.0, 1.0)
    shape = d.get("shape", "step")
    if shape != "step":
        raise LaneRefusal("invalid_disruption", "shape must be 'step'")
    return {"scenario_id": None, "place_id": d["place_id"], "kind": kind, "capacity_loss": loss,
            "duration_d": _num(d["duration_d"], "duration_d", 0.0, DURATION_MAX_D), "shape": "step"}


# ------------------------------------------------------------- layers
class _Layers:
    """What the planner could load, and what it could not (never faked)."""

    def __init__(self, graph, use_extra):
        self.graph = graph
        self.states = {"services": "NOT SERVED", "extra": "NOT SERVED" if use_extra else "OFF",
                       "calibration": "NOT SERVED"}
        self.routes_sel = None
        self.extra_sel = None
        self.services_note = None
        self.cal = None
        try:
            from graph_services.loader import ERAS
            from graph_services.runtime import selection_for_run as services_sel
        except Exception:  # pragma: no cover
            from server.graph_services.loader import ERAS  # type: ignore
            from server.graph_services.runtime import selection_for_run as services_sel  # type: ignore
        sel, _svc, err = services_sel(graph.raw, graph.hash, {"eras": list(ERAS)}, "")
        if err is None and sel is not None:
            self.routes_sel = sel
            self.states["services"] = "SERVED"
        if use_extra:
            try:
                from graph_extra.runtime import selection_for_run as extra_sel
            except Exception:  # pragma: no cover
                from server.graph_extra.runtime import selection_for_run as extra_sel  # type: ignore
            xs, _ex, xerr = extra_sel(graph.raw, graph.hash, True, "engine2")
            if xerr is None and xs is not None:
                self.extra_sel = xs
                self.states["extra"] = "SERVED"
        try:
            try:
                from engine2.calibration import default_calibration
            except Exception:  # pragma: no cover
                from server.engine2.calibration import default_calibration  # type: ignore
            self.cal = default_calibration()
            self.states["calibration"] = "SERVED"
        except Exception:
            self.cal = None

    def engine(self, profile: str) -> Engine2:
        try:
            from engine2.journal import get_engine2, get_extra_engine2, get_routed_engine2
        except Exception:  # pragma: no cover
            from server.engine2.journal import get_engine2, get_extra_engine2, get_routed_engine2  # type: ignore
        cal = self.cal if profile == "rev2c" else None
        if self.extra_sel is not None:
            return get_extra_engine2(self.graph, self.extra_sel, self.routes_sel, calibration=cal)
        if self.routes_sel is not None:
            return get_routed_engine2(self.graph, self.routes_sel, calibration=cal)
        if cal is None:
            return get_engine2(self.graph)
        return _plain_cal_engine(self.graph, cal)


_PLAIN: dict = {}


def _plain_cal_engine(graph, cal):
    key = (id(graph), cal.digest)
    hit = _PLAIN.get(key)
    if hit is None or hit[0] is not graph:
        _PLAIN.clear()
        hit = (graph, Engine2(graph, calibration=cal))
        _PLAIN[key] = hit
    return hit[1]


# ------------------------------------------------------------- candidates
def _corridor_of(node_ids, nodes) -> str:
    chk = [n for n in node_ids if nodes[n].get("kind") == "chokepoint"]
    if "chk_suez" in chk:
        return "suez"
    if "chk_panama" in chk:
        return "panama"
    if "chk_capeofgoodhope" in chk:
        return "cape"
    return "other" if chk else "direct"


def _label(node_ids, nodes) -> str:
    chk = [n for n in node_ids if nodes[n].get("kind") == "chokepoint"]
    for key, name in (("chk_suez", "Suez Canal"), ("chk_panama", "Panama Canal"), ("chk_capeofgoodhope", "Cape of Good Hope")):
        if key in chk:
            return f"Via {name}"
    if not chk:
        return "Direct"
    names = [nodes[c]["name"] for c in chk[:3]]
    return "Via " + ", ".join(names) + (f" +{len(chk) - 3}" if len(chk) > 3 else "")


def _stitch(net: _Net, anchors):
    """Node path through the anchors (shortest chord path between consecutive anchors)."""
    path = [anchors[0]]
    for a, b in zip(anchors, anchors[1:]):
        hop = net.km_path(a, b)
        if hop is None:
            return None
        path.extend(hop[1][1:])
    return path


def _candidates(net: _Net, p: str, q: str, entries: dict, layers: _Layers, core):
    """Ordered candidate list: dicts with nodes (ids), basis, services, n_services."""
    cands: list[dict] = []
    seen_nodes: set = set()

    sigs: set = set()

    def add(nodes, basis, services=(), n=0, note=None):
        key = tuple(nodes)
        if not nodes or key in seen_nodes:
            return None
        # one route per chokepoint signature: a second path through the same
        # chokepoints in the same order is the same option on the map
        sig = tuple(g for g in nodes if net.nodes[g].get("kind") == "chokepoint")
        if sig in sigs:
            return None
        seen_nodes.add(key)
        sigs.add(sig)
        c = {"nodes": list(nodes), "basis": basis, "services": sorted(services), "n_services": n, "note": note}
        cands.append(c)
        return c

    entry = entries.get((p, q))
    primary = None
    if entry is not None:
        for var in entry["variants_seen"]:
            gates = [g for g in var["via"] if g in net.nodes and g not in (p, q)]
            nodes = _stitch(net, [p, *gates, q])
            if nodes is None:
                continue
            c = add(nodes, "services", services=[], n=var["n"])
            if c is not None and primary is None:
                primary = c          # variants_seen is ordered most services first: the engine's primary chain
        # service ids of the primary rotation set
        if primary is not None:
            primary["services"] = sorted(entry["services"])
        alt = entry.get("alt")
        if alt:
            nodes = _stitch(net, [p, *alt["via"], q])
            if nodes is not None:
                add(nodes, "services alternative" if alt.get("basis") == "service" else "alternative",
                    services=alt.get("services") or [], note=alt.get("basis"))
    # graph shortest path by days, and the alternates that avoid one place of it
    if entry is None:
        dp = net.days_path(p, q)
        if dp is not None:
            primary = add(list(dp[1]), "graph legs")
            try:
                from lane.routing import stress_test
            except Exception:  # pragma: no cover
                from server.lane.routing import stress_test  # type: ignore
            from types import SimpleNamespace
            sea_view = SimpleNamespace(nodes=core.nodes, edges=[e for e in core.edges if e["mode"] == "sea"])
            st = stress_test(sea_view, p, q)
            base_km = sum(net.leg_km(dp[1]))
            for alt in (st.get("alternates") or [])[:MAX_GRAPH_ALTERNATES]:
                if sum(net.leg_km(alt["places"])) <= MAX_DETOUR_RATIO * base_km:
                    add(list(alt["places"]), "graph legs avoiding a place")
    # corridor candidates through Suez, Panama and the Cape, when the legs exist
    have_gates = {g for c in cands for g in c["nodes"] if net.nodes[g].get("kind") == "chokepoint"}
    shortest = min((sum(net.leg_km(c["nodes"])) for c in cands), default=None)
    for x in CORRIDORS:
        if x in (p, q) or x in have_gates or x not in net.nodes:
            continue
        nodes = _stitch(net, [p, x, q])
        if nodes is None or len(set(nodes)) != len(nodes):
            continue
        km = sum(net.leg_km(nodes))
        ref = shortest if shortest is not None else None
        if ref is not None and km > MAX_DETOUR_RATIO * ref:
            continue
        add(nodes, "corridor")
    return cands, entry, primary


# ------------------------------------------------------- wait at arrival
_RUN_LOCK = threading.Lock()


def _run_with_series(eng, spec):
    """(state, sim) of one engine run, or (state, None) when the run's raw series cannot be read back safely.
    ``Engine2._last_sim`` is set by the run and is shared by every caller of a cached engine, so the read is
    taken under a lock and checked against the run it must belong to (same root, same step, same cut)."""
    with _RUN_LOCK:
        st = None
        for _ in range(3):
            st = eng.run("lane_plan", spec, {})
            sim = getattr(eng, "_last_sim", None)
            root = eng.ix.get(spec["node_id"])
            prof = sim.profiles.get(root) if sim is not None else None
            if (sim is not None and sim.root == root and abs(float(sim.dt) - float(st.dt)) < 1e-12 and prof
                    and abs(prof[0][2] - float(spec["duration_d"])) < 1e-9
                    and abs(prof[0][1] - float(spec["capacity_loss"])) < 1e-12):
                return st, sim
        return st, None


def _arrival_fns(eng, st, sim) -> dict:
    """{place id: f(t_days) -> wait in days} for the places that have a queue or congestion event, from the FIFO
    wait of the units arriving at each recorded step (the series the run's own peak is the maximum of)."""
    out = {}
    if sim is None:
        return out
    qs = float(st.params.get("queue_sensitivity", 1.0))
    dt = float(sim.dt)
    for ev in st.events:
        if ev["channel"] not in ("queue", "congestion") or ev.get("delay_d") is None:
            continue
        nid = ev["node_id"]
        i = eng.ix.get(nid)
        if i is None or nid in out:
            continue
        waits = dict(eng._fifo_waits(sim.series[i] or [], dt))

        def f(t, waits=waits, i=i, dt=dt, quant=eng._quant, qs=qs):
            k = int(math.floor(t / dt + 1e-9))
            return quant(i, waits.get(k, 0.0)) * qs
        out[nid] = f
    return out


def _arrival_block(ids, nodes, leg_days, sail, arr_by_vid, base_vid):
    """queue_at_arrival, its per-place rows and total_at_arrival for one route.

    ``leg_days`` {low, median, high}: days of each leg at the fast, base and slow speed variants;
    ``sail`` {low, median, high}: the route's sail days; ``arr_by_vid`` {variant id: {place: f(t)}}.
    A vessel reaches the place at index j on the sum of the days of the legs before it; its wait there is
    the wait of the units that reach the place on that day in that run of the engine."""
    cols = ("low", "median", "high")
    arrive = {c: [sum(leg_days[c][:j]) for j in range(len(ids))] for c in cols}
    combos = []                                     # (speed column, queue variant, route wait, per-place waits)
    for c in cols:
        for vid, fns in arr_by_vid.items():
            per = {n: fns[n](arrive[c][j]) for j, n in enumerate(ids) if n in fns}
            combos.append((c, vid, max(per.values(), default=0.0), per))
    base = next((x for x in combos if x[0] == "median" and x[1] == base_vid), None)
    waits = [x[2] for x in combos]
    tot = [sail[c] + w for c, _v, w, _p in combos]
    med_w = base[2] if base else 0.0
    qa = {"low": _r(min(waits)), "median": _r(med_w), "high": _r(max(waits)), "unit": "d", "label": "SIMULATED",
          "measure": "at_arrival", "sources": ["engine2.run"],
          "rule": QUEUE_RULE_ARRIVAL}
    ta = {"low": _r(min(tot)), "median": _r(sail["median"] + med_w), "high": _r(max(tot)), "unit": "d",
          "label": "SIMULATED" if max(waits) > 0 else "MODELLED", "measure": "at_arrival",
          "sources": ["engine2.run"], "rule": QUEUE_RULE_ARRIVAL}
    rows = []
    for j, n in enumerate(ids):
        pw = [x[3].get(n, 0.0) for x in combos if n in x[3]]
        if not pw or max(pw) <= 0.0:
            continue
        bm = base[3].get(n, 0.0) if base else 0.0
        rows.append({"place_id": n, "name": nodes[n]["name"],
                     "arrival_d": {c: _r(arrive[c][j]) for c in cols},
                     "wait_d": {"low": _r(min(pw)), "median": _r(bm), "high": _r(max(pw))}})
    return qa, rows, ta


# ------------------------------------------------------------- the plan
def plan(graph, body, scenarios=None) -> dict:
    inp = parse_body(body, scenarios)
    p, q = inp["origin"], inp["destination"]
    nodes_all = graph.nodes
    for k, pid in (("origin", p), ("destination", q)):
        if pid not in nodes_all:
            raise LaneRefusal("unknown_place", f"unknown {k} place: {pid!r}", place_id=pid)
    if p == q:
        raise LaneRefusal("same_place", "origin and destination must differ", place_id=p)
    for k, pid in (("origin", p), ("destination", q)):
        n = nodes_all[pid]
        if n["mode"] != "sea":
            raise LaneRefusal("not_a_sea_place", f"{k} {n['name']} is a {n['mode']} place; the lane planner serves sea places",
                              place_id=pid)
    anchors: dict = {}
    for k, pid in (("origin", p), ("destination", q)):
        n = nodes_all[pid]
        if n.get("tier", "core") != "core":
            a = shell_anchor(graph, pid)
            if not inp.get("shell_access"):
                nb = sorted({e["to"] if e["from"] == pid else e["from"] for e in graph.edges
                             if pid in (e["from"], e["to"]) and e["mode"] == "sea"
                             and nodes_all[e["to"] if e["from"] == pid else e["from"]].get("tier", "core") == "core"})
                extra_r = {"via": {"id": a["id"], "name": a["name"], "km": _r(a["km"], 1)}} if a else {}
                raise LaneRefusal("shell_endpoint",
                                  f"{k} {n['name']} is a shell place: the engine holds no state for it",
                                  place_id=pid, core_neighbours=[{"id": x, "name": nodes_all[x]["name"]} for x in nb[:8]],
                                  **extra_r)
            if a is None:
                raise LaneRefusal("shell_no_core_leg",
                                  f"{k} {n['name']} is a shell place with no sea leg to a core place: it cannot be served",
                                  place_id=pid)
            anchors[k] = {**a, "place_id": pid}
    p0, q0 = p, q                                 # the places asked for; p and q become the places the engine can route
    if "origin" in anchors:
        p = anchors["origin"]["id"]
    if "destination" in anchors:
        q = anchors["destination"]["id"]
    if p == q:
        a_any = anchors.get("origin") or anchors.get("destination")
        via = {"id": a_any["id"], "name": a_any["name"]}
        if "origin" in anchors and "destination" in anchors:
            raise LaneRefusal("shell_same_anchor",
                              "origin and destination are shell places that reach the same core place; there is no lane between them to plan",
                              origin=p0, destination=q0, via=via)
        raise LaneRefusal("shell_anchor_is_endpoint",
                          f"the core place that serves the shell place is the other endpoint ({a_any['name']}); there is no lane between them to plan",
                          origin=p0, destination=q0, via=via)

    layers = _Layers(graph, inp["extra"])
    eng_base = layers.engine("rev2")
    merged = getattr(eng_base, "merged_graph", None) or graph
    core = core_subgraph(merged)
    net = _Net(core)
    for pid_s in (p0, q0):
        if pid_s not in (p, q):
            net.add_place(nodes_all[pid_s])
    if net.km_path(p, q) is None:
        raise LaneRefusal("no_sea_path", f"no sea path between {nodes_all[p]['name']} and {nodes_all[q]['name']} over the served legs",
                          origin=p, destination=q)
    table = getattr(eng_base, "routes", None)
    entries = {(e["from"], e["to"]): e for e in (table.entries if table is not None else [])}
    cands, entry, primary = _candidates(net, p, q, entries, layers, core)
    pre = [p0] if p0 != p else []                 # the access legs of a shell endpoint are the first and last legs
    post = [q0] if q0 != q else []
    if pre or post:
        for c in cands:
            c["nodes"] = pre + c["nodes"] + post

    # ---- time basis: days per km of the leg itself (the engine's basis)
    if (p, q) in net.days and entry is not None:
        leg_tau = net.days[(p, q)]
        ref_km = float(entry["route_km"])
        basis = {"kind": "service leg", "leg": f"{p}->{q}", "leg_days": _r(leg_tau), "route_km": _r(ref_km, 1)}
    elif (p, q) in net.days:
        leg_tau = net.days[(p, q)]
        ref_km = net.km_path(p, q)[0]
        basis = {"kind": "graph leg", "leg": f"{p}->{q}", "leg_days": _r(leg_tau), "route_km": _r(ref_km, 1)}
    else:
        dp = net.days_path(p, q)
        leg_tau = dp[0] if dp else None
        ref_km = sum(net.leg_km(dp[1])) if dp else None
        basis = {"kind": "graph legs", "leg": "->".join(dp[1]) if dp else None, "leg_days": _r(leg_tau), "route_km": _r(ref_km, 1)}
    if not leg_tau or not ref_km:
        raise LaneRefusal("no_sea_path", "no leg time basis between the places", origin=p, destination=q)
    dpk = leg_tau / ref_km
    basis["days_per_km"] = round(dpk, 9)
    basis["implied_speed_kn"] = _r(1.0 / dpk / 24.0 / 1.852, 2)

    # ---- engine runs under the disruption (named variants)
    dis = inp["disruption"]
    q_variants, waits_by_variant, run_info = [], {}, []
    arr_by_vid: dict = {}
    if dis is not None:
        profiles = ["rev2"] + (["rev2c"] if layers.cal is not None else [])
        for profile in profiles:
            eng = layers.engine(profile)
            for mult in DURATION_MULTS:
                dur = dis["duration_d"] * mult
                if dur > DURATION_MAX_D:
                    continue
                spec = {"node_id": dis["place_id"], "kind": dis["kind"], "capacity_loss": dis["capacity_loss"],
                        "duration_d": dur, "shape": "step", "started_at": RUN_EPOCH}
                try:
                    st, sim = _run_with_series(eng, spec)
                except EngineError as exc:
                    code = ENGINE_ERRORS.get(getattr(exc, "code", None), "invalid_disruption")
                    raise LaneRefusal(code, str(exc), place_id=dis["place_id"]) from None
                vid = f"Q-{profile.upper()}-X{mult:g}"
                w: dict[str, float] = {}
                for ev in st.events:
                    if ev["channel"] in ("queue", "congestion") and ev.get("delay_d") is not None:
                        w[ev["node_id"]] = max(w.get(ev["node_id"], 0.0), float(ev["delay_d"]))
                waits_by_variant[vid] = w
                arr_by_vid[vid] = {n: f for n, f in _arrival_fns(eng, st, sim).items() if n in w}
                q_variants.append({"id": vid, "profile": profile, "duration_mult": mult, "duration_d": _r(dur),
                                   "base": profile == "rev2" and mult == 1.0,
                                   "stream_events": len(st.events)})
        dnode = nodes_all.get(dis["place_id"])
        if dnode is None:
            raise LaneRefusal("unknown_place", f"unknown disruption place: {dis['place_id']!r}", place_id=dis["place_id"])

    base_vid = "Q-REV2-X1"
    f_fast, f_base, f_slow = (BASE_KN / kn for _id, kn in SAIL_VARIANTS)

    routes = []
    for c in cands:
        ids = c["nodes"]
        legs_km = net.leg_km(ids)
        km = sum(legs_km)
        sail = km * dpk
        gate_ids = [n for n in ids if net.nodes[n].get("kind") == "chokepoint"]
        r = {
            "label": _label(ids, net.nodes), "corridor": _corridor_of(ids, net.nodes), "basis": c["basis"],
            "services": c["services"], "n_services": c["n_services"], "note": c["note"],
            "km": {"value": _r(km, 1), "label": "MODELLED", "sources": ["legs"]},
            "places": [{**_place_row(net.nodes[n]), "role": ("origin" if i == 0 else "destination" if i == len(ids) - 1
                                                            else "gate" if n in gate_ids else "waypoint")}
                      for i, n in enumerate(ids)],
            "legs": [{"from": a, "to": b, "km": _r(k, 1), "days": _r(k * dpk)}
                     for (a, b), k in zip(zip(ids, ids[1:]), legs_km)],
            "gates": [{"id": g, "name": net.nodes[g]["name"]} for g in gate_ids],
            "floor_ok": km + 1e-6 >= net.chord(p0, q0),
            "_ids": ids, "_sail": sail, "_km": km, "_legs_km": legs_km,
        }
        r["sail"] = {"low": _r(sail * f_fast), "median": _r(sail * f_base), "high": _r(sail * f_slow),
                     "unit": "d", "label": "MODELLED", "sources": ["legs", "sim.speeds"]}
        routes.append(r)

    # baseline: the primary service chain of a direct leg, else the shortest by days
    if primary is not None:
        base_nodes = primary["nodes"]
    else:
        base_nodes = min(routes, key=lambda r: (r["_sail"], r["label"]))["_ids"]
    for r in routes:
        r["is_baseline"] = r["_ids"] == base_nodes
    order = sorted(routes, key=lambda r: (r["_sail"], r["label"], r["_ids"]))
    # distinct labels
    counts: dict[str, int] = {}
    for r in order:
        counts[r["label"]] = counts.get(r["label"], 0) + 1
    seen_l: dict[str, int] = {}
    for i, r in enumerate(order, 1):
        r["id"] = f"r{i}"
        if counts[r["label"]] > 1:
            seen_l[r["label"]] = seen_l.get(r["label"], 0) + 1
            r["label"] = f"{r['label']} ({seen_l[r['label']]})"
    routes = order
    baseline = next(r for r in routes if r["is_baseline"])

    # ---- queue and totals
    closure = None
    if dis is not None:
        closure = {
            "place_id": dis["place_id"], "name": nodes_all[dis["place_id"]]["name"], "kind": dis["kind"],
            "capacity_loss": _r(dis["capacity_loss"], 4), "scenario_id": dis["scenario_id"],
            "duration_d": _r(dis["duration_d"]), "start_d": 0.0,
            # the band of the closure end is the band of the variants that were RUN: a x2 variant
            # beyond DURATION_MAX_D is skipped above and must not be claimed here
            "end_d": {"low": min(v["duration_d"] for v in q_variants),
                      "median": _r(dis["duration_d"]),
                      "high": max(v["duration_d"] for v in q_variants)},
            "label": "MODELLED"}
    for r in routes:
        ids, sail = r["_ids"], r["_sail"]
        if dis is None:
            r["queue"] = None
            r["total"] = dict(r["sail"])
            r["queue_by_place"] = []
            r["queue_at_arrival"], r["queue_at_arrival_by_place"], r["total_at_arrival"] = None, [], None
            r["disrupted_place_on_route"] = False
        else:
            per_v = {}
            for vid, w in waits_by_variant.items():
                per_v[vid] = max((w.get(n, 0.0) for n in ids), default=0.0)
            vals = list(per_v.values())
            qb = per_v.get(base_vid, 0.0)
            ql, qh = min(vals), max(vals)
            r["queue"] = {"low": _r(ql), "median": _r(qb), "high": _r(qh), "unit": "d", "label": "SIMULATED",
                          "sources": ["engine2.run", "services"] if layers.states["services"] == "SERVED" else ["engine2.run"],
                          "measure": "run_peak", "rule": QUEUE_RULE_PEAK}
            r["total"] = {"low": _r(sail * f_fast + ql), "median": _r(sail * f_base + qb), "high": _r(sail * f_slow + qh),
                          "unit": "d", "label": "SIMULATED" if qh > 0 else "MODELLED",
                          "sources": sorted(set(r["sail"]["sources"]) | set(r["queue"]["sources"]))}
            wb = waits_by_variant.get(base_vid, {})
            r["queue_by_place"] = [{"place_id": n, "name": net.nodes[n]["name"], "wait_d": _r(wb[n]),
                                    "low": _r(min(waits_by_variant[v].get(n, 0.0) for v in waits_by_variant)),
                                    "high": _r(max(waits_by_variant[v].get(n, 0.0) for v in waits_by_variant))}
                                   for n in ids if wb.get(n, 0.0) > 0.0]
            r["disrupted_place_on_route"] = dis["place_id"] in ids
            sail_c = {"low": sail * f_fast, "median": sail * f_base, "high": sail * f_slow}
            leg_c = {"low": [k * dpk * f_fast for k in r["_legs_km"]], "median": [k * dpk * f_base for k in r["_legs_km"]],
                     "high": [k * dpk * f_slow for k in r["_legs_km"]]}
            r["queue_at_arrival"], r["queue_at_arrival_by_place"], r["total_at_arrival"] = _arrival_block(
                ids, net.nodes, leg_c, sail_c, arr_by_vid, base_vid)
        # voyage timeline: sail legs between gates and one queue block at the binding place
        r["timeline"] = _timeline(r, net, dpk, (f_fast, f_base, f_slow), dis, waits_by_variant, base_vid)
    base_med = baseline["sail"]["median"]
    for r in routes:
        r["vs_baseline"] = {"unit": "d", "label": r["total"]["label"],
                            "low": _r(r["total"]["low"] - baseline["sail"]["low"]),
                            "median": _r(r["total"]["median"] - base_med),
                            "high": _r(r["total"]["high"] - baseline["sail"]["high"])}

    # ---- lane geometry: additive fields per route. The old fields above stay as they were;
    # these carry the lane polyline, the lane-based length and the days computed from lane nautical miles.
    lane_ctx = lane_mod.context()
    if lane_ctx is not None:
        for r in routes:
            lane_mod.attach(r, net, lane_ctx, dis, waits_by_variant, base_vid, _timeline,
                            arrival_fn=(lambda ids, leg_c, sail_c: _arrival_block(
                                ids, net.nodes, leg_c, sail_c, arr_by_vid, base_vid)) if dis is not None else None)
        lane_mod.vs_baseline(routes, baseline)
    checks = _distance_checks(routes, p0, q0, nodes_all, net, shell=bool(anchors))
    shell_block = None
    if anchors:
        shell_block = _shell_block(anchors)
        checks.append(_shell_check(shell_block))

    for r in routes:
        for k in ("_ids", "_sail", "_km", "_legs_km"):
            r.pop(k, None)

    used = _sources(layers, bool(dis), bool(q_variants), checks)
    if shell_block is not None:
        used.append({"id": "shell_access", "label": "Shell access legs", "state": "MODELLED",
                     "detail": "nearest connected core place, leg from the graph; no wait simulated at the shell place"})
    if lane_ctx is not None:
        used += [{"id": "sea_routes", "label": "Sea lane network", "state": "MODELLED",
                  "detail": "lane nautical miles per sea leg (hash-pinned sidecar)"},
                 {"id": "lane_geometry", "label": "Lane geometry", "state": "MODELLED",
                  "detail": "smoothed lane polylines checked against 1:50m land"}]
    journal = {
        "schema": SCHEMA, "inputs_sha256": _sha({"schema": SCHEMA, "inputs": inp}),
        "graph_hash": graph.hash,
        "services": ({**layers.routes_sel, "table_sha256": getattr(table, "digest", None)}
                     if layers.routes_sel is not None else None),
        "extra": ({"file_sha256": layers.extra_sel["file_sha256"], "lane_families": layers.extra_sel.get("lane_families"),
                   "table_sha256": getattr(table, "digest", None),
                   "merged_graph_sha256": getattr(eng_base.merged_graph, "hash", None)}
                  if layers.extra_sel is not None else None),
        "lane_geometry": lane_mod.digest(lane_ctx),
        "engine2_registry_hash": PLUGINS_HASH,
        "calibration_digest": layers.cal.digest if layers.cal is not None else None,
        "variants": {"sail": [v for v, _ in SAIL_VARIANTS], "queue": [v["id"] for v in q_variants]},
        "queue_measures": ["run_peak", "at_arrival"],
    }
    out = {
        "schema": SCHEMA,
        "plan_id": "lp_" + _sha(journal)[:16],
        "engine_label": ENGINE_LABEL,
        "inputs": inp,
        "journal": journal,
        "places": {"origin": _place_row(nodes_all[p0]), "destination": _place_row(nodes_all[q0])},
        "shell_access": shell_block,
        "states": layers.states,
        "variants": {
            "sail": [{"id": v, "speed_kn": kn, "factor": round(BASE_KN / kn, 6), "label": "MODELLED",
                      "sources": ["sim.speeds"]} for v, kn in SAIL_VARIANTS],
            "queue": q_variants,
            "rule": "LOW is the smallest and HIGH the largest over the named variants; MEDIAN is the BASE variant",
            "base": {"sail": "SPEED-BASE", "queue": base_vid},
        },
        "queue_measures": QUEUE_MEASURES,
        "time_basis": basis,
        "lane_basis": lane_mod.basis(lane_ctx),
        "baseline": {"route_id": baseline["id"], "label": baseline["label"], "sail": baseline["sail"]},
        "disruption": closure,
        "routes": routes,
        "checks": checks,
        "sources": used,
    }
    return out


def _timeline(r, net, dpk, factors, dis, waits_by_variant, base_vid):
    ids, legs_km = r["_ids"], r["_legs_km"]
    cols = ("low", "median", "high")
    stops = [i for i, n in enumerate(ids) if i in (0, len(ids) - 1) or net.nodes[n].get("kind") == "chokepoint"]
    # binding place of the queue: the place with the largest base wait, else the largest at the highest variant
    bind = None
    if dis is not None and waits_by_variant:
        for vid in (base_vid, max(waits_by_variant, key=lambda v: (max((waits_by_variant[v].get(n, 0.0) for n in ids), default=0.0), v))):
            w = waits_by_variant.get(vid, {})
            best = max(((w.get(n, 0.0), -i, n) for i, n in enumerate(ids)), default=(0.0, 0, None))
            if best[0] > 0:
                bind = best[2]
                break
    q = r["queue"]
    segs = []
    pos = {c: 0.0 for c in cols}
    for a, b in zip(stops, stops[1:]):
        km = sum(legs_km[a:b])
        d = km * dpk
        seg = {"kind": "sail", "from": ids[a], "to": ids[b], "km": _r(km, 1),
               "d": {c: _r(d * f) for c, f in zip(cols, factors)}}
        seg["start"] = {c: _r(pos[c]) for c in cols}
        for c, f in zip(cols, factors):
            pos[c] += d * f
        seg["end"] = {c: _r(pos[c]) for c in cols}
        segs.append(seg)
        if bind is not None and ids[b] == bind and q is not None and q["high"] > 0:
            qs = {"kind": "queue", "place_id": bind, "name": net.nodes[bind]["name"],
                  "d": {c: q[c] for c in cols}, "start": {c: _r(pos[c]) for c in cols}}
            for c in cols:
                pos[c] += q[c]
            qs["end"] = {c: _r(pos[c]) for c in cols}
            segs.append(qs)
    if bind is not None and q is not None and q["high"] > 0 and not any(s["kind"] == "queue" for s in segs):
        # the binding place is an interior waypoint that is not a gate stop: queue at the end of its approach
        segs.append({"kind": "queue", "place_id": bind, "name": net.nodes[bind]["name"],
                     "d": {c: q[c] for c in cols}, "start": {c: _r(pos[c]) for c in cols},
                     "end": {c: _r(pos[c] + q[c]) for c in cols}})
    return segs


def _north_europe(n) -> bool:
    return n["lat"] >= 48.0 and -12.0 <= n["lon"] <= 30.0


def _east_of_suez(n) -> bool:
    return n["lon"] >= 43.0 and n["lon"] <= 180.0


def _shell_block(anchors: dict) -> dict:
    out = {"label": "MODELLED", "limit_km": SHELL_ACCESS_LIMIT_KM,
           "rule": ("a shell place is served through the nearest core place the graph joins to it by a sea leg; that leg is "
                    "added to every route as the first or last leg; no wait is simulated at the shell place"),
           "origin": None, "destination": None}
    for k, a in anchors.items():
        out[k] = {"place_id": a["place_id"], "via": {"id": a["id"], "name": a["name"]}, "km": _r(a["km"], 1),
                  "graph_leg_days": _r(a["leg_days"]), "label": "MODELLED", "wait": "NOT SERVED"}
    return out


def _shell_check(block: dict) -> dict:
    rows = [{"role": k, "place_id": block[k]["place_id"], "via": block[k]["via"]["id"], "km": block[k]["km"]}
            for k in ("origin", "destination") if block.get(k)]
    over = any(r["km"] > SHELL_ACCESS_LIMIT_KM for r in rows)
    return {"id": "shell_access", "state": "ACCESS LEG OVER LIMIT" if over else "WITHIN LIMIT", "flag": bool(over),
            "limit_km": SHELL_ACCESS_LIMIT_KM, "access": rows, "label": "MODELLED", "sources": ["legs"]}


def _distance_checks(routes, p, q, nodes, net, shell=False) -> list:
    out = []
    suez = [r for r in routes if r["corridor"] == "suez"]
    cape = [r for r in routes if r["corridor"] == "cape"]
    if suez and cape:
        s = min(suez, key=lambda r: r["km"]["value"])
        c = min(cape, key=lambda r: r["km"]["value"])
        s_km, c_km = s["km"]["value"], c["km"]["value"]
        cut = (c_km - s_km) / c_km * 100.0 if c_km else None
        a, b = nodes[p], nodes[q]
        ref = refs.PAIR_REFS.get((p, q)) or refs.PAIR_REFS.get((q, p))
        scope = "pair"
        if shell:
            ref = None          # a cited range is for the core pair; these routes carry access legs
        elif ref is None and ((_north_europe(b) and _east_of_suez(a)) or (_north_europe(a) and _east_of_suez(b))):
            ref, scope = refs.CORRIDOR_REF, "corridor"
        row = {"id": "cape_vs_suez", "suez_route_id": s["id"], "cape_route_id": c["id"],
               "engine": {"suez_km": s_km, "cape_km": c_km, "detour_km": _r(c_km - s_km, 1),
                          "ratio": _r(c_km / s_km, 4), "cut_pct": _r(cut, 2), "label": "MODELLED"},
               "reference": None, "state": "NO CITED RANGE", "flag": False, "sources": ["legs"]}
        if ref is not None:
            lo, hi = ref["cut_pct"]["lo"], ref["cut_pct"]["hi"]
            outside = cut is not None and not (lo <= cut <= hi)
            row["reference"] = {"id": ref["id"], "scope": scope, "label": ref["label"],
                                "cut_pct": {"lo": _r(lo, 2), "hi": _r(hi, 2)}, "label_state": "SOURCED",
                                "source": refs.UNCTAD_ID,
                                "ras_tanura": {"suez_km": refs.RAS_TANURA_SUEZ_KM, "cape_km": refs.RAS_TANURA_CAPE_KM}}
            row["state"] = "OUTSIDE CITED RANGE" if outside else "WITHIN CITED RANGE"
            row["flag"] = bool(outside)
            row["sources"] = ["legs", refs.UNCTAD_ID]
        lane_row = lane_mod.check(s, c, ref, scope, refs, getattr(lane_mod.context(), "sea", None),
                                  None if shell else p, q)
        if lane_row is not None:
            row["lane"] = lane_row
        out.append(row)
    floor = [{"route_id": r["id"], "ok": r["floor_ok"]} for r in routes]
    out.append({"id": "great_circle_floor", "routes": floor, "flag": not all(f["ok"] for f in floor),
                "state": "WITHIN" if all(f["ok"] for f in floor) else "BELOW GREAT CIRCLE",
                "engine": {"great_circle_km": _r(net.chord(p, q), 1), "label": "MODELLED"}, "sources": ["legs"]})
    return out


def _sources(layers, has_disruption, has_queue, checks) -> list:
    s = [{"id": "legs", "label": "Leg data v1.0", "state": "MODELLED",
          "detail": "base transit days per leg, place coordinates, chord geometry"},
         {"id": "sim.speeds", "label": "Sim sea speed classes", "state": "MODELLED",
          "detail": "16, 19, 21, 23 kn; 20 kn service speed of generated legs"},
         {"id": "services", "label": "Through services",
          "state": "MODELLED" if layers.states["services"] == "SERVED" else "NOT SERVED",
          "detail": "rotations SOURCED, placement MODELLED"},
         {"id": "extra", "label": "Extra legs",
          "state": {"SERVED": "MODELLED", "OFF": "NOT SERVED"}.get(layers.states["extra"], "NOT SERVED"),
          "detail": "two-way Red Sea corridor, Panama and Cape lanes"}]
    if has_disruption:
        s.append({"id": "engine2.run", "label": "ENGINE REV 2 runs", "state": "SIMULATED",
                  "detail": "queue and congestion wait per place, named variants"})
    if any(c.get("reference") for c in checks):
        s.append({**refs.UNCTAD, "id": refs.UNCTAD_ID, "label": refs.UNCTAD["title"], "detail": "Suez and Cape route lengths"})
    return s


def to_bytes(doc: dict) -> bytes:
    """Byte-stable serialisation: sorted keys, fixed separators, UTF-8."""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
