"""Loader + validator for the graph completeness sidecar (data/graph/extra.json).

Same discipline as the services sidecar: strict and non-repairing. An entry
that fails any rule is DROPPED (listed with its reasons, and everything that
depends on it is dropped with it); nothing is guessed, re-mapped or patched.
A document-level failure (wrong schema, content hash, graph pin does not match
the graph in use) refuses the whole file (ExtraError).

The sidecar only ADDS ids: places, legs, dormant alternative lanes. It never
edits data/graph/graph.json and never overrides a graph number.

Tiers. graph.json has two tiers, core (200) and shell (792). The sidecar adds a
third label, ``core_ext`` (an extension of the two tiers):
engine-visible and structural, NOT calibration-grade. It labels
  * a sidecar PLACE that is engine-visible (a chokepoint with a sourced observed
    capacity), and
  * a sidecar LEG whose two ends are both core or core_ext places.
Every other sidecar leg and every non-chokepoint sidecar place is ``shell``
(routing and simulation only). The 200 / 792 split of graph.json is unchanged.

Rules (R0..R13):
  R0  graph pin (document level)
  R1  place fields, slug and prefix, unit per kind, ranges, a named source,
      provenance envelope, no id or alias collision with graph.json
  R2  place tier in {core_ext, shell}; a chokepoint is core_ext, country-null,
      with a SOURCED observed capacity
  R3  shell capacity_basis in {ESTIMATE, NOT_PUBLISHED, SOURCED}; NOT_PUBLISHED
      equals the MODELED default
  R4  a port closer than 8 km to an existing port needs same_complex_override
  R5  leg key = from->to:mode, unique, endpoints resolve, no self-loop, enums,
      base_transit_d > 0, share in [0, 1], calibration.volume_source
  R6  a primary leg never carries bypass_of (alternatives live in lanes)
  R7  leg tier = core_ext when both ends are core / core_ext, else shell
  R8  alt_medium legs carry a medium
  R9  outbound capacity_share per (from, mode) over graph.json plus the sidecar
      is at most 1.0
  R10 every sidecar place has an inbound and an outbound leg
  R12 lanes: target resolves to a primary leg, an A lane leaves its sender, a B
      lane reaches its receiver, the gate exists, tau > 0, dtau >= 0, share 0
  R13 an A lane adds days unless flagged faster_alternative
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = REPO_ROOT / "data" / "graph" / "extra.json"
SCHEMA = "supermap.graph_extra/1"

PLACE_TIERS = ("core_ext", "shell")
LEG_ROLES = ("primary", "transit", "alt_medium")
UNIT = {"port": "TEU/day", "chokepoint": "vessels/day", "air_hub": "tonnes/day",
        "ground_node": "trucks/day"}
MODE = {"port": "sea", "chokepoint": "sea", "air_hub": "air", "ground_node": "ground"}
PREFIX = {"port": "sea_", "chokepoint": "chk_", "air_hub": "air_", "ground_node": "gnd_"}
SHELL_DEFAULT = {"port": 500, "air_hub": 300, "ground_node": 1500}
REGISTRY = {"port": "portwatch", "air_hub": "ourairports", "ground_node": "wikipedia"}
PLACE_FIELDS = ("id", "name", "mode", "kind", "lat", "lon", "capacity", "capacity_unit",
                "queue_cap", "inventory_days", "sources", "tier", "provenance")
SAME_COMPLEX_KM = 8.0
EARTH_R_KM = 6371.0088


class ExtraError(ValueError):
    """The sidecar is unusable as a whole (never a per-entry problem)."""


def extra_path() -> Path:
    """SUPERMAP_EXTRA_PATH, else <SUPERMAP_DATA_DIR>/graph/extra.json when that
    exists, else the repository's data/graph/extra.json."""
    env = os.environ.get("SUPERMAP_EXTRA_PATH")
    if env:
        return Path(env)
    data_dir = os.environ.get("SUPERMAP_DATA_DIR")
    if data_dir:
        p = Path(data_dir) / "graph" / "extra.json"
        if p.exists():
            return p
    return DEFAULT_PATH


def file_sha256(path: Path | None = None) -> str:
    return hashlib.sha256((path or extra_path()).read_bytes()).hexdigest()


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_sha(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k != "content_sha256"}
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def leg_key(e: dict) -> str:
    return f"{e['from']}->{e['to']}:{e['mode']}"


