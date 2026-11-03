// TWIN path table (pure, allocation-free lookups). All lane rendered
// paths packed into shared typed arrays so the per-frame interpolation
// never allocates: lane i owns vertices start[i]..start[i+1]-1, with a
// cumulative great-circle length (km) per vertex. Entities are placed by
// ARC LENGTH along their lane's served rendered path, which makes motion
// leg-following (it turns where the leg turns) and gives a heading for
// free. Display interpolation only: served positions stay exact at the
// ends of every tween (fleet.ts draws the served lon/lat when settled).

export interface LanePathInput {
  key: string;
  /** server order: [lat, lon] samples of the rendered path */
  path: ReadonlyArray<readonly [number, number]>;
}

export interface PathTable {
  laneIndex: Map<string, number>;
  start: Int32Array; // length lanes+1
  lon: Float64Array;
  lat: Float64Array;
  /** cumulative km from the lane's first vertex */
  cum: Float64Array;
  /** total km per lane */
  total: Float64Array;
  /** cos(latitude) per vertex (hoisted out of the projection loop) */
  kcos: Float64Array;
  /** bearing (deg, 0 = north, clockwise) of segment i -> i+1 per vertex;
   *  the last vertex of each lane is unused */
  brg: Float32Array;
}

const R_KM = 6371.0088;
const D2R = Math.PI / 180;

function wrapDLon(d: number): number {
  if (d > 180) return d - 360;
  if (d < -180) return d + 360;
  return d;
}

/** Great-circle distance in km (haversine, antimeridian-safe). */
export function haversineKm(lon1: number, lat1: number, lon2: number, lat2: number): number {
  const dLat = (lat2 - lat1) * D2R;
  const dLon = wrapDLon(lon2 - lon1) * D2R;
  const a =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * D2R) * Math.cos(lat2 * D2R) * Math.sin(dLon / 2) ** 2;
  return 2 * R_KM * Math.asin(Math.min(1, Math.sqrt(a)));
}

export function buildPathTable(lanes: ReadonlyArray<LanePathInput>): PathTable {
  let nv = 0;
  for (const l of lanes) nv += l.path.length;
  const start = new Int32Array(lanes.length + 1);
  const lon = new Float64Array(nv);
  const lat = new Float64Array(nv);
  const cum = new Float64Array(nv);
  const total = new Float64Array(lanes.length);
  const kcos = new Float64Array(nv);
  const brg = new Float32Array(nv);
  const laneIndex = new Map<string, number>();
  let v = 0;
  for (let i = 0; i < lanes.length; i++) {
    const p = lanes[i].path;
    laneIndex.set(lanes[i].key, i);
    start[i] = v;
    let acc = 0;
    for (let j = 0; j < p.length; j++) {
      lat[v] = p[j][0];
      lon[v] = p[j][1];
      kcos[v] = Math.cos(lat[v] * D2R);
      if (j > 0) {
        acc += haversineKm(lon[v - 1], lat[v - 1], lon[v], lat[v]);
        const dl = wrapDLon(lon[v] - lon[v - 1]);
        let b = Math.atan2(dl * kcos[v - 1], lat[v] - lat[v - 1]) / D2R;
        if (b < 0) b += 360;
        brg[v - 1] = b;
      }
      cum[v] = acc;
      v++;
    }
    total[i] = acc;
  }
  start[lanes.length] = v;
  return { laneIndex, start, lon, lat, cum, total, kcos, brg };
}

/** Vertex count of a lane. */
export const laneVertexCount = (t: PathTable, lane: number): number => t.start[lane + 1] - t.start[lane];

/** Search window (segments each side of the hint) and the accept
 *  radius (deg) of the hinted local search; beyond it a full scan runs. */
const HINT_WINDOW = 12;
const HINT_ACCEPT_DEG2 = 0.5 * 0.5;

/** Index of the vertex at or before arc `s` on a lane (binary search). */
export function vertexAtArc(t: PathTable, lane: number, s: number): number {
  let lo = t.start[lane];
  let hi = t.start[lane + 1] - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (t.cum[mid] <= s) lo = mid;
    else hi = mid;
  }
  return lo;
}

