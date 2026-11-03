// TWIN trails (pure). A trail is the stretch of the entity's OWN lane
// path behind it, sampled into a bounded number of vertices and drawn as
// fading segments (alpha falls off toward the tail). Because it is read
// off the lane path at the entity's arc position it is deterministic
// (scrub-safe, no history to keep) and always follows the leg exactly.
// Length is bounded twice: by TRAIL_VERTS vertices and by MAX_TRAIL_KM.

import { MAX_TRAIL_KM, TRAIL_HOURS, TRAIL_VERTS } from "./lod";
import type { DrawSet, FleetEngine } from "./fleet";
import { lerpOnSegment, vertexAtArc } from "./pathTable";

export const TRAIL_SEGS = TRAIL_VERTS - 1;
/** alpha multiplier per segment, precomputed (head to tail) */
const FADE = Array.from({ length: TRAIL_SEGS }, (_, j) => (1 - (j + 0.5) / TRAIL_SEGS) ** 2);
const MIN_TRAIL_KM = 30;

export class TrailBuffer {
  n = 0;
  /** bumped by fillTrails: lets the layer reuse its data wrapper (no re-upload) when unchanged */
  version = 0;
  cap = 0;
  src = new Float64Array(0);
  dst = new Float64Array(0);
  color = new Uint8ClampedArray(0);
  ensure(segs: number): void {
    if (segs <= this.cap) return;
    let c = Math.max(TRAIL_SEGS * 64, this.cap);
    while (c < segs) c *= 2;
    this.cap = c;
    this.src = new Float64Array(c * 2);
    this.dst = new Float64Array(c * 2);
    this.color = new Uint8ClampedArray(c * 4);
  }
}

/** Trail length in km for a speed (km/h) and mode: speed x window,
 *  bounded to [MIN_TRAIL_KM, MAX_TRAIL_KM[mode]]. */
export function trailLengthKm(speedKmh: number, mode: 0 | 1): number {
  const hours = mode === 1 ? TRAIL_HOURS.air : TRAIL_HOURS.sea;
  const max = mode === 1 ? MAX_TRAIL_KM.air : MAX_TRAIL_KM.sea;
  const km = speedKmh * hours;
  return km < MIN_TRAIL_KM ? MIN_TRAIL_KM : km > max ? max : km;
}

/** Per-segment alpha multiplier for segment j (0 = at the head). */
export function trailFade(j: number, segs: number = TRAIL_SEGS): number {
  const f = 1 - (j + 0.5) / segs;
  return f * f;
}

export interface TrailBounds {
  west: number;
  south: number;
  east: number;
  north: number;
}

const tmp = new Float64Array(3);
const prev = new Float64Array(3);

/**
 * Write the trail segments of every drawn, moving entity of `sets`
 * into `buf` (reused). `bounds` (optional) culls entities outside the
 * padded viewport. Returns the segment count. Entities without a lane
 * path, at rest, or with a degenerate path draw no trail.
 */
export function fillTrails(
  engine: FleetEngine,
  sets: ReadonlyArray<DrawSet>,
  nowMs: number,
  buf: TrailBuffer,
  bounds?: TrailBounds | null,
  maxEntities: number = 12000,
): number {
  const ctx = engine.ctx;
  if (!ctx) {
    buf.n = 0;
    return 0;
  }
  let total = 0;
  for (const s of sets) total += s.n;
  buf.ensure(Math.min(total, maxEntities) * TRAIL_SEGS);
  let seg = 0;
  let ents = 0;
  for (const set of sets) {
    for (let k = 0; k < set.n && ents < maxEntities; k++) {
      const slot = set.slot[k];
      const lane = engine.laneOf(slot);
      if (lane < 0 || engine.stateOf(slot) !== 0) continue;
      const speed = engine.speedOf(slot);
      if (!(speed > 0)) continue;
      const lon = set.pos[k * 2];
      const lat = set.pos[k * 2 + 1];
      if (bounds && (lat < bounds.south || lat > bounds.north || !lonIn(lon, bounds))) continue;
      const arc = engine.arcNow(slot, nowMs);
      if (!(arc >= 0)) continue;
      const mode = engine.modeOf(slot);
      const len = trailLengthKm(speed, mode);
      const step = len / TRAIL_SEGS;
      const dir = engine.dirOf(slot);
      const total_ = ctx.paths.total[lane];
      ents++;
      // vertex 0 = the drawn head (continuity with the glyph)
      prev[0] = lon;
      prev[1] = lat;
      const r = set.color[k * 4];
      const g = set.color[k * 4 + 1];
      const b = set.color[k * 4 + 2];
      const a = set.color[k * 4 + 3];
      const lane0 = ctx.paths.start[lane];
      const laneEnd = ctx.paths.start[lane + 1] - 2; // last segment start
      let v = vertexAtArc(ctx.paths, lane, arc);
      for (let j = 0; j < TRAIL_SEGS; j++) {
        let s = arc - dir * (j + 1) * step;
        if (s < 0) s = 0;
        else if (s > total_) s = total_;
        // walk the vertex cursor to bracket s (trails are ordered)
        const cum = ctx.paths.cum;
        while (v > lane0 && cum[v] > s) v--;
        while (v < laneEnd && cum[v + 1] < s) v++;
        lerpOnSegment(ctx.paths, v, s, tmp);
        const o = seg * 2;
        buf.src[o] = prev[0];
        buf.src[o + 1] = prev[1];
        if (Math.abs(tmp[0] - prev[0]) > 180) {
          // antimeridian: collapse the segment instead of streaking
          buf.dst[o] = prev[0];
          buf.dst[o + 1] = prev[1];
        } else {
          buf.dst[o] = tmp[0];
          buf.dst[o + 1] = tmp[1];
        }
        const c = seg * 4;
        buf.color[c] = r;
        buf.color[c + 1] = g;
        buf.color[c + 2] = b;
        buf.color[c + 3] = a * 0.6 * FADE[j];
        prev[0] = tmp[0];
        prev[1] = tmp[1];
        seg++;
      }
    }
  }
  buf.n = seg;
  buf.version++;
  return seg;
}

function lonIn(lon: number, b: TrailBounds): boolean {
  return b.west <= b.east ? lon >= b.west && lon <= b.east : lon >= b.west || lon <= b.east;
}
