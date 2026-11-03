#!/usr/bin/env python3
"""Generate the candidate sidecar documents for data/graph/extra.json.

Offline and deterministic (pinned inputs only; no network, no RNG). Reads
data/graph/graph.json (never writes it), data/graph/{portwatch_registry_pin,
portwatch_join_input,nodes-shell,ourairports_pin}.json and
data/graph_extra_candidates/inputs/*. Writes, next to itself:

  places.extra.candidates.json   new places (tier core_ext | shell), provenance each
  legs.extra.candidates.json     new primary / transit / alt-medium legs
  lanes.extra.candidates.json    dormant alternative lanes (engine2 routes-table
                                 lane specs: A/B lanes with bypass_of)
  services.port_resolution.json  the 21 unmapped service names resolved or refused
  candidates.sha256              sha256 of the four files (for the sidecar pin)

Rules re-used verbatim from data/graph/build_graph.py (SHELL S1-S6 construction:
nearest CORE hubs, peer links, 889 km/day sea speed, largest-remainder
apportionment of a 0.60 outbound budget by observed vessel calls). Rules that are
new here are named R-CAP (headroom-capped share for legs a SHELL rule does not
cover) and R-ROUTE (transit from a reference leg's own days-per-km). Nothing is
fitted to a backtest outcome.

    python3 data/graph_extra_candidates/build_extra_candidates.py
"""
from __future__ import annotations

import collections
import hashlib
import heapq
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
GD = REPO / "data" / "graph"
INP = HERE / "inputs"
PIN_PREFIX = "9743a288fb5d670d"
RET = "2026-10-25"
URL_PW_PORTS = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/PortWatch_ports_database/FeatureServer/0/query"
URL_PW_CHK = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/PortWatch_chokepoints_database/FeatureServer/0/query"
URL_PW_CHK_DAILY = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0/query"
URL_OURAIRPORTS = "https://davidmegginson.github.io/ourairports-data/airports.csv"
URL_ACI_WP = "https://en.wikipedia.org/wiki/List_of_busiest_airports_by_cargo_traffic"
URL_WP_REST = "https://en.wikipedia.org/api/rest_v1/page/summary/"


