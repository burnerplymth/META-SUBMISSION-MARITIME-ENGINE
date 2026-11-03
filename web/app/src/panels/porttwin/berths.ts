/* PORT TWIN berth and anchorage placement. The simulation says WHICH berth a
 * vessel is at (an index into `berths`, which belong to a berth group named
 * after an OSM terminal or a quay cluster). It does not say where the berth
 * is. This module puts each berth slot on the map using the same rules the
 * port profile uses to count berths (server/ports/geo.py _group_quays):
 *   - a terminal with OSM quay lines: the quay lines;
 *   - a terminal without: the shoreline (dock and basin outline, coastline)
 *     inside it or within 25 m of it, sampled every 20 m (a PROXY, and the
 *     profile says so);
 *   - quay lines in no terminal: 300 m single-linkage clusters, numbered
 *     the way the profile numbers them.
 * Slots are spaced evenly along that line. This is a visualisation layout,
 * not a survey: the map labels it PROXY. Groups that cannot be reproduced
 * (default profiles, a profile built from other geometry) are reported as
 * unplaced, never guessed. */
import {
  distPtRing, pathLength, pointAtHalf, pointInRing, ringArea,
  type BBox, type PFeature, type Prepared, type XY,
} from './geo';

export interface LineSample { x: number; y: number; tx: number; ty: number; nx: number; ny: number }
export interface GroupPlacement {
  id: string;
  /** quay = OSM quay lines; shoreline = PROXY; none = could not be placed. */
  source: 'quay' | 'shoreline' | 'none';
  samples: LineSample[];
  lengthM: number;
}
export interface BerthSlot { index: number; group: string; x: number; y: number; heading: number; nx: number; ny: number; lengthM: number }
export interface AnchorageLayout { positions: XY[]; spacingM: number; polygons: number }
export interface Layout {
  groups: Map<string, GroupPlacement>;
  /** By berth index in the frames doc; null = not placed. */
  berths: Array<BerthSlot | null>;
  anchorage: AnchorageLayout | null;
  unplacedGroups: string[];
}

const STEP_M = 20;
const NEAR_M = 25;
const QUAY_STEP_M = 10;

interface Term { area: number; f: PFeature; ring: XY[]; bb: BBox }

/** The tangent of a polyline at a distance, and the sample point. */
function resample(path: readonly XY[], step: number): Array<{ x: number; y: number; tx: number; ty: number }> {
  const out: Array<{ x: number; y: number; tx: number; ty: number }> = [];
  for (let i = 0; i + 1 < path.length; i += 1) {
    const [x0, y0] = path[i]; const [x1, y1] = path[i + 1];
    const l = Math.hypot(x1 - x0, y1 - y0);
    if (l === 0) continue;
    const k = Math.max(1, Math.ceil(l / step));
    const tx = (x1 - x0) / l; const ty = (y1 - y0) / l;
    for (let j = 0; j < k; j += 1) {
      const t = (j + 0.5) / k;
      out.push({ x: x0 + t * (x1 - x0), y: y0 + t * (y1 - y0), tx, ty });
    }
  }
  return out;
}

/** Is a metre coordinate on water? Water polygons win; otherwise water is whatever the coastline-built land does not cover. */
export function makeWaterTest(p: Prepared): (x: number, y: number) => boolean {
  const waters = (p.byLayer.get('water') ?? []).filter((f) => f.polys.length > 0);
  return (x, y) => {
    for (const f of waters) {
      if (x < f.bbox[0] || x > f.bbox[2] || y < f.bbox[1] || y > f.bbox[3]) continue;
      for (const pg of f.polys) {
        if (pointInRing(x, y, pg[0]) && !pg.slice(1).some((h) => pointInRing(x, y, h))) return true;
      }
    }
    if (!p.hasCoast) return false;
    let inside = false;
    for (const r of p.land) if (pointInRing(x, y, r)) inside = !inside;
    return !inside;
  };
}

function withNormals(raw: Array<{ x: number; y: number; tx: number; ty: number }>, isWater: (x: number, y: number) => boolean): LineSample[] {
  return raw.map((s) => {
    const lx = -s.ty; const ly = s.tx; // left normal
    const left = isWater(s.x + lx * 12, s.y + ly * 12);
    const right = isWater(s.x - lx * 12, s.y - ly * 12);
    const useLeft = left && !right;
    return { ...s, nx: useLeft ? lx : -lx, ny: useLeft ? ly : -ly };
  });
}

