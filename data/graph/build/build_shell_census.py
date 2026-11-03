"""Build the SHELL census.

Reads ONLY pinned, in-repo inputs (never the network) and writes
``data/graph/nodes-shell.json`` — the SHELL-tier census consumed by
``build_graph.py`` alongside the CORE censuses (the base node census +
nodes-expansion.json, both untouched).

Tier bar (two-tier model):
  CORE  = the existing 200 nodes, unchanged. Calibration-grade.
  SHELL = routing-grade: verified coordinates from an authoritative
          registry + observed activity presence in that registry.
          Capacity is a labelled ESTIMATE (basis printed per node) or
          NOT_PUBLISHED (then the engine's MODELED default applies).
          Shell nodes never originate Ledger locks and never feed the
          scorecard — enforced in server code, not here.

Selection rules (stated in nodes-shell.json "derivation" and quoted in
calibration.md):
  PORTS : IMF PortWatch registry groups (records grouped by UN/LOCODE,
          else by name+country; counts summed within a group), records
          matched to CORE nodes (by portid or LOCODE) excluded, ranked
          by total observed vessel calls, top SHELL_PORT_N taken.
          Estimate: TEU/day via core-port median factors (see below).
  AIR   : OurAirports large airports with scheduled service and an
          IATA code. Ranked by (a) presence in the Wikipedia
          busiest-by-passenger top-50 tables (pinned union, best rank),
          then (b) one primary gateway per not-yet-covered country
          (Wikipedia-linked rows preferred), then (c) remaining
          Wikipedia-linked rows by IATA. Capacity NOT_PUBLISHED —
          no cargo figure is published per-airport in the pinned
          sources, and none is invented.
  GROUND: NTAD rail TOFC/COFC facilities, deduplicated, excluding
          facilities within 25 km of a CORE ground node (same terminal
          complex) or within 8 km of an already-selected shell ground
          node; selected state-round-robin for geographic spread.
          Capacity NOT_PUBLISHED (the registry publishes no
          throughput); the MODELED ground default applies.

Deterministic: every ordering below is fully keyed; re-running on the
same pins reproduces nodes-shell.json byte-identically.
"""
from __future__ import annotations

import json
import math
import os
import re
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent          # data/graph/build
GRAPH_DIR = HERE.parent                          # data/graph
REPO = GRAPH_DIR.parents[1]
NODES_DRAFT = Path(os.environ.get("SUPERMAP_NODES_DRAFT", "data/graph/nodes-draft.json"))   # base node census

SHELL_PORT_N = 660
SHELL_AIR_N = 77
SHELL_GROUND_N = 55

# MODELED engine defaults for NOT_PUBLISHED shell capacities (the port
# and air figures are the stated shell defaults; the
# ground figure is the core census's existing GAP-MODELED default).
SHELL_DEFAULTS = {"port": 500, "air_hub": 300, "ground_node": 1500}

PORT_MIN_CORE_KM = 8.0        # shell port closer than this to a CORE
                              # port is presumed the same complex
GROUND_MIN_CORE_KM = 25.0     # NTAD facility vs CORE ground node
GROUND_MIN_SHELL_KM = 8.0     # NTAD facility vs selected shell node

RETRIEVED = "2026-10-21"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fmt_int(x: float) -> str:
    return f"{int(round(x)):,}"


# ----------------------------------------------------------------------
# CORE reference data
# ----------------------------------------------------------------------

