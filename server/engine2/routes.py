"""ENGINE REV 2 ROUTES TABLE built from the graph services layer (opt-in).

Why: the graph stores legs only, so a closure at Suez reaches the ports
whose legs end at Suez and little else; Asia-Europe cargo travels on
through services whose chokepoints are not on any leg. The services sidecar (data/graph/services.json,
graph_services) says which chokepoints a service rotation passes between
two calls. This module turns that into a ROUTES TABLE and into a
routes-augmented copy of the CORE graph that the unchanged rev-2 solver
runs on:

  * a graph sea leg  p -> q  that the selected services serve through
    chokepoints g1..gn (ordered) is REPLACED by the chain
    p -> g1 -> ... -> gn -> q. Every new chain leg carries the original
    leg's attributed flow (share x sender baseline), so the receiver's
    modelled dependence V on that supply is unchanged (flow
    conservation). A chain leg that already exists as a graph leg keeps
    its transit and ADDS the through flow to its share.
  * route lengths are kilometres along the graph's own legs (shortest
    path between consecutive anchors [p, g1.., q], legs sailed either
    way, great-circle chords between place coordinates). The original
    leg's SOURCED/MODELED transit days are distributed over the route in
    proportion to length (so the days-per-km of the leg itself is the
    time basis, port time included); added days of the alternative
    route are that basis times the route length difference
    (R_alt - R_primary), computed by the same method for both.
  * when a Suez or Bab el-Mandeb gate fails, flow takes the Cape of Good
    Hope alternative: for each chain leg that ends in such a gate an
    A lane  u -> Cape  (arrives after the days u -> Cape) and per
    receiver a B lane  Cape -> q  (days Cape -> q) are handed to the
    solver's existing reroute machinery (headroom of the Cape,
    forwarding, bunching), so the diverted cargo restores q's supply
    after the real Cape route. The A lane's reported added days is the
    true route difference. alt_basis is 'service' when a selected
    service sails the pair via the Cape, else 'MODELLED (Cape default)'.

Nothing here edits a graph file; the table is a pure function of
(graph, services selection) and its digest is journaled.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import math

from engine.graph import Graph, edge_key

CAPE = "chk_capeofgoodhope"
GATES_WITH_CAPE_ALT = ("chk_suez", "chk_babelmandeb")
EARTH_R_KM = 6371.0088


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _lam0(node: dict, psi: float) -> float:
    unit_s = str(node.get("capacity_unit", ""))
    u = 1.0 / psi if unit_s.upper().startswith("TEU") else 1.0
    return float(node["capacity"]) * u


def _gc_km(a: tuple, b: tuple) -> float:
    la1, lo1, la2, lo2 = (math.radians(v) for v in (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2.0 * EARTH_R_KM * math.asin(min(1.0, math.sqrt(h)))


class _Km:
    """Shortest path in km (great-circle chords) over the core graph's
    sea legs, sailed in either direction. Deterministic tie-break by
    node sequence."""

    def __init__(self, core: Graph):
        self.xy = {nid: (float(n["lat"]), float(n["lon"])) for nid, n in core.nodes.items()}
        adj: dict[str, set] = {}
        for e in core.edges:
            if e["mode"] != "sea":
                continue
            adj.setdefault(e["from"], set()).add(e["to"])
            adj.setdefault(e["to"], set()).add(e["from"])
        self.adj = {u: sorted(v) for u, v in adj.items()}
        self._cache: dict[tuple, tuple | None] = {}

    def path(self, x: str, y: str):
        """(km, node tuple) or None."""
        key = (x, y)
        if key in self._cache:
            return self._cache[key]
        best = {x: 0.0}
        heap = [(0.0, (x,), x)]
        done = set()
        out = None
        while heap:
            d, p, u = heapq.heappop(heap)
            if u in done:
                continue
            done.add(u)
            if u == y:
                out = (d, p)
                break
            for v in self.adj.get(u, []):
                nd = d + _gc_km(self.xy[u], self.xy[v])
                if nd < best.get(v, float("inf")) - 1e-9:
                    best[v] = nd
                    heapq.heappush(heap, (nd, p + (v,), v))
        self._cache[key] = out
        return out

    def km(self, x: str, y: str):
        r = self.path(x, y)
        return None if r is None else r[0]


class RoutesTable:
    """The routes table plus what the solver needs (chain edges, lane specs)."""

    def __init__(self, entries: list, skipped: list, chain: dict, lanes: list,
                 meta: dict, audit: dict):
        self.entries = entries
        self.skipped = skipped
        self.chain = chain            # (from, to) -> {"tau": float, "w": float}
        self.lane_specs = lanes
        self.meta = meta
        self.audit = audit
        self.replaced = sorted(e["leg"] for e in entries)
        self.digest = hashlib.sha256(_canon(self.to_doc(full=False)).encode("utf-8")).hexdigest()

    def to_doc(self, full: bool = True) -> dict:
        doc = {
            "label": "ROUTES TABLE from graph services (rotations SOURCED; placement MODELLED)",
            "meta": self.meta,
            "legs_bound": len(self.entries),
            "legs_skipped": len(self.skipped),
            "entries": self.entries,
            "lanes": self.lane_specs,
            "audit": self.audit,
        }
        if full:
            doc["skipped"] = self.skipped
            doc["table_sha256"] = self.digest
        return doc

    # ---- augmented core graph -------------------------------------------
    def augment(self, core: Graph, psi: float) -> Graph:
        raw = core.raw
        nodes = raw["nodes"]
        nmap = {n["id"]: n for n in nodes}
        replaced = set(self.replaced)
        existing = {edge_key(e): e for e in raw["edges"]}
        merged: dict[str, dict] = {}
        for k, c in sorted(self.chain.items()):
            u, v = k
            key = f"{u}->{v}:sea"
            lam_u = _lam0(nmap[u], psi)
            if key in existing:
                e = dict(existing[key])
                share = (float(e["capacity_share"]) * lam_u + c["w"]) / lam_u if lam_u > 0 else 1.0
                e["capacity_share"] = round(min(1.0, share), 9)
                e["calibration"] = {**e["calibration"], "routes_merged": True}
                merged[key] = e
            else:
                share = c["w"] / lam_u if lam_u > 0 else 1.0
                merged[key] = {
                    "from": u, "to": v, "mode": "sea",
                    "base_transit_d": round(max(c["tau"], 0.05), 6),
                    "capacity_share": round(min(1.0, share), 9),
                    "calibration": {"basis": "MODELED",
                                    "volume_source": "routes table from graph services",
                                    "routes_chain": True}}
        out_edges = []
        for e in raw["edges"]:
            k = edge_key(e)
            if k in replaced:
                continue
            out_edges.append(merged.pop(k, e))
        out_edges.extend(merged[k] for k in sorted(merged))
        return Graph({"version": raw.get("version"), "nodes": list(nodes), "edges": out_edges})


def build_routes_table(core: Graph, od: dict, meta: dict, psi: float) -> RoutesTable:
    """``core``: the CORE-tier graph the solver runs on; ``od``: the
    graph_services od_routes result {(p, q): {via tuple: {"services", "n"}}}."""
    km = _Km(core)
    nodes = core.nodes
    sea_edges = {(e["from"], e["to"]): e for e in core.edges if e["mode"] == "sea"}
    bypassed = {e["bypass_of"] for e in core.edges if e.get("bypass_of")}
    entries: list = []
    skipped: list = []
    chain_w: dict[tuple, float] = {}
    chain_tau: dict[tuple, float] = {}
    a_acc: dict[tuple, list] = {}         # (x, y) -> [(w, dtau_true, tau_A)]
    b_acc: dict[tuple, list] = {}         # (q, last chain edge) -> [(w, tau_B)]
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
            d = km.km(x, y)
            if d is None:
                skipped.append({"leg": key, "reason": f"no graph path {x} -> {y}"})
                ok = False
                break
            hops.append(d)
        if not ok:
            continue
        r_p = sum(hops)
        if r_p <= 0:
            skipped.append({"leg": key, "reason": "zero route length"})
            continue
        leg_tau = float(e["base_transit_d"])
        day_per_km = leg_tau / r_p                      # the leg's own time basis
        w_add = float(e["capacity_share"]) * _lam0(nodes[p], psi)
        chain = []
        for (x, y), d in zip(zip(anchors, anchors[1:]), hops):
            chain_w[(x, y)] = chain_w.get((x, y), 0.0) + w_add
            chain_tau.setdefault((x, y), d * day_per_km)
            chain.append({"from": x, "to": y, "km": round(d, 3), "days": round(d * day_per_km, 6)})
        alt = None
        cape_variants = [(v, i) for v, i in order if CAPE in v]
        if any(g in GATES_WITH_CAPE_ALT for g in gates) and CAPE not in gates and CAPE in nodes:
            k_pc = km.km(p, CAPE)
            k_cq = km.km(CAPE, q)
            if k_pc is not None and k_cq is not None:
                r_a = k_pc + k_cq
                alt = {"via": [CAPE], "route_km": round(r_a, 3),
                       "route_days": round(r_a * day_per_km, 6),
                       "added_days": round(max(0.0, (r_a - r_p) * day_per_km), 6),
                       "added_days_raw": round((r_a - r_p) * day_per_km, 6),
                       "basis": ("service" if cape_variants else "MODELLED (Cape default)"),
                       "services": sorted(cape_variants[0][1]["services"]) if cape_variants else []}
                remaining = r_p
                for (x, y), d in zip(zip(anchors, anchors[1:]), hops):
                    if y in GATES_WITH_CAPE_ALT and x != CAPE:
                        k_xc = km.km(x, CAPE)
                        if k_xc is not None:
                            a_acc.setdefault((x, y), []).append(
                                (w_add, max(0.0, (k_xc + k_cq - remaining) * day_per_km),
                                 k_xc * day_per_km))
                    remaining -= d
                b_acc.setdefault((q, (anchors[-2], q)), []).append((w_add, k_cq * day_per_km))
        entries.append({
            "leg": key, "from": p, "to": q, "gates": gates,
            "services": sorted(info["services"]), "n_services": info["n"],
            "variants_seen": [{"via": list(v), "n": i["n"]} for v, i in order],
            "leg_tau_d": round(leg_tau, 6), "route_km": round(r_p, 3),
            "chain": chain, "alt": alt, "flow_w": round(w_add, 6),
        })
    chain = {k: {"w": chain_w[k], "tau": chain_tau[k]} for k in sorted(chain_w)}
    lanes: list = []
    for (x, y), lst in sorted(a_acc.items()):
        tw = sum(w for w, _, _ in lst)
        lanes.append({"key": f"{x}->{CAPE}:sea#routes-A[{y}]", "kind": "A", "from": x,
                      "to": CAPE, "leg": f"{x}->{y}:sea",
                      "tau": round(max(sum(w * ta for w, _, ta in lst) / tw, 0.05), 6),
                      "dtau": round(sum(w * dt for w, dt, _ in lst) / tw, 6)})
    for (q, (u, _)), lst in sorted(b_acc.items()):
        tw = sum(w for w, _ in lst)
        lanes.append({"key": f"{CAPE}->{q}:sea#routes-B", "kind": "B", "from": CAPE, "to": q,
                      "leg": f"{u}->{q}:sea",
                      "tau": round(max(sum(w * tb for w, tb in lst) / tw, 0.05), 6),
                      "dtau": 0.0})
    load: dict[str, float] = {}
    for ent in entries:
        for g in ent["gates"]:
            load[g] = load.get(g, 0.0) + ent["flow_w"]
    audit = {
        "gate_through_flow_vs_baseline": {
            g: {"through_flow_calls_per_day": round(v, 4),
                "gate_baseline_calls_per_day": round(_lam0(nodes[g], psi), 4)}
            for g, v in sorted(load.items())},
        "chain_edges": len(chain),
        "chain_edges_merged_with_graph_legs": sum(1 for (u, v) in chain if (u, v) in sea_edges),
        "clamped_shares": sum(
            1 for (u, v), c in chain.items()
            if (c["w"] + (float(sea_edges[(u, v)]["capacity_share"]) * _lam0(nodes[u], psi)
                          if (u, v) in sea_edges else 0.0)) > _lam0(nodes[u], psi) + 1e-12),
        "time_basis": ("days per km of the bound leg itself (its SOURCED/MODELED base_transit_d "
                       "over its route length along graph legs); added days = route length "
                       "difference x that basis"),
    }
    return RoutesTable(entries, skipped, chain, lanes, meta, audit)
