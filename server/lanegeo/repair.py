"""Build-time water router: re-route a lane segment that crosses the 1:50m land layer.

A land raster is filled from the same polygons the exact test uses (every cell a coastline edge passes
through counts as land, so islets and spits are never lost; the whitelisted cuts are burned in as water).
A segment that crosses land is replaced by the shortest 8-connected path over cells that keep a
clearance of k cells from land (k = 3, 2, 1, 0 tried in that order), pulled straight with the exact edge
test, and verified. The world raster is 0.05 degree (about 3 nm); when a narrow inlet is closed at that
size the router retries on a 0.0125 degree raster of the neighbourhood only. Pure Python, deterministic
(heap ties broken by cell index). Used by ``build.py`` only; the stored geometry carries the result,
nothing re-routes at run time.
"""
from __future__ import annotations

import heapq
import math
from itertools import accumulate

from .land import ALL_CUTS, CELL, Land

G = 0.05
G_FINE = 0.0125
CLEARANCES = (3, 2, 1, 0)
FINE_CLEARANCES = (2, 1, 0)
FINE_MAX_DEG = 4.0               # largest neighbourhood the fine retry will rasterise
MAX_EXPANSIONS = 250000
HEURISTIC_WEIGHT = 1.15          # weighted A*: a few percent longer paths, far fewer expansions


class Raster:
    """Land raster. ``bbox`` None: the whole world, wrapping in longitude. Otherwise one window
    (lon0, lat0, lon1, lat1) inside [-180, 180]."""

    def __init__(self, land: Land, g: float = G, bbox: tuple | None = None):
        self.g = g
        full_w, full_h = int(round(360.0 / g)), int(round(180.0 / g))
        if bbox is None:
            self.ox, self.oy, self.W, self.H, self.wrap = 0, 0, full_w, full_h, True
            edge_ids = range(len(land.x1))
        else:
            lon0, lat0, lon1, lat1 = bbox
            self.ox = int(math.floor((lon0 + 180.0) / g))
            self.oy = max(0, int(math.floor((lat0 + 90.0) / g)))
            self.W = int(math.floor((lon1 + 180.0) / g)) - self.ox + 1
            self.H = min(full_h - 1, int(math.floor((lat1 + 90.0) / g))) - self.oy + 1
            self.wrap = False
            ids: set = set()
            for cy in range(Land._iy(lat0 - 1e-9), Land._iy(lat1 + 1e-9) + 1):
                for cx in range(0, 360):
                    lst = land.cells.get((cx, cy))
                    if lst:
                        ids.update(lst)
            edge_ids = sorted(ids)
        W, H, ox, oy = self.W, self.H, self.ox, self.oy
        x1, y1, x2, y2 = land.x1, land.y1, land.x2, land.y2
        rows: list = [[] for _ in range(H)]
        for i in edge_ids:
            ya, yb = y1[i], y2[i]
            if ya == yb:
                continue
            lo, hi = (ya, yb) if ya < yb else (yb, ya)
            j0 = max(oy, int(math.ceil((lo + 90.0) / g - 0.5)) - 1)
            j1 = min(oy + H - 1, int(math.ceil((hi + 90.0) / g - 0.5)) + 1)
            xa, xb = x1[i], x2[i]
            for j in range(j0, j1 + 1):
                y = -90.0 + (j + 0.5) * g
                if (ya > y) == (yb > y):
                    continue
                rows[j - oy].append(xa + (y - ya) * (xb - xa) / (yb - ya))
        m = bytearray(W * H)
        for jj in range(H):
            xs = rows[jj]
            if len(xs) < 2:
                continue
            xs.sort()
            base = jj * W
            for k in range(0, len(xs) - 1, 2):
                a = int(math.ceil((xs[k] + 180.0) / g - 0.5)) - ox
                b = int(math.ceil((xs[k + 1] + 180.0) / g - 0.5)) - ox
                a, b = max(a, 0), min(b, W)
                if b > a:
                    m[base + a: base + b] = b"\x01" * (b - a)
        self.m = m
        # conservative: every cell a coastline edge passes through is land too (islets, spits)
        for i in edge_ids:
            ax, ay, bx, by = x1[i], y1[i], x2[i], y2[i]
            n = max(1, int(math.ceil(max(abs(bx - ax), abs(by - ay)) / (g / 2.0))))
            for s in range(n + 1):
                self._set(ax + (bx - ax) * s / n, ay + (by - ay) * s / n, 1)
        # whitelisted cuts become water in the raster so that the router can use them
        for cut in ALL_CUTS:
            for (ax, ay), (bx, by) in zip(cut["line"], cut["line"][1:]):
                n = max(1, int(math.ceil(max(abs(bx - ax), abs(by - ay)) / (g / 3.0))))
                for s in range(n + 1):
                    self._set(ax + (bx - ax) * s / n, ay + (by - ay) * s / n, 0)

    def _set(self, lon: float, lat: float, v: int) -> None:
        i = int(math.floor((lon + 180.0) / self.g)) - self.ox
        j = int(math.floor((lat + 90.0) / self.g)) - self.oy
        if self.wrap:
            i %= self.W
        if 0 <= i < self.W and 0 <= j < self.H:
            self.m[j * self.W + i] = v

    def row(self, jg: int, ig: int, n: int) -> bytes:
        """Land bytes of global row jg for n cells from (unwrapped, global) column ig; outside = land."""
        j = jg - self.oy
        if j < 0 or j >= self.H:
            return b"\x01" * n
        base = j * self.W
        if self.wrap:
            a = ig % self.W
            if a + n <= self.W:
                return bytes(self.m[base + a: base + a + n])
            out = bytes(self.m[base + a: base + self.W])
            n -= self.W - a
            while n > 0:
                take = min(n, self.W)
                out += bytes(self.m[base: base + take])
                n -= take
            return out
        a = ig - self.ox
        pre = max(0, min(n, -a))
        a = max(a, 0)
        take = max(0, min(n - pre, self.W - a))
        return b"\x01" * pre + bytes(self.m[base + a: base + a + take]) + b"\x01" * (n - pre - take)