def haversine_km(a: float, b: float, c: float, d: float) -> float:
    p = math.pi / 180
    h = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 2 * EARTH_R_KM * math.asin(min(1.0, math.sqrt(h)))


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class Checks:
    """Counts every executed rule so the validator can print PASS / FAIL totals."""

    def __init__(self):
        self.passed = 0
        self.failed: list[str] = []

    def __call__(self, ok: bool, label: str, bag: list | None = None) -> bool:
        if ok:
            self.passed += 1
        else:
            self.failed.append(label)
            if bag is not None:
                bag.append(label)
        return bool(ok)


def validate_place(p: dict, nodes: dict, alias_owner: dict, base_ports: list,
                   seen: set, chk: Checks) -> list[str]:
    bad: list[str] = []
    pid = p.get("id") if isinstance(p, dict) else None
    if not isinstance(p, dict) or not isinstance(pid, str):
        chk(False, "R1 place: not an object with a string id", bad)
        return bad
    missing = [f for f in PLACE_FIELDS if f not in p]
    if not chk(not missing, f"R1 {pid}: fields {missing}", bad):
        return bad
    kind = p["kind"]
    if not chk(kind in UNIT, f"R1 {pid}: kind {kind!r}", bad):
        return bad
    chk(re.fullmatch(r"[a-z0-9_]+", pid) is not None and pid.startswith(PREFIX[kind]),
        f"R1 {pid}: slug and prefix", bad)
    chk(pid not in nodes and pid not in seen, f"R1 {pid}: id not in graph.json or the sidecar", bad)
    chk(p["mode"] == MODE[kind] and p["capacity_unit"] == UNIT[kind],
        f"R1 {pid}: mode and unit per kind", bad)
    chk(_num(p["lat"]) and _num(p["lon"]) and -90 <= p["lat"] <= 90 and -180 <= p["lon"] <= 180
        and _num(p["capacity"]) and p["capacity"] > 0 and _num(p["queue_cap"]) and p["queue_cap"] >= 0
        and _num(p["inventory_days"]) and p["inventory_days"] >= 0, f"R1 {pid}: ranges", bad)
    chk(isinstance(p["sources"], list) and len(p["sources"]) >= 1
        and all(isinstance(s, str) and s for s in p["sources"]), f"R1 {pid}: >= 1 named source", bad)
    prov = p["provenance"] if isinstance(p["provenance"], dict) else {}
    chk(prov.get("mode") in ("LIVE", "SNAPSHOT", "MODELED") and bool(prov.get("retrieved_at")),
        f"R1 {pid}: provenance envelope", bad)
    chk(all(a not in alias_owner for a in p.get("aliases", [])),
        f"R1 {pid}: aliases do not collide with graph.json", bad)
    chk(p["tier"] in PLACE_TIERS, f"R2 {pid}: tier in {PLACE_TIERS}", bad)
    if kind == "chokepoint":
        chk(p["tier"] == "core_ext" and "country" not in p, f"R2 {pid}: chokepoint is core_ext and country-null", bad)
        chk(str(p.get("capacity_basis", "")).startswith("SOURCED"),
            f"R2 {pid}: core_ext chokepoint needs a SOURCED observed capacity", bad)
    else:
        chk(p["tier"] == "shell", f"R2 {pid}: non-chokepoint places are shell", bad)
        chk(p.get("capacity_basis") in ("ESTIMATE", "NOT_PUBLISHED", "SOURCED"),
            f"R3 {pid}: capacity_basis", bad)
        chk(isinstance(p.get("country"), str) and bool(p.get("country")), f"R1 {pid}: country", bad)
        chk(any(REGISTRY[kind] in str(s).lower() for s in p["sources"]),
            f"R3 {pid}: sources name the tier registry ({REGISTRY[kind]})", bad)
        if p.get("capacity_basis") == "NOT_PUBLISHED":
            chk(p["capacity"] == SHELL_DEFAULT[kind], f"R3 {pid}: NOT_PUBLISHED equals the MODELED default", bad)
        if p.get("capacity_basis") == "SOURCED":
            chk("http" in (str(p.get("capacity_note", "")) + " ".join(p["sources"])),
                f"R3 {pid}: SOURCED capacity carries a URL", bad)
    if kind == "port" and _num(p["lat"]) and _num(p["lon"]) and base_ports:
        d, near = min((haversine_km(p["lat"], p["lon"], b["lat"], b["lon"]), b["id"]) for b in base_ports)
        chk(d >= SAME_COMPLEX_KM or bool(p.get("same_complex_override")),
            f"R4 {pid}: {d:.1f} km from {near} needs a same_complex_override", bad)
    return bad


