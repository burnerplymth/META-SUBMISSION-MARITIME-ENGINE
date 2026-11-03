"""ENGINE REV 2 plug-in registry (machine-readable).

Every number in server/engine2 that is NOT a physical or accounting
identity lives here, with: name, value, unit, where it is used, why it is
needed, its status, its source, and what real data would replace it.

Status vocabulary:
  SOURCED    value taken from a cited dataset / a fixed interface rule
  CAL        calibrated from pinned repo data by a stated derivation
  TEMPORARY  behavioural / placeholder assumption, to be replaced
  NUMERICAL  discretisation or solver guard, not a model claim
  CONVENTION modelling convention inherited from the graph / interfaces

Identities (conservation of cargo at a place, queue stock balance
dQ/dt = lambda - mu, wait = Q / mu, leg = FIFO delay) are NOT plug-ins and
are not listed. There is no RNG anywhere in engine2.

The registry hash (canonical-JSON SHA-256) is stamped on every ENGINE REV 2
run record and is part of the rev-2 run id, so a stored run is always
re-derivable against the exact plug-in values that produced it.
"""
from __future__ import annotations

from engine.canonical import canonical_json, sha256_hex
from engine.constants import const

ENGINE_REV = 2
ENGINE_LABEL = "ENGINE REV 2"
REGISTRY_VERSION = "2.0.0"

# Fixed derivation of teu_per_call (see CAL entry): median over the 77
# core ports covered by data/graph/portwatch_join_input.json of
#   graph capacity (TEU/day) / (PortWatch vessel_count_container / 365).
# Reproduced by plugins.derive_teu_per_call(); the fixed constant keeps
# engine2 free of file reads.
TEU_PER_CALL_FROZEN = 2359.35

_P = []


def _add(name, value, unit, where, why, status, source, replace_with, bounds=None):
    _P.append({"name": name, "value": value, "unit": unit, "where_used": where,
               "why_needed": why, "status": status, "source": source,
               "replace_with": replace_with, "bounds": bounds})


_add("teu_per_call", TEU_PER_CALL_FROZEN, "TEU per vessel call",
     "engine.py Engine2._build: every sea place whose capacity_unit is TEU/day "
     "is expressed in vessel-calls/day = TEU/day / teu_per_call, the unit "
     "chokepoint capacities already use (vessels/day)",
     "removes engine v1's mixed-unit sum, where vessels/day and TEU/day were added "
     "as if comparable. One sea unit per layer.",
     "CAL",
     "median of graph capacity / (PortWatch vessel_count_container / 365) over "
     "77 core ports in data/graph/portwatch_join_input.json (IQR 1568-3345); "
     "the PortWatch reference period is taken as one year (UNVERIFIED), and "
     "chokepoint transits count all vessel classes while the port counts are "
     "container-only (population mismatch, not corrected)",
     "per-port TEU per call from AIS vessel size classes (PortWatch daily "
     "series by vessel type); chokepoint container-transit counts",
     [500.0, 10000.0])

_add("mu_max_ratio", 1.3, "service rate / baseline load (dimensionless)",
     "engine.py: mu_max = ratio x lambda0 at every place; spare service "
     "rate = mu_max - lambda0",
     "graph capacity is a throughput proxy (annual/365), so it equals the "
     "baseline load lambda0; a queue only drains if the demonstrated peak "
     "service rate exceeds it. v1 invented 0.85 utilisation for the same job.",
     "TEMPORARY",
     "assumed default of 1.3 (modest headroom over baseline load); "
     "one Ever Given consistency check is possible, none is claimed",
     "per-place demonstrated peak service rate: 99th percentile of the "
     "PortWatch daily series / its mean",
     [1.0, 3.0])

_add("time_step_d", {"short": 0.25, "medium": 0.5, "long": 1.0,
                     "short_max_horizon_d": 120.0, "medium_max_horizon_d": 600.0},
     "days",
     "engine.py Engine2.run: fluid-network time step, chosen once per run "
     "from the declared disruption duration (and kept for forks of that run)",
     "the network is advanced in fixed steps; leg lags are round(transit/step). "
     "Coarser steps for long scenarios keep run cost bounded.",
     "NUMERICAL", "solver choice, not a model claim",
     "event-exact stepping between rate changes")

