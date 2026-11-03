"""ENGINE REV 2 profile d: the ROUTES TABLE on real sea-lane distances, with generic alternatives.

Same construction and output type as routes.build_routes_table (the rev-2 table, which is NOT
edited): a graph sea leg p -> q that selected services sail through chokepoints g1..gn is replaced
by the chain p -> g1 -> ... -> gn -> q carrying the leg's attributed flow, and alternative lanes
are handed to the solver's reroute machinery. What differs (all of it data, no place ids):

  * distances are lane nautical miles from the sea-routes sidecar and days = nm / service speed;
    the chain's days sum to the days of the direct lane (hop distances are scaled by direct / sum);
  * the alternative of a gate is the sidecar lane that AVOIDS the gate's passage, through the
    chokepoint that lane passes and the primary does not (Cape for Suez, Suez for Panama, ...);
    it applies when the gate is in ``unconditional_gates`` (the rev-2 Suez / Bab el-Mandeb
    behaviour) or when a selected service witnesses it; lanes of a conditional alternative do
    not carry the place's exogenous through-traffic;
  * PORT SUBSTITUTION: for consecutive calls u -> b -> c of a service rotation the leg u -> b has
    the alternative "discharge at c" (an A lane u -> c and a B lane c -> b).

Pure function of (graph, sidecar, services, registry). Every number is in plugins_d.py.
"""
from __future__ import annotations

from engine.graph import edge_key

try:
    from graph_services.zones import zone
except Exception:  # pragma: no cover - imported as server.engine2.*
    from server.graph_services.zones import zone  # type: ignore

from .plugins_d import D_PLUG
from .routes import RoutesTable, _lam0
from .sea_routes import KM_PER_NM


def _witness_index(od: dict, coords: dict):
    """{(zone(a), b): [(via tuple, services)]} over every selected origin-destination route."""
    idx: dict = {}
    for (a, b), variants in od.items():
        za = zone(*coords[a]) if a in coords else None
        for via, info in variants.items():
            idx.setdefault((za, b), []).append((tuple(via), list(info["services"])))
    return idx