def validate_leg(e: dict, edge_keys: set, known: dict, seen: set, chk: Checks) -> list[str]:
    """``known``: place id -> tier for every graph.json place and valid sidecar place."""
    bad: list[str] = []
    if not isinstance(e, dict) or not all(f in e for f in ("from", "to", "mode", "key", "tier")):
        chk(False, "R5 leg: missing from/to/mode/key/tier", bad)
        return bad
    k = e["key"]
    chk(k == f"{e['from']}->{e['to']}:{e['mode']}", f"R5 {k}: key = from->to:mode", bad)
    chk(k not in edge_keys and k not in seen, f"R5 {k}: unique against graph.json and the sidecar", bad)
    ok_ends = e["from"] in known and e["to"] in known
    chk(ok_ends, f"R5 {k}: endpoints resolve", bad)
    chk(e["from"] != e["to"], f"R5 {k}: no self-loop", bad)
    chk(e["mode"] in ("sea", "air", "ground") and _num(e.get("base_transit_d")) and e["base_transit_d"] > 0
        and _num(e.get("capacity_share")) and 0.0 <= e["capacity_share"] <= 1.0, f"R5 {k}: enums and ranges", bad)
    chk(isinstance(e.get("calibration"), dict) and bool(e["calibration"].get("volume_source")),
        f"R5 {k}: calibration.volume_source", bad)
    chk("bypass_of" not in e, f"R6 {k}: primary legs never carry bypass_of", bad)
    chk(e.get("role") in LEG_ROLES, f"R5 {k}: role", bad)
    if ok_ends:
        low = {known[e["from"]], known[e["to"]]}
        want = "core_ext" if low <= {"core", "core_ext"} else "shell"
        chk(e["tier"] == want, f"R7 {k}: tier is {want} for ends {sorted(low)}", bad)
    if e.get("role") == "alt_medium":
        chk(bool(e.get("medium")), f"R8 {k}: alt_medium carries a medium", bad)
    return bad


def validate_lane(ln: dict, prim: dict | None, places: set, chk: Checks) -> list[str]:
    bad: list[str] = []
    k = ln.get("key") if isinstance(ln, dict) else None
    if not isinstance(ln, dict) or not all(f in ln for f in ("key", "kind", "from", "to", "leg", "tau", "dtau",
                                                             "family", "alt_gate", "capacity_share")):
        chk(False, "R12 lane: missing fields", bad)
        return bad
    if not chk(prim is not None, f"R12 {k}: bypass target {ln['leg']} resolves to a primary leg", bad):
        return bad
    chk((ln["kind"] == "A" and ln["from"] == prim["from"]) or (ln["kind"] == "B" and ln["to"] == prim["to"]),
        f"R12 {k}: A leaves the primary's sender, B reaches its receiver", bad)
    chk(ln["alt_gate"] in places and ln["from"] in places and ln["to"] in places,
        f"R12 {k}: gate and ends exist", bad)
    chk(_num(ln["tau"]) and ln["tau"] > 0 and _num(ln["dtau"]) and ln["dtau"] >= 0 and ln["capacity_share"] == 0.0,
        f"R12 {k}: tau > 0, dtau >= 0, dormant share 0", bad)
    if ln["kind"] == "A":
        chk(ln["dtau"] > 0 or bool(ln.get("faster_alternative")), f"R13 {k}: an A lane adds days", bad)
    return bad


