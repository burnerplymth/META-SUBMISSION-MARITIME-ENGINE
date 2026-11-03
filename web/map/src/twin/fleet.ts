// TWIN fleet engine (pure, node-testable, allocation-free per frame).
//
// Holds the whole fleet as parallel typed arrays ("slots"), ingests a
// snapshot (the sim state payload, or the LIVE columnar payload) and
// produces per-frame draw buffers:
//   - positions are interpolated between successive snapshots, ALONG
//     the lane's served rendered path (arc-length tween) so motion
//     follows the leg and carries a heading; entities with no lane path
//     (and all LIVE entities) tween linearly the short way round the
//     globe; teleports (scrubs, turn-arounds, lane changes) snap.
//   - served positions stay exact: when a tween settles the served
//     lon/lat is drawn, not the projection.
//   - frame() compacts the visible slots of each mode into reusable
//     typed-array DrawSets (no per-entity objects, no per-frame
//     allocation once warm) that deck.gl consumes as binary attributes.
//
// Everything is DISPLAY interpolation: the fleet is the sim's scripted
// payload; nothing here invents entities or positions beyond smoothing
// between two served ones.

import type { SimEntity, SimLane, SimWeatherCell } from "../sim/types";
import { hullAngleDeg } from "../sim/simLayers";
import {
  CODE_BERTHED,
  CODE_DELAYED,
  CODE_INPORT,
  CODE_QUEUED,
  CODE_WX,
  CODES_PER_MODE,
  speedBin,
} from "./palette";
import {
  arcOfPoint,
  buildPathTable,
  haversineKm,
  pointAtArc,
  type PathTable,
} from "./pathTable";
import { liveKindCode, type LiveEntitiesColumns } from "./live";

/* ---- vocabulary ------------------------------------------------------ */

export const F_MOVING = 1;
export const F_QUEUED = 2;
export const F_ATPORT = 4;
export const F_DELAYED = 8;
export const F_WX = 16;
export const FLAG_BITS = [F_MOVING, F_QUEUED, F_ATPORT, F_DELAYED, F_WX] as const;
export const FLAG_LABEL: Record<number, string> = {
  [F_MOVING]: "MOVING",
  [F_QUEUED]: "QUEUED",
  [F_ATPORT]: "AT PLACE",
  [F_DELAYED]: "DELAYED",
  [F_WX]: "WX-HELD",
};
/** late_min above this (minutes) flags an entity DELAYED. */
export const DELAYED_MIN = 60;

export const CLASS_LABEL = [
  "CONTAINER",
  "BULK",
  "TANKER",
  "RORO",
  "FERRY",
  "VESSEL",
  "AIR CARGO",
  "AIR PAX",
  "AIRCRAFT",
] as const;
export const CLASS_SEA = [0, 1, 2, 3, 4, 5] as const;
export const CLASS_AIR = [6, 7, 8] as const;
export const ALL_CLASSES = (1 << CLASS_LABEL.length) - 1;

export function classCodeOf(e: Pick<SimEntity, "mode" | "cls" | "vessel_class">): number {
  if (e.mode === "air") return e.cls === "cargo" ? 6 : e.cls === "passenger" ? 7 : 8;
  switch (e.vessel_class) {
    case "container":
      return 0;
    case "bulk":
      return 1;
    case "tanker":
      return 2;
    case "roro":
      return 3;
    case "passenger":
      return 4;
    default:
      return 5;
  }
}

const STATE_CODE: Record<string, number> = { EN_ROUTE: 0, QUEUED: 1, BERTHED: 2, IN_PORT: 3 };
export const STATE_LABEL = ["EN ROUTE", "QUEUED", "BERTHED", "IN PORT"] as const;

/* ---- context --------------------------------------------------------- */

export interface FleetContext {
  paths: PathTable;
  laneFrom: string[];
  laneTo: string[];
  laneSpeedKmh: Float32Array;
}

