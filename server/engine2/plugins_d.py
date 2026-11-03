"""ENGINE REV 2 · PROFILE d (``rev2d``) plug-in registry.

Profile d = the calibrated profile (rev2c: per-place mu_max from PortWatch, sourced LA/LB
cover, deficit-days delay definition) PLUS opt-in, DATA-DRIVEN mechanisms that apply to every
place of the graph, with no place-specific code:

  1. REAL SEA-LANE GEOMETRY      every sea leg of the graph (all tiers) sails at lane distance /
                                 service speed (data/graph/sea_routes.json); legs with no real
                                 geometry keep the graph's own transit days and are counted;
  2. GATES                       any place is a GATE with a queue discipline chosen by a data
                                 spec (gate_specs): the DEFAULT gate (every place) is a FIFO
                                 server with a bounded anchorage whose spill-back also covers
                                 exogenous traffic; Panama (fixed slots, booked / unbooked) and
                                 Suez (convoy windows) are two sourced spec entries;
  3. ROUTE CHOICE                a graded demand response (logistic in the time advantage) wherever a
                                 real alternative exists: the network lane that avoids a gate
                                 (Suez/Bab el-Mandeb unconditionally as in rev 2, any other gate only
                                 where a selected service witnesses it) and port substitution along
                                 a service rotation;
  4. ROTATION RECOVERY           a delay that reaches a rotation's port is carried to its next
                                 calls, partly absorbed by schedule slack (every service of
                                 services.json);
  5. TIER WALL                   SHELL places route and propagate but mint no chain event.

The default ENGINE REV 2 registry (plugins.py) and the rev2c registry
(plugins_cal.py) are NOT edited. This module composes the profile's registry from the rev2c
one: entries it replaces are overridden, new entries are appended, everything else is carried
over. A rev2d run is stamped with this registry's hash and the label
``ENGINE REV 2 · PROFILE d``.

Status vocabulary as in plugins.py: SOURCED / CAL / CONVENTION / TEMPORARY / NUMERICAL.
Every number of the new mechanisms is in this file; the code that uses it reads it from here.
"""
from __future__ import annotations

from engine.canonical import canonical_json, sha256_hex

from .plugins import ENGINE_REV
from .plugins_cal import CAL_PLUGINS, CAL_VERSION

PROFILE = "rev2d"
PROFILE_KEY = "d"
PROFILE_LABEL = "ENGINE REV 2 · PROFILE d"
D_VERSION = "2.3.0"

_OVERRIDE: dict = {}
_NEW: list = []


def _entry(name, value, unit, where, why, status, source, replace_with, bounds=None):
    return {"name": name, "value": value, "unit": unit, "where_used": where,
            "why_needed": why, "status": status, "source": source,
            "replace_with": replace_with, "bounds": bounds}


# ============================== 1. GEOMETRY ===================================
_NEW.append(_entry(
    "sea_route_network",
    {"content_sha256": "762a12555a7f564d1c871c958208290aaf65258661e1937601c7310a6b5623d8",
     "file": "data/graph/sea_routes.json", "schema": "supermap.sea_routes/1",
     "network": "MARNET via searoute-py 1.6.0 (Apache-2.0), descended from Eurostat SeaRoute (EUPL-1.2)",
     "restricted_passages": ["northwest", "bering"],
     "snap_limit_nm": 150.0, "chokepoint_radius_nm": 150.0},
    "pin of the geometry sidecar and its generation parameters",
    "sea_routes.py load_sea_routes (refuses a file whose content hash differs); retime_graph; "
    "routes_d.build_routes_table_d (hop and alternative-route distances)",
    "straight chords between place coordinates understate every real route (the chord from "
    "Singapore to Rotterdam made the Cape detour 16.7 d against 9 to 10 d at lane distance); a lane "
    "network respects canals, straits and capes. EVERY sea leg of the graph is routed (CORE and SHELL); "
    "a leg whose end is further than snap_limit_nm from the network (or has no path) keeps the "
    "graph's own transit and is counted as FALLBACK in the coverage report. The two limits are "
    "generation parameters (TEMPORARY, structural): a port 150 nm from any lane has no meaningful route",
    "SOURCED",
    "MARNET maritime network as shipped in the 'searoute' PyPI package v1.6.0 "
    "(https://github.com/genthalili/searoute-py; installed package metadata: Apache-2.0) "
    "which credits Eurostat's SeaRoute Java library "
    "(https://github.com/eurostat/searoute, EUPL-1.2). Retrieved 2026-10-25. The package states it "
    "produces realistic-looking routes, not routing advice. Validated against published port-to-port "
    "distances: Singapore-Rotterdam via Suez 8,372 nm vs 8,288 (Port of "
    "Rotterdam via Hellenic Shipping News https://www.hellenicshippingnews.com/?p=1021740), via the "
    "Cape 11,861 vs 11,755; Shanghai-Rotterdam via Suez 10,594 vs 10,600 and via the Cape 13,914 vs "
    "13,800 (Notteboom, Pallis, Rodrigue, Port Economics, Management and Policy, 2nd ed., 2026, "
    "https://porteconomicsmanagement.org/routing-options-shanghai-rotterdam/); Shanghai-New York via "
    "Panama 10,697 nm = 27.9 d at 16 kn vs 'about 28 days' (same source)",
    "AIS-derived lane statistics per trade; a licensed route engine (e.g. a carrier's own distance table)"))