class Extra:
    """A validated sidecar. ``places``, ``legs``, ``lanes`` hold only entries
    that passed; ``dropped`` lists the rest with reasons; ``disabled`` are the
    lanes the file ships switched off (never selected)."""

    def __init__(self, doc: dict, file_hash: str, places: list, legs: list, lanes: list,
                 disabled: list, dropped: list, chk: Checks):
        self.doc = doc
        self.file_sha256 = file_hash
        self.places = places
        self.legs = legs
        self.lanes = lanes
        self.disabled = disabled
        self.dropped = dropped
        self.checks_passed = chk.passed
        self.checks_failed = list(chk.failed)
        self.families = sorted({ln["family"] for ln in lanes})

    def select_lanes(self, lane_families=None) -> list[dict]:
        want = set(self.families if lane_families is None else lane_families)
        return [ln for ln in self.lanes if ln["family"] in want]

    def summary(self) -> dict:
        by_tier_p: dict = {}
        for p in self.places:
            by_tier_p[p["tier"]] = by_tier_p.get(p["tier"], 0) + 1
        by_tier_l: dict = {}
        for e in self.legs:
            by_tier_l[e["tier"]] = by_tier_l.get(e["tier"], 0) + 1
        fam: dict = {}
        for ln in self.lanes:
            fam[ln["family"]] = fam.get(ln["family"], 0) + 1
        return {
            "schema": SCHEMA,
            "file_sha256": self.file_sha256,
            "content_sha256": self.doc.get("content_sha256"),
            "graph_pin": self.doc.get("graph_pin"),
            "places": len(self.places), "places_by_tier": dict(sorted(by_tier_p.items())),
            "legs": len(self.legs), "legs_by_tier": dict(sorted(by_tier_l.items())),
            "lanes_enabled": len(self.lanes), "lanes_by_family": dict(sorted(fam.items())),
            "lanes_disabled": [{"key": ln["key"], "family": ln["family"], "reason": ln.get("disabled_reason")}
                               for ln in self.disabled],
            "position_notes": self.doc.get("position_notes", []),
            "dropped": self.dropped,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "policy": self.doc.get("policy"),
            "built_from": self.doc.get("built_from"),
        }