export function buildFleetContext(lanes: ReadonlyArray<SimLane>): FleetContext {
  const paths = buildPathTable(lanes);
  const laneSpeedKmh = new Float32Array(lanes.length);
  const laneFrom: string[] = [];
  const laneTo: string[] = [];
  for (let i = 0; i < lanes.length; i++) {
    const l = lanes[i];
    laneFrom.push(l.from);
    laneTo.push(l.to);
    laneSpeedKmh[i] = typeof l.speed_kmh === "number" && l.speed_kmh > 0 ? l.speed_kmh : l.mode === "air" ? 800 : 30;
  }
  return { paths, laneFrom, laneTo, laneSpeedKmh };
}

/* ---- filter ---------------------------------------------------------- */

export interface FleetFilter {
  sea: boolean;
  air: boolean;
  /** bitmask over class codes; ALL_CLASSES = no class filter */
  classMask: number;
  /** OR-mask over F_* flags; 0 = no state filter */
  flagMask: number;
}
export const FILTER_ALL: FleetFilter = { sea: true, air: true, classMask: ALL_CLASSES, flagMask: 0 };

export function passesFilter(f: FleetFilter, mode: number, cls: number, flags: number): boolean {
  if (mode === 0 ? !f.sea : !f.air) return false;
  if (!(f.classMask & (1 << cls))) return false;
  return f.flagMask === 0 || (flags & f.flagMask) !== 0;
}

/* ---- draw buffers ---------------------------------------------------- */

export class DrawSet {
  n = 0;
  cap = 0;
  pos = new Float64Array(0);
  angle = new Float32Array(0);
  color = new Uint8ClampedArray(0);
  slot = new Int32Array(0);
  ensure(cap: number): void {
    if (cap <= this.cap) return;
    let c = Math.max(256, this.cap);
    while (c < cap) c *= 2;
    this.cap = c;
    this.pos = new Float64Array(c * 2);
    this.angle = new Float32Array(c);
    this.color = new Uint8ClampedArray(c * 4);
    this.slot = new Int32Array(c);
  }
}

export interface FleetCounts {
  total: number;
  sea: number;
  air: number;
  /** count per F_* bit, indexed like FLAG_BITS */
  byFlag: number[];
  byClass: number[];
}

/* ---- tween limits ---------------------------------------------------- */

/** Largest displacement (km) between two successive snapshots that is
 *  still tweened; beyond it the entity snaps (scrub / turn-around). */
export const SNAP_KM: readonly [number, number] = [4000, 9000];
const HOLD_KM = 0.05;

type TA = Uint8Array | Int8Array | Int32Array | Float32Array | Float64Array;
function ext<T extends TA>(ctor: new (n: number) => T, old: T | undefined, n: number, fill?: number): T {
  const b = new ctor(n);
  if (fill !== undefined) b.fill(fill);
  if (old) b.set(old as unknown as ArrayLike<number>);
  return b;
}

const M_HOLD = 0;
const M_LINEAR = 1;
const M_PATH = 2;

export class FleetEngine {
  readonly kind: "sim" | "live";
  readonly ctx: FleetContext | null;
  n = 0; // high-water mark of slots
  private cap = 0;
  private ids: (string | null)[] = [];
  private slotOf = new Map<string, number>();
  private free: number[] = [];
  private destIntern = new Map<string, number>();
  private ents: (SimEntity | null)[] = [];
  private liveCols: LiveEntitiesColumns | null = null;
  private liveIdx!: Int32Array;

  private active!: Uint8Array;
  private mode!: Uint8Array;
  private stateCode!: Uint8Array;
  private flags!: Uint8Array;
  private cls!: Uint8Array;
  private colorCode!: Uint8Array;
  private lane!: Int32Array;
  private dir!: Int8Array;
  private dest!: Int32Array;
  private speedKmh!: Float32Array;
  private bearingTo!: Float32Array;
  private motion!: Uint8Array;
  private lon0!: Float64Array;
  private lat0!: Float64Array;
  private lon1!: Float64Array;
  private lat1!: Float64Array;
  private arc0!: Float64Array;
  private arc1!: Float64Array;
  private rgba!: Uint8Array; // per-slot colour (live only)

  private tweenStart = 0;
  private tweenDur = 250;
  private lastIngest = -1;
  private emaDt = 250;
  /** prefers-reduced-motion: snap instead of tween */
  noTween = false;
  /** bumped on every ingest: lets callers invalidate derived buffers. */
  version = 0;

