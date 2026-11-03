"""Lane derivation — pure function of
``(graph, seed, window)``.

Everything here is MODELED and labelled so: headway bands come from the
edge's ``capacity_share`` quartile *within its mode* (sea
{12, 24, 48, 96} h, air {4, 8, 16, 32} h — top quartile gets the
shortest headway); speed classes come from the origin node's capacity
quartile in-mode (sea {16, 19, 21, 23} kn, air {760, 820, 860, 900}
km/h). No external timetable is claimed — the derivation block in the
run record prints the basis and the graph's SOURCED/MODELED edge split.

Schedules and dwells are drawn from ONE ``random.Random(seed)`` in the
canonical order: lanes processed sorted by ``(mode, from, to)``;
within a lane, the first-departure offset then per-interval jitters in
schedule order; dwell draws afterwards at entity creation in
entity-id order. Any other draw order produces a different run — the
order is part of the derivation contract.

Land crossing: each lane's land-crossing fraction is the share
of its great-circle samples falling over land per the bundled Natural
Earth 110 m polygons (``data/ne_land_110m.geojson`` — a vendored copy
of the web coastline underlay artefact, Natural Earth via world-atlas
v2.0.2, public domain). A lane with fraction > 10% whose endpoints are
joined by a graph path of <= 3 hops with a lower combined fraction is
rendered via that path (``rendered_via``, basis DISPLAY ROUTING) and
its nominal duration is computed on the rendered path's length; the
direct distance is recorded alongside. Otherwise the arc stands and
the fraction is disclosed in the lane record.

Tier-aware derivation: the graph is
TIERED (each place carries ``tier: "core" | "shell"``). The rules
implemented here, all deterministic functions of the graph record:

- Tier contract: NO tier value anywhere -> the legacy all-CORE graph,
  derived as for an all-CORE graph. ANY tier value present + a place missing it
  (or carrying a value outside {"core", "shell"}) ->
  :class:`TierContractError`, surfaced at run creation — never a
  silent per-place default.
- Quartile purity: headway quartile bands and speed
  quartile classes are computed over CORE lanes / CORE places ONLY.
  Shell lanes and shell capacities never enter the populations.
- Shell-touching lanes are MODELLED by rule: frequency =
  regional inheritance (the shell place's served ``region`` field when
  the graph carries one, else the region of the nearest CORE place in
  the same mode by great-circle distance — on the shipped graph NO
  place carries a region field, so the nearest-CORE form is what
  runs; a region's population is the CORE lanes incident to that
  region / CORE place, falling back to the mode-global median CORE
  band); speed = the mode-median CORE speed class. Frequency
  provenance prints MODELLED (regional inheritance) even when the
  lane's existence/share is SOURCED. Observed activity, where the
  graph serves a numeric value, may shift the band one step against
  the mode's shell-activity median — recorded per lane.
- Shell-shell lanes with NO observed activity at either endpoint get
  NO discrete entities (lane + flow only); with observed activity
  they spawn at the lightest headway band. On the shipped graph.json
  NO place carries an activity field (activity lives only in the
  shell census artefact, which derivation never reads), so every
  shell-shell lane is in the no-entity class there.
- Budget: CORE-first; shell-touching lanes are capped
  at 25% of the run's entity total (estimate basis), coarsened first.
- Statistics scope: derivation carries the tier split
  (places/routes/entities by tier class) and ``statistics_scope:
  "CORE"``; the twin delay aggregates are filtered to CORE in
  runner.py on this basis.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

try:
    from engine.canonical import canonical_json, sha256_hex
except Exception:  # imported as server.sim.* from the repo root instead
    from server.engine.canonical import canonical_json, sha256_hex  # type: ignore

from .geometry import gc_distance_km, path_length_km, slerp_samples

HEADWAYS_H = {"sea": (12.0, 24.0, 48.0, 96.0), "air": (4.0, 8.0, 16.0, 32.0)}
# Indexed by quartile band 0 (top) .. 3 (bottom): the top capacity
# quartile gets the fastest class — sea {23, 21, 19, 16} kn, air
# {900, 860, 820, 760} km/h (fastest first).
SPEEDS = {"sea": (23.0, 21.0, 19.0, 16.0), "air": (900.0, 860.0, 820.0, 760.0)}
SPEED_UNIT = {"sea": "kn", "air": "km/h"}
KN_TO_KMH = 1.852
DWELL_BAND_H = {"sea": (12.0, 36.0), "air": (2.0, 6.0)}
LAND_THRESHOLD = 0.10
MAX_RENDER_HOPS = 3
# State-payload budget: the roster cap is 6,000
# entities; derivation coarsens headways (documented order:
# shell-touching lanes first, then longest-headway core lanes) rather
# than truncating the roster silently when a window would exceed it.
MAX_ENTITIES = 6000
# Shell sub-budget: shell-touching lanes share at most this
# fraction of the roster cap; the shell coarsening phase targets it.
SHELL_SUB_BUDGET_FRACTION = 0.25
# Transit paths: the shortest path over network legs by
# great-circle distance (Dijkstra, deterministic tie-break by place
# id), accepted when its length <= TRANSIT_ACCEPT_RATIO x the lane's
# direct great-circle distance; otherwise the lane sails direct.
# The lane's OWN direct leg is excluded from its search — with it
# included, the direct leg always wins by the triangle inequality and
# no lane would ever transit anything.
TRANSIT_ACCEPT_RATIO = 1.5
# Transits: chokepoint places ON the sailed movement polyline —
# a transit-path node, a rendered-via node of a transit leg, or a
# chokepoint whose point lies within this corridor of the polyline
# (the leg King Abdullah Port -> Port Said sails THROUGH the Suez
# Canal zone while bracketing the chk_suez node; a pure node test
# measures zero Suez transits on this graph — corridor membership is
# the geographically honest reading, MODELLED constant).
TRANSIT_CORRIDOR_KM = 100.0
# MODELLED: a leg's rendered path counts as its sailed
# path only up to this multiple of the leg's direct length (see
# movement_geometry's leg_sailed for the measured artefact classes:
# genuine curated detours <= ~2.2x, display artefacts 16x-4,000x).
MOVEMENT_RENDERED_MAX_RATIO = 2.5

# --- AIR module config ----
# Every constant of the air module lives here; simulate.py imports
# them, nothing is re-stated elsewhere. All values are MODELLED.
# Class split: the graph's air capacities are freight-based
# (tonnes/day, ACI cargo census on the shipped graph), so the places
# in the TOP QUARTILE of served air capacity among CORE air places
# are the freight/integrator hubs. A lane touching one is
# cargo-dominant: this share of its departures are cargo aircraft
# (integrator pattern). Every other air lane is passenger-dominant:
# this share of its departures are cargo (belly freight on passenger
# aircraft); passenger aircraft contribute belly tonnes only through
# this share — never merged with TEU, never converted.
AIR_CARGO_LANE_CARGO_SHARE = 0.80
AIR_PAX_LANE_CARGO_SHARE = 0.15
# Turnarounds, replacing the generic air dwell band for air
# entities: passenger tight; cargo sort at integrator hubs; cargo
# elsewhere. Nominal (schedule) turnaround = the band MAXIMUM, so the
# drawn actual turnaround leaves absorption slack in every slot.
AIR_TA_PAX_H = (0.75, 1.5)
AIR_TA_CARGO_HUB_H = (2.0, 4.0)
AIR_TA_CARGO_OTHER_H = (1.0, 2.0)
# Passenger banks: scheduled departures from an aircraft's
# home hub quantise UP to the next bank window in the place's local
# time; local time = UTC offset from longitude (MODELLED proxy:
# offset_h = round(lon / 15)). Cargo night sorts: scheduled
# departures from integrator hubs quantise UP to the next evening
# wave (local hour below, daily).
AIR_BANK_LOCAL_HOURS = (7, 10, 13, 16, 19, 22)
AIR_CARGO_WAVE_LOCAL_HOUR = 21
# Air diversion: nearest OPEN place within this range; else
# the aircraft holds airborne for AIR_DIVERT_HOLD_S, then diverts to
# the nearest open place regardless of range (deterministic).
AIR_DIVERT_RANGE_KM = 3000.0
AIR_DIVERT_HOLD_S = 2.0 * 3600.0
# Storm cell: the cell's stated in-cell conditions
# fed through the weather factor formulas (MODELLED).
STORM_CELL_WIND_KN = 50.0
STORM_CELL_WAVE_M = 6.0

# Interpretation of the SERVED disruption-kind vocabulary.
# The vocabulary is DISRUPTION_KINDS in server/engine/engine.py,
# identical to the classifier's VALID_KINDS (server/llm/classifier.py)
# — read from the code:
# {closure, capacity_cut, congestion, strike, weather, grounding,
# accident, policy, other}. There is NO ground-stop / ground-delay-
# programme kind in the served vocabulary — the gap is recorded in
# the table, never papered over. The sim keys ALL behaviour on
# capacity_loss (closure coerced to 1.0 upstream);
# the per-kind rows state what that means per mode.
AIR_KIND_MAPPING = [
    {"kind": "closure", "loss": "1.0 (coerced, closure rule)",
     "sea": "departures held; arrivals queue; transit chokepoints "
            "DIVERT or HOLD",
     "air": "departures held; arrivals DIVERT to the nearest open "
            "place; rotation resumes from the diversion place"},
    {"kind": "capacity_cut", "loss": "as served (0, 1]",
     "sea": "berth scaling + approach slow on partial loss",
     "air": "gate (berth) scaling + approach slow on partial loss"},
    {"kind": "congestion", "loss": "as served (default 0.5)",
     "sea": "berth scaling + approach slow (as capacity_cut)",
     "air": "gate scaling + approach slow (as capacity_cut)"},
    {"kind": "strike", "loss": "as served (default 0.5)",
     "sea": "berth scaling + approach slow (as capacity_cut)",
     "air": "gate scaling + approach slow (as capacity_cut)"},
    {"kind": "weather", "loss": "as served (default 0.5)",
     "sea": "berth scaling + approach slow; NOT the pinned weather snapshot "
            "(a separate input)",
     "air": "gate scaling + approach slow; NOT the pinned weather snapshot "
            "(a separate input)"},
    {"kind": "grounding", "loss": "as served (default 0.5)",
     "sea": "berth scaling by capacity_loss only — no distinct sim "
            "semantics", "air": "gate scaling by capacity_loss only"},
    {"kind": "accident", "loss": "as served (default 0.5)",
     "sea": "berth scaling by capacity_loss only — no distinct sim "
            "semantics", "air": "gate scaling by capacity_loss only"},
    {"kind": "policy", "loss": "as served (default 0.5)",
     "sea": "berth scaling by capacity_loss only — no distinct sim "
            "semantics", "air": "gate scaling by capacity_loss only"},
    {"kind": "other", "loss": "as served (default 0.5)",
     "sea": "berth scaling by capacity_loss only — no distinct sim "
            "semantics", "air": "gate scaling by capacity_loss only"},
    {"kind": "ground_stop / ground_delay_programme",
     "loss": "— ABSENT from the served vocabulary (GAP)",
     "sea": "n/a",
     "air": "NOT EXPRESSIBLE as served: no ground-stop/GDP kind "
            "exists in DISRUPTION_KINDS / classifier VALID_KINDS. "
            "Closest served form is closure on the air place, which "
            "holds departures exactly as a ground stop would, but "
            "DIVERTS arrivals where a true ground stop would let "
            "them continue — the difference is stated, not hidden. "
            "FIFO programme release has no served counterpart."},
    {"kind": "airspace_closure (sim-native)",
     "loss": "places inside the circle: 1.0 for the window",
     "sea": "places inside the circle are closed for the window "
            "(all modes — the construct names places, not airports)",
     "air": "legs whose great-circle path intersects the circle "
            "reroute around it (tangent waypoints, duration rescaled "
            "by path-length ratio); places inside closed. MODELLED, "
            "no chain-engine counterpart; /sim/run input only"},
    {"kind": "storm_cell (sim-native)",
     "loss": "— (weather factors)",
     "sea": "places/legs within the cell radius at time t take the "
            "weather factors at the cell's stated conditions "
            f"(wind {STORM_CELL_WIND_KN:g} kn, wave "
            f"{STORM_CELL_WAVE_M:g} m)",
     "air": "legs within the cell radius at time t take the air "
            "weather factor at the cell's stated wind (full headwind). "
            "MODELLED; distinct from the pinned weather snapshot"},
]


def local_offset_h(lon: float) -> int:
    """MODELLED local-time proxy: UTC offset from longitude."""
    return int(round(float(lon) / 15.0))


def _next_local_hour_epoch(t: float, lon: float, hours: tuple) -> float:
    """Smallest epoch >= t whose local time (longitude proxy) is
    exactly one of ``hours``:00. Deterministic arithmetic, no table."""
    off = local_offset_h(lon) * 3600.0
    local = t + off
    day = math.floor(local / 86400.0) * 86400.0
    cands = [day + h * 3600.0 for h in hours]
    cands.append(day + 86400.0 + min(hours) * 3600.0)
    nxt = min(c for c in cands if c >= local - 1e-9)
    return nxt - off


def next_bank_epoch(t: float, lon: float) -> float:
    """Next passenger bank window at a place."""
    return _next_local_hour_epoch(t, lon, AIR_BANK_LOCAL_HOURS)


def next_cargo_wave_epoch(t: float, lon: float) -> float:
    """Next cargo evening-sort wave at a place."""
    return _next_local_hour_epoch(t, lon, (AIR_CARGO_WAVE_LOCAL_HOUR,))


def air_nominal_turnaround_h(cls: str, at_integrator_hub: bool) -> float:
    """Schedule (nominal) turnaround = the class band maximum — the
    slack between nominal and drawn actual turnaround is what
    absorbs lateness leg by leg."""
    if cls == "cargo":
        return AIR_TA_CARGO_HUB_H[1] if at_integrator_hub \
            else AIR_TA_CARGO_OTHER_H[1]
    return AIR_TA_PAX_H[1]


# --- Generated vehicle identity (MODELLED) ----------------------------
# SIM entities carry generated identity so vehicles can be picked and
# inspected in the UI. Identity is a PURE function of the entity id's
# sha256 — the _build_rotation precedent: hash-derived, deterministic,
# and consuming NO draw from the seeded RNG stream, so the draw
# order/count is untouched and every pre-identity figure (sea figures
# in particular) stays bit-identical. Everything here is MODELLED and
# synthetic: names are assembled from the part tables below, the class
# split follows MODELLED priors, and the size figures are per-class
# prior RANGES — never point values, never a real vessel's or
# aircraft's particulars.

# Real, famous vessel names the generator must never reproduce. The
# part tables below contain NONE of these names' tokens, so no
# combination of parts can assemble one — collision is impossible by
# construction; sea_identity() additionally asserts against this list
# as a belt-and-braces check, and it is checked against thousands of synthetic
# ids.
FAMOUS_VESSEL_NAMES = frozenset({
    "EVER GIVEN", "EVER ACE", "SEAWISE GIANT", "TITANIC",
    "QUEEN MARY 2", "ICON OF THE SEAS", "MSC GULSUN",
    "CMA CGM JACQUES SAADE", "EMMA MAERSK", "HARMONY OF THE SEAS",
    "SYMPHONY OF THE SEAS", "WONDER OF THE SEAS", "PIONEER SPIRIT",
    "PRELUDE", "BARZAN", "AL MURAYKH", "HMM ALGECIRAS",
    "COSCO SHIPPING UNIVERSE", "OOCL SPAIN", "MADRID MAERSK",
})

# MODELLED class priors: relative weights for the deterministic
# weighted pick over the id hash — a plausible merchant-fleet mix
# (container / bulk / tanker dominant; roro and passenger rare).
VESSEL_CLASS_WEIGHTS = (
    ("container", 40), ("bulk", 25), ("tanker", 20),
    ("roro", 8), ("passenger", 7),
)

# MODELLED per-class size priors (ranges only). Capacity unit: TEU
# for container and roro, DWT for bulk and tanker. Passenger vessels
# carry NO capacity entry — their GT range suffices (a berth count
# would be overreach for a modelled prior).
VESSEL_PRIORS = {
    "container": {"loa_m": [250.0, 400.0], "beam_m": [48.0, 61.0],
                  "gt": [95000, 235000],
                  "capacity": {"unit": "TEU", "range": [10000, 24000]}},
    "bulk": {"loa_m": [180.0, 300.0], "beam_m": [32.0, 50.0],
             "gt": [30000, 120000],
             "capacity": {"unit": "DWT", "range": [35000, 210000]}},
    "tanker": {"loa_m": [180.0, 333.0], "beam_m": [32.0, 60.0],
               "gt": [30000, 160000],
               "capacity": {"unit": "DWT", "range": [40000, 320000]}},
    "roro": {"loa_m": [180.0, 265.0], "beam_m": [32.0, 40.0],
             "gt": [45000, 75000],
             "capacity": {"unit": "TEU", "range": [4000, 8500]}},
    "passenger": {"loa_m": [220.0, 365.0], "beam_m": [36.0, 66.0],
                  "gt": [70000, 250000]},
}

# Synthetic name-part tables per class (MODELLED): a vessel name is
# PREFIX + CORE, plus a SUFFIX when the id hash says so. No token in
# any table appears in FAMOUS_VESSEL_NAMES (see the denylist note).
VESSEL_NAME_PARTS = {
    "container": {
        "prefix": ["ASTRA", "MERIDIAN", "NORDSTAR", "PACIFICA",
                   "VELORA", "KESTREL", "SOLSTICE", "HALCYON"],
        "core": ["BRIDGE", "CROWN", "HARVEST", "LANTERN",
                 "SUMMIT", "VOYAGER", "ANCHOR", "COMPASS"],
        "suffix": ["EXPRESS", "TRADER", "STAR", "GLORY", "LINE"],
    },
    "bulk": {
        "prefix": ["IRONHOLD", "GRANITE", "HARROW", "DUNMORE",
                   "FIELDSTONE", "ASHFORD", "BLACKMOOR", "TARNHOLM"],
        "core": ["CLIFF", "MOOR", "VALE", "SHORE", "RIDGE", "HILL",
                 "FIELD", "HEATH"],
        "suffix": ["TRADER", "CARRIER", "STAR", "GLORY"],
    },
    "tanker": {
        "prefix": ["VELORA", "NORDIC", "ASTRA", "SOLENT",
                   "MARLIN", "PETREL", "HALCYON", "KESTREL"],
        "core": ["WAVE", "TIDE", "CURRENT", "SPRAY", "DEEP", "REEF",
                 "SURGE", "DRIFT"],
        "suffix": ["STAR", "GLORY", "TRADER", "VOYAGER"],
    },
    "roro": {
        "prefix": ["ASTRA", "MERIDIAN", "HIGHWAY", "ROADSTAR",
                   "VELORA", "NORDSTAR", "KESTREL", "HALCYON"],
        "core": ["ROAD", "BRIDGE", "GATE", "LINK", "CAUSEWAY",
                 "PASSAGE", "SPAN", "FERRY"],
        "suffix": ["EXPRESS", "STAR", "LINE", "VOYAGER"],
    },
    "passenger": {
        "prefix": ["AZURE", "CELESTIAL", "SERENE", "LUMINA",
                   "OCEANA", "VELORA", "ASTRA", "SOLSTICE"],
        "core": ["SKY", "DAWN", "HORIZON", "MELODY", "BREEZE",
                 "AURORA", "RADIANCE", "SERENADE"],
        "suffix": ["STAR", "GLORY", "VOYAGER", "LINE"],
    },
}


def _identity_hash(eid: str) -> int:
    """The entity id's identity hash (sha256 over the canonical
    ``{"identity_for": eid}`` — the _build_rotation pattern)."""
    return int(sha256_hex(canonical_json({"identity_for": eid}))[:16], 16)


def sea_identity(eid: str) -> dict:
    """Generated sea identity for one entity id (MODELLED): vessel
    class by deterministic weighted pick from the id hash, a
    synthetic name from the per-class part tables, and the class's
    size priors. Pure function of the id — no RNG draw."""
    h = _identity_hash(eid)
    total = sum(w for _, w in VESSEL_CLASS_WEIGHTS)
    pick = h % total
    vessel_class = VESSEL_CLASS_WEIGHTS[-1][0]
    for cls_name, weight in VESSEL_CLASS_WEIGHTS:
        if pick < weight:
            vessel_class = cls_name
            break
        pick -= weight
    parts = VESSEL_NAME_PARTS[vessel_class]
    words = [parts["prefix"][(h >> 8) % len(parts["prefix"])],
             parts["core"][(h >> 16) % len(parts["core"])]]
    if (h >> 24) % 2:
        words.append(parts["suffix"][(h >> 32) % len(parts["suffix"])])
    vessel_name = " ".join(words).title()
    # Denylist backstop: impossible by table design (no famous token
    # appears in any part table), asserted so a future table edit
    # that breaks the design fails loudly at derivation.
    assert vessel_name.upper() not in FAMOUS_VESSEL_NAMES, vessel_name
    priors = VESSEL_PRIORS[vessel_class]
    out_priors: dict = {"loa_m": list(priors["loa_m"]),
                        "beam_m": list(priors["beam_m"]),
                        "gt": list(priors["gt"])}
    if "capacity" in priors:
        cap = priors["capacity"]
        out_priors["capacity"] = {"unit": cap["unit"],
                                  "range": list(cap["range"])}
    return {"vessel_class": vessel_class, "vessel_name": vessel_name,
            "vessel_priors": out_priors}


def air_identity(eid: str, cls: str) -> dict:
    """Generated air identity for one entity id (MODELLED): a
    ``SIM-N#####`` tail number and an aircraft type — "freighter"
    for the cargo class, "widebody" / "narrowbody" by id hash for
    passenger. Pure function of the id — no RNG draw."""
    h = _identity_hash(eid)
    if cls == "cargo":
        aircraft_type = "freighter"
    else:
        aircraft_type = "widebody" if (h >> 8) % 2 else "narrowbody"
    return {"tail": f"SIM-N{h % 100000:05d}",
            "aircraft_type": aircraft_type}

_LAND_PATH = Path(__file__).resolve().parent / "data" / "ne_land_110m.geojson"
_land_polys_cache: list | None = None


def lane_key(mode: str, frm: str, to: str) -> str:
    return f"{mode}:{frm}->{to}"


# -- tier contract + observed activity -----------

class TierContractError(ValueError):
    """Tier-contract violation: the graph carries a tier value on
    at least one place while another place is missing its tier (or
    carries a value outside {"core", "shell"}). Raised by derive() so
    the failure surfaces at run creation — never a silent default."""


def resolve_tiers(nodes_list: list) -> tuple[dict, bool]:
    """(tiers by place id, tiered?) under the tier contract. No tier
    value anywhere -> every place is CORE (the legacy graph)."""
    served = {n["id"]: n.get("tier") for n in nodes_list}
    if all(v is None for v in served.values()):
        return {nid: "core" for nid in served}, False
    missing = sorted(nid for nid, v in served.items() if v is None)
    invalid = sorted((nid, repr(v)) for nid, v in served.items()
                     if v is not None and v not in ("core", "shell"))
    if missing or invalid:
        parts = []
        if missing:
            parts.append(f"{len(missing)} place(s) missing tier "
                         f"(first: {missing[:5]})")
        if invalid:
            parts.append(f"invalid tier value(s): {invalid[:5]}")
        raise TierContractError(
            "tiered graph violates the sim tier contract — "
            + "; ".join(parts))
    return dict(served), True


_ACTIVITY_NUMERIC_KEYS = ("vessel_calls", "movements", "calls",
                          "movements_per_period",
                          "vessel_calls_per_period", "calls_per_period")


def observed_activity_value(node: dict) -> float | None:
    """The place's served observed-activity magnitude, or None when
    the graph serves none. Accepts a numeric ``activity`` /
    ``observed_activity`` field, a numeric activity sub-field, or a
    dedicated calls/movements field — what's served, nothing else."""
    act = node.get("activity")
    if isinstance(act, (int, float)) and not isinstance(act, bool):
        return float(act)
    if isinstance(act, dict):
        for k in ("value",) + _ACTIVITY_NUMERIC_KEYS:
            v = act.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
    for k in ("observed_activity",) + _ACTIVITY_NUMERIC_KEYS:
        v = node.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def has_observed_activity(node: dict) -> bool:
    """Whether the graph record serves ANY observed activity for the
    place: a positive numeric magnitude, or a census-style presence
    record (a truthy ``activity`` object). The shipped graph.json
    serves neither on any place — see the module docstring."""
    value = observed_activity_value(node)
    if value is not None:
        return value > 0
    if node.get("activity"):
        return True
    return any(bool(node.get(k))
               for k in ("observed_activity",) + _ACTIVITY_NUMERIC_KEYS)


