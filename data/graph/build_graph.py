#!/usr/bin/env python3
"""Build graph.json (v1.0) + calibration.md tables.

Determinism: nodes sorted by id, edges sorted by (from, to, mode), JSON dumped
with sort_keys=True, indent=2, ensure_ascii=False, single trailing newline.
No timestamps, no randomness, no dict-order dependence anywhere in the output:
re-running this script on the same inputs produces a byte-identical graph.json.

Inputs: the base node census (path from the first command-line argument, else the
environment variable SUPERMAP_NODES_DRAFT, else data/graph/nodes-draft.json),
plus the PINNED PortWatch join input `portwatch_join_input.json` next to this
script: port_activity records extracted verbatim from
the adapter snapshot data/snapshots/portwatch.json. The generator
reads ONLY that pinned file for the calibration join — never the live
snapshot and never the network — so regeneration stays byte-identical even
when the adapter refreshes its snapshot.
Honesty: every capacity is either (a) derived arithmetically from a figure in
the census capacity_note or a figure retrieved from the named source
(recorded per row), or (b) an explicit
MODELED engine default flagged GAP in calibration.md. Edge capacity_share
values are either (a) re-derived from the pinned PortWatch snapshot by the
stated destination-attraction formula in `portwatch_join()` below (basis
SOURCED, inputs recorded per edge; PORT origins only — chokepoint origins
are excluded, because
chokepoint-exit legs are transit flows, not port calls), or (b) MODELED base
allocations with the reason recorded per edge. base_transit_d stays MODELED on every edge: the
fetched PortWatch services (ports registry + disruptions view) carry no
transit-time field. Nothing is silently estimated; see calibration.md.

EXTENDED CENSUS: the base census is extended by
`nodes-expansion.json` beside this script (80 further nodes: 55 ports,
2 chokepoints, 13 air hubs, 10 ground nodes — same entry rules: a named
source per node, recorded in each entry's `source_name`, retrieved
per entry; port coordinates from the IMF PortWatch ports registry). The
extended census merges over the base census in main(); the base census
file itself is untouched. Figures that are not a plain
"X.XXM TEU in 2023" (fiscal-year, other-year, derived or run-rate figures)
go through PORT_OVERRIDE / the existing AIR_OVERRIDE / GROUND_OVERRIDE /
CHOKE_CAP dicts with the derivation written out per node. The pinned
PortWatch join input also carries the 55 extended-census ports' records (extracted
verbatim from the same live FeatureServer, each carrying its
own retrieved_at), so the join covers them on the same terms; SOURCED
edges record the retrieved_at of their destination's record.

COUNTRY FIELD: every node additionally serves a
`country`, taken verbatim from its census entry's `country` field — a
build input in all three censuses (base census + extended census for
CORE; shell census for SHELL) — never derived or hand-assigned at build
time. The one systematic exception is BY KIND: chokepoints are
transnational straits/canals and their census entries carry no country,
so chokepoint nodes serve NO `country` key (country-null by kind; a
country is never guessed for them). The field is presentation/search
data only: the chain engine never reads it.
"""
import hashlib, json, math, os, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = Path(os.environ.get("SUPERMAP_NODES_DRAFT", "data/graph/nodes-draft.json"))
PORTWATCH_INPUT = HERE / "portwatch_join_input.json"

MODE_BY_KIND = {"port": "sea", "chokepoint": "sea", "air_hub": "air", "ground_node": "ground"}
UNIT_BY_KIND = {"port": "TEU/day", "chokepoint": "vessels/day", "air_hub": "tonnes/day", "ground_node": "trucks/day"}
# MODELED engine defaults (documented in calibration.md; not sourced data)
INVENTORY_DAYS = {"port": 7.0, "chokepoint": 0.0, "air_hub": 3.0, "ground_node": 5.0}
GROUND_DEFAULT_TRUCKS = 1500          # MODELED placeholder where no sourced figure exists
CAPE_DEFAULT_VESSELS = 100            # MODELED placeholder; Cape is a route node, no transit-count source retrieved
TEU_PER_TRUCK = 2.0                   # MODELED conversion: one truck movement carries one 40 ft box = 2 TEU

SOURCE_NAME_BY_URL = {
    "https://en.wikipedia.org/wiki/List_of_busiest_container_ports":
        "Lloyd's List Top 100 / World Shipping Council 2023 TEU, via Wikipedia 'List of busiest container ports' (retrieved 2026-10-20)",
    "https://www.eia.gov/outlooks/steo/report/energysecurity/article.php":
        "U.S. EIA Short-Term Energy Outlook, World Oil Transit Chokepoints table, 1H2025 (retrieved 2026-10-20)",
    "https://en.wikipedia.org/wiki/List_of_busiest_airports_by_cargo_traffic":
        "ACI World airport cargo rankings, via Wikipedia 'List of busiest airports by cargo traffic' (retrieved 2026-10-20)",
    "https://www.bts.gov/newsroom/transborder-freight-data-annual-report-2025-0":
        "U.S. Bureau of Transportation Statistics, TransBorder Freight data, Jun 2025 (retrieved 2026-10-20)",
    "https://www.newsilkroadnetwork.com/news/duisport-series-300-years-and-more-duisburger-hafen-transforms-from-a-german-industrial-port-to-a-global-intermodal-hubs":
        "New Silk Road Network, Duisport profile (retrieved 2026-10-20)",
    "https://www.greathensen.com/en/trade-lanes/rail-freight-china-to-duisburg.html":
        "Great Hensen trade-lane guide, China-EU rail freight (retrieved 2026-10-20)",
    "https://www.unthinkablebuild.com/khorgos-the-worlds-largest-dry-port/":
        "Unthinkable Build, Khorgos dry port profile (retrieved 2026-10-20; secondary source)",
    "https://www.visualcapitalist.com/mapped-the-worlds-oil-chokepoints/":
        "Visual Capitalist, 'Mapped: The World's Oil Chokepoints' (retrieved 2026-10-20; secondary source)",
}
GAP_SOURCE = "GAP: no source retrieved for this node - see calibration.md Gaps"

# --- Figures retrieved from the named sources ---
# node_id -> (capacity_value_in_kind_unit, extra_source_name, derivation, status)
CHOKE_CAP = {
    "chk_malacca": (281, "Malaysian Marine Department via Seatrade Maritime (102,525 transits of vessels >300 GT in the reported year)",
                    "102,525 transits/yr / 365 = 281 vessels/day (observed throughput used as capacity proxy, MODELED interpretation)", "SOURCED"),
    "chk_hormuz": (74, "IMF PortWatch pre-crisis norm via Strait of Hormuz Report (PortWatch 7-day-average transit series; secondary aggregator)",
                   "~74 commercial transits/day pre-crisis norm (IMF PortWatch)", "SOURCED"),
    "chk_suez": (72, "Suez Canal Authority figures via Britannica, 'Suez Canal - The economy' (26,434 transits in 2023)",
                 "26,434 transits in 2023 / 365 = 72 vessels/day (2023 peak-year average)", "SOURCED"),
    "chk_babelmandeb": (70, "IMF PortWatch via Phys.org summary of University of Oxford / PortWatch research (~70 vessels/day before the 2023 diversions)",
                        "~70 vessels/day pre-crisis (IMF PortWatch)", "SOURCED"),
    "chk_panama": (36, "Panama Canal Authority (ACP) Customer Advisory 2023-10-30 via DB Group (normal schedule ~36 transits/day)",
                   "Normal ACP schedule ~36 transits/day (base census capacity_note + ACP advisory)", "SOURCED"),
    "chk_danish": (167, "Danish Maritime Authority, Traffic Separation Systems page (>26,000 ships/yr Great Belt; ~35,000 ships/yr the Sound)",
                   "(26,000 + 35,000) ships/yr / 365 = 167 vessels/day for the Danish Straits system (official DMA figures)", "SOURCED"),
    "chk_turkish": (110, "Turkish General Directorate of Maritime Affairs via SeaNews (40,172 Istanbul Strait transits in 2025)",
                    "40,172 Istanbul Strait transits in 2025 / 365 = 110 vessels/day (Canakkale/Dardanelles transits not added - would double-count through-transits)", "SOURCED"),
    "chk_taiwan": (240, "Bloomberg vessel data via Institut Thomas More Note 80 (2026) - average 240 ships/day through the Taiwan Strait or along Taiwan's east coast, 2022",
                   "240 vessels/day (Bloomberg, 2022) - figure includes the east-coast route, so it overstates strait-only transits; flagged in Gaps", "SOURCED-APPROX"),
    "chk_gibraltar": (274, "LTDN, 'The Strait of Gibraltar and the Geopolitics of Maritime Corridors' (>100,000 commercial ships/yr; secondary source)",
                     "100,000 transits/yr / 365 = 274 vessels/day - round published figure from a secondary source; flagged in Gaps", "SOURCED-APPROX"),
    "chk_capeofgoodhope": (CAPE_DEFAULT_VESSELS, None,
                           "No sourced transit-count or capacity figure retrieved (the Cape is an open-ocean route, not a constrained strait). MODELED engine default 100 vessels/day; GAP - see calibration.md", "GAP-MODELED"),
    # --- extended census ---
    "chk_dover": (400, "UK Marine Management Organisation AIS analysis via Esri UK ('around 400 vessels pass through our waters every day' — Dover Strait); UK Parliament Transport Committee evidence cites ~500/day for the Straits incl. cross-Channel ferry traffic",
                  "400 vessels/day adopted from the MMO AIS through-traffic figure; ferry-inclusive published counts run higher (~500/day) — flagged. Retrieved 2026-10-21", "SOURCED-APPROX"),
    "chk_mozambique": (60, None,
                       "No sourced transit-count or capacity figure retrieved (the channel is a wide deep-water route, not a constrained strait, and no transit census is published). MODELED engine default 60 vessels/day; GAP - see calibration.md", "GAP-MODELED"),
}
AIR_OVERRIDE = {
    "air_cincinnati": (5480, None, "~2M tonnes/yr class figure in base census (ACI top-20, exact year figure not retrieved); 2,000,000 / 365 = 5,480 tonnes/day. Precision GAP", "SOURCED-APPROX"),
    "air_istanbul": (4822, None, "~1.76M tonnes class figure in base census (2025 industry compilations of ACI data, exact ACI figure not retrieved); 1,760,000 / 365 = 4,822 tonnes/day. Precision GAP", "SOURCED-APPROX"),
    "air_shenzhen": (4822, None, "~1.76M tonnes class figure in base census (2025 industry compilations of ACI data, exact ACI figure not retrieved); 1,760,000 / 365 = 4,822 tonnes/day. Precision GAP", "SOURCED-APPROX"),
    "air_jakarta": (1585, "Angkasa Pura II airport statistics via Wikipedia, 'Soekarno-Hatta International Airport' (578,621 t cargo in 2023)",
                    "578,621 t in 2023 / 365 = 1,585 tonnes/day (figure retrieved)", "SOURCED"),
    "air_delhi": (2748, "Delhi International Airport Ltd (DIAL) via ITLN / India Seatrade News (1.003M t handled FY24; cargo terminals rated 1.8M t)",
                  "1,003,000 t handled in FY24 / 365 = 2,748 tonnes/day (figure retrieved; supersedes the census 'roughly 1M t class' note)", "SOURCED"),
    "air_dubaiworldcentral": (1578, "Dubai Airports, DWC fact file (576,000 t of cargo handled in 2023)",
                              "576,000 t in 2023 / 365 = 1,578 tonnes/day (operator figure retrieved)", "SOURCED"),
    "air_ontario": (1869, "Ontario International Airport Authority traffic releases via flyontario.com (752,199 US tons freight + mail in 2023)",
                    "752,199 US (short) tons in 2023 x 0.90718474 t/ton = 682,353 t / 365 = 1,869 tonnes/day (unit conversion documented; retrieved)", "SOURCED"),
    "air_indianapolis": (2445, "Indianapolis Airport Authority, Airline Activity Reports (983,420 US tons total cargo in calendar 2023)",
                         "983,420 US (short) tons in 2023 x 0.90718474 t/ton = 892,166 t / 365 = 2,445 tonnes/day (unit conversion documented; retrieved)", "SOURCED"),
    "air_addisababa": (1644, "Unitechnik / Ethiopian Airlines Bole cargo terminal (terminal designed to handle 600,000 t/yr)",
                       "600,000 t/yr terminal design capacity / 365 = 1,644 tonnes/day - a true design-capacity figure, retrieved", "SOURCED"),
    # --- extended census: all expansion air hubs are overrides,
    # because their figures are short-ton, system-total, derived or
    # other-year values the census regex (which names ACI) must not claim ---
    "air_newyork": (3952, "Port Authority of New York & New Jersey, Airport Traffic Report (JFK freight 1.59M short tons in 2023; the 2025 report gives 1.67M tons for 2024, +5% on 2023)",
                    "1,590,000 US (short) tons in 2023 x 0.90718474 t/ton = 1,442,424 t / 365 = 3,952 tonnes/day (unit conversion documented; retrieved 2026-10-21)", "SOURCED"),
    "air_leipzig": (3836, "Mitteldeutsche Flughafen AG via Air Cargo News (Leipzig/Halle cargo declined 7.7% to 1.4M tonnes in 2023)",
                    "1,400,000 t in 2023 / 365 = 3,836 tonnes/day (retrieved 2026-10-21)", "SOURCED"),
    "air_liege": (2755, "Liège Airport (1,005,676 tonnes total cargo in 2023)",
                  "1,005,676 t in 2023 / 365 = 2,755 tonnes/day (retrieved 2026-10-21)", "SOURCED"),
    "air_cologne": (2384, "Flughafen Köln/Bonn GmbH (2024 release: freight 'only slightly below that of 2023 (870,000 tonnes)')",
                    "870,000 t in 2023 / 365 = 2,384 tonnes/day (retrieved 2026-10-21)", "SOURCED"),
    "air_madrid": (1763, "Aena (Madrid-Barajas freight traffic 643,535 tonnes in 2023)",
                   "643,535 t in 2023 / 365 = 1,763 tonnes/day (retrieved 2026-10-21)", "SOURCED"),
    "air_milan": (1827, "SEA Group 2023 results release (Milan airport system cargo 667,000 tonnes in 2023)",
                  "667,000 t in 2023 (Milan SYSTEM total; Malpensa is the system's cargo airport, Linate cargo negligible) / 365 = 1,827 tonnes/day (retrieved 2026-10-21)", "SOURCED"),
    "air_brussels": (1912, "Brussels Airport Company (732,797 tonnes total cargo in 2024, reported +5% vs 2023)",
                     "732,797 / 1.05 = 697,902 t in 2023 (derived from the operator's reported growth rate) / 365 = 1,912 tonnes/day (retrieved 2026-10-21)", "SOURCED"),
    "air_atlanta": (1445, "City of Atlanta Department of Aviation, monthly Airport Traffic Reports (grand total cargo 527,293 metric tons, Jan-Nov 2023)",
                    "527,293 t in Jan-Nov 2023 / 365 = 1,445 tonnes/day (11-month figure used as the annual-basis proxy — stated, not annualised; retrieved 2026-10-21)", "SOURCED"),
    "air_houston": (1463, "Houston Airports 2024 Economic Impact Study (airport-system air freight 534,123 metric tons in 2023)",
                    "534,123 t in 2023 (system total; IAH carries ~98% of system cargo per the study's airport split) / 365 = 1,463 tonnes/day (retrieved 2026-10-21)", "SOURCED"),
    "air_toronto": (1210, "Greater Toronto Airports Authority traffic summary via Transport Canada (Toronto Pearson cargo 441,500 metric tons in 2024)",
                    "441,500 t in 2024 / 365 = 1,210 tonnes/day (retrieved 2026-10-21)", "SOURCED"),
    "air_zhengzhou": (1932, "Henan Province Airport Group via Xinhua (Zhengzhou cargo throughput 705,000 tonnes in 2021, first year above 700,000 t)",
                      "705,000 t in 2021 / 365 = 1,932 tonnes/day (latest figure retrieved; later growth not pulled — flagged in calibration.md; retrieved 2026-10-21)", "SOURCED"),
    "air_ezhou": (672, "Ezhou Huahu Airport / Ezhou municipal government (cargo and mail throughput 245,300 tonnes in 2023)",
                  "245,300 t in 2023 / 365 = 672 tonnes/day (retrieved 2026-10-21)", "SOURCED"),
    "air_osaka": (2065, "Kansai Airports official traffic report (KIX total cargo volume 753,697 t in FY2022)",
                  "753,697 t in FY2022 / 365 = 2,065 tonnes/day (fiscal-year basis, stated; retrieved 2026-10-21)", "SOURCED"),
}
GROUND_OVERRIDE = {
    "gnd_chicago": (20000, "CenterPoint Intermodal Center / Site Selection Magazine (CIC handles ~20,000 trucks daily); Mid-America Freight Coalition, BNSF Logistics Park Chicago profile",
                    "~20,000 trucks/day handled across the CenterPoint Intermodal Center cluster (BNSF LPC + UP Joliet), as published; cluster scope, not a single terminal", "SOURCED"),
    "gnd_channeltunnel": (3187, "Getlink (Eurotunnel) shuttle traffic release, FY2025 via Euronext/BusinessWire (1,163,124 trucks carried on LeShuttle Freight in 2025)",
                          "1,163,124 trucks in 2025 / 365 = 3,187 trucks/day (truck shuttles only; through rail freight trains not counted - noted in Gaps)", "SOURCED"),
    "gnd_dadri": (685, "UNESCAP Transport and Communications Bulletin No. 77, CONCOR ICD Dadri (phase-I annual handling 500,000 TEU, planned enhancement to 1M TEU/yr)",
                  "500,000 TEU/yr / 365 / 2 TEU per truck (MODELED conversion, 1 truck = one 40 ft box) = 685 trucks/day", "SOURCED+MODELED-CONVERSION"),
    "gnd_citydeep": (548, "Transnet via SAnews (South African Government News Agency) - City Deep upgrade doubled capacity to 400,000 TEU/yr",
                     "400,000 TEU/yr / 365 / 2 TEU per truck (MODELED conversion) = 548 trucks/day", "SOURCED+MODELED-CONVERSION"),
    "gnd_riyadh": (548, "Bergqvist, 'Developing Large-Scale Dry Ports - The Case of Arriyadh', SCIRP (dry port handling ~400,000 TEU/yr, at/near maximum)",
                   "~400,000 TEU/yr / 365 / 2 TEU per truck (MODELED conversion) = 548 trucks/day", "SOURCED+MODELED-CONVERSION"),
    # --- extended census ---
    "gnd_hidalgo": (1973, "U.S. BTS TransBorder Freight Data Annual Report 2025 (Hidalgo - Texas: 720,300 truck crossings)",
                    "720,300 truck crossings in 2025 / 365 = 1,973 trucks/day (observed loaded+empty truck crossings used as capacity proxy, MODELED interpretation; retrieved 2026-10-21)", "SOURCED"),
    "gnd_eaglepass": (671, "U.S. BTS TransBorder Freight Data Annual Report 2025 (Eagle Pass - Texas: 245,000 truck crossings)",
                      "245,000 truck crossings in 2025 / 365 = 671 trucks/day (same proxy interpretation as gnd_hidalgo; retrieved 2026-10-21)", "SOURCED"),
    "gnd_calexico": (1596, "U.S. BTS TransBorder Freight Data Annual Report 2025 (Calexico - East - California: 582,400 truck crossings)",
                     "582,400 truck crossings in 2025 / 365 = 1,596 trucks/day (same proxy interpretation as gnd_hidalgo; retrieved 2026-10-21)", "SOURCED"),
    "gnd_greer": (402, "South Carolina Ports Authority (Inland Port Greer handled 146,813 rail moves in FY2023)",
                  "146,813 terminal rail moves in FY2023 / 365 = 402 moves/day, treated as truck-equivalent terminal moves (MODELED 1:1 interpretation of a sourced figure; retrieved 2026-10-21)", "SOURCED+MODELED-CONVERSION"),
}
# Ports whose census figure is NOT a plain calendar-2023 total (fiscal-year,
# other-year, derived or run-rate) bypass the capacity_note regex and take
# the documented value here, mirroring AIR_OVERRIDE / GROUND_OVERRIDE.
# node_id -> (capacity_teu_per_day, extra_source_name, derivation, status)
PORT_OVERRIDE = {
    "sea_fuzhou": (9370, "APEC Port Services Network throughput review (Fuzhou 1.71M TEU in H1 2022)",
                   "1,710,000 TEU in H1 2022 x 2 (annualised) = 3,420,000 TEU/yr / 365 = 9,370 TEU/day. The annualisation is a MODELED step on a sourced figure — flagged; no full-year official total retrieved (retrieved 2026-10-21)", "SOURCED+MODELED-CONVERSION"),
    "sea_gwangyang": (5507, "Korea Ministry of Oceans and Fisheries via korea.net (Gwangyang handled 2.01M TEU in 2024)",
                      "2,010,000 TEU in 2024 / 365 = 5,507 TEU/day (retrieved 2026-10-21)", "SOURCED"),
    "sea_karachi": (5288, "Karachi Port Trust official release (1.93M TEU handled at FY2022-23 year end)",
                    "1,930,000 TEU in FY2022-23 (Jul 2022-Jun 2023) / 365 = 5,288 TEU/day (fiscal-year basis, stated; retrieved 2026-10-21)", "SOURCED"),
    "sea_durban": (6219, "Transnet Port Terminals / ICTSI (Durban Container Terminal Pier 2 processed 1.7M TEU in FY2023/24)",
                   "Pier 2's 1,700,000 TEU reported as roughly three-quarters of the port's container volume -> ~2,270,000 TEU port-wide / 365 = 6,219 TEU/day. DERIVED figure — no single official port-wide total was retrieved; stated as a derivation, not a reported total (retrieved 2026-10-21)", "SOURCED+MODELED-CONVERSION"),
    "sea_capetown": (1918, "Transnet National Ports Authority figures via Wikipedia 'Port of Cape Town' (annual container volume ~700,000 TEU in 2024)",
                     "700,000 TEU in 2024 / 365 = 1,918 TEU/day (retrieved 2026-10-21)", "SOURCED"),
    "sea_charleston": (7123, "South Carolina Ports Authority (FY2023 throughput 'nearly 2.6 million TEU', system-wide; Charleston is the system's container gateway)",
                       "2,600,000 TEU in FY2023 (Jul 2022-Jun 2023) / 365 = 7,123 TEU/day (fiscal-year basis, stated; retrieved 2026-10-21)", "SOURCED"),
    "sea_manzanillo": (10685, "ASIPONA Manzanillo via AFmedios (port handled 3.9M TEU in 2024)",
                       "3,900,000 TEU in 2024 / 365 = 10,685 TEU/day (retrieved 2026-10-21)", "SOURCED"),
    "sea_melbourne": (9304, "Port of Melbourne (3,396,000 TEU handled in 2024, a record year)",
                      "3,396,000 TEU in 2024 / 365 = 9,304 TEU/day (retrieved 2026-10-21)", "SOURCED"),
    "sea_sydney": (7671, "NSW Ports (Port Botany 'handles 99.6% of NSW container traffic, or 2.8 million TEU a year')",
                   "2,800,000 TEU/yr published operator run-rate (undated) / 365 = 7,671 TEU/day (retrieved 2026-10-21)", "SOURCED-APPROX"),
    "sea_brisbane": (4438, "Port of Brisbane Pty Ltd via Splash247 (around 1.62M TEU in FY2024-25, before the FY26 record of 1.74M TEU)",
                     "1,620,000 TEU in FY2024-25 / 365 = 4,438 TEU/day (fiscal-year basis, stated; retrieved 2026-10-21)", "SOURCED"),
}

