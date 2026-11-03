"""Build data/graph/lane_geometry.json from data/graph/sea_routes.json and the 1:50m land layer.

Deterministic, no RNG, pure Python. For every unordered pair of places joined by a sea leg that has real
lane geometry (the hash-pinned sidecar ``sea_routes.json`` holds its waypoints), the builder produces one
polyline from place A to place B:

  * the lane core is the sidecar's waypoint list (network node to network node, Douglas-Peucker
    simplified at 0.35 degrees by the sidecar's builder), with any waypoint that sits on land in the
    1:50m layer moved to the nearest water cell first;
  * candidate 1 is a centripetal Catmull-Rom curve through the core; candidate 2 is the great circle
    between consecutive waypoints; any waypoint-to-waypoint segment that still meets land is re-routed
    over water by ``repair.route_segment``; the first candidate with no land crossing wins and its name
    is stored (``smoothing``);
  * the two port approaches (place to first and last core vertex) are straight connectors that are
    allowed to cross the generalised coastline (river and harbour ports sit inside the 1:50m polygons),
    and their length and the land length on them are stored;
  * a core that still meets land (other than the Suez and Panama canal cuts) is stored with
    status ``flagged`` and the crossing list; flagged lanes are not drawn as lanes and carry no sim vessel.

The stored vertices are rounded to 0.001 degree BEFORE the final land test, so the test is on exactly
what is stored.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

from . import smooth
from .land import CANAL_CUTS, LAND_SOURCE, Land, gc_nm, in_canal_cut, wrap_lon
from .repair import Raster, Window, nearest_passable, route_segment

REPO = Path(__file__).resolve().parents[2]
SEA_ROUTES = REPO / "data" / "graph" / "sea_routes.json"
OUT = REPO / "data" / "graph" / "lane_geometry.json"
OUT_SHA = REPO / "data" / "graph" / "lane_geometry.sha256"
SCHEMA = "supermap.lane_geometry/1"
ALGO_VERSION = 1
STEP_NEAR_DEG = 0.25             # vertex spacing within a degree of a coastline
STEP_FAR_DEG = 1.0               # vertex spacing in open water
SIMPLIFY_TOL_DEG = 0.02          # Douglas-Peucker after smoothing (about 1.2 nm)
NUDGE_MAX_DEG = 1.0
APPROACH_MAX_NM = 150.0          # the sidecar's own snap limit: a longer connector is not a port approach
LENGTH_RATIO_MAX = 1.5           # a polyline longer than this x the sidecar's lane nm (+ slack) is a different route
LENGTH_SLACK_NM = 15.0
MAX_RETREAT = 3                  # end waypoints a lane may give up to the port connector
PARAMS = {
    "algorithm_version": ALGO_VERSION,
    "smoothing": "centripetal Catmull-Rom (alpha 0.5), then great circle, then water re-route",
    "vertex_step_deg": {"within_1_degree_of_coast": STEP_NEAR_DEG, "open_water": STEP_FAR_DEG},
    "simplify_tolerance_deg": SIMPLIFY_TOL_DEG,
    "coordinate_decimals": smooth.COORD_DP,
    "water_raster_deg": 0.05,
    "clearance_cells_tried": [3, 2, 1, 0],
    "nudge_max_deg": NUDGE_MAX_DEG,
    "approach_max_nm": APPROACH_MAX_NM,
    "length_rule": "flagged when the polyline exceeds %g x lane nm + %g nm" % (LENGTH_RATIO_MAX, LENGTH_SLACK_NM),
    "canal_cuts": [{"id": c["id"], "bbox": list(c["bbox"])} for c in CANAL_CUTS],
}


def content_hash(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k != "content_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def pair_key(a: str, b: str) -> str:
    return f"{a}|{b}" if a < b else f"{b}|{a}"


def unwrap_near(lon: float, ref: float) -> float:
    return lon + 360.0 * round((ref - lon) / 360.0)


class Builder:
    def __init__(self, land: Land, ras: Raster):
        self.land = land
        self.ras = ras
        self.seg_cache: dict = {}
        self.stats = {"astar_calls": 0, "astar_fail": 0}

    # ---- water state --------------------------------------------------------
    def in_water(self, p: tuple) -> bool:
        return in_canal_cut(*p) is not None or not self.land.is_land(*p)

    def to_water(self, p: tuple):
        """(point, moved_nm) with the point in water (canal boxes count as water), or (None, 0)."""
        if self.in_water(p):
            return p, 0.0
        win = Window(self.ras, p[0] - NUDGE_MAX_DEG, max(-89.9, p[1] - NUDGE_MAX_DEG),
                     p[0] + NUDGE_MAX_DEG, min(89.9, p[1] + NUDGE_MAX_DEG))
        for k in (1, 0):
            c = nearest_passable(win, p[0], p[1], k, int(NUDGE_MAX_DEG / 0.05))
            if c is not None:
                q = win.center(*c)
                if self.land.is_land(*q) and in_canal_cut(*q) is None:
                    continue
                return q, gc_nm(p[0], p[1], q[0], q[1])
        return None, 0.0

    # ---- one waypoint segment ------------------------------------------------
    def chain(self, a: tuple, b: tuple) -> list:
        """The great circle a-b densified: 0.25 degree steps near a coast, 1 degree in open water."""
        out = [a] + smooth.gc_midpoints(a, b, STEP_NEAR_DEG) + [b]
        # thin out the open-water stretch: keep a vertex when it is near a coast or 1 degree from the last
        thin = [out[0]]
        last = out[0]
        for i in range(1, len(out) - 1):
            p = out[i]
            if self.land.near_coast(*p) or max(abs(p[0] - last[0]), abs(p[1] - last[1])) >= STEP_FAR_DEG:
                thin.append(p)
                last = p
        thin.append(out[-1])
        return [smooth.rnd(p) for p in thin]

    def clean(self, pts: list) -> bool:
        runs = self.land.land_runs(pts)
        cuts, bad = self.land.classify_runs(runs)
        return not bad

    def segment(self, a: tuple, b: tuple):
        """(vertices a..b, how) for one waypoint segment; vertices rounded; how in {gc, route, none}.

        The great circle is densified and tested; each land run on it is replaced by a water route
        between the last water vertex before it and the first water vertex after it, so the repair is
        as local as the crossing is."""
        key = (smooth.rnd(a), smooth.rnd(b))
        got = self.seg_cache.get(key)
        if got is not None:
            return got
        ch = self.chain(a, b)
        runs = self.land.land_runs(ch)
        _cuts, bad = self.land.classify_runs(runs)
        if not bad:
            res = (ch, "gc")
            self.seg_cache[key] = res
            return res
        cum = [0.0]
        for p, q in zip(ch, ch[1:]):
            cum.append(cum[-1] + gc_nm(p[0], p[1], q[0], q[1]))
        spans = []
        for r in bad:
            ie = max([i for i, c in enumerate(cum) if c <= r["from_nm"] - 1e-9] or [0])
            ix = min([i for i, c in enumerate(cum) if c >= r["to_nm"] + 1e-9] or [len(ch) - 1])
            if spans and ie <= spans[-1][1]:
                spans[-1] = (spans[-1][0], max(ix, spans[-1][1]))
            else:
                spans.append((ie, ix))
        out = [ch[0]]
        pos = 0
        ok = True
        for ie, ix in spans:
            out.extend(ch[pos + 1: ie + 1])
            self.stats["astar_calls"] += 1
            path = route_segment(self.land, self.ras, ch[ie], ch[ix])
            if path is None:
                self.stats["astar_fail"] += 1
                ok = False
                out.extend(ch[ie + 1: ix + 1])
            else:
                out.extend(smooth.rnd(p) for p in path[1:])
            pos = ix
        out.extend(ch[pos + 1:])
        out = [out[0]] + [p for i, p in enumerate(out[1:], 1) if p != out[i - 1]]
        if ok and self.clean(out):
            res = (out, "route")
        else:
            res = (ch, "none")
        self.seg_cache[key] = res
        return res

    # ---- one pair ---------------------------------------------------------------
    def build_pair(self, wps: list, pu: tuple, pv: tuple, lane_nm: float) -> dict:
        """wps: unwrapped (lon, lat) waypoints A..B; pu, pv: the two places' (lon, lat) in the same frame."""
        pts = []
        for p in wps:
            if not pts or (abs(p[0] - pts[-1][0]) > 1e-9 or abs(p[1] - pts[-1][1]) > 1e-9):
                pts.append((p[0], p[1]))
        nudged = []
        core_in = []
        flags = []
        for i, p in enumerate(pts):
            q, mv = self.to_water(p)
            if q is None:
                flags.append({"at": [round(p[0], 3), round(p[1], 3)], "reason": "no water within 1 degree"})
                q = p
            elif mv > 0:
                nudged.append(round(mv, 1))
            core_in.append(q)
        # candidate 1: Catmull-Rom through the core
        smoothing = None
        core = None
        retreated = 0
        if len(core_in) >= 3 and not flags:
            cr = smooth.catmull_rom(core_in, 0.2)
            cr = smooth.dp_simplify(cr, SIMPLIFY_TOL_DEG)
            cr = [smooth.rnd(p) for p in cr]
            if self.clean(cr):
                core, smoothing = cr, "catmull_rom"
        repaired = 0
        if core is None:
            lo, hi = 0, len(core_in) - 1
            while True:
                sub = core_in[lo:hi + 1]
                core = [smooth.rnd(sub[0])]
                repaired = 0
                fails = []
                for idx, (a, b) in enumerate(zip(sub, sub[1:])):
                    vs, how = self.segment(a, b)
                    if how == "route":
                        repaired += 1
                    elif how == "none":
                        fails.append(idx)
                    core.extend(vs[1:])
                if not fails or len(sub) <= 2 or retreated >= MAX_RETREAT:
                    break
                # a port node that sits in water the sea cannot reach (an estuary the 1:50m layer closes): the lane
                # ends at the last vertex that is reachable and the connector to the place gets longer
                if fails[0] == 0:
                    lo += 1
                elif fails[-1] == len(sub) - 2:
                    hi -= 1
                else:
                    break
                retreated += 1
            smoothing = "great_circle" if not repaired else "great_circle+water_route"
        if len(core) == 1:
            core = [core[0], core[0]]
        runs = self.land.land_runs(core)
        cuts, bad = self.land.classify_runs(runs)
        for r in bad:
            flags.append({"at": [round(r["enter"][0], 3), round(r["enter"][1], 3)], "nm": round(r["nm"], 1),
                          "reason": "land crossing"})
        full = [smooth.rnd(pu)] + core + [smooth.rnd(pv)]
        # collapse a duplicate vertex produced by rounding
        dedup = [full[0]]
        for p in full[1:]:
            if p != dedup[-1]:
                dedup.append(p)
        if len(dedup) < 2:
            dedup = [full[0], full[-1]]
        full = dedup
        a_nm = gc_nm(full[0][0], full[0][1], full[1][0], full[1][1])
        b_nm = gc_nm(full[-2][0], full[-2][1], full[-1][0], full[-1][1])
        a_land = sum(r["nm"] for r in self.land.land_runs(full[:2]))
        b_land = sum(r["nm"] for r in self.land.land_runs(full[-2:]))
        geometry_nm = smooth.length_nm(full)
        if max(a_nm, b_nm) > APPROACH_MAX_NM:
            flags.append({"reason": "port approach longer than %g nm" % APPROACH_MAX_NM, "nm": round(max(a_nm, b_nm), 1)})
        if geometry_nm > LENGTH_RATIO_MAX * lane_nm + LENGTH_SLACK_NM:
            flags.append({"reason": "polyline %.0f nm against %.0f nm of lane in the sidecar" % (geometry_nm, lane_nm),
                          "nm": round(geometry_nm, 1)})
        status = "lane" if not flags else "flagged"
        rec = {"v": [[p[0], p[1]] for p in full], "status": status, "smoothing": smoothing,
               "geometry_nm": round(smooth.length_nm(full), 1),
               "approach_nm": [round(a_nm, 1), round(b_nm, 1)],
               "approach_land_nm": [round(a_land, 1), round(b_land, 1)]}
        if repaired:
            rec["routed_segments"] = repaired
        if retreated:
            rec["end_waypoints_dropped"] = retreated
        if nudged:
            rec["nudged_nm"] = nudged
        if cuts:
            rec["canals"] = sorted({c["canal"] for c in cuts})
        if flags:
            rec["flags"] = flags
        return rec


