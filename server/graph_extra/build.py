"""Build the graph completeness SIDECAR (data/graph/extra.json), first slice,
from the candidate files.

    cd server && ../.venv/bin/python -m graph_extra.build            # dry run, prints the stamp
    cd server && ../.venv/bin/python -m graph_extra.build --write    # writes extra.json + extra.sha256
    cd server && ../.venv/bin/python -m graph_extra.build --check    # exits 1 unless the files on disk match

Pure and deterministic: no network, no clock, no RNG; the same candidates give
the same bytes. Nothing here touches data/graph/graph.json (its sha256 prefix is
asserted). The candidates are SELECTED, never edited: tiers are relabelled to the
sidecar vocabulary (see loader.py), the landbridge lane family is moved to
``lanes_disabled``, and the Bremerhaven position is carried as a NOTE that is
never applied.

Contents:
  * the two-way Red Sea corridor (candidate block RS: Suez, Bab el-Mandeb and the
    Aden / Djibouti / Port Sudan / El Sokhna exits) and its Cape lanes;
  * Panama alternative lanes via Suez and via the Cape (landbridge shipped off);
  * the six services ports (Trieste, Wilhelmshaven, Ensenada, Posorja, Chancay,
    Safi) with the shell legs the shell construction gives them, plus Aden.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

try:
    from engine.canonical import graph_hash
except Exception:  # imported as server.graph_extra.* from the repo root
    from server.engine.canonical import graph_hash  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[2]
CAND = REPO_ROOT / "data" / "graph_extra_candidates"
GRAPH = REPO_ROOT / "data" / "graph" / "graph.json"
OUT = REPO_ROOT / "data" / "graph" / "extra.json"
PIN = REPO_ROOT / "data" / "graph" / "extra.sha256"

SCHEMA = "supermap.graph_extra/1"
GRAPH_PREFIX = "9743a288fb5d670d"

SLICE_PLACES = ("sea_aden", "sea_chancay", "sea_ensenada", "sea_posorja", "sea_safi",
                "sea_trieste", "sea_wilhelmshaven")
SERVICES_PORTS = SLICE_PLACES[1:]
SLICE_LEG_BLOCKS = ("RS",)
ENABLED_FAMILIES = ("BAB_VIA_CAPE", "PANAMA_VIA_CAPE", "PANAMA_VIA_SUEZ")
DISABLED_FAMILIES = {
    "PANAMA_VIA_USLANDBRIDGE": (
        "disabled: the lane's temporary layer equivalence turns the Chicago ground node (20,000 trucks/day) into "
        "a capacity that dwarfs the 36 vessels/day Panama gate; "
        "a sourced landbridge gate capacity (rail intermodal TEU/day) is required before it may be enabled"),
}

POLICY = {
    "opt_in": "Consumed only when a run asks: `extra` on /sim/run (view sim) or on a rev-2 /run (view engine2). "
              "Journaled (file sha256, view, lane families) only when used; absent, every id and byte is unchanged.",
    "adds_only": "The sidecar adds ids. It never edits graph.json and never overrides a fixed number "
                 "(capacity, share, transit, queue_cap, inventory_days).",
    "tiers": "core_ext = engine-visible, structural, NOT calibration-grade. A sidecar leg between two core / "
             "core_ext places is core_ext; any leg touching a shell place is shell. The graph's 200 core / 792 "
             "shell split is unchanged. No place of this slice is core_ext (the four core_ext chokepoints of the "
             "candidates are outside the first slice).",
    "sim_view": "Every sidecar place enters the sim as shell. A core_ext leg is WITHHELD from the sim view: "
                "derive() sizes its headway bands from the core-core lane population, so a core-core addition "
                "would move the band of existing lanes. It is visible to engine rev 2 instead.",
    "engine2_view": "core places + core_ext places and core_ext legs only; shell places and legs are dropped, "
                    "exactly as core_subgraph drops them for graph.json shells.",
    "lanes": "Dormant (capacity_share 0): carried as engine rev 2 reroute-lane specs, used only when the primary "
             "leg's forecast wait exceeds the lane's added days. Route lengths are MODELLED geometry; the split "
             "among alternatives is not sourced.",
    "shares": "capacity_share of every new leg is a MODELED rule output (S1-S6, R-CAP), not data.",
    "claims": "Structure only. No accuracy claim follows from this file.",
}


def _load(name: str):
    return json.loads((CAND / name).read_text(encoding="utf-8"))


def dumps(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def content_sha(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k != "content_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def build() -> dict:
    raw_graph = GRAPH.read_bytes()
    gsha = hashlib.sha256(raw_graph).hexdigest()
    assert gsha.startswith(GRAPH_PREFIX), "graph.json changed"
    graph = json.loads(raw_graph)
    cand_sha = {}
    for line in (CAND / "candidates.sha256").read_text().splitlines():
        h, name = line.split()
        assert hashlib.sha256((CAND / name).read_bytes()).hexdigest() == h, f"{name} differs from candidates.sha256"
        cand_sha[name] = h
    P = {p["id"]: p for p in _load("places.extra.candidates.json")["places"]}
    L = _load("legs.extra.candidates.json")["legs"]
    N = _load("lanes.extra.candidates.json")["lanes"]

    places = [P[i] for i in sorted(SLICE_PLACES)]
    legs = []
    for e in L:
        if e["block"] in SLICE_LEG_BLOCKS or e["from"] in SERVICES_PORTS or e["to"] in SERVICES_PORTS:
            e = dict(e)
            e["tier"] = "core_ext" if e["tier"] in ("core", "core_ext") else "shell"
            legs.append(e)
    legs.sort(key=lambda e: e["key"])
    lanes = sorted((dict(n, enabled=True) for n in N if n["family"] in ENABLED_FAMILIES), key=lambda n: n["key"])
    disabled = sorted((dict(n, enabled=False, disabled_reason=DISABLED_FAMILIES[n["family"]])
                       for n in N if n["family"] in DISABLED_FAMILIES), key=lambda n: n["key"])
    brem = next(n for n in graph["nodes"] if n["id"] == "sea_bremerhaven")
    audit = _load("audit.json")
    defect = next(d for d in audit["core_coordinate_defects"] if d.get("id") == "sea_bremerhaven")
    notes = [{
        "place_id": "sea_bremerhaven",
        "applied": False,
        "graph_position": [brem["lat"], brem["lon"]],
        "registry_position": defect["registry_Bremerhaven"],
        "km_graph_to_registry_bremerhaven": defect["km_to_registry_Bremerhaven"],
        "km_graph_to_registry_wilhelmshaven": defect["km_to_registry_Wilhelmshaven"],
        "note": ("graph.json places Bremerhaven at Wilhelmshaven's position (28.2 km from the registry's "
                 "Bremerhaven, 1.2 km from its Wilhelmshaven). this note is not "
                 "applied to the graph: the sidecar changes no coordinate, and the fix belongs in a graph "
                 "rebuild. Consequence carried here: sea_wilhelmshaven has a same_complex_override and "
                 "sits 1.2 km from CORE sea_bremerhaven in any merged view."),
        "source": "data/graph_extra_candidates/audit.json core_coordinate_defects",
    }]
    basis_places: dict = {}
    for p in places:
        basis_places[p["capacity_basis"]] = basis_places.get(p["capacity_basis"], 0) + 1
    doc = {
        "schema": SCHEMA,
        "status": "SIDECAR to data/graph/graph.json (additive, opt-in; the sidecar never edits the graph "
                  "file). First slice. Structure only: shares and route lengths are MODELED.",
        "graph_pin": {
            "path": "data/graph/graph.json",
            "sha256": gsha,
            "canonical_hash": graph_hash(graph),
            "canonical_hash_note": "sha256 of the canonical JSON of the graph dict = engine.graph.Graph.hash "
                                   "= journal graph_hash",
            "version": graph.get("version"),
        },
        "built_from": {
            "builder": "server/graph_extra/build.py",
            "candidates_dir": "data/graph_extra_candidates",
            "candidates_sha256": cand_sha,
            "selection": {
                "places": list(SLICE_PLACES),
                "leg_blocks": list(SLICE_LEG_BLOCKS),
                "legs_also_touching": list(SERVICES_PORTS),
                "lane_families_enabled": list(ENABLED_FAMILIES),
                "lane_families_disabled": sorted(DISABLED_FAMILIES),
            },
        },
        "policy": POLICY,
        "places_extra": places,
        "legs_extra": legs,
        "lanes_extra": lanes,
        "lanes_disabled": disabled,
        "position_notes": notes,
        "provenance": {
            "split": {"places_capacity_basis": dict(sorted(basis_places.items())),
                      "legs_basis": {"MODELED": len(legs)}, "lanes_basis": {"MODELLED_GEOMETRY": len(lanes)}},
            "sources": sorted({s for p in places for s in p["sources"]}),
            "licensing": "IMF PortWatch registry (licence terms: see THIRD-PARTY.md); Wikipedia compilations are "
                         "CC BY-SA, only facts stored; geometry is analyst-placed waypoints (MODELLED).",
        },
    }
    doc["content_sha256"] = content_sha(doc)
    return doc


def main(argv: list[str]) -> int:
    doc = build()
    text = dumps(doc)
    fsha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    print(f"extra.json: {len(doc['places_extra'])} places, {len(doc['legs_extra'])} legs, "
          f"{len(doc['lanes_extra'])} lanes (+{len(doc['lanes_disabled'])} disabled)")
    print(f"content_sha256 {doc['content_sha256']}")
    print(f"file sha256    {fsha}")
    if "--write" in argv:
        OUT.write_text(text, encoding="utf-8")
        PIN.write_text(fsha + "\n", encoding="utf-8")
        print(f"wrote {OUT.relative_to(REPO_ROOT)} and {PIN.relative_to(REPO_ROOT)}")
    if "--check" in argv:
        ok = OUT.exists() and OUT.read_text(encoding="utf-8") == text \
            and PIN.exists() and PIN.read_text().strip() == fsha
        print("check:", "OK" if ok else "MISMATCH")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