_NEW.append(_entry(
    "land_separation_ratio", 3.0, "lane nautical miles / straight-line nautical miles",
    "sea_routes.SeaRoutes: a leg whose sidecar lane is longer than this multiple of its straight line keeps "
    "the graph's own transit days and is counted as FALLBACK (land-separated) in the coverage report",
    "many graph sea legs join two ports that a short road, rail or unmodelled canal link and that the sea "
    "route reaches only by going round a peninsula (Luebeck-Hamburg 624 nm for 39, Tsuruga-Nagoya 787 for 54, "
    "Lazaro Cardenas-Veracruz 3,233 for 352 via Panama); giving them the sea-route transit would make a "
    "one-day link a week-long voyage",
    "TEMPORARY",
    "assumption: the median ratio over all 3,634 routed legs is 1.5, the 90th percentile 3.6; three is a "
    "round cut above the bulk. Fixed before any replay run, not tuned",
    "a mode label per leg (road, rail, feeder, deep sea) from the graph builder", [1.5, 10.0]))

_NEW.append(_entry(
    "service_speed_kn", {"long_haul": 15.6, "regional": 13.8}, "knots",
    "sea_routes.SeaRoutes.days: transit days of a sea route = nautical miles / (speed x 24 h)",
    "a distance needs a speed to become days; liner services slow-steam, so the design speed is not used",
    "SOURCED",
    "BIMCO, container fleet average sailing speed 13.8 kn in Q1 2023 (all sizes; via Hellenic Shipping "
    "News https://www.hellenicshippingnews.com/?p=981345, retrieved 2026-10-25) for regional legs; "
    "average speed of 7,500 TEU+ ships 15.6 kn in early 2023 (study reported by DVZ via Kuehne+Nagel "
    "https://mykn.kuehne-nagel.com/news/article/speeds-of-container-vessels-drop-to-a-new-low-24-May-2023, "
    "retrieved 2026-10-25) for long-haul legs. Consistent with the Port Economics 16 kn / 27 d example",
    "AIS-observed speed per leg by vessel size class and trade", [10.0, 24.0]))

_NEW.append(_entry(
    "speed_class_boundary_nm", 3000.0, "nautical miles",
    "sea_routes.SeaRoutes.speed_kn: a route at least this long sails at the long-haul speed",
    "the graph legs carry no vessel size class; the route length is the only available proxy for "
    "mainline (large ship) versus regional service",
    "TEMPORARY",
    "assumption: roughly the break between intra-regional and inter-regional container trades; not a "
    "sourced figure",
    "vessel size class per service from the services sidecar (TEU per ship) or AIS", [500.0, 8000.0]))

_NEW.append(_entry(
    "leg_transit_rule",
    {"rule": "base_transit_d(leg) = lane nautical miles / (speed x hours_per_day); no port time",
     "hours_per_day": 24.0},
    "rule / hours",
    "sea_routes.retime_graph: replaces base_transit_d of every sea leg that has a sidecar entry; "
    "routes_d: chain and alternative-route days",
    "the graph's transit days were MODELED point values inside published typical lane ranges and "
    "(for chord-derived routes) included port time implicitly; the lane distance gives a sailing "
    "time, port dwell is the queue and cover of the place",
    "CONVENTION", "definition (time = distance / speed); 24 h per day is an identity",
    "carrier-published rotation days per service"))