def build_nodes(census):
    nodes, calib = [], []
    for n in sorted(census["nodes"], key=lambda x: x["id"]):
        kind = n["type"]
        mode = MODE_BY_KIND[kind]
        unit = UNIT_BY_KIND[kind]
        tier = n.get("tier", "core")
        note = n.get("capacity_note", "")
        src_url = n.get("source_url")
        sources = []
        if src_url and src_url in SOURCE_NAME_BY_URL:
            sources.append(SOURCE_NAME_BY_URL[src_url])
        elif src_url:
            sources.append(f"Base census source (retrieved 2026-10-20): {src_url}")
        if n.get("source_name"):
            # Expansion census entries carry an explicit named source
            # (retrieved date recorded there); it replaces the URL-derived label so
            # a base-census attribution is never attached to expansion data.
            sources = [n["source_name"]]
        if tier == "shell":
            # SHELL path (two-tier): the census
            # builder computed the capacity and the named basis; the full
            # derivation text is printed in calibration.md from `derivation`.
            # Sources are the registry URLs verbatim — the activity evidence.
            sources = list(n.get("sources") or [])
        status, derivation = None, None
        if tier == "shell":
            capacity = n["capacity"]
            status = n["capacity_basis"]     # ESTIMATE | NOT_PUBLISHED
            derivation = n["capacity_note"]  # basis printed in calibration.md
        elif kind == "port":
            if n["id"] in PORT_OVERRIDE:
                capacity, extra, derivation, status = PORT_OVERRIDE[n["id"]]
                if extra:
                    sources.append(extra)
            else:
                m = re.search(r"([\d]+\.?\d*)M TEU in 2023", note)
                annual = float(m.group(1)) * 1_000_000
                capacity = round(annual / 365)
                derivation = f"{m.group(1)}M TEU handled in 2023 (census capacity_note) / 365 = {capacity:,} TEU/day. Throughput used as capacity proxy (MODELED interpretation of a sourced figure)."
                status = "SOURCED"
        elif kind == "chokepoint":
            capacity, extra, derivation, status = CHOKE_CAP[n["id"]]
            if extra:
                sources.append(extra)
        elif kind == "air_hub":
            if n["id"] in AIR_OVERRIDE:
                capacity, extra, derivation, status = AIR_OVERRIDE[n["id"]]
                if extra:
                    sources.append(extra)
            else:
                m = re.search(r"([\d]+\.?\d*)M tonnes .*?in (\d{4})", note)
                annual = float(m.group(1)) * 1_000_000
                capacity = round(annual / 365)
                derivation = f"{m.group(1)}M tonnes cargo in {m.group(2)} (census capacity_note, ACI) / 365 = {capacity:,} tonnes/day. Throughput used as capacity proxy (MODELED interpretation of a sourced figure)."
                status = "SOURCED"
        else:  # ground_node
            if n["id"] in GROUND_OVERRIDE:
                capacity, extra, derivation, status = GROUND_OVERRIDE[n["id"]]
                if extra:
                    sources.append(extra)
            else:
                capacity = GROUND_DEFAULT_TRUCKS
                derivation = ("No sourced trucks/day (or convertible TEU/yr) figure retrieved for this node. "
                              f"MODELED engine default {GROUND_DEFAULT_TRUCKS:,} trucks/day. GAP - see calibration.md")
                status = "GAP-MODELED"
        if not sources:
            sources = [GAP_SOURCE]
        if kind == "port":
            queue_cap = capacity * 3          # MODELED: 3 days of throughput as waiting stock (TEU)
        elif kind == "chokepoint":
            queue_cap = 150 if n["id"] == "chk_panama" else capacity * 2
            # Panama queue_cap is the observed crisis queue (~135-150 ships, Ballast Markets compilation of ACP/press) - SOURCED observation, not the formula.
        elif kind == "air_hub":
            queue_cap = capacity * 2          # MODELED: 2 days of throughput (tonnes)
        else:
            queue_cap = capacity              # MODELED: 1 day of throughput (trucks)
        aliases = list(n.get("aliases", []))
        # Only unique registry codes become aliases (aliases must not
        # collide across nodes). Shared descriptors in the census codes dict
        # (border_crossing e.g. "US-MX", gauge_break) are NOT unique to a node
        # and are therefore not aliases.
        for k in ("unlocode", "iata", "icao"):
            v = (n.get("codes") or {}).get(k)
            if isinstance(v, str) and v and v not in aliases:
                aliases.append(v)
        node = {
            "id": n["id"], "name": n["name"], "mode": mode, "kind": kind,
            "lat": n["lat"], "lon": n["lon"], "capacity": capacity, "capacity_unit": unit,
            "queue_cap": queue_cap, "inventory_days": INVENTORY_DAYS[kind],
            "aliases": aliases, "sources": sources,
            "tier": tier,
        }
        if tier == "shell":
            node["capacity_basis"] = n["capacity_basis"]
        # Country (see module docstring): the
        # census entry's own `country`, served verbatim. Absent exactly
        # when the census carries none — the chokepoints, country-null
        # by kind — and never filled in by the builder.
        if n.get("country"):
            node["country"] = n["country"]
        nodes.append(node)
        calib.append({"id": n["id"], "kind": kind, "capacity": capacity, "unit": unit,
                      "queue_cap": queue_cap, "inventory_days": INVENTORY_DAYS[kind],
                      "status": status, "derivation": derivation,
                      "draft_source_url": src_url, "country": n.get("country")})
    return nodes, calib