def core_reference():
    base_doc = load(NODES_DRAFT)
    base_nodes = base_doc if isinstance(base_doc, list) else base_doc["nodes"]
    exp = load(GRAPH_DIR / "nodes-expansion.json")
    full_graph = load(GRAPH_DIR / "graph.json")
    # The CORE reference is the tier==core subset (on a pre-tier
    # graph.json every node counts as core). Filtering here is what
    # makes the builder reproducible against the tiered graph:
    # without it, a re-run would mistake SHELL nodes for core
    # reference points (proximity drops, K factors) and drift.
    graph = {**full_graph,
             "nodes": [n for n in full_graph["nodes"]
                       if n.get("tier", "core") == "core"]}
    census = list(base_nodes) + list(exp["nodes"])
    core_locodes = set()
    core_iatas, core_icaos = set(), set()
    for e in census:
        codes = e.get("codes") or {}
        if codes.get("unlocode"):
            core_locodes.add(re.sub(r"\s+", "", codes["unlocode"]).upper())
        if codes.get("iata"):
            core_iatas.add(codes["iata"].upper())
        if codes.get("icao"):
            core_icaos.add(codes["icao"].upper())
    join_input = load(GRAPH_DIR / "portwatch_join_input.json")
    core_portids = {r["portid"] for r in join_input["records"]}
    node_portids: dict[str, list[str]] = {}
    for r in join_input["records"]:
        node_portids.setdefault(r["node_id"], []).append(r["portid"])
    return {
        "graph": graph,
        "core_ids": {n["id"] for n in graph["nodes"]},
        "core_aliases": {a for n in graph["nodes"] for a in n.get("aliases", [])},
        "core_locodes": core_locodes,
        "core_iatas": core_iatas,
        "core_icaos": core_icaos,
        "core_portids": core_portids,
        "node_portids": node_portids,
        "ports": {n["id"]: n for n in graph["nodes"] if n["kind"] == "port"},
        "ground": {n["id"]: n for n in graph["nodes"] if n["kind"] == "ground_node"},
        "air": {n["id"]: n for n in graph["nodes"] if n["kind"] == "air_hub"},
    }


# ----------------------------------------------------------------------
# PORTS
# ----------------------------------------------------------------------

