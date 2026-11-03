"""Sentinel-1 bright-target detector: cell-averaging CFAR over a sea mask.

Algorithm id ``sentinel1-cfar/1``. Deterministic, pure Python (stdlib
only; the caller decodes the raster), no model, no training data.

Input is the GRD VV *amplitude* (digital numbers), as served. Steps:

1. Intensity ``I = amplitude ** 2``. Zero is nodata and is never used.
2. Sea mask. The frame is cut into square blocks; a block is sea when its
   median intensity is low. The split is Otsu on log10 block medians; when
   the frame is not bimodal by at least ``sea_separation_log10`` the
   whole frame is one class, called sea only if its median amplitude is
   below ``abs_sea_amp``. Land blocks are then dilated by
   ``land_buffer_blocks`` (land components smaller than
   ``min_land_blocks`` are targets, not land) so coastal clutter, quays and berthed hulls
   against a quay are excluded. This is a statistical threshold mask, not
   a coastline: Natural Earth in this repo is 1:110m and cannot place a
   quay.
3. CA-CFAR. For each candidate pixel (3x3 mean intensity well above the
   frame's sea level) the local clutter level is the mean of a ring of
   sea pixels around it (a guard box is left out so the target does not
   contaminate its own background; ring intensities are clipped at
   ``clip_factor`` times the sea level so a neighbouring ship does not
   raise the threshold). A pixel is a hit when its 3x3 mean exceeds
   ``alpha * background``, alpha being the larger of the gamma-tail
   threshold for the stated false-alarm probability and the minimum
   contrast.
4. Hits within ``merge_px`` of each other form one target. Position and
   size come from the target's core (pixels within ``core_db`` of its
   peak), which keeps the cross-shaped sidelobe streaks of a strong
   reflector out of the size.
5. A target needs ``min_pixels`` core pixels and a length under
   ``max_length_m``; anything else is counted as rejected, with its
   reason, never silently dropped.

What this is not: it does not classify, identify or measure vessels. A
bright target on open water is a candidate: ships, platforms, buoys,
wind-farm foundations, ice and strong sea-surface features all qualify.
Counts are a floor. Thin ships oriented with the satellite track can be
missed, sea states above a fresh breeze raise the false-alarm rate, and
small vessels are below the sensor's resolution. ``score`` is an
uncalibrated ranking value, not a probability.
"""
from __future__ import annotations

import math
from array import array
from dataclasses import asdict, dataclass
from itertools import accumulate
from statistics import median
from typing import Optional, Sequence

ALGORITHM_ID = "sentinel1-cfar/1"
ALGORITHM_LABEL = "CELL-AVERAGING CFAR · VV AMPLITUDE"

STATUS_OK = "OK"
STATUS_NO_VALID = "NO VALID PIXELS"
STATUS_NO_SEA = "NO SEA"
STATUS_CLUTTER = "SEA CLUTTER LIMIT"


@dataclass(frozen=True)
class CfarParams:
    """Tunables. Pixel sizes are stated for 10 m spacing and are scaled to
    the frame's actual pixel spacing by ``detect_ships``."""
    block_px: int = 16
    land_buffer_blocks: int = 2
    min_land_blocks: int = 24
    sea_separation_log10: float = 0.6
    abs_sea_amp: float = 150.0
    guard_half_px: int = 18
    ring_half_px: int = 34
    min_ring_valid_frac: float = 0.35
    clip_factor: float = 6.0
    pre_factor: float = 3.0
    enl_eff: float = 20.0
    pfa: float = 1e-6
    min_contrast_db: float = 12.0
    merge_px: int = 3
    min_pixels: int = 3
    core_db: float = 6.0
    max_length_m: float = 500.0
    max_hit_pixels: int = 150_000


# ---------------------------------------------------------------------------
# Gamma tail (no scipy): threshold factor for a stated false-alarm rate
# ---------------------------------------------------------------------------

