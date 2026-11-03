"""PORT TWIN geometry: OSM -> flat port map layers + port profile.

Pipeline (all pure and deterministic; the network lives in geo_fetch.py):

  OSM API XML tiles --parse_osm--> nodes/ways/relations
      --classify--> one `layer` per feature (water, coastline, quay, ...)
      --clip to the port rectangle, Douglas-Peucker (fixed metres),
        quantise to 1e-5 deg, sort--> GeoJSON FeatureCollection
      --build_profile--> port-profile/1 (berth groups, anchorage, yard,
        cranes) for the port simulation.

Honesty rules carried by every output:
  * the vector layers are OpenStreetMap data (c) OpenStreetMap
    contributors, ODbL 1.0, with the retrieval date;
  * everything in the profile derived from geometry is a MODELLED
    estimate (berths = quay length / mean ship length; yard TEU = area /
    m2 per TEU; anchorage slots = area / swing-circle area) and says so;
  * a port whose OSM coverage is thin simply has small counts: nothing is
    invented, and counts are never padded.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from . import geo_catalog

ATTRIBUTION = "(c) OpenStreetMap contributors"
LICENSE = "ODbL 1.0 - https://opendatacommons.org/licenses/odbl/1-0/"
COPYRIGHT_URL = "https://www.openstreetmap.org/copyright"
SCHEMA_GEO = "port-geo/1"
SCHEMA_PROFILE = "port-profile/1"
EARTH_R = 6371008.8

LAYER_ORDER = ["water", "coastline", "terminal", "yard", "storage",
               "building", "rail", "road", "breakwater", "pier", "quay",
               "berth", "lock", "fairway", "anchorage", "crane"]
POINT_LAYERS = {"crane", "berth", "lock"}
LINE_ONLY = {"road", "rail", "coastline", "quay", "berth", "crane"}
AREA_ONLY = {"terminal", "yard", "water", "building", "anchorage", "storage"}
# closed way -> polygon for everything else except these (need area=yes)
LINE_UNLESS_AREA = {"fairway"}

DP_TOL_M = {"water": 6.0, "coastline": 4.0, "terminal": 5.0, "yard": 4.0,
            "storage": 3.0, "building": 2.5, "rail": 4.0, "road": 4.0,
            "breakwater": 3.0, "pier": 2.5, "quay": 2.0, "berth": 2.0,
            "lock": 2.0, "fairway": 8.0, "anchorage": 15.0, "crane": 0.0}
QUANT = 1e5                     # 1e-5 degree ~ 1.1 m
SIZE_BUDGET = 380_000           # bytes per port file (target: < ~400 KB)
MIN_AREA_M2 = {"water": 2500.0, "terminal": 2000.0, "yard": 1500.0,
               "storage": 60.0, "building": 600.0, "anchorage": 50000.0}

ROAD_CLASSES = {"motorway", "motorway_link", "trunk", "trunk_link",
                "primary", "primary_link", "secondary", "secondary_link",
                "tertiary", "tertiary_link", "unclassified", "service"}
FAIRWAY_SEAMARK = {"fairway", "recommended_track", "dredged_area",
                   "navigation_line", "two-way_route", "recommended_route_centreline"}

KIND_PATTERNS = [
    ("container", re.compile(
        r"container|\bteu\b|\bcts\b|\bict\b|\bect\b|\bgct\b|\bmct\b|\brwg\b|"
        r"apm terminals|\bpsa\b|dp world|hutchison|cosco|maersk|"
        r"集装箱|货柜|컨테이너", re.I)),
    ("tanker", re.compile(
        r"\boil\b|petro|\btank|\blng\b|\blpg\b|chemic|refiner|\bfuel\b|"
        r"crude|油|化工|石化", re.I)),
    ("bulk", re.compile(
        r"\bbulk|\bcoal\b|\bore\b|grain|cement|potash|fertili|mineral|"
        r"煤|矿|散货", re.I)),
    ("ro-ro", re.compile(
        r"ro[- ]?ro\b|vehicle|car terminal|\bcars\b|automobile|汽车|滚装",
        re.I)),
    ("general", re.compile(
        r"general cargo|multi.?purpose|break.?bulk|多用途|杂货", re.I)),
]
CARGO_TAG_KIND = {"container": "container", "oil": "tanker", "fuel": "tanker",
                  "lng": "tanker", "gas": "tanker", "chemical": "tanker",
                  "bulk": "bulk", "dry_bulk": "bulk", "coal": "bulk",
                  "grain": "bulk", "ore": "bulk", "vehicle": "ro-ro",
                  "cars": "ro-ro", "roro": "ro-ro", "general": "general",
                  "multipurpose": "general", "passengers": "ro-ro"}
TERMINAL_NAME = re.compile(r"\b(terminal|container|cts|teu)\b|码头|集装箱", re.I)


# ---------------------------------------------------------------------------
# projection helpers (local equirectangular, metres)
# ---------------------------------------------------------------------------

class Proj:
    def __init__(self, bbox):
        self.lon0 = (bbox[0] + bbox[2]) / 2
        self.lat0 = (bbox[1] + bbox[3]) / 2
        self.kx = math.radians(1) * EARTH_R * math.cos(math.radians(self.lat0))
        self.ky = math.radians(1) * EARTH_R

    def fwd(self, lon, lat):
        return ((lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky)

    def path(self, pts):
        return [self.fwd(x, y) for x, y in pts]


def ring_area(pts) -> float:
    """Signed shoelace area of a closed or open ring in projected units."""
    a = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return a / 2.0


def path_length(pts) -> float:
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
               for i in range(len(pts) - 1))


def point_at_half(pts):
    total = path_length(pts)
    if total == 0:
        return pts[0]
    half, acc = total / 2, 0.0
    for i in range(len(pts) - 1):
        seg = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        if acc + seg >= half and seg > 0:
            f = (half - acc) / seg
            return (pts[i][0] + f * (pts[i + 1][0] - pts[i][0]),
                    pts[i][1] + f * (pts[i + 1][1] - pts[i][1]))
        acc += seg
    return pts[-1]


def point_in_ring(pt, ring) -> bool:
    x, y = pt
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def dist_pt_seg(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    if L == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def dist_pt_ring(p, ring) -> float:
    return min(dist_pt_seg(p, ring[i], ring[(i + 1) % len(ring)])
               for i in range(len(ring)))


def douglas_peucker(pts, tol):
    """Iterative Douglas-Peucker on a list of (x, y); deterministic (first
    farthest vertex wins ties). Keeps both end points."""
    n = len(pts)
    if n < 3 or tol <= 0:
        return list(pts)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i0, i1 = stack.pop()
        a, b = pts[i0], pts[i1]
        dmax, idx = -1.0, -1
        for i in range(i0 + 1, i1):
            d = dist_pt_seg(pts[i], a, b)
            if d > dmax:
                dmax, idx = d, i
        if dmax > tol:
            keep[idx] = True
            stack.append((i0, idx))
            stack.append((idx, i1))
    return [p for p, k in zip(pts, keep) if k]


# ---------------------------------------------------------------------------
# clipping to the port rectangle (lon/lat space)
# ---------------------------------------------------------------------------

def _outcode(x, y, r):
    c = 0
    if x < r[0]:
        c |= 1
    elif x > r[2]:
        c |= 2
    if y < r[1]:
        c |= 4
    elif y > r[3]:
        c |= 8
    return c


def clip_segment(p, q, r):
    """Cohen-Sutherland; returns (p', q') or None."""
    x0, y0 = p
    x1, y1 = q
    c0, c1 = _outcode(x0, y0, r), _outcode(x1, y1, r)
    while True:
        if not (c0 | c1):
            return (x0, y0), (x1, y1)
        if c0 & c1:
            return None
        c = c0 or c1
        if c & 8:
            x = x0 + (x1 - x0) * (r[3] - y0) / (y1 - y0); y = r[3]
        elif c & 4:
            x = x0 + (x1 - x0) * (r[1] - y0) / (y1 - y0); y = r[1]
        elif c & 2:
            y = y0 + (y1 - y0) * (r[2] - x0) / (x1 - x0); x = r[2]
        else:
            y = y0 + (y1 - y0) * (r[0] - x0) / (x1 - x0); x = r[0]
        if c == c0:
            x0, y0, c0 = x, y, _outcode(x, y, r)
        else:
            x1, y1, c1 = x, y, _outcode(x, y, r)


def clip_polyline(pts, r):
    """Clip a polyline to rectangle r -> list of polylines."""
    out, cur = [], []
    for i in range(len(pts) - 1):
        seg = clip_segment(pts[i], pts[i + 1], r)
        if seg is None:
            if len(cur) > 1:
                out.append(cur)
            cur = []
            continue
        a, b = seg
        if cur and cur[-1] == a:
            cur.append(b)
        else:
            if len(cur) > 1:
                out.append(cur)
            cur = [a, b]
        if b != pts[i + 1]:
            if len(cur) > 1:
                out.append(cur)
            cur = []
    if len(cur) > 1:
        out.append(cur)
    return out


def clip_ring(ring, r):
    """Sutherland-Hodgman against rectangle r; ring is open (no repeat)."""
    def clip_edge(poly, inside, inter):
        out = []
        for i in range(len(poly)):
            a, b = poly[i - 1], poly[i]
            ia, ib = inside(a), inside(b)
            if ib:
                if not ia:
                    out.append(inter(a, b))
                out.append(b)
            elif ia:
                out.append(inter(a, b))
        return out
    ix = lambda xv: (lambda a, b: (xv, a[1] + (b[1] - a[1]) * (xv - a[0]) / (b[0] - a[0])))
    iy = lambda yv: (lambda a, b: (a[0] + (b[0] - a[0]) * (yv - a[1]) / (b[1] - a[1]), yv))
    poly = list(ring)
    for inside, inter in (
            (lambda p: p[0] >= r[0], ix(r[0])),
            (lambda p: p[0] <= r[2], ix(r[2])),
            (lambda p: p[1] >= r[1], iy(r[1])),
            (lambda p: p[1] <= r[3], iy(r[3]))):
        if not poly:
            return []
        poly = clip_edge(poly, inside, inter)
    return poly


# ---------------------------------------------------------------------------
# OSM parsing
# ---------------------------------------------------------------------------

class Osm:
    def __init__(self):
        self.nodes: dict[int, tuple] = {}
        self.ntags: dict[int, dict] = {}
        self.ways: dict[int, tuple] = {}      # id -> (refs, tags)
        self.rels: dict[int, tuple] = {}      # id -> (members, tags)

    def stats(self):
        return {"nodes": len(self.nodes), "ways": len(self.ways),
                "relations": len(self.rels)}


def parse_osm(files) -> Osm:
    """Parse one or more OSM XML (.xml or .gz) files into an Osm. Elements
    seen in several tiles are the same OSM object: the first (in sorted
    file order) wins."""
    osm = Osm()
    for fp in files:
        opener = gzip.open if str(fp).endswith(".gz") else open
        with opener(fp, "rb") as fh:
            for _ev, el in ET.iterparse(fh, events=("end",)):
                tag = el.tag
                if tag == "node":
                    i = int(el.get("id"))
                    if i not in osm.nodes:
                        osm.nodes[i] = (float(el.get("lon")), float(el.get("lat")))
                        tags = {t.get("k"): t.get("v") for t in el.iter("tag")}
                        if tags:
                            osm.ntags[i] = tags
                    el.clear()
                elif tag == "way":
                    i = int(el.get("id"))
                    if i not in osm.ways:
                        refs = tuple(int(n.get("ref")) for n in el.iter("nd"))
                        tags = {t.get("k"): t.get("v") for t in el.iter("tag")}
                        osm.ways[i] = (refs, tags)
                    el.clear()
                elif tag == "relation":
                    i = int(el.get("id"))
                    if i not in osm.rels:
                        tags = {t.get("k"): t.get("v") for t in el.iter("tag")}
                        if tags.get("type") == "multipolygon":
                            mem = tuple((m.get("type"), int(m.get("ref")), m.get("role") or "")
                                        for m in el.iter("member"))
                            osm.rels[i] = (mem, tags)
                    el.clear()
    return osm


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------

def best_name(tags: dict):
    for k in ("name:en", "int_name", "name"):
        v = tags.get(k)
        if v:
            return v.strip()[:80]
    return None


def terminal_kind(tags: dict) -> str:
    """Cargo kind of a terminal / quay from OSM tags, then its name."""
    for k in ("cargo", "product", "industrial", "harbour:category"):
        v = (tags.get(k) or "").lower()
        for part in re.split(r"[;,]", v):
            part = part.strip()
            if part == "container_terminal":
                return "container"
            if part in CARGO_TAG_KIND:
                return CARGO_TAG_KIND[part]
    text = " ".join(tags.get(k, "") for k in ("name", "name:en", "operator", "description"))
    for kind, rx in KIND_PATTERNS:
        if rx.search(text):
            return kind
    return "unknown"


def classify(tags: dict):
    """-> layer name or None. First matching rule wins."""
    g = tags.get
    seamark = g("seamark:type")
    if seamark == "anchorage":
        return "anchorage"
    if seamark in FAIRWAY_SEAMARK or g("waterway") == "fairway":
        return "fairway"
    if g("lock") == "yes" or g("waterway") in ("lock_gate", "lock"):
        return "lock"
    if g("man_made") in ("breakwater", "groyne") or seamark == "breakwater":
        return "breakwater"
    if g("man_made") in ("quay", "wharf"):
        return "quay"
    if seamark == "berth":
        return "berth"
    if g("man_made") in ("pier", "jetty"):
        return "pier"
    if g("man_made") == "crane":
        return "crane"
    if g("natural") == "coastline":
        return "coastline"
    if (g("natural") == "water" or g("waterway") in ("dock", "riverbank")
            or g("landuse") == "basin" or seamark == "harbour_basin"):
        return "water"
    if g("railway") in ("rail", "light_rail") and not g("service") in ("crossover",):
        return "rail"
    if g("highway") in ROAD_CLASSES:
        return "road"
    if (g("landuse") == "port" or g("industrial") in ("port", "container_terminal")
            or g("harbour") == "yes" or g("amenity") == "ferry_terminal"
            or (g("landuse") == "industrial" and TERMINAL_NAME.search(
                " ".join(tags.get(k, "") for k in ("name", "name:en"))))):
        return "terminal"
    if (g("industrial") in ("container_yard", "depot", "intermodal",
                            "logistics", "port_yard")
            or g("landuse") in ("depot", "logistics")
            or g("parking") == "containers"):
        return "yard"
    if g("man_made") in ("storage_tank", "silo", "tank") or g("industrial") == "oil":
        return "storage"
    if g("building"):
        return "building"
    return None


# ---------------------------------------------------------------------------
# geometry assembly
# ---------------------------------------------------------------------------

def stitch_rings(seqs):
    """Join way node-id sequences into closed rings; open leftovers are
    counted and dropped. -> (rings, n_open)"""
    segs = [list(s) for s in seqs if len(s) >= 2]
    rings, open_n = [], 0
    while segs:
        cur = segs.pop(0)
        progressed = True
        while cur[0] != cur[-1] and progressed:
            progressed = False
            for i, s in enumerate(segs):
                if s[0] == cur[-1]:
                    cur += s[1:]
                elif s[-1] == cur[-1]:
                    cur += s[::-1][1:]
                elif s[-1] == cur[0]:
                    cur = s[:-1] + cur
                elif s[0] == cur[0]:
                    cur = s[::-1][:-1] + cur
                else:
                    continue
                segs.pop(i)
                progressed = True
                break
        if cur[0] == cur[-1] and len(cur) >= 4:
            rings.append(cur)
        else:
            open_n += 1
    return rings, open_n


def _coords(osm: Osm, refs):
    out = []
    for r in refs:
        c = osm.nodes.get(r)
        if c is not None:
            out.append(c)
    return out


class Builder:
    """Collects classified, clipped, simplified features for one port."""

    def __init__(self, bbox, level=0):
        self.bbox = bbox
        self.proj = Proj(bbox)
        self.level = level
        self.stats = {"relations_incomplete": 0}

    # -- per-geometry processing ------------------------------------------
    def _simplify_line(self, layer, pts):
        tol = DP_TOL_M[layer] * 1.0
        mp = self.proj.path(pts)
        keep = douglas_peucker(mp, tol)
        idx = []
        # map back: recompute by matching on identity of projected tuples
        keep_set = {id(p) for p in keep}
        for p, ll in zip(mp, pts):
            if id(p) in keep_set:
                idx.append(ll)
        return idx

    @staticmethod
    def _q(pts):
        out = []
        for lon, lat in pts:
            q = (round(lon * QUANT) / QUANT, round(lat * QUANT) / QUANT)
            if not out or out[-1] != q:
                out.append(q)
        return out

    def line_feature(self, layer, pts):
        """-> (geometry, length_m) or None. pts in lon/lat."""
        pieces = clip_polyline(pts, self.bbox)
        if not pieces:
            return None
        lines, total = [], 0.0
        for pc in pieces:
            total += path_length(self.proj.path(pc))
            s = self._q(self._simplify_line(layer, pc))
            if len(s) >= 2:
                lines.append([[x, y] for x, y in s])
        if not lines:
            return None
        geom = ({"type": "LineString", "coordinates": lines[0]} if len(lines) == 1
                else {"type": "MultiLineString", "coordinates": lines})
        return geom, total

    def _poly_ring(self, layer, ring):
        """ring lon/lat open -> simplified, quantised, closed ring or None."""
        clipped = clip_ring(ring, self.bbox)
        if len(clipped) < 3:
            return None, 0.0
        area = abs(ring_area(self.proj.path(clipped)))
        closed = clipped + [clipped[0]]
        s = self._q(self._simplify_line(layer, closed))
        if len(s) < 4:
            return None, area
        if s[0] != s[-1]:
            s.append(s[0])
        if len(s) < 4:
            return None, area
        return [[x, y] for x, y in s], area

    def poly_feature(self, layer, outer, holes=()):
        """outer/holes: open lon/lat rings. -> (geometry, area_m2) or None"""
        o, area = self._poly_ring(layer, outer)
        if o is None:
            return None
        rings = [self._orient(o, True)]
        for h in holes:
            hr, ha = self._poly_ring(layer, h)
            if hr is not None and ha >= 200.0:
                rings.append(self._orient(hr, False))
                area -= ha
        return {"type": "Polygon", "coordinates": rings}, max(area, 0.0)

    @staticmethod
    def _orient(ring, ccw):
        a = ring_area(ring[:-1])
        if (a > 0) != ccw:
            return ring[::-1]
        return ring


def _in_box(lon, lat, b):
    return b[0] <= lon <= b[2] and b[1] <= lat <= b[3]


AREA_PROP = {"terminal", "yard", "water", "anchorage", "pier", "lock",
             "breakwater", "fairway"}
LEN_PROP = {"quay", "pier", "breakwater", "fairway", "berth", "lock"}


def apply_level(feats, level):
    """Size-budget ladder: drop the least important small objects, then
    strip the internal metric `_m`. Level 0 keeps everything the builder
    kept; each level doubles the building/tank area threshold and raises
    the road/rail/water thresholds by sqrt(2). Terminals, yards, quays,
    piers, breakwaters, locks, fairways, anchorages and cranes are never
    dropped."""
    scale = 2 ** (level / 2)
    bscale = 2 ** level
    out = []
    for f in feats:
        p = f["properties"]
        lay, m = p["layer"], p.get("_m", 0.0)
        if lay in ("building", "storage"):
            ok = m >= MIN_AREA_M2[lay] * bscale
        elif lay == "water":
            ok = m >= MIN_AREA_M2[lay] * scale
        elif lay == "road":
            ok = (m >= 80.0 * scale and level < 12 if p.get("kind") == "service"
                  else m >= 30.0 * scale)
        elif lay == "rail":
            ok = m >= 60.0 * scale
        else:
            ok = True
        if not ok:
            continue
        q = {k: v for k, v in p.items() if k != "_m"}
        if f["geometry"]["type"] == "Polygon" and lay in AREA_PROP:
            q["area_m2"] = int(round(m))
        elif f["geometry"]["type"] != "Polygon" and lay in LEN_PROP and f["geometry"]["type"] != "Point":
            q["length_m"] = int(round(m))
        out.append({"type": "Feature", "properties": q, "geometry": f["geometry"]})
    return out


def build_features(osm: Osm, bbox, stats_out=None) -> list[dict]:
    """Classify every OSM object in `osm` and return GeoJSON features
    (sorted, byte-stable) clipped to `bbox`, each carrying an internal
    `_m` (area or length in metres) that apply_level() uses and strips."""
    b = Builder(bbox, 0)
    feats = []

    def add(layer, osm_id, tags, geom, extra):
        props = {"layer": layer, "osm": osm_id}
        nm = best_name(tags)
        if nm:
            props["name"] = nm
        if layer in ("terminal", "quay", "berth", "pier"):
            k = terminal_kind(tags)
            if k != "unknown" or layer == "terminal":
                props["kind"] = k
        elif layer == "road":
            props["kind"] = tags.get("highway")
        elif layer == "rail":
            props["kind"] = tags.get("service") or "main"
        elif layer == "crane":
            props["kind"] = tags.get("crane:type") or tags.get("crane") or "crane"
        elif layer == "water":
            props["kind"] = tags.get("water") or tags.get("waterway") or "water"
        elif layer == "anchorage":
            props["kind"] = "anchorage"
        elif layer == "fairway":
            props["kind"] = tags.get("seamark:type") or tags.get("waterway")
        elif layer == "building":
            props["kind"] = tags.get("building") if tags.get("building") != "yes" else None
            if props["kind"] is None:
                del props["kind"]
        if tags.get("operator") and layer in ("terminal", "quay", "yard"):
            props["operator"] = tags["operator"].strip()[:60]
        if extra is not None:
            props["_m"] = round(extra, 1)
        feats.append({"type": "Feature", "properties": props, "geometry": geom})

    # nodes
    for nid in sorted(osm.ntags):
        tags = osm.ntags[nid]
        layer = classify(tags)
        if layer not in POINT_LAYERS:
            continue
        lon, lat = osm.nodes[nid]
        if not _in_box(lon, lat, bbox):
            continue
        add(layer, f"n{nid}", tags,
            {"type": "Point", "coordinates": [round(lon * QUANT) / QUANT,
                                              round(lat * QUANT) / QUANT]}, None)

    def centroid(pts):
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    # ways
    for wid in sorted(osm.ways):
        refs, tags = osm.ways[wid]
        if not tags:
            continue
        layer = classify(tags)
        if layer is None:
            continue
        pts = _coords(osm, refs)
        if len(pts) < 2:
            continue
        closed = len(refs) >= 4 and refs[0] == refs[-1]
        if layer in POINT_LAYERS:
            c = centroid(pts)
            if _in_box(c[0], c[1], bbox):
                add(layer, f"w{wid}", tags,
                    {"type": "Point", "coordinates": [round(c[0] * QUANT) / QUANT,
                                                      round(c[1] * QUANT) / QUANT]}, None)
            continue
        as_area = closed and (
            layer in AREA_ONLY
            or (layer not in LINE_ONLY and layer not in LINE_UNLESS_AREA)
            or (layer in LINE_UNLESS_AREA and tags.get("area") == "yes"))
        if layer in AREA_ONLY and not closed:
            continue
        if as_area:
            r = b.poly_feature(layer, pts[:-1])
            if r is None:
                continue
            geom, area = r
            if area < MIN_AREA_M2.get(layer, 0.0):
                continue
            add(layer, f"w{wid}", tags, geom, area)
        else:
            r = b.line_feature(layer, pts)
            if r is None:
                continue
            geom, length = r
            add(layer, f"w{wid}", tags, geom, length)

    # multipolygon relations
    for rid in sorted(osm.rels):
        members, tags = osm.rels[rid]
        layer = classify(tags)
        if layer is None or layer not in (AREA_ONLY | {"pier", "lock", "breakwater"}):
            continue
        outers = [osm.ways[m[1]][0] for m in members
                  if m[0] == "way" and m[2] in ("outer", "") and m[1] in osm.ways]
        inners = [osm.ways[m[1]][0] for m in members
                  if m[0] == "way" and m[2] == "inner" and m[1] in osm.ways]
        n_expected = sum(1 for m in members if m[0] == "way" and m[2] in ("outer", ""))
        o_rings, o_open = stitch_rings(outers)
        i_rings, _ = stitch_rings(inners)
        if o_open or len(outers) < n_expected:
            b.stats["relations_incomplete"] += 1
        o_ll = [_coords(osm, r)[:-1] for r in o_rings]
        i_ll = [_coords(osm, r)[:-1] for r in i_rings]
        o_ll = [r for r in o_ll if len(r) >= 3]
        i_ll = [r for r in i_ll if len(r) >= 3]
        o_proj = [b.proj.path(r) for r in o_ll]
        for k, ring in enumerate(o_ll):
            holes = [h for h in i_ll
                     if point_in_ring(b.proj.fwd(*h[0]), o_proj[k])]
            r = b.poly_feature(layer, ring, holes)
            if r is None:
                continue
            geom, area = r
            if area < MIN_AREA_M2.get(layer, 0.0):
                continue
            add(layer, f"r{rid}" if len(o_ll) == 1 else f"r{rid}.{k}", tags, geom, area)
    if stats_out is not None:
        stats_out.update(b.stats)

    feats.sort(key=lambda f: (LAYER_ORDER.index(f["properties"]["layer"]),
                              _osm_sort(f["properties"]["osm"])))
    return feats


def _osm_sort(osm_id: str):
    m = re.match(r"([nwr])(\d+)(?:\.(\d+))?$", osm_id)
    return ("nwr".index(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def dumps(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def layer_counts(feats) -> dict:
    out = {}
    for f in feats:
        out[f["properties"]["layer"]] = out.get(f["properties"]["layer"], 0) + 1
    return {k: out[k] for k in LAYER_ORDER if k in out}


def make_collection(place_id, name, bbox, feats, meta) -> dict:
    return {
        "type": "FeatureCollection",
        "bbox": bbox,
        "properties": {
            "schema": SCHEMA_GEO, "place_id": place_id, "name": name,
            "attribution": ATTRIBUTION, "license": LICENSE,
            "provenance": {
                "source": "OpenStreetMap API 0.6 /map (tiled)",
                "basis": "SOURCED",
                "osm_retrieved": meta["osm_retrieved"],
                "tile_count": meta["tile_count"],
                "dp_tolerance_m": DP_TOL_M,
                "quantum_deg": 1 / QUANT,
                "generalisation_level": meta["level"],
                "relations_incomplete": meta.get("relations_incomplete", 0),
            },
            "counts": layer_counts(feats),
        },
        "features": feats,
    }


def build_collection(place_id, name, bbox, osm: Osm, meta) -> tuple[dict, bytes]:
    """Run the size-budget ladder; return (collection, bytes)."""
    st = {}
    base = build_features(osm, bbox, st)
    level = 0
    while True:
        feats = apply_level(base, level)
        meta2 = dict(meta, level=level, **st)
        col = make_collection(place_id, name, bbox, feats, meta2)
        raw = dumps(col)
        if len(raw) <= SIZE_BUDGET or level >= 16:
            return col, raw
        level += 1


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------

# Parameters of the MODELLED estimates. Each carries a basis flag; the
# sources list names where each value comes from.
MEAN_LOA_M = {"container": 300.0, "bulk": 250.0, "tanker": 240.0,
              "ro-ro": 200.0, "general": 160.0, "unknown": 250.0}
PARAMS = {
    "mean_loa_m": MEAN_LOA_M["container"],
    "mean_loa_m_by_kind": MEAN_LOA_M,
    "m2_per_teu": 16.0,
    "m2_per_teu_terminal_gross": 25.0,
    "anchorage_slot_radius_m": 400.0,
    "min_yard_block_m2": 5000,
    "min_terminal_block_m2": 20000,
    "sources": [
        {"name": "Rodrigue & Notteboom, Port Economics, Management and "
                 "Policy: stacking density of container yard equipment "
                 "(straddle carrier 500-700 TEU/ha, RTG 500-1000 TEU/ha); "
                 "m2_per_teu = 10000 / 625 TEU/ha = 16 m2 gross yard area "
                 "per TEU slot (SOURCED range 10-20 m2, value chosen "
                 "inside it: TEMPORARY point value)",
         "url": "https://porteconomicsmanagement.org/stacking-density-container-yard-equipment",
         "retrieved": "2026-10-25"},
        {"name": "Container berth length = ship length overall + ~10% "
                 "clearance (30,000 TEU study: 453 m ship occupies 498 m "
                 "of quay, MDPI J. Mar. Sci. Eng. 7(5) 131, 2019); the "
                 "300 m mean_loa_m for container ships is a TEMPORARY "
                 "assumption between feeder (~200 m) and ULCV (~400 m) "
                 "tonnage, not a sourced fleet mean",
         "url": "https://www.mdpi.com/2077-1312/7/5/131/htm",
         "retrieved": "2026-10-25"},
        {"name": "Whole-terminal gross area per TEU slot "
                 "(m2_per_teu_terminal_gross = 25): TEMPORARY, assumes "
                 "stacking blocks are ~65% of a terminal polygon; no "
                 "source found",
         "url": "", "retrieved": "2026-10-25"},
        {"name": "Anchorage swing radius 400 m (= 300 m LOA + ~3 x 30 m "
                 "depth, common planning rule) and hex packing 0.9069: "
                 "TEMPORARY",
         "url": "", "retrieved": "2026-10-25"},
    ],
    "basis": "TEMPORARY",
}


def _ring_of(f):
    g = f["geometry"]
    return g["coordinates"][0] if g["type"] == "Polygon" else None


WATERFRONT_STEP_M = 20.0
WATERFRONT_NEAR_M = 25.0


def _shore_paths(feats, proj):
    """Shoreline polylines (projected metres): coastline lines and the
    rings of water polygons."""
    out = []
    for f in feats:
        lay = f["properties"]["layer"]
        g = f["geometry"]
        if lay == "coastline":
            lines = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
            out += [proj.path([tuple(c) for c in ln]) for ln in lines]
        elif lay == "water" and g["type"] == "Polygon":
            out += [proj.path([tuple(c) for c in r]) for r in g["coordinates"]]
    return out


def _waterfront(feats, proj, quay_less, groups, bbox_m):
    (bx0, by0), (bx1, by1) = bbox_m
    cand = []
    for a, f, ring in quay_less:
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        cand.append((a, f, ring, (min(xs) - WATERFRONT_NEAR_M, min(ys) - WATERFRONT_NEAR_M,
                                  max(xs) + WATERFRONT_NEAR_M, max(ys) + WATERFRONT_NEAR_M)))
    acc: dict[str, list] = {}
    for path in _shore_paths(feats, proj):
        for i in range(len(path) - 1):
            (x0, y0), (x1, y1) = path[i], path[i + 1]
            L = math.hypot(x1 - x0, y1 - y0)
            if L == 0:
                continue
            k = max(1, int(math.ceil(L / WATERFRONT_STEP_M)))
            for j in range(k):
                t = (j + 0.5) / k
                p = (x0 + t * (x1 - x0), y0 + t * (y1 - y0))
                if (p[0] < bx0 + 3 or p[0] > bx1 - 3 or p[1] < by0 + 3
                        or p[1] > by1 - 3):
                    continue          # an edge of the clip rectangle, not a shore
                for a, f, ring, bb in cand:
                    if not (bb[0] <= p[0] <= bb[2] and bb[1] <= p[1] <= bb[3]):
                        continue
                    if (point_in_ring(p, ring)
                            or dist_pt_ring(p, ring) <= WATERFRONT_NEAR_M):
                        e = acc.setdefault(f["properties"]["osm"], [0.0, 0, []])
                        e[0] += L / k
                        e[1] += 1
                        e[2].append(p)
                        break         # smallest terminal first (cand is area-sorted)
    for a, f, ring, bb in cand:
        e = acc.get(f["properties"]["osm"])
        if not e or e[0] < 40.0:
            continue
        gid = "bg_" + f["properties"]["osm"]
        groups[gid] = {"id": gid, "terminal": f, "length": e[0], "n": 0,
                       "name": f["properties"].get("name"),
                       "kind": f["properties"].get("kind", "unknown"),
                       "mids": e[2][:50], "source": "shoreline_proxy"}


def _group_quays(feats, proj, bbox_m=None):
    """Assign quay lines to terminals / spatial clusters.
    -> list of dict(group id/name/kind/length/n_quays)."""
    terms = []
    for f in feats:
        if f["properties"]["layer"] == "terminal" and f["geometry"]["type"] == "Polygon":
            ring = proj.path([tuple(c) for c in f["geometry"]["coordinates"][0]])
            terms.append((abs(ring_area(ring)), f, ring))
    terms.sort(key=lambda t: (t[0], t[1]["properties"]["osm"]))
    groups: dict[str, dict] = {}
    unassigned = []
    for f in feats:
        if f["properties"]["layer"] != "quay":
            continue
        g = f["geometry"]
        lines = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
        for ln in lines:
            pl = proj.path([tuple(c) for c in ln])
            if len(pl) < 2:
                continue
            length = path_length(pl)
            mid = point_at_half(pl)
            tf = None
            for _a, t, ring in terms:
                if point_in_ring(mid, ring):
                    tf = t
                    break
            if tf is None:
                best = None
                for _a, t, ring in terms:
                    d = dist_pt_ring(mid, ring)
                    if d <= 100.0 and (best is None or d < best[0]):
                        best = (d, t)
                tf = best[1] if best else None
            if tf is not None:
                gid = "bg_" + tf["properties"]["osm"]
                grp = groups.setdefault(gid, {
                    "id": gid, "terminal": tf, "length": 0.0, "n": 0,
                    "name": tf["properties"].get("name"),
                    "kind": tf["properties"].get("kind", "unknown"),
                    "mids": []})
                grp["length"] += length
                grp["n"] += 1
                grp["mids"].append(mid)
            else:
                unassigned.append((mid, length, f["properties"].get("kind", "unknown"),
                                   f["properties"].get("name"), pl))
    # Fallback for terminals with no OSM quay line at all: the shoreline
    # (dock/basin outlines and coastline) that lies inside the terminal
    # polygon or within 25 m of it is a PROXY for its quay length. Each
    # shoreline sample goes to the smallest terminal polygon it touches.
    quay_less = [(a, f, ring) for a, f, ring in terms
                 if "bg_" + f["properties"]["osm"] not in groups]
    if quay_less:
        _waterfront(feats, proj, quay_less, groups, bbox_m)
    # single-linkage clusters (300 m) over the unassigned quays
    parent = list(range(len(unassigned)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(unassigned)):
        for j in range(i + 1, len(unassigned)):
            if find(i) == find(j):
                continue
            a, b = unassigned[i], unassigned[j]
            if math.hypot(a[0][0] - b[0][0], a[0][1] - b[0][1]) <= 300.0 + 0.5 * (a[1] + b[1]):
                parent[find(i)] = find(j)
    clusters: dict[int, list] = {}
    for i in range(len(unassigned)):
        clusters.setdefault(find(i), []).append(unassigned[i])
    ordered = sorted(clusters.values(),
                     key=lambda c: (round(min(m[0][1] for m in c), 1),
                                    round(min(m[0][0] for m in c), 1),
                                    -sum(m[1] for m in c)))
    for n, c in enumerate(ordered, 1):
        names = sorted({m[3] for m in c if m[3]})
        kinds = {m[2] for m in c if m[2] != "unknown"}
        gid = f"bg_q{n}"
        groups[gid] = {"id": gid, "terminal": None,
                       "length": sum(m[1] for m in c), "n": len(c),
                       "name": names[0] if names else f"Quay group {n}",
                       "kind": next(iter(kinds)) if len(kinds) == 1 else "unknown",
                       "mids": [m[0] for m in c]}
    # OSM seamark berth features inside each terminal-based group (a cross-
    # check on berth_count_est, not used in it)
    bcount: dict[str, int] = {}
    for f in feats:
        if f["properties"]["layer"] != "berth":
            continue
        g = f["geometry"]
        c = (g["coordinates"] if g["type"] == "Point" else
             g["coordinates"][len(g["coordinates"]) // 2] if g["type"] == "LineString"
             else None)
        if c is None:
            continue
        mid = proj.fwd(c[0], c[1])
        for _a, t, ring in terms:
            if point_in_ring(mid, ring):
                bcount[t["properties"]["osm"]] = bcount.get(t["properties"]["osm"], 0) + 1
                break
    for g in groups.values():
        t = g.get("terminal")
        g["berth_features"] = bcount.get(t["properties"]["osm"], 0) if t else 0
    return [groups[k] for k in sorted(groups, key=lambda k: (k.startswith("bg_q"),
                                                              int(k[4:]) if k.startswith("bg_q") else 0,
                                                              k))]


def _polygon_area_m2(f, proj):
    g = f["geometry"]
    if g["type"] != "Polygon":
        return 0.0
    a = abs(ring_area(proj.path([tuple(c) for c in g["coordinates"][0]])))
    for h in g["coordinates"][1:]:
        a -= abs(ring_area(proj.path([tuple(c) for c in h])))
    return a


def build_profile(place_id, name, collection: dict, geometry_hash: str,
                  params: dict | None = None) -> dict:
    """port-profile/1 from a built collection (pure)."""
    P = dict(PARAMS)
    if params:
        P.update(params)
    feats = collection["features"]
    bbox = collection["bbox"]
    proj = Proj(bbox)
    prov = collection["properties"]["provenance"]
    basis_flag = P.get("basis", "TEMPORARY")

    # berth groups
    bgs = []
    bbox_m = (proj.fwd(bbox[0], bbox[1]), proj.fwd(bbox[2], bbox[3]))
    for g in _group_quays(feats, proj, bbox_m):
        loa = P["mean_loa_m_by_kind"].get(g["kind"], P["mean_loa_m"])
        length = g["length"]
        est = int(length // loa) if length >= 0.5 * loa else 0
        if length >= 0.5 * loa and est == 0:
            est = 1
        bgs.append({
            "id": g["id"],
            "name": g["name"] or f"Quay group {len(bgs) + 1}",
            "kind": g["kind"],
            "quay_length_m": int(round(length)),
            "berth_count_est": est,
            "basis": "quay_length / mean_loa_m",
            "mean_loa_m": loa,
            "quay_segments_osm": g["n"],
            "osm_berth_features": g.get("berth_features", 0),
            "flag": basis_flag,
            "length_source": ("PROXY: dock/basin/coast shoreline inside or "
                              "within 25 m of the terminal polygon (no OSM "
                              "quay line mapped for it)"
                              if g.get("source") == "shoreline_proxy"
                              else "OSM man_made=quay lines"),
            "origin": ("terminal polygon" if g["terminal"] is not None
                       else "spatial cluster of quay lines (300 m linkage)"),
        })

    # anchorage
    anch = [f for f in feats if f["properties"]["layer"] == "anchorage"
            and f["geometry"]["type"] == "Polygon"]
    anch_area = sum(_polygon_area_m2(f, proj) for f in anch)
    if anch:
        slot = math.pi * P["anchorage_slot_radius_m"] ** 2 / 0.9069
        anchorage = {
            "area_km2": round(anch_area / 1e6, 3),
            "capacity_vessels_est": int(anch_area // slot),
            "basis": ("OSM seamark:type=anchorage polygons inside the "
                      f"rectangle; area / (pi*r^2/0.9069), r={P['anchorage_slot_radius_m']:.0f} m "
                      "swing radius (hex packing)"),
            "polygons_osm": len(anch), "flag": basis_flag}
    else:
        anchorage = {"area_km2": None, "capacity_vessels_est": None,
                     "basis": "no seamark:type=anchorage polygon in the "
                              "mapped rectangle (anchorages are often "
                              "outside it or unmapped in OSM)",
                     "polygons_osm": 0, "flag": "NOT SERVED"}

    # channel
    fw = [f for f in feats if f["properties"]["layer"] == "fairway"]
    lk = [f for f in feats if f["properties"]["layer"] == "lock"]
    bw = [f for f in feats if f["properties"]["layer"] == "breakwater"]
    channel = {"present": bool(fw or lk),
               "notes": (f"OSM: {len(fw)} fairway/channel feature(s), "
                         f"{len(lk)} lock feature(s), {len(bw)} breakwater "
                         "feature(s) in the rectangle. Channel depth and "
                         "width are NOT in this dataset.")}

    # yard blocks
    terms = [f for f in feats if f["properties"]["layer"] == "terminal"
             and f["geometry"]["type"] == "Polygon"]
    yards = [f for f in feats if f["properties"]["layer"] == "yard"
             and f["geometry"]["type"] == "Polygon"]

    def centroid_m(f):
        ring = proj.path([tuple(c) for c in f["geometry"]["coordinates"][0]][:-1])
        return (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)), ring

    yard_info = []
    for f in yards:
        a = _polygon_area_m2(f, proj)
        if a >= P["min_yard_block_m2"]:
            c, ring = centroid_m(f)
            yard_info.append((a, f, c, ring))
    # drop yard blocks whose centroid sits inside a bigger yard block
    keep = []
    for a, f, c, ring in yard_info:
        if any(a2 > a and point_in_ring(c, r2) for a2, f2, c2, r2 in yard_info):
            continue
        keep.append((a, f, c, ring))
    blocks = []
    for a, f, c, ring in sorted(keep, key=lambda t: t[1]["properties"]["osm"]):
        blocks.append({
            "id": "yb_" + f["properties"]["osm"], "area_m2": int(round(a)),
            "capacity_teu_est": int(a // P["m2_per_teu"]),
            "basis": "area / m2_per_teu",
            "source": "OSM yard polygon", "flag": basis_flag,
            **({"name": f["properties"]["name"]} if f["properties"].get("name") else {})})
    cont_terms = []
    for f in terms:
        if f["properties"].get("kind") != "container":
            continue
        a = _polygon_area_m2(f, proj)
        if a < P["min_terminal_block_m2"]:
            continue
        c, ring = centroid_m(f)
        cont_terms.append((a, f, c, ring))
    for a, f, c, ring in sorted(cont_terms, key=lambda t: t[1]["properties"]["osm"]):
        # leaf terminals only, and only where no yard polygon already covers them
        if any(a2 < a and point_in_ring(c2, ring) for a2, f2, c2, r2 in cont_terms):
            continue
        if any(point_in_ring(yc, ring) for _a, _f, yc, _r in keep):
            continue
        blocks.append({
            "id": "yb_" + f["properties"]["osm"], "area_m2": int(round(a)),
            "capacity_teu_est": int(a // P["m2_per_teu_terminal_gross"]),
            "basis": "area / m2_per_teu",
            "source": "OSM container-terminal polygon (gross area; "
                      "m2_per_teu_terminal_gross)",
            "flag": basis_flag,
            **({"name": f["properties"]["name"]} if f["properties"].get("name") else {})})
    blocks.sort(key=lambda b: b["id"])

    cranes = [f for f in feats if f["properties"]["layer"] == "crane"]
    return {
        "schema": SCHEMA_PROFILE,
        "place_id": place_id,
        "name": name,
        "geometry_hash": geometry_hash,
        "berth_groups": bgs,
        "anchorage": anchorage,
        "channel": channel,
        "yard": {"blocks": blocks,
                 "total_area_m2": sum(b["area_m2"] for b in blocks)},
        "cranes": {"count_osm": len(cranes),
                   "note": "OSM man_made=crane objects (gantry, portal and "
                           "mobile cranes alike); OSM crane coverage is "
                           "incomplete, so this is a lower bound, not a "
                           "terminal inventory."},
        "params": {k: P[k] for k in ("mean_loa_m", "mean_loa_m_by_kind",
                                     "m2_per_teu", "m2_per_teu_terminal_gross",
                                     "anchorage_slot_radius_m", "sources", "basis")},
        "provenance": {
            "osm_retrieved": prov["osm_retrieved"],
            "tile_count": prov["tile_count"],
            "attribution": ATTRIBUTION,
            "basis": "MODELLED estimates from SOURCED OSM geometry",
            "generalisation_level": prov["generalisation_level"],
        },
    }


# ---------------------------------------------------------------------------
# batch build (offline, from the tile cache)
# ---------------------------------------------------------------------------

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def build_port(place_id, cache_dir: Path, out_dir: Path, params=None, log=print):
    from .geo_fetch import OsmCache, tile_key
    entry = geo_catalog.PORTS[place_id]
    cache = OsmCache(cache_dir)
    keys = []

    def expand(t):
        k = tile_key(t)
        m = cache.index.get(k)
        if m is None:
            raise SystemExit(f"{place_id}: tile {k} is not in the cache; run fetch first")
        if m["status"] == "split":
            from .geo_fetch import _quad
            for q in _quad(t):
                expand(q)
        elif m["status"] == "ok":
            keys.append(k)
        else:
            raise SystemExit(f"{place_id}: tile {k} failed")
    for t in geo_catalog.tiles_for(entry["bbox"]):
        expand(t)
    keys = sorted(set(keys))
    osm = parse_osm([cache.path(k) for k in keys])
    retrieved = max(cache.index[k]["retrieved"] for k in keys)[:10]
    meta = {"osm_retrieved": retrieved, "tile_count": len(keys)}
    col, raw = build_collection(place_id, entry["name"], entry["bbox"], osm, meta)
    geo_dir = Path(out_dir)
    geo_dir.mkdir(parents=True, exist_ok=True)
    (geo_dir / f"{place_id}.geojson").write_bytes(raw)
    h = sha256_bytes(raw)
    prof = build_profile(place_id, entry["name"], col, h, params)
    pdir = geo_dir / "profiles"
    pdir.mkdir(exist_ok=True)
    praw = dumps(prof)
    (pdir / f"{place_id}.json").write_bytes(praw)
    log(f"{place_id}: {len(raw)//1024} KB level {col['properties']['provenance']['generalisation_level']} "
        f"{col['properties']['counts']}")
    entry = {"file": f"{place_id}.geojson", "sha256": h, "bytes": len(raw),
            "bbox": entry["bbox"], "counts": col["properties"]["counts"],
            "osm_retrieved": retrieved, "tile_count": len(keys),
            "generalisation_level": col["properties"]["provenance"]["generalisation_level"],
            "profile": {"file": f"profiles/{place_id}.json",
                        "sha256": sha256_bytes(praw), "bytes": len(praw)},
            "osm_stats": osm.stats(), "note": entry["note"]}
    ed = geo_dir / "_entries"
    ed.mkdir(exist_ok=True)
    (ed / f"{place_id}.json").write_text(json.dumps(entry, sort_keys=True))
    return entry


def build_all(cache_dir: Path, out_dir: Path, ids, params=None):
    out_dir = Path(out_dir)
    man_path = out_dir / "manifest.json"
    old = json.loads(man_path.read_text()) if man_path.exists() else {}
    ports = dict(old.get("ports", {}))
    for pid in ids:
        ports[pid] = build_port(pid, cache_dir, out_dir, params)
    # entries written by (possibly parallel) build_port runs
    for ef in sorted((out_dir / "_entries").glob("*.json")) if (out_dir / "_entries").is_dir() else []:
        ports[ef.stem] = json.loads(ef.read_text())
    idx_path = out_dir / "imagery" / "index.json"
    idx = (json.loads(idx_path.read_text()).get("ports", {})
           if idx_path.exists() else {})
    for pid in ports:
        im = idx.get(pid)
        ports[pid]["imagery"] = (
            {"served": True, "file": f"imagery/{pid}.png",
             "scene_date": im["scene_date"], "scene_id": im["scene_id"],
             "cloud_pct_aoi": im["cloud_pct_aoi"], "bytes": im["bytes"],
             "basis": "SOURCED", "resolution_m": 10}
            if im and im.get("served") else
            {"served": False,
             "reason": (im or {}).get("reason", "not attempted")})
    man = {
        "schema": "port-geo-manifest/1",
        "attribution": ATTRIBUTION, "license": LICENSE,
        "license_file": "LICENSE-ODbL.txt",
        "source": "OpenStreetMap API 0.6 /map, tiled, cached",
        "ports": {k: ports[k] for k in sorted(ports)},
    }
    man_path.write_bytes(json.dumps(man, sort_keys=True, indent=1).encode() + b"\n")
    return man