  readonly sea = new DrawSet();
  readonly air = new DrawSet();
  counts: FleetCounts = { total: 0, sea: 0, air: 0, byFlag: [0, 0, 0, 0, 0], byClass: new Array(9).fill(0) };
  /** diagnostic: ingest-time tween decisions (path / linear / hold / snap). */
  stats = { path: 0, linear: 0, hold: 0, snap: 0, projResidualKmMedian: 0 };

  private readonly scratch = new Float64Array(3);
  private readonly scratch2 = new Float64Array(3);
  private readonly resid = new Float64Array(1);

  constructor(kind: "sim" | "live", ctx: FleetContext | null = null) {
    this.kind = kind;
    this.ctx = ctx;
    this.alloc(1024);
  }

  /* -- storage -- */
  private alloc(cap: number): void {
    this.active = ext(Uint8Array, this.active, cap);
    this.mode = ext(Uint8Array, this.mode, cap);
    this.stateCode = ext(Uint8Array, this.stateCode, cap);
    this.flags = ext(Uint8Array, this.flags, cap);
    this.cls = ext(Uint8Array, this.cls, cap);
    this.colorCode = ext(Uint8Array, this.colorCode, cap);
    this.motion = ext(Uint8Array, this.motion, cap);
    this.lane = ext(Int32Array, this.lane, cap, -1);
    this.dir = ext(Int8Array, this.dir, cap);
    this.dest = ext(Int32Array, this.dest, cap, -1);
    this.liveIdx = ext(Int32Array, this.liveIdx, cap);
    this.speedKmh = ext(Float32Array, this.speedKmh, cap);
    this.bearingTo = ext(Float32Array, this.bearingTo, cap, NaN);
    this.lon0 = ext(Float64Array, this.lon0, cap);
    this.lat0 = ext(Float64Array, this.lat0, cap);
    this.lon1 = ext(Float64Array, this.lon1, cap);
    this.lat1 = ext(Float64Array, this.lat1, cap);
    this.arc0 = ext(Float64Array, this.arc0, cap);
    this.arc1 = ext(Float64Array, this.arc1, cap);
    this.rgba = ext(Uint8Array, this.rgba, cap * 4);
    this.cap = cap;
  }

  /** set by slotFor: whether the last returned slot was newly allocated */
  private lastNew = false;
  private slotFor(id: string): number {
    const hit = this.slotOf.get(id);
    if (hit !== undefined) {
      this.lastNew = false;
      return hit;
    }
    let s: number;
    const reuse = this.free.pop();
    if (reuse !== undefined) s = reuse;
    else {
      s = this.n++;
      if (s >= this.cap) this.alloc(this.cap * 2);
    }
    this.slotOf.set(id, s);
    this.ids[s] = id;
    this.lane[s] = -1;
    this.dest[s] = -1;
    this.bearingTo[s] = NaN;
    this.lastNew = true;
    return s;
  }

  private beginIngest(nowMs: number): void {
    for (let i = 0; i < this.n; i++) this.active[i] = 0;
    if (this.lastIngest >= 0) {
      const dt = Math.max(40, Math.min(700, nowMs - this.lastIngest));
      this.emaDt = this.emaDt * 0.6 + dt * 0.4;
    }
    this.lastIngest = nowMs;
    this.stats = { path: 0, linear: 0, hold: 0, snap: 0, projResidualKmMedian: 0 };
  }

  private endIngest(nowMs: number): void {
    for (let i = 0; i < this.n; i++) {
      if (!this.active[i] && this.ids[i] !== null && this.ids[i] !== undefined) {
        this.slotOf.delete(this.ids[i] as string);
        this.ids[i] = null;
        this.ents[i] = null;
        this.free.push(i);
      }
    }
    this.tweenStart = nowMs;
    // reduced motion: positions step at each served snapshot, no tween
    this.tweenDur = this.noTween ? 0 : this.emaDt;
    this.version++;
    this.recount();
  }