def build(progress=None, limit: int | None = None) -> dict:
    sea = json.loads(SEA_ROUTES.read_text(encoding="utf-8"))
    raw_bytes = SEA_ROUTES.read_bytes()
    land = Land()
    land_sha = hashlib.sha256((Path(__file__).resolve().parent / "data" / "ne_land_50m.geojson").read_bytes()).hexdigest()
    ras = Raster(land)
    bld = Builder(land, ras)
    nodes = sea["nodes"]
    # which legs count as having real geometry: the loader's rule (lane <= land_separation_ratio x chord)
    try:
        from engine2.sea_routes import load_sea_routes
    except Exception:  # pragma: no cover
        from server.engine2.sea_routes import load_sea_routes  # type: ignore
    sr = load_sea_routes()
    pairs: dict = {}
    legs = sea["legs"]
    keys = sorted(legs)
    t0 = time.time()
    done = 0
    for key in keys:
        u, rest = key.split("->")
        v = rest.rsplit(":", 1)[0]
        if sr.leg(key) is None:
            continue
        pk = pair_key(u, v)
        if pk in pairs:
            continue
        a, b = pk.split("|")
        k_ab = f"{a}->{b}:sea"
        k_ba = f"{b}->{a}:sea"
        if k_ab in legs and sr.leg(k_ab) is not None:
            wps = [tuple(w) for w in legs[k_ab]["waypoints"]]
        else:
            wps = [tuple(w) for w in reversed(legs[k_ba]["waypoints"])]
        na, nb = nodes[a], nodes[b]
        pu = (unwrap_near(na["lon"], wps[0][0]), na["lat"])
        pv = (unwrap_near(nb["lon"], wps[-1][0]), nb["lat"])
        lane_nm = legs[k_ab]['nm'] if k_ab in legs else legs[k_ba]['nm']
        rec = bld.build_pair(wps, pu, pv, lane_nm)
        rec["a"], rec["b"] = a, b
        rec["lane_nm"] = legs[k_ab]["nm"] if k_ab in legs else legs[k_ba]["nm"]
        rec["passages"] = list(legs[k_ab]["passages"] if k_ab in legs else legs[k_ba]["passages"])
        pairs[pk] = rec
        done += 1
        if progress and done % 100 == 0:
            progress(done, time.time() - t0, bld.stats)
        if limit and done >= limit:
            break
    st = {"lane": 0, "flagged": 0}
    sm: dict = {}
    for r in pairs.values():
        st[r["status"]] += 1
        sm[r["smoothing"]] = sm.get(r["smoothing"], 0) + 1
    legs_covered = sum(1 for k in legs if sr.leg(k) is not None)
    legs_clean = 0
    for k in legs:
        if sr.leg(k) is None:
            continue
        u, rest = k.split("->")
        v = rest.rsplit(":", 1)[0]
        r = pairs.get(pair_key(u, v))
        if r is not None and r["status"] == "lane":
            legs_clean += 1
    doc = {
        "schema": SCHEMA,
        "label": "MODELLED",
        "status": ("SIDECAR to data/graph/sea_routes.json (additive, derived): smoothed lane polylines checked against the "
                   "Natural Earth 1:50m land layer. Lane geometry is MODELLED; it is not routing advice."),
        "sea_routes": {"content_sha256": sea["content_sha256"], "file_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                       "schema": sea["schema"]},
        "graph_pin": sea["graph_pin"],
        "land": {**LAND_SOURCE, "file_sha256": land_sha, "scale": "1:50,000,000"},
        "params": PARAMS,
        "stats": {"pairs": len(pairs), "pairs_by_status": st, "pairs_by_smoothing": dict(sorted(sm.items())),
                  "legs_with_geometry_in_sea_routes": legs_covered, "legs_with_clean_lane": legs_clean,
                  "astar": bld.stats},
        "pairs": dict(sorted(pairs.items())),
    }
    doc["content_sha256"] = content_hash(doc)
    return doc


def write(doc: dict) -> str:
    text = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    OUT.write_text(text, encoding="utf-8")
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    OUT_SHA.write_text(f"{sha}  data/graph/lane_geometry.json\n")
    return sha