def _median_band(bands: list) -> int:
    """Lower median of a non-empty list of quartile band indices."""
    ordered = sorted(bands)
    return int(ordered[(len(ordered) - 1) // 2])


# -- Natural Earth land polygons ----------------------------------------

def load_land_polygons(path: str | Path | None = None) -> list:
    """Land polygons as ``[(bbox, [ring, ...]), ...]``.

    A ring is a list of (lon, lat) points; the first ring of a polygon
    is its outer boundary, the rest are holes (lakes). Bounding boxes
    prefilter the point tests — the full set is 125 polygons.
    """
    global _land_polys_cache
    if path is None and _land_polys_cache is not None:
        return _land_polys_cache
    doc = json.loads(Path(path or _LAND_PATH).read_text(encoding="utf-8"))
    polys: list = []
    for feat in doc.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("type") == "Polygon":
            raw_polys = [geom["coordinates"]]
        elif geom.get("type") == "MultiPolygon":
            raw_polys = geom["coordinates"]
        else:
            continue
        for raw in raw_polys:
            rings = [[(float(x), float(y)) for x, y in ring] for ring in raw]
            xs = [p[0] for ring in rings for p in ring]
            ys = [p[1] for ring in rings for p in ring]
            polys.append(((min(xs), min(ys), max(xs), max(ys)), rings))
    if path is None:
        _land_polys_cache = polys
    return polys


def _in_ring(lon: float, lat: float, ring: list) -> bool:
    # Same edges, same arithmetic, same parity as walking
    # zip(ring, ring[1:] + ring[:1]); the closing edge comes first and
    # no shifted copy of the ring is built (toggling is commutative).
    if not ring:
        return False
    inside = False
    x1, y1 = ring[-1]
    for x2, y2 in ring:
        if (y1 > lat) != (y2 > lat):
            xin = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < xin:
                inside = not inside
        x1, y1 = x2, y2
    return inside


def point_on_land(lat: float, lon: float, polys: list) -> bool:
    for (minx, miny, maxx, maxy), rings in polys:
        if not (minx <= lon <= maxx and miny <= lat <= maxy):
            continue
        if _in_ring(lon, lat, rings[0]) and not any(
                _in_ring(lon, lat, hole) for hole in rings[1:]):
            return True
    return False


def land_fraction(pts: list, polys: list) -> float:
    """Share of sampled (lat, lon) points falling over land."""
    if not pts:
        return 0.0
    hits = sum(1 for lat, lon in pts if point_on_land(lat, lon, polys))
    return hits / len(pts)


# -- quartiles ------------------------------------------------------------

def _pct(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    pos = (len(sorted_vals) - 1) * q
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _band(value: float, sorted_vals: list[float]) -> int:
    """Quartile band 0 (top) .. 3 (bottom) of ``value`` in its mode."""
    if value >= _pct(sorted_vals, 0.75):
        return 0
    if value >= _pct(sorted_vals, 0.50):
        return 1
    if value >= _pct(sorted_vals, 0.25):
        return 2
    return 3


# -- derivation -------------------------------------------------------------

def _best_via(frm: str, to: str, mode: str, direct_fraction: float,
              lanes_by_key: dict, adjacency: dict) -> list[str] | None:
    """Best <=3-hop graph path frm->to (same mode) whose combined
    land fraction beats ``direct_fraction``; returns the intermediate
    node ids, or None. Combined fraction is length-weighted over the
    legs' direct arcs. Deterministic: candidates are compared by
    (fraction, hops, node sequence)."""
    best: tuple | None = None  # (fraction, hops, via tuple)

    def consider(path_nodes: list[str]) -> None:
        nonlocal best
        num = den = 0.0
        for a, b in zip(path_nodes, path_nodes[1:]):
            leg = lanes_by_key.get(lane_key(mode, a, b))
            if leg is None:
                return
            num += leg["_fraction"] * leg["_direct_km"]
            den += leg["_direct_km"]
        if den <= 0.0:
            return
        frac = num / den
        cand = (frac, len(path_nodes) - 1, tuple(path_nodes[1:-1]))
        if frac < direct_fraction and (best is None or cand < best):
            best = cand

    frontier = [[frm]]
    for _depth in range(MAX_RENDER_HOPS):
        nxt = []
        for path in frontier:
            for nb in adjacency.get((mode, path[-1]), []):
                if nb in path:
                    continue
                cand = path + [nb]
                if nb == to and len(cand) >= 3:  # >= 2 edges: a real via
                    consider(cand)
                elif nb != to:
                    nxt.append(cand)
        frontier = nxt
    if best is None:
        return None
    return list(best[2])


def _dijkstra(adjacency: dict, src: str, skip_edge: tuple | None = None,
              targets: set | None = None,
              blocked: set | None = None) -> tuple[dict, dict]:
    """Shortest paths from ``src`` over ``adjacency`` ({node: [(nbr,
    weight_km)]} sorted by nbr). Deterministic tie-break by place id:
    heap entries carry the node sequence, so equal-length candidates
    resolve to the lexicographically smallest node sequence. Returns
    (dist, path) with path[node] = node tuple from src. ``skip_edge``
    removes one directed edge (a lane's own direct leg);
    ``blocked`` removes nodes as intermediates/destinations (the
    diversion search avoiding currently-closed places — the
    source itself is never blocked)."""
    import heapq

    blocked = blocked or set()
    dist: dict[str, float] = {src: 0.0}
    path: dict[str, tuple] = {src: (src,)}
    heap: list[tuple] = [(0.0, (src,), src)]
    settled: set = set()
    inf = math.inf
    heappop, heappush = heapq.heappop, heapq.heappush
    dist_get = dist.get
    while heap:
        d, p, u = heappop(heap)
        if u in settled or d > dist_get(u, inf) + 1e-9:
            continue
        settled.add(u)
        if targets is not None and targets <= settled:
            break
        for v, w in adjacency.get(u, []):
            if v in blocked:
                continue
            if skip_edge is not None and u == skip_edge[0] and v == skip_edge[1]:
                continue
            nd = d + w
            dv = dist_get(v, inf)
            # The candidate sequence is only built when it can win:
            # strictly shorter, or (within 1e-9) a smaller sequence.
            if nd < dv - 1e-9:
                cand = p + (v,)
            elif abs(nd - dv) <= 1e-9:
                cand = p + (v,)
                if not cand < path[v]:
                    continue
            else:
                continue
            dist[v] = nd
            path[v] = cand
            heappush(heap, (nd, cand, v))
    return dist, path


def _point_segment_dist_km(plat: float, plon: float,
                           a: tuple, b: tuple) -> tuple[float, float]:
    """(distance km, projection t in [0,1]) from a (lat, lon) point
    to the polyline segment a->b ((lat, lon) tuples), in a local
    equirectangular frame centred on the point (longitudes unwrapped
    next to ``a``)."""
    kx = 111.32 * math.cos(math.radians(plat))
    ky = 110.57

    def uw(lon: float, ref: float) -> float:
        while lon - ref > 180.0:
            lon -= 360.0
        while lon - ref < -180.0:
            lon += 360.0
        return lon

    ax, ay = a[1] * kx, a[0] * ky
    bx, by = uw(b[1], a[1]) * kx, b[0] * ky
    px, py = uw(plon, a[1]) * kx, plat * ky
    dx, dy = bx - ax, by - ay
    len2 = dx * dx + dy * dy
    t = 0.0 if len2 <= 0.0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy)), t


def movement_geometry(path_nodes: list, lanes_by_key: dict, mode: str,
                      node_coords: dict, chokepoint_ids: set) -> dict:
    """Movement geometry for a node path: the polyline is the
    concatenation of the legs' rendered paths; transits are the
    chokepoint places on that polyline (path nodes, rendered-via
    nodes — both are polyline vertices, distance 0 — or points within
    TRANSIT_CORRIDOR_KM of it), each with its arc-length fraction,
    sample index and the next path node after the closest approach.
    Shared by derivation (lane transit paths) and the simulator
    (diversion / resume paths) so both measure transits identically.
    ``lanes_by_key`` values need ``path`` (or ``_path_pts``),
    ``rendered_distance_km`` and ``direct_distance_km``."""
    def leg_sailed(x: str, y: str):
        """(points, sailed_km) for one leg: its rendered path —
        UNLESS the display detour exceeds
        MOVEMENT_RENDERED_MAX_RATIO over the leg's direct length.
        The fraction-only display rule produces two classes of long
        rendered paths (measured on the shipped graph): genuine
        curated routings a vessel does sail (Colombo->Rotterdam via
        the Cape and Port Said, 18,594 km = 2.2x direct — the
        graph's own sailing decision) and display artefacts no
        vessel sails (Los Angeles->Long Beach, 5 km direct,
        "renders" via Shanghai at 21,132 km = 4,000x; Busan->
        Qingdao, 796 km, via Los Angeles at 20,652 km = 26x;
        Fuzhou->Shanghai, 637 km, at 21,878 km = 34x). Concatenat-
        ing the artefacts as movement would invent 50,000
        km legs; the 2.5x line sits between the measured classes.
        An artefact leg is sailed on its direct arc; its DISPLAY
        path is unchanged either way."""
        leg = lanes_by_key[lane_key(mode, x, y)]
        if float(leg["rendered_distance_km"]) <= \
                MOVEMENT_RENDERED_MAX_RATIO * float(leg["direct_distance_km"]):
            return (leg.get("_path_pts") or [tuple(p) for p in leg["path"]],
                    float(leg["rendered_distance_km"]))
        return (leg.get("_direct_pts") or slerp_samples(
            node_coords[x], node_coords[y]),
                float(leg["direct_distance_km"]))

    pts: list = []
    total_km = 0.0
    gc_km = 0.0
    node_at_km: dict[str, float] = {}
    for x, y in zip(path_nodes, path_nodes[1:]):
        leg = lanes_by_key[lane_key(mode, x, y)]
        if x not in node_at_km:
            node_at_km[x] = total_km
        leg_pts, leg_km = leg_sailed(x, y)
        pts.extend(leg_pts if not pts else leg_pts[1:])
        total_km += leg_km
        gc_km += float(leg["direct_distance_km"])
    if path_nodes and path_nodes[-1] not in node_at_km:
        node_at_km[path_nodes[-1]] = total_km
    # Cumulative rendered length at each polyline sample.
    cum = [0.0]
    for p, q in zip(pts, pts[1:]):
        cum.append(cum[-1] + gc_distance_km(p, q))
    span = cum[-1] if cum else 0.0
    transits: list[dict] = []
    for nid in sorted(chokepoint_ids):
        if nid in (path_nodes[0], path_nodes[-1]):
            continue
        lat, lon = node_coords[nid]
        if nid in node_at_km and span > 0.0:
            frac = node_at_km[nid] / total_km if total_km > 0 else 0.0
            # Sample nearest the node's arc position.
            target = frac * span
            idx = min(range(len(cum)), key=lambda i: abs(cum[i] - target))
            nxt = next((n for n in path_nodes[1:]
                        if node_at_km.get(n, -1.0) > node_at_km[nid]), None)
            _pos = path_nodes.index(nid)
            prv = path_nodes[_pos - 1] if _pos >= 1 else None
            transits.append({"node": nid, "frac": round(frac, 6),
                             "sample_idx": idx, "next_node": nxt,
                             "prev_node": prv})
            continue
        # Corridor membership: closest approach to the polyline.
        best = math.inf
        best_i = 0
        best_t = 0.0
        for i, (p, q) in enumerate(zip(pts, pts[1:])):
            d, t = _point_segment_dist_km(lat, lon, p, q)
            if d < best:
                best, best_i, best_t = d, i, t
        if best <= TRANSIT_CORRIDOR_KM and span > 0.0:
            frac = (cum[best_i] + best_t * (cum[best_i + 1] - cum[best_i])) / span
            # Departure/arrival vicinity is not a transit: a place
            # whose closest approach is the leg's own origin or
            # destination neighbourhood (e.g. Suez is ~95 km from
            # Port Said, so every Port Said departure is "near" it)
            # is not transited by the leg.
            if frac <= 0.02 or frac >= 0.98:
                continue
            nxt = None
            prv = None
            if total_km > 0.0:
                acc = 0.0
                for x, y in zip(path_nodes, path_nodes[1:]):
                    acc += leg_sailed(x, y)[1]
                    if acc / total_km > frac:
                        nxt = y
                        prv = x
                        break
            transits.append({"node": nid, "frac": round(frac, 6),
                             "sample_idx": best_i, "next_node": nxt,
                             "prev_node": prv})
    transits.sort(key=lambda t: (t["frac"], t["node"]))
    return {"points": pts, "length_km": total_km, "gc_km": gc_km,
            "transits": transits}


# Service routes (opt-in layer): a hop between two anchors (origin,
# stated chokepoint, destination) is the shortest path over the graph's
# legs (sailed in either direction, weighted by great-circle distance);
# when the legs give no path, or one longer than this multiple of the
# anchors' great-circle distance, the hop is the direct arc. MODELLED.
SERVICE_HOP_MAX_RATIO = 3.0
# A hop whose direct arc crosses land (> LAND_THRESHOLD) takes an
# along-coast path over the graph's legs when it is at most this multiple
# of the direct arc (MODELLED; tighter than the 1.5 transit ratio so a hop does not
# back-track through a neighbouring port).
SERVICE_LAND_ALT_RATIO = 1.25


class _ServiceRouter:
    """Builds a service lane's node path and an augmented lane table for
    movement_geometry (reversed copies of graph legs sailed backwards,
    direct arcs for hops with no graph leg). Additive: it never mutates
    ``lanes_by_key``."""

    def __init__(self, lanes_by_key: dict, sea_adj: dict, node_coords: dict):
        und: dict[str, list] = {}
        for u, lst in sea_adj.items():
            for v, w in lst:
                und.setdefault(u, []).append((v, w))
                und.setdefault(v, []).append((u, w))
        self.adj = {u: sorted(set(lst)) for u, lst in und.items()}
        self.lanes = lanes_by_key
        self.coords = node_coords
        # Service hops are sailed on the legs' DIRECT arcs, never on the
        # graph's display-routed polylines (those carry artefacts such as
        # Cape of Good Hope -> Rotterdam rendered via Port Said, which
        # would tag a Cape service as a Suez transit).
        self.geo: dict[str, dict] = {}
        self._hops: dict[tuple, list] = {}

    def _leg_lane(self, x: str, y: str):
        return (self.lanes.get(lane_key("sea", x, y))
                or self.lanes.get(lane_key("sea", y, x)))

    def _hop(self, x: str, y: str) -> list:
        got = self._hops.get((x, y))
        if got is not None:
            return got
        gc = gc_distance_km(self.coords[x], self.coords[y])
        dist, path = _dijkstra(self.adj, x, targets={y})
        cand = path.get(y)
        if cand is None or len(cand) < 2 or \
                dist[y] > SERVICE_HOP_MAX_RATIO * max(gc, 1.0):
            cand = (x, y)
            dist_c = gc
        else:
            dist_c = dist[y]
        lane = self._leg_lane(x, y)
        if len(cand) == 2 and lane is not None and \
                lane["_fraction"] > LAND_THRESHOLD:
            # the direct arc crosses land: take an along-coast path over
            # the graph's legs when one exists within the transit ratio
            adj2 = dict(self.adj)
            adj2[x] = [t for t in adj2.get(x, []) if t[0] != y]
            adj2[y] = [t for t in adj2.get(y, []) if t[0] != x]
            d2, p2 = _dijkstra(adj2, x, targets={y})
            c2 = p2.get(y)
            if c2 is not None and len(c2) >= 3 and \
                    d2[y] <= SERVICE_LAND_ALT_RATIO * dist_c:
                cand = c2
        got = list(cand)
        self._hops[(x, y)] = got
        return got

    def _ensure(self, u: str, v: str) -> None:
        k = lane_key("sea", u, v)
        if k in self.geo:
            return
        r = self.lanes.get(lane_key("sea", u, v))
        if r is not None:
            pts = r["_direct_pts"]
        else:
            r = self.lanes.get(lane_key("sea", v, u))
            if r is not None:
                pts = list(reversed(r["_direct_pts"]))
            else:
                pts = slerp_samples(self.coords[u], self.coords[v])
        km = path_length_km(pts)
        self.geo[k] = {"_path_pts": pts, "_direct_pts": pts,
                       "rendered_distance_km": km, "direct_distance_km": km}

    def route(self, frm: str, via: list, to: str) -> tuple[list, dict]:
        anchors = [frm] + list(via) + [to]
        nodes_seq = [frm]
        for x, y in zip(anchors, anchors[1:]):
            seg = self._hop(x, y)
            nodes_seq.extend(seg[1:])
        for u, v in zip(nodes_seq, nodes_seq[1:]):
            self._ensure(u, v)
        return nodes_seq, self.geo


def derive(graph_data: dict, seed: int, start_epoch: float,
           horizon_end_epoch: float, land_polys: list | None = None,
           services: dict | None = None,
           lane_geometry=None) -> dict:
    """Derive lanes, roster and schedules for one sim run window.

    ``lane_geometry`` (opt-in, None by default - then every line below behaves exactly as before and the
    derivation is byte-identical): a ``lanegeo.sim.SimLaneGeometry``. A sea lane that has a land-checked
    lane polyline in it is placed on that polyline instead of the great-circle chord (positions by
    along-track distance, heading from the local tangent, durations on the polyline length; speed classes
    and headways are unchanged); a sea lane without one keeps its chord and spawns no discrete vessel (it
    stays in the derivation as topology), and no transit path runs over such a lane. Air and ground are
    unchanged.

    ``services`` (opt-in, None by default - then every line below behaves
    exactly as before and the derivation is byte-identical): the leg table
    of graph_services.legs.build_leg_table (a dict with ``legs`` and
    ``meta``). Its THROUGH legs (a service leg carrying >= 1 chokepoint)
    become lanes: a leg that is not a graph lane is CREATED (frequency
    MODELLED, the CORE median share), a leg that is a graph lane has its
    transit path REPLACED by the service route (its display geometry is
    unchanged). The transit path of a service lane is the shortest path
    over the graph's legs between consecutive anchors [origin, stated
    chokepoints in order, destination], so a vessel on a Suez-routed
    service really transits Suez and Bab el-Mandeb. Lanes without a
    service leg keep the old Dijkstra inference (its search graph excludes
    created service lanes).

    ``graph_data`` is the graph dict (nodes/edges lists).
    The window is ``[start_epoch - preroll, horizon_end_epoch]`` where
    preroll = 2x the longest lane's rendered duration.
    """
    polys = land_polys if land_polys is not None else load_land_polygons()
    nodes = {n["id"]: n for n in graph_data["nodes"]}
    edges = list(graph_data["edges"])
    svc_legs = (services or {}).get("legs") or {}
    existing_keys = {lane_key(e["mode"], e["from"], e["to"]) for e in edges}
    svc_created: dict[str, dict] = {}     # lane key -> service leg (no such graph lane)
    svc_over: dict[str, dict] = {}        # lane key -> service leg (graph lane, path replaced)
    for (_a, _b), _leg in sorted(svc_legs.items()):
        _k = lane_key("sea", _a, _b)
        if _a not in nodes or _b not in nodes:
            continue
        (svc_over if _k in existing_keys else svc_created)[_k] = _leg

    # Tier contract: resolved BEFORE any population or
    # RNG work — a tiered graph with a place missing its tier fails
    # here, at derivation, and the error surfaces at run creation.
    tiers, tiered = resolve_tiers(graph_data["nodes"])

    def _tier_class(frm: str, to: str) -> str:
        a, b = tiers[frm], tiers[to]
        if a == "core" and b == "core":
            return "core"
        if a == "shell" and b == "shell":
            return "shell_shell"
        return "shell_touching"

    # Quartile populations over CORE only: headway shares
    # from CORE lanes, origin capacities from CORE places. Shell
    # lanes / shell capacities never enter either population.
    shares_by_mode: dict[str, list[float]] = {}
    caps_by_mode: dict[str, list[float]] = {}
    for e in edges:
        if _tier_class(e["from"], e["to"]) == "core":
            shares_by_mode.setdefault(e["mode"], []).append(
                float(e["capacity_share"]))
    for n in graph_data["nodes"]:
        if tiers[n["id"]] == "core":
            caps_by_mode.setdefault(n["mode"], []).append(
                float(n["capacity"]))
    for vals in list(shares_by_mode.values()) + list(caps_by_mode.values()):
        vals.sort()

    # Service-created lanes (opt-in): synthetic edges appended AFTER the
    # quartile populations above are fixed, so they never move a band.
    # Frequency is MODELLED: the CORE median share of sea lanes.
    edges_all = list(edges)
    if svc_created:
        _sea_shares = shares_by_mode.get("sea") or [0.5]
        _median_share = _sea_shares[(len(_sea_shares) - 1) // 2]
        for _k, _leg in sorted(svc_created.items()):
            edges_all.append({
                "mode": "sea", "from": _leg["from"], "to": _leg["to"],
                "capacity_share": _median_share,
                "calibration": {"basis": "SERVICE_LEG"}})

    # Region machinery: a shell place's region is its
    # served ``region`` field when the graph carries one (the shipped
    # graph carries none), else the nearest CORE place in the same
    # mode by great-circle distance (deterministic tie-break by id).
    node_coords_all = {n["id"]: (float(n["lat"]), float(n["lon"]))
                       for n in graph_data["nodes"]}
    core_by_mode: dict[str, list[str]] = {}
    for n in graph_data["nodes"]:
        if tiers[n["id"]] == "core":
            core_by_mode.setdefault(n["mode"], []).append(n["id"])
    nearest_core: dict[str, str | None] = {}
    for n in graph_data["nodes"]:
        if tiers[n["id"]] != "shell":
            continue
        cands = core_by_mode.get(n["mode"], [])
        nearest_core[n["id"]] = min(
            cands, key=lambda cid: (gc_distance_km(
                node_coords_all[n["id"]], node_coords_all[cid]), cid),
            default=None)

    # Pass 1: per-lane classes + geometry (no RNG yet — durations set
    # the pre-roll, which sets the schedule anchor).
    lanes: list[dict] = []
    lanes_by_key: dict[str, dict] = {}
    for e in edges_all:
        mode, frm, to = e["mode"], e["from"], e["to"]
        a = (float(nodes[frm]["lat"]), float(nodes[frm]["lon"]))
        b = (float(nodes[to]["lat"]), float(nodes[to]["lon"]))
        direct_pts = slerp_samples(a, b)
        lane_poly = None
        if lane_geometry is not None and mode == "sea":
            lane_poly = lane_geometry.sea_points(frm, to)
            if lane_poly is not None:
                direct_pts = lane_poly
        direct_km = path_length_km(direct_pts)
        # a lane polyline was checked against the 1:50m land layer when it was built; the 110 m display
        # test below is for chords
        fraction = (land_fraction(direct_pts, polys) if mode in ("sea", "air") else 0.0) \
            if lane_poly is None else 0.0
        tclass = _tier_class(frm, to)
        lane: dict = {
            "key": lane_key(mode, frm, to), "mode": mode, "from": frm, "to": to,
            "capacity_share": float(e["capacity_share"]),
            "share_basis": (e.get("calibration") or {}).get("basis", "MODELED"),
            "flow_only": mode == "ground",
            "tier_class": tclass,
            "discrete_entities": mode != "ground",
            "_direct_pts": direct_pts, "_direct_km": direct_km,
            "_fraction": fraction,
        }
        if lane_geometry is not None and mode == "sea":
            lane["lane_geometry"] = {
                "status": "lane" if lane_poly is not None else "approx",
                "label": "MODELLED",
                "lane_nm": lane_geometry.lane_nm(frm, to) if lane_poly is not None else None}
            if lane_poly is None:
                lane["discrete_entities"] = False
                lane["no_entities_reason"] = (
                    "lane geometry mode: this sea leg has no land-checked lane polyline, so no vessel "
                    "is placed on its chord (the leg stays as topology)")
        if mode in HEADWAYS_H and tclass == "core":
            # CORE lanes: quartile populations are the CORE-only ones
            # above.
            hband = _band(float(e["capacity_share"]), shares_by_mode[mode])
            sband = _band(float(nodes[frm]["capacity"]), caps_by_mode[mode])
            speed = SPEEDS[mode][sband]
            lane.update({
                "headway_band": hband, "headway_h": HEADWAYS_H[mode][hband],
                "speed_class": sband, "speed": speed,
                "speed_unit": SPEED_UNIT[mode],
                "speed_kmh": speed * KN_TO_KMH if mode == "sea" else speed,
                "frequency_provenance": (
                    "MODELLED (capacity_share quartile over CORE lanes "
                    "only)"),
                "speed_provenance": (
                    "MODELLED (origin capacity quartile over CORE "
                    "places only)"),
            })
        lanes.append(lane)
        lanes_by_key[lane["key"]] = lane

    # Pass 1b: shell-touching lanes — frequency
    # by regional inheritance, speed = mode-median CORE speed class.
    # Regional populations are the CORE lanes of the region: with a
    # served region field, CORE lanes touching a place of that
    # region; otherwise CORE lanes incident to the shell place's
    # nearest CORE place in-mode. Empty regional population -> the
    # mode-global median CORE band (fallback recorded per lane).
    core_lanes_by_mode: dict[str, list[dict]] = {}
    for ln in lanes:
        if ln["tier_class"] == "core" and ln["mode"] in HEADWAYS_H:
            core_lanes_by_mode.setdefault(ln["mode"], []).append(ln)
    global_median_band: dict[str, int] = {}
    median_speed_class: dict[str, int] = {}
    for mode, cl in core_lanes_by_mode.items():
        global_median_band[mode] = _median_band(
            [ln["headway_band"] for ln in cl])
        median_speed_class[mode] = _median_band(
            [ln["speed_class"] for ln in cl])
    # Mode shell-activity median (numeric served values only) — the
    # reference for the one-band activity shift.
    shell_activity_median: dict[str, float | None] = {}
    for mode in HEADWAYS_H:
        vals = sorted(v for n in graph_data["nodes"]
                      if tiers[n["id"]] == "shell" and n["mode"] == mode
                      for v in [observed_activity_value(n)]
                      if v is not None)
        shell_activity_median[mode] = (
            vals[(len(vals) - 1) // 2] if vals else None)

    def _regional_median_band(mode: str, shell_id: str) -> tuple:
        """(band, region_key, region_source, population_size)."""
        if mode not in global_median_band:
            return 3, None, "no CORE lanes in mode — lightest band", 0
        region = nodes[shell_id].get("region")
        if region:
            pop = [ln["headway_band"] for ln in core_lanes_by_mode[mode]
                   if nodes[ln["from"]].get("region") == region
                   or nodes[ln["to"]].get("region") == region]
            if pop:
                return _median_band(pop), str(region), \
                    "served region field", len(pop)
            return global_median_band[mode], str(region), \
                "served region field (no CORE lanes in region — " \
                "mode-global median CORE band)", 0
        anchor = nearest_core.get(shell_id)
        if anchor is not None:
            pop = [ln["headway_band"] for ln in core_lanes_by_mode[mode]
                   if ln["from"] == anchor or ln["to"] == anchor]
            if pop:
                return _median_band(pop), anchor, \
                    "nearest CORE place in mode (no region field " \
                    "served)", len(pop)
            return global_median_band[mode], anchor, \
                "nearest CORE place in mode (no CORE lanes at that " \
                "place — mode-global median CORE band)", 0
        return global_median_band[mode], None, \
            "mode-global median CORE band (no CORE place in mode)", 0

    for lane in lanes:
        if lane["mode"] not in HEADWAYS_H or lane["tier_class"] == "core":
            continue
        mode = lane["mode"]
        # The shell place whose region the lane inherits: the origin
        # when it is shell, else the destination.
        shell_id = lane["from"] if tiers[lane["from"]] == "shell" \
            else lane["to"]
        band, region_key, region_source, pop_n = _regional_median_band(
            mode, shell_id)
        # Observed-activity shift: a served numeric activity above
        # the mode's shell median shifts one band faster, below one
        # band slower; presence without magnitude never shifts.
        act_val = observed_activity_value(nodes[shell_id])
        act_med = shell_activity_median[mode]
        shift = 0
        activity_basis = None
        if act_val is not None and act_med is not None:
            if act_val > act_med:
                shift = -1
            elif act_val < act_med:
                shift = 1
            if shift:
                activity_basis = (
                    f"observed activity {act_val:g} vs mode shell "
                    f"median {act_med:g} — band shifted "
                    f"{'one step faster' if shift < 0 else 'one step slower'}")
        if lane["tier_class"] == "shell_shell":
            ends_activity = (has_observed_activity(nodes[lane["from"]])
                             or has_observed_activity(nodes[lane["to"]]))
            if not ends_activity:
                # no observed activity at either endpoint
                # -> NO discrete entities (lane + flow only).
                lane["discrete_entities"] = False
                lane["no_entities_reason"] = (
                    "shell-shell lane with no observed activity at "
                    "either endpoint — lane + flow only, no discrete "
                    "entities")
            else:
                band, shift = 3, 0  # lightest headway band
                activity_basis = (
                    "shell-shell lane with observed activity at an "
                    "endpoint — entities at the lightest headway band")
        band = max(0, min(3, band + shift))
        sclass = median_speed_class.get(mode, 3)
        speed = SPEEDS[mode][sclass]
        lane.update({
            "headway_band": band, "headway_h": HEADWAYS_H[mode][band],
            "speed_class": sclass, "speed": speed,
            "speed_unit": SPEED_UNIT[mode],
            "speed_kmh": speed * KN_TO_KMH if mode == "sea" else speed,
            "frequency_provenance": "MODELLED (regional inheritance)",
            "frequency_basis": (
                f"regional inheritance: region {region_key!r} via "
                f"{region_source}; median CORE headway band over "
                f"{pop_n} regional CORE lane(s)"),
            "region_key": region_key, "region_source": region_source,
            "activity_shift": shift, "activity_basis": activity_basis,
            "speed_provenance": (
                "MODELLED (mode-median CORE speed class)"),
        })

    # Pass 1c: air classes. Integrator hubs = CORE
    # air places in the top quartile of served air capacity (the
    # graph's air capacity is freight tonnes/day — see the config
    # block). The population is CORE-only.
    air_caps_core = sorted(
        float(n["capacity"]) for n in graph_data["nodes"]
        if n["mode"] == "air" and tiers[n["id"]] == "core")
    integrator_hubs = sorted(
        n["id"] for n in graph_data["nodes"]
        if n["mode"] == "air" and tiers[n["id"]] == "core"
        and air_caps_core
        and _band(float(n["capacity"]), air_caps_core) == 0)
    integrator_set = set(integrator_hubs)
    for lane in lanes:
        if lane["mode"] != "air":
            continue
        if lane["from"] in integrator_set or lane["to"] in integrator_set:
            lane["air_class"] = "cargo_dominant"
            lane["cargo_share"] = AIR_CARGO_LANE_CARGO_SHARE
        else:
            lane["air_class"] = "passenger_dominant"
            lane["cargo_share"] = AIR_PAX_LANE_CARGO_SHARE

    # Air adjacency (either direction — a leg may be flown on the
    # reverse of a served lane, exactly as geom resolution in the
    # simulator already allows) for rotation construction.
    air_adj: dict[str, set] = {}
    for lane in lanes:
        if lane["mode"] == "air" and not lane["flow_only"]:
            air_adj.setdefault(lane["from"], set()).add(lane["to"])
            air_adj.setdefault(lane["to"], set()).add(lane["from"])

    def _build_rotation(eid: str, frm: str, to: str) -> tuple[list, str]:
        """Deterministic closed rotation for one air entity:
        a closed tour [hub, ..., hub] of 2-4 legs from the entity's
        home hub (the spawn lane's higher-capacity endpoint; ties by
        place id). Patterns: hub-spoke-hub, hub-spoke-spoke-hub
        (second spoke joined to BOTH hub and first spoke in the lane
        structure), hub-spoke-hub-spoke-hub. The pattern + second
        spoke are drawn from the entity id's hash — seeded by
        construction (the id derives from {lane, index, seed}), so
        NO RNG stream is consumed and the draw order is
        untouched."""
        capf = float(nodes[frm]["capacity"])
        capt = float(nodes[to]["capacity"])
        if capf > capt or (capf == capt and frm < to):
            hub, spoke1 = frm, to
        else:
            hub, spoke1 = to, frm
        h = int(sha256_hex(canonical_json({"rotation_for": eid}))[:8], 16)
        spokes3 = sorted(x for x in air_adj.get(hub, set())
                         if x not in (hub, spoke1)
                         and spoke1 in air_adj.get(x, set()))
        spokes_hub = sorted(x for x in air_adj.get(hub, set())
                            if x not in (hub, spoke1))
        pattern = h % 3
        if pattern == 1 and spokes3:
            s2 = spokes3[(h >> 4) % len(spokes3)]
            return [hub, spoke1, s2, hub], hub
        if pattern == 2 and spokes_hub:
            s2 = spokes_hub[(h >> 4) % len(spokes_hub)]
            return [hub, spoke1, hub, s2, hub], hub
        return [hub, spoke1, hub], hub

    # Pass 2: land-crossing disposition. Adjacency per mode.
    adjacency: dict[tuple, list[str]] = {}
    for lane in lanes:
        if lane["key"] in svc_created:
            continue                 # created service lanes are not display-routing hops
        adjacency.setdefault((lane["mode"], lane["from"]), []).append(lane["to"])
    for key in adjacency:
        adjacency[key] = sorted(set(adjacency[key]))
    for lane in lanes:
        via: list[str] = []
        if lane["mode"] in ("sea", "air") and lane["_fraction"] > LAND_THRESHOLD:
            via = _best_via(lane["from"], lane["to"], lane["mode"],
                            lane["_fraction"], lanes_by_key, adjacency) or []
        lane["rendered_via"] = via
        if via:
            pts: list = []
            chain = [lane["from"]] + via + [lane["to"]]
            for x, y in zip(chain, chain[1:]):
                leg_pts = lanes_by_key[lane_key(lane["mode"], x, y)]["_direct_pts"]
                pts.extend(leg_pts if not pts else leg_pts[1:])
            lane["_path_pts"] = pts
            lane["disposition"] = (
                "rendered via graph path (DISPLAY ROUTING) — direct arc "
                f"land-crossing {lane['_fraction']:.3f} > {LAND_THRESHOLD}")
        else:
            lane["_path_pts"] = lane["_direct_pts"]
            if lane["mode"] in ("sea", "air") and lane["_fraction"] > LAND_THRESHOLD:
                lane["disposition"] = (
                    f"land_crossing: {lane['_fraction']:.3f}, "
                    "no graph path improves")
            elif lane["flow_only"]:
                lane["disposition"] = "ground corridor — flow only, no discrete entities"
            else:
                lane["disposition"] = "direct great-circle arc"
        lane["path"] = [[round(lat, 5), round(lon, 5)] for lat, lon in lane["_path_pts"]]
        if (lane.get("lane_geometry") or {}).get("status") == "lane":
            lane["disposition"] = ("lane polyline (lane geometry, MODELLED; land-checked against "
                                   "Natural Earth 1:50m)")
        # Distances stay at full float precision in the record: the
        # simulator's leg durations divide them, and rounding here
        # would shift every arrival by centiseconds for no benefit.
        lane["direct_distance_km"] = lane["_direct_km"]
        rendered_km = path_length_km(lane["_path_pts"])
        lane["rendered_distance_km"] = rendered_km
        if not lane["flow_only"]:
            lane["_duration_h_exact"] = rendered_km / lane["speed_kmh"]
            lane["nominal_duration_h"] = round(lane["_duration_h_exact"], 6)
            # Midpoint of the rendered path (weather sampling point).
            mid = lane["_path_pts"][len(lane["_path_pts"]) // 2]
            lane["midpoint"] = [round(mid[0], 5), round(mid[1], 5)]

    # Pass 2.5: transit paths. Dijkstra per (mode, origin)
    # over directed legs weighted by great-circle distance; a lane's
    # own direct leg is excluded from its search (see the constant's
    # comment). The accepted path's movement geometry — concatenated
    # rendered paths of its legs, with chokepoint transits — replaces
    # the rendered length as the lane's sailed distance, so leg
    # durations are computed on the movement path (the transit
    # path is what a vessel sails; display routing is unchanged).
    node_coords = {nid: (float(n["lat"]), float(n["lon"]))
                   for nid, n in nodes.items()}
    chokepoints_by_mode: dict[str, set] = {}
    for n in graph_data["nodes"]:
        if n.get("kind") == "chokepoint":
            chokepoints_by_mode.setdefault(n["mode"], set()).add(n["id"])
    weighted_adj: dict[tuple, list[tuple]] = {}
    for lane in lanes:
        if lane["flow_only"] or lane["key"] in svc_created:
            continue
        if lane_geometry is not None and (lane.get("lane_geometry") or {}).get("status") == "approx":
            continue                 # no transit path runs over a chord in lane geometry mode
        weighted_adj.setdefault((lane["mode"], lane["from"]), []).append(
            (lane["to"], lane["direct_distance_km"]))
    for key in weighted_adj:
        weighted_adj[key] = sorted(set(weighted_adj[key]))
    mode_adj: dict[str, dict] = {}
    for (mode, frm), lst in weighted_adj.items():
        mode_adj.setdefault(mode, {})[frm] = lst
    dijkstra_cache: dict[tuple, tuple] = {}
    paths: dict[str, dict] = {}
    svc_state = _ServiceRouter(lanes_by_key, mode_adj.get("sea", {}), node_coords) \
        if (svc_created or svc_over) else None
    for lane in [ln for ln in lanes if not ln["flow_only"]]:
        mode, frm, to = lane["mode"], lane["from"], lane["to"]
        svc_leg = svc_created.get(lane["key"]) or svc_over.get(lane["key"])
        geo_lanes = lanes_by_key
        if svc_leg is not None:
            # Service route (opt-in): anchors origin, stated chokepoints
            # in order, destination; legs between anchors from the graph.
            path_nodes, geo_lanes = svc_state.route(frm, svc_leg["via"], to)
            lane["service"] = {
                "services": list(svc_leg["services"]),
                "via": list(svc_leg["via"]),
                "implied": list(svc_leg.get("implied", [])),
                "placement": list(svc_leg.get("placement", [])),
                "alternatives": list(svc_leg.get("alternatives", [])),
                "created": lane["key"] in svc_created,
                "basis": ("SERVICE: rotation SOURCED, chokepoint placement "
                          "per the leg's placement label (MODELLED where "
                          "not a printed marker), frequency MODELLED"
                          if lane["key"] in svc_created else
                          "SERVICE: graph lane, transit path replaced by "
                          "the service route; frequency from the graph"),
            }
        else:
            cache_key = (mode, frm)
            if cache_key not in dijkstra_cache:
                dijkstra_cache[cache_key] = _dijkstra(mode_adj[mode], frm)
            dist, path = dijkstra_cache[cache_key]
            cand = path.get(to)
            if cand is None or len(cand) <= 2:
                # The unskipped search used the lane's own direct leg (or
                # found nothing): re-run with that leg excluded.
                dist, path = _dijkstra(mode_adj[mode], frm,
                                       skip_edge=(frm, to), targets={to})
                cand = path.get(to)
            if cand is not None and len(cand) >= 2 and \
                    dist[to] <= TRANSIT_ACCEPT_RATIO * lane["direct_distance_km"]:
                path_nodes = list(cand)
            else:
                path_nodes = [frm, to]
            if lane_geometry is not None and mode == "sea":
                # lane geometry mode: the vessel sails the lane polyline itself (what moves is what is
                # drawn); the chokepoints it transits are the ones the polyline passes (corridor rule)
                path_nodes = [frm, to]
        geom = movement_geometry(path_nodes, geo_lanes, mode,
                                 node_coords,
                                 chokepoints_by_mode.get(mode, set()))
        lane["transit_path"] = path_nodes
        lane["transit_gc_km"] = round(geom["gc_km"], 6)
        lane["movement_distance_km"] = round(geom["length_km"], 6)
        lane["transits"] = geom["transits"]
        lane["_duration_h_exact"] = geom["length_km"] / lane["speed_kmh"]
        lane["nominal_duration_h"] = round(lane["_duration_h_exact"], 6)
        paths[lane["key"]] = {
            "nodes": path_nodes,
            "points": [[round(lat, 5), round(lon, 5)]
                       for lat, lon in geom["points"]],
            "length_km": round(geom["length_km"], 6),
            "gc_km": round(geom["gc_km"], 6),
            "transits": geom["transits"],
        }

    mobile = [ln for ln in lanes if not ln["flow_only"]]
    # Lanes that spawn no discrete entities
    # (shell-shell, no observed activity) are topology + flow only —
    # they set neither the pre-roll nor the roster estimate.
    spawning = [ln for ln in mobile if ln["discrete_entities"]]
    longest_h = max((ln["_duration_h_exact"] for ln in spawning),
                    default=0.0)
    preroll_s = 2.0 * longest_h * 3600.0
    anchor = start_epoch - preroll_s
    window_h = (horizon_end_epoch - anchor) / 3600.0

    # Scale budget: CORE-first derivation;
    # shell-touching lanes share a sub-budget capped at 25% OF THE
    # RUN'S ENTITY TOTAL — shell_est <= 25% of (core_est + shell_est),
    # i.e. shell_est <= core_est / 3 — AND at 25% of the roster cap.
    # (A sub-budget of 25% of the cap alone is the ratio
    # rule only when the roster actually reaches the cap.) Coarsening
    # order: shell lanes first (to the tighter of the ratio target
    # against uncoarsened CORE and the absolute cap share), then
    # longest-headway core lanes down to the cap, then a final ratio
    # re-check against the coarsened CORE estimate. Coarsening
    # proceeds in rounds over the class sorted by (headway desc,
    # lane key); within the final partial round only the
    # longest-headway lanes receive the extra doubling, so the
    # densest services are preserved longest.
    def _is_shell(ln: dict) -> bool:
        return ln["tier_class"] != "core"

    est_map = {ln["key"]: window_h / ln["headway_h"] for ln in spawning}
    mult_map = {ln["key"]: 1 for ln in spawning}
    shell_lanes = sorted((ln for ln in spawning if _is_shell(ln)),
                         key=lambda ln: (-ln["headway_h"], ln["key"]))
    core_lanes = sorted((ln for ln in spawning if not _is_shell(ln)),
                        key=lambda ln: (-ln["headway_h"], ln["key"]))
    shell_abs_cap = SHELL_SUB_BUDGET_FRACTION * MAX_ENTITIES

    def _coarsen(group: list, group_est: float, target: float) -> float:
        total = group_est
        while total > target and group:
            progressed = False
            for ln in group:
                if total <= target:
                    break
                est_map[ln["key"]] /= 2.0
                mult_map[ln["key"]] *= 2
                total -= est_map[ln["key"]]
                progressed = True
            if not progressed or max(mult_map[ln["key"]] for ln in group) >= 2 ** 20:
                break
        return total

    def _ratio_target(core_est: float) -> float:
        """Shell estimate ceiling for the 25%-of-total rule."""
        if core_est <= 0.0:
            return shell_abs_cap
        return core_est * SHELL_SUB_BUDGET_FRACTION / (
            1.0 - SHELL_SUB_BUDGET_FRACTION)

    est_total0 = sum(est_map.values())
    est_shell = sum(est_map[ln["key"]] for ln in shell_lanes)
    est_core = sum(est_map[ln["key"]] for ln in core_lanes)
    shell_target = min(shell_abs_cap, _ratio_target(est_core))
    if est_shell > shell_target:
        est_shell = _coarsen(shell_lanes, est_shell, shell_target)
    est_total = sum(est_map.values())
    if est_total > MAX_ENTITIES:
        core_est = sum(est_map[ln["key"]] for ln in core_lanes)
        _coarsen(core_lanes, core_est,
                 core_est - (est_total - MAX_ENTITIES))
        est_total = sum(est_map.values())
    # Final ratio re-check against the (possibly coarsened) CORE
    # estimate: core coarsening must not push the shell share over
    # 25% of the run's total.
    est_core_final = sum(est_map[ln["key"]] for ln in core_lanes)
    est_shell = sum(est_map[ln["key"]] for ln in shell_lanes)
    ratio_target_final = _ratio_target(est_core_final)
    if est_shell > ratio_target_final:
        est_shell = _coarsen(shell_lanes, est_shell, ratio_target_final)
        est_total = sum(est_map.values())
    for ln in spawning:
        ln["headway_h"] = ln["headway_h"] * mult_map[ln["key"]]
        ln["headway_coarsen_factor"] = mult_map[ln["key"]]
    for ln in mobile:
        ln.setdefault("headway_coarsen_factor", 1)
    coarsen = max(mult_map.values()) if mult_map else 1
    shell_max = max((mult_map[ln["key"]] for ln in shell_lanes), default=1)
    core_max = max((mult_map[ln["key"]] for ln in core_lanes), default=1)
    shell_share_est = (est_shell / est_total) if est_total > 0 else 0.0
    if coarsen > 1:
        coarsen_reason = (
            f"roster estimate {est_total0:.0f} exceeded the entity cap "
            f"({MAX_ENTITIES}) and/or the shell sub-budget (25% of "
            f"the run's entity total); shell-touching lanes coarsened "
            f"first (max x{shell_max}, shell estimate {est_shell:.0f} "
            f"= {shell_share_est:.1%} of the run estimate), then "
            f"longest-headway core lanes (max x{core_max}); estimate "
            f"after coarsening {est_total:.0f}")
    else:
        coarsen_reason = "none — roster within the entity cap"

    # Pass 3: schedules + roster — the ONLY RNG draws, canonical order.
    rng = random.Random(seed)
    ordered = sorted(spawning, key=lambda ln: (ln["mode"], ln["from"], ln["to"]))
    entities: list[dict] = []
    seen_eids: set[str] = set()
    for lane in ordered:
        headway_s = lane["headway_h"] * 3600.0
        offset = rng.uniform(0.0, headway_s)
        t = anchor + offset
        idx = 0
        departures: list[float] = []
        while t <= horizon_end_epoch:
            departures.append(t)
            jitter = rng.uniform(-0.10, 0.10)
            t += headway_s * (1.0 + jitter)
            idx += 1
        lane["departure_count"] = len(departures)
        prefix = "vsl_" if lane["mode"] == "sea" else "air_"
        for i, dep in enumerate(departures):
            eid = prefix + sha256_hex(canonical_json(
                {"lane": lane["key"], "departure_index": i, "seed": seed}))[:10]
            # 40-bit ids: a collision would silently merge two entities.
            assert eid not in seen_eids, f"entity id collision: {eid}"
            seen_eids.add(eid)
            ent = {
                "id": eid, "mode": lane["mode"], "lane": lane["key"],
                "from": lane["from"], "to": lane["to"],
                "departure_index": i, "first_departure_epoch": dep,
                "speed_kmh": lane["speed_kmh"], "speed_class": lane["speed_class"],
            }
            if lane["mode"] == "sea":
                # generated identity (MODELLED) — a
                # pure function of the entity id hash; consumes no
                # RNG draw, so the draw order is untouched.
                ent.update(sea_identity(eid))
            if lane["mode"] == "air":
                # class: interleaved deterministic assignment
                # at the lane's cargo share (fractional accumulation
                # — no RNG draw, draw order untouched).
                share = lane["cargo_share"]
                ent["cls"] = "cargo" if math.floor((i + 1) * share) \
                    > math.floor(i * share) else "passenger"
                # generated identity (MODELLED), same
                # hash-derived, RNG-free rule as sea.
                ent.update(air_identity(eid, ent["cls"]))
                rotation, hub = _build_rotation(eid, lane["from"],
                                                lane["to"])
                ent["rotation"] = rotation
                ent["home_hub"] = hub
                # first slot: passenger departures from the
                # home hub quantise up to the next bank window; cargo
                # departures from an integrator hub quantise up to
                # the next evening sort wave. Outstations keep the
                # lane schedule's even spacing.
                if ent["cls"] == "passenger" and lane["from"] == hub:
                    ent["first_departure_epoch"] = next_bank_epoch(
                        dep, float(nodes[lane["from"]]["lon"]))
                elif ent["cls"] == "cargo" \
                        and lane["from"] in integrator_set:
                    ent["first_departure_epoch"] = next_cargo_wave_epoch(
                        dep, float(nodes[lane["from"]]["lon"]))
            entities.append(ent)
    # Dwell draws at entity creation, in entity-id order.
    # Exactly ONE draw per entity (the stream count is independent of
    # the air module, so sea dwells are unaffected). Sea: the dwell band.
    # Air: passenger
    # one uniform in the passenger band; cargo one uniform u mapped
    # into both bands (elsewhere = 1-2 h, integrator hub = 2-4 h).
    for ent in sorted(entities, key=lambda e: e["id"]):
        if ent["mode"] == "air":
            if ent["cls"] == "cargo":
                u = rng.random()
                ent["dwell_h"] = AIR_TA_CARGO_OTHER_H[0] + u * (
                    AIR_TA_CARGO_OTHER_H[1] - AIR_TA_CARGO_OTHER_H[0])
                ent["dwell_hub_h"] = AIR_TA_CARGO_HUB_H[0] + u * (
                    AIR_TA_CARGO_HUB_H[1] - AIR_TA_CARGO_HUB_H[0])
            else:
                ent["dwell_h"] = rng.uniform(*AIR_TA_PAX_H)
        else:
            lo, hi = DWELL_BAND_H[ent["mode"]]
            ent["dwell_h"] = rng.uniform(lo, hi)
    entities.sort(key=lambda e: e["id"])
    for lane in lanes:
        lane.setdefault("departure_count", 0)
        lane["land_crossing"] = round(lane["_fraction"], 6)
        for k in ("_direct_pts", "_path_pts", "_direct_km", "_fraction",
                  "_duration_h_exact"):
            lane.pop(k, None)
    sourced = sum(1 for e in edges
                  if (e.get("calibration") or {}).get("basis") == "SOURCED")
    # Per-tier split: places, routes and
    # entities counted by tier class honestly from the resolved tier
    # map (index-based classification — never dict-equality matching,
    # which could misclassify duplicate edges). "core" in by_tier =
    # both endpoints CORE; "shell" = every shell-touching route
    # (shell_shell included), matching the shape of the overall split.
    def _split(subset):
        s = sum(1 for e in subset
                if (e.get("calibration") or {}).get("basis") == "SOURCED")
        return {"sourced_edges": s, "modeled_edges": len(subset) - s,
                "total_edges": len(subset)}

    _core_edges = [e for e in edges
                   if _tier_class(e["from"], e["to"]) == "core"]
    _shell_edges = [e for e in edges
                    if _tier_class(e["from"], e["to"]) != "core"]
    tier_split = {"core": _split(_core_edges), "shell": _split(_shell_edges)}
    places_by_tier = {
        "core": sum(1 for t in tiers.values() if t == "core"),
        "shell": sum(1 for t in tiers.values() if t == "shell"),
    }
    lanes_by_tier_class: dict[str, int] = {
        "core": 0, "shell_touching": 0, "shell_shell": 0}
    for ln in lanes:
        lanes_by_tier_class[ln["tier_class"]] += 1
    lane_class_by_key = {ln["key"]: ln["tier_class"] for ln in lanes}
    entities_by_tier_class: dict[str, int] = {
        "core": 0, "shell_touching": 0, "shell_shell": 0}
    for ent in entities:
        entities_by_tier_class[lane_class_by_key[ent["lane"]]] += 1
    tier_block = {
        "tiered": tiered,
        "places_by_tier": places_by_tier,
        "lanes_by_tier_class": lanes_by_tier_class,
        "entities_by_tier_class": entities_by_tier_class,
        "statistics_scope": "CORE",
        "shell_inclusive_label": "SHELL-INCLUSIVE (MODELLED)",
        "shell_frequency_provenance": "MODELLED (regional inheritance)",
    }
    air_entities = [e for e in entities if e["mode"] == "air"]
    air_block = {
        "label": "MODELLED",
        "integrator_hubs": integrator_hubs,
        "class_counts": {
            "cargo": sum(1 for e in air_entities if e["cls"] == "cargo"),
            "passenger": sum(1 for e in air_entities
                             if e["cls"] == "passenger"),
        },
        "entities_by_lane_class": {
            "cargo_dominant": sum(1 for e in air_entities
                                  if lanes_by_key[e["lane"]].get(
                                      "air_class") == "cargo_dominant"),
            "passenger_dominant": sum(1 for e in air_entities
                                      if lanes_by_key[e["lane"]].get(
                                          "air_class")
                                      == "passenger_dominant"),
        },
        "split_rule": (
            "integrator hubs = CORE air places in the top quartile of "
            "served air capacity (the graph's air capacity is "
            "freight-based, tonnes/day); lanes touching an integrator "
            f"hub are cargo-dominant ({AIR_CARGO_LANE_CARGO_SHARE:.0%} "
            "of departures cargo, interleaved deterministically); all "
            "other air lanes are passenger-dominant "
            f"({AIR_PAX_LANE_CARGO_SHARE:.0%} of departures cargo — "
            "belly freight). MODELLED"),
        "turnarounds_h": {
            "passenger": list(AIR_TA_PAX_H),
            "cargo_integrator_hub": list(AIR_TA_CARGO_HUB_H),
            "cargo_elsewhere": list(AIR_TA_CARGO_OTHER_H),
            "nominal_schedule_turnaround": "band maximum (slack "
                                           "absorbs lateness)",
        },
        "bank_local_hours": list(AIR_BANK_LOCAL_HOURS),
        "cargo_wave_local_hour": AIR_CARGO_WAVE_LOCAL_HOUR,
        "local_time_basis": ("MODELLED proxy: local UTC offset = "
                             "round(longitude / 15) hours; the sim "
                             "clock stays UTC"),
        "divert_range_km": AIR_DIVERT_RANGE_KM,
        "divert_hold_s": AIR_DIVERT_HOLD_S,
        "kind_mapping": AIR_KIND_MAPPING,
    }
    svc_block = None
    if services is not None:
        sea_l = [ln for ln in lanes if ln["mode"] == "sea"]

        def _census(subset: list) -> dict:
            def has(ln, chk):
                return any(t["node"] == chk for t in ln.get("transits", []))
            n_s = sum(1 for ln in subset if has(ln, "chk_suez"))
            n_b = sum(1 for ln in subset if has(ln, "chk_babelmandeb"))
            n_both = sum(1 for ln in subset
                         if has(ln, "chk_suez") and has(ln, "chk_babelmandeb"))
            n_cape = sum(1 for ln in subset if has(ln, "chk_capeofgoodhope"))
            return {"lanes": len(subset), "suez": n_s, "babelmandeb": n_b,
                    "both": n_both, "cape": n_cape,
                    "through_share_suez_x_bab": (round(n_both / n_s, 6)
                                                 if n_s else None)}

        svc_block = {
            "label": ("SERVICES LAYER (opt-in): service rotations SOURCED "
                      "with provenance; chokepoint placement beyond a printed "
                      "marker, Bab el-Mandeb on Suez legs and the frequency of "
                      "created lanes MODELLED"),
            "selection": services.get("meta"),
            "through_legs": len(svc_legs),
            "lanes_created": len(svc_created),
            "lanes_replaced": len(svc_over),
            "census_sea_all": _census(sea_l),
            "census_service_lanes": _census([ln for ln in sea_l if ln.get("service")]),
        }
    lg_block = None
    if lane_geometry is not None:
        sea_l = [ln for ln in lanes if ln["mode"] == "sea"]
        on = [ln for ln in sea_l if ln["lane_geometry"]["status"] == "lane"]
        lg_block = {
            "label": "MODELLED",
            "selection": lane_geometry.digest(),
            "sea_lanes": len(sea_l), "lanes_on_geometry": len(on),
            "lanes_without_geometry": len(sea_l) - len(on),
            "vessels_on_geometry": sum(1 for e in entities if e["mode"] == "sea"),
            "rule": ("sea vessels are placed along land-checked lane polylines by along-track distance, "
                     "heading from the local tangent, speed classes unchanged; a sea leg without a "
                     "polyline stays as topology and carries no vessel"),
        }
    return {
        "lanes": lanes,
        "paths": paths,
        "entities": entities,
        "air": air_block,
        "entity_count": len(entities),
        "vessel_count": sum(1 for e in entities if e["mode"] == "sea"),
        "aircraft_count": sum(1 for e in entities if e["mode"] == "air"),
        "longest_lane_duration_h": round(longest_h, 6),
        "preroll_s": preroll_s,
        "schedule_anchor_epoch": anchor,
        "tier": tier_block,
        "statistics_scope": "CORE",
        "headway_coarsening": {
            "factor": coarsen,
            "reason": coarsen_reason,
            "shell_max_factor": shell_max,
            "core_max_factor": core_max,
            "shell_sub_budget": int(shell_abs_cap),
            "shell_sub_budget_fraction": SHELL_SUB_BUDGET_FRACTION,
            "shell_estimate": round(est_shell, 1),
            "core_estimate": round(est_core_final, 1),
            "shell_share_estimate": round(shell_share_est, 6),
            "roster_estimate": round(est_total0, 1),
        },
        **({"services": svc_block} if svc_block is not None else {}),
        **({"lane_geometry": lg_block} if lg_block is not None else {}),
        "split": {"sourced_edges": sourced,
                  "modeled_edges": len(edges) - sourced,
                  "total_edges": len(edges),
                  "by_tier": tier_split,
                  "places_by_tier": places_by_tier,
                  "lanes_by_tier_class": lanes_by_tier_class,
                  "entities_by_tier_class": entities_by_tier_class},
        "basis": ("CORE lanes: headway band from capacity_share quartile "
                  "over CORE lanes only, speed class from origin-place "
                  "capacity quartile over CORE places only; shell-touching "
                  "lanes: frequency by regional inheritance (median CORE "
                  "headway band of the region/mode; served region field, "
                  "else nearest CORE place in mode) with frequency "
                  "provenance MODELLED (regional inheritance), speed = "
                  "mode-median CORE speed class; shell-shell lanes with "
                  "no observed activity at either endpoint spawn no "
                  "discrete entities; "
                  "dwells seeded within band (sea 12-36 h, air 2-6 h); "
                  "berths = max(1, round(queue_cap/40)) sea, "
                  "max(1, round(queue_cap/400)) air, defaulting to 1 "
                  "when queue_cap is absent — all MODELED; "
                  "transit paths = Dijkstra shortest path over network legs "
                  "by great-circle distance (lane's own direct leg excluded), "
                  "accepted <= 1.5x direct; transits = chokepoints on the "
                  "sailed movement polyline (path/rendered-via nodes, or "
                  "within a 100 km corridor) — MODELED; "
                  "statistics scope: CORE only; "
                  "air module (MODELLED): classes from "
                  "the freight-capacity quartile (integrator hubs), "
                  "deterministic closed rotations from a home hub, "
                  "turnarounds by class, passenger banks + cargo "
                  "evening sort waves in longitude-proxy local time, "
                  "next departure = max(schedule slot, arrival + "
                  "turnaround) for both modes"),
    }
