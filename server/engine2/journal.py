"""ENGINE REV 2 journal helpers (replay from a journal alone).

A rev-2 journal carries engine_rev=2, the plug-in registry hash it was
produced under, and (for forks) the embedded parent journal. Replay
rebuilds the parent state by re-running its journal, then re-forks, so a
fork chain of any depth reproduces byte-identically with no store reads.
"""
from __future__ import annotations

from .engine import Engine2, RunState2
from .fork import fork_events2
from .plugins import ENGINE_LABEL, ENGINE_REV, PLUGINS_HASH

_CACHE: dict = {}


def get_engine2(graph) -> Engine2:
    """One Engine2 per graph object (built once; construction is cheap)."""
    key = id(graph)
    hit = _CACHE.get(key)
    if hit is None or hit[0] is not graph:
        _CACHE.clear()
        hit = (graph, Engine2(graph))
        _CACHE[key] = hit
    return hit[1]


def rev_stamp() -> dict:
    return {"engine_rev": ENGINE_REV, "engine_label": ENGINE_LABEL,
            "engine2_registry_hash": PLUGINS_HASH}


# ---- routes table from the graph services layer (opt-in) -----------------
_ROUTED: dict = {}
_ROUTES_KEYS = ("file_sha256", "eras", "include_unverified", "include_unstated")


def _routes_selection(routes_journal: dict) -> dict:
    return {k: routes_journal[k] for k in _ROUTES_KEYS}


def get_routed_engine2(graph, selection: dict, calibration=None) -> Engine2:
    """One routes-augmented Engine2 per (graph object, services selection
    [, calibration profile])."""
    import json

    from engine.engine import core_subgraph

    from .plugins import plug
    from .routes import build_routes_table
    try:
        from graph_services.runtime import route_services
    except Exception:  # imported as server.engine2.* from the repo root
        from server.graph_services.runtime import route_services  # type: ignore
    key = (id(graph), json.dumps(selection, sort_keys=True),
           getattr(calibration, "digest", None))
    hit = _ROUTED.get(key)
    if hit is not None and hit[0] is graph:
        return hit[1]
    r = route_services(graph.raw, graph.hash, selection)
    table = build_routes_table(core_subgraph(graph), r["od"], r["meta"], plug("teu_per_call"))
    eng = Engine2(graph, routes=table, calibration=calibration)
    if len(_ROUTED) >= 8:
        _ROUTED.clear()
    _ROUTED[key] = (graph, eng)
    return eng


# ---- ENGINE REV 2 profile d (opt-in): sea-lane geometry, gates, route choice, rotations ------
_PROFILED: dict = {}


def get_profile_engine2(graph, selection: dict | None = None, profile: str = "d",
                        ablate=()) -> Engine2:
    """One profile-d Engine2 per (graph object, optional services selection for the chokepoint
    chains). The profile ALWAYS reads the services layer for rotation coupling and port
    substitution (call order only); ``selection`` additionally binds legs to chokepoint chains."""
    import json

    from .plugins import plug
    from .profile_d import ProfileD
    from .rotation import leg_zeta, raw_zeta
    from .routes_d import build_routes_table_d
    from .sea_routes import load_sea_routes, retime_graph
    try:
        from graph_services.loader import load_services
        from graph_services.runtime import route_services
    except Exception:  # imported as server.engine2.*
        from server.graph_services.loader import load_services  # type: ignore
        from server.graph_services.runtime import route_services  # type: ignore
    if profile not in ("d", "e"):
        raise ValueError(f"unknown engine 2 profile: {profile!r}")
    ablate = tuple(sorted(set(ablate)))
    key = (id(graph), json.dumps(selection, sort_keys=True), ablate) if profile == "d" else \
        (id(graph), json.dumps(selection, sort_keys=True), ablate, profile)
    hit = _PROFILED.get(key)
    if hit is not None and hit[0] is graph:
        return hit[1]
    sea = load_sea_routes()
    svc = load_services(graph.raw, graph.hash)
    retimed = retime_graph(graph, sea)
    od, meta = {}, {"file_sha256": svc.file_sha256, "services_selected": 0, "service_ids": []}
    if selection:
        r = route_services(graph.raw, graph.hash, selection)
        od, meta = r["od"], r["meta"]
    table = build_routes_table_d(retimed, od, meta, plug("teu_per_call"), sea, svc.entries, ablate)
    zeta = leg_zeta(raw_zeta(svc.entries, graph.raw), table, retimed.nodes)
    if profile == "e":
        from .profile_e import ProfileE
        prof = ProfileE(sea=sea, zeta=zeta, ablate=ablate)
    else:
        prof = ProfileD(sea=sea, zeta=zeta, ablate=ablate)
    eng = Engine2(graph, routes=table, profile=prof)
    eng.profile_selection = selection
    eng.profile_services_sha256 = svc.file_sha256
    eng.profile_services_n = len(svc.entries)
    if len(_PROFILED) >= 8:
        _PROFILED.clear()
    _PROFILED[key] = (graph, eng)
    return eng


def profile_stamp(engine: Engine2) -> dict:
    """The journal-core keys of a profile-d run: label, registry hash, geometry hash, services
    file hash, the engine's profile digest and routes-table digest, and the chain selection."""
    st = {**engine.prof.stamp(), "profile_digest": engine.prof.digest,
          "services_file_sha256": engine.profile_services_sha256,
          "table_sha256": engine.routes.digest}
    if engine.profile_selection:
        st["routes"] = {"source": "services", **engine.profile_selection,
                        "table_sha256": engine.routes.digest}
    return st


