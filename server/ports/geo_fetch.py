"""Network side of the Port Twin: OSM API tile fetch with an on-disk
cache, and Sentinel-2 true-colour crops.

Not imported by the API routes: only by scripts/build_port_geo.py.

OSM API usage policy: descriptive User-Agent, ~1 request per few
seconds, everything cached on disk and never re-fetched, 0.25 sq-degree
and 50,000-node limits (a 400 answer splits the tile in four).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .geo_catalog import tiles_for

OSM_API = "https://api.openstreetmap.org/api/0.6/map"
USER_AGENT = ("supermap-port-twin/1.0 (SupermAP; "
              "port-geometry research build; cached, <=1 req/3s)")
MIN_INTERVAL_S = 3.0
MAX_SPLIT_DEPTH = 4


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tile_key(b: list) -> str:
    return "_".join(f"{v:.5f}" for v in b)


class OsmCache:
    """cache_dir/<key>.osm.gz + cache_dir/index.json (key -> meta)."""

    def __init__(self, cache_dir: Path):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.json"
        self.index = (json.loads(self.index_path.read_text())
                      if self.index_path.exists() else {})
        self._last = 0.0

    def save_index(self):
        self.index_path.write_text(
            json.dumps(self.index, sort_keys=True, indent=1))

    def path(self, key: str) -> Path:
        return self.dir / f"{key}.osm.gz"

    def _http(self, b: list):
        url = f"{OSM_API}?bbox={b[0]:.5f},{b[1]:.5f},{b[2]:.5f},{b[3]:.5f}"
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
        wait = MIN_INTERVAL_S - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return r.status, raw
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return 0, str(exc).encode()

    def get_tile(self, b: list, depth: int = 0, log=print) -> list[str]:
        """Return the cache keys of the files that cover tile `b` (one,
        or the 4 sub-tiles' keys when the API said too many nodes)."""
        key = tile_key(b)
        meta = self.index.get(key)
        if meta and meta.get("status") == "ok" and self.path(key).exists():
            return [key]
        if meta and meta.get("status") == "failed":
            meta = None
            self.index.pop(key, None)
        if meta and meta.get("status") == "split":
            out = []
            for sb in _quad(b):
                out += self.get_tile(sb, depth + 1, log)
            return out
        for attempt in range(4):
            status, raw = self._http(b)
            if status == 200:
                with gzip.open(self.path(key), "wb", compresslevel=6,
                               ) as f:
                    f.write(raw)
                self.index[key] = {
                    "status": "ok", "bbox": b, "retrieved": _now(),
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest()}
                self.save_index()
                log(f"  fetched {key} {len(raw)//1024} KB")
                return [key]
            if status == 400 and depth < MAX_SPLIT_DEPTH and b"node" in raw.lower():
                log(f"  {key}: too many nodes -> split")
                self.index[key] = {"status": "split", "bbox": b,
                                   "retrieved": _now()}
                self.save_index()
                out = []
                for sb in _quad(b):
                    out += self.get_tile(sb, depth + 1, log)
                return out
            log(f"  {key}: HTTP {status} {raw[:80]!r}; retry {attempt}")
            time.sleep(15 * (attempt + 1))
        self.index[key] = {"status": "failed", "bbox": b, "retrieved": _now()}
        self.save_index()
        log(f"  {key}: GAVE UP (recorded as failed; will be retried next run)")
        return []

    def port_keys(self, bbox: list, log=print) -> list[str]:
        keys = []
        for t in tiles_for(bbox):
            keys += self.get_tile(t, log=log)
        return keys


def _quad(b: list) -> list[list]:
    mx = round((b[0] + b[2]) / 2, 5)
    my = round((b[1] + b[3]) / 2, 5)
    return [[b[0], b[1], mx, my], [mx, b[1], b[2], my],
            [b[0], my, mx, b[3]], [mx, my, b[2], b[3]]]