def build_ports(core, drops):
    pin = load(GRAPH_DIR / "portwatch_registry_pin.json")
    records = pin["records"]
    by_portid = {r["portid"]: r for r in records}

    # Core-port capacity factors (TEU per observed call), from the pins.
    kc, kt = [], []
    for nid, node in sorted(core["ports"].items()):
        pids = core["node_portids"].get(nid, [])
        cont = sum((by_portid[p].get("vessel_count_container") or 0)
                   for p in pids if p in by_portid)
        tot = sum((by_portid[p].get("vessel_count_total") or 0)
                  for p in pids if p in by_portid)
        if cont > 0:
            kc.append(node["capacity"] * 365.0 / cont)
        if tot > 0:
            kt.append(node["capacity"] * 365.0 / tot)
    k_container = statistics.median(kc)
    k_total = statistics.median(kt)

    # Group registry records; drop CORE-matched records first.
    groups: dict[str, dict] = {}
    for r in records:
        loc = re.sub(r"\s+", "", r["LOCODE"]).upper() if r.get("LOCODE") else None
        if r["portid"] in core["core_portids"] or (loc and loc in core["core_locodes"]):
            continue
        key = f"loc:{loc}" if loc else f"name:{norm(r['portname'])}|{r['ISO3']}"
        g = groups.setdefault(key, {"records": [], "locode": loc})
        g["records"].append(r)

    # CORE-representation guards beyond portid/LOCODE record matching
    # (each catches a real case):
    #  - the group's LOCODE is already an alias of a CORE node (the
    #    registry port is the core gateway under another portid —
    #    Seattle vs the core Seattle-Tacoma NWSA node);
    #  - the group's name equals a CORE port's name and its registry
    #    point sits within 150 km of that core node (the same port
    #    under a romanisation the LOCODE match missed — Busan, whose
    #    census LOCODE is KRPUS vs the registry's KRBUS). Same-name
    #    ports farther away are genuinely different ports (Cartagena
    #    ESP vs Cartagena COL) and are kept.
    core_name_pts = []
    for n in core["graph"]["nodes"]:
        if n["kind"] == "port":
            core_name_pts.append((norm(n["name"]), n["id"], n["lat"], n["lon"]))
            stripped = n["name"][8:] if n["name"].lower().startswith("port of ") \
                else n["name"]
            core_name_pts.append((norm(stripped), n["id"], n["lat"], n["lon"]))

    cands = []
    for key, g in groups.items():
        recs = g["records"]
        tot = sum(r.get("vessel_count_total") or 0 for r in recs)
        if tot <= 0:
            drops.append(("port", key, "no observed port calls in the PortWatch registry"))
            continue
        rep0 = sorted(recs, key=lambda r: (-(r.get("vessel_count_total") or 0),
                                           r["portid"]))[0]
        if g["locode"] and g["locode"] in core["core_aliases"]:
            drops.append(("port", key,
                          f"UN/LOCODE {g['locode']} is already an alias of a CORE "
                          "node — the registry port is represented by that "
                          "gateway"))
            continue
        gname = norm(rep0["portname"])
        gname_stripped = norm(rep0["portname"][8:]) \
            if rep0["portname"].lower().startswith("port of ") else gname
        dup = None
        for cname, cid, la, lo in core_name_pts:
            if gname == cname or gname_stripped == cname:
                d = haversine_km(rep0["lat"], rep0["lon"], la, lo)
                if d < 150.0:
                    dup = (cid, d)
                    break
        if dup:
            drops.append(("port", key,
                          f"same name as CORE port {dup[0]} and {dup[1]:.1f} km "
                          "from it — the same port (registry romanisation the "
                          "LOCODE match missed)"))
            continue
        rep = sorted(recs, key=lambda r: (-(r.get("vessel_count_total") or 0),
                                          r["portid"]))[0]
        cands.append({
            "key": key, "rep": rep, "records": recs, "locode": g["locode"],
            "total": tot,
            "container": sum(r.get("vessel_count_container") or 0 for r in recs),
            "dry_bulk": sum(r.get("vessel_count_dry_bulk") or 0 for r in recs),
            "general_cargo": sum(r.get("vessel_count_general_cargo") or 0 for r in recs),
            "tanker": sum(r.get("vessel_count_tanker") or 0 for r in recs),
            "roro": sum(r.get("vessel_count_RoRo") or 0 for r in recs),
        })
    cands.sort(key=lambda c: (-c["total"], min(r["portid"] for r in c["records"])))

    used_ids = set(core["core_ids"])
    used_aliases = set(core["core_aliases"]) | set(core["core_ids"])
    core_port_pts = [(n["id"], n["lat"], n["lon"]) for n in core["ports"].values()]
    out, cutoff = [], None
    for c in cands:
        if len(out) >= SHELL_PORT_N:
            if cutoff is None:
                cutoff = c["total"]
            drops.append(("port", c["key"],
                          f"below the shell rank cutoff (total calls {c['total']}; "
                          "not padded to a target)"))
            continue
        rep = c["rep"]
        near = [(d, nid) for nid, la, lo in core_port_pts
                for d in [haversine_km(rep["lat"], rep["lon"], la, lo)]
                if d < PORT_MIN_CORE_KM]
        if near:
            d, nid = min(near)
            drops.append(("port", c["key"],
                          f"within {d:.1f} km of CORE port {nid} — presumed the "
                          "same port complex"))
            continue
        name = rep["portname"]
        base = name[8:] if name.lower().startswith("port of ") else name
        nid = "sea_" + slug(base)
        if not nid.strip("sea_"):
            nid = "sea_" + slug(rep["fullname"] or name)
        if nid in used_ids:
            nid = f"{nid}_{rep['ISO3'].lower()}"
        if nid in used_ids:
            nid = f"{nid}_{min(r['portid'] for r in c['records'])}"
        used_ids.add(nid)
        aliases = []
        if c["locode"]:
            if c["locode"] in used_aliases:
                drops.append(("port", c["key"],
                              f"UN/LOCODE {c['locode']} already an alias of a CORE "
                              "node — alias dropped, node kept"))
            else:
                aliases.append(c["locode"])
                used_aliases.add(c["locode"])
        if c["container"] > 0:
            est = c["container"] / 365.0 * k_container
            basis = (f"container-call factor K_container = {k_container:,.0f} TEU per "
                     f"container call (median over {len(kc)} CORE ports)")
        else:
            est = c["total"] / 365.0 * k_total
            basis = (f"all-traffic factor K_total = {k_total:,.0f} TEU per vessel "
                     f"call (median over {len(kt)} CORE ports); the registry "
                     "records no container calls for this port")
        capacity = max(25, int(round(est)))
        pids = ", ".join(sorted(r["portid"] for r in c["records"]))
        note = (f"SHELL ESTIMATE: IMF PortWatch "
                f"registry portid(s) {pids} — {c['container']:,} container / "
                f"{c['total']:,} total observed vessel calls in the registry "
                f"reference period ⇒ ≈ {fmt_int(est)} TEU/day via the core-port "
                f"median factor ({basis}). Routing-grade figure: throughput as a "
                "capacity proxy, the same MODELED interpretation the CORE census "
                "prints for its ports.")
        out.append({
            "id": nid, "name": name, "aliases": aliases, "type": "port",
            "country": rep["country"], "lat": rep["lat"], "lon": rep["lon"],
            "codes": ({"unlocode": c["locode"]} if c["locode"] else {}),
            "capacity": capacity, "capacity_basis": "ESTIMATE",
            "capacity_note": note,
            "source_url": pin["source_url"], "source_name": pin["source_name"],
            "sources": [pin["source_url"]],
            "tier": "shell",
            "activity": {
                "kind": "observed_port_calls",
                "detail": (f"IMF PortWatch registry: {c['total']:,} total observed "
                           f"vessel calls (container {c['container']:,}, dry bulk "
                           f"{c['dry_bulk']:,}, general cargo {c['general_cargo']:,}, "
                           f"tanker {c['tanker']:,}, RoRo {c['roro']:,})"),
                "vessel_count_total": c["total"],
                "vessel_count_container": c["container"],
            },
            "registry": {"portids": sorted(r["portid"] for r in c["records"]),
                         "fullname": rep["fullname"], "iso3": rep["ISO3"]},
        })
    return out, {"k_container": k_container, "k_container_n": len(kc),
                 "k_total": k_total, "k_total_n": len(kt),
                 "cutoff_total_calls": cutoff,
                 "candidates": len(cands)}