_add("layer_equivalence", "ratio of layer baseline totals", "dimensionless",
     "engine.py Engine2._build: cargo moved across a layer boundary "
     "(sea<->ground<->air legs) is converted by g = total(lambda0 of the "
     "receiving layer) / total(lambda0 of the sending layer)",
     "sea (vessel-calls), ground (trucks) and air (tonnes) share no unit; "
     "cross-layer legs need some equivalence or they cannot be added",
     "TEMPORARY",
     "derived from the graph's own capacities; asserts the layers carry equal "
     "total cargo, which is wrong in general",
     "tonnes per vessel call / per truck / per air movement by commodity class "
     "(UN Comtrade weights, UNCTAD, airline statistics)")

_add("inbound_attribution", "omega_e = share_e x lambda0_from x g / sum over inbound legs",
     "dimensionless",
     "engine.py: the fraction of a place's arrivals supplied through each "
     "primary inbound leg; V_e = omega_e x lambda0_to is the cargo the leg "
     "supplies in the receiver's scale",
     "the graph has legs with outbound shares but no origin-destination "
     "matrix, so arrivals cannot be traced leg by leg in absolute terms",
     "CONVENTION",
     "inherited from engine v1 (child dependence = leg flow / modelled inbound "
     "flow), now computed in one unit per layer",
     "UN Comtrade bilateral flows / PortWatch port-to-port flows per leg")

_add("inventory_days_cover", "graph field inventory_days", "days of baseline load",
     "engine.py: buffer stock S0 = inventory_days x lambda0 at every place; "
     "while stock remains the place keeps shipping at lambda0 although arrivals fall",
     "inventory is a stock balance; the cover the stock starts with must come "
     "from somewhere",
     "TEMPORARY",
     "graph field (MODELED): 7 d for every port, 5 d ground, 3 d air, 0 d "
     "chokepoints; a placeholder",
     "US Census inventory-to-sales ratios / Eurostat per commodity class, "
     "per-commodity stocks")

_add("no_restock", True, "boolean",
     "engine.py: a drawn-down buffer is not refilled after the disruption",
     "refill needs a surplus supply rate, which the graph does not carry; "
     "omitted rather than invented",
     "TEMPORARY", "documented limitation",
     "replenishment policy per commodity class")

_add("route_choice_rule", "divert iff added days + forecast wait on the "
     "alternative < forecast wait on the primary; diverted flow <= alternative headroom",
     "rule",
     "engine.py: bypass legs (graph bypass_of) are the routes table; flow "
     "blocked at a place is reassigned to the bypass when it is faster, up to "
     "mu_max - lambda0 - already committed diversions at the receiving place",
     "which alternative carriers choose has no identity; shortest forecast "
     "generalised time is the standard choice",
     "TEMPORARY",
     "same wait-vs-divert shape as the simulator's reroute rule",
     "routes table of ordered legs per origin-destination pair with added days "
     "= route length difference (needs a sea-lane network)")

_add("route_foresight", "carriers know the declared duration", "boolean",
     "engine.py: forecast wait at an impaired place = remaining declared "
     "impairment + backlog / mu_max",
     "the diversion decision needs an expected wait",
     "TEMPORARY", "use the event's declared duration",
     "an expectation model (open-ended events divert)")

_add("bypass_added_days", "authored bypass transit minus primary transit",
     "days",
     "engine.py: added days of a bypass lane = base_transit_d(bypass) - "
     "base_transit_d(primary); the graph states a bypass connects the same "
     "broader O/D pair",
     "route length difference must come from the graph",
     "CONVENTION",
     "graph bypass legs: 5 authored legs only (through-services absent)",
     "routes table derived from a sea-lane network")

_add("hit_rule", {"hit_threshold": const("hit_threshold_default"),
                  "delay_floor_d": const("delay_hit_floor_d"),
                  "min_material_share": const("min_material_share")},
     "shortfall fraction / days / fraction",
     "engine.py: when a place earns an event (observation threshold, "
     "not physics)",
     "the event stream reports first-hit places; something must say when an effect is large "
     "enough to report",
     "SOURCED",
     "same values as engine v1's registry (hit "
     "rule; min_material_share is a v1 ASSUMPTION)",
     "backtest-tuned thresholds under a fixed protocol")

_add("queue_sensitivity", "multiplier on reported queue wait", "dimensionless",
     "engine.py: delay_d = queue_sensitivity x FIFO wait. It does NOT change "
     "the dynamics.",
     "the run API parameter must stay accepted; wait itself is an identity",
     "CONVENTION", "run API params", "retire when the run API drops the parameter")