# ---------------------------------------------------------------------------
# Edge set. Tuple: (from, to, mode, base_transit_d, capacity_share, note[, bypass_of])
# Calibration objects are assigned by rule (documented in calibration.md):
#   sea, both endpoints ports        -> volume IMF PortWatch, efficiency World Bank CPPI 2024
#   sea, any chokepoint endpoint     -> volume IMF PortWatch, efficiency UNCTAD LSCI 2024
#   air / ground                     -> volume UN Comtrade 2024, no efficiency_source
# base_transit_d values are MODELED point values inside published typical lane
# ranges (carrier schedule / sailing-distance ranges) - see calibration.md.
# They remain MODELED after the join: the PortWatch services the adapter
# fetches (ports registry vessel counts; disruptions view event dates) carry
# no transit-time / AIS-duration field, so no sourced transit derivation
# exists in the recorded data.
# capacity_share values START as MODELED allocations of each from-node's
# outbound flow, ranked consistently with the census throughput rankings and
# the calibration datasets' published aggregates. portwatch_join() below then
# re-derives the sea edges the pinned PortWatch snapshot supports;
# every other edge keeps its MODELED allocation and records why.
# ---------------------------------------------------------------------------
RAW_EDGES = [
    # --- Trans-Pacific container lanes ---
    ("sea_shanghai", "sea_losangeles", "sea", 14.0, 0.14, "Trans-Pacific lane"),
    ("sea_shanghai", "sea_longbeach", "sea", 14.0, 0.05, "Trans-Pacific lane"),
    ("sea_ningbo", "sea_losangeles", "sea", 14.0, 0.16, "Trans-Pacific lane"),
    ("sea_ningbo", "sea_longbeach", "sea", 14.0, 0.05, "Trans-Pacific lane"),
    ("sea_shenzhen", "sea_losangeles", "sea", 13.0, 0.15, "Trans-Pacific lane"),
    ("sea_shenzhen", "sea_longbeach", "sea", 13.0, 0.05, "Trans-Pacific lane"),
    ("sea_busan", "sea_losangeles", "sea", 12.0, 0.18, "Trans-Pacific lane"),
    ("sea_busan", "sea_longbeach", "sea", 12.0, 0.06, "Trans-Pacific lane"),
    ("sea_qingdao", "sea_losangeles", "sea", 13.0, 0.14, "Trans-Pacific lane"),
    ("sea_xiamen", "sea_losangeles", "sea", 14.0, 0.12, "Trans-Pacific lane"),
    ("sea_kaohsiung", "sea_losangeles", "sea", 13.0, 0.14, "Trans-Pacific lane"),
    ("sea_tokyo", "sea_losangeles", "sea", 11.0, 0.16, "Trans-Pacific lane"),
    ("sea_hochiminh", "sea_losangeles", "sea", 16.0, 0.10, "Trans-Pacific lane"),
    ("sea_caimep", "sea_longbeach", "sea", 16.0, 0.10, "Trans-Pacific lane"),
    ("sea_guangzhou", "sea_losangeles", "sea", 14.0, 0.10, "Trans-Pacific lane"),
    ("sea_hongkong", "sea_losangeles", "sea", 13.0, 0.12, "Trans-Pacific lane"),
    ("sea_shanghai", "sea_newyork", "sea", 25.0, 0.08, "Asia-US East Coast lane via Panama Canal (single edge; route ambiguity documented in calibration.md)"),
    ("sea_ningbo", "sea_newyork", "sea", 25.0, 0.07, "Asia-US East Coast lane via Panama Canal"),
    ("sea_shenzhen", "sea_newyork", "sea", 24.0, 0.07, "Asia-US East Coast lane via Panama Canal"),
    ("sea_shanghai", "sea_savannah", "sea", 26.0, 0.04, "Asia-US East Coast lane via Panama Canal"),
    ("sea_shanghai", "sea_houston", "sea", 27.0, 0.03, "Asia-US Gulf lane via Panama Canal"),
    ("sea_busan", "sea_newyork", "sea", 24.0, 0.08, "Asia-US East Coast lane via Panama Canal"),
    ("sea_losangeles", "sea_shanghai", "sea", 16.0, 0.20, "Trans-Pacific lane, westbound"),
    ("sea_longbeach", "sea_shanghai", "sea", 16.0, 0.15, "Trans-Pacific lane, westbound"),
    ("sea_newyork", "sea_shanghai", "sea", 27.0, 0.12, "US East Coast-Asia lane via Panama Canal, westbound"),
    # --- Asia-Europe corridor segments (via Malacca / Bab el-Mandeb / Suez) ---
    ("sea_shanghai", "sea_singapore", "sea", 5.0, 0.10, "Asia-Europe corridor segment, South China Sea"),
    ("sea_singapore", "chk_malacca", "sea", 1.0, 0.30, "Chokepoint transit leg"),
    ("sea_portklang", "chk_malacca", "sea", 0.7, 0.25, "Chokepoint transit leg"),
    ("sea_tanjungpelepas", "chk_malacca", "sea", 0.5, 0.30, "Chokepoint transit leg"),
    ("chk_malacca", "sea_colombo", "sea", 5.0, 0.35, "Asia-Europe corridor segment, Indian Ocean"),
    ("chk_malacca", "sea_singapore", "sea", 1.0, 0.20, "Chokepoint transit leg, eastbound"),
    ("sea_colombo", "chk_babelmandeb", "sea", 6.0, 0.25, "Asia-Europe corridor segment, Arabian Sea / Gulf of Aden"),
    ("chk_babelmandeb", "sea_jeddah", "sea", 1.5, 0.30, "Red Sea segment"),
    ("sea_jeddah", "chk_suez", "sea", 1.5, 0.35, "Red Sea segment to canal south entrance"),
    ("chk_suez", "sea_portsaid", "sea", 0.7, 0.40, "Canal transit leg (convoy transit ~11-16 h plus waiting, MODELED 0.7 d)"),
    ("chk_suez", "sea_jeddah", "sea", 1.5, 0.20, "Red Sea segment, southbound"),
    ("sea_portsaid", "sea_piraeus", "sea", 2.0, 0.20, "Eastern Mediterranean segment"),
    ("sea_portsaid", "sea_rotterdam", "sea", 11.0, 0.15, "Mediterranean-Gibraltar-North Europe lane"),
    ("sea_piraeus", "sea_rotterdam", "sea", 9.0, 0.12, "Mediterranean-North Europe lane"),
    ("sea_piraeus", "sea_valencia", "sea", 4.0, 0.15, "Mediterranean lane"),
    ("sea_valencia", "sea_algeciras", "sea", 1.5, 0.20, "Western Mediterranean segment"),
    ("sea_algeciras", "chk_gibraltar", "sea", 0.3, 0.30, "Chokepoint transit leg"),
    ("chk_gibraltar", "sea_tangermed", "sea", 0.3, 0.25, "Chokepoint transit leg"),
    ("sea_tangermed", "sea_algeciras", "sea", 0.3, 0.25, "Strait of Gibraltar shuttle segment"),
    ("sea_algeciras", "sea_rotterdam", "sea", 5.0, 0.15, "Atlantic Europe lane"),
    ("sea_jeddah", "sea_portsaid", "sea", 3.0, 0.15, "Red Sea + Suez combined segment (primary leg for Cape bypasses 51/52 in calibration table)"),
    # --- Cape of Good Hope bypasses (bypass_of) ---
    ("sea_colombo", "chk_capeofgoodhope", "sea", 11.0, 0.05, "Cape reroute leg (UNCTAD: Cape arrivals +85-89% in the 2024 Red Sea crisis)", "sea_colombo->chk_babelmandeb:sea"),
    ("chk_capeofgoodhope", "sea_rotterdam", "sea", 13.0, 0.30, "Cape reroute leg, Atlantic ascent to North Europe", "sea_portsaid->sea_rotterdam:sea"),
    ("sea_jeddah", "chk_capeofgoodhope", "sea", 9.0, 0.03, "Cape reroute leg - vessel exits the Red Sea southbound and rounds Africa instead of transiting Suez", "sea_jeddah->chk_suez:sea"),
    ("chk_capeofgoodhope", "sea_portsaid", "sea", 16.0, 0.10, "Cape reroute leg - Atlantic + Gibraltar + Mediterranean to the canal's north entrance", "chk_suez->sea_portsaid:sea"),
    ("sea_singapore", "sea_colombo", "sea", 5.0, 0.15, "Sunda/Lombok-route direct leg modelled as bypassing the Malacca transit leg", "sea_singapore->chk_malacca:sea"),
    # --- Direct Asia-Europe / Asia-Med lanes ---
    ("sea_shanghai", "sea_rotterdam", "sea", 30.0, 0.16, "Asia-North Europe direct lane via Suez"),
    ("sea_ningbo", "sea_rotterdam", "sea", 29.0, 0.15, "Asia-North Europe direct lane via Suez"),
    ("sea_shenzhen", "sea_antwerp", "sea", 28.0, 0.12, "Asia-North Europe direct lane via Suez"),
    ("sea_shanghai", "sea_antwerp", "sea", 30.0, 0.07, "Asia-North Europe direct lane via Suez"),
    ("sea_singapore", "sea_rotterdam", "sea", 26.0, 0.18, "Asia-North Europe direct lane via Suez"),
    ("sea_busan", "sea_rotterdam", "sea", 31.0, 0.10, "Asia-North Europe direct lane via Suez"),
    ("sea_shanghai", "sea_hamburg", "sea", 31.0, 0.06, "Asia-North Europe direct lane via Suez"),
    ("sea_qingdao", "sea_rotterdam", "sea", 32.0, 0.08, "Asia-North Europe direct lane via Suez"),
    ("sea_jebelali", "sea_rotterdam", "sea", 18.0, 0.12, "Gulf-North Europe lane via Suez"),
    ("sea_colombo", "sea_rotterdam", "sea", 19.0, 0.10, "South Asia-North Europe lane via Suez"),
    ("sea_rotterdam", "sea_shanghai", "sea", 31.0, 0.18, "North Europe-Asia lane via Suez, eastbound"),
    ("sea_antwerp", "sea_shanghai", "sea", 31.0, 0.15, "North Europe-Asia lane via Suez, eastbound"),
    ("sea_mundra", "sea_colombo", "sea", 3.0, 0.15, "West India-South Asia transshipment segment"),
    # --- Hormuz / Gulf segments ---
    ("sea_jebelali", "chk_hormuz", "sea", 1.0, 0.25, "Chokepoint transit leg"),
    ("chk_hormuz", "sea_salalah", "sea", 2.0, 0.30, "Gulf of Oman / Arabian Sea segment"),
    ("chk_hormuz", "sea_mundra", "sea", 3.0, 0.20, "Gulf-West India lane"),
    ("sea_salalah", "sea_jeddah", "sea", 4.0, 0.12, "Arabian Sea / Red Sea lane (Bab el-Mandeb passage)"),
    ("sea_salalah", "sea_colombo", "sea", 5.0, 0.12, "Arabian Sea lane"),
    ("sea_jebelali", "sea_singapore", "sea", 7.0, 0.12, "Gulf-Southeast Asia lane via Malacca"),
    # --- Panama Canal segments ---
    ("sea_shanghai", "chk_panama", "sea", 20.0, 0.06, "Trans-Pacific approach to the canal's Pacific entrance"),
    ("chk_panama", "sea_newyork", "sea", 6.0, 0.25, "Canal Atlantic exit to US East Coast"),
    ("chk_panama", "sea_savannah", "sea", 5.0, 0.15, "Canal Atlantic exit to US East Coast"),
    ("chk_panama", "sea_houston", "sea", 6.0, 0.15, "Canal Atlantic exit to US Gulf"),
    ("chk_panama", "sea_colon", "sea", 0.5, 0.20, "Canal transit leg to the Atlantic terminal cluster (canal transit ~0.5 d incl. scheduling, MODELED)"),
    ("sea_colon", "chk_panama", "sea", 0.5, 0.20, "Canal transit leg, westbound entry"),
    ("sea_colon", "sea_newyork", "sea", 5.0, 0.18, "Caribbean-US East Coast lane"),
    ("chk_panama", "sea_shanghai", "sea", 21.0, 0.20, "Canal Pacific exit to North Asia"),
    ("sea_losangeles", "chk_panama", "sea", 9.0, 0.08, "US West Coast to canal Pacific entrance (land-bridge alternative in the Panama replay)"),
    # --- Intra-Asia lanes ---
    ("sea_shanghai", "sea_busan", "sea", 2.0, 0.07, "Intra-Asia lane"),
    ("sea_busan", "sea_tokyo", "sea", 2.0, 0.15, "Intra-Asia lane"),
    ("sea_shanghai", "sea_hongkong", "sea", 3.0, 0.04, "Intra-Asia lane"),
    ("sea_shenzhen", "sea_hongkong", "sea", 0.5, 0.10, "Pearl River Delta segment"),
    ("sea_shanghai", "sea_qingdao", "sea", 1.5, 0.04, "China coastal segment"),
    ("sea_qingdao", "sea_dalian", "sea", 1.0, 0.20, "Bohai Rim segment"),
    ("sea_dalian", "sea_tianjin", "sea", 1.0, 0.25, "Bohai Rim segment"),
    ("sea_shanghai", "sea_ningbo", "sea", 1.0, 0.05, "Yangtze Delta segment"),
    ("sea_ningbo", "sea_xiamen", "sea", 1.5, 0.12, "China coastal segment"),
    ("sea_xiamen", "chk_taiwan", "sea", 0.5, 0.20, "Chokepoint transit leg (port sits on the strait)"),
    ("chk_taiwan", "sea_shanghai", "sea", 2.0, 0.30, "Taiwan Strait exit to Yangtze Delta"),
    ("sea_kaohsiung", "chk_taiwan", "sea", 0.5, 0.20, "Chokepoint transit leg"),
    ("chk_taiwan", "sea_hongkong", "sea", 1.5, 0.25, "Strait exit to Pearl River Delta"),
    ("sea_singapore", "sea_portklang", "sea", 1.0, 0.12, "Malacca Strait coastal segment"),
    ("sea_singapore", "sea_tanjungpelepas", "sea", 0.5, 0.10, "Singapore Strait segment"),
    ("sea_singapore", "sea_laemchabang", "sea", 2.0, 0.08, "Southeast Asia lane"),
    ("sea_laemchabang", "sea_hochiminh", "sea", 1.5, 0.15, "Gulf of Thailand segment"),
    ("sea_hochiminh", "sea_caimep", "sea", 0.5, 0.25, "Vietnam coastal segment (same gateway complex)"),
    ("sea_caimep", "sea_hongkong", "sea", 2.0, 0.18, "South China Sea lane"),
    ("sea_haiphong", "sea_hongkong", "sea", 1.5, 0.20, "South China Sea lane"),
    ("sea_manila", "sea_hongkong", "sea", 2.0, 0.20, "South China Sea lane"),
    ("sea_manila", "sea_kaohsiung", "sea", 1.5, 0.20, "Luzon Strait segment"),
    ("sea_tanjungpriok", "sea_singapore", "sea", 2.0, 0.25, "Java Sea / Singapore Strait lane"),
    ("sea_busan", "sea_qingdao", "sea", 1.5, 0.12, "Yellow Sea lane"),
    ("sea_rizhao", "sea_qingdao", "sea", 0.7, 0.30, "Shandong coastal segment"),
    ("sea_taicang", "sea_shanghai", "sea", 0.5, 0.35, "Yangtze Delta feeder segment"),
    ("sea_taicang", "sea_ningbo", "sea", 1.0, 0.20, "Yangtze Delta feeder segment"),
    # --- Europe / Atlantic / Americas sea lanes ---
    ("sea_rotterdam", "sea_antwerp", "sea", 0.5, 0.10, "North Europe short-sea segment"),
    ("sea_antwerp", "sea_hamburg", "sea", 1.0, 0.12, "North Europe short-sea segment"),
    ("sea_hamburg", "sea_bremerhaven", "sea", 0.5, 0.25, "German Bight segment"),
    ("sea_rotterdam", "sea_newyork", "sea", 11.0, 0.12, "North Atlantic lane"),
    ("sea_hamburg", "sea_newyork", "sea", 12.0, 0.08, "North Atlantic lane"),
    ("sea_rotterdam", "sea_santos", "sea", 13.0, 0.06, "Europe-South America lane"),
    ("sea_algeciras", "sea_santos", "sea", 10.0, 0.10, "Atlantic lane to Brazil"),
    ("sea_santos", "sea_rotterdam", "sea", 13.0, 0.25, "South America-Europe lane"),
    ("sea_santos", "sea_newyork", "sea", 10.0, 0.20, "South America-US East Coast lane"),
    ("sea_newyork", "sea_rotterdam", "sea", 11.0, 0.15, "North Atlantic lane, eastbound"),
    ("sea_savannah", "sea_houston", "sea", 4.0, 0.15, "US East Coast-Gulf coastal lane"),
    ("sea_hamburg", "chk_danish", "sea", 1.5, 0.15, "Chokepoint transit leg toward the Baltic (no Baltic port node exists in the census - see Gaps)"),
    ("chk_danish", "sea_hamburg", "sea", 1.5, 0.30, "Chokepoint transit leg, Baltic exit"),
    ("sea_piraeus", "chk_turkish", "sea", 1.0, 0.15, "Chokepoint transit leg toward the Black Sea (no Black Sea port node exists in the census - see Gaps)"),
    ("chk_turkish", "sea_piraeus", "sea", 1.0, 0.30, "Chokepoint transit leg, Black Sea exit"),
    ("sea_tangermed", "sea_rotterdam", "sea", 5.0, 0.20, "Atlantic Europe lane"),
    ("sea_bremerhaven", "sea_hamburg", "sea", 0.5, 0.20, "German Bight segment"),
    # --- Air cargo lanes ---
    ("air_hongkong", "air_shanghaipudong", "air", 0.4, 0.20, "Intra-Asia air lane (door-to-door days, MODELED)"),
    ("air_shanghaipudong", "air_anchorage", "air", 0.5, 0.18, "Trans-Pacific air lane, tech-stop pattern"),
    ("air_anchorage", "air_chicago", "air", 0.4, 0.25, "North America air lane"),
    ("air_anchorage", "air_memphis", "air", 0.5, 0.20, "North America air lane (integrator pattern)"),
    ("air_anchorage", "air_louisville", "air", 0.5, 0.15, "North America air lane (integrator pattern)"),
    ("air_hongkong", "air_anchorage", "air", 0.5, 0.15, "Trans-Pacific air lane, tech-stop pattern"),
    ("air_incheon", "air_anchorage", "air", 0.4, 0.20, "Trans-Pacific air lane, tech-stop pattern"),
    ("air_narita", "air_anchorage", "air", 0.4, 0.20, "Trans-Pacific air lane, tech-stop pattern"),
    ("air_taipei", "air_anchorage", "air", 0.5, 0.18, "Trans-Pacific air lane, tech-stop pattern"),
    ("air_hongkong", "air_losangeles", "air", 1.0, 0.12, "Trans-Pacific air lane, direct"),
    ("air_shanghaipudong", "air_losangeles", "air", 1.0, 0.15, "Trans-Pacific air lane, direct"),
    ("air_incheon", "air_losangeles", "air", 1.0, 0.15, "Trans-Pacific air lane, direct"),
    ("air_narita", "air_chicago", "air", 0.9, 0.18, "Trans-Pacific air lane, direct"),
    ("air_chicago", "air_frankfurt", "air", 0.8, 0.20, "Trans-Atlantic air lane"),
    ("air_paris", "air_frankfurt", "air", 0.3, 0.20, "Intra-Europe air lane"),
    ("air_london", "air_paris", "air", 0.3, 0.20, "Intra-Europe air lane"),
    ("air_amsterdam", "air_frankfurt", "air", 0.3, 0.20, "Intra-Europe air lane"),
    ("air_istanbul", "air_frankfurt", "air", 0.4, 0.25, "Europe-Middle East air lane"),
    ("air_dubai", "air_frankfurt", "air", 0.6, 0.20, "Europe-Middle East air lane"),
    ("air_doha", "air_frankfurt", "air", 0.6, 0.20, "Europe-Middle East air lane"),
    ("air_singapore", "air_frankfurt", "air", 1.0, 0.18, "Asia-Europe air lane"),
    ("air_singapore", "air_dubai", "air", 0.7, 0.15, "Asia-Middle East air lane"),
    ("air_dubai", "air_doha", "air", 0.2, 0.15, "Gulf air segment"),
    ("air_bangkok", "air_singapore", "air", 0.3, 0.25, "Southeast Asia air lane"),
    ("air_jakarta", "air_singapore", "air", 0.3, 0.30, "Southeast Asia air lane"),
    ("air_delhi", "air_dubai", "air", 0.4, 0.25, "South Asia-Gulf air lane"),
    ("air_delhi", "air_frankfurt", "air", 0.8, 0.20, "South Asia-Europe air lane"),
    ("air_addisababa", "air_dubai", "air", 0.5, 0.25, "Africa-Gulf air lane"),
    ("air_addisababa", "air_frankfurt", "air", 0.7, 0.20, "Africa-Europe air lane"),
    ("air_addisababa", "air_london", "air", 0.8, 0.20, "Africa-Europe air lane"),
    ("air_miami", "air_losangeles", "air", 0.5, 0.20, "US domestic air lane"),
    ("air_guangzhou", "air_shanghaipudong", "air", 0.3, 0.25, "China domestic air lane"),
    ("air_shenzhen", "air_shanghaipudong", "air", 0.3, 0.20, "China domestic air lane"),
    ("air_beijing", "air_shanghaipudong", "air", 0.3, 0.25, "China domestic air lane"),
    ("air_hongkong", "air_taipei", "air", 0.2, 0.12, "Intra-Asia air lane"),
    ("air_hongkong", "air_singapore", "air", 0.4, 0.12, "Intra-Asia air lane"),
    ("air_hongkong", "air_bangkok", "air", 0.3, 0.10, "Intra-Asia air lane"),
    ("air_taipei", "air_losangeles", "air", 1.0, 0.15, "Trans-Pacific air lane, direct"),
    ("air_losangeles", "air_memphis", "air", 0.4, 0.15, "US domestic air lane (integrator pattern)"),
    ("air_louisville", "air_cincinnati", "air", 0.2, 0.20, "US integrator air segment"),
    ("air_cincinnati", "air_memphis", "air", 0.2, 0.25, "US integrator air segment"),
    ("air_indianapolis", "air_memphis", "air", 0.2, 0.20, "US integrator air segment"),
    ("air_ontario", "air_memphis", "air", 0.4, 0.30, "US domestic air lane (integrator pattern)"),
    ("air_doha", "air_london", "air", 0.7, 0.15, "Gulf-Europe air lane"),
    ("air_frankfurt", "air_chicago", "air", 0.8, 0.15, "Trans-Atlantic air lane, westbound"),
    ("sea_houston", "sea_newyork", "sea", 5.0, 0.12, "US Gulf-East Coast coastal lane"),
    ("sea_tianjin", "sea_shanghai", "sea", 2.0, 0.20, "Bohai Rim to Yangtze Delta lane"),
    # --- Ground corridors: North America ---
    ("sea_losangeles", "gnd_inlandempire", "ground", 0.5, 0.10, "Port drayage/rail to Inland Empire warehouse cluster (LA replay landside leg)"),
    ("sea_longbeach", "gnd_inlandempire", "ground", 0.5, 0.10, "Port drayage/rail to Inland Empire"),
    ("gnd_inlandempire", "gnd_chicago", "ground", 4.0, 0.25, "Transcontinental intermodal rail corridor"),
    ("gnd_inlandempire", "gnd_dallas", "ground", 3.0, 0.20, "Intermodal rail corridor"),
    ("gnd_chicago", "gnd_dallas", "ground", 2.0, 0.15, "Intermodal rail corridor"),
    ("gnd_chicago", "gnd_kansascity", "ground", 1.0, 0.15, "Intermodal rail corridor"),
    ("gnd_chicago", "gnd_memphis", "ground", 1.0, 0.12, "Intermodal rail corridor"),
    ("gnd_chicago", "gnd_detroit", "ground", 0.7, 0.10, "Intermodal rail corridor"),
    ("gnd_detroit", "gnd_buffalo", "ground", 0.7, 0.25, "Border-corridor ground leg (via Ontario)"),
    ("gnd_buffalo", "sea_newyork", "ground", 1.0, 0.20, "Ground leg to port complex"),
    ("gnd_toronto", "gnd_detroit", "ground", 0.5, 0.30, "Canada-US border corridor"),
    ("gnd_montreal", "gnd_toronto", "ground", 1.0, 0.30, "Canadian intermodal corridor"),
    ("gnd_vancouver", "gnd_blaine", "ground", 0.3, 0.35, "Canada-US border corridor"),
    ("gnd_blaine", "gnd_chicago", "ground", 3.0, 0.20, "Northern transcontinental rail corridor"),
    ("gnd_laredo", "gnd_dallas", "ground", 1.0, 0.30, "US-Mexico border corridor (BTS top truck port)"),
    ("gnd_laredo", "sea_houston", "ground", 0.7, 0.20, "Border-to-Gulf-port ground leg"),
    ("gnd_elpaso", "gnd_dallas", "ground", 1.5, 0.30, "US-Mexico border corridor"),
    ("gnd_otaymesa", "gnd_inlandempire", "ground", 0.5, 0.30, "US-Mexico border corridor to warehouse cluster"),
    ("gnd_nogales", "gnd_dallas", "ground", 2.0, 0.25, "US-Mexico border corridor"),
    ("gnd_dallas", "gnd_atlanta", "ground", 2.0, 0.15, "Intermodal rail corridor"),
    ("gnd_memphis", "gnd_atlanta", "ground", 1.0, 0.20, "Intermodal rail corridor"),
    ("gnd_atlanta", "sea_savannah", "ground", 0.5, 0.25, "Inland-to-port ground leg"),
    ("gnd_columbus", "sea_savannah", "ground", 1.0, 0.20, "Inland-to-port ground leg"),
    ("gnd_kansascity", "gnd_dallas", "ground", 1.5, 0.25, "Intermodal rail corridor"),
    ("gnd_porthuron", "gnd_detroit", "ground", 0.5, 0.30, "Canada-US border corridor"),
    ("sea_newyork", "gnd_columbus", "ground", 1.2, 0.05, "Port-to-Midwest intermodal leg"),
    # --- Ground corridors: Europe ---
    ("sea_rotterdam", "gnd_duisburg", "ground", 0.5, 0.06, "Rhine corridor port-to-inland-port leg"),
    ("sea_antwerp", "gnd_duisburg", "ground", 0.7, 0.06, "Port-to-inland-port leg"),
    ("gnd_duisburg", "gnd_warsaw", "ground", 2.0, 0.20, "China-EU rail corridor, EU segment"),
    ("gnd_warsaw", "gnd_malaszewicze", "ground", 0.7, 0.30, "China-EU rail corridor to the gauge break"),
    ("gnd_malaszewicze", "gnd_duisburg", "ground", 3.0, 0.25, "China-EU rail corridor, westbound exit from the gauge break"),
    ("gnd_duisburg", "gnd_budapest", "ground", 2.0, 0.12, "EU intermodal corridor"),
    ("gnd_duisburg", "gnd_milan", "ground", 1.5, 0.12, "EU intermodal corridor (Alpine crossing)"),
    ("gnd_duisburg", "gnd_bettembourg", "ground", 0.7, 0.10, "EU intermodal corridor"),
    ("gnd_bettembourg", "gnd_paris", "ground", 0.7, 0.30, "EU intermodal corridor"),
    ("gnd_paris", "gnd_channeltunnel", "ground", 0.5, 0.25, "Ground leg to Channel Tunnel terminal"),
    ("gnd_channeltunnel", "gnd_paris", "ground", 0.5, 0.30, "Channel Tunnel shuttle corridor, continent-bound"),
    ("sea_hamburg", "gnd_duisburg", "ground", 0.7, 0.06, "Port-to-inland-port leg"),
    ("gnd_madrid", "gnd_paris", "ground", 2.0, 0.30, "Iberia-France ground corridor (Iberian gauge break noted in census)"),
    ("gnd_milan", "gnd_bettembourg", "ground", 1.5, 0.25, "EU intermodal corridor"),
    # --- Ground corridors: Asia / Middle East / Africa ---
    ("gnd_khorgos", "gnd_malaszewicze", "ground", 11.0, 0.35, "China-EU northern rail corridor (via Kazakhstan/Russia/Belarus)"),
    ("gnd_dostyk", "gnd_malaszewicze", "ground", 12.0, 0.30, "China-EU northern rail corridor, original crossing"),
    ("gnd_manzhouli", "gnd_malaszewicze", "ground", 9.0, 0.35, "Trans-Manchurian / Trans-Siberian rail corridor"),
    ("gnd_erenhot", "gnd_malaszewicze", "ground", 10.0, 0.30, "Trans-Mongolian rail corridor"),
    ("gnd_khorgos", "gnd_halkali", "ground", 12.0, 0.20, "Middle Corridor (Trans-Caspian) rail leg, MODELED routing"),
    ("gnd_halkali", "gnd_budapest", "ground", 3.0, 0.30, "Turkey-EU rail corridor"),
    ("sea_nhavasheva", "gnd_dadri", "ground", 2.0, 0.08, "Port-to-ICD rail leg (Western Dedicated Freight Corridor)"),
    ("sea_mundra", "gnd_dadri", "ground", 2.0, 0.08, "Port-to-ICD rail leg"),
    ("sea_dammam", "gnd_riyadh", "ground", 1.0, 0.06, "Dammam port to Riyadh Dry Port rail leg - the dry port's primary rail link. REPOINTED in the graph expansion: this leg landed on Jeddah while Dammam was not a census node (documented proxy, Gaps #7); Dammam is a node now, so the leg lands on the port the source names"),
    ("sea_durban", "gnd_citydeep", "ground", 1.0, 0.05, "Durban port to Johannesburg City Deep inland terminal rail leg (Durban is the container gateway for Gauteng). REPOINTED in the graph expansion: this leg landed on the Cape route node as a documented proxy while Durban was not a census node (Gaps #7)"),
    # --- Intermodal short links (mode = ground) ---
    ("sea_losangeles", "air_losangeles", "ground", 0.2, 0.03, "Port-to-airport mode-shift link (LA replay mode-shift leg)"),
    ("gnd_inlandempire", "air_ontario", "ground", 0.2, 0.15, "Warehouse cluster to UPS air hub"),
    ("sea_hongkong", "air_hongkong", "ground", 0.3, 0.03, "Port-to-airport mode-shift link"),
    ("sea_shanghai", "air_shanghaipudong", "ground", 0.3, 0.03, "Port-to-airport mode-shift link"),
    ("air_dubai", "air_dubaiworldcentral", "ground", 0.2, 0.10, "Intra-Dubai airport transfer (freighter shift DXB-DWC)"),
    ("air_memphis", "gnd_memphis", "ground", 0.1, 0.10, "Air hub to intermodal cluster"),
    ("gnd_memphis", "air_memphis", "ground", 0.1, 0.15, "Intermodal cluster to air hub"),
    ("sea_tanjungpriok", "air_jakarta", "ground", 0.3, 0.04, "Port-to-airport mode-shift link"),
    ("gnd_dadri", "air_delhi", "ground", 0.3, 0.20, "ICD to air hub"),
    ("sea_busan", "air_incheon", "ground", 0.5, 0.04, "Port-to-airport mode-shift link"),
    ("air_istanbul", "gnd_halkali", "ground", 0.3, 0.15, "Air hub to rail terminal"),
    ("air_dubaiworldcentral", "air_dubai", "ground", 0.2, 0.25, "Intra-Dubai airport transfer, return leg"),
    ("gnd_budapest", "gnd_duisburg", "ground", 2.0, 0.20, "EU intermodal corridor, westbound"),
]