# ----------------------------------------------------------------------
# AIR
# ----------------------------------------------------------------------

def build_air(core, drops):
    pin = load(GRAPH_DIR / "ourairports_pin.json")
    cmap = load(GRAPH_DIR / "ourairports_countries_pin.json")["countries"]
    ranking = load(GRAPH_DIR / "air_ranking_pin.json")
    rank_by_iata = {r["iata"]: r for r in ranking["records"]}

    rows = {}
    for r in pin["records"]:
        iata = r["iata_code"].strip().upper()
        if iata in rows:  # duplicate IATA in the registry: first ident wins
            continue
        rows[iata] = r
    core_air_countries = set()
    for iata, r in rows.items():
        if iata in core["core_iatas"] or (r["icao_code"] or "").upper() in core["core_icaos"]:
            core_air_countries.add(r["iso_country"])

    used_aliases = set(core["core_aliases"]) | set(core["core_ids"])
    cands = {}
    for iata, r in sorted(rows.items()):
        if iata in core["core_iatas"]:
            drops.append(("air", iata, "already a CORE air hub (IATA match)"))
            continue
        icao = (r["icao_code"] or "").strip().upper()
        if icao and icao in core["core_icaos"]:
            drops.append(("air", iata, "already a CORE air hub (ICAO match)"))
            continue
        if iata in used_aliases or (icao and icao in used_aliases):
            drops.append(("air", iata, "IATA/ICAO collides with an existing node "
                                       "alias — dropped rather than aliased ambiguously"))
            continue
        try:
            lat, lon = float(r["latitude_deg"]), float(r["longitude_deg"])
        except (TypeError, ValueError):
            drops.append(("air", iata, "no usable coordinates in OurAirports"))
            continue
        cands[iata] = {"row": r, "lat": lat, "lon": lon, "icao": icao}

    selected: list[str] = []
    via: dict[str, str] = {}
    # (a) passenger-list presence, best rank first
    for iata in sorted((i for i in cands if i in rank_by_iata),
                       key=lambda i: (rank_by_iata[i]["best_rank"], i)):
        if len(selected) >= SHELL_AIR_N:
            break
        selected.append(iata)
        via[iata] = "pax_rank"
    covered = core_air_countries | {cands[i]["row"]["iso_country"] for i in selected}
    # (b) one gateway per uncovered country, Wikipedia-linked first
    changed = True
    while len(selected) < SHELL_AIR_N and changed:
        changed = False
        for cc in sorted({c["row"]["iso_country"] for c in cands.values()} - covered):
            if len(selected) >= SHELL_AIR_N:
                break
            pool = [i for i, c in cands.items()
                    if c["row"]["iso_country"] == cc and i not in selected]
            if not pool:
                continue
            pool.sort(key=lambda i: (0 if cands[i]["row"]["wikipedia_link"] else 1, i))
            selected.append(pool[0])
            via[pool[0]] = "country_gateway"
            covered.add(cc)
            changed = True
    # (c) remaining Wikipedia-linked rows by IATA
    for iata in sorted((i for i in cands
                        if i not in selected and cands[i]["row"]["wikipedia_link"]),
                       key=lambda i: i):
        if len(selected) >= SHELL_AIR_N:
            break
        selected.append(iata)
        via[iata] = "registry_linked"

    used_ids = set(core["core_ids"])
    out = []
    for iata in selected:
        c = cands[iata]
        r = c["row"]
        base = (r["municipality"] or "").strip() or r["name"]
        nid = "air_" + slug(base)
        if nid in used_ids or len(nid) <= 4:
            nid = "air_" + iata.lower()
        if nid in used_ids:
            nid = f"{nid}_{r['iso_country'].lower()}"
        used_ids.add(nid)
        aliases = [iata] + ([c["icao"]] if c["icao"] else [])
        if via[iata] == "pax_rank":
            detail = (f"Wikipedia busiest-by-passenger-traffic yearly top-50 "
                      f"tables (pinned union): best rank "
                      f"{rank_by_iata[iata]['best_rank']}; OurAirports large "
                      "airport with scheduled service")
            sources = [pin["source_url"], ranking["source_url"]]
        else:
            detail = ("OurAirports registry: large airport with scheduled "
                      "service" + ("; primary gateway selected for country "
                      "coverage" if via[iata] == "country_gateway" else ""))
            sources = [pin["source_url"]]
        out.append({
            "id": nid, "name": r["name"], "aliases": aliases, "type": "air_hub",
            "country": cmap.get(r["iso_country"], r["iso_country"]),
            "lat": c["lat"], "lon": c["lon"],
            "codes": {"iata": iata, **({"icao": c["icao"]} if c["icao"] else {})},
            "capacity": SHELL_DEFAULTS["air_hub"],
            "capacity_basis": "NOT_PUBLISHED",
            "capacity_note": (f"SHELL: OurAirports "
                              f"registry record {r['ident']} ({iata}"
                              f"{'/' + c['icao'] if c['icao'] else ''}), "
                              f"{r['municipality'] or '—'}, {r['iso_country']}. "
                              "No per-airport cargo throughput is published in "
                              "the pinned sources and none is invented ⇒ capacity "
                              "NOT PUBLISHED; the MODELED shell default of 300 "
                              "tonnes/day applies (routing-grade only)."),
            "source_url": pin["source_url"], "source_name": pin["source_name"],
            "sources": sources,
            "tier": "shell",
            "activity": {"kind": "airport_presence", "detail": detail,
                         "selection": via[iata]},
            "registry": {"ident": r["ident"], "iso_country": r["iso_country"]},
        })
    return out, {"candidates": len(cands), "selected": len(out),
                 "via": {k: sum(1 for v in via.values() if v == k)
                         for k in ("pax_rank", "country_gateway", "registry_linked")}}


