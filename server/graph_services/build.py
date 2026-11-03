"""Build the services SIDECAR (data/graph/services.json) from the research
lane's candidates file.

    cd server && ../.venv/bin/python -m graph_services.build [--write]

Nothing here touches data/graph/graph.json (its sha256 is asserted). The
build is a pure, deterministic transformation: no call is guessed, none is
invented. An unmapped call (place_id null) is DROPPED and the gap is
recorded, so adjacency across it is never created. Marker positions are
re-indexed from the candidates' original call list onto the mapped list.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = REPO_ROOT / "data" / "graph_services_candidates" / "services.candidates.json"
GRAPH = REPO_ROOT / "data" / "graph" / "graph.json"
OUT = REPO_ROOT / "data" / "graph" / "services.json"
PIN = REPO_ROOT / "data" / "graph" / "services.sha256"

SCHEMA = "supermap.graph_services/1"

POLICY = {
    "default_selection": {
        "include_unverified": False,
        "include_unstated_routing": False,
        "eras": "auto (by the run's start_at: < 2023-11-01 pre_red_sea_crisis; "
                "2023-11-01 .. 2026-06-30 red_sea_diversion; from 2026-07-01 "
                "red_sea_diversion + partial_suez_return_2026)",
    },
    "unverified_policy": (
        "The 21 entries whose excerpt could only be fetched through WebFetch's "
        "extractor (Ocean Alliance Day 9, Apr 2025, 17 entries; Day 3, 2019, "
        "4 entries) carry flag webfetch_not_machine_verified. They cannot be "
        "diffed against the saved page text, so they are EXCLUDED from every "
        "default selection and enter a run only when the body says "
        "include_unverified: true (journaled)."),
    "unstated_routing_policy": (
        "Entries whose routing basis is unstated, article_text_indirect or "
        "conflicting_sources (and the one article_text entry that names no "
        "chokepoint) say nothing positive about a chokepoint. They are kept in "
        "the sidecar for audit and excluded by default; include_unstated: true "
        "admits them (their legs are then local legs with no stated chokepoint)."),
    "leg_rule": (
        "A leg is two consecutive MAPPED calls with no dropped call between "
        "them. The loop closes (last call to first) only where the source "
        "prints a chokepoint marker after the last call. A call that repeats "
        "the previous call makes no leg. Entries with a sequence_caveat build "
        "no leg between the two sides of the Suez basin."),
    "via_rule": (
        "A chokepoint is on a leg when the source prints its marker after the "
        "leg's origin call (SOURCED, positioned); when the source states the "
        "routing for the whole service or the era default applies, it is on "
        "the legs that cross the basin by zone (MODELLED placement, "
        "graph_services.zones); Bab el-Mandeb is added to a Suez leg whose "
        "eastern end lies outside the Red Sea (MODELLED, geographic "
        "necessity: no other water connection)."),
    "frequency": "null unless the source states it; never invented",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_graph_hash(graph_path: Path) -> str:
    return hashlib.sha256(canonical(json.loads(graph_path.read_text(encoding="utf-8")))
                          .encode("utf-8")).hexdigest()


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_sha(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k != "content_sha256"}
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def _entry(s: dict) -> dict:
    calls_in = s["calls"]
    mapped: list[str] = []
    quality: list[str] = []
    notes: list[dict] = []
    gaps: list[int] = []
    dropped: list[dict] = []
    orig_to_mapped: dict[int, int] = {}      # original index -> mapped index (mapped calls only)
    last_mapped_before: dict[int, int | None] = {}
    prev_unmapped = False
    for i, c in enumerate(calls_in):
        pid = c.get("place_id")
        last_mapped_before[i] = len(mapped) - 1 if mapped else None
        if pid is None:
            dropped.append({"raw": c["raw"], "after_call": len(mapped) - 1 if mapped else None,
                            "reason": c.get("map_note") or c.get("map_quality")})
            prev_unmapped = True
            continue
        if prev_unmapped and mapped:
            gaps.append(len(mapped) - 1)      # no leg between mapped[-1] and the next call
        prev_unmapped = False
        orig_to_mapped[i] = len(mapped)
        mapped.append(pid)
        quality.append(c.get("map_quality") or "unknown")
        if c.get("alt_place_ids"):
            notes.append({"call": len(mapped) - 1, "alt_place_ids": list(c["alt_place_ids"])})
    r = s["routing"]
    via = []
    unplaced = []
    for m in (r.get("via_stated_markers") or []):
        oi = m["after_call_index"]
        if oi in orig_to_mapped and (oi + 1 in orig_to_mapped or oi == len(calls_in) - 1):
            via.append({"chk": m["chk"], "after_call": orig_to_mapped[oi]})
        else:
            unplaced.append({"chk": m["chk"], "after_call_index": oi})
    via.sort(key=lambda v: (v["after_call"],))
    basis = r["basis"]
    positioned = {v["chk"] for v in via}
    service_level = []
    if basis in ("article_text",):
        service_level = [c for c in r.get("via_stated", []) if c not in positioned]
    elif basis == "marker_in_rotation_string":
        service_level = []
    era_default = list(r.get("era_default_via") or [])
    flags = []
    if s["evidence"]["excerpt_check"].startswith("webfetch"):
        flags.append("webfetch_not_machine_verified")
    if basis == "conflicting_sources":
        flags.append("conflicting_sources")
    if basis == "unstated":
        flags.append("routing_unstated")
    if basis == "article_text_indirect":
        flags.append("routing_indirect")
    if basis == "article_text" and not (r.get("via_stated") or era_default):
        flags.append("routing_unstated")
    if s.get("sequence_caveat"):
        flags.append("sequence_caveat")
    if era_default:
        flags.append("era_default_via")
    if unplaced:
        flags.append("marker_unplaced")
    if dropped:
        flags.append("has_dropped_calls")
    sources = sorted({s["evidence"]["source_id"]}
                     | ({r["statement_source"]} if r.get("statement_source") else set()))
    e = {
        "service_id": s["service_id"],
        "mode": "sea",
        "operator": s["operator"],
        "code": s["code"],
        "trade": s["trade"],
        "era": s["era"],
        "published": s["published"],
        "loop": bool(s["loop"]),
        "calls": mapped,
        "call_quality": quality,
        "gaps": sorted(set(gaps)),
        "dropped_calls": dropped,
        "via": via,
        "via_service_level": service_level,
        "via_era_default": era_default,
        "via_basis": basis if not (basis == "unstated" and era_default) else "era_default_via",
        "via_geo_inferred": list(r.get("via_geo_inferred") or []),
        "via_geo_inferred_basis": "analyst-computed from call geography; MODELLED; never used to place a leg",
        "frequency": s.get("frequency"),
        "frequency_basis": s.get("frequency_basis"),
        "flags": sorted(flags),
        "calibration": {
            "basis": "SOURCED",
            "sources": sources,
            "retrieved": "2026-10-25",
            "excerpt": s["evidence"]["excerpt"],
            "excerpt_check": ("machine_verified" if s["evidence"]["excerpt_check"].startswith("machine")
                              else "webfetch_not_machine_verified"),
        },
    }
    if notes:
        e["call_notes"] = notes
    if unplaced:
        e["via_unplaced"] = unplaced
    if r.get("statement"):
        e["routing_statement"] = {"text": r["statement"], "kind": r.get("route_text"),
                                  "source": r.get("statement_source")}
    if s.get("sequence_caveat"):
        e["sequence_caveat"] = s["sequence_caveat"]
    if s.get("notes"):
        e["notes"] = s["notes"]
    return e


def build(candidates_path: Path = CANDIDATES, graph_path: Path = GRAPH) -> dict:
    cand = json.loads(candidates_path.read_text(encoding="utf-8"))
    graph_sha = _sha(graph_path)
    pin = cand["graph_pin"]["sha256"]
    if graph_sha != pin:
        raise SystemExit(f"graph sha256 {graph_sha} != candidates pin {pin}")
    entries = sorted((_entry(s) for s in cand["services"]), key=lambda e: e["service_id"])
    used = sorted({k for e in entries for k in e["calibration"]["sources"]})
    sources = {k: cand["sources"][k] for k in used}
    n_calls = sum(len(e["calls"]) for e in entries)
    n_drop = sum(len(e["dropped_calls"]) for e in entries)
    doc = {
        "schema": SCHEMA,
        "status": ("SIDECAR to data/graph/graph.json (additive, opt-in; the sidecar never "
                   "edits the graph file). Entries are SOURCED rotations with "
                   "provenance; derived placement is labelled MODELLED."),
        "graph_pin": {"path": "data/graph/graph.json", "sha256": graph_sha,
                      "canonical_hash": _canonical_graph_hash(graph_path),
                      "canonical_hash_note": ("sha256 of the canonical JSON of the graph dict = "
                                              "engine.graph.Graph.hash = journal graph_hash"),
                      "version": 1.0},
        "built_from": {"path": "data/graph_services_candidates/services.candidates.json",
                       "sha256": _sha(candidates_path)},
        "policy": POLICY,
        "provenance": {
            "sources": sources,
            "split": {
                "SOURCED": ("service rotations (ordered calls), published dates, eras, stated "
                            "chokepoint markers and statements, excerpts"),
                "MODELLED": ("placement of a service-level routing statement on the legs that "
                             "cross the basin (zones), Bab el-Mandeb on Suez legs, the era "
                             "default for the 11 Ocean Alliance Day 7 loops, any frequency"),
            },
            "licensing": ("port sequences are facts; the pages are copyrighted trade press and "
                          "carrier releases. Only sequences and a first-25-word excerpt are "
                          "stored. Entries are announcements, not proof of sailed routes."),
            "counts": {"services": len(entries), "calls_mapped": n_calls,
                       "calls_dropped_unmapped": n_drop,
                       "sources": len(sources)},
        },
        "services": entries,
    }
    doc["content_sha256"] = content_sha(doc)
    return doc


def dumps(doc: dict) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    doc = build()
    text = dumps(doc)
    if "--write" in argv:
        OUT.write_text(text, encoding="utf-8")
        PIN.write_text(hashlib.sha256(text.encode("utf-8")).hexdigest() + "\n", encoding="utf-8")
        print(f"wrote {OUT} ({len(text)} bytes) sha256 "
              f"{hashlib.sha256(text.encode('utf-8')).hexdigest()}")
    else:
        print(json.dumps({"services": len(doc["services"]),
                          "content_sha256": doc["content_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