  private recount(): void {
    const c = this.counts;
    c.total = 0;
    c.sea = 0;
    c.air = 0;
    c.byFlag.fill(0);
    c.byClass.fill(0);
    for (let i = 0; i < this.n; i++) {
      if (!this.active[i]) continue;
      c.total++;
      if (this.mode[i] === 0) c.sea++;
      else c.air++;
      c.byClass[this.cls[i]]++;
      const f = this.flags[i];
      for (let b = 0; b < FLAG_BITS.length; b++) if (f & FLAG_BITS[b]) c.byFlag[b]++;
    }
  }

  /* -- evaluation -- */

  /** Current displayed position of a slot at `nowMs` -> out[0..2]
   *  (lon, lat, bearing). Bearing is NaN when unknown. */
  evalSlot(i: number, nowMs: number, out: Float64Array): void {
    const dur = this.tweenDur;
    let u = dur > 0 ? (nowMs - this.tweenStart) / dur : 1;
    if (u > 1) u = 1;
    else if (u < 0) u = 0;
    const m = this.motion[i];
    if (u >= 1 || m === M_HOLD) {
      out[0] = this.lon1[i];
      out[1] = this.lat1[i];
      out[2] = this.settledBearing(i);
      return;
    }
    if (m === M_PATH && this.ctx) {
      const a0 = this.arc0[i];
      const a1 = this.arc1[i];
      if (pointAtArc(this.ctx.paths, this.lane[i], a0 + (a1 - a0) * u, this.scratch2)) {
        out[0] = this.scratch2[0];
        out[1] = this.scratch2[1];
        /* the heading is the one the frame serves (bearing_deg, the local tangent of the lane at the
         * served position); only an entity whose frame serves none takes the lane path's bearing */
        const served = this.bearingTo[i];
        out[2] = served === served ? served : a1 >= a0 ? this.scratch2[2] : (this.scratch2[2] + 180) % 360;
        return;
      }
    }
    const lo0 = this.lon0[i];
    let dLon = this.lon1[i] - lo0;
    if (dLon > 180) dLon -= 360;
    else if (dLon < -180) dLon += 360;
    let lon = lo0 + dLon * u;
    if (lon > 180) lon -= 360;
    else if (lon < -180) lon += 360;
    out[0] = lon;
    out[1] = this.lat0[i] + (this.lat1[i] - this.lat0[i]) * u;
    const dLat = this.lat1[i] - this.lat0[i];
    let br = Math.atan2(dLon * Math.cos((this.lat0[i] * Math.PI) / 180), dLat) * (180 / Math.PI);
    if (br < 0) br += 360;
    out[2] = dLon === 0 && dLat === 0 ? this.settledBearing(i) : br;
  }

  private settledBearing(i: number): number {
    return this.bearingTo[i]; // NaN when unknown
  }

  /** Arc position + direction of travel of a slot now (for trails and
   *  route previews). Returns NaN arc when the slot has no lane path. */
  arcNow(i: number, nowMs: number): number {
    if (!this.ctx || this.lane[i] < 0) return NaN;
    const dur = this.tweenDur;
    let u = dur > 0 ? (nowMs - this.tweenStart) / dur : 1;
    if (u > 1) u = 1;
    else if (u < 0) u = 0;
    if (this.motion[i] === M_PATH) return this.arc0[i] + (this.arc1[i] - this.arc0[i]) * u;
    return this.arc1[i];
  }

  /** True while any tween is still in flight at `nowMs`. */
  moving(nowMs: number): boolean {
    return nowMs < this.tweenStart + this.tweenDur;
  }

  /* -- ingest: sim -- */