# ---------------------------------------------------------------------------
# PortWatch calibration join.
#
# INPUT: the pinned snapshot extract `portwatch_join_input.json` (see module
# docstring). It carries per-node vessel counts from the IMF PortWatch ports
# database as recorded by server/adapters/portwatch.py — port calls by vessel
# type, NOT bilateral O-D flows and NOT TEU volumes (the fetched services do
# not publish either). Duplicate port records for one node (e.g. sea_ningbo
# port824 + port1429) are summed per node.
#
# FORMULA (destination-attraction, stated in full): for a sea PORT origin O,
# let COV(O) be the destinations of O's sea edges whose node has a PortWatch
# container-vessel count c(D) > 0 in the pinned input, and let S_cov be the
# sum of the pre-join MODELED capacity_share over COV(O). If |COV(O)| >= 2,
# each covered edge is re-derived as
#     capacity_share(O->D) = S_cov * c(D) / sum_{D' in COV(O)} c(D')
# i.e. the origin's covered subtotal is preserved exactly and redistributed
# in proportion to destination container-vessel calls. Shares are apportioned
# in integer units of 1e-4 by largest remainder (ties broken by edge key), so
# the redistribution is exact at 4 dp and fully deterministic. If |COV(O)| < 2
# the snapshot cannot support a distribution and the edge stays MODELED.
# Edges whose destination is a chokepoint can never be covered (the snapshot
# carries no chokepoint records), and air/ground edges are out of PortWatch
# scope entirely (their join is UN Comtrade — see build/comtrade_overlay.py).
#
# CHOKEPOINT origins
# are excluded from the join entirely. Chokepoint-exit legs are transit
# flows, not port calls — a vessel transits Suez past Port Said without
# calling there — so destination call-attraction is the wrong mechanism for
# these legs: Port Said's low container call count mechanically shrank
# chk_suez->sea_portsaid 0.40 -> 0.1217 under the original join. All 8
# chokepoint->port edges the original join re-derived revert to exactly
# their pre-join base MODELED shares (the join only ever overwrites covered
# edges in place, so excluding the origin restores the RAW_EDGES allocation
# untouched); their calibration records the ruling as the reason they stay
# MODELED, pending a transit-distribution source. The 35 port->port
# derivations stand.
#
# LIMITATION (also stated in calibration.md): destination container calls are
# an attraction proxy, not observed O-D flow. Every re-derived edge records
# its exact inputs in calibration.share_inputs so the derivation is auditable
# and reversible.
# ---------------------------------------------------------------------------
PW_SHARE_SOURCE = ("IMF PortWatch ports database (ArcGIS FeatureServer "
                   "PortWatch_ports_database/FeatureServer/0) via pinned input "
                   "data/graph/portwatch_join_input.json (adapter snapshot "
                   "data/snapshots/portwatch.json)")
PW_SHARE_METHOD = ("PortWatch destination-attraction: capacity_share = S_cov * "
                   "vessel_count_container(to) / SUM(vessel_count_container over "
                   "covered sea destinations of from); S_cov = pre-join modeled "
                   "subtotal over the covered edges; apportioned in 1e-4 units "
                   "by largest remainder (ties by edge key)")