class Window:
    """A rectangular window of cells in the unwrapped longitude frame, with land counts."""

    def __init__(self, ras: Raster, lon0: float, lat0: float, lon1: float, lat1: float):
        self.ras = ras
        g = self.g = ras.g
        self.i0 = int(math.floor((lon0 + 180.0) / g))
        self.j0 = max(0, int(math.floor((lat0 + 90.0) / g)))
        self.nw = int(math.floor((lon1 + 180.0) / g)) - self.i0 + 1
        self.nh = min(int(round(180.0 / g)) - 1, int(math.floor((lat1 + 90.0) / g))) - self.j0 + 1
        nw, nh = self.nw, self.nh
        sat = [[0] * (nw + 1)]            # summed-area table of land
        for y in range(nh):
            acc = accumulate(ras.row(self.j0 + y, self.i0, nw))
            prev = sat[-1]
            sat.append([0] + [p + a for p, a in zip(prev[1:], acc)])
        self.sat = sat
        self._pass: dict = {}

    def cell_of(self, lon: float, lat: float) -> tuple:
        return (int(math.floor((lon + 180.0) / self.g)) - self.i0, int(math.floor((lat + 90.0) / self.g)) - self.j0)

    def center(self, x: int, y: int) -> tuple:
        return (-180.0 + (self.i0 + x + 0.5) * self.g, -90.0 + (self.j0 + y + 0.5) * self.g)

    def inside(self, x: int, y: int) -> bool:
        return 0 <= x < self.nw and 0 <= y < self.nh

    def passable(self, x: int, y: int, k: int) -> bool:
        if not self.inside(x, y):
            return False
        xa, xb = max(0, x - k), min(self.nw - 1, x + k)
        ya, yb = max(0, y - k), min(self.nh - 1, y + k)
        s = self.sat
        return s[yb + 1][xb + 1] - s[ya][xb + 1] - s[yb + 1][xa] + s[ya][xa] == 0


def _mask(win: Window, k: int) -> bytearray:
    """passable(x, y, k) for every cell of the window, row-major (cached on the window)."""
    got = win._pass.get(k)
    if got is None:
        nw, nh = win.nw, win.nh
        got = bytearray(nw * nh)
        for y in range(nh):
            base = y * nw
            for x in range(nw):
                if win.passable(x, y, k):
                    got[base + x] = 1
        win._pass[k] = got
    return got