# ----------------------------------------------------------------------
# GROUND
# ----------------------------------------------------------------------

def build_ground(core, drops):
    pin = load(GRAPH_DIR / "ntad_rail_pin.json")
    core_pts = [(n["id"], n["lat"], n["lon"]) for n in core["ground"].values()]

    seen, facs = set(), []
    for r in pin["records"]:
        key = (norm(r.get("TERMINAL")), norm(r.get("CITY")), r.get("STATE"))
        if key in seen:
            drops.append(("ground", f"OBJECTID {r['OBJECTID']}",
                          "duplicate NTAD record (same terminal/city/state)"))
            continue
        seen.add(key)
        facs.append(r)

    eligible = []
    for r in facs:
        near = [(d, nid) for nid, la, lo in core_pts
                for d in [haversine_km(r["LAT"], r["LON"], la, lo)]
                if d < GROUND_MIN_CORE_KM]
        if near:
            d, nid = min(near)
            drops.append(("ground", f"OBJECTID {r['OBJECTID']}",
                          f"within {d:.1f} km of CORE ground node {nid} — same "
                          "terminal complex"))
            continue
        eligible.append(r)

    by_state: dict[str, list] = {}
    for r in eligible:
        by_state.setdefault(r["STATE"] or "??", []).append(r)
    for st in by_state:
        by_state[st].sort(key=lambda r: ((r.get("TERMINAL") or ""),
                                         (r.get("CITY") or ""), r["OBJECTID"]))
    state_order = sorted(by_state, key=lambda s: (-len(by_state[s]), s))

    selected, shell_pts = [], []
    idx = {s: 0 for s in state_order}
    while len(selected) < SHELL_GROUND_N:
        progressed = False
        for st in state_order:
            if len(selected) >= SHELL_GROUND_N:
                break
            while idx[st] < len(by_state[st]):
                r = by_state[st][idx[st]]
                idx[st] += 1
                if any(haversine_km(r["LAT"], r["LON"], la, lo) < GROUND_MIN_SHELL_KM
                       for la, lo in shell_pts):
                    drops.append(("ground", f"OBJECTID {r['OBJECTID']}",
                                  "within 8 km of an already-selected shell "
                                  "ground node — same terminal complex"))
                    continue
                selected.append(r)
                shell_pts.append((r["LAT"], r["LON"]))
                progressed = True
                break
        if not progressed:
            break

    used_ids = set(core["core_ids"])
    out = []
    for r in selected:
        name = (r.get("TERMINAL") or "").strip() or \
            f"{(r.get('CITY') or 'Unknown').strip()} Intermodal Terminal"
        nid = "gnd_" + slug(r.get("CITY") or name)
        if nid in used_ids:
            nid = f"{nid}_{(r.get('STATE') or 'us').lower()}"
        if nid in used_ids:
            nid = f"{nid}_{r['OBJECTID']}"
        used_ids.add(nid)
        out.append({
            "id": nid, "name": name, "aliases": [], "type": "ground_node",
            "country": "United States", "lat": r["LAT"], "lon": r["LON"],
            "codes": {},
            "capacity": SHELL_DEFAULTS["ground_node"],
            "capacity_basis": "NOT_PUBLISHED",
            "capacity_note": (f"SHELL: NTAD "
                              f"Intermodal Freight Facilities Rail TOFC/COFC "
                              f"record OBJECTID {r['OBJECTID']} ({name}, "
                              f"{r.get('CITY')}, {r.get('STATE')}; rail: "
                              f"{r.get('RAIL_CO') or '—'}; equipment: "
                              f"{r.get('EQUIPMENT') or '—'}). The registry "
                              "publishes no throughput figure ⇒ capacity NOT "
                              "PUBLISHED; the MODELED default of 1,500 "
                              "trucks/day applies — the same GAP-MODELED "
                              "default the CORE census uses for unsourced "
                              "ground nodes."),
            "source_url": pin["source_url"], "source_name": pin["source_name"],
            "sources": [pin["source_url"]],
            "tier": "shell",
            "activity": {"kind": "facility_presence",
                         "detail": ("NTAD registry: operating intermodal "
                                    "freight facility (rail TOFC/COFC)")},
            "registry": {"objectid": r["OBJECTID"], "state": r.get("STATE"),
                         "city": r.get("CITY"), "splc": r.get("SPLC")},
        })
    return out, {"facilities": len(facs), "eligible": len(eligible),
                 "selected": len(out)}