# ---------------------------------------------------------------------------
# Extended-census edges. Same tuple shape as RAW_EDGES.
# Shares are MODELED allocations on the same terms as the base set (ranked
# against census throughput; per-origin outbound sums kept <= 1.0 against
# RAW_EDGES + this list combined — note sea_shanghai's base sea budget is
# 0.99, so NO new outbound edge leaves Shanghai). Sea port->port edges are
# then re-derived by portwatch_join() wherever the (extended) pinned input
# covers them, exactly like the base edges; chokepoint legs stay MODELED per
# the chokepoint exclusion. base_transit_d values are MODELED point
# values inside published typical lane ranges. Every expansion node gets
# >= 2 inbound and >= 1 outbound edges (the
# validator's isolation check is the hard gate).
# ---------------------------------------------------------------------------
RAW_EDGES_EXPANSION = [
    # --- China coast / Bohai / Yangtze / PRD domestic + near-sea ---
    ("sea_qingdao", "sea_yantai", "sea", 0.7, 0.08, "Shandong peninsula coastal leg Qingdao-Yantai"),
    ("sea_yantai", "sea_qingdao", "sea", 0.7, 0.10, "Shandong peninsula coastal leg Yantai-Qingdao"),
    ("sea_yantai", "sea_dalian", "sea", 0.5, 0.15, "Bohai Strait crossing Yantai-Dalian (ro-ro/container lane)"),
    ("sea_dalian", "sea_yantai", "sea", 0.5, 0.10, "Bohai Strait crossing Dalian-Yantai"),
    ("sea_dalian", "sea_yingkou", "sea", 0.5, 0.20, "Liaodong Bay coastal leg Dalian-Yingkou"),
    ("sea_yingkou", "sea_tianjin", "sea", 1.0, 0.25, "Bohai Bay leg Yingkou-Tianjin (Xingang)"),
    ("sea_tianjin", "sea_yingkou", "sea", 1.0, 0.15, "Bohai Bay leg Tianjin-Yingkou"),
    ("sea_yingkou", "sea_qingdao", "sea", 1.2, 0.15, "Bohai exit leg Yingkou-Qingdao"),
    ("sea_qingdao", "sea_lianyungang", "sea", 0.8, 0.08, "Yellow Sea coastal leg Qingdao-Lianyungang"),
    ("sea_lianyungang", "sea_qingdao", "sea", 0.8, 0.12, "Yellow Sea coastal leg Lianyungang-Qingdao"),
    ("sea_lianyungang", "sea_shanghai", "sea", 1.2, 0.25, "Lianyungang-Shanghai coastal trunk"),
    ("sea_ningbo", "sea_lianyungang", "sea", 1.5, 0.04, "Ningbo-Lianyungang coastal leg"),
    ("sea_taicang", "sea_lianyungang", "sea", 1.5, 0.06, "Yangtze mouth to Lianyungang coastal leg"),
    ("sea_ningbo", "sea_fuzhou", "sea", 1.5, 0.05, "Ningbo-Fuzhou Taiwan Strait west-coast leg"),
    ("sea_fuzhou", "sea_xiamen", "sea", 0.8, 0.18, "Fujian coastal leg Fuzhou-Xiamen"),
    ("sea_xiamen", "sea_fuzhou", "sea", 0.8, 0.15, "Fujian coastal leg Xiamen-Fuzhou"),
    ("sea_fuzhou", "sea_shanghai", "sea", 1.8, 0.20, "Fuzhou-Shanghai strait/coastal trunk"),
    ("sea_fuzhou", "chk_taiwan", "sea", 0.3, 0.15, "Fuzhou into the Taiwan Strait"),
    ("chk_taiwan", "sea_fuzhou", "sea", 0.3, 0.10, "Taiwan Strait exit to Fuzhou"),
    ("sea_guangzhou", "sea_dongguan", "sea", 0.3, 0.10, "Pearl River leg Guangzhou-Dongguan (Humen)"),
    ("sea_dongguan", "sea_guangzhou", "sea", 0.3, 0.12, "Pearl River leg Dongguan-Guangzhou (Nansha)"),
    ("sea_dongguan", "sea_hongkong", "sea", 0.5, 0.15, "Dongguan-Hong Kong PRD leg"),
    ("sea_hongkong", "sea_dongguan", "sea", 0.5, 0.08, "Hong Kong-Dongguan PRD leg"),
    ("sea_dongguan", "sea_shenzhen", "sea", 0.4, 0.10, "Dongguan-Shenzhen PRD leg"),
    ("sea_shenzhen", "sea_dongguan", "sea", 0.4, 0.06, "Shenzhen-Dongguan PRD leg"),
    ("sea_qinzhou", "sea_haiphong", "sea", 0.8, 0.15, "Beibu Gulf leg Qinzhou-Hai Phong"),
    ("sea_haiphong", "sea_qinzhou", "sea", 0.8, 0.12, "Beibu Gulf leg Hai Phong-Qinzhou"),
    ("sea_qinzhou", "sea_guangzhou", "sea", 1.2, 0.20, "Qinzhou-Pearl River Delta coastal trunk"),
    ("sea_guangzhou", "sea_qinzhou", "sea", 1.2, 0.06, "Guangzhou-Qinzhou Beibu Gulf leg"),
    ("sea_qinzhou", "sea_hongkong", "sea", 1.0, 0.12, "Qinzhou-Hong Kong coastal leg"),
    # --- Japan / Korea / Taiwan ---
    ("sea_tokyo", "sea_yokohama", "sea", 0.3, 0.20, "Tokyo Bay pair leg Tokyo-Yokohama"),
    ("sea_yokohama", "sea_tokyo", "sea", 0.3, 0.25, "Tokyo Bay pair leg Yokohama-Tokyo"),
    ("sea_yokohama", "sea_nagoya", "sea", 0.8, 0.15, "Yokohama-Nagoya (Ise Bay) coastal leg"),
    ("sea_nagoya", "sea_yokohama", "sea", 0.8, 0.18, "Nagoya-Yokohama coastal leg"),
    ("sea_nagoya", "sea_tokyo", "sea", 1.0, 0.12, "Nagoya-Tokyo coastal trunk"),
    ("sea_tokyo", "sea_princerupert", "sea", 7.5, 0.06, "Transpacific Tokyo-Prince Rupert (shortest North America crossing)"),
    ("sea_yokohama", "sea_busan", "sea", 1.8, 0.10, "Yokohama-Busan Korea Strait leg"),
    ("sea_busan", "sea_yokohama", "sea", 1.8, 0.06, "Busan-Yokohama leg"),
    ("sea_busan", "sea_incheon", "sea", 0.8, 0.05, "Busan-Incheon Korean coastal leg"),
    ("sea_incheon", "sea_busan", "sea", 0.8, 0.12, "Incheon-Busan Korean coastal leg"),
    ("sea_incheon", "sea_qingdao", "sea", 1.0, 0.18, "Incheon-Qingdao Yellow Sea ferry/container lane"),
    ("sea_qingdao", "sea_incheon", "sea", 1.0, 0.08, "Qingdao-Incheon Yellow Sea lane"),
    ("sea_incheon", "sea_shanghai", "sea", 1.2, 0.22, "Incheon-Shanghai Yellow Sea trunk"),
    ("sea_gwangyang", "sea_busan", "sea", 0.4, 0.25, "Gwangyang-Busan Korean south-coast leg"),
    ("sea_busan", "sea_gwangyang", "sea", 0.4, 0.04, "Busan-Gwangyang leg"),
    ("sea_ningbo", "sea_gwangyang", "sea", 1.8, 0.05, "Ningbo-Gwangyang East China Sea leg"),
    ("sea_gwangyang", "sea_shanghai", "sea", 1.5, 0.20, "Gwangyang-Shanghai trunk"),
    ("sea_gwangyang", "sea_nagoya", "sea", 1.5, 0.10, "Gwangyang-Nagoya leg via Korea Strait"),
    ("sea_kaohsiung", "sea_taichung", "sea", 0.5, 0.15, "Kaohsiung-Taichung Taiwan west-coast leg"),
    ("sea_taichung", "sea_kaohsiung", "sea", 0.5, 0.20, "Taichung-Kaohsiung west-coast leg"),
    ("sea_taichung", "sea_xiamen", "sea", 0.7, 0.15, "Taichung-Xiamen cross-strait leg"),
    ("sea_xiamen", "sea_taichung", "sea", 0.7, 0.08, "Xiamen-Taichung cross-strait leg"),
    ("sea_keelung", "sea_taichung", "sea", 0.5, 0.18, "Keelung-Taichung Taiwan coastal leg"),
    ("sea_taichung", "sea_keelung", "sea", 0.5, 0.15, "Taichung-Keelung coastal leg"),
    ("sea_keelung", "chk_taiwan", "sea", 0.2, 0.12, "Keelung into the Taiwan Strait north exit"),
    ("chk_taiwan", "sea_keelung", "sea", 0.2, 0.08, "Taiwan Strait north exit to Keelung"),
    ("sea_keelung", "sea_shanghai", "sea", 1.2, 0.20, "Keelung-Shanghai East China Sea trunk"),
    # --- Southeast Asia ---
    ("sea_tanjungpriok", "sea_tanjungperak", "sea", 1.0, 0.15, "Jakarta-Surabaya Java Sea leg"),
    ("sea_tanjungperak", "sea_tanjungpriok", "sea", 1.0, 0.18, "Surabaya-Jakarta Java Sea leg"),
    ("sea_tanjungperak", "sea_singapore", "sea", 2.0, 0.22, "Surabaya-Singapore trunk"),
    ("sea_singapore", "sea_tanjungperak", "sea", 2.0, 0.04, "Singapore-Surabaya leg"),
    ("sea_tanjungperak", "sea_portklang", "sea", 2.5, 0.10, "Surabaya-Port Klang leg via Singapore Strait"),
    # --- South Asia ---
    ("sea_colombo", "sea_karachi", "sea", 2.5, 0.06, "Colombo-Karachi Arabian Sea leg"),
    ("sea_karachi", "sea_mundra", "sea", 0.8, 0.25, "Karachi-Mundra Gulf of Kutch leg"),
    ("sea_mundra", "sea_karachi", "sea", 0.8, 0.10, "Mundra-Karachi leg"),
    ("sea_karachi", "chk_hormuz", "sea", 1.8, 0.15, "Karachi to the Hormuz approaches"),
    ("sea_karachi", "sea_jebelali", "sea", 2.0, 0.12, "Karachi-Jebel Ali Gulf of Oman leg"),
    ("sea_colombo", "sea_chattogram", "sea", 3.0, 0.05, "Colombo-Chattogram Bay of Bengal leg"),
    ("sea_chattogram", "sea_colombo", "sea", 3.0, 0.20, "Chattogram-Colombo Bay of Bengal trunk"),
    ("sea_chattogram", "sea_portklang", "sea", 3.5, 0.18, "Chattogram-Port Klang leg via the Malacca approaches"),
    ("sea_portklang", "sea_chattogram", "sea", 3.5, 0.06, "Port Klang-Chattogram leg"),
    # --- Gulf ---
    ("sea_jebelali", "sea_khalifa", "sea", 0.5, 0.08, "Jebel Ali-Khalifa Port intra-UAE leg"),
    ("sea_khalifa", "sea_jebelali", "sea", 0.5, 0.20, "Khalifa Port-Jebel Ali leg"),
    ("sea_khalifa", "chk_hormuz", "sea", 0.8, 0.18, "Khalifa Port to the Hormuz exit"),
    ("sea_khalifa", "sea_dammam", "sea", 0.7, 0.12, "Khalifa Port-Dammam upper Gulf leg"),
    ("sea_dammam", "sea_khalifa", "sea", 0.7, 0.15, "Dammam-Khalifa Port upper Gulf leg"),
    ("sea_dammam", "sea_hamad", "sea", 0.6, 0.12, "Dammam-Hamad Port leg"),
    ("sea_hamad", "sea_dammam", "sea", 0.6, 0.15, "Hamad Port-Dammam leg"),
    ("sea_hamad", "sea_jebelali", "sea", 0.9, 0.20, "Hamad Port-Jebel Ali leg"),
    ("sea_jebelali", "sea_hamad", "sea", 0.9, 0.05, "Jebel Ali-Hamad Port leg"),
    ("chk_hormuz", "sea_dammam", "sea", 0.9, 0.08, "Hormuz exit into the upper Gulf to Dammam"),
    ("sea_dammam", "chk_hormuz", "sea", 0.9, 0.10, "Dammam to the Hormuz exit"),
    # --- Northern Europe / Baltic / Dover ---
    ("chk_danish", "sea_gdansk", "sea", 0.8, 0.20, "Danish Straits exit into the Baltic to Gdansk"),
    ("sea_gdansk", "chk_danish", "sea", 0.8, 0.15, "Gdansk to the Danish Straits"),
    ("sea_gdansk", "sea_gdynia", "sea", 0.3, 0.18, "Gdansk-Gdynia Gulf of Gdansk pair leg"),
    ("sea_gdynia", "sea_gdansk", "sea", 0.3, 0.22, "Gdynia-Gdansk pair leg"),
    ("sea_gdynia", "chk_danish", "sea", 0.9, 0.15, "Gdynia to the Danish Straits"),
    ("chk_danish", "sea_gdynia", "sea", 0.9, 0.08, "Danish Straits exit to Gdynia"),
    ("sea_gdansk", "sea_rotterdam", "sea", 2.2, 0.12, "Gdansk-Rotterdam Baltic/North Sea trunk"),
    ("sea_gdynia", "sea_hamburg", "sea", 1.2, 0.10, "Gdynia-Hamburg leg"),
    ("sea_hamburg", "sea_felixstowe", "sea", 1.0, 0.08, "Hamburg-Felixstowe North Sea leg"),
    ("sea_felixstowe", "sea_hamburg", "sea", 1.0, 0.10, "Felixstowe-Hamburg leg"),
    ("sea_rotterdam", "sea_felixstowe", "sea", 0.6, 0.10, "Rotterdam-Felixstowe short-sea leg"),
    ("sea_felixstowe", "sea_rotterdam", "sea", 0.6, 0.15, "Felixstowe-Rotterdam short-sea leg"),
    ("sea_felixstowe", "sea_lehavre", "sea", 0.8, 0.08, "Felixstowe-Le Havre Channel leg"),
    ("sea_lehavre", "sea_felixstowe", "sea", 0.8, 0.10, "Le Havre-Felixstowe Channel leg"),
    ("sea_lehavre", "sea_antwerp", "sea", 0.7, 0.12, "Le Havre-Antwerp coastal leg"),
    ("sea_antwerp", "sea_lehavre", "sea", 0.7, 0.08, "Antwerp-Le Havre coastal leg"),
    ("sea_felixstowe", "chk_dover", "sea", 0.4, 0.06, "Felixstowe into the Dover Strait TSS"),
    ("chk_dover", "sea_felixstowe", "sea", 0.4, 0.20, "Dover Strait exit to Felixstowe"),
    ("sea_lehavre", "chk_dover", "sea", 0.5, 0.10, "Le Havre into the Dover Strait TSS"),
    ("chk_dover", "sea_lehavre", "sea", 0.5, 0.18, "Dover Strait exit to Le Havre"),
    ("sea_rotterdam", "chk_dover", "sea", 0.6, 0.06, "Rotterdam into the Dover Strait TSS approaches"),
    ("sea_dublin", "sea_rotterdam", "sea", 1.5, 0.20, "Dublin-Rotterdam deep-sea leg"),
    ("sea_rotterdam", "sea_dublin", "sea", 1.5, 0.05, "Rotterdam-Dublin leg"),
    ("sea_dublin", "sea_lehavre", "sea", 1.2, 0.12, "Dublin-Le Havre Celtic Sea/Channel leg"),
    ("sea_lehavre", "sea_dublin", "sea", 1.2, 0.06, "Le Havre-Dublin leg"),
    ("sea_rotterdam", "sea_montreal", "sea", 8.0, 0.05, "Rotterdam-Montreal North Atlantic / St. Lawrence loop"),
    ("sea_rotterdam", "sea_buenosaires", "sea", 13.0, 0.04, "Rotterdam-Buenos Aires Europe/ECSA loop leg"),
    # --- Mediterranean / Black Sea ---
    ("chk_turkish", "sea_constanta", "sea", 1.2, 0.15, "Bosphorus exit into the Black Sea to Constanta"),
    ("sea_constanta", "chk_turkish", "sea", 1.2, 0.20, "Constanta to the Bosphorus"),
    ("sea_piraeus", "sea_constanta", "sea", 1.5, 0.08, "Piraeus-Constanta Aegean/Bosphorus leg"),
    ("sea_constanta", "sea_piraeus", "sea", 1.5, 0.15, "Constanta-Piraeus leg"),
    ("sea_piraeus", "sea_koper", "sea", 1.8, 0.06, "Piraeus-Koper Adriatic leg"),
    ("sea_gioiatauro", "sea_koper", "sea", 1.2, 0.15, "Gioia Tauro-Koper Adriatic leg"),
    ("sea_koper", "sea_gioiatauro", "sea", 1.2, 0.18, "Koper-Gioia Tauro leg"),
    ("sea_koper", "gnd_budapest", "ground", 1.0, 0.10, "Koper-Budapest rail corridor (North Adriatic gateway leg)"),
    ("gnd_budapest", "sea_koper", "ground", 1.0, 0.08, "Budapest-Koper rail corridor return"),
    ("sea_gioiatauro", "sea_marsaxlokk", "sea", 0.5, 0.15, "Gioia Tauro-Malta Freeport Sicily Channel leg"),
    ("sea_marsaxlokk", "sea_gioiatauro", "sea", 0.5, 0.20, "Malta Freeport-Gioia Tauro leg"),
    ("sea_marsaxlokk", "sea_portsaid", "sea", 2.0, 0.12, "Malta Freeport-Port Said eastern Med trunk"),
    ("sea_portsaid", "sea_marsaxlokk", "sea", 2.0, 0.10, "Port Said-Malta Freeport leg"),
    ("sea_gioiatauro", "sea_genoa", "sea", 1.2, 0.12, "Gioia Tauro-Genoa Tyrrhenian leg"),
    ("sea_genoa", "sea_barcelona", "sea", 1.2, 0.15, "Genoa-Barcelona Ligurian/Gulf of Lion leg"),
    ("sea_barcelona", "sea_genoa", "sea", 1.2, 0.12, "Barcelona-Genoa leg"),
    ("sea_barcelona", "sea_valencia", "sea", 0.7, 0.15, "Barcelona-Valencia Spanish Med leg"),
    ("sea_valencia", "sea_barcelona", "sea", 0.7, 0.10, "Valencia-Barcelona leg"),
    ("sea_genoa", "sea_marsaxlokk", "sea", 1.5, 0.10, "Genoa-Malta Freeport leg"),
    ("sea_barcelona", "sea_sines", "sea", 2.5, 0.08, "Barcelona-Sines leg via Gibraltar"),
    ("sea_sines", "sea_algeciras", "sea", 0.8, 0.20, "Sines-Algeciras leg"),
    ("sea_algeciras", "sea_sines", "sea", 0.8, 0.06, "Algeciras-Sines leg"),
    ("sea_sines", "sea_rotterdam", "sea", 3.5, 0.15, "Sines-Rotterdam Atlantic facade trunk"),
    ("sea_sines", "chk_gibraltar", "sea", 0.7, 0.10, "Sines into the Gibraltar approaches"),
    ("sea_ambarli", "chk_turkish", "sea", 0.3, 0.20, "Ambarli into the Bosphorus (Istanbul Strait)"),
    ("chk_turkish", "sea_ambarli", "sea", 0.3, 0.12, "Bosphorus exit to Ambarli (Istanbul)"),
    ("sea_ambarli", "sea_mersin", "sea", 1.5, 0.12, "Ambarli-Mersin Marmara/Aegean coastal leg"),
    ("sea_mersin", "sea_ambarli", "sea", 1.5, 0.10, "Mersin-Ambarli leg"),
    ("sea_mersin", "sea_beirut", "sea", 0.8, 0.12, "Mersin-Beirut Levant coastal leg"),
    ("sea_beirut", "sea_mersin", "sea", 0.8, 0.15, "Beirut-Mersin leg"),
    ("sea_piraeus", "sea_beirut", "sea", 1.5, 0.05, "Piraeus-Beirut eastern Med leg"),
    ("sea_beirut", "sea_portsaid", "sea", 1.2, 0.12, "Beirut-Port Said Levant leg"),
    ("sea_portsaid", "sea_beirut", "sea", 1.2, 0.08, "Port Said-Beirut leg"),
    ("sea_mersin", "sea_damietta", "sea", 1.0, 0.10, "Mersin-Damietta leg"),
    ("sea_damietta", "sea_portsaid", "sea", 0.5, 0.25, "Damietta-Port Said Nile Delta pair leg"),
    ("sea_portsaid", "sea_damietta", "sea", 0.5, 0.07, "Port Said-Damietta pair leg"),
    ("sea_damietta", "sea_piraeus", "sea", 1.8, 0.12, "Damietta-Piraeus eastern Med leg"),
    # --- Africa ---
    ("sea_durban", "chk_mozambique", "sea", 1.8, 0.15, "Durban into the Mozambique Channel"),
    ("chk_mozambique", "sea_durban", "sea", 1.8, 0.15, "Mozambique Channel south exit to Durban"),
    ("sea_mombasa", "chk_mozambique", "sea", 2.5, 0.12, "Mombasa into the Mozambique Channel"),
    ("chk_mozambique", "sea_mombasa", "sea", 2.5, 0.20, "Mozambique Channel north exit to Mombasa"),
    ("sea_colombo", "chk_mozambique", "sea", 4.5, 0.04, "Colombo to the Mozambique Channel (Indian Ocean crossing)"),
    ("sea_durban", "sea_capetown", "sea", 1.5, 0.12, "Durban-Cape Town Agulhas leg"),
    ("sea_capetown", "sea_durban", "sea", 1.5, 0.15, "Cape Town-Durban leg"),
    ("sea_capetown", "chk_capeofgoodhope", "sea", 0.5, 0.10, "Cape Town into the Cape route"),
    ("chk_capeofgoodhope", "sea_capetown", "sea", 0.5, 0.10, "Cape route exit to Cape Town"),
    ("sea_capetown", "gnd_citydeep", "ground", 2.0, 0.03, "Cape Town-Johannesburg (City Deep) rail leg"),
    ("sea_mombasa", "sea_durban", "sea", 4.0, 0.10, "Mombasa-Durban East Africa coastal trunk"),
    ("sea_durban", "sea_mombasa", "sea", 4.0, 0.08, "Durban-Mombasa coastal trunk"),
    # --- North America ---
    ("sea_losangeles", "sea_longbeach", "sea", 0.2, 0.15, "San Pedro Bay pair leg Los Angeles-Long Beach (the two ports share the bay; closes the LA-event replay gap noted in the scorecard)"),
    ("sea_longbeach", "sea_losangeles", "sea", 0.2, 0.15, "San Pedro Bay pair leg Long Beach-Los Angeles"),
    ("sea_oakland", "sea_losangeles", "sea", 1.0, 0.15, "Oakland-Los Angeles California coastal leg"),
    ("sea_losangeles", "sea_oakland", "sea", 1.0, 0.06, "Los Angeles-Oakland coastal leg"),
    ("sea_oakland", "sea_nwsa", "sea", 1.8, 0.12, "Oakland-Seattle/Tacoma (NWSA) coastal leg"),
    ("sea_nwsa", "sea_oakland", "sea", 1.8, 0.15, "NWSA-Oakland coastal leg"),
    ("sea_nwsa", "sea_vancouver", "sea", 0.5, 0.10, "NWSA-Vancouver Salish Sea pair leg"),
    ("sea_vancouver", "sea_nwsa", "sea", 0.5, 0.12, "Vancouver-NWSA pair leg"),
    ("sea_vancouver", "sea_princerupert", "sea", 1.2, 0.10, "Vancouver-Prince Rupert BC coastal leg"),
    ("sea_princerupert", "sea_vancouver", "sea", 1.2, 0.18, "Prince Rupert-Vancouver leg"),
    ("sea_princerupert", "sea_busan", "sea", 8.5, 0.15, "Prince Rupert-Busan transpacific leg"),
    ("sea_vancouver", "sea_shanghai", "sea", 11.0, 0.15, "Vancouver-Shanghai transpacific trunk"),
    ("sea_nwsa", "sea_busan", "sea", 9.5, 0.12, "NWSA-Busan transpacific leg"),
    ("sea_charleston", "sea_savannah", "sea", 0.5, 0.15, "Charleston-Savannah SE Atlantic pair leg"),
    ("sea_savannah", "sea_charleston", "sea", 0.5, 0.10, "Savannah-Charleston pair leg"),
    ("sea_charleston", "sea_virginia", "sea", 0.8, 0.12, "Charleston-Virginia (Norfolk) leg"),
    ("sea_virginia", "sea_charleston", "sea", 0.8, 0.10, "Virginia-Charleston leg"),
    ("sea_virginia", "sea_newyork", "sea", 1.0, 0.15, "Virginia-New York/NJ leg"),
    ("sea_newyork", "sea_virginia", "sea", 1.0, 0.06, "New York/NJ-Virginia leg"),
    ("sea_charleston", "sea_newyork", "sea", 1.2, 0.10, "Charleston-New York/NJ trunk"),
    ("sea_halifax", "sea_newyork", "sea", 1.5, 0.20, "Halifax-New York/NJ North Atlantic leg"),
    ("sea_newyork", "sea_halifax", "sea", 1.5, 0.05, "New York/NJ-Halifax leg"),
    ("sea_halifax", "sea_montreal", "sea", 1.5, 0.15, "Halifax-Montreal Gulf of St. Lawrence leg"),
    ("sea_montreal", "sea_halifax", "sea", 1.5, 0.18, "Montreal-Halifax St. Lawrence exit leg"),
    ("sea_montreal", "gnd_montreal", "ground", 0.2, 0.10, "Port of Montreal to the Montreal ground/intermodal node (port-gate leg)"),
    ("sea_vancouver", "gnd_vancouver", "ground", 0.2, 0.10, "Port of Vancouver to the Vancouver ground/intermodal node (port-gate leg)"),
    # --- Latin America / Panama ---
    ("sea_longbeach", "sea_manzanillo", "sea", 4.0, 0.05, "Long Beach-Manzanillo (MX) west-coast Americas leg"),
    ("sea_manzanillo", "sea_balboa", "sea", 4.5, 0.12, "Manzanillo-Balboa leg"),
    ("sea_balboa", "sea_manzanillo", "sea", 4.5, 0.10, "Balboa-Manzanillo leg"),
    ("sea_balboa", "chk_panama", "sea", 0.3, 0.25, "Balboa into the Panama Canal Pacific entrance"),
    ("chk_panama", "sea_balboa", "sea", 0.3, 0.04, "Panama Canal Pacific exit to Balboa"),
    ("sea_manzanillo", "chk_panama", "sea", 3.5, 0.10, "Manzanillo to the Panama Pacific entrance"),
    ("sea_balboa", "sea_callao", "sea", 3.5, 0.15, "Balboa-Callao west-coast South America leg"),
    ("sea_callao", "sea_balboa", "sea", 3.5, 0.12, "Callao-Balboa leg"),
    ("sea_callao", "sea_guayaquil", "sea", 1.8, 0.15, "Callao-Guayaquil leg"),
    ("sea_guayaquil", "sea_callao", "sea", 1.8, 0.18, "Guayaquil-Callao leg"),
    ("sea_guayaquil", "sea_balboa", "sea", 2.5, 0.12, "Guayaquil-Balboa leg"),
    ("sea_balboa", "sea_guayaquil", "sea", 2.5, 0.10, "Balboa-Guayaquil leg"),
    ("sea_balboa", "sea_sanantonio", "sea", 6.5, 0.06, "Balboa-San Antonio (CL) long-haul WCSA leg"),
    ("sea_colon", "sea_cartagena", "sea", 0.8, 0.15, "Colon-Cartagena Caribbean pair leg"),
    ("sea_cartagena", "sea_colon", "sea", 0.8, 0.18, "Cartagena-Colon pair leg"),
    ("sea_cartagena", "chk_panama", "sea", 1.0, 0.15, "Cartagena to the Panama Atlantic entrance"),
    ("sea_cartagena", "sea_santos", "sea", 6.0, 0.10, "Cartagena-Santos Caribbean/ECSA leg"),
    ("sea_santos", "sea_cartagena", "sea", 6.0, 0.06, "Santos-Cartagena leg"),
    ("sea_sanantonio", "sea_callao", "sea", 3.0, 0.15, "San Antonio-Callao WCSA leg"),
    ("sea_callao", "sea_sanantonio", "sea", 3.0, 0.12, "Callao-San Antonio leg"),
    ("sea_buenosaires", "sea_santos", "sea", 3.5, 0.20, "Buenos Aires-Santos Rio de la Plata/ECSA leg"),
    ("sea_santos", "sea_buenosaires", "sea", 3.5, 0.08, "Santos-Buenos Aires leg"),
    # --- Oceania ---
    ("sea_tanjungpelepas", "sea_melbourne", "sea", 7.5, 0.05, "Tanjung Pelepas-Melbourne leg via the Singapore/Australia loop"),
    ("sea_melbourne", "sea_singapore", "sea", 7.0, 0.10, "Melbourne-Singapore trunk"),
    ("sea_melbourne", "sea_sydney", "sea", 1.2, 0.15, "Melbourne-Sydney (Port Botany) coastal leg"),
    ("sea_sydney", "sea_melbourne", "sea", 1.2, 0.18, "Sydney-Melbourne coastal leg"),
    ("sea_sydney", "sea_brisbane", "sea", 1.0, 0.15, "Sydney-Brisbane coastal leg"),
    ("sea_brisbane", "sea_sydney", "sea", 1.0, 0.20, "Brisbane-Sydney coastal leg"),
    ("sea_melbourne", "sea_brisbane", "sea", 2.0, 0.08, "Melbourne-Brisbane coastal leg"),
    ("sea_brisbane", "sea_melbourne", "sea", 2.0, 0.10, "Brisbane-Melbourne coastal leg"),
    # --- Air (MODELED allocations, lane basis in each note) ---
    ("air_newyork", "air_london", "air", 0.35, 0.15, "JFK-LHR transatlantic cargo lane"),
    ("air_london", "air_newyork", "air", 0.35, 0.12, "LHR-JFK transatlantic cargo lane"),
    ("air_newyork", "air_frankfurt", "air", 0.35, 0.12, "JFK-FRA transatlantic cargo lane"),
    ("air_frankfurt", "air_newyork", "air", 0.35, 0.10, "FRA-JFK lane"),
    ("air_newyork", "air_chicago", "air", 0.1, 0.10, "JFK-ORD domestic trunk"),
    ("air_chicago", "air_newyork", "air", 0.1, 0.12, "ORD-JFK domestic trunk"),
    ("air_atlanta", "air_chicago", "air", 0.1, 0.15, "ATL-ORD domestic trunk"),
    ("air_chicago", "air_atlanta", "air", 0.1, 0.10, "ORD-ATL domestic trunk"),
    ("air_atlanta", "air_frankfurt", "air", 0.4, 0.12, "ATL-FRA transatlantic cargo lane (Lufthansa Cargo)"),
    ("air_memphis", "air_atlanta", "air", 0.1, 0.08, "MEM-ATL FedEx/domestic leg"),
    ("air_houston", "air_memphis", "air", 0.1, 0.15, "IAH-MEM domestic leg"),
    ("air_memphis", "air_houston", "air", 0.1, 0.08, "MEM-IAH domestic leg"),
    ("air_houston", "air_frankfurt", "air", 0.45, 0.12, "IAH-FRA transatlantic cargo lane"),
    ("air_frankfurt", "air_houston", "air", 0.45, 0.05, "FRA-IAH lane"),
    ("air_toronto", "air_chicago", "air", 0.1, 0.18, "YYZ-ORD cross-border trunk"),
    ("air_chicago", "air_toronto", "air", 0.1, 0.08, "ORD-YYZ cross-border trunk"),
    ("air_toronto", "air_london", "air", 0.35, 0.12, "YYZ-LHR transatlantic lane"),
    ("air_london", "air_toronto", "air", 0.35, 0.06, "LHR-YYZ lane"),
    ("air_leipzig", "air_frankfurt", "air", 0.05, 0.20, "LEJ-FRA intra-German leg (DHL network)"),
    ("air_frankfurt", "air_leipzig", "air", 0.05, 0.08, "FRA-LEJ leg"),
    ("air_leipzig", "air_liege", "air", 0.05, 0.15, "LEJ-LGG intra-European express leg"),
    ("air_liege", "air_leipzig", "air", 0.05, 0.12, "LGG-LEJ leg"),
    ("air_liege", "air_paris", "air", 0.05, 0.15, "LGG-CDG leg (FedEx/TNT network)"),
    ("air_paris", "air_liege", "air", 0.05, 0.08, "CDG-LGG leg"),
    ("air_liege", "air_frankfurt", "air", 0.05, 0.12, "LGG-FRA leg"),
    ("air_liege", "air_brussels", "air", 0.05, 0.10, "LGG-BRU intra-Belgian leg"),
    ("air_frankfurt", "air_brussels", "air", 0.05, 0.05, "FRA-BRU leg"),
    ("air_brussels", "air_frankfurt", "air", 0.05, 0.15, "BRU-FRA leg"),
    ("air_brussels", "air_singapore", "air", 0.55, 0.10, "BRU-SIN lane (Singapore Airlines Cargo)"),
    ("air_singapore", "air_brussels", "air", 0.55, 0.06, "SIN-BRU lane"),
    ("air_cologne", "air_frankfurt", "air", 0.05, 0.18, "CGN-FRA intra-German leg"),
    ("air_frankfurt", "air_cologne", "air", 0.05, 0.06, "FRA-CGN leg"),
    ("air_cologne", "air_louisville", "air", 0.4, 0.15, "CGN-SDF UPS transatlantic lane"),
    ("air_louisville", "air_cologne", "air", 0.4, 0.10, "SDF-CGN UPS lane"),
    ("air_madrid", "air_frankfurt", "air", 0.15, 0.15, "MAD-FRA European trunk"),
    ("air_frankfurt", "air_madrid", "air", 0.15, 0.06, "FRA-MAD trunk"),
    ("air_madrid", "air_doha", "air", 0.3, 0.10, "MAD-DOH lane (Qatar Airways Cargo)"),
    ("gnd_madrid", "air_madrid", "ground", 0.1, 0.10, "Madrid ground node to Madrid Barajas air hub (airport-gate leg)"),
    ("air_milan", "air_frankfurt", "air", 0.1, 0.18, "MXP-FRA European trunk"),
    ("air_frankfurt", "air_milan", "air", 0.1, 0.05, "FRA-MXP trunk"),
    ("gnd_milan", "air_milan", "ground", 0.1, 0.10, "Milan ground node to Milan Malpensa air hub (airport-gate leg)"),
    ("air_zhengzhou", "air_anchorage", "air", 0.45, 0.18, "CGO-ANC transpacific tech-stop lane"),
    ("air_incheon", "air_zhengzhou", "air", 0.15, 0.12, "ICN-CGO lane"),
    ("air_hongkong", "air_zhengzhou", "air", 0.15, 0.05, "HKG-CGO lane"),
    ("air_zhengzhou", "air_frankfurt", "air", 0.5, 0.15, "CGO-FRA lane (Cargolux Zhengzhou hub pattern)"),
    ("air_ezhou", "air_anchorage", "air", 0.5, 0.15, "EHU-ANC lane (SF Airlines Ezhou-US services)"),
    ("air_anchorage", "air_ezhou", "air", 0.5, 0.05, "ANC-EHU return lane"),
    ("air_ezhou", "air_delhi", "air", 0.25, 0.10, "EHU-DEL lane (SF international network)"),
    ("air_delhi", "air_ezhou", "air", 0.25, 0.06, "DEL-EHU lane"),
    ("air_ezhou", "air_liege", "air", 0.5, 0.08, "EHU-LGG e-commerce lane (SF/Cainiao pattern)"),
    ("air_osaka", "air_incheon", "air", 0.1, 0.18, "KIX-ICN lane"),
    ("air_incheon", "air_osaka", "air", 0.1, 0.08, "ICN-KIX lane"),
    ("air_osaka", "air_anchorage", "air", 0.4, 0.12, "KIX-ANC transpacific lane"),
    ("air_osaka", "air_narita", "air", 0.05, 0.15, "KIX-NRT domestic trunk"),
    ("air_narita", "air_osaka", "air", 0.05, 0.10, "NRT-KIX domestic trunk"),
    # --- Ground (MODELED allocations, corridor basis in each note) ---
    ("gnd_laredo", "gnd_hidalgo", "ground", 0.3, 0.10, "Laredo-Hidalgo Rio Grande Valley corridor (US-83)"),
    ("gnd_hidalgo", "gnd_laredo", "ground", 0.3, 0.15, "Hidalgo-Laredo corridor"),
    ("gnd_hidalgo", "gnd_dallas", "ground", 0.7, 0.20, "Hidalgo-Dallas I-35 corridor leg"),
    ("gnd_dallas", "gnd_hidalgo", "ground", 0.7, 0.08, "Dallas-Hidalgo I-35 leg"),
    ("gnd_laredo", "gnd_eaglepass", "ground", 0.4, 0.07, "Laredo-Eagle Pass border corridor"),
    ("gnd_dallas", "gnd_eaglepass", "ground", 0.6, 0.06, "Dallas-Eagle Pass leg (US-57)"),
    ("gnd_eaglepass", "gnd_dallas", "ground", 0.6, 0.20, "Eagle Pass-Dallas corridor"),
    ("gnd_eaglepass", "gnd_laredo", "ground", 0.4, 0.15, "Eagle Pass-Laredo corridor"),
    ("gnd_otaymesa", "gnd_calexico", "ground", 0.3, 0.15, "Otay Mesa-Calexico East border corridor (I-8)"),
    ("gnd_calexico", "gnd_inlandempire", "ground", 0.4, 0.20, "Calexico East-Inland Empire leg"),
    ("gnd_inlandempire", "gnd_calexico", "ground", 0.4, 0.08, "Inland Empire-Calexico leg"),
    ("gnd_toronto", "gnd_sweetgrass", "ground", 1.8, 0.12, "Toronto-Sweetgrass trans-Canada corridor leg (long-haul; CPKC corridor)"),
    ("gnd_sweetgrass", "gnd_chicago", "ground", 1.5, 0.15, "Sweetgrass-Chicago corridor (I-15/I-90)"),
    ("gnd_chicago", "gnd_sweetgrass", "ground", 1.5, 0.06, "Chicago-Sweetgrass corridor"),
    ("gnd_montreal", "gnd_alexandriabay", "ground", 0.5, 0.15, "Montreal-Alexandria Bay leg (A-401/I-81, Thousand Islands)"),
    ("gnd_alexandriabay", "gnd_buffalo", "ground", 0.6, 0.18, "Alexandria Bay-Buffalo leg (I-81/I-90)"),
    ("gnd_buffalo", "gnd_alexandriabay", "ground", 0.6, 0.08, "Buffalo-Alexandria Bay leg"),
    ("sea_charleston", "gnd_greer", "ground", 1.0, 0.10, "Charleston-Inland Port Greer rail shuttle (SC Ports inland port leg)"),
    ("gnd_greer", "gnd_atlanta", "ground", 0.4, 0.20, "Greer-Atlanta I-85 leg"),
    ("gnd_atlanta", "gnd_greer", "ground", 0.4, 0.10, "Atlanta-Greer I-85 leg"),
    ("sea_virginia", "gnd_frontroyal", "ground", 0.5, 0.12, "Port of Virginia-Virginia Inland Port rail shuttle (NS, Front Royal)"),
    ("gnd_frontroyal", "gnd_columbus", "ground", 0.7, 0.15, "Front Royal-Columbus leg (I-81/I-70)"),
    ("gnd_columbus", "gnd_frontroyal", "ground", 0.7, 0.12, "Columbus-Front Royal leg"),
    ("sea_oakland", "gnd_stockton", "ground", 0.3, 0.15, "Port of Oakland-Stockton Intermodal leg (BNSF)"),
    ("gnd_stockton", "gnd_chicago", "ground", 2.0, 0.12, "Stockton-Chicago transcontinental intermodal leg (BNSF)"),
    ("gnd_inlandempire", "gnd_stockton", "ground", 0.6, 0.07, "Inland Empire-Stockton California leg (I-5)"),
    ("sea_losangeles", "gnd_saltlakecity", "ground", 0.8, 0.05, "Los Angeles-Salt Lake City ground leg (I-15; Utah Inland Port)"),
    ("sea_oakland", "gnd_saltlakecity", "ground", 0.9, 0.06, "Oakland-Salt Lake City ground leg (I-80)"),
    ("gnd_saltlakecity", "gnd_chicago", "ground", 1.5, 0.15, "Salt Lake City-Chicago intermodal leg (UP)"),
    ("gnd_chicago", "gnd_stlouis", "ground", 0.5, 0.12, "Chicago-St. Louis leg (I-55)"),
    ("gnd_stlouis", "gnd_chicago", "ground", 0.5, 0.15, "St. Louis-Chicago leg"),
    ("gnd_stlouis", "gnd_kansascity", "ground", 0.4, 0.15, "St. Louis-Kansas City leg (I-70)"),
    ("gnd_kansascity", "gnd_stlouis", "ground", 0.4, 0.12, "Kansas City-St. Louis leg"),
    ("gnd_memphis", "gnd_stlouis", "ground", 0.5, 0.10, "Memphis-St. Louis leg (I-55)"),
]