def build_routes_table_d(graph, od: dict, meta: dict, psi: float, sea, rotations: list,
                         ablate=()) -> RoutesTable:
    """``graph``: the retimed graph the solver runs on (all tiers); ``od``: graph_services
    od_routes result; ``sea``: sea_routes.SeaRoutes; ``rotations``: service entries for port
    substitution (call order only)."""
    nodes = graph.nodes
    coords = {n: (float(v["lat"]), float(v["lon"])) for n, v in nodes.items()}
    policy = D_PLUG("alternative_routes")
    uncond = dict(policy["unconditional_gates"])         # gate -> the named alternative node
    suffix = policy["unconditional_avoid_suffix"]
    no_alt = "alternatives" in ablate
    no_cond = "alternatives_conditional" in ablate
    subst = D_PLUG("port_substitution")
    max_feeder = float(subst["max_feeder_nm"])
    widx = _witness_index(od, coords)
    sea_edges = {(e["from"], e["to"]): e for e in graph.edges if e["mode"] == "sea"}
    bypassed = {e["bypass_of"] for e in graph.edges if e.get("bypass_of")}
    entries: list = []
    skipped: list = []
    chain_w: dict = {}
    chain_tau: dict = {}
    a_acc: dict = {}
    a_cond: set = set()
    b_acc: dict = {}
    alt_counts: dict = {}

    def witnessed_node(p, q, g, cands):
        """(alt node, witness services): among the chokepoints the avoid-the-gate lane passes
        (``cands``), the last one a selected service sails towards q without g, from the same sea
        zone as p, in the variant with the most services; (None, []) if no service does."""
        wv = [(v, s) for v, s in widx.get((zone(*coords[p]), q), [])
              if g not in v and set(v) & set(cands)]
        if not wv:
            return None, []
        v, s = max(wv, key=lambda vs: (len(vs[1]), vs[0]))
        return [c for c in v if c in set(cands)][-1], sorted(s)

    for (p, q), variants in sorted(od.items()):
        e = sea_edges.get((p, q))
        if e is None:
            continue
        key = edge_key(e)
        order = sorted(variants.items(), key=lambda kv: (-kv[1]["n"], kv[0]))
        via, info = order[0]
        gates: list = []
        for g in via:
            if g in (p, q) or (gates and gates[-1] == g):
                continue
            if g in nodes and nodes[g].get("kind") == "chokepoint":
                gates.append(g)
        if not gates:
            continue
        if e.get("bypass_of") or key in bypassed:
            skipped.append({"leg": key, "reason": "leg is a bypass or has authored bypasses"})
            continue
        anchors = [p] + gates + [q]
        hops: list = []
        ok = True
        for x, y in zip(anchors, anchors[1:]):
            d = sea.nm(x, y)
            if d is None:
                skipped.append({"leg": key, "reason": f"no sea route {x} -> {y}"})
                ok = False
                break
            hops.append(d)
        if not ok:
            continue
        r_hops = sum(hops)
        if r_hops <= 0:
            skipped.append({"leg": key, "reason": "zero route length"})
            continue
        direct = sea.nm(p, q)
        r_p = direct if direct else r_hops
        s_p = r_p / r_hops                                  # hops scaled to the direct lane
        days = lambda nm, _r=r_p: sea.days(nm, _r)          # noqa: E731 - speed class of the route
        leg_tau = float(e["base_transit_d"])
        w_add = float(e["capacity_share"]) * _lam0(nodes[p], psi)
        chain = []
        for (x, y), d in zip(zip(anchors, anchors[1:]), hops):
            chain_w[(x, y)] = chain_w.get((x, y), 0.0) + w_add
            chain_tau.setdefault((x, y), days(d * s_p))
            chain.append({"from": x, "to": y, "nm": round(d * s_p, 3), "days": round(days(d * s_p), 6)})
        # ---- the alternative lane of the first gate that has one
        alt = None
        prim = sea.pair(p, q)
        for g in ([] if no_alt else gates):
            pas = sea.gate_passages.get(g)
            unconditional = g in uncond
            if pas is None or (no_cond and not unconditional):
                continue
            # an unconditional gate has a NAMED alternative node and the lane that also avoids the other
            # interoceanic canal (rev 2: Suez / Bab el-Mandeb -> the Cape); any other gate takes the lane
            # that avoids its own passage and needs a service witness for the node that lane passes
            a = sea.alt(p, q, pas + (suffix if unconditional else ""))
            if a is None:
                continue
            on_primary = set(sea.chokepoints(p, q))
            cands = [c for c in a["chokepoints"]
                     if c not in on_primary and c not in gates and c in nodes and c not in (p, q)
                     and sea.nm(p, c) is not None and sea.nm(c, q) is not None]
            if unconditional:
                cands = [c for c in cands if c == uncond[g]]
            if not cands:
                continue
            if unconditional:
                alt_node, wit = min(cands, key=lambda c: (sea.nm(p, c) + sea.nm(c, q), c)), []
            else:
                alt_node, wit = witnessed_node(p, q, g, cands)
                if alt_node is None:
                    continue
            k_pc, k_cq = sea.nm(p, alt_node), sea.nm(alt_node, q)
            s_a = a["nm"] / (k_pc + k_cq)
            prim_pas = set((prim or {}).get("passages", []))
            avoided = {g2 for g2 in gates
                       if sea.gate_passages.get(g2) in prim_pas - set(a.get("passages", []))}
            avoided.add(g)
            cape_like = [(v, i) for v, i in order if alt_node in v]
            alt = {"via": [alt_node], "route_nm": round(a["nm"], 3),
                   "route_km": round(a["nm"] * KM_PER_NM, 3),
                   "route_days": round(days(a["nm"]), 6),
                   "added_days": round(max(0.0, days(a["nm"]) - days(r_p)), 6),
                   "added_days_raw": round(days(a["nm"]) - days(r_p), 6),
                   "gate": g,
                   "basis": ("service" if cape_like else
                             ("MODELLED (unconditional gate)" if unconditional else
                              "service witness (zone-level match MODELLED)")),
                   "services": (sorted(cape_like[0][1]["services"]) if cape_like else wit)}
            remaining = r_p
            for (x, y), d in zip(zip(anchors, anchors[1:]), hops):
                if y in avoided and x != alt_node:
                    k_xc = sea.nm(x, alt_node)
                    if k_xc is not None:
                        a_acc.setdefault((x, y, alt_node), []).append(
                            (w_add,
                             max(0.0, days((k_xc + k_cq) * s_a) - days(remaining * s_p)),
                             days(k_xc * s_a)))
                        if not unconditional:
                            a_cond.add((x, y, alt_node))
                remaining -= d
            b_acc.setdefault((q, (anchors[-2], q), alt_node), []).append((w_add, days(k_cq * s_a)))
            alt_counts[g] = alt_counts.get(g, 0) + 1
            break
        entries.append({
            "leg": key, "from": p, "to": q, "gates": gates,
            "services": sorted(info["services"]), "n_services": info["n"],
            "variants_seen": [{"via": list(v), "n": i["n"]} for v, i in order],
            "leg_tau_d": round(leg_tau, 6), "route_nm": round(r_p, 3), "route_km": round(r_p * KM_PER_NM, 3),
            "chain": chain, "alt": alt, "flow_w": round(w_add, 6),
        })
    chain_d = {k: {"w": chain_w[k], "tau": chain_tau[k]} for k in sorted(chain_w)}
    lanes: list = []
    for (x, y, an), lst in sorted(a_acc.items()):
        tw = sum(w for w, _, _ in lst)
        lanes.append({"key": f"{x}->{an}:sea#routes-A[{y}]", "kind": "A", "from": x,
                      "to": an, "leg": f"{x}->{y}:sea",
                      "tau": round(max(sum(w * ta for w, _, ta in lst) / tw, 0.05), 6),
                      "dtau": round(sum(w * dt for w, dt, _ in lst) / tw, 6),
                      **({"exo": False} if (x, y, an) in a_cond else {})})
    for (q, (u, _), an), lst in sorted(b_acc.items()):
        tw = sum(w for w, _ in lst)
        lanes.append({"key": f"{an}->{q}:sea#routes-B", "kind": "B", "from": an, "to": q,
                      "leg": f"{u}->{q}:sea",
                      "tau": round(max(sum(w * tb for w, tb in lst) / tw, 0.05), 6),
                      "dtau": 0.0})
    # ---- port substitution along service rotations
    replaced = {en["leg"] for en in entries}
    seen: set = set()
    n_sub = 0
    for r in ([] if "substitution" in ablate else rotations):
        calls = r["calls"]
        gaps = set(r.get("gaps", []))
        for i in range(len(calls) - 2):
            if i in gaps or (i + 1) in gaps:
                continue
            u, b, c = calls[i], calls[i + 1], calls[i + 2]
            if len({u, b, c}) < 3 or (u, b, c) in seen:
                continue
            e = sea_edges.get((u, b))
            if e is None or edge_key(e) in replaced or edge_key(e) in bypassed or e.get("bypass_of"):
                continue
            nc = nodes.get(c)
            if nc is None or nc["mode"] != "sea" or nc.get("kind") != "port":
                continue
            d_bc, d_uc, d_cb = sea.nm(b, c), sea.nm(u, c), sea.nm(c, b)
            if d_bc is None or d_bc > max_feeder or d_uc is None or d_cb is None:
                continue
            seen.add((u, b, c))
            lk = edge_key(e)
            tau_a = sea.days(d_uc)
            lanes.append({"key": f"{u}->{c}:sea#subst-A[{b}]", "kind": "A", "from": u, "to": c,
                          "leg": lk, "tau": round(max(tau_a, 0.05), 6),
                          "dtau": round(max(0.0, tau_a - float(e["base_transit_d"])), 6),
                          "exo": False})
            lanes.append({"key": f"{c}->{b}:sea#subst-B[{u}]", "kind": "B", "from": c, "to": b,
                          "leg": lk, "tau": round(max(sea.days(d_cb), 0.05), 6), "dtau": 0.0})
            n_sub += 1
    load: dict = {}
    for ent in entries:
        for g in ent["gates"]:
            load[g] = load.get(g, 0.0) + ent["flow_w"]
    audit = {
        "gate_through_flow_vs_baseline": {
            g: {"through_flow_calls_per_day": round(v, 4),
                "gate_baseline_calls_per_day": round(_lam0(nodes[g], psi), 4)}
            for g, v in sorted(load.items())},
        "chain_edges": len(chain_d),
        "chain_edges_merged_with_graph_legs": sum(1 for (u, v) in chain_d if (u, v) in sea_edges),
        "clamped_shares": sum(
            1 for (u, v), c in chain_d.items()
            if (c["w"] + (float(sea_edges[(u, v)]["capacity_share"]) * _lam0(nodes[u], psi)
                          if (u, v) in sea_edges else 0.0)) > _lam0(nodes[u], psi) + 1e-12),
        "alternatives_by_gate": dict(sorted(alt_counts.items())),
        "port_substitution_triples": n_sub,
        "time_basis": ("lane nautical miles / (service speed x 24 h); the chain's hop distances are "
                       "scaled so the chain sums to the direct lane"),
    }
    return RoutesTable(entries, skipped, chain_d, lanes, meta, audit)