_OVERRIDE["cape_detour_days"] = _entry(
    "cape_detour_days",
    "(lane nm of the route that avoids the gate - lane nm through the gate) / (service speed x 24 h)",
    "days",
    "routes_d: added days of the alternative of a Suez / Bab el-Mandeb route (and of every other gate "
    "alternative)",
    "the route-length difference at lane distance and a sourced speed replaces the chord difference "
    "x the bound leg's own days per km (16.7 d for Singapore-Rotterdam, against 9 to 10 d)",
    "CAL",
    "sea_route_network (distances) and service_speed_kn (speed). Shanghai-Rotterdam: +3,320 nm = 8.9 d "
    "at 15.6 kn; Singapore-Rotterdam +3,488 nm = 9.3 d. Published comparison: Port of Rotterdam "
    "(via Hellenic Shipping News https://www.hellenicshippingnews.com/?p=1021740, retrieved 2026-10-25) 'extended voyage duration of eight to twelve "
    "days' for container ships; Notteboom et al. 'minimum of 7 days'; UNCTAD 14 to 24 days is "
    "secondary and includes port effects",
    "carrier-published rotation days per service")

# ======================= 2. GATES: queue disciplines by data ====================
_NEW.append(_entry(
    "gate_specs",
    [
        {"id": "default_fifo_bounded_anchorage", "match": {"all": True},
         "discipline": "fifo_bounded",
         "params": {"spill_exogenous": True},
         "derivation": "capacity -> lambda0 (graph), queue_cap -> anchorage (graph), service rate "
                       "mu_max = ratio x lambda0 with the rev2c ratio (IMF PortWatch P99/median, class "
                       "median for places with no series, 1.3 for ground and air = TEMPORARY)",
         "source": "stock-and-flow queue with a soft queue_cap; rev2c "
                   "calibration for the ratios. The World Bank / S&P CPPI 2025 sidecar (CC BY-NC 3.0 "
                   "IGO) is a RELATIVE time-in-port index in index points: it gives "
                   "no capacity, service rate or queue length and its own note forbids reading it as a "
                   "service time, so it is NOT used by the default gate"},
        {"id": "acp_panama_booked_slots", "match": {"ids": ["chk_panama"]},
         "discipline": "slot_booked",
         "params": {"booked_share": 0.9459},
         "rule": "(1) during an impairment the service rate is lambda0 x (1 - loss), capped at mu_max: "
                 "the slot count falls, the demonstrated-peak ratio is not applied to the cut; "
                 "(2) while capacity is partly cut (0 < loss < 1: ACP orders slots down and bookings cannot "
                 "exceed them) booked arrivals = booked_share x min(arrivals, service rate) are served at "
                 "once (strict priority; their queue is nil); the rest are unbooked and wait for the "
                 "residual capacity: wait = backlog / (service rate - booked rate). Only the unbooked "
                 "class reads the wait and diverts. A full closure and an open gate are plain FIFO",
         "source": "Panama Canal Authority, Advisory to Shipping A-04-2024 (Monthly Canal Operations "
                   "Summary, January 2024; https://pancanal.com/wp-content/uploads/2024/01/ADV04-2024-"
                   "Monthly-Canal-Operations-Summary-%E2%80%93-January-2024.pdf, retrieved 2026-10-25): "
                   "'the normal transit capacity of the Panamax locks is 34-36 vessels per day, and in the "
                   "Neopanamax locks 9-11 vessels per day ... maximum sustainable capacity ... approximately "
                   "36-38 vessels per day'; slots are ordered down when capacity is expected to be reduced; "
                   "702 oceangoing transits in January 2024, booking slots used Neopanamax 153, large 247, "
                   "regular 102, auctioned 162 (= 664 booked): 664/702 = 0.9459. AMBIGUITY: the footnote "
                   "says the 'used' column includes booked transits only and that 'available' excludes "
                   "auctioned slots; if the auctioned 162 are already inside the three used rows the "
                   "booked share is 502/702 = 0.715. The upper reading is used; it is the share of a "
                   "drought-slot month, not of the pre-drought first-come regime. The replay's cut basis: "
                   "ACP advisory 30 Oct 2023, 36 normal, 18 minimum from 1 Feb 2024. Draft and size effect: "
                   "the cut is in vessel-equivalents of the graph's 36 per day, no size mix is modelled"},
        {"id": "sca_suez_convoys", "match": {"ids": ["chk_suez"]},
         "discipline": "convoy",
         "params": {"northbound": 1, "southbound": 2},
         "rule": "a wait at the gate is rounded UP to the next convoy departure: w -> sum over "
                 "directions of weight x ceil(w x n) / n, n = convoys per day in that direction; "
                 "applied to the decision wait and to the reported FIFO wait",
         "source": "Kuehne+Nagel 'Passage through the Suez Canal' (13 Sep 2024; https://mykn.kuehne-nagel."
                   "com/news/article/passage-through-the-suez-canal-13-Sep-2024, retrieved 2026-10-25): "
                   "'Typically, there are three daily convoys: two southbound and one northbound'; vessels "
                   "'must wait at designated anchorages until their convoy is scheduled to depart'; transit "
                   "12 to 16 hours. Equal spacing within a direction is a modelling convention. Bab "
                   "el-Mandeb: no source for scheduled convoys was found (naval escorts of 2024 are not a "
                   "transit schedule), so it keeps the default gate"},
    ],
    "list of gate specs",
    "profile_d.ProfileD.attach: each spec is applied to the places its ``match`` selects (``all`` or "
    "``ids``), in order, later specs overriding earlier ones; engine.py reads only the resulting "
    "per-place arrays (no place id appears in engine code)",
    "a gate is any capacity-constrained resource (canal, strait, lock, port entrance, berth group, "
    "pilotage window, rail landbridge, border crossing). Its discipline decides who waits and how "
    "long; the default (FIFO with a bounded anchorage) covers every place, a sourced regime overrides it",
    "SOURCED",
    "see each spec (ACP Advisory A-04-2024 for Panama, Kuehne+Nagel for Suez); the default gate's mu_max ratios are CAL (PortWatch, rev2c) for 110 core places, "
    "class medians for the other sea places (TEMPORARY) and 1.3 for ground and air (TEMPORARY)",
    "per-gate regime data (ACP/SCA/Port authorities); berth and gate counts per terminal"))

