"""Lane geometry: sampled great-circle polylines.

Pure functions, no state. A lane's path is a slerp on the unit sphere
between its endpoint coordinates — never a 2-point chord. Sampling
keeps consecutive samples <= 2 degrees of arc apart with >= 8 samples
minimum per lane. The antimeridian rule splits a sampled
polyline wherever consecutive longitudes jump by > 180 degrees,
inserting the interpolated crossing latitude at (+/-180, lat) so no
subpath is ever drawn across the map's full width. Entity positions in
state.py interpolate along these same sampled paths — what moves is
what is drawn.
"""
from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0
MAX_STEP_DEG = 2.0
MIN_SAMPLES = 8


def _to_vec(lat: float, lon: float) -> tuple[float, float, float]:
    la, lo = math.radians(lat), math.radians(lon)
    return (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo),
            math.sin(la))


def _to_latlon(v: tuple[float, float, float]) -> tuple[float, float]:
    x, y, z = v
    lat = math.degrees(math.asin(max(-1.0, min(1.0, z))))
    lon = math.degrees(math.atan2(y, x))
    return lat, lon


def central_angle_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle central angle between (lat, lon) points, degrees."""
    va, vb = _to_vec(*a), _to_vec(*b)
    dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(va, vb))))
    return math.degrees(math.acos(dot))


def gc_distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in km between (lat, lon) points."""
    return math.radians(central_angle_deg(a, b)) * EARTH_RADIUS_KM


def slerp_samples(a: tuple[float, float], b: tuple[float, float],
                  max_step_deg: float = MAX_STEP_DEG,
                  min_samples: int = MIN_SAMPLES) -> list[tuple[float, float]]:
    """Sampled great-circle polyline from ``a`` to ``b`` ((lat, lon)).

    Returns >= ``min_samples`` (lat, lon) points including both
    endpoints, consecutive samples <= ``max_step_deg`` of arc apart.
    Longitudes are in (-180, 180]. A zero-length lane (same point)
    returns ``min_samples`` copies of the point.
    """
    angle = central_angle_deg(a, b)
    n = max(min_samples, int(math.ceil(angle / max_step_deg)) + 1)
    if angle < 1e-12:
        return [a] * n
    va, vb = _to_vec(*a), _to_vec(*b)
    sin_total = math.sin(math.radians(angle))
    pts: list[tuple[float, float]] = []
    for i in range(n):
        t = i / (n - 1)
        f1 = math.sin((1 - t) * math.radians(angle)) / sin_total
        f2 = math.sin(t * math.radians(angle)) / sin_total
        v = tuple(f1 * x + f2 * y for x, y in zip(va, vb))
        pts.append(_to_latlon(v))  # type: ignore[arg-type]
    pts[0], pts[-1] = a, b
    return pts


