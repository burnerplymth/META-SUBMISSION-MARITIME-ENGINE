"""Rotation coupling for ENGINE REV 2 profile d (opt-in): which inbound flows are carried by
vessels that go on to a later call of the same service rotation.

``zeta(leg u->v)`` = (number of occurrences of the leg in the selected rotations whose vessels
continue to a later call) / (number of occurrences of the leg). A call is continued unless it is
the last call of an open string (a loop continues; so does any call followed by a leg). Nothing
here invents a service, a call or a frequency: it reads the order of calls only. The engine uses
zeta in plugins_d.rotation_coupling / rotation_recovery (see engine.py, profile d).

Pure function of (selected services, graph coordinates, routes table).
"""
from __future__ import annotations

try:
    from graph_services.legs import entry_legs
    from graph_services.zones import zone
except Exception:  # pragma: no cover - imported as server.engine2.*
    from server.graph_services.legs import entry_legs  # type: ignore
    from server.graph_services.zones import zone  # type: ignore


def raw_zeta(selected: list, graph_data: dict) -> dict:
    """{(from, to): zeta} over every leg of the selected rotations."""
    coords = {n["id"]: (float(n["lat"]), float(n["lon"])) for n in graph_data["nodes"]}
    zones = {pid: zone(*ll) for pid, ll in coords.items()}
    seen: dict = {}
    for e in selected:
        legs = entry_legs(e, zones)
        n_calls = len(e["calls"])
        for lg in legs:
            j = (lg["i"] + 1) % n_calls if lg["wrap"] else lg["i"] + 1
            nxt = any(l2["i"] >= j for l2 in legs if l2 is not lg and not
                      (l2["i"] == lg["i"]))
            if lg["wrap"]:
                nxt = True                    # the loop closes: the vessel goes round again
            tot, cont = seen.get((lg["from"], lg["to"]), (0, 0))
            seen[(lg["from"], lg["to"])] = (tot + 1, cont + (1 if nxt else 0))
    return {k: cont / tot for k, (tot, cont) in sorted(seen.items())}


def leg_zeta(raw: dict, table, core_nodes: dict) -> dict:
    """{edge key "u->v:sea": zeta} for the legs of the (routes-augmented) graph.

    A leg a selected rotation uses keeps its zeta; a leg that ends at a chokepoint is passed
    through by the vessel (zeta 1); the last leg of a routes chain inherits the zeta of the leg
    the chain replaced. Legs no selected rotation uses are absent (zeta 0)."""
    out: dict = {}
    for (u, v), z in raw.items():
        out[f"{u}->{v}:sea"] = z
    if table is not None:
        for ent in table.entries:
            hops = ent["chain"]
            for hop in hops:
                key = f'{hop["from"]}->{hop["to"]}:sea'
                if core_nodes.get(hop["to"], {}).get("kind") == "chokepoint":
                    out[key] = max(out.get(key, 0.0), 1.0)
            last = hops[-1]
            lk = f'{last["from"]}->{last["to"]}:sea'
            z_orig = raw.get((ent["from"], ent["to"]))
            if z_orig is not None and core_nodes.get(last["to"], {}).get("kind") != "chokepoint":
                out[lk] = max(out.get(lk, 0.0), z_orig)
    return dict(sorted(out.items()))