def _astar(win: Window, start: tuple, goal: tuple, k: int):
    nw, nh = win.nw, win.nh
    ok = _mask(win, k)
    sx, sy = start
    gx, gy = goal
    cosl = [max(math.cos(math.radians(win.center(0, y)[1])), 0.05) for y in range(nh)]
    S2 = math.sqrt(2) - 1.0
    Gn = win.g * 60.0
    s0, g0 = sy * nw + sx, gy * nw + gx
    best = {s0: 0.0}
    prev: dict = {}
    dx0 = abs(sx - gx) * cosl[sy]
    dy0 = abs(sy - gy)
    heap = [(HEURISTIC_WEIGHT * (max(dx0, dy0) + S2 * min(dx0, dy0)) * Gn, 0.0, s0)]
    done = set()
    steps = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
    pop, push = heapq.heappop, heapq.heappush
    n = 0
    while heap:
        _f, g, cur = pop(heap)
        if cur in done:
            continue
        done.add(cur)
        n += 1
        if n > MAX_EXPANSIONS:
            return None
        if cur == g0:
            path = [cur]
            while cur in prev:
                cur = prev[cur]
                path.append(cur)
            return [(c % nw, c // nw) for c in path[::-1]]
        y, x = divmod(cur, nw)
        cl = cosl[y]
        for dx, dy in steps:
            nx, ny = x + dx, y + dy
            if nx < 0 or ny < 0 or nx >= nw or ny >= nh:
                continue
            nxt = ny * nw + nx
            if not ok[nxt]:
                continue
            ng = g + math.hypot(dx * cl, dy) * Gn
            if ng < best.get(nxt, 1e18) - 1e-9:
                best[nxt] = ng
                prev[nxt] = cur
                hx = abs(nx - gx) * cosl[ny]
                hy = abs(ny - gy)
                push(heap, (ng + HEURISTIC_WEIGHT * (max(hx, hy) + S2 * min(hx, hy)) * Gn, ng, nxt))
    return None


def nearest_passable(win: Window, lon: float, lat: float, k: int, max_cells: int):
    cx, cy = win.cell_of(lon, lat)
    if win.inside(cx, cy) and win.passable(cx, cy, k):
        return (cx, cy)
    for r in range(1, max_cells + 1):
        best = None
        for dx in range(-r, r + 1):
            for dy in (-r, r) if abs(dx) != r else range(-r, r + 1):
                x, y = cx + dx, cy + dy
                if win.inside(x, y) and win.passable(x, y, k):
                    c = win.center(x, y)
                    d = (c[0] - lon) ** 2 + (c[1] - lat) ** 2
                    if best is None or (d, x, y) < best[0]:
                        best = ((d, x, y), (x, y))
        if best is not None:
            return best[1]
    return None


def endpoint_cell(land: Land, win: Window, p: tuple, k: int, max_cells: int = 6):
    """The passable cell nearest to p that p can reach over water (exact test), or None."""
    cx, cy = win.cell_of(p[0], p[1])
    cands = []
    for dx in range(-max_cells, max_cells + 1):
        for dy in range(-max_cells, max_cells + 1):
            x, y = cx + dx, cy + dy
            if win.inside(x, y) and win.passable(x, y, k):
                c = win.center(x, y)
                cands.append(((c[0] - p[0]) ** 2 + (c[1] - p[1]) ** 2, x, y))
    cands.sort()
    for _d, x, y in cands[:60]:
        if land.segment_ok(p, win.center(x, y)):
            return (x, y)
    return None


def _visible(land: Land, win: Window, p: tuple, q: tuple, k: int) -> bool:
    if not land.segment_ok(p, q):
        return False
    if k <= 0:
        return True
    d = max(abs(q[0] - p[0]), abs(q[1] - p[1]))
    n = max(1, int(math.ceil(d / (win.g / 2))))
    for s in range(n + 1):
        t = s / n
        c = win.cell_of(p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]))
        if not win.passable(c[0], c[1], max(k - 1, 0)):
            return False
    return True


def _route_in(land: Land, win: Window, a: tuple, b: tuple, clearances) -> list | None:
    for k in clearances:
        s = endpoint_cell(land, win, a, k)
        e = endpoint_cell(land, win, b, k)
        if s is None or e is None:
            continue
        cells = _astar(win, s, e, k)
        if cells is None:
            continue
        pts = [a] + [win.center(x, y) for x, y in cells] + [b]
        out = [pts[0]]
        i = 0
        n = len(pts)
        while i < n - 1:
            j = n - 1
            while j > i + 1 and not _visible(land, win, pts[i], pts[j], k):
                j -= 1
            out.append(pts[j])
            i = j
        if all(land.segment_ok(p, q) for p, q in zip(out, out[1:])):
            return out
    return None


def route_segment(land: Land, ras: Raster, a: tuple, b: tuple, pad_list=(1.0, 3.0, 8.0), fine: bool = True):
    """Water path from a to b (both in water): list of (lon, lat) vertices from a to b, or None."""
    for pad in pad_list:
        lon0, lon1 = min(a[0], b[0]) - pad, max(a[0], b[0]) + pad
        lat0, lat1 = max(-89.9, min(a[1], b[1]) - pad), min(89.9, max(a[1], b[1]) + pad)
        if (lon1 - lon0) > 340:
            lon0, lon1 = min(a[0], b[0]) - 20.0, max(a[0], b[0]) + 20.0
        win = Window(ras, lon0, lat0, lon1, lat1)
        got = _route_in(land, win, a, b, CLEARANCES)
        if got is not None:
            return got
    if not fine:
        return None
    # a narrow inlet the coarse raster closes: the neighbourhood again at a quarter of the cell size
    k360 = math.floor((a[0] + 180.0) / 360.0)
    sh = 360.0 * k360
    a2, b2 = (a[0] - sh, a[1]), (b[0] - sh, b[1])
    pad = 0.6
    lon0, lon1 = min(a2[0], b2[0]) - pad, max(a2[0], b2[0]) + pad
    lat0, lat1 = max(-89.9, min(a2[1], b2[1]) - pad), min(89.9, max(a2[1], b2[1]) + pad)
    if lon0 < -179.9 or lon1 > 179.9 or (lon1 - lon0) > FINE_MAX_DEG or (lat1 - lat0) > FINE_MAX_DEG:
        return None
    ras2 = Raster(land, G_FINE, (lon0, lat0, lon1, lat1))
    win = Window(ras2, lon0, lat0, lon1, lat1)
    got = _route_in(land, win, a2, b2, FINE_CLEARANCES)
    if got is None:
        return None
    return [(x + sh, y) for x, y in got]