def _gammq(a: float, x: float) -> float:
    """Regularised upper incomplete gamma Q(a, x) (series / continued
    fraction, Numerical Recipes 6.2)."""
    if x <= 0:
        return 1.0
    gln = math.lgamma(a)
    if x < a + 1.0:
        ap, total = a, 1.0 / a
        delta = total
        for _ in range(500):
            ap += 1.0
            delta *= x / ap
            total += delta
            if abs(delta) < abs(total) * 1e-14:
                break
        return max(0.0, 1.0 - total * math.exp(-x + a * math.log(x) - gln))
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return math.exp(-x + a * math.log(x) - gln) * h


def cfar_alpha(enl: float, pfa: float) -> float:
    """Multiplier alpha with P(mean > alpha * mu) = pfa when the test
    statistic is Gamma(shape=enl) distributed with mean mu."""
    lo, hi = enl, enl * 60.0 + 100.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if _gammq(enl, mid) > pfa:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi) / enl


# ---------------------------------------------------------------------------
# Sea mask
# ---------------------------------------------------------------------------

def _otsu(values: list[float], bins: int = 64) -> tuple[float, float]:
    """Otsu split of ``values``: (threshold, separation of class means)."""
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return hi, 0.0
    width = (hi - lo) / bins
    hist = [0] * bins
    for v in values:
        hist[min(bins - 1, int((v - lo) / width))] += 1
    total = len(values)
    sum_all = sum((lo + (i + 0.5) * width) * c for i, c in enumerate(hist))
    best, best_t, w0, s0 = -1.0, hi, 0, 0.0
    best_sep = 0.0
    for i in range(bins - 1):
        w0 += hist[i]
        s0 += (lo + (i + 0.5) * width) * hist[i]
        w1 = total - w0
        if w0 == 0 or w1 == 0:
            continue
        m0, m1 = s0 / w0, (sum_all - s0) / w1
        between = w0 * w1 * (m0 - m1) ** 2
        if between > best:
            best, best_t, best_sep = between, lo + (i + 1) * width, abs(m1 - m0)
    return best_t, best_sep