def routes_stamp(engine: Engine2, selection: dict) -> dict:
    """The journal-core key of a routed run: the selection plus the table's digest."""
    return {"routes": {"source": "services", **selection, "table_sha256": engine.routes.digest}}


# ---- graph extra sidecar (opt-in) ------------------------------------------
_EXTRA: dict = {}
_EXTRA_KEYS = ("file_sha256", "view", "lane_families")


def _extra_selection(extra_journal: dict) -> dict:
    return {k: extra_journal[k] for k in _EXTRA_KEYS}


def get_extra_engine2(graph, extra_selection: dict, routes_selection: dict | None = None,
                      calibration=None) -> Engine2:
    """One Engine2 per (graph object, extra selection [, services routes
    selection] [, calibration profile]). The solver runs on the engine-rev-2
    view of graph + sidecar (core_ext places and legs added; shell dropped)
    and gains the sidecar's dormant reroute lanes; with a services routes
    selection the table is built on that merged core and the lanes are
    appended to it (flow conservation of the chains is the routes module's)."""
    import json

    from engine.engine import core_subgraph
    from engine.graph import Graph

    from .plugins import plug
    from .routes import RoutesTable, build_routes_table
    try:
        from graph_extra.runtime import engine2_inputs
        from graph_services.runtime import route_services
    except Exception:  # imported as server.engine2.* from the repo root
        from server.graph_extra.runtime import engine2_inputs  # type: ignore
        from server.graph_services.runtime import route_services  # type: ignore
    key = (id(graph), json.dumps(extra_selection, sort_keys=True),
           json.dumps(routes_selection, sort_keys=True) if routes_selection else None,
           getattr(calibration, "digest", None))
    hit = _EXTRA.get(key)
    if hit is not None and hit[0] is graph:
        return hit[1]
    raw, specs, meta = engine2_inputs(graph.raw, graph.hash, extra_selection)
    merged = Graph(raw)
    if routes_selection:
        r = route_services(graph.raw, graph.hash, routes_selection)
        base = build_routes_table(core_subgraph(merged), r["od"], r["meta"], plug("teu_per_call"))
        table = RoutesTable(base.entries, base.skipped, base.chain,
                            list(base.lane_specs) + specs,
                            {**base.meta, "graph_extra": meta}, base.audit)
    else:
        table = RoutesTable([], [], {}, specs, {"source": "graph_extra", "graph_extra": meta}, {})
    eng = Engine2(merged, routes=table, calibration=calibration)
    # ``full_graph`` stays the served graph (as for a routed engine), so a
    # fork chain that replays through this engine rebuilds from graph.json;
    # the merged graph the solver ran on is kept beside it.
    eng.merged_graph = merged
    eng.full_graph = graph
    if len(_EXTRA) >= 8:
        _EXTRA.clear()
    _EXTRA[key] = (graph, eng)
    return eng


def extra_stamp(engine: Engine2, selection: dict) -> dict:
    """The journal-core key of an extra run: the selection plus the digests
    of the lane table and of the merged graph the solver ran on."""
    return {"extra": {"source": "graph_extra", **selection, "table_sha256": engine.routes.digest,
                      "merged_graph_sha256": engine.merged_graph.hash}}


def engine_for(engine2: Engine2, journal: dict) -> Engine2:
    """The engine a journal must replay under: the extra one when the
    journal carries an ``extra`` key, the routed one when it carries a
    ``routes`` key (refused if the table it was run under cannot be rebuilt
    identically), else ``engine2`` itself."""
    if journal.get("profile"):
        rj = journal.get("routes")
        eng = get_profile_engine2(engine2.full_graph, _routes_selection(rj) if rj else None,
                                  journal["profile"])
        for k, have in (("profile_digest", eng.prof.digest), ("table_sha256", eng.routes.digest),
                        ("services_file_sha256", eng.profile_services_sha256),
                        ("engine2_registry_hash", eng.prof.hash)):
            if journal.get(k) != have:
                raise ValueError(f"profile {journal['profile']} run was journaled under a different "
                                 f"{k}; it is not re-derived against other data")
        return eng
    rj = journal.get("routes")
    xj = journal.get("extra")
    if xj:
        eng = get_extra_engine2(engine2.full_graph, _extra_selection(xj),
                                _routes_selection(rj) if rj else None)
        if eng.routes.digest != xj.get("table_sha256") \
                or eng.merged_graph.hash != xj.get("merged_graph_sha256") \
                or (rj and eng.routes.digest != rj.get("table_sha256")):
            raise ValueError("extra tables differ from the ones this run was journaled under")
        return eng
    if not rj:
        return engine2
    eng = get_routed_engine2(engine2.full_graph, _routes_selection(rj))
    if eng.routes.digest != rj.get("table_sha256"):
        raise ValueError("routes table differs from the one this run was journaled under")
    return eng


def replay_journal2(engine2: Engine2, journal: dict, run_id: str) -> RunState2:
    if journal.get("engine_rev") != ENGINE_REV:
        raise ValueError("not an ENGINE REV 2 journal")
    engine2 = engine_for(engine2, journal)
    if journal["kind"] == "run":
        return engine2.run(run_id, journal["disruption"], journal["params"],
                           journal.get("node_overrides") or {})
    if journal["kind"] == "fork":
        pj = journal["parent_journal"]
        parent = replay_journal2(engine2, pj, journal["parent_run_id"])
        return fork_events2(engine_for(engine2, pj), parent, pj["disruption"], pj["params"],
                            journal["at_seq"], journal["overrides"], run_id,
                            parent_node_overrides=pj.get("node_overrides") or None)
    raise ValueError(f"unknown journal kind: {journal['kind']}")