  ingestSim(entities: ReadonlyArray<SimEntity>, nowMs: number, cells?: ReadonlyArray<SimWeatherCell> | null): void {
    const ctx = this.ctx as FleetContext;
    this.beginIngest(nowMs);
    const cur = this.scratch;
    let sampled = 0;
    const residuals: number[] = [];
    // severe cells only: WX-held means inside a SEVERE modelled cell
    let sev: SimWeatherCell[] | null = null;
    if (cells && cells.length) sev = cells.filter((c) => c.severe);
    for (let k = 0; k < entities.length; k++) {
      const e = entities[k];
      if (e.lon === null || e.lat === null || e.lon === undefined || e.lat === undefined) continue;
      const s = this.slotFor(e.id);
      const isNew = this.lastNew;
      this.active[s] = 1;
      this.ents[s] = e;
      const mode = e.mode === "air" ? 1 : 0;
      const stCode = STATE_CODE[e.state] ?? 3;
      const laneIdx = ctx.paths.laneIndex.get(e.lane) ?? -1;
      const destId = e.dest ? this.intern(e.dest) : -1;
      const lon = e.lon as number;
      const lat = e.lat as number;
      const moving = stCode === 0;
      const late = typeof e.late_min === "number" ? e.late_min : 0;
      let fl = moving ? F_MOVING : stCode === 1 ? F_QUEUED : F_ATPORT;
      if (late > DELAYED_MIN) fl |= F_DELAYED;
      if (sev && inAnyCell(sev, lon, lat)) fl |= F_WX;

      // FAST PATH: a settled entity whose served position / lane / leg /
      // state did not change only refreshes its flags and colour.
      if (
        !isNew &&
        this.motion[s] === M_HOLD &&
        lon === this.lon1[s] &&
        lat === this.lat1[s] &&
        laneIdx === this.lane[s] &&
        destId === this.dest[s] &&
        stCode === this.stateCode[s]
      ) {
        this.flags[s] = fl;
        this.colorCode[s] = mode * CODES_PER_MODE + this.codeFor(stCode, fl, this.speedKmh[s], mode);
        if (typeof e.bearing_deg === "number") this.bearingTo[s] = e.bearing_deg;
        this.stats.hold++;
        continue;
      }

      // where the entity is drawn right now (becomes the tween origin)
      let curArc = NaN;
      if (!isNew) {
        this.evalSlot(s, nowMs, cur);
        curArc = this.motion[s] === M_PATH || this.motion[s] === M_HOLD ? this.arcNow(s, nowMs) : NaN;
      }
      const prevLane = this.lane[s];
      const prevState = this.stateCode[s];
      const prevDest = this.dest[s];

      this.mode[s] = mode;
      this.stateCode[s] = stCode;
      this.cls[s] = classCodeOf(e);
      this.lane[s] = laneIdx;
      this.dest[s] = destId;
      this.bearingTo[s] = typeof e.bearing_deg === "number" ? e.bearing_deg : NaN;

      // target arc (projection of the served point onto the lane path)
      let arc1 = -1;
      if (laneIdx >= 0) {
        if (!isNew && lon === this.lon1[s] && lat === this.lat1[s] && prevLane === laneIdx) arc1 = this.arc1[s];
        else {
          const hint = !isNew && prevLane === laneIdx ? this.arc1[s] : -1;
          arc1 = arcOfPoint(ctx.paths, laneIdx, lon, lat, this.resid, hint);
          if (arc1 >= 0 && (sampled++ & 15) === 0) residuals.push(this.resid[0]);
        }
      }

      this.flags[s] = fl;
      const sp = moving && laneIdx >= 0 ? ctx.laneSpeedKmh[laneIdx] : 0;
      this.speedKmh[s] = sp;
      this.colorCode[s] = mode * CODES_PER_MODE + this.codeFor(stCode, fl, sp, mode);

      // motion decision
      this.lon1[s] = lon;
      this.lat1[s] = lat;
      this.arc1[s] = arc1;
      if (isNew) {
        this.hold(s, lon, lat, arc1);
        this.dir[s] = this.dirFromDest(laneIdx, destId);
        this.fillBearing(s, laneIdx, arc1);
        continue;
      }
      const maxKm = SNAP_KM[mode];
      const d = haversineKm(cur[0], cur[1], lon, lat);
      const turned = prevState === 0 && stCode === 0 && prevDest !== destId && prevDest >= 0 && destId >= 0;
      if (d < HOLD_KM) {
        this.hold(s, lon, lat, arc1);
      } else if (d > maxKm || turned) {
        this.stats.snap++;
        this.hold(s, lon, lat, arc1);
      } else if (laneIdx >= 0 && laneIdx === prevLane && arc1 >= 0) {
        let a0 = curArc;
        if (!(a0 >= 0)) a0 = arcOfPoint(ctx.paths, laneIdx, cur[0], cur[1], undefined, arc1);
        if (a0 < 0 || Math.abs(arc1 - a0) > maxKm * 1.5) {
          this.stats.snap++;
          this.hold(s, lon, lat, arc1);
        } else {
          this.motion[s] = M_PATH;
          this.arc0[s] = a0;
          this.lon0[s] = cur[0];
          this.lat0[s] = cur[1];
          this.dir[s] = arc1 >= a0 ? 1 : -1;
          this.stats.path++;
        }
      } else {
        this.motion[s] = M_LINEAR;
        this.lon0[s] = cur[0];
        this.lat0[s] = cur[1];
        this.arc0[s] = arc1;
        this.dir[s] = 1;
        this.stats.linear++;
      }
      if (this.motion[s] === M_HOLD) this.dir[s] = this.dirFromDest(laneIdx, destId);
      this.fillBearing(s, laneIdx, arc1);
    }
    this.endIngest(nowMs);
    if (residuals.length) {
      residuals.sort((a, b) => a - b);
      this.stats.projResidualKmMedian = residuals[residuals.length >> 1];
    }
  }