_NEW.append(_entry(
    "convoy_direction_weights",
    "weight(dir) = baseline inbound flow of the place arriving from that side / total modelled inbound; "
    "side = sender latitude below (northbound) or above (southbound) the place; equal weights if the "
    "place has no modelled inbound leg",
    "dimensionless",
    "profile_d.ProfileD.attach: the mix of convoy directions a wait is quantised over",
    "the aggregate engine has one queue per place; the directions' share of units decides which "
    "convoy interval applies",
    "CONVENTION", "latitude test on graph coordinates; inbound shares from the graph legs",
    "directional transit counts (PortWatch publishes them per direction for some chokepoints)"))

_OVERRIDE["backpressure"] = _entry(
    "backpressure",
    "soft queue_cap with spillback at EVERY place (default gate); profile d also holds EXOGENOUS "
    "through-traffic above the anchorage: when a place is at its queue_cap it accepts only its "
    "service rate, the refused exogenous flow is held outside the place (counted in its wait), "
    "released into the spare service rate (mu_max - arrivals) once the place accepts again", "rule",
    "engine.py (profile d): exogenous arrivals of a place are scaled by its acceptance factor like "
    "modelled legs; the held stock Xh is part of the backlog the reported wait is computed on",
    "in rev 2 the 23.6 vessels/day of Panama through-traffic that no modelled leg accounts for were "
    "never refused, so Panama's queue grew to 2,019 vessel-days against a queue_cap of 150; "
    "the anchorage is a physical bound and the excess waits upstream, nothing is discarded",
    "CONVENTION", "conservation identity, extended to exogenous traffic",
    "yard and anchorage occupancy model; AIS vessels-at-anchor counts")

