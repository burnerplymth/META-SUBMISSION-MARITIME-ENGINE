#!/usr/bin/env python3
"""Extend server/satwatch/aois.json from the graph's own port places.

Selection rule (deterministic): the top-N CORE-tier ports of
data/graph/graph.json by served ``capacity``, skipping ports that
already have an AOI and the exclusions below. Each new AOI is a
GRAPH-POINT-CENTRED crop: ``core_bbox`` = the node's lat/lon +/- a fixed
half-extent; ``bbox`` = core + a search margin. These coordinates are
the graph's, nothing else: ``placement: "GRAPH_POINT_CENTRED"`` and
``anchorage_verified: false`` say so in the file and in every API
payload. A graph point is a port location, not an anchorage; whether
open-water anchorage lies inside the crop is NOT claimed. The scan
pipeline still enforces footprint coverage and AOI cloud screening, and
a human places/verifies a core before a count from it is trusted.

Inland river ports (graph point not at a coast) are excluded because a
point-centred crop there is land.

    .venv/bin/python scripts/derive_satwatch_aois.py [--top 25] [--write]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GRAPH = REPO / "data" / "graph" / "graph.json"
AOIS = REPO / "server" / "satwatch" / "aois.json"

HALF_LON, HALF_LAT = 0.09, 0.08   # core ~ 0.18 x 0.16 deg (~19 x 18 km), like the curated cores
MARGIN = 0.05                      # search bbox margin beyond the core

EXCLUDED = {
    "sea_guangzhou": "inland Pearl River port; graph point is land",
    "sea_taicang": "inland Yangtze river port; graph point is land",
    "sea_hochiminh": "inland Saigon river port; graph point is land",
}


def derive(top: int) -> list[dict]:
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    existing = {a["node_id"] for a in json.loads(AOIS.read_text())["aois"]}
    ports = [n for n in graph["nodes"]
             if n.get("kind") == "port" and n.get("tier", "core") == "core"
             and n.get("capacity")]
    ports.sort(key=lambda n: (-n["capacity"], n["id"]))
    out = []
    for rank, n in enumerate(ports[:top], start=1):
        if n["id"] in existing or n["id"] in EXCLUDED:
            continue
        lon, lat = round(n["lon"], 4), round(n["lat"], 4)
        core = [round(lon - HALF_LON, 4), round(lat - HALF_LAT, 4),
                round(lon + HALF_LON, 4), round(lat + HALF_LAT, 4)]
        bbox = [round(core[0] - MARGIN, 4), round(core[1] - MARGIN, 4),
                round(core[2] + MARGIN, 4), round(core[3] + MARGIN, 4)]
        out.append({
            "node_id": n["id"],
            "name": n["name"],
            "bbox": bbox,
            "core_bbox": core,
            "placement": "GRAPH_POINT_CENTRED",
            "anchorage_verified": False,
            "capacity_rank": rank,
            "capacity": n["capacity"],
            "capacity_unit": n.get("capacity_unit"),
            "notes": ("Derived from the graph node point "
                      f"({lat}, {lon}); not a verified anchorage. Footprint "
                      "coverage is checked at scan time."),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    new = derive(args.top)
    for a in new:
        print(f"{a['capacity_rank']:>3} {a['node_id']:<22} core={a['core_bbox']}")
    if args.write and new:
        doc = json.loads(AOIS.read_text(encoding="utf-8"))
        have = {a["node_id"] for a in doc["aois"]}
        doc["aois"] += [a for a in new if a["node_id"] not in have]
        AOIS.write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False) + "\n",
                        encoding="utf-8")
        print(f"wrote {len(new)} AOIs -> {AOIS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
