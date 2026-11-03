"""Deterministic polyline helpers for lane geometry: centripetal Catmull-Rom, great-circle
densification, Douglas-Peucker. Pure functions on lists of (lon, lat) with unwrapped longitudes."""
from __future__ import annotations

import math

from .land import EARTH_R_NM

COORD_DP = 3                      # stored precision: 0.001 degree, about 110 m


def rnd(p: tuple) -> tuple:
    return (round(p[0], COORD_DP) + 0.0, round(p[1], COORD_DP) + 0.0)


def gc_midpoints(a: tuple, b: tuple, step_deg: float) -> list:
    """Great-circle points strictly between a and b, spaced at most ``step_deg`` of arc apart.
    Longitudes follow a's frame (b is the unwrapped continuation of a)."""
    la1, la2 = math.radians(a[1]), math.radians(b[1])
    lo1, lo2 = math.radians(a[0]), math.radians(b[0])
    d = 2.0 * math.asin(min(1.0, math.sqrt(
        math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)))
    deg = math.degrees(d)
    n = int(math.ceil(deg / step_deg))
    if n <= 1 or d < 1e-12:
        return []
    x1, y1, z1 = math.cos(la1) * math.cos(lo1), math.cos(la1) * math.sin(lo1), math.sin(la1)
    x2, y2, z2 = math.cos(la2) * math.cos(lo2), math.cos(la2) * math.sin(lo2), math.sin(la2)
    sd = math.sin(d)
    out = []
    for i in range(1, n):
        t = i / n
        f1, f2 = math.sin((1 - t) * d) / sd, math.sin(t * d) / sd
        x, y, z = f1 * x1 + f2 * x2, f1 * y1 + f2 * y2, f1 * z1 + f2 * z2
        lon = math.degrees(math.atan2(y, x))
        lat = math.degrees(math.asin(max(-1.0, min(1.0, z))))
        # keep the continuation of a's frame
        lon += 360.0 * round((a[0] - lon) / 360.0)
        out.append((lon, lat))
    return out


def densify(pts: list, step_deg: float) -> list:
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        out.extend(gc_midpoints(a, b, step_deg))
        out.append(b)
    return out


def catmull_rom(pts: list, per_deg: float = 0.2) -> list:
    """Centripetal Catmull-Rom (alpha 0.5) through ``pts``, each span sampled about every ``per_deg``
    degrees. Each span is evaluated in a local frame (lon scaled by cos of the span's mean latitude),
    so the curve is a function of the points only. Endpoints are reflected for the end tangents."""
    n = len(pts)
    if n < 3:
        return list(pts)
    ext = [(2 * pts[0][0] - pts[1][0], 2 * pts[0][1] - pts[1][1])] + list(pts) + \
          [(2 * pts[-1][0] - pts[-2][0], 2 * pts[-1][1] - pts[-2][1])]
    out = [pts[0]]
    for i in range(1, n):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        c = math.cos(math.radians((p1[1] + p2[1]) / 2.0))
        c = max(c, 0.05)
        q = [((p[0] - p1[0]) * c, p[1]) for p in (p0, p1, p2, p3)]

        def knot(a, b):
            return max(math.hypot(b[0] - a[0], b[1] - a[1]) ** 0.5, 1e-6)

        t0 = 0.0
        t1 = t0 + knot(q[0], q[1])
        t2 = t1 + knot(q[1], q[2])
        t3 = t2 + knot(q[2], q[3])
        span = math.hypot(q[2][0] - q[1][0], q[2][1] - q[1][1])
        m = max(1, int(math.ceil(span / per_deg)))
        for s in range(1, m + 1):
            t = t1 + (t2 - t1) * s / m
            if s == m:
                out.append(pts[i])
                break

            def lerp(a, b, ta, tb):
                w = (t - ta) / (tb - ta)
                return (a[0] * (1 - w) + b[0] * w, a[1] * (1 - w) + b[1] * w)

            a1 = lerp(q[0], q[1], t0, t1)
            a2 = lerp(q[1], q[2], t1, t2)
            a3 = lerp(q[2], q[3], t2, t3)
            b1 = lerp(a1, a2, t0, t2)
            b2 = lerp(a2, a3, t1, t3)
            cc = lerp(b1, b2, t1, t2)
            out.append((p1[0] + cc[0] / c, cc[1]))
    return out


def dp_simplify(pts: list, tol_deg: float) -> list:
    """Douglas-Peucker in lon/lat degrees (iterative, deterministic)."""
    n = len(pts)
    if n < 3:
        return list(pts)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        ax, ay = pts[i]
        bx, by = pts[j]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        best, bi = -1.0, -1
        for k in range(i + 1, j):
            px, py = pts[k]
            if L2 == 0.0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
                d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d > best:
                best, bi = d, k
        if best > tol_deg:
            keep[bi] = True
            stack.append((i, bi))
            stack.append((bi, j))
    return [p for p, k in zip(pts, keep) if k]


def length_nm(pts: list) -> float:
    s = 0.0
    for a, b in zip(pts, pts[1:]):
        p1, p2 = math.radians(a[1]), math.radians(b[1])
        h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2
        s += 2.0 * EARTH_R_NM * math.asin(min(1.0, math.sqrt(h)))
    return s