def jl(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def canon(o):
    return json.dumps(o, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def hv_km(a, b, c, d):
    p = math.pi / 180
    h = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def slug(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def apportion(total_units, weights):
    """Largest-remainder apportionment (same as build_graph._apportion_1e4)."""
    tw = sum(w for _, w in weights)
    raw = {k: total_units * w / tw for k, w in weights}
    u = {k: math.floor(v) for k, v in raw.items()}
    rem = total_units - sum(u.values())
    for k, _ in sorted(weights, key=lambda kw: (-(raw[kw[0]] - u[kw[0]]), kw[0]))[:rem]:
        u[k] += 1
    return u


# ----------------------------------------------------------------------------
# inputs
# ----------------------------------------------------------------------------
raw_graph = (GD / "graph.json").read_bytes()
assert hashlib.sha256(raw_graph).hexdigest().startswith(PIN_PREFIX), "graph.json changed"
G = json.loads(raw_graph)
N = {n["id"]: n for n in G["nodes"]}
EDGES = G["edges"]
EKEY = {f"{e['from']}->{e['to']}:{e['mode']}": e for e in EDGES}
OUT = collections.defaultdict(float)
for e in EDGES:
    OUT[(e["from"], e["mode"])] += e["capacity_share"]
REG = {r["portid"]: r for r in jl(GD / "portwatch_registry_pin.json")["records"]}
JOIN = collections.defaultdict(int)
for r in jl(GD / "portwatch_join_input.json")["records"]:
    if isinstance(r.get("vessel_count_total"), int):
        JOIN[r["node_id"]] += r["vessel_count_total"]
SHELL_DOC = jl(GD / "nodes-shell.json")
KF = SHELL_DOC["derivation"]["port_capacity_factors"]
K_CONT, K_TOT = KF["k_container_teu_per_call"], KF["k_total_teu_per_call"]
SHELL_CALLS = {n["id"]: (n.get("activity") or {}).get("vessel_count_total") for n in SHELL_DOC["nodes"]}
PWDB = {r["portid"]: r for r in jl(INP / "portwatch_chokepoints_database.json")["records"]}
PWD = jl(INP / "portwatch_chokepoints_daily_window.json")["chokepoints"]
OURA = {r["iata_code"]: r for r in jl(GD / "ourairports_pin.json")["records"] if r.get("iata_code")}
ACI = {r["iata"]: r for r in jl(INP / "aci_air_cargo_rankings.json")["tables"]["2015"]}
WPC = jl(INP / "wikipedia_coordinates.json")["cities"]

# ----------------------------------------------------------------------------
# waypoints (MODELLED: analyst-placed offshore routing points; every route
# length here is superseded by data/graph/sea_routes.json)
# ----------------------------------------------------------------------------
WP = {
    "PS": (31.27, 32.31), "GSUEZ": (28.0, 33.3), "RS1": (27.2, 34.2), "RS2": (21.0, 38.2), "RS3": (16.0, 41.0),
    "RS4": (13.2, 42.8), "ADEN": (12.4, 46.0), "GUARD": (12.1, 51.6), "ARAB1": (14.5, 57.0), "ARAB2": (16.0, 62.0),
    "ARAB3": (17.5, 66.5), "IND1": (11.0, 62.0), "IND2": (7.5, 74.0), "CMB": (6.4, 80.0), "GOMAN": (25.3, 58.0),
    "GOMAN2": (23.0, 60.5), "PGMID": (27.0, 51.5), "HZ2": (26.2, 55.0), "MALW": (5.3, 94.5), "SINGSTR": (1.2, 104.0),
    "KARIMATA": (-1.0, 108.0), "JAVA1": (-5.5, 106.8), "SUNDA": (-5.967, 105.775), "LOMBOK": (-8.419, 115.801),
    "JAVA2": (-5.8, 113.0), "SIO1": (-8.5, 100.0), "SIO2": (-2.0, 88.0), "SIO3": (3.0, 82.0), "SIO4": (-10.5, 112.0),
    "SIO5": (-8.0, 100.0), "TAIW": (24.7, 119.7), "SCS": (10.0, 110.5), "ECS_E": (24.0, 123.5), "HK_APP": (21.5, 114.5),
    "MEDW": (37.0, 0.0), "MEDC": (36.5, 12.0), "MEDE": (34.0, 24.0), "GIB": (35.97, -5.57), "GIBW": (36.5, -9.5),
    "FINIST": (43.2, -9.8), "USHANT": (48.6, -5.6), "DOVERG": (51.02, 1.42), "NSEA1": (53.0, 3.5), "NSEA2": (54.0, 7.0),
    "ELBE": (54.0, 8.3), "SKAGEN": (57.8, 10.6), "KATTEGAT": (56.3, 11.5), "GREATBELT": (55.3, 11.0), "KIELB": (54.5, 10.3),
    "BALT1": (55.0, 14.5), "BALT2": (56.5, 18.5), "GOF": (59.7, 24.0), "BOSPH": (41.12, 29.05), "BLK1": (43.0, 31.0),
    "AEG": (37.5, 25.0), "CAPEG": (-34.42, 18.48), "SATL1": (-20.0, 5.0), "SATL2": (0.0, -15.0), "NATL1": (20.0, -40.0),
    "NATL2": (35.0, -65.0), "AGUL": (-35.3, 20.0), "MOZ": (-26.5, 34.5), "MOZ2": (-12.0, 42.0), "MOZ3": (-20.2, 41.3),
    "EAF": (-4.5, 40.5), "PANG": (9.15, -79.68), "PAC1": (34.0, -170.0), "PAC2": (18.0, -125.0), "OSUMI": (30.9, 131.0),
    "CARIB1": (15.0, -77.0), "CARIB2": (22.0, -77.5), "FLST": (25.5, -79.8), "USEC1": (32.0, -76.0),
    "GULFMX1": (21.8, -85.6), "GULFMX2": (24.5, -85.0), "GULFMX3": (28.9, -94.5),
    "AN1": (38.0, -30.0), "AN2": (39.5, -55.0), "AS1": (33.0, -30.0), "AS2": (31.0, -60.0), "AH1": (30.0, -40.0), "AH2": (26.0, -70.0),
}


def pt(x):
    if x in WP:
        return WP[x]
    if x in N:
        return (N[x]["lat"], N[x]["lon"])
    if x in PLACES_BY_ID:
        return (PLACES_BY_ID[x]["lat"], PLACES_BY_ID[x]["lon"])
    raise KeyError(x)


PLACES_BY_ID: dict = {}


def chain_km(names):
    pts = [pt(n) for n in names]
    return sum(hv_km(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:]))


# ----------------------------------------------------------------------------
# 1. PLACES
# ----------------------------------------------------------------------------
places: list = []


def prov(src, mode="SNAPSHOT"):
    return {"source": src, "retrieved_at": RET, "mode": mode}


def add_chokepoint(cid, name, pw, aliases, priority, why, kiel=False):
    if kiel:
        cap = round(26659 / 365)           # WSV 2023 via Hansa
        lat, lon = WPC["Kiel Canal"]
        srcs = ["Federal Waterways and Shipping Administration (WSV) via Hansa News, 26,659 ships in 2023 / 365 = 73 vessels/day (observed throughput used as capacity proxy, the CORE chokepoint interpretation) https://hansa.news/nok-less-cargo-last-year/ (retrieved 2026-10-25)",
                "Coordinates: Wikipedia REST summary 'Kiel Canal' " + URL_WP_REST + "Kiel_Canal"]
        basis = "SOURCED_OBSERVED"
        note = "26,659 ships (2023) / 365; secondary report of WSV statistics; WSV page not retrieved"
    else:
        d = PWD[pw]
        cap = int(round(d["median_daily_transits"]))
        r = PWDB[pw]
        lat, lon = r["lat"], r["lon"]
        srcs = [f"IMF PortWatch Daily_Chokepoints_Data {pw} median daily transits {d['median_daily_transits']} (P99 {d['p99_daily_transits']}) over {d['window'][0]}..{d['window'][1]}, {URL_PW_CHK_DAILY} (retrieved {RET})",
                f"IMF PortWatch chokepoints database {pw} coordinates, {URL_PW_CHK} (retrieved {RET})"]
        basis = "SOURCED_OBSERVED"
        note = f"median of {d['n_days']} daily n_total counts, same window as the rev2c calibration; a demonstrated rate, not a design capacity"
    p = {"id": cid, "name": name, "aliases": aliases, "mode": "sea", "kind": "chokepoint", "lat": round(lat, 5), "lon": round(lon, 5),
         "capacity": cap, "capacity_unit": "vessels/day", "queue_cap": 2 * cap, "inventory_days": 0.0,
         "tier": "core_ext", "capacity_basis": basis, "capacity_note": note, "sources": srcs,
         "queue_cap_note": "2 x capacity, the CORE chokepoint convention (11 of 12 CORE chokepoints)",
         "priority": priority, "why": why, "provenance": prov(srcs[0])}
    places.append(p)
    PLACES_BY_ID[cid] = p


add_chokepoint("chk_sunda", "Sunda Strait", "chokepoint19", ["Selat Sunda"], "P0",
               "Malacca alternative for Asia-Indian Ocean and Cape-bound traffic; the graph carries it only as the authored singapore->colombo bypass leg")
add_chokepoint("chk_lombok", "Lombok Strait", "chokepoint15", ["Selat Lombok"], "P0",
               "second Malacca alternative (deeper draft); Australia-Asia and Cape-bound bulk")
add_chokepoint("chk_kiel", "Kiel Canal", None, ["Nord-Ostsee-Kanal", "NOK"], "P2",
               "North Sea-Baltic shortcut parallel to the Danish Straits (26,659 ships 2023)", kiel=True)
add_chokepoint("chk_magellan", "Strait of Magellan", "chokepoint21", ["Estrecho de Magallanes"], "P2",
               "the only non-Panama sea link between the two Chilean/Argentine coasts for ships using the straits (4/day)")

# ports (SHELL)
def port_record(portid):
    return REG[portid]


def add_port(pid_reg, nid, name, priority, why, aliases=None, sourced_cap=None, override_same_complex=None, country=None):
    r = port_record(pid_reg)
    cont, tot = r.get("vessel_count_container") or 0, r.get("vessel_count_total") or 0
    if sourced_cap is not None:
        capacity, basis = sourced_cap["teu_day"], "SOURCED"
        cnote = sourced_cap["note"]
        csrc = [sourced_cap["source"]]
    else:
        if cont > 0:
            est, how = cont / 365.0 * K_CONT, f"container-call factor K_container = {K_CONT:,.0f} TEU per container call (median over 77 CORE ports; data/graph/nodes-shell.json derivation)"
        else:
            est, how = tot / 365.0 * K_TOT, f"all-traffic factor K_total = {K_TOT:,.0f} TEU per vessel call (median over 77 CORE ports); the registry records no container calls"
        capacity, basis = max(25, int(round(est))), "ESTIMATE"
        cnote = (f"SHELL ESTIMATE: IMF PortWatch registry portid {pid_reg}: {cont:,} container / {tot:,} total observed vessel calls "
                 f"=> about {est:,.0f} TEU/day via {how}. Routing-grade throughput proxy, the same MODELED interpretation the CORE census prints.")
        csrc = []
    loc = (r.get("LOCODE") or "").replace(" ", "")
    if loc in {a for n_ in N.values() for a in n_.get("aliases", [])}:
        loc = ""      # the registry gives both BOTAS records the LOCODE TR BOT; the graph alias belongs to the Marmara LNG terminal
    nearest = min((hv_km(r["lat"], r["lon"], n["lat"], n["lon"]), i) for i, n in N.items() if n["kind"] == "port")
    p = {"id": nid, "name": name, "aliases": [loc] if loc else [], "mode": "sea", "kind": "port",
         "country": country or r["country"], "lat": round(r["lat"], 6), "lon": round(r["lon"], 6),
         "capacity": capacity, "capacity_unit": "TEU/day", "queue_cap": 3 * capacity, "inventory_days": 7.0,
         "tier": "shell", "capacity_basis": basis, "capacity_note": cnote,
         "sources": [URL_PW_PORTS] + csrc,
         "activity": {"kind": "observed_port_calls", "vessel_count_total": tot, "vessel_count_container": cont,
                      "vessel_count_tanker": r.get("vessel_count_tanker") or 0},
         "registry": {"portids": [pid_reg], "fullname": r.get("fullname"), "iso3": r.get("ISO3")},
         "dominant_vessel_type": max(("container", "tanker", "dry_bulk", "general_cargo", "RoRo"), key=lambda t: r.get("vessel_count_" + t) or 0),
         "capacity_is_container_capacity": (cont / tot >= 0.5) if tot else False,
         "nearest_existing_graph_port": {"id": nearest[1], "km": round(nearest[0], 1)},
         "priority": priority, "why": why, "provenance": prov(URL_PW_PORTS)}
    if override_same_complex:
        p["same_complex_override"] = override_same_complex
    if aliases:
        p["aliases"] = sorted(set(p["aliases"] + aliases))
    assert nid not in N and nid not in PLACES_BY_ID, nid
    places.append(p)
    PLACES_BY_ID[nid] = p


add_port("port1318", "sea_trieste", "Trieste", "P0", "services (6 dropped calls); TAL pipeline head; separate country and hinterland from Koper",
         override_same_complex="8 km from CORE sea_koper (Slovenia): the shell builder's 8 km same-complex rule absorbed it; Trieste is Italian, different customs territory, different terminals and rail (Trieste-Villa Opicina)")
add_port("port1396", "sea_wilhelmshaven", "Wilhelmshaven (JadeWeserPort)", "P0", "services (4 dropped calls); Germany's only deep-water container port",
         override_same_complex="CORE sea_bremerhaven is geocoded at 53.57N 8.13E, which is Wilhelmshaven's position (the true Bremerhaven is 53.55N 8.55E, 28 km east); the 1 km same-complex rule therefore dropped Wilhelmshaven; the graph file is not edited, so the sidecar carries it")
add_port("port326", "sea_ensenada", "Ensenada", "P0", "services (2 dropped calls); Baja California container port (Pacific Mexico)")
add_port("port2032", "sea_posorja", "Posorja (DP World Posorja)", "P0", "services (1 dropped call); Ecuador's deep-water port, CPPI 2023 rank 37 absent from graph")
add_port("port2272", "sea_chancay", "Chancay", "P0", "services (1 dropped call); COSCO-operated deep-water port opened Nov 2024 (China-Peru trans-Pacific service)",
         sourced_cap={"teu_day": round(1_000_000 / 365), "note": "Phase 1 design capacity 1.0 million TEU per year (Container News, 'Peruvian COSCO terminal commences operations', Nov 2024) / 365 = 2,740 TEU/day; one report quotes 1.2 million (Seatrade). The registry (62 calls) pre-dates the opening, so the SOURCED design figure is used instead of the registry ESTIMATE.",
                      "source": "https://container-news.com/peruvian-cosco-terminal-commences-operations/ ; https://www.seatrade-maritime.com/ports-logistics/cosco-shipping-terminal-in-peru-to-start-operations-this-month (search results, pages not machine-verified; retrieved 2026-10-25)"})
add_port("port1118", "sea_safi", "Safi", "P0", "services (1 dropped call); Morocco Atlantic port on the Asia-Mediterranean Cape-route loops")
add_port("port9", "sea_aden", "Aden", "P0", "Gulf of Aden gate port 40 km from Bab el-Mandeb; Yemen container terminal; CPPI 2023 rank 237")
add_port("port2129", "sea_sidikerir", "Sidi Kerir (SUMED terminal)", "P0", "Mediterranean end of the SUMED pipeline, the only Suez-Canal oil bypass (2.5 mb/d)")
add_port("port162", "sea_botasceyhan", "Ceyhan (BOTAS crude terminal)", "P1", "Kirkuk-Ceyhan and BTC crude export terminal; Hormuz bypass for Iraqi crude when the line operates")
add_port("port561", "sea_khorfakkan", "Khor Fakkan", "P1", "Gulf-of-Oman container port outside Hormuz; named in the Hapag-Lloyd 2024 land-corridor logic; Lloyd's List tail (1.8 million TEU)")
add_port("port2164", "sea_kharg", "Kharg Island", "P1", "Iran's main crude export terminal inside the Gulf (registry under-counts: AIS gaps)")
add_port("port1136", "sea_salinacruz", "Salina Cruz", "P1", "Pacific end of the Isthmus of Tehuantepec Interoceanic Corridor (Panama alternative)")
add_port("port2150", "sea_vizhinjam", "Vizhinjam", "P1", "India's first deep-water transshipment port (Colombo competitor), opened 2025")
add_port("port2033", "sea_yeosu", "Yeosu", "P1", "CPPI 2023 rank 20; Korean south-coast energy and container port 20 km from Gwangyang")
add_port("port656", "sea_lobito", "Lobito", "P2", "Atlantic head of the Lobito corridor rail to DRC/Zambia")
add_port("port784", "sea_nacala", "Nacala", "P2", "Indian Ocean head of the Nacala corridor (Malawi, Zambia)")
add_port("port1255", "sea_takoradi", "Takoradi", "P2", "Ghana's second port; West Africa coverage")
add_port("port146", "sea_berbera", "Berbera", "P2", "CPPI 2023 rank 106; DP World Berbera, Gulf of Aden")
add_port("port984", "sea_duqm", "Duqm", "P2", "CPPI 2023 rank 158; Oman Arabian Sea port outside Hormuz")
add_port("port678", "sea_hambantota", "Hambantota", "P2", "Sri Lanka south-coast deep-water port on the main east-west lane")
add_port("port263", "sea_coronel", "Coronel", "P2", "CPPI 2023 rank 97; Chile (Biobio)")
add_port("port1388", "sea_wellington", "Wellington", "P2", "CPPI 2023 rank 112; New Zealand capital port")

# air hubs (SHELL)
for iata, nid, pr, why in (("LUX", "air_luxembourg", "P1", "Cargolux hub; ACI 2015 rank 29 (779,478 t), absent from graph"),
                           ("AUH", "air_abudhabi", "P1", "Etihad Cargo hub; ACI 2015 rank 28 (837,551 t), absent from graph")):
    o = OURA[iata]
    t = ACI[iata]["tonnes"]
    cap = int(round(t / 365.0))
    p = {"id": nid, "name": o["name"], "aliases": [iata, o["icao_code"]], "mode": "air", "kind": "air_hub",
         "country": {"LUX": "Luxembourg", "AUH": "United Arab Emirates"}[iata],
         "lat": round(float(o["latitude_deg"]), 6), "lon": round(float(o["longitude_deg"]), 6),
         "capacity": cap, "capacity_unit": "tonnes/day", "queue_cap": 2 * cap, "inventory_days": 3.0,
         "tier": "shell", "capacity_basis": "ESTIMATE",
         "capacity_note": f"ACI World 2015 annual cargo {t:,} tonnes (Wikipedia compilation of ACI figures, rank {ACI[iata]['rank']}) / 365 = {cap:,} t/day. 11-year-old figure: a labelled ESTIMATE, not a current throughput.",
         "sources": [URL_OURAIRPORTS, URL_ACI_WP], "activity": {"kind": "aci_annual_cargo_tonnes_2015", "value": t},
         "priority": pr, "why": why, "provenance": prov(URL_ACI_WP)}
    places.append(p)
    PLACES_BY_ID[nid] = p

# ground nodes (SHELL, coordinates + counts only; capacity NOT_PUBLISHED)
CRE = {"Xi'an": ("gnd_xian", "Xi'an International Port (Chang'an)", 3849), "Chengdu": ("gnd_chengdu", "Chengdu International Railway Port", 2285),
       "Chongqing": ("gnd_chongqing", "Chongqing Tuanjie Village Station", 2059), "Zhengzhou": ("gnd_zhengzhou", "Zhengzhou International Hub", 2052),
       "Yiwu": ("gnd_yiwu", "Yiwu West", 812)}
for city, (nid, nm, trips) in CRE.items():
    la, lo = WPC[city]
    p = {"id": nid, "name": nm, "aliases": [], "mode": "ground", "kind": "ground_node", "country": "China",
         "lat": round(la, 5), "lon": round(lo, 5), "capacity": 1500, "capacity_unit": "trucks/day", "queue_cap": 1500, "inventory_days": 5.0,
         "tier": "shell", "capacity_basis": "NOT_PUBLISHED",
         "capacity_note": "NOT_PUBLISHED: no per-hub throughput retrieved; the MODELED ground shell default (1,500) applies. Coordinates are the CITY centroid (+-30 km), not the terminal.",
         "sources": [URL_WP_REST + city.replace(" ", "_").replace("'", "%27"), "https://railmarket.com/news/freight-rail/28943-china-europe-19-000-freight-trains-in-2024"],
         "activity": {"kind": "china_europe_train_operation_trips_2024", "value": trips,
                      "note": "New Silk Road Discovery / CRCT, via railmarket.com search summary; trips may count both directions"},
         "routing_only": True, "priority": "P1", "why": "China-Europe rail origin hub (top-5 by 2024 trips); the four CORE border nodes have no inbound leg today",
         "provenance": prov("https://railmarket.com/news/freight-rail/28943-china-europe-19-000-freight-trains-in-2024")}
    places.append(p)
    PLACES_BY_ID[nid] = p

# ----------------------------------------------------------------------------
# 2. LEGS
# ----------------------------------------------------------------------------
legs: dict = {}                 # key -> leg dict
pending_r_cap: list = []        # primaries awaiting an R-CAP share


def node(i):
    return N.get(i) or PLACES_BY_ID[i]


def tier_of(i):
    return node(i)["tier"]


def leg_tier(a, b):
    ta, tb = tier_of(a), tier_of(b)
    if ta == "core" and tb == "core":
        return "core"
    if {ta, tb} <= {"core", "core_ext"}:
        return "core_ext"
    return "shell"


def sea_rule_days(km):
    return round(max(0.3, km / 889.0), 1)


def ref_days(new_chain, ref_key, ref_chain, floor_km=300.0):
    """R-ROUTE: new leg days = new route km x (reference leg days / reference route km).
    Falls back to the 889 km/day shell rule when the reference route is shorter than floor_km."""
    km_new = chain_km(new_chain)
    ek = EKEY[ref_key]
    km_ref = chain_km(ref_chain)
    if km_ref < floor_km:
        return sea_rule_days(km_new), km_new, "889 km/day shell rule (reference leg too short for a ratio)"
    d = round(max(0.3, km_new * ek["base_transit_d"] / km_ref), 1)
    return d, km_new, f"R-ROUTE: {km_new:,.0f} km x {ek['base_transit_d']} d / {km_ref:,.0f} km of reference leg {ref_key}"


def add_leg(frm, to, mode, role, block, priority, chain=None, ref=None, weight=None, why="", medium=None,
            leg_capacity=None, sources=None, tau=None, tau_basis=None, share=None, share_basis=None, extra=None):
    key = f"{frm}->{to}:{mode}"
    assert key not in EKEY, f"collides with graph leg {key}"
    assert key not in legs, f"duplicate extra leg {key}"
    assert frm in N or frm in PLACES_BY_ID, frm
    assert to in N or to in PLACES_BY_ID, to
    if tau is None:
        ch = chain or [frm, to]
        if ref:
            tau, km, tau_basis = ref_days(ch, ref[0], ref[1])
        else:
            km = chain_km(ch)
            if mode == "sea":
                tau, tau_basis = sea_rule_days(km), "889 km/day shell rule on routed/great-circle km"
            elif mode == "ground":
                tau, tau_basis = round(max(0.3, km / 1320.0), 1), "1,320 km/day shell ground rule"
            else:
                tau, tau_basis = round(max(0.2, km / 19200.0), 1), "19,200 km/day shell air rule"
    else:
        km = chain_km(chain) if chain else hv_km(*pt(frm), *pt(to))
    leg = {"key": key, "from": frm, "to": to, "mode": mode, "base_transit_d": tau, "role": role, "block": block,
           "priority": priority, "tier": leg_tier(frm, to), "route_km": round(km, 1),
           "route": [c for c in (chain or [frm, to])], "why": why,
           "calibration": {"basis": "MODELED", "volume_source": "IMF PortWatch (attraction weights / observed transits); sidecar rule recorded in capacity_share_basis",
                           "transit_basis": f"MODELED - {tau_basis}"}}
    if medium:
        leg["medium"] = medium
    if leg_capacity:
        leg["leg_capacity"] = leg_capacity
    if sources:
        leg["sources"] = sources
    if extra:
        leg.update(extra)
    if share is not None:
        leg["capacity_share"], leg["capacity_share_basis"] = share, share_basis
    else:
        leg["_weight"] = weight if weight is not None else 1.0
        pending_r_cap.append(key)
    legs[key] = leg
    return leg


def attraction(i):
    """Destination attraction weight for apportionment: observed vessel calls."""
    n = node(i)
    if n["kind"] == "chokepoint":
        pw = {"chk_suez": "chokepoint1", "chk_panama": "chokepoint2", "chk_turkish": "chokepoint3", "chk_babelmandeb": "chokepoint4",
              "chk_malacca": "chokepoint5", "chk_hormuz": "chokepoint6", "chk_capeofgoodhope": "chokepoint7", "chk_gibraltar": "chokepoint8",
              "chk_dover": "chokepoint9", "chk_danish": "chokepoint10", "chk_taiwan": "chokepoint11"}.get(i)
        if pw:
            return float(PWD[pw]["median_daily_transits"]) * 365
        if "activity" in n:
            return 1.0
        return float(n["capacity"]) * 365
    if n["tier"] == "shell":
        a = (n.get("activity") or {}).get("vessel_count_total") or SHELL_CALLS.get(i)
        return float(a) if a else 1.0
    return float(JOIN.get(i) or n["capacity"])


# ---- 2a. structural corridor legs (CORE / core_ext endpoints) -----------------
REF_COL_BAB = ("sea_colombo->chk_babelmandeb:sea", ["sea_colombo", "CMB", "IND2", "IND1", "GUARD", "ADEN", "chk_babelmandeb"])
REF_SUEZ_JED = ("chk_suez->sea_jeddah:sea", ["chk_suez", "GSUEZ", "RS1", "RS2", "sea_jeddah"])
REF_SUEZ_PS = ("chk_suez->sea_portsaid:sea", ["chk_suez", "sea_portsaid"])
REF_JA_HZ = ("sea_jebelali->chk_hormuz:sea", ["sea_jebelali", "HZ2", "chk_hormuz"])
REF_HZ_MUNDRA = ("chk_hormuz->sea_mundra:sea", ["chk_hormuz", "GOMAN2", "ARAB3", "sea_mundra"])
REF_TW_SH = ("chk_taiwan->sea_shanghai:sea", ["chk_taiwan", "sea_shanghai"])
REF_HAM_DK = ("sea_hamburg->chk_danish:sea", ["sea_hamburg", "ELBE", "NSEA2", "SKAGEN", "KATTEGAT", "chk_danish"])
REF_ROT_DV = ("sea_rotterdam->chk_dover:sea", ["sea_rotterdam", "NSEA1", "chk_dover"])


def both(a, b, mode, role, block, pri, chain, ref=None, why="", **kw):
    add_leg(a, b, mode, role, block, pri, chain=chain, ref=ref, why=why, **kw)
    add_leg(b, a, mode, role, block, pri, chain=list(reversed(chain)), ref=ref, why=why, **kw)


# Red Sea / Suez / Bab el-Mandeb (block RS) --------------------------------------
add_leg("sea_portsaid", "chk_suez", "sea", "transit", "RS", "P0", chain=["sea_portsaid", "chk_suez"], ref=REF_SUEZ_PS,
        why="northern canal entrance: the graph has Suez->Port Said but no Port Said->Suez, so Europe/Mediterranean to Asia has no canal leg")
both("chk_suez", "chk_babelmandeb", "sea", "transit", "RS", "P0", ["chk_suez", "GSUEZ", "RS1", "RS2", "RS3", "RS4", "chk_babelmandeb"], REF_SUEZ_JED,
     why="Red Sea transit: Suez and Bab el-Mandeb are connected today only through Jeddah and only westbound")
add_leg("sea_jeddah", "chk_babelmandeb", "sea", "transit", "RS", "P0", chain=["sea_jeddah", "RS2", "RS3", "RS4", "chk_babelmandeb"], ref=REF_SUEZ_JED,
        why="southbound Red Sea exit: Bab el-Mandeb has one inbound leg (Colombo) and one outbound (Jeddah); there is no Jeddah->Bab")
add_leg("chk_babelmandeb", "sea_colombo", "sea", "transit", "RS", "P0", chain=list(reversed(REF_COL_BAB[1])), ref=REF_COL_BAB,
        why="eastbound exit toward the Indian Ocean (reverse of the only inbound leg)")
for port, via in (("sea_salalah", ["ARAB1", "GUARD", "ADEN"]), ("sea_jebelali", ["HZ2", "GOMAN", "GOMAN2", "ARAB2", "IND1", "GUARD", "ADEN"]),
                  ("sea_mundra", ["ARAB3", "IND1", "GUARD", "ADEN"]), ("sea_nhavasheva", ["IND1", "GUARD", "ADEN"]),
                  ("sea_karachi", ["ARAB3", "IND1", "GUARD", "ADEN"])):
    both(port, "chk_babelmandeb", "sea", "transit", "RS", "P0", [port] + via + ["chk_babelmandeb"], REF_COL_BAB,
         why="Arabian Sea / Gulf port to the Red Sea gate: only Colombo reaches Bab el-Mandeb today")
for shp in ("sea_djibouti", "sea_aden", "sea_portsudan"):
    ch = [shp, "chk_babelmandeb"] if shp != "sea_portsudan" else ["sea_portsudan", "RS3", "RS4", "chk_babelmandeb"]
    both(shp, "chk_babelmandeb", "sea", "transit", "RS", "P0" if shp == "sea_aden" else "P1", ch, REF_SUEZ_JED,
         why="Gulf of Aden / southern Red Sea port on the gate")
both("sea_elsokhna", "chk_suez", "sea", "transit", "RS", "P1", ["sea_elsokhna", "chk_suez"], REF_SUEZ_PS,
     why="Gulf-of-Suez port (Ain Sokhna): the southern canal anchorage and SUMED terminal")

# Hormuz (HZ) ----------------------------------------------------------------------
for dst, ch, ref in (("sea_jebelali", ["chk_hormuz", "HZ2", "sea_jebelali"], REF_JA_HZ), ("sea_khalifa", ["chk_hormuz", "HZ2", "sea_khalifa"], REF_JA_HZ),
                     ("sea_hamad", ["chk_hormuz", "HZ2", "PGMID", "sea_hamad"], REF_JA_HZ),
                     ("sea_karachi", ["chk_hormuz", "GOMAN", "ARAB3", "sea_karachi"], REF_HZ_MUNDRA),
                     ("sea_nhavasheva", ["chk_hormuz", "GOMAN2", "ARAB3", "IND1", "sea_nhavasheva"], REF_HZ_MUNDRA)):
    add_leg("chk_hormuz", dst, "sea", "transit", "HZ", "P0", chain=ch, ref=ref,
            why="Hormuz exit leg missing: today Hormuz->{Dammam, Mundra, Salalah} only, so a Hormuz closure does not reach Jebel Ali, Khalifa, Hamad or Karachi inbound")
add_leg("sea_hamad", "chk_hormuz", "sea", "transit", "HZ", "P0", chain=["sea_hamad", "PGMID", "HZ2", "chk_hormuz"], ref=REF_JA_HZ,
        why="Qatar's only container port has no Hormuz leg at all")

# Gibraltar (GB) -------------------------------------------------------------------
REF_ALG_GIB = ("sea_algeciras->chk_gibraltar:sea", ["sea_algeciras", "chk_gibraltar"])
for src, ch in (("sea_valencia", ["sea_valencia", "MEDW", "chk_gibraltar"]), ("sea_barcelona", ["sea_barcelona", "MEDW", "chk_gibraltar"]),
                ("sea_marsaxlokk", ["sea_marsaxlokk", "MEDC", "MEDW", "chk_gibraltar"]), ("sea_genoa", ["sea_genoa", "MEDW", "chk_gibraltar"]),
                ("sea_portsaid", ["sea_portsaid", "MEDE", "MEDC", "MEDW", "chk_gibraltar"]), ("sea_piraeus", ["sea_piraeus", "MEDE", "MEDC", "MEDW", "chk_gibraltar"])):
    add_leg(src, "chk_gibraltar", "sea", "transit", "GB", "P0", chain=ch, why="Mediterranean port to the Atlantic gate: Gibraltar has only Algeciras and Sines inbound")
for dst, ch in (("sea_algeciras", ["chk_gibraltar", "sea_algeciras"]), ("sea_sines", ["chk_gibraltar", "GIBW", "sea_sines"]),
                ("sea_lehavre", ["chk_gibraltar", "GIBW", "FINIST", "USHANT", "sea_lehavre"]), ("sea_antwerp", ["chk_gibraltar", "GIBW", "FINIST", "USHANT", "DOVERG", "sea_antwerp"]),
                ("sea_rotterdam", ["chk_gibraltar", "GIBW", "FINIST", "USHANT", "DOVERG", "sea_rotterdam"])):
    add_leg("chk_gibraltar", dst, "sea", "transit", "GB", "P0", chain=ch, why="Atlantic exit: Gibraltar has one outbound leg (Tanger Med)")

# Dover (DV) -----------------------------------------------------------------------
for p_, ch in (("sea_antwerp", ["sea_antwerp", "NSEA1", "chk_dover"]), ("sea_hamburg", ["sea_hamburg", "ELBE", "NSEA2", "NSEA1", "chk_dover"]),
               ("sea_bremerhaven", ["sea_bremerhaven", "NSEA2", "NSEA1", "chk_dover"])):
    both(p_, "chk_dover", "sea", "transit", "DV", "P0", ch, REF_ROT_DV, why="North Sea port to the Dover Strait (Dover has 3 inbound and 2 outbound legs)")
add_leg("chk_dover", "sea_rotterdam", "sea", "transit", "DV", "P0", chain=["chk_dover", "NSEA1", "sea_rotterdam"], ref=REF_ROT_DV,
        why="reverse of the only inbound North Sea leg")

# Taiwan Strait (TW) ---------------------------------------------------------------
for p_ in ("sea_ningbo", "sea_shanghai", "sea_hongkong", "sea_shenzhen", "sea_taichung"):
    ch = {"sea_ningbo": ["sea_ningbo", "chk_taiwan"], "sea_shanghai": ["sea_shanghai", "chk_taiwan"], "sea_hongkong": ["sea_hongkong", "HK_APP", "chk_taiwan"],
          "sea_shenzhen": ["sea_shenzhen", "HK_APP", "chk_taiwan"], "sea_taichung": ["sea_taichung", "chk_taiwan"]}[p_]
    add_leg(p_, "chk_taiwan", "sea", "transit", "TW", "P0", chain=ch, ref=REF_TW_SH,
            why="the Taiwan Strait carries Shanghai/Ningbo-Hong Kong/Shenzhen traffic (31,190 container-vessel days/yr); the graph's inbound legs are Fuzhou, Kaohsiung, Keelung, Xiamen only")
for p_ in ("sea_ningbo", "sea_shenzhen", "sea_taichung", "sea_kaohsiung", "sea_xiamen"):
    ch = {"sea_ningbo": ["chk_taiwan", "sea_ningbo"], "sea_shenzhen": ["chk_taiwan", "HK_APP", "sea_shenzhen"], "sea_taichung": ["chk_taiwan", "sea_taichung"],
          "sea_kaohsiung": ["chk_taiwan", "sea_kaohsiung"], "sea_xiamen": ["chk_taiwan", "sea_xiamen"]}[p_]
    add_leg("chk_taiwan", p_, "sea", "transit", "TW", "P0", chain=ch, ref=REF_TW_SH, why="Taiwan Strait exit leg missing")

# Malacca / Sunda / Lombok (MA) ----------------------------------------------------
add_leg("chk_malacca", "sea_portklang", "sea", "transit", "MA", "P1", chain=["chk_malacca", "sea_portklang"], why="reverse of an existing inbound leg")
add_leg("chk_malacca", "sea_tanjungpelepas", "sea", "transit", "MA", "P1", chain=["chk_malacca", "sea_tanjungpelepas"], why="reverse of an existing inbound leg")
for gate in ("chk_sunda", "chk_lombok"):
    for p_ in ("sea_singapore", "sea_colombo"):
        ch = {("chk_sunda", "sea_singapore"): ["sea_singapore", "KARIMATA", "JAVA1", "chk_sunda"],
              ("chk_sunda", "sea_colombo"): ["sea_colombo", "SIO3", "SIO2", "SIO1", "chk_sunda"],
              ("chk_lombok", "sea_singapore"): ["sea_singapore", "KARIMATA", "JAVA2", "chk_lombok"],
              ("chk_lombok", "sea_colombo"): ["sea_colombo", "SIO3", "SIO2", "SIO5", "SIO4", "chk_lombok"]}[(gate, p_)]
        both(p_, gate, "sea", "transit", "MA", "P0", ch, why="Malacca alternative: Sunda/Lombok connect Singapore and Colombo (the authored singapore->colombo bypass models this as one direct leg)")
both("sea_tanjungpriok", "chk_sunda", "sea", "transit", "MA", "P0", ["sea_tanjungpriok", "chk_sunda"], why="Jakarta's Indian Ocean approach is the Sunda Strait")
both("sea_tanjungperak", "chk_lombok", "sea", "transit", "MA", "P1", ["sea_tanjungperak", "JAVA2", "chk_lombok"], why="Surabaya's southern exit is the Lombok Strait")

# Panama Railway and landbridges (ground, medium != sea) ----------------------------
PCR_SRC = ["https://panarail.com/en/cargo/main.html (fetched 2026-10-25: 47-mile single track Balboa-Colon; 10 trains each direction per 24 h, 'possibility to increase to 32 daily train runs'; Phase I about 500,000 container moves per year; stated maximum 2 million TEU per year)"]
both("sea_balboa", "sea_colon", "ground", "alt_medium", "PANRAIL", "P1", ["sea_balboa", "sea_colon"], medium="rail",
     leg_capacity={"value": 500000, "unit": "container moves/year (Phase I); max 2,000,000 TEU/year", "basis": "panarail.com, operator statement"},
     sources=PCR_SRC, tau=0.3, tau_basis="1,320 km/day ground rule floor (0.3 d); the railway states about 1.5 h line time, handling dominates",
     why="Panama Railway: containers cross the isthmus without the canal; no ground leg joins Balboa and Colon today")
SAUDI_SRC = ["Maritime Executive, 'Hapag-Lloyd offers Saudi Arabia land routes as Red Sea diversions continue' (Jan 2024): land corridors from Jebel Ali, Dammam and Jubail to Jeddah https://maritime-executive.com/article/hapag-lloyd-offers-saudi-arabia-land-routes-as-red-sea-diversions-continue (search result, page not machine-verified; retrieved 2026-10-25)"]
for a, b in (("sea_dammam", "sea_jeddah"), ("sea_jebelali", "sea_jeddah")):
    both(a, b, "ground", "alt_medium", "SAUDILB", "P1", [a, b], medium="road/rail", sources=SAUDI_SRC,
         why="Saudi land corridor: Gulf port to Red Sea port by truck/rail (Hapag-Lloyd 2024); no ground leg joins them today")
TEH_SRC = ["Datamarnews / AFP via search results (retrieved 2026-10-25): Interoceanic Corridor of the Isthmus of Tehuantepec, 303-308 km line Salina Cruz - Coatzacoalcos; government plan of six trains x 260 TEU, two trips a day = 1.14 million TEU/year maximum; 300,000 containers/year by 2028. PLANNED capacity, operating volumes not confirmed; pages not machine-verified."]
both("sea_salinacruz", "sea_coatzacoalcos", "ground", "alt_medium", "TEHUANTEPEC", "P1", ["sea_salinacruz", "sea_coatzacoalcos"], medium="rail",
     leg_capacity={"value": 1_140_000, "unit": "TEU/year (government maximum, planned)", "basis": "six trains x 260 TEU x 2 trips/day x 365"}, sources=TEH_SRC,
     why="Tehuantepec rail landbridge: Pacific-Gulf container link that competes with Panama; no Salina Cruz place or leg exists")
# pipelines: topology-only, share 0.0 (no liquid-bulk layer equivalence exists)
PIPES = [
    ("sea_elsokhna", "sea_sidikerir", "SUMED (Ain Sokhna - Sidi Kerir), 320 km", 2_500_000, "https://en.wikipedia.org/wiki/Sumed_pipeline (fetched 2026-10-25; cites Khaleej Times 2007; EIA gives the same approximate figure per search result)", "P0"),
    ("sea_rastanura", "sea_yanbukingfahdport", "East-West / Petroline (Abqaiq - Yanbu), 1,201 km; Ras Tanura is the Gulf-side proxy for Abqaiq", 7_000_000, "https://en.wikipedia.org/wiki/East%E2%80%93West_Crude_Oil_Pipeline (fetched 2026-10-25: stated capacity 7 Mbbl/d after 2026 conversion, citing Middle East Eye and EIA; earlier 5 Mbbl/d)", "P1"),
    ("sea_khalifa", "sea_fujairah", "ADCOP (Habshan - Fujairah), 406 km; Khalifa Port is the Abu Dhabi-side proxy for Habshan", 1_500_000, "https://en.wikipedia.org/wiki/Abu_Dhabi_Crude_Oil_Pipeline (fetched 2026-10-25: 1.5 million b/d, uncited sentence; Gulf News 2008)", "P1"),
]
for a, b, nm, cap, src, pri in PIPES:
    add_leg(a, b, "ground", "alt_medium", "PIPE", pri, chain=[a, b], medium="pipeline", share=0.0,
            share_basis="DORMANT_ZERO: no liquid-bulk layer equivalence exists in the engine (capacity units are TEU, vessels, trucks, tonnes); the leg carries topology and a sourced barrels/day attribute only",
            leg_capacity={"value": cap, "unit": "barrels/day", "basis": nm}, sources=[src],
            tau=1.0, tau_basis="MODELED 1.0 d pipeline residence (placeholder; no source)",
            extra={"engine_use": "topology_only"}, why="Suez / Hormuz oil bypass pipelines are absent from the graph")

# Eurasian rail hubs -> CORE border crossings and nearest CORE port --------------------
BORDERS = ["gnd_dostyk", "gnd_khorgos", "gnd_erenhot", "gnd_manzhouli"]
for hub in CRE.values():
    hid = hub[0]
    near = sorted((hv_km(*pt(hid), *pt(b)), b) for b in BORDERS)[:2]
    for d, b in near:
        add_leg(hid, b, "ground", "primary", "EURAIL", "P1", chain=[hid, b], medium="rail",
                why="R-EURAIL: each China-Europe origin hub links to its 2 nearest CORE border crossings (those four nodes have no inbound leg today)")
    # nearest CORE port (S6 rule)
    dp, pid = min((hv_km(*pt(hid), n["lat"], n["lon"]), i) for i, n in N.items() if n["kind"] == "port" and n["tier"] == "core")
    add_leg(hid, pid, "ground", "primary", "EURAIL", "P1", chain=[hid, pid], medium="rail", why="S6: nearest CORE port (rail-sea link)")

# S6 peer links between the new hubs (nearest other new hub within 800 km, else the nearest within 3,000 km), symmetric
_hubs = [h[0] for h in CRE.values()]
for h in _hubs:
    cand = sorted((hv_km(*pt(h), *pt(o)), o) for o in _hubs if o != h)
    near = [c for c in cand if c[0] <= 800.0][:1] or cand[:1]
    for d, o in near:
        for a, b in ((h, o), (o, h)):
            if f"{a}->{b}:ground" not in legs:
                add_leg(a, b, "ground", "primary", "EURAIL", "P1", chain=[a, b], medium="rail", why="S6 peer link between China-Europe origin hubs")

# Black Sea / Baltic / Mozambique shell gates (block SHELLGATE) --------------------
def shell_gate(gate, ports, block, pri, why):
    for p_ in ports:
        if p_ not in N and p_ not in PLACES_BY_ID:
            continue
        ch = [p_, gate]
        both(p_, gate, "sea", "transit", block, pri, ch, why=why)


shell_gate("chk_turkish", ["sea_novorossiysk", "sea_odessa", "sea_chornomorsk", "sea_poti", "sea_batumi", "sea_varna"], "BS", "P1",
           "Black Sea port to the Turkish Straits: only Constanta and Ambarli/Piraeus reach the gate today")
shell_gate("chk_danish", ["sea_saintpetersburg", "sea_ustluga", "sea_primorsk", "sea_gteborg", "sea_kbenhavn", "sea_helsinki", "sea_muugaportoftallinn",
                          "sea_riga", "sea_klaipeda", "sea_malm", "sea_aarhus", "sea_rostock", "sea_kaliningrad"], "DK", "P1",
           "Baltic port to the Danish Straits: 3 of 62 Baltic-region ports have a leg to the gate today (Gdansk, Gdynia, Hamburg)")
shell_gate("chk_mozambique", ["sea_maputo", "sea_beira", "sea_daressalaam", "sea_nacala"], "MZ", "P1",
           "Mozambique Channel port: only Colombo, Durban and Mombasa reach the gate today")
shell_gate("chk_hormuz", ["sea_rastanura", "sea_raslaffan", "sea_kharg", "sea_ummqasr", "sea_shuaiba", "sea_jubail"], "HZ", "P1",
           "Gulf energy / container port to Hormuz: tanker ports behind the strait have no gate leg (Ras Laffan 1,634 tanker calls)")
# Kiel and Magellan (P2 places need at least one leg each way)
for p_ in ("sea_hamburg", "sea_bremerhaven"):
    both(p_, "chk_kiel", "sea", "transit", "KC", "P2", [p_, "ELBE", "chk_kiel"], why="Elbe entrance to the Kiel Canal")
for p_ in ("sea_gdansk", "sea_gdynia"):
    both("chk_kiel", p_, "sea", "transit", "KC", "P2", ["chk_kiel", "KIELB", "BALT1", p_], why="Baltic exit of the Kiel Canal")
for p_, ch in (("sea_sanantonio", None), ("sea_callao", None), ("sea_buenosaires", None), ("sea_santos", None)):
    both(p_, "chk_magellan", "sea", "transit", "MG", "P2", [p_, "chk_magellan"], why="Strait of Magellan: Pacific-Atlantic route for Chile/Peru-Argentina/Brazil traffic that avoids Panama")

# ---- 2b. SHELL construction S1/S2/S3 for new shell places ------------------------
NEW_SHELL_SEA = [p for p in places if p["kind"] == "port"]
all_shell_sea = {i: n for i, n in N.items() if n["kind"] == "port" and n["tier"] == "shell"}
all_shell_sea.update({p["id"]: p for p in NEW_SHELL_SEA})
core_ports = {i: n for i, n in N.items() if n["kind"] == "port" and n["tier"] == "core"}
S3_USED = collections.Counter()
for e in EDGES:
    if e["mode"] == "sea" and N[e["from"]]["tier"] == "core" and N[e["to"]]["tier"] == "shell":
        S3_USED[e["from"]] += 1
SUM_EXTRA = collections.defaultdict(float)     # (node, mode) -> extra share allocated by S rules


def left(frm, mode):
    return 1.0 - OUT[(frm, mode)] - SUM_EXTRA[(frm, mode)]


def S_construct(origins, hubs, peers_pool, peer_k, peer_radius, budget_units, mode, tfn, weighted, rules, block, pri):
    out_links = {}
    for o in origins:
        oid = o["id"]
        others = [c for c in peers_pool if c["id"] != oid]
        nearest = lambda pool, k, mx=None: [(d, c["id"]) for d, c in sorted(  # noqa: E731
            ((hv_km(o["lat"], o["lon"], c["lat"], c["lon"]), c) for c in pool), key=lambda t: (t[0], t[1]["id"])) if mx is None or d <= mx][:k]
        peers = nearest(others, peer_k, peer_radius) or nearest(others, 1, peer_radius * 3 if peer_radius else None)
        out_links[oid] = [(hid, tfn(d), rules[0]) for d, hid in nearest(hubs, 2)] + [(qid, tfn(d), rules[1]) for d, qid in peers]
    # peer symmetry: reverse link to EXISTING shell peers is headroom-capped (their shares are fixed)
    reverse = []
    for oid, links in out_links.items():
        for dest, tr, rule in links:
            if rule == rules[1] and dest not in out_links:
                reverse.append((dest, oid, tr, rule))
            elif rule == rules[1] and oid not in [d for d, _, _ in out_links[dest]]:
                out_links[dest].append((oid, tr, rule))
    for oid, links in sorted(out_links.items()):
        if not links:
            continue
        ws = [(d, attraction(d) if weighted else 1.0) for d, _, _ in sorted(links)]
        units = apportion(budget_units, ws)
        for dest, tr, rule in links:
            key = f"{oid}->{dest}:{mode}"
            if key in EKEY or key in legs:
                continue
            add_leg(oid, dest, mode, "primary", block, pri, tau=tr, tau_basis=f"{tfn.__doc__}", chain=[oid, dest],
                    share=units[dest] / 10000.0,
                    share_basis=f"{rule}: largest-remainder apportionment of {budget_units/10000:.2f} outbound budget by destination observed vessel calls",
                    why=f"SHELL construction {rule}", extra={"rule": rule})
            SUM_EXTRA[(oid, mode)] += units[dest] / 10000.0
            if rule == rules[0] and tier_of(dest) == "core" and S3_USED[dest] < 3:
                lo = left(dest, mode)
                if lo >= 0.01:
                    sh = round(min(0.03, lo / 2), 4)
                    rk = f"{dest}->{oid}:{mode}"
                    if rk not in EKEY and rk not in legs and sh > 0:
                        add_leg(dest, oid, mode, "primary", block, pri, tau=tr, tau_basis=f"{tfn.__doc__}", chain=[dest, oid], share=sh,
                                share_basis="S3: CORE->shell reverse; share min(0.03, half the origin's remaining outbound budget); at most 3 per CORE node (counting the 3 already used in graph.json)",
                                why="SHELL construction S3", extra={"rule": "S3"})
                        SUM_EXTRA[(dest, mode)] += sh
                        S3_USED[dest] += 1
    for dest, oid, tr, rule in sorted(reverse):
        key = f"{dest}->{oid}:{mode}"
        lo = left(dest, mode)
        if key in EKEY or key in legs or lo < 0.01:
            continue
        sh = round(min(0.03, lo / 2), 4)
        add_leg(dest, oid, mode, "primary", block, pri, tau=tr, tau_basis=f"{tfn.__doc__}", chain=[dest, oid], share=sh,
                share_basis=f"{rule} reverse onto an EXISTING shell peer: its shares are fixed in graph.json, so min(0.03, half its remaining outbound budget)",
                why=f"SHELL construction {rule} (reverse)", extra={"rule": rule + "r"})
        SUM_EXTRA[(dest, mode)] += sh


def sea_t(d):
    """MODELED shell sea speed 889 km/day (20 kn), floor 0.3 d"""
    return round(max(0.3, d / 889.0), 1)


def air_t(d):
    """MODELED shell air speed 19,200 km/day (800 km/h), floor 0.2 d"""
    return round(max(0.2, d / 19200.0), 1)


def gnd_t(d):
    """MODELED shell ground speed 1,320 km/day (55 km/h), floor 0.3 d"""
    return round(max(0.3, d / 1320.0), 1)


for p in NEW_SHELL_SEA:
    p.setdefault("lat", p["lat"])
S_construct(NEW_SHELL_SEA, list(core_ports.values()), list(all_shell_sea.values()), 2, 1500.0, 6000, "sea", sea_t, True, ("S1", "S2"), "SHELLNEW", "P0")
NEW_AIR = [p for p in places if p["kind"] == "air_hub"]
core_air = [n for n in N.values() if n["kind"] == "air_hub" and n["tier"] == "core"]
shell_air = [n for n in N.values() if n["kind"] == "air_hub" and n["tier"] == "shell"] + NEW_AIR
S_construct(NEW_AIR, core_air, shell_air, 2, 3000.0, 5000, "air", air_t, False, ("S4", "S5"), "SHELLNEW", "P1")

# R-CAP shares for the structural legs ---------------------------------------------
by_origin = collections.defaultdict(list)
for k in pending_r_cap:
    by_origin[(legs[k]["from"], legs[k]["mode"])].append(k)
for (frm, mode), keys in sorted(by_origin.items()):
    head = max(0.0, 1.0 - OUT[(frm, mode)] - SUM_EXTRA[(frm, mode)])
    cap = 0.20 if (N.get(frm) or PLACES_BY_ID[frm])["kind"] == "chokepoint" else 0.10
    budget = round(min(cap, head / 2), 4)
    ws = [(legs[k]["to"], attraction(legs[k]["to"])) for k in sorted(keys)]
    units = apportion(int(round(budget * 10000)), sorted(ws))
    for k in sorted(keys):
        l = legs[k]
        l["capacity_share"] = units[l["to"]] / 10000.0
        l["capacity_share_basis"] = (f"R-CAP: budget min({cap}, half of the origin's remaining outbound headroom {head:.4f}) = {budget}, "
                                      f"apportioned over this origin's new {mode} legs by destination observed calls/transits (MODELED rule; no source for the true split)")
        del l["_weight"]
    SUM_EXTRA[(frm, mode)] += sum(units.values()) / 10000.0
for l in legs.values():
    l.pop("_weight", None)

# S3-style reverse legs into the China hubs (Europe-to-China return trains): CORE border / port -> hub
for k in sorted(k for k, l in legs.items() if l["block"] == "EURAIL" and l["from"] in PLACES_BY_ID and l["mode"] == "ground"):
    l = legs[k]
    dest = l["to"]
    rk = f"{dest}->{l['from']}:ground"
    lo = left(dest, "ground")
    if rk in EKEY or rk in legs or S3_USED[dest] >= 3 or lo < 0.01:
        continue
    sh = round(min(0.03, lo / 2), 4)
    add_leg(dest, l["from"], "ground", "primary", "EURAIL", "P1", chain=[dest, l["from"]], medium="rail", share=sh,
            share_basis="S3: CORE->shell reverse; share min(0.03, half the origin's remaining outbound budget); at most 3 per CORE node",
            tau=l["base_transit_d"], tau_basis=l["calibration"]["transit_basis"], why="return trains (2024: 8,846 inbound vs 10,546 outbound trips)", extra={"rule": "S3"})
    SUM_EXTRA[(dest, "ground")] += sh
    S3_USED[dest] += 1

# ----------------------------------------------------------------------------
# 3. LANES (alternative routes as engine2 routes-table lane specs)
# ----------------------------------------------------------------------------
lanes: list = []


def edge_tau(a, b, mode="sea"):
    e = EKEY.get(f"{a}->{b}:{mode}")
    if e:
        return e["base_transit_d"]
    return legs[f"{a}->{b}:{mode}"]["base_transit_d"]


def add_lane(kind, frm, to, leg, tau, dtau, alt_gate, family, pri, route, basis, sources=None, faster=False, why=""):
    assert leg in EKEY or leg in legs, leg
    lk = f"{frm}->{to}:sea"
    carrier = lk if (lk in EKEY or lk in legs) else None
    if carrier:
        tau = (EKEY.get(carrier) or legs[carrier])["base_transit_d"]     # one source of truth for the days
    lane = {"key": lk, "carrier_edge": carrier, "kind": kind, "from": frm, "to": to, "leg": leg, "tau": round(tau, 3), "dtau": round(dtau, 3),
            "alt_gate": alt_gate, "family": family, "priority": pri, "route": route, "basis": basis, "faster_alternative": faster, "why": why,
            "capacity_share": 0.0, "capacity_share_basis": "DORMANT_ZERO: an alternative lane carries no baseline flow; it is used only when the primary leg's forecast wait exceeds the lane's added days (engine2 reroute rule)",
            "provenance": prov("derived: waypoint-chain route lengths (MODELLED) x the primary leg's own days per km")}
    if sources:
        lane["sources"] = sources
    lanes.append(lane)
    return lane


# Panama alternatives (A lanes from Pacific-side senders, B lanes to the Atlantic receivers) ----------
def d_chain(*names):
    return chain_km(list(names)) / 1.852


D = {}
D["SH-PAN"] = d_chain("sea_shanghai", "OSUMI", "PAC1", "PAC2", "PANG")
D["PAN-NY"] = d_chain("PANG", "CARIB1", "CARIB2", "FLST", "USEC1", "sea_newyork")
D["PAN-SAV"] = d_chain("PANG", "CARIB1", "CARIB2", "FLST", "sea_savannah")
D["PAN-HOU"] = d_chain("PANG", "CARIB1", "GULFMX1", "GULFMX2", "GULFMX3", "sea_houston")
SUEZ_TO_ATL = ["chk_suez", "PS", "MEDE", "MEDC", "MEDW", "GIB", "GIBW"]
D["SH-SUEZ"] = d_chain("sea_shanghai", "TAIW", "SCS", "SINGSTR", "chk_malacca", "MALW", "CMB", "GUARD", "chk_babelmandeb", "RS3", "RS2", "chk_suez")
D["SUEZ-NY"] = d_chain(*SUEZ_TO_ATL, "AN1", "AN2", "sea_newyork")
D["SUEZ-SAV"] = d_chain(*SUEZ_TO_ATL, "AS1", "AS2", "sea_savannah")
D["SUEZ-HOU"] = d_chain(*SUEZ_TO_ATL, "AH1", "AH2", "FLST", "GULFMX2", "GULFMX3", "sea_houston")
D["SH-CAPE"] = d_chain("sea_shanghai", "TAIW", "SCS", "SINGSTR", "chk_malacca", "MALW", "SIO2", "AGUL", "chk_capeofgoodhope")
D["CAPE-NY"] = d_chain("chk_capeofgoodhope", "SATL1", "SATL2", "NATL1", "NATL2", "sea_newyork")
D["CAPE-SAV"] = d_chain("chk_capeofgoodhope", "SATL1", "SATL2", "NATL1", "sea_savannah")
D["CAPE-HOU"] = d_chain("chk_capeofgoodhope", "SATL1", "SATL2", "NATL1", "FLST", "GULFMX2", "GULFMX3", "sea_houston")
RECV = {"sea_newyork": ("NY", 6.0, 0.25), "sea_savannah": ("SAV", 5.0, 0.15), "sea_houston": ("HOU", 6.0, 0.15)}
for gate, gname, fam, src_note in (("chk_suez", "SUEZ", "PANAMA_VIA_SUEZ", "THE22 EC4 and PREM services rotate Asia-US East Coast via (Suez); data/graph/services.json svc_the22_ec4"),
                                  ("chk_capeofgoodhope", "CAPE", "PANAMA_VIA_CAPE", "PREM EC2/EC3 and ONE/Premier services rotate Asia-US East Coast via (Cape of Good Hope); data/graph/services.json svc_prem_2512_ec2, svc_prem_2512_ec3")):
    dsh = D[f"SH-{gname}"]
    dtau_w = 0.0
    wsum = sum(v[2] for v in RECV.values())
    for q, (tag, tpq, shq) in RECV.items():
        dpq = D[f"PAN-{tag}"]
        dpn = (20.0 + tpq) / (D["SH-PAN"] + dpq)                 # the primary chain's own days per nm
        dq = D[f"{gname}-{tag}"]
        dtau_w += max(0.0, (dsh + dq - D["SH-PAN"] - dpq) * dpn) * shq / wsum
        add_lane("B", gate, q, f"chk_panama->{q}:sea", tpq * dq / dpq, 0.0, gate, fam, "P0",
                 f"{gname} -> {q.replace('sea_','')} {dq:,.0f} nm vs Panama -> {q.replace('sea_','')} {dpq:,.0f} nm",
                 "R-ROUTE: tau = primary leg days x (alternative nm / primary nm); dtau on the B lane is 0 (engine2 takes max(0, tau - leg tau)); the route difference is carried on the A lane, as in server/engine2/routes.py",
                 sources=[src_note])
    add_lane("A", "sea_shanghai", gate, "sea_shanghai->chk_panama:sea", 20.0 * dsh / D["SH-PAN"], dtau_w, gate, fam, "P0",
             f"Shanghai -> {gname} {dsh:,.0f} nm vs Shanghai -> Panama {D['SH-PAN']:,.0f} nm",
             "R-ROUTE; dtau = flow-weighted mean over the three Atlantic receivers of (alternative total nm - Panama total nm) x the Shanghai-Panama-receiver chain's days per nm",
             sources=[src_note])
# US landbridge via existing ground legs (graph-internal shortest path by base_transit_d)
GA = collections.defaultdict(list)
for e in EDGES:
    if e["mode"] == "ground" and N[e["from"]]["tier"] == "core" and N[e["to"]]["tier"] == "core":    # engine2 sees CORE only (tier wall)
        GA[e["from"]].append((e["to"], e["base_transit_d"]))


def gpath(s, t):
    pq, best = [(0.0, s, [s])], {}
    while pq:
        c, u, p = heapq.heappop(pq)
        if u in best:
            continue
        best[u] = c
        if u == t:
            return c, p
        for v, w in GA[u]:
            if v not in best:
                heapq.heappush(pq, (c + w, v, p + [v]))
    return None


LB_GATE = "gnd_chicago"
a1 = gpath("gnd_inlandempire", LB_GATE)
lb_src = ["US transcontinental rail landbridge: chain of existing graph ground legs sea_losangeles->gnd_inlandempire->gnd_chicago->...; days are the graph's MODELED ground leg days (data/graph/graph.json)"]
for q in ("sea_newyork", "sea_savannah", "sea_houston"):
    r = gpath(LB_GATE, q)
    if r is None:
        continue
    add_lane("B", LB_GATE, q, f"chk_panama->{q}:sea", r[0], 0.0, LB_GATE, "PANAMA_VIA_USLANDBRIDGE", "P0",
             " > ".join(r[1]), "R-GRAPH: sum of existing graph ground-leg days along the shortest ground path", sources=lb_src)
sh_la = EKEY["sea_shanghai->sea_losangeles:sea"]["base_transit_d"]
la_ie = EKEY["sea_losangeles->gnd_inlandempire:ground"]["base_transit_d"]
ie_ch = gpath("gnd_inlandempire", LB_GATE)[0]
# Shanghai -> LA -> rail -> Chicago -> NY vs Shanghai -> Panama -> NY (graph days both sides)
tot_alt = sh_la + la_ie + ie_ch + gpath(LB_GATE, "sea_newyork")[0]
tot_pan = 20.0 + 6.0
lane = add_lane("A", "sea_shanghai", LB_GATE, "sea_shanghai->chk_panama:sea", sh_la + la_ie + ie_ch, max(0.0, tot_alt - tot_pan), LB_GATE,
                "PANAMA_VIA_USLANDBRIDGE", "P0",
                f"Shanghai -> Los Angeles {sh_la} d + LA -> Inland Empire {la_ie} d + Inland Empire -> Chicago {ie_ch:.1f} d; total to New York {tot_alt:.1f} d vs {tot_pan:.1f} d via Panama",
                "R-GRAPH: graph leg days; the landbridge is FASTER than the canal route on the graph's own numbers, so dtau is floored at 0 and the lane is flagged faster_alternative (its cost and capacity limits are the gate headroom, not time)",
                sources=lb_src, faster=(tot_alt < tot_pan))
add_lane("A", "sea_losangeles", LB_GATE, "sea_losangeles->chk_panama:sea", la_ie + ie_ch, max(0.0, la_ie + ie_ch + gpath(LB_GATE, "sea_newyork")[0] - 9.0 - 6.0), LB_GATE,
         "PANAMA_VIA_USLANDBRIDGE", "P0", "Los Angeles -> Inland Empire -> Chicago by rail", "R-GRAPH", sources=lb_src, faster=True)

# Malacca alternatives: Sunda and Lombok -----------------------------------------------------
MAL_LEG_IN = {"sea_singapore": "sea_singapore->chk_malacca:sea", "sea_tanjungpelepas": "sea_tanjungpelepas->chk_malacca:sea"}
for gate, gname, fam, via_sg, via_cmb in (("chk_sunda", "SUNDA", "MALACCA_VIA_SUNDA", ["sea_singapore", "KARIMATA", "JAVA1", "chk_sunda"], ["chk_sunda", "SIO1", "SIO2", "SIO3", "sea_colombo"]),
                                         ("chk_lombok", "LOMBOK", "MALACCA_VIA_LOMBOK", ["sea_singapore", "KARIMATA", "JAVA2", "chk_lombok"], ["chk_lombok", "SIO4", "SIO5", "SIO2", "SIO3", "sea_colombo"])):
    prim_sg_col = chain_km(["sea_singapore", "chk_malacca", "MALW", "SIO3", "CMB", "sea_colombo"])
    alt_sg_col = chain_km(via_sg) + chain_km(via_cmb)
    leg_days_sg_col = EKEY["chk_malacca->sea_colombo:sea"]["base_transit_d"] + EKEY["sea_singapore->chk_malacca:sea"]["base_transit_d"]
    dpk = leg_days_sg_col / prim_sg_col
    dtau = max(0.0, (alt_sg_col - prim_sg_col) * dpk)
    for s_ in ("sea_singapore", "sea_tanjungpelepas"):
        sg_km = chain_km([s_] + via_sg[1:])
        prim_in = chain_km([s_, "chk_malacca"])
        add_lane("A", s_, gate, MAL_LEG_IN[s_], EKEY[MAL_LEG_IN[s_]]["base_transit_d"] * sg_km / max(prim_in, 150.0), dtau, gate, fam, "P0",
                 f"{s_} -> {gname} {sg_km:,.0f} km", "R-ROUTE with a 150 km floor on the primary reference; dtau from the Singapore-Colombo total via the alternative vs via Malacca at the primary chain's days per km",
                 sources=["Authored bypass sea_singapore->sea_colombo (bypass_of sea_singapore->chk_malacca:sea) in graph.json models the Sunda/Lombok route as one direct leg"])
    for dst, leg_key in (("sea_colombo", "chk_malacca->sea_colombo:sea"), ("sea_singapore", "chk_malacca->sea_singapore:sea")):
        km_alt = chain_km(via_cmb) if dst == "sea_colombo" else chain_km(["chk_sunda" if gate == "chk_sunda" else "chk_lombok", "JAVA1" if gate == "chk_sunda" else "JAVA2", "KARIMATA", "sea_singapore"])
        km_prim = chain_km(["chk_malacca", "MALW", "SIO3", "CMB", "sea_colombo"]) if dst == "sea_colombo" else chain_km(["chk_malacca", "sea_singapore"])
        add_lane("B", gate, dst, leg_key, EKEY[leg_key]["base_transit_d"] * km_alt / max(km_prim, 150.0), 0.0, gate, fam, "P0",
                 f"{gname} -> {dst.replace('sea_','')} {km_alt:,.0f} km", "R-ROUTE with a 150 km floor on the primary reference")

# Cape alternatives for the NEW Red Sea / Bab legs (graph-only legs, no service) ---------------------
CAPE = "chk_capeofgoodhope"
for port, via in (("sea_salalah", ["ARAB1"]), ("sea_jebelali", ["GOMAN", "ARAB1"]), ("sea_mundra", ["ARAB3", "IND2"]), ("sea_nhavasheva", ["IND2"]), ("sea_karachi", ["ARAB3", "IND2"])):
    leg = f"{port}->chk_babelmandeb:sea"
    prim = chain_km([port] + legs[leg]["route"][1:])
    alt = chain_km([port] + via + ["SIO2", "AGUL", CAPE])
    dpk = legs[leg]["base_transit_d"] / prim
    add_lane("A", port, CAPE, leg, alt * dpk, max(0.0, alt * dpk - legs[leg]["base_transit_d"]), CAPE, "BAB_VIA_CAPE", "P0",
             f"{port} -> Cape {alt:,.0f} km vs {port} -> Bab el-Mandeb {prim:,.0f} km", "R-ROUTE at the new leg's own days per km; dtau = lane tau - leg tau (A-lane convention of the authored colombo->Cape bypass)")
for dst in ("sea_colombo", "sea_salalah", "sea_mundra", "sea_nhavasheva", "sea_karachi", "sea_jebelali"):
    leg = f"chk_babelmandeb->{dst}:sea"
    if leg not in legs and leg not in EKEY:
        continue
    prim_days = (legs.get(leg) or EKEY[leg])["base_transit_d"]
    prim_km = chain_km((legs.get(leg) or {"route": ["chk_babelmandeb", dst]})["route"])
    alt = chain_km([CAPE, "AGUL", "SIO2", "SIO3", dst]) if dst in ("sea_colombo",) else chain_km([CAPE, "AGUL", "SIO2", "IND2", dst])
    add_lane("B", CAPE, dst, leg, prim_days * alt / prim_km, 0.0, CAPE, "BAB_VIA_CAPE", "P0",
             f"Cape -> {dst.replace('sea_','')} {alt:,.0f} km vs Bab el-Mandeb -> {dst.replace('sea_','')} {prim_km:,.0f} km", "R-ROUTE")

# ----------------------------------------------------------------------------
# 4. services port resolution
# ----------------------------------------------------------------------------
def res(name, calls, target, conf, how, status="resolved"):
    return {"service_name": name, "dropped_calls": calls, "place_id": target, "status": status, "confidence": conf, "basis": how}


RES = [
    res("Trieste", 6, "sea_trieste", "high", "new SHELL place (registry port1318)"),
    res("Wilhelmshaven", 4, "sea_wilhelmshaven", "high", "new SHELL place (registry port1396); CORE sea_bremerhaven carries Wilhelmshaven's coordinates"),
    res("Venice", 1, "sea_portodilidovenezia", "high", "ALREADY in graph.json as shell 'Porto Di Lido-Venezia' (registry port1005); the service mapper did not find it", "alias_existing"),
    res("Tekirdag", 1, "sea_tekirdag", "high", "ALREADY in graph.json (shell); mapper miss", "alias_existing"),
    res("Yarimca", 1, "sea_yarimca", "high", "ALREADY in graph.json (shell); mapper miss", "alias_existing"),
    res("Navegantes", 2, "sea_itaja", "high", "Navegantes (Portonave) and Itajai face each other across the Itajai-Acu river mouth: one port complex (coordinates of Portonave not fetched; adjacency is general knowledge, so confidence is high but not machine-checked); ALREADY in graph.json", "alias_existing"),
    res("Sihanoukville", 1, "sea_kmpngsam", "high", "Sihanoukville Autonomous Port was Kompong Som (graph name 'Kampong Saom'); ALREADY in graph.json", "alias_existing"),
    res("Vado Ligure", 1, "sea_savona", "medium", "Vado Gateway lies in the Savona-Vado port system (CPPI 'SAVONA-VADO' rank 36); graph place is 'Savona' (shell); same port authority and port system, a few km apart (Vado coordinates not fetched)", "alias_existing"),
    res("Bahrain", 1, "sea_sitrah", "low", "Khalifa Bin Salman Port (registry port2012, 1 call) is 7 km from shell Sitrah and was absorbed by the same-complex rule; map to Sitrah, flag low confidence", "alias_existing"),
    res("Ensenada", 2, "sea_ensenada", "high", "new SHELL place (registry port326)"),
    res("Posorja", 1, "sea_posorja", "high", "new SHELL place (registry port2032)"),
    res("Chancay", 1, "sea_chancay", "high", "new SHELL place (registry port2272; SOURCED design capacity)"),
    res("Safi", 1, "sea_safi", "high", "new SHELL place (registry port1118)"),
    res("Manzanillo", 4, "sea_colon", "medium", "every occurrence follows a printed (Panama) marker and precedes Savannah/Charleston (THE22 EC1, PREM EC2/EC3): Manzanillo International Terminal is in Colon province (Atlantic side); the Mexican Manzanillo (sea_manzanillo) is on the Pacific and cannot sit between (Panama) and Savannah", "ambiguous_resolved"),
    res("Wilmington", 1, "sea_wilmingtonnc", "medium", "THE22 EC2 sequence Norfolk - Wilmington - Savannah is the US South Atlantic coast: Wilmington NC, not Wilmington DE (sea_wilmingtonde)", "ambiguous_resolved"),
    res("Izmit", 2, "sea_derinceizmitkocaeli", "medium", "Gulf of Izmit port cluster (Derince/Izmit/Kocaeli) exists in graph as one shell place", "ambiguous_resolved"),
    res("Istanbul-Evyap (Izmit)", 1, "sea_gebze", "low", "Evyap Port is on the Gulf of Izmit (Dilovasi, Gebze district); graph shell 'Gebze' is the nearest named place; flagged low", "ambiguous_resolved"),
    res("Istanbul", 5, None, "none", "REFUSED: could be Ambarli (CORE), Marport, Kumport or Evyap; each rotation prints 'Istanbul' alone next to a named terminal elsewhere in the loop, so no unique mapping exists", "unresolved"),
    res("South PRC", 18, None, "none", "REFUSED: aggregate label for a set of south-China ports (Yantian/Nansha/Shekou/Chiwan class), not a place; resolution needs the carrier's terminal list", "unresolved"),
    res("TPWC", 2, None, "none", "REFUSED: unexplained abbreviation in a Hapag-Lloyd 2022 rotation ('From TPWC - Tokyo ...'); not matched to any port", "unresolved"),
    res("Ecuador", 1, None, "none", "REFUSED: country label; candidates Guayaquil (CORE) and new Posorja", "unresolved"),
]
assert sum(r["dropped_calls"] for r in RES) == 57 and len(RES) == 21
RES_SUMMARY = {
    "names": 21, "calls": 57,
    "resolved_to_new_place": sum(1 for r in RES if r["status"] == "resolved"),
    "resolved_to_existing_place": sum(1 for r in RES if r["status"] == "alias_existing"),
    "ambiguous_resolved_with_caveat": sum(1 for r in RES if r["status"] == "ambiguous_resolved"),
    "unresolved": sum(1 for r in RES if r["status"] == "unresolved"),
    "calls_resolved_high_or_medium": sum(r["dropped_calls"] for r in RES if r["place_id"] and r["confidence"] in ("high", "medium")),
    "calls_resolved_low": sum(r["dropped_calls"] for r in RES if r["place_id"] and r["confidence"] == "low"),
    "calls_unresolved": sum(r["dropped_calls"] for r in RES if not r["place_id"]),
}

# ----------------------------------------------------------------------------
# 5. validation (prototype of the sidecar validator rules) and output
# ----------------------------------------------------------------------------
problems = []
tot_extra = collections.defaultdict(float)
for k, l in legs.items():
    if l["from"] == l["to"]:
        problems.append(f"self loop {k}")
    for end in (l["from"], l["to"]):
        if end not in N and end not in PLACES_BY_ID:
            problems.append(f"{k}: unknown endpoint {end}")
    if not (0.0 <= l["capacity_share"] <= 1.0):
        problems.append(f"{k}: share out of range")
    if not l["base_transit_d"] > 0:
        problems.append(f"{k}: transit <= 0")
    tot_extra[(l["from"], l["mode"])] += l["capacity_share"]
for (frm, mode), s in tot_extra.items():
    if OUT[(frm, mode)] + s > 1.0 + 1e-9:
        problems.append(f"outbound share ({frm},{mode}) = {OUT[(frm, mode)] + s:.4f} > 1.0")
for p in places:
    used = [k for k in legs if legs[k]["from"] == p["id"] or legs[k]["to"] == p["id"]]
    if not used:
        problems.append(f"place {p['id']} has no leg (isolated)")
    if not any(legs[k]["from"] == p["id"] for k in used):
        problems.append(f"place {p['id']} has no outbound leg")
    if not any(legs[k]["to"] == p["id"] for k in used):
        problems.append(f"place {p['id']} has no inbound leg")
for ln in lanes:
    leg = EKEY.get(ln["leg"]) or legs.get(ln["leg"])
    if leg is None:
        problems.append(f"lane {ln['key']}: unknown primary {ln['leg']}")
        continue
    if ln["kind"] == "A" and ln["from"] != leg["from"]:
        problems.append(f"lane {ln['key']}: A lane must leave the primary's sender")
    if ln["kind"] == "B" and ln["to"] != leg["to"]:
        problems.append(f"lane {ln['key']}: B lane must reach the primary's receiver")
    if not ln["tau"] > 0 or ln["dtau"] < 0:
        problems.append(f"lane {ln['key']}: tau/dtau")
    if (ln["key"] in EKEY or ln["key"] in legs) and ln.get("carrier_edge") != ln["key"]:
        problems.append(f"lane {ln['key']}: collides with a primary leg key without declaring it as carrier")
if problems:
    print("VALIDATION PROBLEMS:")
    for p in problems:
        print("  ", p)
    sys.exit(1)

legs_out = sorted(legs.values(), key=lambda l: l["key"])
for l in legs_out:
    l["provenance"] = prov("derived: " + l.get("why", "") + " | rule " + l.get("capacity_share_basis", "")[:40])
places_out = sorted(places, key=lambda p: p["id"])
lanes_out = sorted(lanes, key=lambda l: (l["family"], l["kind"], l["key"]))

summary = {
    "places": len(places_out), "places_by_tier_kind": dict(collections.Counter(f"{p['tier']}/{p['kind']}" for p in places_out)),
    "legs": len(legs_out), "legs_by_role": dict(collections.Counter(l["role"] for l in legs_out)),
    "legs_by_tier": dict(collections.Counter(l["tier"] for l in legs_out)),
    "legs_by_block": dict(collections.Counter(l["block"] for l in legs_out)),
    "legs_by_mode": dict(collections.Counter(l["mode"] for l in legs_out)),
    "lanes": len(lanes_out), "lanes_by_family": dict(collections.Counter(l["family"] for l in lanes_out)),
    "core_core_legs": sum(1 for l in legs_out if l["tier"] == "core"),
}
hdr = {"schema": "supermap.graph_extra.candidates/1", "status": "CANDIDATES - not wired into any engine; sidecar data/graph/extra.json does not exist yet",
       "graph_pin": {"file_sha256_prefix": PIN_PREFIX}, "retrieved_at": RET, "lane": "realism: ports, places, legs completeness"}
docs = {
    "places.extra.candidates.json": {**hdr, "summary": {k: summary[k] for k in ("places", "places_by_tier_kind")}, "places": places_out},
    "legs.extra.candidates.json": {**hdr, "summary": {k: summary[k] for k in ("legs", "legs_by_role", "legs_by_tier", "legs_by_block", "legs_by_mode", "core_core_legs")},
                                    "rules": {"R-CAP": "new structural legs: origin budget min(0.20 chokepoint | 0.10 port, half the remaining outbound headroom), apportioned by destination observed calls/transits; MODELED",
                                              "R-ROUTE": "transit = route km x reference leg days / reference route km (889 km/day when the reference route < 300 km); route km from waypoint chains in this file's builder (MODELLED)",
                                              "S1-S6": "data/graph/build_graph.py SHELL construction, re-run for the new places only"},
                                    "legs": legs_out},
    "lanes.extra.candidates.json": {**hdr, "summary": {k: summary[k] for k in ("lanes", "lanes_by_family")},
                                     "semantics": "engine2 routes-table lane specs (server/engine2/routes.py): kind A leaves the primary leg's sender for an alternative gate, kind B leaves the gate for the primary leg's receiver; tau = days to the far end; dtau = added days (A lane only; B lane dtau is max(0, tau - primary tau) inside the engine). capacity_share is 0.0 by construction.",
                                     "lanes": lanes_out},
    "services.port_resolution.json": {**hdr, "summary": RES_SUMMARY, "resolution": RES},
}
sums = {}
for name, d in docs.items():
    s = canon(d)
    (HERE / name).write_text(s, encoding="utf-8")
    sums[name] = hashlib.sha256(s.encode("utf-8")).hexdigest()
(HERE / "candidates.sha256").write_text("".join(f"{h}  {n}\n" for n, h in sorted(sums.items())), encoding="utf-8")
print(json.dumps(summary, indent=1))
print(json.dumps(RES_SUMMARY))