function shorePaths(p: Prepared): XY[][] {
  const out: XY[][] = [];
  for (const f of p.byLayer.get('coastline') ?? []) out.push(...f.lines);
  for (const f of p.byLayer.get('water') ?? []) for (const pg of f.polys) for (const r of pg) out.push(r);
  return out;
}

/** Group placements for every group the profile can name (terminal groups, shoreline proxies, quay clusters). */
export function computeGroups(p: Prepared): Map<string, GroupPlacement> {
  const isWater = makeWaterTest(p);
  const terms: Term[] = [];
  for (const f of p.byLayer.get('terminal') ?? []) {
    const ring = f.polys.length === 1 ? f.polys[0][0] : null; // the server used Polygon geometry only
    if (!ring || f.lines.length || f.points.length) continue;
    terms.push({ area: Math.abs(ringArea(ring)), f, ring, bb: [f.bbox[0] - NEAR_M, f.bbox[1] - NEAR_M, f.bbox[2] + NEAR_M, f.bbox[3] + NEAR_M] });
  }
  terms.sort((a, b) => a.area - b.area || (a.f.osm < b.f.osm ? -1 : 1));

  const quayByTerm = new Map<string, XY[][]>();
  const unassigned: Array<{ mid: XY; len: number; path: XY[] }> = [];
  for (const f of p.byLayer.get('quay') ?? []) {
    for (const ln of f.lines) {
      if (ln.length < 2) continue;
      const len = pathLength(ln); const mid = pointAtHalf(ln);
      let tf: Term | null = null;
      for (const t of terms) if (pointInRing(mid[0], mid[1], t.ring)) { tf = t; break; }
      if (!tf) {
        let best: [number, Term] | null = null;
        for (const t of terms) { const d = distPtRing(mid[0], mid[1], t.ring); if (d <= 100 && (!best || d < best[0])) best = [d, t]; }
        tf = best ? best[1] : null;
      }
      if (tf) { const a = quayByTerm.get(tf.f.osm); if (a) a.push(ln); else quayByTerm.set(tf.f.osm, [ln]); }
      else unassigned.push({ mid, len, path: ln });
    }
  }

  const groups = new Map<string, GroupPlacement>();
  const fromPaths = (id: string, paths: XY[][], source: 'quay' | 'shoreline'): void => {
    const raw = paths.flatMap((pl) => resample(pl, QUAY_STEP_M));
    groups.set(id, { id, source, samples: withNormals(raw, isWater), lengthM: paths.reduce((s, pl) => s + pathLength(pl), 0) });
  };
  for (const [osm, paths] of quayByTerm) fromPaths(`bg_${osm}`, paths, 'quay');

  // terminals with no quay line: shoreline proxy
  const quayLess = terms.filter((t) => !quayByTerm.has(t.f.osm));
  if (quayLess.length) {
    const acc = new Map<string, { len: number; raw: Array<{ x: number; y: number; tx: number; ty: number }> }>();
    const [bx0, by0, bx1, by1] = p.bboxM;
    for (const path of shorePaths(p)) {
      for (let i = 0; i + 1 < path.length; i += 1) {
        const [x0, y0] = path[i]; const [x1, y1] = path[i + 1];
        const l = Math.hypot(x1 - x0, y1 - y0);
        if (l === 0) continue;
        const k = Math.max(1, Math.ceil(l / STEP_M));
        const tx = (x1 - x0) / l; const ty = (y1 - y0) / l;
        for (let j = 0; j < k; j += 1) {
          const t = (j + 0.5) / k;
          const x = x0 + t * (x1 - x0); const y = y0 + t * (y1 - y0);
          if (x < bx0 + 3 || x > bx1 - 3 || y < by0 + 3 || y > by1 - 3) continue;
          for (const t2 of quayLess) {
            if (x < t2.bb[0] || x > t2.bb[2] || y < t2.bb[1] || y > t2.bb[3]) continue;
            if (pointInRing(x, y, t2.ring) || distPtRing(x, y, t2.ring) <= NEAR_M) {
              const e = acc.get(t2.f.osm) ?? { len: 0, raw: [] };
              e.len += l / k; e.raw.push({ x, y, tx, ty }); acc.set(t2.f.osm, e);
              break;
            }
          }
        }
      }
    }
    for (const t of quayLess) {
      const e = acc.get(t.f.osm);
      if (e && e.len >= 40) groups.set(`bg_${t.f.osm}`, { id: `bg_${t.f.osm}`, source: 'shoreline', samples: withNormals(e.raw, isWater), lengthM: e.len });
    }
  }

  // quay lines in no terminal: 300 m single-linkage clusters, numbered as the profile numbers them
  const parent = unassigned.map((_, i) => i);
  const find = (i: number): number => { let a = i; while (parent[a] !== a) { parent[a] = parent[parent[a]]; a = parent[a]; } return a; };
  for (let i = 0; i < unassigned.length; i += 1) {
    for (let j = i + 1; j < unassigned.length; j += 1) {
      if (find(i) === find(j)) continue;
      const a = unassigned[i]; const b = unassigned[j];
      if (Math.hypot(a.mid[0] - b.mid[0], a.mid[1] - b.mid[1]) <= 300 + 0.5 * (a.len + b.len)) parent[find(i)] = find(j);
    }
  }
  const clusters = new Map<number, typeof unassigned>();
  unassigned.forEach((u, i) => { const r = find(i); const a = clusters.get(r); if (a) a.push(u); else clusters.set(r, [u]); });
  const round1 = (v: number) => Math.round(v * 10) / 10;
  const ordered = [...clusters.values()].sort((a, b) => {
    const ka = [round1(Math.min(...a.map((m) => m.mid[1]))), round1(Math.min(...a.map((m) => m.mid[0]))), -a.reduce((s, m) => s + m.len, 0)];
    const kb = [round1(Math.min(...b.map((m) => m.mid[1]))), round1(Math.min(...b.map((m) => m.mid[0]))), -b.reduce((s, m) => s + m.len, 0)];
    return ka[0] - kb[0] || ka[1] - kb[1] || ka[2] - kb[2];
  });
  ordered.forEach((c, n) => fromPaths(`bg_q${n + 1}`, c.map((m) => m.path), 'quay'));
  return groups;
}

