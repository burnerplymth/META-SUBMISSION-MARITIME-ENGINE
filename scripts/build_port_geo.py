#!/usr/bin/env python3
"""Port Twin builder.

  build_port_geo.py fetch   [--cache DIR] [--ports a,b]   # OSM API tiles -> cache
  build_port_geo.py build   [--cache DIR] [--out data/ports_geo]
  build_port_geo.py imagery [--out data/ports_geo] [--ports a,b]

`fetch` and `imagery` use the network and are polite (cached, rate
limited).  `build` is offline and byte-reproducible from the cache.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))

from ports import geo_catalog  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "build", "imagery"])
    ap.add_argument("--cache", default=str(REPO / ".cache" / "osm"))
    ap.add_argument("--out", default=str(REPO / "data" / "ports_geo"))
    ap.add_argument("--ports", default="")
    ap.add_argument("--retrieved", default="")
    a = ap.parse_args(argv)
    ids = [p for p in a.ports.split(",") if p] or list(geo_catalog.PORTS)
    unknown = [p for p in ids if p not in geo_catalog.PORTS]
    if unknown:
        raise SystemExit(f"unknown ports: {unknown}")
    if a.cmd == "fetch":
        from ports.geo_fetch import OsmCache
        cache = OsmCache(Path(a.cache))
        for pid in ids:
            print(pid, flush=True)
            cache.port_keys(geo_catalog.PORTS[pid]["bbox"], log=lambda m: print(m, flush=True))
    elif a.cmd == "build":
        from ports import geo
        geo.build_all(Path(a.cache), Path(a.out), ids)
    else:
        from ports import geo_imagery
        geo_imagery.build_imagery(Path(a.out), ids)


if __name__ == "__main__":
    main()