def split_antimeridian(
        pts: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Split a (lat, lon) polyline at the antimeridian.

    Wherever consecutive longitudes differ by > 180 degrees, the
    crossing latitude is interpolated (in unwrapped-longitude space)
    and the path is cut: one subpath ends at (+/-180, lat), the next
    resumes at (-/+180, lat). A polyline with no crossing returns a
    single subpath equal to the input.
    """
    if len(pts) < 2:
        return [list(pts)]
    subpaths: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = [pts[0]]
    for (lat1, lon1), (lat2, lon2) in zip(pts, pts[1:]):
        if abs(lon2 - lon1) > 180.0:
            # Unwrap lon2 next to lon1, find where the segment crosses
            # the +-180 meridian, interpolate the latitude there.
            lon2u = lon2 + 360.0 if lon2 < lon1 else lon2 - 360.0
            boundary = 180.0 if lon1 > 0 else -180.0
            frac = (boundary - lon1) / (lon2u - lon1)
            cross_lat = lat1 + frac * (lat2 - lat1)
            current.append((cross_lat, boundary))
            subpaths.append(current)
            current = [(cross_lat, -boundary), (lat2, lon2)]
        else:
            current.append((lat2, lon2))
    subpaths.append(current)
    return subpaths


def path_length_km(pts: list[tuple[float, float]]) -> float:
    """Length of a sampled polyline (sum of segment great circles)."""
    return sum(gc_distance_km(p, q) for p, q in zip(pts, pts[1:]))


def point_at_fraction(pts: list[tuple[float, float]],
                      frac: float) -> tuple[float, float]:
    """Point at arc-length fraction ``frac`` (0..1) along a polyline.

    Linear interpolation within the containing segment — the samples
    are <= 2 degrees apart, so the chord error is negligible and the
    function stays a pure function of the sampled path (determinism).
    Longitude interpolation is unwrapped so a segment crossing the
    antimeridian interpolates the short way.
    """
    if not pts:
        raise ValueError("empty path")
    if frac <= 0.0 or len(pts) == 1:
        return pts[0]
    if frac >= 1.0:
        return pts[-1]
    total = path_length_km(pts)
    if total <= 0.0:
        return pts[0]
    target = frac * total
    acc = 0.0
    for p, q in zip(pts, pts[1:]):
        seg = gc_distance_km(p, q)
        if seg <= 0.0:
            continue
        if acc + seg >= target:
            t = (target - acc) / seg
            lat = p[0] + t * (q[0] - p[0])
            dlon = q[1] - p[1]
            if dlon > 180.0:
                dlon -= 360.0
            elif dlon < -180.0:
                dlon += 360.0
            lon = p[1] + t * dlon
            if lon > 180.0:
                lon -= 360.0
            elif lon < -180.0:
                lon += 360.0
            return (lat, lon)
        acc += seg
    return pts[-1]


def detour_around_circle(pts: list[tuple[float, float]],
                         center: tuple[float, float],
                         radius_km: float) -> list[tuple[float, float]] | None:
    """Reroute a sampled polyline around a circle: if the polyline's closest approach to
    ``center`` is >= ``radius_km`` the leg does not intersect the
    circle and None is returned. Otherwise a detour polyline is
    built: entry/exit points on a slightly enlarged circle
    (radius x 1.05) at the bearings of the polyline's endpoints as
    seen from the centre, joined by an arc sampled every <= 15
    degrees; of the two arc directions the one giving the shorter
    total detour wins (ties go counter-clockwise) — deterministic.
    Computed in a local equirectangular kilometre frame centred on
    ``center``; endpoints are preserved exactly."""
    if len(pts) < 2:
        return None
    clat, clon = center
    kx = 111.32 * math.cos(math.radians(clat))
    ky = 110.57

    def to_xy(p: tuple[float, float]) -> tuple[float, float]:
        dlon = p[1] - clon
        while dlon > 180.0:
            dlon -= 360.0
        while dlon < -180.0:
            dlon += 360.0
        return (dlon * kx, (p[0] - clat) * ky)

    def to_ll(x: float, y: float) -> tuple[float, float]:
        lon = clon + x / kx if kx > 1e-9 else clon
        while lon > 180.0:
            lon -= 360.0
        while lon < -180.0:
            lon += 360.0
        return (clat + y / ky, lon)

    xy = [to_xy(p) for p in pts]

    def seg_dist(a, b) -> float:
        dx, dy = b[0] - a[0], b[1] - a[1]
        len2 = dx * dx + dy * dy
        t = 0.0 if len2 <= 0.0 else max(
            0.0, min(1.0, -(a[0] * dx + a[1] * dy) / len2))
        return math.hypot(a[0] + t * dx, a[1] + t * dy)

    if min(seg_dist(a, b) for a, b in zip(xy, xy[1:])) >= radius_km:
        return None
    a_xy, b_xy = xy[0], xy[-1]
    ra = math.hypot(*a_xy)
    rb = math.hypot(*b_xy)
    if ra <= radius_km or rb <= radius_km:
        # An endpoint inside the circle is a closed place —
        # handled by the closure machinery, not by rerouting.
        return None
    r_det = radius_km * 1.05
    tha = math.atan2(a_xy[1], a_xy[0])
    thb = math.atan2(b_xy[1], b_xy[0])

    def arc_points(direction: int) -> list[tuple[float, float]]:
        # Sweep from tha to thb in ``direction`` (+1 CCW, -1 CW).
        sweep = (thb - tha) % (2 * math.pi) if direction > 0 \
            else -((tha - thb) % (2 * math.pi))
        steps = max(2, int(abs(sweep) / math.radians(15.0)) + 1)
        return [(r_det * math.cos(tha + sweep * i / steps),
                 r_det * math.sin(tha + sweep * i / steps))
                for i in range(steps + 1)]

    def total_len(seq: list[tuple[float, float]]) -> float:
        return sum(math.hypot(q[0] - p[0], q[1] - p[1])
                   for p, q in zip(seq, seq[1:]))

    cands = []
    for direction in (1, -1):
        seq = [a_xy] + arc_points(direction) + [b_xy]
        cands.append((total_len(seq), -direction, seq))
    # Shorter detour wins; ties (equal length) go counter-clockwise
    # (direction +1 sorts first via the -direction key).
    cands.sort(key=lambda c: (round(c[0], 9), c[1]))
    best = cands[0][2]
    out = [pts[0]] + [to_ll(x, y) for x, y in best[1:-1]] + [pts[-1]]
    return out


def bearing_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Initial great-circle bearing from ``a`` to ``b``, degrees 0..360."""
    la1, la2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    y = math.sin(dlon) * math.cos(la2)
    x = (math.cos(la1) * math.sin(la2)
         - math.sin(la1) * math.cos(la2) * math.cos(dlon))
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