  /** Served bearing missing (parked / queued): take the lane's heading
   *  at the entity once, so glyphs are oriented without per-frame work. */
  private fillBearing(s: number, laneIdx: number, arc: number): void {
    const b = this.bearingTo[s];
    if (b === b || !this.ctx || laneIdx < 0 || arc < 0) return;
    if (pointAtArc(this.ctx.paths, laneIdx, arc, this.scratch2)) {
      this.bearingTo[s] = this.dir[s] < 0 ? (this.scratch2[2] + 180) % 360 : this.scratch2[2];
    }
  }

  private hold(s: number, lon: number, lat: number, arc: number): void {
    this.motion[s] = M_HOLD;
    this.lon0[s] = lon;
    this.lat0[s] = lat;
    this.arc0[s] = arc;
    this.stats.hold++;
  }

  private dirFromDest(laneIdx: number, destId: number): number {
    if (!this.ctx || laneIdx < 0 || destId < 0) return 1;
    const dname = this.internRev(destId);
    return this.ctx.laneTo[laneIdx] === dname ? 1 : this.ctx.laneFrom[laneIdx] === dname ? -1 : 1;
  }

  private internRevArr: string[] = [];
  private intern(s: string): number {
    let v = this.destIntern.get(s);
    if (v === undefined) {
      v = this.internRevArr.length;
      this.internRevArr.push(s);
      this.destIntern.set(s, v);
    }
    return v;
  }
  private internRev(id: number): string {
    return this.internRevArr[id];
  }

  private codeFor(stCode: number, fl: number, speedKmh: number, mode: 0 | 1): number {
    if (stCode === 1) return CODE_QUEUED;
    if (stCode === 2) return CODE_BERTHED;
    if (stCode === 3) return CODE_INPORT;
    if (fl & F_WX) return CODE_WX;
    if (fl & F_DELAYED) return CODE_DELAYED;
    return speedBin(speedKmh, mode);
  }

  /* -- ingest: live -- */