TRANSIT_BASIS = ("MODELED - the fetched PortWatch services (ports registry "
                 "vessel counts; disruptions view event dates) carry no "
                 "transit-time field, so base_transit_d remains a MODELED "
                 "point value inside published typical lane ranges")
REASON_CHOKE_ORIGIN = ("MODELED allocation - PortWatch join not applicable: "
                       "chokepoint-exit transit leg: chokepoint-exit legs "
                       "are transit flows, not port calls, so the "
                       "call-attraction mechanism is not applicable to "
                       "transit flows (vessels transit the chokepoint past "
                       "the destination without calling there, and the "
                       "destination's container call count does not measure "
                       "this leg's flow); the MODELED share is retained "
                       "pending a transit-distribution source")
REASON_CHOKE_DEST = ("MODELED allocation - PortWatch join not applicable: "
                     "destination is a chokepoint and the pinned PortWatch "
                     "snapshot carries no chokepoint transit series (the fetched "
                     "services record port activity and disruptions only)")
REASON_DEST_UNCOVERED = ("MODELED allocation - PortWatch join not applicable: "
                         "destination port has no port_activity record in the "
                         "pinned PortWatch join input (the input's records "
                         "cover a subset of graph ports; see calibration.md)")
REASON_SINGLE_COVERED = ("MODELED allocation - PortWatch join not applicable: "
                         "fewer than 2 of this origin's sea destinations are "
                         "covered by the pinned PortWatch snapshot, so no "
                         "sourced distribution can be derived")
REASON_COMTRADE = ("MODELED allocation - UN Comtrade 2024 join pending: "
                   "bilateral flows require UN_COMTRADE_API_KEY (ready-to-run "
                   "overlay builder: data/graph/build/comtrade_overlay.py); "
                   "PortWatch carries no air/ground flow data")


def load_portwatch_counts():
    """Return (container_count_by_node, retrieved_at_by_node,
    doc_retrieved_at, input_sha256). retrieved_at_by_node carries each
    record's own retrieved_at where the record has one (the expansion
    records do; the original adapter-snapshot records do not and
    fall back to the document-level retrieved_at at the call site)."""
    raw = PORTWATCH_INPUT.read_bytes()
    doc = json.loads(raw.decode("utf-8"))
    counts, retrieved_by_node = {}, {}
    for r in doc["records"]:
        c = r.get("vessel_count_container")
        if isinstance(c, int) and c > 0:
            counts[r["node_id"]] = counts.get(r["node_id"], 0) + c
            if r.get("retrieved_at"):
                retrieved_by_node[r["node_id"]] = max(
                    retrieved_by_node.get(r["node_id"], ""), r["retrieved_at"])
    return counts, retrieved_by_node, doc["retrieved_at"], hashlib.sha256(raw).hexdigest()


def load_portwatch_total_counts():
    """Total (all vessel types) PortWatch counts by node id from the same
    pinned join input — used only as attraction weights for SHELL hub
    links (construction rule S1), never for CORE shares."""
    doc = json.loads(PORTWATCH_INPUT.read_text(encoding="utf-8"))
    totals = {}
    for r in doc["records"]:
        t = r.get("vessel_count_total")
        if isinstance(t, int) and t > 0:
            totals[r["node_id"]] = totals.get(r["node_id"], 0) + t
    return totals


def _apportion_1e4(total_units, weights):
    """Largest-remainder apportionment of total_units over [(key, weight)],
    weights pre-sorted by key; returns {key: units}. Deterministic."""
    total_w = sum(w for _, w in weights)
    raw = {k: total_units * w / total_w for k, w in weights}
    units = {k: math.floor(v) for k, v in raw.items()}
    remainder = total_units - sum(units.values())
    by_remainder = sorted(weights, key=lambda kw: (-(raw[kw[0]] - units[kw[0]]), kw[0]))
    for k, _ in by_remainder[:remainder]:
        units[k] += 1
    return units


def portwatch_join(edges, node_by_id, counts):
    """Re-derive supported sea capacity_share values in place.
    Returns a stats dict for calibration.md."""
    sea_by_origin = {}
    for e in edges:
        if e["mode"] == "sea":
            sea_by_origin.setdefault(e["from"], []).append(e)
    stats = {"sourced": [], "modeled_reasons": {}, "origins": []}
    for origin in sorted(sea_by_origin):
        if node_by_id[origin]["kind"] == "chokepoint":
            # Chokepoint origins excluded —
            # chokepoint-exit legs are transit flows, not port calls, so the
            # destination call-attraction formula does not apply to them.
            # Their edges keep the pre-join base MODELED shares untouched.
            continue
        out = sorted(sea_by_origin[origin], key=lambda e: (e["to"], e["mode"]))
        covered = [e for e in out if e["to"] in counts]
        if len(covered) < 2:
            continue
        s_cov = round(sum(e["capacity_share"] for e in covered), 4)
        total_units = int(round(s_cov * 10000))
        weights = [(f'{e["from"]}->{e["to"]}:{e["mode"]}', counts[e["to"]])
                   for e in covered]
        units = _apportion_1e4(total_units, weights)
        cov_total = sum(counts[e["to"]] for e in covered)
        for e in covered:
            key = f'{e["from"]}->{e["to"]}:{e["mode"]}'
            e["capacity_share"] = units[key] / 10000
            e["_pw_inputs"] = {"covered_container_calls_total": cov_total,
                               "covered_modeled_subtotal": s_cov,
                               "vessel_count_container_to": counts[e["to"]]}
            stats["sourced"].append(key)
        stats["origins"].append(origin)
    sourced = set(stats["sourced"])
    for e in edges:
        key = f'{e["from"]}->{e["to"]}:{e["mode"]}'
        if key in sourced or e["mode"] != "sea":
            continue
        if node_by_id[e["from"]]["kind"] == "chokepoint":
            reason = "chokepoint_origin"
        elif node_by_id[e["to"]]["kind"] == "chokepoint":
            reason = "chokepoint_destination"
        elif e["to"] not in counts:
            reason = "destination_uncovered"
        else:
            reason = "single_covered_origin"
        stats["modeled_reasons"][key] = reason
    # Per-tier accounting (two-tier): the CORE
    # figures are computed over CORE<->CORE sea edges only, so the
    # calibration claims of the 200-node build stay exactly reproducible
    # on the tiered graph.
    def _is_core(nid):
        return node_by_id[nid].get("tier", "core") == "core"

    stats["core"] = {
        "sourced": [k for k in stats["sourced"]
                    if _is_core(k.split("->")[0])
                    and _is_core(k.split("->")[1].rsplit(":", 1)[0])],
        "modeled_reasons": {k: r for k, r in stats["modeled_reasons"].items()
                            if _is_core(k.split("->")[0])
                            and _is_core(k.split("->")[1].rsplit(":", 1)[0])},
        "origins": [o for o in stats["origins"] if _is_core(o)],
    }
    return stats


# ---------------------------------------------------------------------------
# SHELL edge construction (two-tier). Generated
# deterministically from node geometry + registry activity — never
# hand-listed. The rules (also printed in calibration.md and in the
# graph's metadata.shell_construction):
#   S1 sea hub link : shell port -> 2 nearest CORE ports; MODELED share
#       allocation inside the shell origin's 0.60 sea outbound budget,
#       weighted by destination observed vessel calls (IMF PortWatch
#       registry totals; shell destinations use their registry totals,
#       core destinations their pinned join-input totals, capacity as
#       fallback). The PortWatch join may still re-derive a shell->core
#       leg as SOURCED where the pinned CORE snapshot covers the
#       destination (observed-call evidence) — classified per edge.
#   S2 sea peer link: bidirectional, up to 2 nearest shell ports within
#       1,500 km (if none in range, the single nearest shell port), so
#       every shell port has at least one shell-peer pair.
#   S3 reverse link : CORE -> shell reverse of a shell hub link, share =
#       min(0.03, half the core origin's remaining outbound budget for
#       that mode), only when the remaining budget is >= 0.01, at most
#       3 reverse links per core node per mode.
#   S4 air hub link : shell air hub -> 2 nearest CORE air hubs; MODELED
#       equal-weight allocation inside a 0.50 air outbound budget.
#   S5 air peer link: bidirectional, up to 2 nearest shell air hubs
#       within 3,000 km (escalation as S2); equal weights.
#   S6 ground links : shell ground node -> nearest CORE ground node and
#       nearest CORE port (ground mode), plus 1 nearest shell ground
#       peer within 800 km (escalation as S2); S3 reverses apply;
#       MODELED equal-weight allocation inside a 0.50 ground budget.
# Transit times on generated legs are MODELED point values from
# great-circle distance at a stated service speed (sea 20 kn = 889
# km/day, floor 0.3 d; air 800 km/h = 19,200 km/day, floor 0.2 d;
# ground 55 km/h = 1,320 km/day, floor 0.3 d) — the same MODELED status
# transit carries on every edge of this graph.
# ---------------------------------------------------------------------------
SHELL_RULES = {
    "S1": "SHELL construction S1: shell port -> nearest CORE ports (2, great-circle); MODELED allocation within the shell origin's 0.60 sea outbound budget, weighted by destination observed vessel calls (IMF PortWatch registry).",
    "S2": "SHELL construction S2: bidirectional shell-port peer link — up to 2 nearest shell ports within 1,500 km (escalated to the single nearest shell port when none is in range); MODELED allocation weighted by destination observed vessel calls.",
    "S3": "SHELL construction S3: CORE -> shell reverse of a shell-origin hub link; share capped at 0.03 and at half the core origin's remaining outbound budget for the mode (only when >= 0.01 remains), at most 3 per core node per mode; MODELED.",
    "S4": "SHELL construction S4: shell air hub -> nearest CORE air hubs (2, great-circle); MODELED equal-weight allocation within the shell origin's 0.50 air outbound budget.",
    "S5": "SHELL construction S5: bidirectional shell air peer link — up to 2 nearest shell air hubs within 3,000 km (escalation as S2); MODELED equal-weight allocation.",
    "S6": "SHELL construction S6: shell ground node -> nearest CORE ground node and nearest CORE port (ground mode), plus 1 nearest shell ground peer within 800 km (escalation as S2); MODELED equal-weight allocation within the shell origin's 0.50 ground outbound budget.",
}
SHELL_NOTE = {"S1": "Shell hub link (construction S1)",
              "S2": "Shell peer link (construction S2)",
              "S3": "Shell reverse link (construction S3)",
              "S4": "Shell air hub link (construction S4)",
              "S5": "Shell air peer link (construction S5)",
              "S6": "Shell ground link (construction S6)"}


def _haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def _nearest(node, cands, k, max_km=None):
    ds = sorted((_haversine_km(node["lat"], node["lon"], c["lat"], c["lon"]),
                 c["id"]) for c in cands)
    if max_km is not None:
        ds = [d for d in ds if d[0] <= max_km]
    return ds[:k]