_add("cost_reference_d", const("cost_delay_scale_d"), "days",
     "engine.py: cost indicator c = (queue + cover-deficit stock-days at a "
     "place) / (lambda0 x reference days), clipped to [0,1]; a cost leaf is "
     "emitted at min_material_share",
     "makes the stock-days dimensionless; never currency (market quarantine)",
     "TEMPORARY",
     "v1 registry cost_delay_scale_d (ASSUMPTION)",
     "reference horizon from carrying-cost-free inventory-in-motion baselines")

_add("cost_in_process_days", 1.0, "days of baseline throughput",
     "engine.py: baseline stock of a place for the cost indicator = "
     "inventory cover + this many days of throughput in process",
     "gives a place with no cover (a chokepoint) a non-zero baseline stock "
     "so the stock-days ratio is defined",
     "TEMPORARY", "ASSUMPTION (one day of in-process stock)",
     "baseline pipeline stock from leg lengths x flows (Little)")

_add("mode_shift", {"time_sensitive_fraction": 0.02, "trigger_wait_d": 7.0,
                    "tonnes_per_teu": 10.0},
     "fraction / days / tonnes",
     "engine.py: from a sea port with an air leg, shifted tonnes/day = "
     "fraction x arrivals (TEU/day) x tonnes_per_teu once the port wait "
     "exceeds the trigger, capped by the air hub's headroom mu_max - lambda0",
     "mode choice under delay has no identity; headroom bound is the real limit",
     "TEMPORARY",
     "assumed defaults (fraction 2 %, trigger 7 d; "
     "tonnes_per_teu is an UNVERIFIED recall)",
     "air-vs-vessel value share by HS chapter (Eurostat / US Census), "
     "commodity weights")

_add("inventory_delay_definition", "supply gap = days onward flow stayed cut by >= the hit "
     "threshold (+ queue wait)", "days",
     "engine.py emission: delay_d of an inventory event",
     "a stockout has no queue wait; the cargo that did not leave arrives late "
     "by about the length of the cut, which is what delay_d reports",
     "CONVENTION", "definition (delay_d is days late)",
     "per-commodity delivery-lateness distribution")

_add("unrouted_diversion_sink", "diverted cargo not matched by a bypass lane is consumed at "
     "the receiving place", "rule",
     "engine.py: extra cargo at a bypass place beyond what its lanes restore leaves the model",
     "the graph has 5 authored bypass legs and no routes table, so the onward "
     "route of most diverted cargo is unknown; it is not forwarded and is "
     "counted as consumed at that place",
     "TEMPORARY", "documented limitation (through-services absent)",
     "routes table of ordered legs per origin-destination pair")

_add("backpressure", "soft queue_cap with spillback", "rule",
     "engine.py: above queue_cap a place accepts only mu; the refused flow "
     "stays at the sending place (its queue grows). Nothing is discarded.",
     "queue_cap is an anchorage size, not a sink; conservation requires the "
     "excess to be held somewhere",
     "CONVENTION", "conservation identity",
     "yard and anchorage occupancy model")

_add("event_ordering", "events sorted by (step, kind, place id); a parent must "
     "be hit at an earlier step", "rule",
     "engine.py emission pass",
     "gapless seq, parent.seq < seq, parent.at < at (event DAG invariants)",
     "CONVENTION", "event stream DAG rules", "n/a")

PLUGINS = list(_P)
PLUGIN_BY_NAME = {p["name"]: p for p in PLUGINS}


def plug(name):
    return PLUGIN_BY_NAME[name]["value"]


def registry() -> dict:
    return {"engine_rev": ENGINE_REV, "label": ENGINE_LABEL,
            "version": REGISTRY_VERSION, "plugins": PLUGINS}


PLUGINS_HASH = sha256_hex(canonical_json(registry()))


def derive_teu_per_call(graph_path, join_path) -> float:
    """Reproduce TEU_PER_CALL_FROZEN from the pinned repo files."""
    import json
    import statistics
    g = json.load(open(graph_path, encoding="utf-8"))
    cap = {n["id"]: n["capacity"] for n in g["nodes"]
           if n.get("tier", "core") == "core" and n["kind"] == "port"}
    calls: dict = {}
    for r in json.load(open(join_path, encoding="utf-8"))["records"]:
        if r["node_id"] in cap:
            calls[r["node_id"]] = calls.get(r["node_id"], 0) + r["vessel_count_container"]
    rat = [cap[n] / (c / 365.0) for n, c in calls.items() if c > 0]
    return round(statistics.median(rat), 2)
