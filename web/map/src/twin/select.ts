// TWIN selection (pure): filter toggles, hover/selection chips, region
// (box / lasso) counting and the selected entity's full-route preview.

import type { SimEntity } from "../sim/types";
import {
  ALL_CLASSES,
  CLASS_LABEL,
  F_DELAYED,
  F_WX,
  FLAG_BITS,
  FLAG_LABEL,
  STATE_LABEL,
  type FleetEngine,
  type FleetFilter,
} from "./fleet";
import { pointAtArc } from "./pathTable";

/* ---- filters --------------------------------------------------------- */

/** Toggle one state flag in the OR-mask (QUEUED / DELAYED / WX-HELD ...). */
export function toggleFlag(f: FleetFilter, bit: number): FleetFilter {
  return { ...f, flagMask: f.flagMask ^ bit };
}

/** Toggle one class. From the all-classes state, the first click ISOLATES
 *  the class; clicking the last remaining class restores all. */
export function toggleClass(f: FleetFilter, cls: number): FleetFilter {
  const bit = 1 << cls;
  if (f.classMask === ALL_CLASSES) return { ...f, classMask: bit };
  const next = f.classMask ^ bit;
  return { ...f, classMask: next === 0 ? ALL_CLASSES : next };
}

export const filterActive = (f: FleetFilter): boolean => f.flagMask !== 0 || f.classMask !== ALL_CLASSES;

/* ---- chips ----------------------------------------------------------- */

export interface EntityChips {
  id: string;
  /** chips, in order: class, state, then DELAYED / WX-HELD / DIVERTED flags */
  chips: string[];
  /** true for the sim fleet: the SIMULATED chip is mandatory */
  simulated: boolean;
}

/** Hover/selection chips for a SIM slot: id, class, state, SIMULATED. */
export function simChips(engine: FleetEngine, slot: number): EntityChips | null {
  const e: SimEntity | null = engine.simEntity(slot);
  if (!e) return null;
  const chips: string[] = [CLASS_LABEL[engine.classOf(slot)], STATE_LABEL[engine.stateOf(slot)]];
  const fl = engine.flagsOf(slot);
  for (const bit of FLAG_BITS) {
    if ((bit === F_DELAYED || bit === F_WX) && fl & bit) chips.push(FLAG_LABEL[bit]);
  }
  if (e.diverted) chips.push("DIVERTED");
  return { id: e.vessel_name ?? e.tail ?? e.id, chips, simulated: true };
}

/** Hover chips for a LIVE slot (never SIMULATED). */
export function liveChips(engine: FleetEngine, slot: number, label: "LIVE" | "SNAPSHOT"): EntityChips | null {
  const r = engine.liveRow(slot);
  if (!r) return null;
  const chips: string[] = [label, engine.modeOf(slot) === 1 ? "AIRCRAFT" : "VESSEL"];
  if (Number.isFinite(r.speed)) chips.push(`${Math.round(r.speed)} kn`);
  if (Number.isFinite(r.age_s)) chips.push(`Age ${Math.round(r.age_s)} s`);
  return { id: r.id, chips, simulated: false };
}

/* ---- region selection ------------------------------------------------ */

export type Pt = readonly [number, number];

/** Ray-casting point-in-polygon (screen space). Polygon is implicitly closed. */
export function pointInPolygon(x: number, y: number, poly: ReadonlyArray<Pt>): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i][0];
    const yi = poly[i][1];
    const xj = poly[j][0];
    const yj = poly[j][1];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export function rectPolygon(x0: number, y0: number, x1: number, y1: number): Pt[] {
  const a = Math.min(x0, x1);
  const b = Math.max(x0, x1);
  const c = Math.min(y0, y1);
  const d = Math.max(y0, y1);
  return [
    [a, c],
    [b, c],
    [b, d],
    [a, d],
  ];
}

export interface RegionSummary {
  total: number;
  sea: number;
  air: number;
  byFlag: number[]; // indexed like FLAG_BITS
  slots: number[];
}

/**
 * Count the visible (filter-passing) sim entities whose projected
 * position falls inside a screen-space polygon. `project` maps
 * lon/lat to container pixels (map.project). `maxSlots` bounds the
 * returned slot list; counts are always complete.
 */
export function summariseRegion(
  engine: FleetEngine,
  nowMs: number,
  filter: FleetFilter,
  project: (lon: number, lat: number) => Pt,
  poly: ReadonlyArray<Pt>,
  maxSlots = 5000,
): RegionSummary {
  const out: RegionSummary = { total: 0, sea: 0, air: 0, byFlag: FLAG_BITS.map(() => 0), slots: [] };
  if (poly.length < 3) return out;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const p of poly) {
    if (p[0] < minX) minX = p[0];
    if (p[0] > maxX) maxX = p[0];
    if (p[1] < minY) minY = p[1];
    if (p[1] > maxY) maxY = p[1];
  }
  engine.forEachVisible(nowMs, filter, (slot, lon, lat) => {
    const p = project(lon, lat);
    if (p[0] < minX || p[0] > maxX || p[1] < minY || p[1] > maxY) return;
    if (!pointInPolygon(p[0], p[1], poly)) return;
    out.total++;
    if (engine.modeOf(slot) === 0) out.sea++;
    else out.air++;
    const f = engine.flagsOf(slot);
    for (let b = 0; b < FLAG_BITS.length; b++) if (f & FLAG_BITS[b]) out.byFlag[b]++;
    if (out.slots.length < maxSlots) out.slots.push(slot);
  });
  return out;
}

/* ---- route preview --------------------------------------------------- */

export interface RoutePreview {
  /** travelled part of the leg, [lon, lat] vertices, start -> entity */
  done: [number, number][];
  /** remaining part, entity -> destination end */
  ahead: [number, number][];
}

const tmp = new Float64Array(3);

/**
 * The selected entity's FULL leg on its lane path, split at the entity:
 * `done` (behind) and `ahead` (toward its destination). Antimeridian
 * crossings split into separate sub-polylines upstream; here vertices
 * are returned as served (the layer splits on |dLon| > 180).
 */
export function routePreview(engine: FleetEngine, slot: number, nowMs: number): RoutePreview | null {
  const ctx = engine.ctx;
  if (!ctx) return null;
  const lane = engine.laneOf(slot);
  if (lane < 0) return null;
  const arc = engine.arcNow(slot, nowMs);
  if (!(arc >= 0)) return null;
  const t = ctx.paths;
  const a = t.start[lane];
  const b = t.start[lane + 1];
  const dir = engine.dirOf(slot);
  const behind: [number, number][] = [];
  const front: [number, number][] = [];
  if (!pointAtArc(t, lane, arc, tmp)) return null;
  const head: [number, number] = [tmp[0], tmp[1]];
  for (let i = a; i < b; i++) {
    const c = t.cum[i];
    const v: [number, number] = [t.lon[i], t.lat[i]];
    if (c < arc) behind.push(v);
    else front.push(v);
  }
  // behind = [start .. head), front = [head .. end]
  const startSide = [...behind, head];
  const endSide = [head, ...front];
  return dir >= 0 ? { done: startSide, ahead: endSide } : { done: endSide.slice().reverse(), ahead: startSide.slice().reverse() };
}