def sea_blocks(width: int, height: int, intensity: array, block: int,
               params: CfarParams) -> dict:
    """Classify blocks; returns the grid plus the sea-level estimate."""
    nbx, nby = -(-width // block), -(-height // block)
    med: list[list[Optional[float]]] = [[None] * nbx for _ in range(nby)]
    low: list[list[Optional[float]]] = [[None] * nbx for _ in range(nby)]
    for by in range(nby):
        y0, y1 = by * block, min(height, (by + 1) * block)
        for bx in range(nbx):
            x0, x1 = bx * block, min(width, (bx + 1) * block)
            vals: list[float] = []
            for y in range(y0, y1):
                base = y * width
                vals.extend(v for v in intensity[base + x0:base + x1] if v > 0.0)
            area = (y1 - y0) * (x1 - x0)
            if vals and len(vals) >= 0.5 * area:
                vals.sort()
                med[by][bx] = vals[len(vals) // 2]
                low[by][bx] = vals[len(vals) // 4]
    # Classify on the lower quartile, not the median: a ship (or its
    # sidelobe cross) fills a minority of a block, so the lower quartile
    # stays at sea level while dense land lifts it.
    logs = [math.log10(m) for row in low for m in row if m is not None]
    if not logs:
        return {"status": STATUS_NO_VALID}
    thr, sep = _otsu(logs)
    sea = [[False] * nbx for _ in range(nby)]
    if sep >= params.sea_separation_log10:
        mode = "bimodal"
        for by in range(nby):
            for bx in range(nbx):
                m = low[by][bx]
                sea[by][bx] = m is not None and math.log10(m) <= thr
    else:
        mode = "single-class"
        mean_log = sum(logs) / len(logs)
        is_sea = 10.0 ** mean_log <= params.abs_sea_amp ** 2
        for by in range(nby):
            for bx in range(nbx):
                sea[by][bx] = is_sea and low[by][bx] is not None
    # Small bright components (a ship with its sidelobe cross, a platform)
    # are targets, not land: only a land component of at least
    # ``min_land_blocks`` blocks is kept as land. Invalid blocks stay out.
    seen_b: set[tuple[int, int]] = set()
    for by in range(nby):
        for bx in range(nbx):
            if sea[by][bx] or med[by][bx] is None or (by, bx) in seen_b:
                continue
            comp, stack = [], [(by, bx)]
            seen_b.add((by, bx))
            while stack:
                cy, cx = stack.pop()
                comp.append((cy, cx))
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if (0 <= ny < nby and 0 <= nx < nbx and (ny, nx) not in seen_b
                            and not sea[ny][nx] and med[ny][nx] is not None):
                        seen_b.add((ny, nx))
                        stack.append((ny, nx))
            if len(comp) < params.min_land_blocks:
                for cy, cx in comp:
                    sea[cy][cx] = True
    # Land (or invalid) blocks are dilated: coastal clutter is not sea.
    buf = params.land_buffer_blocks
    ok = [[False] * nbx for _ in range(nby)]
    for by in range(nby):
        for bx in range(nbx):
            if not sea[by][bx]:
                continue
            clear = True
            for yy in range(max(0, by - buf), min(nby, by + buf + 1)):
                row = sea[yy]
                for xx in range(max(0, bx - buf), min(nbx, bx + buf + 1)):
                    if not row[xx]:
                        clear = False
                        break
                if not clear:
                    break
            ok[by][bx] = clear
    sea_meds = sorted(med[by][bx] for by in range(nby) for bx in range(nbx)
                      if ok[by][bx] and med[by][bx] is not None)
    return {"status": STATUS_OK, "mode": mode, "ok": ok, "nbx": nbx, "nby": nby,
            "separation_log10": round(sep, 3), "n_blocks": nbx * nby,
            "n_sea_blocks": len(sea_meds),
            "level": sea_meds[len(sea_meds) // 2] if sea_meds else None}


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _integral(width: int, height: int, rows) -> array:
    """Summed-area table with a zero first row and column."""
    w1 = width + 1
    sat = array("d", bytes(8 * w1 * (height + 1)))
    for y in range(height):
        acc = accumulate(rows(y))
        prev = sat[y * w1 + 1:(y + 1) * w1]
        sat[(y + 1) * w1 + 1:(y + 2) * w1] = array("d", [a + b for a, b in zip(acc, prev)])
    return sat


def _box(sat: array, w1: int, x0: int, y0: int, x1: int, y1: int) -> float:
    return sat[y1 * w1 + x1] - sat[y0 * w1 + x1] - sat[y1 * w1 + x0] + sat[y0 * w1 + x0]


def _empty(status: str, width: int, height: int, spacing: float,
           params: CfarParams, extra: Optional[dict] = None) -> dict:
    out = {"algorithm": ALGORITHM_ID, "status": status, "width": width,
           "height": height, "pixel_spacing_m": spacing, "detections": [],
           "rejected": {}, "params": asdict(params), "stats": {}}
    if extra:
        out["stats"].update(extra)
    return out


def detect_ships(width: int, height: int, amplitude: Sequence[float], *,
                 pixel_spacing_m: float = 10.0,
                 params: Optional[CfarParams] = None) -> dict:
    """Run the detector on a row-major amplitude raster.

    Returns ``{algorithm, status, detections, rejected, params, stats}``.
    Each detection: ``{x, y}`` (pixel centroid, 0-based, top-left origin),
    ``score`` (0..1 ranking value), ``contrast_db`` and ``size_px``
    (``long``, ``short``, ``area``, plus ``long_m`` at the frame spacing).
    ``status`` is OK or a named refusal (no valid pixels, no sea, clutter
    limit); a refusal carries zero detections and says so.
    """
    P = params or CfarParams()
    n = width * height
    if width < 8 or height < 8 or len(amplitude) != n:
        raise ValueError("amplitude must be a row-major width*height raster")
    k = 10.0 / max(pixel_spacing_m, 1e-6)          # px-scale vs a 10 m frame

    def sc(px: float, floor: int = 1) -> int:
        return max(floor, int(round(px * k)))

    block = sc(P.block_px, 4)
    guard, ring = sc(P.guard_half_px, 3), sc(P.ring_half_px, 6)
    if ring <= guard:
        ring = guard + 4
    merge = sc(P.merge_px, 1)
    min_pix = max(2, int(round(P.min_pixels * k * k)))

    intensity = array("d", [float(v) * float(v) for v in amplitude])
    valid_px = sum(1 for v in intensity if v > 0.0)
    if valid_px < 0.05 * n:
        return _empty(STATUS_NO_VALID, width, height, pixel_spacing_m, P,
                      {"valid_pct": round(100.0 * valid_px / n, 2)})

    blocks = sea_blocks(width, height, intensity, block, P)
    if blocks["status"] != STATUS_OK or not blocks["n_sea_blocks"]:
        return _empty(STATUS_NO_SEA, width, height, pixel_spacing_m, P,
                      {"valid_pct": round(100.0 * valid_px / n, 2)})
    level = blocks["level"]            # sea-level intensity (median of sea blocks)

    # Pixel sea mask (1 = sea and valid).
    mask = bytearray(n)
    nbx, nby, ok = blocks["nbx"], blocks["nby"], blocks["ok"]
    for by in range(nby):
        for bx in range(nbx):
            if not ok[by][bx]:
                continue
            x0, x1 = bx * block, min(width, (bx + 1) * block)
            for y in range(by * block, min(height, (by + 1) * block)):
                mask[y * width + x0:y * width + x1] = b"\x01" * (x1 - x0)
    for i, v in enumerate(intensity):
        if v <= 0.0:
            mask[i] = 0
    sea_px = sum(mask)

    cap = P.clip_factor * level
    w1 = width + 1

    def clipped_rows(y: int):
        base = y * width
        return [min(intensity[base + x], cap) if mask[base + x] else 0.0
                for x in range(width)]

    def count_rows(y: int):
        base = y * width
        return [float(mask[base + x]) for x in range(width)]

    sat_c = _integral(width, height, clipped_rows)
    sat_n = _integral(width, height, count_rows)

    alpha_gamma = cfar_alpha(P.enl_eff, P.pfa)
    alpha_min = 10.0 ** (P.min_contrast_db / 10.0)
    alpha = max(alpha_gamma, alpha_min)
    pre = P.pre_factor * level
    ring_area = (2 * ring + 1) ** 2 - (2 * guard + 1) ** 2
    min_cnt = P.min_ring_valid_frac * ring_area

    hits: dict[int, tuple[float, float]] = {}
    candidates = 0
    skipped_coast = 0
    for i in (j for j, v in enumerate(intensity) if v > pre and mask[j]):
        candidates += 1
        y, x = divmod(i, width)
        xa, xb = max(0, x - 1), min(width, x + 2)
        ya, yb = max(0, y - 1), min(height, y + 2)
        tot = 0.0
        for yy in range(ya, yb):
            tot += sum(intensity[yy * width + xa:yy * width + xb])
        i3 = tot / ((xb - xa) * (yb - ya))
        ox0, oy0 = max(0, x - ring), max(0, y - ring)
        ox1, oy1 = min(width, x + ring + 1), min(height, y + ring + 1)
        gx0, gy0 = max(0, x - guard), max(0, y - guard)
        gx1, gy1 = min(width, x + guard + 1), min(height, y + guard + 1)
        cnt = _box(sat_n, w1, ox0, oy0, ox1, oy1) - _box(sat_n, w1, gx0, gy0, gx1, gy1)
        if cnt < min_cnt:
            skipped_coast += 1
            continue
        bg = (_box(sat_c, w1, ox0, oy0, ox1, oy1) - _box(sat_c, w1, gx0, gy0, gx1, gy1)) / cnt
        if bg > 0.0 and i3 > alpha * bg:
            hits[i] = (i3, bg)
            if len(hits) > P.max_hit_pixels:
                return _empty(STATUS_CLUTTER, width, height, pixel_spacing_m, P,
                              {"hit_pixels": len(hits)})

    # Group hits within `merge` px (Chebyshev) into targets.
    seen: set[int] = set()
    rejected: dict[str, int] = {}
    if skipped_coast:
        rejected["coast_clutter_skipped"] = skipped_coast
    detections: list[dict] = []
    core_lin = 10.0 ** (-P.core_db / 10.0)
    for seed in sorted(hits):
        if seed in seen:
            continue
        stack, members = [seed], []
        seen.add(seed)
        while stack:
            cur = stack.pop()
            members.append(cur)
            cy, cx = divmod(cur, width)
            for yy in range(max(0, cy - merge), min(height, cy + merge + 1)):
                for xx in range(max(0, cx - merge), min(width, cx + merge + 1)):
                    j = yy * width + xx
                    if j in hits and j not in seen:
                        seen.add(j)
                        stack.append(j)
        peak_i = max(hits[m][0] for m in members)
        peak_bg = next(hits[m][1] for m in members if hits[m][0] == peak_i)
        core = [m for m in members if hits[m][0] >= peak_i * core_lin]
        if len(core) < min_pix:
            rejected["too_small"] = rejected.get("too_small", 0) + 1
            continue
        xs = [m % width for m in core]
        ys = [m // width for m in core]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sxx = sum((x - mx) ** 2 for x in xs) / len(xs)
        syy = sum((y - my) ** 2 for y in ys) / len(ys)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / len(xs)
        theta = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
        ux, uy = math.cos(theta), math.sin(theta)
        pl = [(x - mx) * ux + (y - my) * uy for x, y in zip(xs, ys)]
        ps = [-(x - mx) * uy + (y - my) * ux for x, y in zip(xs, ys)]
        long_px = max(pl) - min(pl) + 1.0
        short_px = max(ps) - min(ps) + 1.0
        if short_px > long_px:
            long_px, short_px = short_px, long_px
        long_m = long_px * pixel_spacing_m
        if long_m > P.max_length_m:
            rejected["too_large"] = rejected.get("too_large", 0) + 1
            continue
        contrast = 10.0 * math.log10(peak_i / peak_bg)
        score = (contrast - P.min_contrast_db) / 28.0
        detections.append({
            "x": round(mx, 2), "y": round(my, 2),
            "score": round(min(1.0, max(0.0, score)), 3),
            "contrast_db": round(contrast, 1),
            "size_px": {"long": round(long_px, 1), "short": round(short_px, 1),
                        "area": len(core), "long_m": round(long_m, 0)},
        })
    detections.sort(key=lambda d: (-d["score"], d["y"], d["x"]))
    return {
        "algorithm": ALGORITHM_ID, "status": STATUS_OK, "width": width,
        "height": height, "pixel_spacing_m": pixel_spacing_m,
        "detections": detections, "rejected": rejected, "params": asdict(P),
        "stats": {
            "valid_pct": round(100.0 * valid_px / n, 2),
            "sea_pct": round(100.0 * sea_px / n, 2),
            "sea_mask_mode": blocks["mode"],
            "sea_level_amp": round(math.sqrt(level), 1),
            "alpha": round(alpha, 2), "alpha_gamma": round(alpha_gamma, 2),
            "candidate_pixels": candidates, "hit_pixels": len(hits),
        },
    }