  ingestLive(cols: LiveEntitiesColumns, nowMs: number, core: [number, number, number]): void {
    this.beginIngest(nowMs);
    this.liveCols = cols;
    const cur = this.scratch;
    const n = cols.ids.length;
    for (let k = 0; k < n; k++) {
      const lon = cols.lon[k];
      const lat = cols.lat[k];
      if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
      const s = this.slotFor(cols.ids[k]);
      const isNew = this.lastNew;
      this.active[s] = 1;
      this.liveIdx[s] = k;
      const mode = liveKindCode(cols.kind?.[k]);
      if (!isNew) this.evalSlot(s, nowMs, cur);
      this.mode[s] = mode;
      this.cls[s] = mode === 1 ? 8 : 5;
      this.stateCode[s] = 0;
      const sp = Number(cols.speed?.[k] ?? 0);
      const mv = sp > 0.5;
      this.flags[s] = mv ? F_MOVING : F_ATPORT;
      this.speedKmh[s] = sp * 1.852;
      const h = Number(cols.heading?.[k]);
      this.bearingTo[s] = Number.isFinite(h) && h >= 0 && h <= 360 ? h : NaN;
      this.lane[s] = -1;
      const age = Number(cols.age_s?.[k] ?? 0);
      // observation age fades the mark: fresh = opaque, 5+ min = faint
      const a = Math.round(235 - 150 * Math.min(1, Math.max(0, age) / 300));
      const o = s * 4;
      this.rgba[o] = core[0];
      this.rgba[o + 1] = core[1];
      this.rgba[o + 2] = core[2];
      this.rgba[o + 3] = a;
      this.lon1[s] = lon;
      this.lat1[s] = lat;
      this.arc1[s] = -1;
      if (isNew) {
        this.hold(s, lon, lat, -1);
        continue;
      }
      const d = haversineKm(cur[0], cur[1], lon, lat);
      if (d < HOLD_KM) this.hold(s, lon, lat, -1);
      else if (d > SNAP_KM[mode]) {
        this.stats.snap++;
        this.hold(s, lon, lat, -1);
      } else {
        this.motion[s] = M_LINEAR;
        this.lon0[s] = cur[0];
        this.lat0[s] = cur[1];
        this.stats.linear++;
      }
    }
    this.endIngest(nowMs);
  }

  /* -- frame -- */

  /* -- frame / layout --
   * The DrawSets are laid out ONCE per (ingest, filter, palette): every
   * visible slot gets a stable draw index, static slots (holding at their
   * served position) are written into the buffers a single time, and only
   * the MOVERS (slots still tweening) are re-evaluated on each frame. A
   * frame therefore costs O(movers), not O(fleet). */
  private movers = new Int32Array(0);
  private moverDraw = new Int32Array(0);
  private moverMode = new Uint8Array(0);
  private nMovers = 0;
  private layoutVersion = -1;
  private layoutPal: Uint8Array | null = null;
  private layoutFilter: FleetFilter | null = null;
  /** bumped whenever the DrawSets were re-laid-out (buffers rewritten) */
  layoutId = 0;

  private layoutStale(filter: FleetFilter, palette: Uint8Array | null): boolean {
    const f = this.layoutFilter;
    return (
      this.layoutVersion !== this.version ||
      this.layoutPal !== palette ||
      f === null ||
      f.sea !== filter.sea ||
      f.air !== filter.air ||
      f.classMask !== filter.classMask ||
      f.flagMask !== filter.flagMask
    );
  }

  private buildLayout(nowMs: number, filter: FleetFilter, palette: Uint8Array | null): void {
    const sea = this.sea;
    const air = this.air;
    sea.ensure(this.counts.sea + 8);
    air.ensure(this.counts.air + 8);
    if (this.movers.length < this.n) {
      this.movers = new Int32Array(this.cap);
      this.moverDraw = new Int32Array(this.cap);
      this.moverMode = new Uint8Array(this.cap);
    }
    let ns = 0;
    let na = 0;
    let nm = 0;
    const out = this.scratch;
    const live = this.kind === "live";
    const settled = !this.moving(nowMs);
    for (let i = 0; i < this.n; i++) {
      if (!this.active[i]) continue;
      const m = this.mode[i];
      if (!live && !passesFilter(filter, m, this.cls[i], this.flags[i])) continue;
      const set = m === 0 ? sea : air;
      const k = m === 0 ? ns++ : na++;
      set.slot[k] = i;
      const o = k * 4;
      if (live) {
        const r = i * 4;
        set.color[o] = this.rgba[r];
        set.color[o + 1] = this.rgba[r + 1];
        set.color[o + 2] = this.rgba[r + 2];
        set.color[o + 3] = this.rgba[r + 3];
      } else if (palette) {
        const p = this.colorCode[i] * 4;
        set.color[o] = palette[p];
        set.color[o + 1] = palette[p + 1];
        set.color[o + 2] = palette[p + 2];
        set.color[o + 3] = palette[p + 3];
      }
      this.evalSlot(i, nowMs, out);
      set.pos[k * 2] = out[0];
      set.pos[k * 2 + 1] = out[1];
      const br = out[2];
      set.angle[k] = br === br ? hullAngleDeg(br) : 0;
      if (!settled && this.motion[i] !== M_HOLD) {
        this.movers[nm] = i;
        this.moverDraw[nm] = k;
        this.moverMode[nm] = m;
        nm++;
      }
    }
    sea.n = ns;
    air.n = na;
    this.nMovers = nm;
    this.layoutVersion = this.version;
    this.layoutPal = palette;
    this.layoutFilter = { ...filter };
    this.layoutId++;
  }