def validate_document(doc: dict, graph_data: dict, graph_hash: str | None = None,
                      file_hash: str = "") -> Extra:
    """Validate ``doc`` against the raw graph dict; raise ExtraError on a
    document-level failure, drop entries otherwise."""
    chk = Checks()
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise ExtraError(f"unsupported extra schema: {doc.get('schema') if isinstance(doc, dict) else None!r}")
    if doc.get("content_sha256") != content_sha(doc):
        raise ExtraError("extra content_sha256 does not match its content")
    pin = (doc.get("graph_pin") or {}).get("canonical_hash")
    if graph_hash is not None and pin != graph_hash:
        raise ExtraError("extra graph_pin does not match the graph in use")
    chk(True, "R0 document: schema, content hash, graph pin")
    nodes = {n["id"]: n for n in graph_data["nodes"]}
    edge_keys = {leg_key(e) for e in graph_data["edges"]}
    edge_by_key = {leg_key(e): e for e in graph_data["edges"]}
    alias_owner = {a: i for i, n in nodes.items() for a in n.get("aliases", [])}
    base_ports = [n for n in nodes.values() if n.get("kind") == "port"]
    tiers = {i: n.get("tier", "core") for i, n in nodes.items()}
    dropped: list[dict] = []

    # ---- places ---------------------------------------------------------
    places: dict[str, dict] = {}
    for p in doc.get("places_extra", []):
        why = validate_place(p, nodes, alias_owner, base_ports, set(places), chk)
        if why:
            dropped.append({"kind": "place", "id": p.get("id") if isinstance(p, dict) else None, "reasons": why})
            continue
        places[p["id"]] = p
        for a in p.get("aliases", []):
            alias_owner[a] = p["id"]

    # ---- legs (+ fixpoint on R9 / R10) ------------------------------------
    def leg_pass(places_now: dict) -> list[dict]:
        known = dict(tiers)
        known.update({i: p["tier"] for i, p in places_now.items()})
        seen: set = set()
        out = []
        for e in doc.get("legs_extra", []):
            why = validate_leg(e, edge_keys, known, seen, chk)
            if why:
                if not any(d["kind"] == "leg" and d["id"] == e.get("key") for d in dropped):
                    dropped.append({"kind": "leg", "id": e.get("key") if isinstance(e, dict) else None,
                                    "reasons": why})
                continue
            seen.add(e["key"])
            out.append(e)
        return out

    legs = leg_pass(places)
    for _ in range(8):
        changed = False
        out0: dict = {}
        for e in graph_data["edges"]:
            out0[(e["from"], e["mode"])] = out0.get((e["from"], e["mode"]), 0.0) + float(e["capacity_share"])
        add: dict = {}
        for e in legs:
            add[(e["from"], e["mode"])] = add.get((e["from"], e["mode"]), 0.0) + float(e["capacity_share"])
        over = {g for g, s in add.items() if out0.get(g, 0.0) + s > 1.0 + 1e-9}
        for g in sorted(add):
            chk(g not in over, f"R9 outbound share {g} = {out0.get(g, 0.0) + add[g]:.4f} <= 1.0")
        if over:
            for e in list(legs):
                if (e["from"], e["mode"]) in over:
                    dropped.append({"kind": "leg", "id": e["key"],
                                    "reasons": [f"R9 outbound share of {e['from']} ({e['mode']}) exceeds 1.0"]})
            legs = [e for e in legs if (e["from"], e["mode"]) not in over]
            changed = True
        ins = {e["to"] for e in legs}
        outs = {e["from"] for e in legs}
        for pid in sorted(places):
            ok = pid in ins and pid in outs
            if not ok:
                dropped.append({"kind": "place", "id": pid,
                                "reasons": ["R10 no inbound and outbound sidecar leg"]})
                chk(False, f"R10 {pid}: inbound and outbound leg")
                del places[pid]
                changed = True
        if changed:
            keep = []
            for e in legs:
                if all(x in tiers or x in places for x in (e["from"], e["to"])):
                    keep.append(e)
                else:
                    dropped.append({"kind": "leg", "id": e["key"],
                                    "reasons": ["an endpoint place was dropped"]})
            legs = keep
            continue
        for pid in places:
            chk(True, f"R10 {pid}: inbound and outbound leg")
        break

    # ---- lanes -----------------------------------------------------------
    prim_keys = dict(edge_by_key)
    prim_keys = {k: e for k, e in prim_keys.items() if not e.get("bypass_of")}
    prim_keys.update({e["key"]: e for e in legs})
    place_ids = set(tiers) | set(places)
    lane_seen: set = set()
    lanes, disabled = [], []
    off_families = {ln.get("family") for ln in doc.get("lanes_disabled", []) if isinstance(ln, dict)}
    for group, ref in (("lanes_extra", lanes), ("lanes_disabled", disabled)):
        for ln in doc.get(group, []):
            why = validate_lane(ln, prim_keys.get(ln.get("leg")) if isinstance(ln, dict) else None, place_ids, chk)
            if isinstance(ln, dict) and ln.get("key") in lane_seen:
                why.append("duplicate lane key")
            if isinstance(ln, dict) and ln.get("key") in edge_keys:
                why.append("lane key collides with a graph.json leg")
            if group == "lanes_extra" and isinstance(ln, dict) and ln.get("faster_alternative"):
                why.append("a faster_alternative lane must ship disabled")
            if group == "lanes_extra" and isinstance(ln, dict) and ln.get("family") in off_families:
                why.append(f"family {ln.get('family')} is shipped disabled; none of its lanes may be enabled")
            if group == "lanes_disabled" and isinstance(ln, dict) and ln.get("enabled") is not False:
                why.append("lanes_disabled entries carry enabled: false")
            if why:
                dropped.append({"kind": "lane", "id": ln.get("key") if isinstance(ln, dict) else None,
                                "reasons": why})
                continue
            lane_seen.add(ln["key"])
            ref.append(ln)
    return Extra(doc, file_hash, [places[i] for i in sorted(places)],
                 sorted(legs, key=lambda e: e["key"]),
                 sorted(lanes, key=lambda ln: ln["key"]),
                 sorted(disabled, key=lambda ln: ln["key"]), dropped, chk)


def load_extra(graph_data: dict, graph_hash: str | None = None, path: Path | None = None) -> Extra:
    """Load + validate against ``graph_data`` (raw graph dict)."""
    p = Path(path) if path else extra_path()
    raw = p.read_bytes()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ExtraError(f"extra file is not JSON: {exc}") from exc
    return validate_document(doc, graph_data, graph_hash, hashlib.sha256(raw).hexdigest())