# ============================= 3. ROUTE CHOICE ================================
_OVERRIDE["route_choice_rule"] = _entry(
    "route_choice_rule",
    "advantage = forecast wait on the primary (the unbooked wait at a slot-booked gate) - (added days + "
    "forecast wait on the alternative); a flow fraction f = 2 x sigmoid(advantage / s) - 1 diverts when "
    "advantage > 0, none otherwise; diverted flow <= alternative headroom", "rule",
    "engine.py (profile d): the lane request is scaled by f (and by the unbooked share at a slot-booked "
    "gate); rev 2 diverted ALL of it whenever the advantage was positive",
    "carriers differ in cost and commitments, so the response to a time advantage is graded, not a step; "
    "the form is zero at no advantage and tends to one as the advantage grows",
    "TEMPORARY", "wait-vs-divert shape; logistic form is a modelling choice",
    "routes table of ordered legs per origin-destination pair with observed diversion shares")

_NEW.append(_entry(
    "diversion_logistic", {"scale_d": 2.0}, "days",
    "engine.py (profile d): s in f = 2 x sigmoid(advantage / s) - 1",
    "sets how fast the diverted share rises with the time advantage (f = 0.46 at one scale, 0.76 at two)",
    "TEMPORARY",
    "assumption; no sourced elasticity of container routing to expected delay was found. Fixed before any "
    "replay run (pre-registered), not tuned",
    "observed share of Asia-US East Coast capacity moved Panama -> Suez / Cape per day of waiting "
    "(AIS service-level data)", [0.25, 10.0]))

_NEW.append(_entry(
    "alternative_routes",
    {"rule": "for a graph leg p -> q that selected services sail through gate g (a chokepoint with a "
             "network passage), the alternative is a sidecar lane that AVOIDS g's passage; its lane "
             "node is the chokepoint that lane passes and the primary lane does not (the one nearest "
             "the primary route); added days = (alternative nm - primary nm) / speed. A gate in "
             "unconditional_gates (the rev-2 behaviour) uses its named node and the lane that also avoids "
             "the other interoceanic canal; any other gate applies only when a selected service sails an "
             "origin-destination pair (a, q) through the lane node and not through g with a in the same "
             "sea zone as p",
     "unconditional_gates": {"chk_suez": "chk_capeofgoodhope", "chk_babelmandeb": "chk_capeofgoodhope"},
     "unconditional_avoid_suffix": "+canals",
     "exogenous_traffic_uses": "unconditional gates only",
     "pairs": "CORE places (the sidecar holds avoid-the-gate lanes for CORE pairs only)"},
    "rule",
    "routes_d.build_routes_table_d: which alternative lane a gate failure can use",
    "a queue can only be left if a real alternative route exists. In rev 2 only Suez and Bab "
    "el-Mandeb had one (Cape) and Panama had none, so a long Panama cut queued for ever. "
    "The network gives the avoid-the-gate lane for every gate with a passage; a service witness is "
    "required before a conditional alternative is used",
    "SOURCED",
    "sea_route_network (lanes). Unconditional gates are the rev-2 behaviour (Cape default, MODELLED "
    "basis). Witness for Panama -> Suez: svc_the22_ec4 and svc_the22_ec5 in data/graph/services.json "
    "sail Asia -> US East Coast through Suez (THE Alliance 2022 release, Hapag-Lloyd via Hellenic "
    "Shipping News https://www.hellenicshippingnews.com/?p=911712, retrieved 2026-10-25). NOT sourced: "
    "that these rotations serve the same origin-destination pairs the routes table puts on Panama "
    "(Shanghai, Ningbo, Busan -> New York); the zone-level match is MODELLED and is reported as such. "
    "The observed +7 to 10 d of the Panama replay was not used to choose the rule",
    "a routes table of ordered legs per origin-destination pair with alternatives (carrier network data)"))

_NEW.append(_entry(
    "port_substitution",
    {"rule": "for consecutive mapped calls u -> b -> c of a service rotation (all 129 entries of "
             "services.json; the call order is SOURCED whatever the routing basis) with c a sea "
             "port, the leg u -> b has the alternative 'discharge at c': an A lane u -> c (lane "
             "distance / speed) and a B lane c -> b (feeder at lane distance / speed), applied under "
             "the same logistic demand response and limited by c's headroom",
     "max_feeder_nm": 400.0, "exogenous_traffic": False},
    "rule / nautical miles",
    "routes_d.build_routes_table_d (substitution lanes); engine reroute machinery (A and B lanes)",
    "route choice must not stop at chokepoints: a vessel that sails u -> b -> c can discharge b's "
    "cargo at c when b is congested, the next call of the very same service",
    "TEMPORARY",
    "the existence of the adjacent call is SOURCED (rotation order); that cargo for b is "
    "substitutable at c, the distance limit and the feeder as a sea leg are MODELLED assumptions "
    "(max_feeder_nm is roughly one regional hop)",
    "port-pair substitution shares from trade statistics; hinterland transfer cost and time",
    [50.0, 2000.0]))