  /**
   * Fill the sea/air DrawSets for time `nowMs`. `palette` resolves the
   * sim colour codes (live slots carry their own RGBA). Allocation-free
   * once the DrawSets are warm; O(movers) between layouts.
   */
  frame(nowMs: number, filter: FleetFilter, palette: Uint8Array | null): void {
    if (this.layoutStale(filter, palette)) {
      this.buildLayout(nowMs, filter, palette);
      return;
    }
    const out = this.scratch;
    const nm = this.nMovers;
    for (let j = 0; j < nm; j++) {
      this.evalSlot(this.movers[j], nowMs, out);
      const set = this.moverMode[j] === 0 ? this.sea : this.air;
      const k = this.moverDraw[j];
      set.pos[k * 2] = out[0];
      set.pos[k * 2 + 1] = out[1];
      const br = out[2];
      set.angle[k] = br === br ? hullAngleDeg(br) : 0;
    }
    // settled: the writes above were the final ones
    if (!this.moving(nowMs)) this.nMovers = 0;
  }

  /* -- accessors for selection / tooltip / trails -- */

  slotOfId(id: string): number {
    const s = this.slotOf.get(id);
    return s === undefined ? -1 : s;
  }
  idOf(slot: number): string | null {
    return slot >= 0 && slot < this.n ? (this.ids[slot] ?? null) : null;
  }
  isActive(slot: number): boolean {
    return slot >= 0 && slot < this.n && this.active[slot] === 1;
  }
  modeOf(slot: number): 0 | 1 {
    return this.mode[slot] === 1 ? 1 : 0;
  }
  classOf(slot: number): number {
    return this.cls[slot];
  }
  flagsOf(slot: number): number {
    return this.flags[slot];
  }
  stateOf(slot: number): number {
    return this.stateCode[slot];
  }
  laneOf(slot: number): number {
    return this.lane[slot];
  }
  dirOf(slot: number): number {
    return this.dir[slot] || 1;
  }
  speedOf(slot: number): number {
    return this.speedKmh[slot];
  }
  simEntity(slot: number): SimEntity | null {
    return this.kind === "sim" ? (this.ents[slot] ?? null) : null;
  }
  /** LIVE slot -> the served column row (for the tooltip). */
  liveRow(slot: number): { id: string; kind: string | number; speed: number; age_s: number; heading: number } | null {
    const c = this.liveCols;
    if (this.kind !== "live" || !c || !this.isActive(slot)) return null;
    const k = this.liveIdx[slot];
    return {
      id: c.ids[k],
      kind: c.kind?.[k] ?? "",
      speed: Number(c.speed?.[k] ?? NaN),
      age_s: Number(c.age_s?.[k] ?? NaN),
      heading: Number(c.heading?.[k] ?? NaN),
    };
  }
  /** Active slots passing a filter, for selection counts. */
  forEachVisible(nowMs: number, filter: FleetFilter, cb: (slot: number, lon: number, lat: number) => void): void {
    const out = this.scratch;
    for (let i = 0; i < this.n; i++) {
      if (!this.active[i]) continue;
      if (this.kind === "sim" && !passesFilter(filter, this.mode[i], this.cls[i], this.flags[i])) continue;
      this.evalSlot(i, nowMs, out);
      cb(i, out[0], out[1]);
    }
  }
}

function inAnyCell(cells: ReadonlyArray<SimWeatherCell>, lon: number, lat: number): boolean {
  for (let i = 0; i < cells.length; i++) {
    const b = cells[i].bbox;
    if (lon >= b[0] && lon <= b[2] && lat >= b[1] && lat <= b[3]) return true;
  }
  return false;
}
