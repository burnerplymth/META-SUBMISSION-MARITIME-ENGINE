"""Land test against Natural Earth 1:50m land polygons (pure Python, no dependencies).

``data/ne_land_50m.geojson`` is the Natural Earth ``land`` layer at 1:50,000,000, taken from the
``world-atlas`` npm package v2.0.2 (``land-50m.json``, TopoJSON, sha256
``619477ff690c086885e45cb91707d783805561bd75ae8e437b7d4694b0204e0f``), de-quantised, stitched to one
MultiPolygon and rounded to 3 decimals (about 110 m). Natural Earth is public domain.
Coordinates are (lon, lat) degrees; lines between two vertices are straight in lon/lat, which is the
convention of every polyline this package produces (vertices are at most 1 degree apart).

The land layer is a coastline at a scale of 1:50 million. It does not separate narrow straits and
canals (Suez, Panama, the Bosphorus), and river and harbour ports lie inside it. Both are handled
explicitly by the callers (``CANAL_CUTS`` below and the port-approach rule in ``geometry.py``).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

LAND_PATH = Path(__file__).resolve().parent / "data" / "ne_land_50m.geojson"
LAND_SOURCE = {
    "dataset": "Natural Earth land, 1:50m (public domain), via npm world-atlas 2.0.2 land-50m.json",
    "topojson_sha256": "619477ff690c086885e45cb91707d783805561bd75ae8e437b7d4694b0204e0f",
}
EARTH_R_NM = 3440.065
CELL = 1.0

# Cuts: stretches of the 1:50m land layer that ships do cross. A land run whose entry and exit both lie
# in one cut's box is reported as a cut, never as a land crossing; the box and the centre line are
# explicit data below. CANALS are the two named here (Suez, Panama). STRAITS are natural straits
# narrower than the 1:50m layer resolves (the layer closes them with a land bridge); each one is listed
# and counted in the build report. Nothing else is whitelisted.
CANAL_CUTS = (
    {"id": "suez", "kind": "canal", "name": "Suez Canal", "bbox": (32.15, 29.80, 32.75, 31.45),
     "line": ((32.31, 31.28), (32.33, 31.00), (32.33, 30.80), (32.27, 30.60), (32.35, 30.40),
              (32.40, 30.25), (32.45, 30.10), (32.55, 29.93), (32.57, 29.85))},
    {"id": "panama", "kind": "canal", "name": "Panama Canal", "bbox": (-80.05, 8.85, -79.40, 9.45),
     "line": ((-79.92, 9.38), (-79.80, 9.28), (-79.70, 9.20), (-79.60, 9.05), (-79.55, 8.93))},
)
STRAIT_CUTS: tuple = (
    {"id": "bosporus", "kind": "strait", "name": "Bosphorus", "bbox": (28.90, 40.95, 29.30, 41.35),
     "line": ((29.00, 40.99), (29.03, 41.08), (29.07, 41.17), (29.13, 41.24))},
    {"id": "dardanelles", "kind": "strait", "name": "Dardanelles", "bbox": (26.05, 39.95, 27.05, 40.60),
     "line": ((26.18, 40.03), (26.25, 40.10), (26.36, 40.17), (26.45, 40.25), (26.60, 40.35),
              (26.70, 40.42), (26.90, 40.50))},
)
ALL_CUTS = CANAL_CUTS + STRAIT_CUTS


def gc_nm(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2.0 * EARTH_R_NM * math.asin(min(1.0, math.sqrt(h)))


def wrap_lon(lon: float) -> float:
    return ((lon + 180.0) % 360.0) - 180.0


def in_canal_cut(lon: float, lat: float) -> str | None:
    lon = wrap_lon(lon)
    for c in ALL_CUTS:
        x0, y0, x1, y1 = c["bbox"]
        if x0 <= lon <= x1 and y0 <= lat <= y1:
            return c["id"]
    return None


def _clip(ring: list, axis_lo: float | None, axis_hi: float | None) -> list:
    """Sutherland-Hodgman clip of a ring to axis_lo <= x <= axis_hi (either may be None)."""
    def side(pts, bound, keep_ge):
        out = []
        n = len(pts)
        for i in range(n):
            a, b = pts[i - 1], pts[i]
            ina = a[0] >= bound if keep_ge else a[0] <= bound
            inb = b[0] >= bound if keep_ge else b[0] <= bound
            if inb:
                if not ina:
                    t = (bound - a[0]) / (b[0] - a[0])
                    out.append((bound, a[1] + t * (b[1] - a[1])))
                out.append((b[0], b[1]))
            elif ina:
                t = (bound - a[0]) / (b[0] - a[0])
                out.append((bound, a[1] + t * (b[1] - a[1])))
        return out
    pts = [(p[0], p[1]) for p in ring]
    if axis_lo is not None:
        pts = side(pts, axis_lo, True)
    if axis_hi is not None and pts:
        pts = side(pts, axis_hi, False)
    return pts


def normalise_ring(ring: list) -> list:
    """Rings of the land layer that cross the antimeridian are stored continuous (a +-180 jump between
    neighbours). Unwrap such a ring and cut it at +-180 into pieces that each lie inside [-180, 180];
    the polar cap (Antarctica) is dropped (no sea lane goes there). Other rings are returned as they are."""
    if any(p[1] < -85.0 for p in ring):
        return []
    n = len(ring)
    jump = any(abs(ring[i][0] - ring[(i + 1) % n][0]) > 180.0 for i in range(n))
    if not jump:
        return [ring]
    un = [(ring[0][0], ring[0][1])]
    for p in ring[1:]:
        x = p[0] + 360.0 * round((un[-1][0] - p[0]) / 360.0)
        un.append((x, p[1]))
    out = []
    for lo, hi, shift in ((-180.0, 180.0, 0.0), (180.0, 540.0, -360.0), (-540.0, -180.0, 360.0)):
        piece = _clip(un, lo, hi)
        if len(piece) < 3:
            continue
        area = sum(piece[i - 1][0] * piece[i][1] - piece[i][0] * piece[i - 1][1] for i in range(len(piece))) / 2.0
        if abs(area) < 1e-6:
            continue
        out.append([(x + shift, y) for x, y in piece])
    return out


class Land:
    def __init__(self, path: Path | str | None = None):
        doc = json.loads(Path(path or LAND_PATH).read_text(encoding="utf-8"))
        ex1: list = []
        ey1: list = []
        ex2: list = []
        ey2: list = []
        for feat in doc["features"]:
            geom = feat["geometry"]
            polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
            for poly in polys:
                for ring in poly:
                    for r in normalise_ring(ring):
                        n = len(r)
                        for i in range(n):
                            a, b = r[i], r[(i + 1) % n]
                            if a[0] == b[0] and a[1] == b[1]:
                                continue
                            ex1.append(float(a[0]))
                            ey1.append(float(a[1]))
                            ex2.append(float(b[0]))
                            ey2.append(float(b[1]))
        self.x1, self.y1, self.x2, self.y2 = ex1, ey1, ex2, ey2
        cells: dict = {}
        for i in range(len(ex1)):
            lo_x, hi_x = (ex1[i], ex2[i]) if ex1[i] <= ex2[i] else (ex2[i], ex1[i])
            lo_y, hi_y = (ey1[i], ey2[i]) if ey1[i] <= ey2[i] else (ey2[i], ey1[i])
            for cx in range(self._ix(lo_x), self._ix(hi_x) + 1):
                for cy in range(self._iy(lo_y), self._iy(hi_y) + 1):
                    cells.setdefault((cx, cy), []).append(i)
        self.cells = cells

    # ---- cell helpers -------------------------------------------------
    @staticmethod
    def _ix(x: float) -> int:
        return max(0, min(359, int(math.floor(x / CELL)) + 180))

    @staticmethod
    def _iy(y: float) -> int:
        return max(0, min(179, int(math.floor(y / CELL)) + 90))

    def near_coast(self, lon: float, lat: float) -> bool:
        """True when the 1-degree cell of the point, or one next to it, holds a coastline edge."""
        cx, cy = self._ix(wrap_lon(lon)), self._iy(lat)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if (cx + dx, cy + dy) in self.cells:
                    return True
        return False

    # ---- point in land --------------------------------------------------
    def is_land(self, lon: float, lat: float) -> bool:
        """Even-odd ray cast to the east along the point's latitude (holes count as water)."""
        lon = wrap_lon(lon)
        cy = self._iy(lat)
        cx0 = self._ix(lon)
        inside = False
        x1, y1, x2, y2 = self.x1, self.y1, self.x2, self.y2
        for cx in range(cx0, 360):
            lst = self.cells.get((cx, cy))
            if not lst:
                continue
            for i in lst:
                ya, yb = y1[i], y2[i]
                if (ya > lat) == (yb > lat):
                    continue
                xin = x1[i] + (lat - ya) * (x2[i] - x1[i]) / (yb - ya)
                if xin > lon and min(359, int(math.floor(xin / CELL)) + 180) == cx:
                    inside = not inside
        return inside

    # ---- segment crossings ----------------------------------------------
    def _seg_ts(self, ax: float, ay: float, bx: float, by: float) -> list:
        """Parameters t in [0, 1] where the straight lon/lat segment a-b crosses a coastline edge."""
        cxa, cxb = sorted((self._ix(ax), self._ix(bx)))
        cya, cyb = sorted((self._iy(ay), self._iy(by)))
        seen: set = set()
        ts: list = []
        dx, dy = bx - ax, by - ay
        x1, y1, x2, y2 = self.x1, self.y1, self.x2, self.y2
        for cx in range(cxa, cxb + 1):
            for cy in range(cya, cyb + 1):
                lst = self.cells.get((cx, cy))
                if not lst:
                    continue
                for i in lst:
                    if i in seen:
                        continue
                    seen.add(i)
                    ex, ey = x2[i] - x1[i], y2[i] - y1[i]
                    den = dx * ey - dy * ex
                    if den == 0.0:
                        continue
                    wx, wy = x1[i] - ax, y1[i] - ay
                    t = (wx * ey - wy * ex) / den
                    s = (wx * dy - wy * dx) / den
                    if 0.0 <= t <= 1.0 and 0.0 <= s < 1.0:
                        ts.append(t)
        ts.sort()
        return ts

    @staticmethod
    def _pieces(ax: float, ay: float, bx: float, by: float):
        """Split an (unwrapped) segment into pieces inside [-180, 180]: (ax, ay, bx, by, t0, t1)."""
        k = math.floor((ax + 180.0) / 360.0)
        ax, bx = ax - 360.0 * k, bx - 360.0 * k
        if -180.0 <= bx <= 180.0:
            return [(ax, ay, bx, by, 0.0, 1.0)]
        edge = 180.0 if bx > 180.0 else -180.0
        t = (edge - ax) / (bx - ax)
        ym = ay + t * (by - ay)
        out = [(ax, ay, edge, ym, 0.0, t)]
        # the rest lies in the neighbouring frame
        shift = -360.0 if bx > 180.0 else 360.0
        out.append((-edge, ym, bx + shift, by, t, 1.0))
        return out

    def land_runs(self, pts: list, start_land: bool | None = None) -> list:
        """Land runs along a polyline of (lon, lat) vertices (unwrapped longitudes allowed).

        Returns [{"from_nm", "to_nm", "nm", "enter", "exit"}] in order along the line, where enter/exit
        are (lon, lat). ``start_land`` is the state of the first vertex (computed when None)."""
        if len(pts) < 2:
            return []
        state = self.is_land(*pts[0]) if start_land is None else start_land
        runs: list = []
        cur_start = (0.0, pts[0]) if state else None
        along = 0.0
        for a, b in zip(pts, pts[1:]):
            seg_nm = gc_nm(a[0], a[1], b[0], b[1])
            for (ax, ay, bx, by, t0, t1) in self._pieces(a[0], a[1], b[0], b[1]):
                for t in self._seg_ts(ax, ay, bx, by):
                    tt = t0 + t * (t1 - t0)
                    pos = along + tt * seg_nm
                    pt = (a[0] + tt * (b[0] - a[0]), a[1] + tt * (b[1] - a[1]))
                    if not state:
                        cur_start = (pos, pt)
                        state = True
                    else:
                        runs.append({"from_nm": cur_start[0], "to_nm": pos, "nm": pos - cur_start[0],
                                     "enter": cur_start[1], "exit": pt})
                        cur_start = None
                        state = False
            along += seg_nm
        if state and cur_start is not None:
            runs.append({"from_nm": cur_start[0], "to_nm": along, "nm": along - cur_start[0],
                         "enter": cur_start[1], "exit": pts[-1]})
        return runs

    def classify_runs(self, runs: list) -> tuple[list, list]:
        """(canal cuts, land crossings): a run whose entry and exit both lie in one canal box is a cut."""
        cuts, bad = [], []
        for r in runs:
            a = in_canal_cut(*r["enter"])
            b = in_canal_cut(*r["exit"])
            if a is not None and a == b:
                cuts.append({**r, "canal": a})
            else:
                bad.append(r)
        return cuts, bad

    def segment_ok(self, a: tuple, b: tuple) -> bool:
        """True when a-b meets no coastline edge, or only inside a whitelisted cut."""
        if self.segment_clear(a, b):
            return True
        _cuts, bad = self.classify_runs(self.land_runs([a, b]))
        return not bad

    def segment_clear(self, a: tuple, b: tuple) -> bool:
        """True when the straight lon/lat segment a-b meets no coastline edge (so it is all water if a is)."""
        for (ax, ay, bx, by, _t0, _t1) in self._pieces(a[0], a[1], b[0], b[1]):
            if self._seg_ts(ax, ay, bx, by):
                return False
        return True


_LAND: Land | None = None


def get_land() -> Land:
    global _LAND
    if _LAND is None:
        _LAND = Land()
    return _LAND