# ===================== 4. ROTATION RECOVERY ====================================
_NEW.append(_entry(
    "rotation_coupling",
    "zeta(leg u->v) = share of the occurrences of u->v in ALL 129 entries of services.json whose "
    "vessels continue to a later call (not the last call of an open string; a loop continues); legs "
    "ending at a chokepoint have zeta = 1; a chain's last leg inherits the replaced leg's zeta; "
    "legs no service uses have zeta = 0",
    "dimensionless",
    "rotation.py (zeta per leg), engine.py (profile d): the part of a place's inbound deviation that is "
    "carried by continuing vessels",
    "a vessel that arrives late at a call leaves late for the next call, whatever stock the port holds; "
    "the engine's cover otherwise lets a port keep shipping regardless of what arrives",
    "CONVENTION",
    "services sidecar rotations (SOURCED order of calls; era-independent, so every entry is used, "
    "including the 21 entries flagged webfetch_not_machine_verified and the entries with unstated "
    "routing - frequency is null in most entries and is NOT used)",
    "frequency and vessel count per service (the sidecar leaves them null unless the source states them)"))

_NEW.append(_entry(
    "rotation_recovery", {"absorbed_fraction_per_call": 0.3}, "fraction of a late arrival",
    "engine.py (profile d): at a place in its cover regime the coupled departures follow the coupled "
    "arrivals: a deficit passes on as (1 - absorbed) x deficit, a surplus (bunching) passes on in full",
    "schedules carry slack and ships speed up; part of a delay is recovered at each call",
    "TEMPORARY",
    "assumption. Context only (not a derivation): Sea-Intelligence Global Liner Performance reports "
    "global schedule reliability of 51 to 63 percent and an average delay of late arrivals of 5.3 to "
    "6.1 days (2024 to 2026, via Container News https://container-news.com/global-schedule-reliability-"
    "falls-to-56-4-in-july/ and FreshPlaza, retrieved 2026-10-25); neither gives a recovery fraction",
    "per-call arrival-delay vs departure-delay pairs from AIS / schedule data (Sea-Intelligence, Drewry)",
    [0.0, 1.0]))

# ========================= 5. TIER WALL =======================================
_NEW.append(_entry(
    "tier_wall",
    {"rule": "the engine runs on the whole graph (CORE and SHELL); a disruption must be at a CORE place; "
             "SHELL places route, queue and propagate but emit no chain event, no cost leaf and no WHYNOT "
             "row; a CORE event whose cause passed through SHELL places is parented to its nearest CORE "
             "ancestor in the hit chain"},
    "rule",
    "engine.py (profile d): _emit, run (root check), _derive_not_hit",
    "SHELL places are routing-grade and never feed scorecard or backtest claims; "
    "routes through them still carry flow",
    "CONVENTION", "engine.core_subgraph docstring (tier wall)",
    "n/a"))


def _compose() -> list:
    out = []
    for p in CAL_PLUGINS:
        out.append(dict(_OVERRIDE.get(p["name"], p)))
    out.extend(dict(p) for p in _NEW)
    return out


D_PLUGINS = _compose()
D_BY_NAME = {p["name"]: p for p in D_PLUGINS}
CHANGED = sorted(set(_OVERRIDE) | {p["name"] for p in _NEW})


def D_PLUG(name):
    return D_BY_NAME[name]["value"]


def registry() -> dict:
    return {"engine_rev": ENGINE_REV, "label": PROFILE_LABEL, "profile": PROFILE,
            "version": D_VERSION, "based_on": {"profile": "rev2c", "version": CAL_VERSION},
            "plugins": D_PLUGINS}


D_HASH = sha256_hex(canonical_json(registry()))