function scanSegments(
  t: PathTable,
  i0: number,
  i1: number,
  lon: number,
  lat: number,
  out: Float64Array,
): void {
  let bestD = Infinity;
  let bestArc = 0;
  for (let i = i0; i < i1; i++) {
    const k = t.kcos[i];
    const sx = wrapDLon(t.lon[i + 1] - t.lon[i]) * k;
    const sy = t.lat[i + 1] - t.lat[i];
    const px = wrapDLon(lon - t.lon[i]) * k;
    const py = lat - t.lat[i];
    const len2 = sx * sx + sy * sy;
    let u = len2 > 0 ? (px * sx + py * sy) / len2 : 0;
    if (u < 0) u = 0;
    else if (u > 1) u = 1;
    const dx = px - u * sx;
    const dy = py - u * sy;
    const d = dx * dx + dy * dy;
    if (d < bestD) {
      bestD = d;
      bestArc = t.cum[i] + u * (t.cum[i + 1] - t.cum[i]);
    }
  }
  out[0] = bestD;
  out[1] = bestArc;
}

const scanOut = new Float64Array(2);

/**
 * Arc position (km from the lane start) of the point on the lane's
 * path nearest to (lon, lat): nearest-segment projection in a local
 * equirectangular frame. Returns -1 for lanes with < 2 vertices.
 * `residual` (optional 1-elem array) receives the offset in km.
 * `hintArc` (optional, >= 0) is a nearby previous arc: a windowed
 * search around it is tried first and accepted when it lands within
 * 0.5 deg of the path, else the whole lane is scanned.
 */
export function arcOfPoint(
  t: PathTable,
  lane: number,
  lon: number,
  lat: number,
  residual?: Float64Array,
  hintArc: number = -1,
): number {
  const a = t.start[lane];
  const b = t.start[lane + 1];
  if (b - a < 2) return -1;
  if (hintArc >= 0 && b - a > 2 * HINT_WINDOW + 2) {
    const v = vertexAtArc(t, lane, hintArc);
    const i0 = Math.max(a, v - HINT_WINDOW);
    const i1 = Math.min(b - 1, v + HINT_WINDOW);
    scanSegments(t, i0, i1, lon, lat, scanOut);
    if (scanOut[0] <= HINT_ACCEPT_DEG2) {
      if (residual) residual[0] = Math.sqrt(scanOut[0]) * 111.195;
      return scanOut[1];
    }
  }
  scanSegments(t, a, b - 1, lon, lat, scanOut);
  if (residual) residual[0] = Math.sqrt(scanOut[0]) * 111.195;
  return scanOut[1];
}

/**
 * Position + bearing at arc `s` (km) on a lane. Writes
 * out[0]=lon, out[1]=lat, out[2]=bearing (deg, 0=north, clockwise, in
 * the direction of INCREASING arc). `s` clamps to [0, total]. Binary
 * search per call, no allocation. Returns false for degenerate lanes.
 */
export function pointAtArc(t: PathTable, lane: number, s: number, out: Float64Array): boolean {
  const a = t.start[lane];
  const b = t.start[lane + 1];
  if (b - a < 2) return false;
  const total = t.total[lane];
  const arc = s <= 0 ? 0 : s >= total ? total : s;
  const lo = vertexAtArc(t, lane, arc);
  const hi = lo + 1;
  const seg = t.cum[hi] - t.cum[lo];
  const u = seg > 0 ? (arc - t.cum[lo]) / seg : 0;
  const dLon = wrapDLon(t.lon[hi] - t.lon[lo]);
  let lon = t.lon[lo] + dLon * u;
  if (lon > 180) lon -= 360;
  else if (lon < -180) lon += 360;
  out[0] = lon;
  out[1] = t.lat[lo] + (t.lat[hi] - t.lat[lo]) * u;
  out[2] = t.brg[lo];
  return true;
}

/**
 * Position at arc `s` given a vertex index `v` known to bracket it
 * (cum[v] <= s <= cum[v+1]); no search. Writes lon/lat to out[0..1].
 * Used by trails, which walk a lane in order.
 */
export function lerpOnSegment(t: PathTable, v: number, s: number, out: Float64Array): void {
  const seg = t.cum[v + 1] - t.cum[v];
  const u = seg > 0 ? Math.min(1, Math.max(0, (s - t.cum[v]) / seg)) : 0;
  const dLon = wrapDLon(t.lon[v + 1] - t.lon[v]);
  let lon = t.lon[v] + dLon * u;
  if (lon > 180) lon -= 360;
  else if (lon < -180) lon += 360;
  out[0] = lon;
  out[1] = t.lat[v] + (t.lat[v + 1] - t.lat[v]) * u;
}