# ----------------------------------------------------------------------

def main() -> int:
    core = core_reference()
    drops: list[tuple[str, str, str]] = []
    ports, pinfo = build_ports(core, drops)
    air, ainfo = build_air(core, drops)
    ground, ginfo = build_ground(core, drops)
    nodes = sorted(ports + air + ground, key=lambda e: e["id"])
    doc = {
        "about": (
            "SHELL-tier census. Generated by data/graph/build/"
            "build_shell_census.py from the pinned registry extracts in "
            "this directory (portwatch_registry_pin.json, "
            "ourairports_pin.json, air_ranking_pin.json, ntad_rail_pin.json) "
            "— re-running the builder on the same pins reproduces this "
            "file byte-identically. Every entry passed the SHELL bar: "
            "verified registry coordinates + observed activity presence; "
            "capacity is a labelled ESTIMATE (basis in capacity_note) or "
            "NOT_PUBLISHED with the MODELED shell default. Shell nodes "
            "never originate Ledger locks and never feed the scorecard "
            "(enforced in server code)."),
        "derivation": {
            "retrieved_at": RETRIEVED,
            "port_capacity_factors": {
                "k_container_teu_per_call": pinfo["k_container"],
                "k_container_core_ports": pinfo["k_container_n"],
                "k_total_teu_per_call": pinfo["k_total"],
                "k_total_core_ports": pinfo["k_total_n"],
                "shell_cutoff_total_calls": pinfo["cutoff_total_calls"],
            },
            "shell_defaults": SHELL_DEFAULTS,
            "counts": {"port": len(ports), "air_hub": len(air),
                       "ground_node": len(ground)},
            "air_selection": ainfo,
            "ground_selection": ginfo,
        },
        "nodes": nodes,
    }
    out = GRAPH_DIR / "nodes-shell.json"
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False,
                              sort_keys=True) + "\n", encoding="utf-8")
    print(f"shell census: {len(ports)} ports, {len(air)} air, "
          f"{len(ground)} ground = {len(nodes)} nodes -> {out}")
    print(f"K_container = {pinfo['k_container']:,.1f} TEU/call "
          f"(n={pinfo['k_container_n']}); K_total = {pinfo['k_total']:,.1f} "
          f"(n={pinfo['k_total_n']}); port cutoff total calls = "
          f"{pinfo['cutoff_total_calls']}")
    from collections import Counter
    dc = Counter((k, r.split(' — ')[0].split(' (')[0]) for k, _, r in drops)
    print(f"drops: {len(drops)}")
    for (kind, reason), n in sorted(dc.items()):
        print(f"  [{kind}] x{n}: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