/** Hex-packed anchorage slot centres inside the anchorage polygons: 800 m apart (the profile's swing radius), tighter only when the run needs more slots than that holds. */
export function anchoragePositions(p: Prepared, slots: number, radiusM = 400): AnchorageLayout | null {
  const polys = (p.byLayer.get('anchorage') ?? []).flatMap((f) => f.polys.map((pg) => pg[0])).filter((r) => r && r.length >= 3);
  if (polys.length === 0 || slots <= 0) return null;
  polys.sort((a, b) => Math.abs(ringArea(b)) - Math.abs(ringArea(a)));
  let d = radiusM * 2;
  for (let guard = 0; guard < 12; guard += 1) {
    const pos: XY[] = [];
    for (const ring of polys) {
      let x0 = Infinity; let y0 = Infinity; let x1 = -Infinity; let y1 = -Infinity;
      for (const [x, y] of ring) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
      const rowH = (d * Math.sqrt(3)) / 2;
      let row = 0;
      for (let y = y0 + rowH / 2; y <= y1; y += rowH, row += 1) {
        for (let x = x0 + d / 2 + (row % 2 ? d / 2 : 0); x <= x1; x += d) {
          if (pointInRing(x, y, ring) && distPtRing(x, y, ring) >= d * 0.3) pos.push([x, y]);
        }
      }
    }
    if (pos.length >= slots || d <= 90) return { positions: pos.slice(0, slots), spacingM: d, polygons: polys.length };
    d *= 0.82;
  }
  return null;
}

/** Slot i of n along a group's samples, centre-weighted. */
export function slotSample(g: GroupPlacement, i: number, n: number): LineSample | null {
  if (g.samples.length === 0 || n <= 0) return null;
  return g.samples[Math.min(g.samples.length - 1, Math.floor(((i + 0.5) * g.samples.length) / n))];
}

export interface BerthRef { index: number; group: string; slot: number; length_m: number }

/** The full layout for one frames doc. */
export function computeLayout(p: Prepared, berths: readonly BerthRef[], anchorageSlots: number, groups?: Map<string, GroupPlacement>): Layout {
  const gp = groups ?? computeGroups(p);
  const perGroup = new Map<string, number>();
  for (const b of berths) perGroup.set(b.group, Math.max(perGroup.get(b.group) ?? 0, b.slot + 1));
  const slots: Array<BerthSlot | null> = berths.map((b) => {
    const g = gp.get(b.group);
    const s = g ? slotSample(g, b.slot, perGroup.get(b.group) ?? 1) : null;
    return s ? { index: b.index, group: b.group, x: s.x, y: s.y, heading: Math.atan2(s.ty, s.tx), nx: s.nx, ny: s.ny, lengthM: b.length_m } : null;
  });
  const unplaced = [...perGroup.keys()].filter((id) => !(gp.get(id)?.samples.length)).sort();
  return { groups: gp, berths: slots, anchorage: anchoragePositions(p, anchorageSlots), unplacedGroups: unplaced };
}
