// Lane geometry, client side.
// Pure functions, no state. The server derivation already computed the
// rendered path (slerp samples along the great circle, or along the
// rendered_via graph path when the land-crossing rule fired); this
// module only converts it for deck.gl:
//   server path: [lat, lon] samples  ->  deck polylines: [lon, lat],
//   split at the antimeridian (same rule as
//   server/sim/geometry.py split_antimeridian) so no segment is ever
//   drawn across the map's full width.

export type LonLat = [number, number];
export type LatLon = [number, number];

/**
 * Split a [lon, lat] polyline at the antimeridian. Wherever consecutive
 * longitudes differ by > 180°, the crossing latitude is interpolated
 * (in unwrapped-longitude space) and the path is cut: one subpath ends
 * at (±180, lat), the next resumes at (∓180, lat). A polyline with no
 * crossing returns a single subpath equal to the input.
 */
export function splitAntimeridianLonLat(
  pts: LonLat[],
): LonLat[][] {
  if (pts.length < 2) return [pts.slice()];
  const subpaths: LonLat[][] = [];
  let current: LonLat[] = [pts[0]];
  for (let i = 1; i < pts.length; i++) {
    const [lon1, lat1] = pts[i - 1];
    const [lon2, lat2] = pts[i];
    if (Math.abs(lon2 - lon1) > 180) {
      const lon2u = lon2 < lon1 ? lon2 + 360 : lon2 - 360;
      const boundary = lon1 > 0 ? 180 : -180;
      const frac = (boundary - lon1) / (lon2u - lon1);
      const crossLat = lat1 + frac * (lat2 - lat1);
      current.push([boundary, crossLat]);
      subpaths.push(current);
      current = [
        [-boundary, crossLat],
        [lon2, lat2],
      ];
    } else {
      current.push([lon2, lat2]);
    }
  }
  subpaths.push(current);
  return subpaths;
}

/** Server [lat, lon] lane path -> antimeridian-split [lon, lat] subpaths. */
export function laneSubpaths(pathLatLon: LatLon[]): LonLat[][] {
  const lonLat: LonLat[] = pathLatLon.map(([lat, lon]) => [lon, lat]);
  return splitAntimeridianLonLat(lonLat);
}

function haversineKm(a: LonLat, b: LonLat): number {
  const R = 6371;
  const dLat = ((b[1] - a[1]) * Math.PI) / 180;
  let dLonDeg = b[0] - a[0];
  if (dLonDeg > 180) dLonDeg -= 360;
  if (dLonDeg < -180) dLonDeg += 360;
  const dLon = (dLonDeg * Math.PI) / 180;
  const la1 = (a[1] * Math.PI) / 180;
  const la2 = (b[1] * Math.PI) / 180;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(la1) * Math.cos(la2) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

/* Segment lengths per polyline, computed once: pointAtFraction runs for
 * every flow pulse of every lane on each 300 ms flow step, and the
 * haversine pass over the path dominated it. Paths are treated as
 * immutable once indexed (they are the run's served lane paths). */
const segCache = new WeakMap<LonLat[], { segs: Float64Array; total: number }>();
function segInfo(pts: LonLat[]): { segs: Float64Array; total: number } {
  const hit = segCache.get(pts);
  if (hit && hit.segs.length === pts.length - 1) return hit;
  const segs = new Float64Array(pts.length - 1);
  let total = 0;
  for (let i = 1; i < pts.length; i++) {
    const s = haversineKm(pts[i - 1], pts[i]);
    segs[i - 1] = s;
    total += s;
  }
  const info = { segs, total };
  segCache.set(pts, info);
  return info;
}

/**
 * Point at arc-length fraction `frac` (0..1) along a [lon, lat]
 * polyline — used for lane-midpoint chips and flow pulses. This is a
 * DISPLAY interpolation over the server's rendered path only; entity
 * positions never come from here (they are served by /sim/state).
 * Longitude interpolation is unwrapped so a segment crossing the
 * antimeridian interpolates the short way (mirrors server
 * point_at_fraction).
 */
export function pointAtFraction(pts: LonLat[], frac: number): LonLat {
  if (!pts.length) throw new Error("pointAtFraction: empty path");
  if (frac <= 0 || pts.length === 1) return pts[0];
  if (frac >= 1) return pts[pts.length - 1];
  const { segs, total } = segInfo(pts);
  if (total <= 0) return pts[0];
  let target = frac * total;
  for (let i = 0; i < segs.length; i++) {
    const seg = segs[i];
    if (seg <= 0) continue;
    if (target <= seg) {
      const t = target / seg;
      const [lon1, lat1] = pts[i];
      const [lon2, lat2] = pts[i + 1];
      let dLon = lon2 - lon1;
      if (dLon > 180) dLon -= 360;
      if (dLon < -180) dLon += 360;
      let lon = lon1 + t * dLon;
      if (lon > 180) lon -= 360;
      if (lon < -180) lon += 360;
      return [lon, lat1 + t * (lat2 - lat1)];
    }
    target -= seg;
  }
  return pts[pts.length - 1];
}

/**
 * Initial bearing (degrees, 0 = north, clockwise) of travel along a
 * [lon, lat] polyline at the point nearest `pos` — orients the entity
 * glyphs. Display-only: derived from the rendered lane geometry, never
 * from a recomputed entity position.
 */
export function bearingAlongPath(pts: LonLat[], pos: LonLat): number {
  if (pts.length < 2) return 0;
  let best = 0;
  let bestD = Infinity;
  for (let i = 0; i < pts.length; i++) {
    const d = haversineKm(pts[i], pos);
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  }
  // Direction of travel along the segment starting at the nearest
  // sample (the final segment when the nearest sample is the last one).
  const i0 = Math.min(best, pts.length - 2);
  const from = pts[i0];
  const to = pts[i0 + 1];
  const la1 = (from[1] * Math.PI) / 180;
  const la2 = (to[1] * Math.PI) / 180;
  let dLonDeg = to[0] - from[0];
  if (dLonDeg > 180) dLonDeg -= 360;
  if (dLonDeg < -180) dLonDeg += 360;
  const dLon = (dLonDeg * Math.PI) / 180;
  const y = Math.sin(dLon) * Math.cos(la2);
  const x =
    Math.cos(la1) * Math.sin(la2) -
    Math.sin(la1) * Math.cos(la2) * Math.cos(dLon);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

/** True when any segment of a [lon, lat] polyline spans > 30° of
 *  longitude — the acceptance check (no wrap streaks). */
export function hasWrapStreak(subpaths: LonLat[][]): boolean {
  for (const sub of subpaths) {
    for (let i = 1; i < sub.length; i++) {
      if (Math.abs(sub[i][0] - sub[i - 1][0]) > 30) return true;
    }
  }
  return false;
}
