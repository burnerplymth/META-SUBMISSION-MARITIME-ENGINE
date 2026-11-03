#!/usr/bin/env python3
"""Build data/graph/lane_geometry.json: smoothed, land-checked sea-lane polylines.

SIDECAR derived from data/graph/sea_routes.json (hash-pinned, read only) and the Natural Earth 1:50m
land layer vendored at server/lanegeo/data/ne_land_50m.geojson. Deterministic, pure Python, no network,
no extra packages (about 40 s). After a rebuild, update the pin in server/lanegeo/pin.py.

    python scripts/build_lane_geometry.py            # write data/graph/lane_geometry.json
    python scripts/build_lane_geometry.py --check    # rebuild in memory and compare with the file
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))

from lanegeo import build  # noqa: E402


def main(argv) -> int:
    t0 = time.time()
    doc = build.build(progress=lambda n, dt, st: print(f"  {n} pairs, {dt:.0f}s, router {st}", file=sys.stderr))
    print(json.dumps(doc["stats"], indent=1))
    print("content_sha256", doc["content_sha256"])
    if "--check" in argv:
        have = json.loads(build.OUT.read_text(encoding="utf-8"))
        same = have.get("content_sha256") == doc["content_sha256"]
        print("matches the file on disk" if same else "DIFFERS from the file on disk")
        return 0 if same else 1
    sha = build.write(doc)
    print("wrote", build.OUT, "file sha256", sha, f"({time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