def build_shell_edges(node_by_id, shell_activity, core_totals, origin_totals):
    """Generate the SHELL construction edges. Returns (pairs, origin_totals)
    where pairs are (edge, note) tuples in build_edges' shape and
    origin_totals is updated with the S3 reverse shares consumed."""
    nodes = list(node_by_id.values())
    core = [n for n in nodes if n["tier"] == "core"]
    shell = [n for n in nodes if n["tier"] == "shell"]
    by_id = node_by_id
    totals = dict(origin_totals)
    seen = set()
    out_pairs = []

    def weight_of(dest_id):
        d = by_id[dest_id]
        if d["tier"] == "shell":
            return (shell_activity.get(dest_id) or {}).get(
                "vessel_count_total") or 1
        return core_totals.get(dest_id) or d["capacity"]

    reverse_used = {}

    def add_reverse(core_node, shell_node, mode, transit):
        key = (core_node["id"], mode)
        if reverse_used.get(key, 0) >= 3:
            return
        leftover = 1.0 - totals.get(key, 0.0)
        if leftover < 0.01:
            return
        share = round(min(0.03, leftover / 2), 4)
        if share <= 0:
            return
        ekey = (core_node["id"], shell_node["id"], mode)
        if ekey in seen:
            return
        seen.add(ekey)
        e = {"from": core_node["id"], "to": shell_node["id"], "mode": mode,
             "base_transit_d": transit, "capacity_share": share,
             "_method": SHELL_RULES["S3"]}
        out_pairs.append((e, SHELL_NOTE["S3"]))
        totals[key] = totals.get(key, 0.0) + share
        reverse_used[key] = reverse_used.get(key, 0) + 1

    def emit_class(origins, hub_cands, peer_pool, peer_k, peer_radius,
                   budget_units, mode, transit_fn, hub_rule, peer_rule,
                   weighted):
        out_links = {o["id"]: [] for o in origins}   # id -> [(dest, transit, rule)]
        for o in origins:
            others = [c for c in peer_pool if c["id"] != o["id"]]
            peers = _nearest(o, others, peer_k, peer_radius)
            if not peers:
                peers = _nearest(o, others, 1)
            for dist, hid in _nearest(o, hub_cands, 2):
                out_links[o["id"]].append((hid, transit_fn(dist), hub_rule))
            for dist, qid in peers:
                out_links[o["id"]].append((qid, transit_fn(dist), peer_rule))
        # Peer symmetry: a selected peer link exists in both directions.
        for o in origins:
            for dest, transit, rule in list(out_links[o["id"]]):
                if rule == peer_rule and o["id"] not in \
                        [d for d, _, _ in out_links[dest]]:
                    out_links[dest].append((o["id"], transit, peer_rule))
        for o in origins:
            links = out_links[o["id"]]
            if not links:
                continue
            weights = [(dest, weight_of(dest) if weighted else 1)
                       for dest, _, _ in sorted(links)]
            units = _apportion_1e4(budget_units, weights)
            for dest, transit, rule in links:
                ekey = (o["id"], dest, mode)
                if ekey in seen:
                    continue
                seen.add(ekey)
                e = {"from": o["id"], "to": dest, "mode": mode,
                     "base_transit_d": transit,
                     "capacity_share": units[dest] / 10000,
                     "_method": SHELL_RULES[rule]}
                out_pairs.append((e, SHELL_NOTE[rule]))
                if by_id[dest]["tier"] == "core" and rule == hub_rule:
                    add_reverse(by_id[dest], o, mode, transit)

    def sea_transit(d):
        return round(max(0.3, d / 889.0), 1)

    def air_transit(d):
        return round(max(0.2, d / 19200.0), 1)

    def ground_transit(d):
        return round(max(0.3, d / 1320.0), 1)

    core_ports = [n for n in core if n["kind"] == "port"]
    shell_ports = [n for n in shell if n["kind"] == "port"]
    emit_class(shell_ports, core_ports, shell_ports, 2, 1500.0, 6000,
               "sea", sea_transit, "S1", "S2", weighted=True)
    core_air = [n for n in core if n["kind"] == "air_hub"]
    shell_air = [n for n in shell if n["kind"] == "air_hub"]
    emit_class(shell_air, core_air, shell_air, 2, 3000.0, 5000,
               "air", air_transit, "S4", "S5", weighted=False)
    core_ground = [n for n in core if n["kind"] == "ground_node"]
    shell_ground = [n for n in shell if n["kind"] == "ground_node"]
    # Ground hub candidates are the nearest CORE ground node AND the
    # nearest CORE port — emit_class takes one hub set, so ground runs
    # with the union set and a hub count of 2 reproduced by construction:
    # the union's 2 nearest could be two ground nodes, so instead the
    # ground class is emitted with explicit hub picks:
    out_links = {o["id"]: [] for o in shell_ground}
    for o in shell_ground:
        others = [c for c in shell_ground if c["id"] != o["id"]]
        hubs = _nearest(o, core_ground, 1) + _nearest(o, core_ports, 1)
        peers = _nearest(o, others, 1, 800.0) or _nearest(o, others, 1)
        for dist, hid in hubs:
            out_links[o["id"]].append((hid, ground_transit(dist), "S6"))
        for dist, qid in peers:
            out_links[o["id"]].append((qid, ground_transit(dist), "S6P"))
    for o in shell_ground:
        for dest, transit, rule in list(out_links[o["id"]]):
            if rule == "S6P" and o["id"] not in \
                    [d for d, _, _ in out_links[dest]]:
                out_links[dest].append((o["id"], transit, "S6P"))
    for o in shell_ground:
        links = out_links[o["id"]]
        if not links:
            continue
        units = _apportion_1e4(5000, [(dest, 1)
                                      for dest, _, _ in sorted(links)])
        for dest, transit, rule in links:
            ekey = (o["id"], dest, "ground")
            if ekey in seen:
                continue
            seen.add(ekey)
            e = {"from": o["id"], "to": dest, "mode": "ground",
                 "base_transit_d": transit,
                 "capacity_share": units[dest] / 10000,
                 "_method": SHELL_RULES["S6"]}
            out_pairs.append((e, SHELL_NOTE["S6"]))
            if by_id[dest]["tier"] == "core" and rule == "S6":
                add_reverse(by_id[dest], o, "ground", transit)
    return out_pairs, totals


def build_edges(node_by_id, counts, retrieved_by_node, default_retrieved_at,
                shell_activity=None, core_totals=None):
    edges = []
    for row in RAW_EDGES + RAW_EDGES_EXPANSION:
        frm, to, mode, days, share, note = row[:6]
        bypass = row[6] if len(row) > 6 else None
        e = {"from": frm, "to": to, "mode": mode, "base_transit_d": days,
             "capacity_share": share}
        if bypass:
            e["bypass_of"] = bypass
        edges.append((e, note))
    if shell_activity is not None:
        origin_totals = {}
        for e, _ in edges:
            key = (e["from"], e["mode"])
            origin_totals[key] = origin_totals.get(key, 0.0) + \
                e["capacity_share"]
        generated, _ = build_shell_edges(node_by_id, shell_activity,
                                         core_totals or {}, origin_totals)
        edges = edges + generated
    edges.sort(key=lambda en: (en[0]["from"], en[0]["to"], en[0]["mode"]))
    flat = [e for e, _ in edges]
    stats = portwatch_join(flat, node_by_id, counts)
    sourced = set(stats["sourced"])
    reason_text = {"chokepoint_origin": REASON_CHOKE_ORIGIN,
                   "chokepoint_destination": REASON_CHOKE_DEST,
                   "destination_uncovered": REASON_DEST_UNCOVERED,
                   "single_covered_origin": REASON_SINGLE_COVERED}
    for e, _note in edges:
        key = f'{e["from"]}->{e["to"]}:{e["mode"]}'
        if e["mode"] == "sea":
            calib = {"volume_source": "IMF PortWatch"}
            kinds = {node_by_id[e["from"]]["kind"], node_by_id[e["to"]]["kind"]}
            calib["efficiency_source"] = "World Bank CPPI 2024" if kinds == {"port"} else "UNCTAD LSCI 2024"
        else:
            calib = {"volume_source": "UN Comtrade 2024"}
        calib["transit_basis"] = TRANSIT_BASIS
        pw_inputs = e.pop("_pw_inputs", None)
        custom_method = e.pop("_method", None)
        if key in sourced:
            calib["basis"] = "SOURCED"
            calib["share_source"] = PW_SHARE_SOURCE
            # The retrieved_at of the DESTINATION's pinned record (the
            # expansion records carry their own extract date); destinations
            # from the original adapter extract fall back to its date.
            calib["share_retrieved_at"] = retrieved_by_node.get(
                e["to"], default_retrieved_at)
            calib["share_method"] = PW_SHARE_METHOD
            calib["share_inputs"] = pw_inputs
        elif custom_method:
            # SHELL construction edge (two-tier): the rule text
            # is the method; for sea legs the join's MODELED reason is
            # appended so the record also says why the join did not
            # re-derive this leg.
            calib["basis"] = "MODELED"
            if e["mode"] == "sea":
                calib["share_method"] = custom_method + \
                    " Not join-derived on this leg: " + \
                    reason_text[stats["modeled_reasons"][key]]
            else:
                calib["share_method"] = custom_method
        elif e["mode"] == "sea":
            calib["basis"] = "MODELED"
            calib["share_method"] = reason_text[stats["modeled_reasons"][key]]
        else:
            calib["basis"] = "MODELED"
            calib["share_method"] = REASON_COMTRADE
        e["calibration"] = calib
    return edges, stats

PROSE_HEAD = """# SupermAP — Calibration table and derivation notes (graph v1.0)

Built from the base node census (retrieved 2026-10-20) plus
figures retrieved 2026-10-20 (named per row). The PortWatch edge-share join
is described below. Generated by `data/graph/build_graph.py`; the node and edge tables
below are emitted by the script so this file regenerates byte-identically
with `graph.json`.

## Method & unit rulings

- **Graph conversion:** census `type` -> `kind` unchanged; `mode`: port/chokepoint
  = `sea`, air_hub = `air`, ground_node = `ground`.
- **Capacity units:** ports `TEU/day`, air hubs `tonnes/day`, ground nodes
  `trucks/day`. **Exception:**
  chokepoints use `vessels/day`, because every sourced chokepoint figure is a
  vessel transit count and no sourced TEU/day figure exists for any strait;
  vessels are the chokepoint stock unit
  (`queue_cap`). This exception is stated explicitly rather
  than converting oil bbl/d or vessel counts into invented TEU figures.
- **Capacity semantics:** where the sourced figure is *handled throughput*
  (all ports, most air hubs), `capacity` = annual throughput / 365 and is a
  **throughput proxy for capacity** — labelled MODELED interpretation in the
  table. True nameplate capacity is higher; the proxy is the conservative,
  sourced choice. Where a true design-capacity figure was retrieved
  (Addis Ababa terminal, Dadri phase-I, City Deep) the table says so.
- **queue_cap (all MODELED except Panama):** ports = 3 days of throughput
  (TEU stock); chokepoints = 2 days of transits (vessels); air hubs = 2 days
  of throughput (tonnes); ground nodes = 1 day of throughput (trucks).
  Panama's queue_cap = 150 vessels is the *observed* crisis queue
  (135-150 ships, Panama replay) — the one sourced queue_cap.
- **inventory_days (all MODELED engine defaults):** port 7, chokepoint 0
  (a strait stores nothing), air hub 3,
  ground node 5. These are engine defaults, not data.

## Edge calibration — what the `calibration` object means here

- `volume_source` names the dataset that supplies that leg's flow share: **IMF PortWatch**
  for sea legs, **UN Comtrade 2024** for air/ground legs.
- `efficiency_source` (sea legs only): **World Bank CPPI 2024** where both
  endpoints are container ports, **UNCTAD LSCI 2024** where a chokepoint is an
  endpoint (CPPI does not cover straits).
- **HONESTY — what is NOT sourced:** no
  per-port PortWatch series beyond the join below, no Comtrade API queries, no CPPI score join and
  no LSCI join were executed (the PortWatch
  export endpoint, the CPPI edition URL and the port-LSCI dataset code are
  unconfirmed, and Comtrade bulk/API access needs a free key that was
  not available). Consequently the base `capacity_share` values are
  **MODELED allocations** — ranked consistently with the census throughput
  rankings and the named datasets' published aggregates — and
  `base_transit_d` values were **MODELED point values inside published
  typical lane ranges** (carrier schedules / sailing distances).
- **PortWatch join — executed**
  for the edges the recorded data supports; see the dedicated section below
  for the formula, the pinned snapshot input, per-class counts and the
  proxy's limitations. **Correction: the join covers PORT origins only — the 8 chokepoint->port edges it first
  re-derived are reverted to their MODELED shares, because
  chokepoint-exit legs are transit flows and destination call-attraction
  is the wrong mechanism for them (see the correction in the join
  section).** Edges the snapshot cannot support keep their MODELED
  allocation, and every edge's `calibration` object now records `basis`
  (SOURCED / MODELED), the method or the reason it stays modeled, and — for
  SOURCED edges — the source, `share_retrieved_at` and the exact numeric
  inputs. Still NOT executed: the Comtrade join (needs
  `UN_COMTRADE_API_KEY`; a ready-to-run overlay builder sits at
  `data/graph/build/comtrade_overlay.py` and changes nothing without the
  key) and the CPPI / LSCI efficiency joins (the `efficiency_source` field
  still only *names* the assigned dataset; no score has been joined onto any
  edge or node). `base_transit_d` remains **MODELED on every edge**: the
  PortWatch services the adapter fetches (ports registry vessel counts;
  disruptions view event dates) carry no transit-time field, so no sourced
  transit derivation exists in the recorded data — stated plainly rather
  than dressed up as a derivation.
- **EXTENDED CENSUS — 120 -> 200 nodes:**
  `data/graph/nodes-expansion.json` adds 55 ports, 2 chokepoints
  (Dover Strait, Mozambique Channel), 13 air hubs and 10 ground nodes
  under the same entry rules as the base census (a named source per node,
  carried in each entry's `source_name` and into graph.json `sources`).
  Port coordinates are the IMF PortWatch ports-registry values; the extended
  census ports' PortWatch records are part of the pinned join input (see
  the join section), so the PortWatch join covers the new ports on the
  same terms; new
  air/ground edges are MODELED allocations with the lane basis stated in
  each edge note, pending the Comtrade join. Figures that are not plain
  calendar-2023 totals (fiscal-year, 2024, derived, run-rate) are NOT
  forced through the 2023 regex: they are derived in the PORT_OVERRIDE /
  AIR_OVERRIDE / GROUND_OVERRIDE tables in `build_graph.py` with the
  derivation written out. This also closes Gaps #7 below: the
  Dammam and Durban nodes exist, and the two ground legs
  (Riyadh, City Deep) land on the ports their sources name.
- **TWO-TIER GRAPH — 200 CORE + 792 SHELL = 992 nodes:**
  every node carries a stored, validator-checked `tier`. CORE is the
  200-node census above, unchanged in membership and calibration: it is
  the only tier that may originate Ledger locks and the only tier the
  scorecard/backtest grades. SHELL is a routing-grade census of 792
  nodes (660 ports, 77 air hubs, 55 ground nodes) built by
  `data/graph/build/build_shell_census.py` from pinned registry extracts
  (`portwatch_registry_pin.json`, `ourairports_pin.json`,
  `air_ranking_pin.json`, `ntad_rail_pin.json` — verbatim pulls of the
  IMF PortWatch ports database, OurAirports, the Wikipedia
  busiest-by-passenger tables and the NTAD rail TOFC/COFC layer,
  retrieved 2026-10-21). The SHELL bar: verified registry coordinates +
  observed activity presence (PortWatch vessel calls for ports;
  large-airport scheduled-service presence for air hubs; an operating
  NTAD facility record for ground nodes). Shell capacity is a labelled
  ESTIMATE where a basis exists — shell ports only: TEU/day derived from
  the port's registry vessel calls via the CORE-port median factors
  printed in the per-node derivation column — and NOT_PUBLISHED where no
  figure is published in the pinned sources (all shell air hubs and
  ground nodes), in which case a stated MODELED shell default applies
  (ports 500, air 300, ground 1,500 per day). Shell edges are generated,
  never hand-listed, under construction rules S1-S6 (hub links to the
  nearest CORE nodes, bidirectional regional peer mesh, budget-capped
  CORE->shell reverses); legs the PortWatch join supports are SOURCED
  per edge, the rest are MODELED with the rule recorded. Provenance is
  reported PER TIER everywhere (graph.json `metadata.provenance`, the
  section generated below, the SIM provenance line); no single blended
  figure is used. Registry data quality note: coordinates
  are the registries' own, verbatim — the PortWatch record for
  Guangzhou (Nansha) geocodes to the Guangzhou port centroid (its
  registry point sits ~9 km from the CORE Guangzhou node, not at the
  Nansha terminals ~55 km south); flagged here rather than silently
  corrected, because the shell bar is registry fidelity.

## Node calibration table

| id | kind | capacity | unit | queue_cap | inventory_days | basis | derivation |
|---|---|---|---|---|---|---|---|
"""

EDGE_TABLE_HEAD = """| edge key | mode | base_transit_d | capacity_share | basis | volume_source | efficiency_source | derivation note |
|---|---|---|---|---|---|---|---|
"""

PROSE_MID = """
## Edge calibration table

Every directed edge in graph.json (sorted by edge key). `basis` is the
per-edge truth: SOURCED shares were re-derived from the
pinned PortWatch snapshot by the formula in the join section above
(exact inputs in each edge's `calibration.share_inputs`
in graph.json); MODELED shares are the original allocations, with the reason
recorded in each edge's `calibration.share_method`. Transit is MODELED on
every edge (see the honesty note). Volume/efficiency sources name the
calibration dataset assigned to the leg.

"""


def tier_section_md(metadata):
    """The two-tier provenance section of calibration.md, generated from
    the same metadata object graph.json carries."""
    t, p = metadata["tiers"], metadata["provenance"]
    c, s = t["core"], t["shell"]
    pc, ps = p["core"], p["shell"]
    return f"""
## Two-tier provenance

Per-tier figures replace the single blended SOURCED share everywhere
the product prints provenance (this file, graph.json `metadata`, the
SIM provenance line, the graph panel).

- **CORE:** {c['nodes']} nodes (ports {c['by_kind']['port']}, chokepoints
  {c['by_kind']['chokepoint']}, air hubs {c['by_kind']['air_hub']},
  ground {c['by_kind']['ground_node']}); {pc['edges']} CORE<->CORE edges,
  of which {pc['sea_edges']} are sea and {pc['sourced_edges']} carry
  SOURCED (PortWatch join) shares — CORE sourced share of sea:
  {pc['sourced_share_of_sea']:.1%}. The SHELL tier cannot move it
  (join coverage set pinned, shell destinations excluded, engine runs
  the CORE subgraph).
- **SHELL:** {s['nodes']} nodes (ports {s['by_kind']['port']}, air hubs
  {s['by_kind']['air_hub']}, ground {s['by_kind']['ground_node']}) — every
  one with registry-verified coordinates and observed activity presence
  ({ps['nodes_with_registry_activity']} of {s['nodes']});
  {ps['edges']} shell-touching edges: {ps['sourced_edges']} SOURCED
  (shell->CORE hub legs the PortWatch join derives from the pinned CORE
  snapshot), {ps['modeled_edges']} MODELED (construction rules S1-S6).
- **Enforcement, not convention:** the tier is validated by
  the graph checks (shell node without registry coordinates/activity
  or without a labelled capacity basis FAILs), the chain engine runs the
  CORE subgraph for chains/backtests/scorecard (a disruption addressed
  at a shell node is refused as unknown to the chain engine), and
  `LedgerStore.lock` refuses any lock whose disruption node or chain
  events touch a shell node.
"""


def join_section_md(edges, stats, node_by_id, counts, retrieved_at, input_sha256, n_records):
    """The PortWatch join section of calibration.md, generated from
    the same in-memory objects as graph.json so the counts cannot drift."""
    sourced = [e for e in edges if e["calibration"]["basis"] == "SOURCED"]
    sea = [e for e in edges if e["mode"] == "sea"]
    pp = sum(1 for e in sourced if node_by_id[e["from"]]["kind"] == "port")
    cp = sum(1 for e in sourced if node_by_id[e["from"]]["kind"] == "chokepoint")
    # Per-tier split (two-tier): the CORE result is
    # computed over CORE<->CORE sea edges only and is the figure the
    # scorecard-era claims cite; SHELL-touching legs are reported apart.
    core_ids = {nid for nid, n in node_by_id.items()
                if n.get("tier", "core") == "core"}
    core_sea = [e for e in sea if e["from"] in core_ids and e["to"] in core_ids]
    core_sourced = [e for e in core_sea
                    if e["calibration"]["basis"] == "SOURCED"]
    shell_sea = [e for e in sea if e not in core_sea]
    shell_sourced = [e for e in shell_sea
                     if e["calibration"]["basis"] == "SOURCED"]
    reasons = {}
    for r in stats["core"]["modeled_reasons"].values():
        reasons[r] = reasons.get(r, 0) + 1
    pp_core = sum(1 for k in stats["core"]["sourced"]
                  if node_by_id[k.split("->")[0]]["kind"] == "port")
    air_n = sum(1 for e in edges if e["mode"] == "air")
    ground_n = sum(1 for e in edges if e["mode"] == "ground")
    return f"""
## PortWatch join

- **Input (pinned, recorded):** `data/graph/portwatch_join_input.json` —
  {n_records} `port_activity` records (IMF PortWatch ports database, ArcGIS
  FeatureServer `PortWatch_ports_database`) in two verbatim extracts: the
  original 28 records from the adapter snapshot
  `data/snapshots/portwatch.json` (fetched by
  `server/adapters/portwatch.py`; snapshot `retrieved_at`:
  {retrieved_at}), plus the expansion
  ports' records extracted verbatim from the same live FeatureServer, each
  carrying its own `retrieved_at` (2026-10-21), which SOURCED edges to
  those destinations record as their `share_retrieved_at`. Join-input
  sha256: `{input_sha256}`. The generator reads only this pinned file —
  never the live snapshot, never the network — so regeneration stays
  byte-identical when the adapter refreshes its snapshot. The records
  cover {len(counts)} graph nodes with a container-vessel count
  (duplicate port records for one node summed); the input carries no
  chokepoint records.
- **Formula (destination-attraction):** for a sea PORT origin O with at
  least 2 covered destinations, `capacity_share(O->D) = S_cov * c(D) /
  SUM(c over covered destinations of O)`, where `c(D)` is D's PortWatch
  `vessel_count_container` and `S_cov` is the sum of the pre-join MODELED
  shares over O's covered sea edges — the origin's covered subtotal is
  preserved exactly and redistributed in proportion to destination
  container-vessel calls, apportioned in 1e-4 units by largest remainder
  (ties by edge key). Origins with fewer than 2 covered destinations cannot
  support a distribution and keep MODELED shares throughout. CHOKEPOINT
  origins are excluded from the join entirely (correction below). The
  join's coverage set (the pinned snapshot of CORE destinations) is
  unchanged by the shell tier: shell destinations are not in it,
  so CORE shares cannot move.
- **Result (per tier):** CORE — {len(core_sourced)}
  of {len(core_sea)} CORE sea edges carry SOURCED (join-derived) shares
  ({pp_core} port-origin derivations across {len(stats['core']['origins'])}
  CORE origins; the CORE coverage set is the pinned snapshot of
  CORE destinations, so CORE shares do not depend on the SHELL tier). SHELL — {len(shell_sourced)} of
  {len(shell_sea)} shell-touching sea edges are SOURCED: shell->CORE hub
  legs whose destination the pinned CORE snapshot covers (observed-call
  evidence, classified per edge); the remaining shell-touching legs stay
  MODELED under construction rules S1-S3 (see the two-tier section of
  this file and `metadata.shell_construction` in graph.json).
  Remaining CORE sea edges stay MODELED:
  {reasons.get('chokepoint_origin', 0)} chokepoint-exit transit legs
  excluded by the correction below,
  {reasons.get('destination_uncovered', 0)} whose destination port has no
  record in the snapshot, {reasons.get('chokepoint_destination', 0)} whose
  destination is a chokepoint (no chokepoint series in the fetched
  services), {reasons.get('single_covered_origin', 0)} whose origin has
  fewer than 2 covered destinations. All {air_n} air and {ground_n} ground
  edges stay MODELED — CORE legs pending the Comtrade join (key seam:
  `data/graph/build/comtrade_overlay.py`), SHELL legs under construction
  rules S4-S6.
- **Correction — chokepoint-exit legs reverted:** the join as first executed also re-derived 8
  chokepoint->port edges. That was the wrong mechanism for those legs:
  chokepoint-exit legs are transit flows, not port calls — vessels transit
  Suez past Port Said without calling there — so a destination's container
  call count does not measure the leg's flow, and Port Said's low call
  count mechanically shrank `chk_suez->sea_portsaid` from its MODELED
  0.40 to 0.1217 (the canonical Suez cascade fell from 13 to 8 events).
  A sourced number with a wrong mechanism is worse than a labelled
  modelled allocation, so chokepoint origins are excluded from the join
  entirely and the 8 re-derived edges (from `chk_capeofgoodhope`,
  `chk_panama` and `chk_suez`) carry exactly their pre-join MODELED
  shares again, with the reason recorded in each edge's
  `calibration.share_method`; they stay MODELED pending a
  transit-distribution source. The 35 port->port derivations stand.
- **Limitation, stated plainly:** PortWatch's fetched ports service records
  port calls by vessel type — not bilateral O-D flows and not TEU volumes.
  Destination container calls are therefore an *attraction proxy* for the
  split of a port origin's covered flow, not observed lane flow. The proxy
  is not applied to chokepoint-exit transit legs at all (see the correction
  above): a transit leg's destination may be passed without a call, so
  call counts cannot even proxy its split. Each SOURCED edge records its
  exact inputs (`calibration.share_inputs`) so the derivation is auditable
  and the MODELED value recoverable from `covered_modeled_subtotal`.
- **Transit times:** unchanged and MODELED on all {len(edges)} edges — the
  fetched services carry no transit-time field (see the honesty note).
"""

PROSE_TAIL = """
## Verification checklist (base census) — status at build

- **UN/LOCODE / IATA / ICAO codes:** carried from the base census into
  `aliases`. They were **not** independently re-verified against the current
  UNECE UN/LOCODE release or Wikidata in this build — the census itself flagged them for verification, and that item **remains open**
  (listed as a gap below). Spot check performed: Soekarno-Hatta
  (CGK/WIII) coordinates on its Wikipedia page (6.12556 S, 106.65583 E) match
  the census values (-6.13, 106.66) at census rounding.
- **Coordinates:** reference-point values from the census; not re-verified
  node-by-node (same open item).
- **Known code/identity notes:** `sea_antwerp` codes carry Antwerp's UN/LOCODE
  (BEANR); the merged Antwerp-Bruges entity's Zeebrugge component (BEBRU) is
  not separately coded, and the census TEU figure is the Antwerp component
  only. `sea_colon` carries PAMIT (Manzanillo International Terminal, Colon
  cluster) per the census. `chk_capeofgoodhope` is a route node, not a
  geographic chokepoint (per census note).

## Gaps (explicit — nothing below was filled with an invented figure)

1. **Ground-node capacities (36 nodes, GAP-MODELED):** 36 of 45 ground nodes
   carry the MODELED engine default of 1,500 trucks/day; no sourced
   trucks/day or convertible TEU/yr figure was retrieved for them. They split
   into (a) 18 nodes whose draft `source_url` was null and for which no source
   was found either - gnd_atlanta, gnd_bettembourg, gnd_budapest,
   gnd_columbus, gnd_dallas, gnd_erenhot, gnd_halkali, gnd_inlandempire,
   gnd_kansascity, gnd_madrid, gnd_manzhouli, gnd_memphis, gnd_milan,
   gnd_montreal, gnd_paris, gnd_toronto, gnd_vancouver, gnd_warsaw - which
   keep the explicit GAP source string in graph.json, and (b) 18 nodes with a
   census source whose figure is a trade value or a qualitative role note,
   not convertible to trucks/day without an invented price-per-truck
   assumption - gnd_blaine, gnd_buffalo, gnd_detroit, gnd_dostyk,
   gnd_duisburg, gnd_elpaso, gnd_khorgos, gnd_laredo, gnd_malaszewicze,
   gnd_nogales, gnd_otaymesa, gnd_porthuron, plus 6 expansion nodes whose
   identity source is named but no convertible figure was retrieved (gnd_sweetgrass, gnd_alexandriabay — BTS port-of-entry
   identities; gnd_frontroyal, gnd_stockton, gnd_saltlakecity, gnd_stlouis —
   operator/authority identities); operator statistics
   are named in each node's census capacity_note.
2. **Null-source ground nodes resolved (5):** gnd_chicago,
   gnd_channeltunnel, gnd_dadri, gnd_citydeep, gnd_riyadh now carry retrieved
   sources and sourced (or sourced + documented-conversion) capacities.
3. **Chokepoint capacity, Cape of Good Hope (GAP-MODELED):** 100 vessels/day
   default; the Cape is an open-ocean route with no transit-count series
   retrieved.
4. **Chokepoint figures with precision caveats (SOURCED-APPROX):**
   chk_taiwan's 240/day (Bloomberg via Institut Thomas More) includes traffic
   sailing along Taiwan's east coast, overstating strait-only transits;
   chk_gibraltar's 100,000/yr is a round figure from a secondary source;
   chk_hormuz's ~74/day is a PortWatch pre-crisis norm reported by a secondary
   aggregator, not pulled from PortWatch directly.
5. **Air-hub precision gaps (SOURCED-APPROX):** air_cincinnati (~2M t/yr
   class), air_istanbul and air_shenzhen (~1.76M t/yr class) use the census
   class figures; exact ACI year figures were not retrieved.
6. **Edge shares are PARTIALLY sourced:** the
   PortWatch join re-derived the sea edges the pinned snapshot supports
   (exact counts by class, the formula, the coverage breakdown and the
   attraction-proxy limitation are in the generated join section above).
   The remaining sea edges and all air/ground edges remain MODELED
   allocations: ALL chokepoint-origin sea edges (chokepoint-exit transit
   legs are excluded from the join by the correction —
   call-attraction does not apply to transit flows; they keep their MODELED shares pending a transit-distribution source), sea edges whose
   destination the snapshot does not cover,
   and air/ground edges whose Comtrade join is built ready-to-run but still
   needs UN_COMTRADE_API_KEY. The CPPI / LSCI efficiency joins have still
   not been executed — `efficiency_source` names the assigned dataset only.
   **Transit times remain MODELED on every edge**: the fetched PortWatch
   services carry no transit-time field, and no other recorded source in
   the repo supports per-lane transit values.
7. **Census nodes completing two chokepoints (resolved):** the Baltic has Gdańsk and
   Gdynia (chk_danish legs land on Baltic ports), the Black Sea has
   Constanța (chk_turkish legs land there), Durban and Cape Town exist
   (gnd_citydeep's port leg lands on Durban, the port its traffic actually
   uses; a Cape Town leg was added), and Dammam exists (gnd_riyadh's rail
   leg now lands on Dammam, the link its source names; the Jeddah proxy
   leg was removed). The census stands at 200 nodes; the graph object is
   `"version": "1.0"`.
8. **Channel Tunnel trucks/day** counts LeShuttle Freight truck shuttles
   only (Getlink FY2025); through rail freight trains are additional and not
   counted.
9. **Panama queue/draft ground truth** in the replay still rests partly on
   the Ballast Markets secondary compilation (see replay files and the known gaps listed there); ACP / PortWatch re-verification is an open item.
10. **Shanghai->New York sea edge** is a single edge whose typical routing is
    Panama; the Suez routing of the same O/D pair cannot coexist (the graph
    schema forbids duplicate from/to/mode). The segmented Panama chain edges carry the
    canal-specific propagation.
11. **Expansion-figure caveats:** sea_hamad's total is DERIVED
    from Mwani Qatar's transshipment share (43%), not a published total;
    sea_fuzhou is an annualisation of an H1-2022 figure; sea_durban is
    derived from DCT Pier 2's reported ~three-quarter share of port volume;
    sea_felixstowe's two published 2023 figures disagree (3.25M Dynaliners
    table vs 2.82M Euromediterranean, the latter flagged 'no details' in
    its source — the Dynaliners value is used); sea_sydney is an undated
    operator run-rate; air_zhengzhou uses the latest figure retrieved (2021, 705,000 t) although the airport has since
    grown past 800,000 t/yr; air_atlanta uses an 11-month 2023 figure as
    the annual-basis proxy; chk_mozambique is GAP-MODELED (no published
    transit census exists for a wide deep-water channel). Each derivation
    is written out in the override tables in `build_graph.py`.
12. **SHELL tier limits:** the 792 shell
    nodes are routing-grade only. Shell port capacities are ESTIMATEs
    from registry vessel-call counts via CORE-port median factors —
    call counts are not TEU volumes, and the estimate for a
    container-quiet bulk port (derived through the all-traffic factor)
    is a TEU-equivalent proxy, printed as such per node. Shell air and
    ground capacities are NOT_PUBLISHED in the pinned sources and use
    stated MODELED defaults. Shell edge shares are MODELED construction
    allocations except the shell->CORE hub legs the PortWatch join
    derives; shell transit times are distance/speed point values.
    Nothing in the shell feeds the scorecard, the backtest or a Ledger
    lock — the seams refuse it in code. The shell census is US-heavy on
    the ground (NTAD is a US registry) and registry-shaped at sea
    (PortWatch coverage is thinnest for small island and river ports,
    which fall below the observed-calls cutoff and are dropped, not
    padded in).

## Determinism

`graph.json` is written by `build_graph.py` with nodes sorted by `id`, edges
sorted by (`from`, `to`, `mode`), `json.dump(..., sort_keys=True, indent=2,
ensure_ascii=False)` and one trailing newline. No timestamps or random values
are emitted. This file's tables are generated from the same in-memory objects.
The PortWatch join reads only the pinned `portwatch_join_input.json` beside
the generator (its sha256 is recorded in the join section above; the
snapshot's `retrieved_at` is carried into each SOURCED edge's calibration,
never a wall-clock value). Re-running the script on the same census input
and the same pinned join input reproduces both files byte-identically
(verified by hashing two consecutive runs).
"""

def graph_metadata(nodes, edges, shell_doc):
    """Per-tier provenance, computed from the final in-memory graph and
    printed identically in graph.json (served by GET /graph) and
    calibration.md. Replaces the single blended SOURCED figure."""
    core_ids = {n["id"] for n in nodes if n["tier"] == "core"}

    def is_core_edge(e):
        return e["from"] in core_ids and e["to"] in core_ids

    core_edges = [e for e in edges if is_core_edge(e)]
    shell_edges = [e for e in edges if not is_core_edge(e)]
    core_sea = [e for e in core_edges if e["mode"] == "sea"]
    core_sourced = [e for e in core_sea
                    if e["calibration"]["basis"] == "SOURCED"]
    shell_sourced = [e for e in shell_edges
                     if e["calibration"]["basis"] == "SOURCED"]
    by_kind = {}
    for tier in ("core", "shell"):
        by_kind[tier] = {}
        for kind in ("port", "chokepoint", "air_hub", "ground_node"):
            by_kind[tier][kind] = sum(
                1 for n in nodes if n["tier"] == tier and n["kind"] == kind)
    pin_shas = {}
    for name in ("portwatch_registry_pin.json", "ourairports_pin.json",
                 "air_ranking_pin.json", "ntad_rail_pin.json",
                 "nodes-shell.json"):
        p = HERE / name
        if p.exists():
            pin_shas[name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return {
        "tiers": {
            "core": {"nodes": sum(1 for n in nodes if n["tier"] == "core"),
                     "by_kind": by_kind["core"],
                     "definition": "Calibration-grade: capacity with a named source; may originate Ledger locks; feeds the scorecard/backtest."},
            "shell": {"nodes": sum(1 for n in nodes if n["tier"] == "shell"),
                      "by_kind": by_kind["shell"],
                      "definition": "Routing-grade: verified registry coordinates + observed activity presence; capacity ESTIMATE (basis printed in calibration.md) or NOT_PUBLISHED; never originates a Ledger lock; never feeds scorecard/backtest claims; excluded from calibration joins over CORE claims (the PortWatch join's CORE coverage set is unchanged)."},
        },
        "provenance": {
            "core": {"edges": len(core_edges), "sea_edges": len(core_sea),
                     "sourced_edges": len(core_sourced),
                     "sourced_share_of_sea": round(
                         len(core_sourced) / len(core_sea), 4) if core_sea else 0},
            "shell": {"edges": len(shell_edges),
                      "sourced_edges": len(shell_sourced),
                      "modeled_edges": len(shell_edges) - len(shell_sourced),
                      "nodes_with_registry_activity":
                          sum(1 for n in nodes if n["tier"] == "shell"),
                      "activity_sources": {
                          "port": "IMF PortWatch ports database (observed vessel calls)",
                          "air_hub": "OurAirports large airports with scheduled service; Wikipedia busiest-by-passenger top-50 presence for ranking",
                          "ground_node": "NTAD (BTS) Intermodal Freight Facilities Rail TOFC/COFC"}},
        },
        "shell_construction": {
            "retrieved_at": "2026-10-21",
            "census": "data/graph/nodes-shell.json",
            "builder": "data/graph/build/build_shell_census.py",
            "rules": SHELL_RULES,
            "pin_sha256": pin_shas,
            "derivation": (shell_doc or {}).get("derivation"),
        },
    }


def main():
    inp = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT
    census = json.loads(inp.read_text(encoding="utf-8"))
    # Extended census: merge the expansion census over the
    # base census. The base census file is never modified.
    exp_path = HERE / "nodes-expansion.json"
    if exp_path.exists():
        exp = json.loads(exp_path.read_text(encoding="utf-8"))
        census = {**census, "nodes": census["nodes"] + exp["nodes"]}
    # Two-tier graph: merge the SHELL census (pinned
    # registry extracts; builder in data/graph/build/). The CORE
    # censuses are never modified.
    shell_path = HERE / "nodes-shell.json"
    shell_doc, shell_activity = None, {}
    if shell_path.exists():
        shell_doc = json.loads(shell_path.read_text(encoding="utf-8"))
        census = {**census, "nodes": census["nodes"] + shell_doc["nodes"]}
        shell_activity = {e["id"]: e.get("activity") or {}
                          for e in shell_doc["nodes"]}
    nodes, calib = build_nodes(census)
    node_by_id = {n["id"]: n for n in nodes}
    counts, retrieved_by_node, retrieved_at, input_sha256 = load_portwatch_counts()
    core_totals = load_portwatch_total_counts()
    n_pw_records = len(json.loads(PORTWATCH_INPUT.read_text(encoding="utf-8"))["records"])
    edge_pairs, join_stats = build_edges(
        node_by_id, counts, retrieved_by_node, retrieved_at,
        shell_activity=shell_activity if shell_doc else None,
        core_totals=core_totals)
    edges = [e for e, _ in edge_pairs]
    notes = {f'{e["from"]}->{e["to"]}:{e["mode"]}': note for e, note in edge_pairs}
    metadata = graph_metadata(nodes, edges, shell_doc)
    graph = {"version": "1.0", "nodes": nodes, "edges": edges,
             "metadata": metadata}
    out = json.dumps(graph, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    (HERE / "graph.json").write_text(out, encoding="utf-8")
    # calibration.md
    md = [PROSE_HEAD]
    for c in sorted(calib, key=lambda x: x["id"]):
        md.append(f'| {c["id"]} | {c["kind"]} | {c["capacity"]:,} | {UNIT_BY_KIND[c["kind"]]} | {c["queue_cap"]:,} | {c["inventory_days"]:g} | {c["status"]} | {c["derivation"]} |\n')
    md.append(tier_section_md(metadata))
    md.append(join_section_md(edges, join_stats, node_by_id, counts,
                              retrieved_at, input_sha256, n_pw_records))
    md.append(PROSE_MID)
    md.append(EDGE_TABLE_HEAD)
    for e in edges:
        key = f'{e["from"]}->{e["to"]}:{e["mode"]}'
        eff = e["calibration"].get("efficiency_source", "-")
        md.append(f'| {key} | {e["mode"]} | {e["base_transit_d"]:g} | {e["capacity_share"]:.4f} | {e["calibration"]["basis"]} | {e["calibration"]["volume_source"]} | {eff} | {notes[key]} |\n')
    md.append(PROSE_TAIL)
    (HERE / "calibration.md").write_text("".join(md), encoding="utf-8")
    print(f"nodes={len(nodes)} (core={metadata['tiers']['core']['nodes']} "
          f"shell={metadata['tiers']['shell']['nodes']}) edges={len(edges)} "
          f"sourced={len(join_stats['sourced'])} "
          f"core_sourced={len(join_stats['core']['sourced'])}")

if __name__ == "__main__":
    main()
