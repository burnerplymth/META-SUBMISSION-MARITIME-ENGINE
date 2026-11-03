/* PORT TWIN geometry: projection, preparation of the OSM layers, the land
 * polygons rebuilt from the coastline, and the small geometry helpers the
 * renderer and the berth placement share. Pure (no DOM): the canvas side
 * is render.ts. Coordinates are metres on the same local equirectangular
 * projection the server uses for the port profile (server/ports/geo.py
 * Proj), x east and y north, origin at the middle of the port rectangle. */
import type { GeoDoc, GeoLayer } from './types';

export type XY = [number, number];
export type BBox = [number, number, number, number];

const EARTH_R = 6371008.8;

export interface Proj { lon0: number; lat0: number; kx: number; ky: number }
export function makeProj(bbox: BBox): Proj {
  const lon0 = (bbox[0] + bbox[2]) / 2;
  const lat0 = (bbox[1] + bbox[3]) / 2;
  return { lon0, lat0, kx: (Math.PI / 180) * EARTH_R * Math.cos((lat0 * Math.PI) / 180), ky: (Math.PI / 180) * EARTH_R };
}
export const fwd = (p: Proj, lon: number, lat: number): XY => [(lon - p.lon0) * p.kx, (lat - p.lat0) * p.ky];
export const inv = (p: Proj, x: number, y: number): [number, number] => [x / p.kx + p.lon0, y / p.ky + p.lat0];

/* ── small geometry helpers (mirrors server/ports/geo.py) ───────────── */
export function ringArea(r: readonly XY[]): number {
  let a = 0;
  for (let i = 0; i < r.length; i += 1) {
    const [x0, y0] = r[i]; const [x1, y1] = r[(i + 1) % r.length];
    a += x0 * y1 - x1 * y0;
  }
  return a / 2;
}
export function pointInRing(x: number, y: number, r: readonly XY[]): boolean {
  let inside = false;
  for (let i = 0, j = r.length - 1; i < r.length; j = i, i += 1) {
    const [xi, yi] = r[i]; const [xj, yj] = r[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
export function distPtSeg(px: number, py: number, a: XY, b: XY): number {
  const dx = b[0] - a[0]; const dy = b[1] - a[1];
  const l = dx * dx + dy * dy;
  if (l === 0) return Math.hypot(px - a[0], py - a[1]);
  const t = Math.max(0, Math.min(1, ((px - a[0]) * dx + (py - a[1]) * dy) / l));
  return Math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy));
}
export function distPtRing(px: number, py: number, r: readonly XY[]): number {
  let d = Infinity;
  for (let i = 0; i < r.length; i += 1) d = Math.min(d, distPtSeg(px, py, r[i], r[(i + 1) % r.length]));
  return d;
}
export function distPtPath(px: number, py: number, p: readonly XY[]): number {
  let d = Infinity;
  for (let i = 0; i + 1 < p.length; i += 1) d = Math.min(d, distPtSeg(px, py, p[i], p[i + 1]));
  return d;
}
export const pathLength = (p: readonly XY[]): number => {
  let s = 0;
  for (let i = 0; i + 1 < p.length; i += 1) s += Math.hypot(p[i + 1][0] - p[i][0], p[i + 1][1] - p[i][1]);
  return s;
};
export function pointAtHalf(p: readonly XY[]): XY {
  const total = pathLength(p);
  if (total === 0) return p[0];
  const half = total / 2; let acc = 0;
  for (let i = 0; i + 1 < p.length; i += 1) {
    const seg = Math.hypot(p[i + 1][0] - p[i][0], p[i + 1][1] - p[i][1]);
    if (acc + seg >= half && seg > 0) {
      const f = (half - acc) / seg;
      return [p[i][0] + f * (p[i + 1][0] - p[i][0]), p[i][1] + f * (p[i + 1][1] - p[i][1])];
    }
    acc += seg;
  }
  return p[p.length - 1];
}
export function ringCentroid(r: readonly XY[]): XY {
  let a = 0; let cx = 0; let cy = 0;
  for (let i = 0; i < r.length; i += 1) {
    const [x0, y0] = r[i]; const [x1, y1] = r[(i + 1) % r.length];
    const f = x0 * y1 - x1 * y0;
    a += f; cx += (x0 + x1) * f; cy += (y0 + y1) * f;
  }
  if (Math.abs(a) < 1e-6) { // degenerate: mean of vertices
    let sx = 0; let sy = 0; for (const [x, y] of r) { sx += x; sy += y; }
    return [sx / Math.max(1, r.length), sy / Math.max(1, r.length)];
  }
  return [cx / (3 * a), cy / (3 * a)];
}
export function bboxOf(pts: readonly XY[]): BBox {
  let a = Infinity; let b = Infinity; let c = -Infinity; let d = -Infinity;
  for (const [x, y] of pts) { if (x < a) a = x; if (y < b) b = y; if (x > c) c = x; if (y > d) d = y; }
  return [a, b, c, d];
}

/* ── prepared geometry ──────────────────────────────────────────────── */
export interface PFeature {
  layer: GeoLayer;
  osm: string;
  name?: string;
  kind?: string;
  operator?: string;
  areaM2?: number;
  lengthM?: number;
  /** Polygons as [outer, ...holes]. */
  polys: XY[][][];
  lines: XY[][];
  points: XY[];
  bbox: BBox;
}
export interface Prepared {
  proj: Proj;
  /** The port rectangle in metres: [minx, miny, maxx, maxy]. */
  bboxM: BBox;
  features: PFeature[];
  byLayer: Map<GeoLayer, PFeature[]>;
  counts: Record<string, number>;
  /** Land rings rebuilt from the coastline (evenodd fill). Empty = no coastline in the rectangle. */
  land: XY[][];
  hasCoast: boolean;
}

function toXY(p: Proj, c: unknown): XY { const a = c as number[]; return fwd(p, a[0], a[1]); }
const ringXY = (p: Proj, r: unknown): XY[] => (r as unknown[]).map((c) => toXY(p, c));

export function prepareGeo(doc: GeoDoc): Prepared {
  const bbox = doc.geojson.bbox;
  const proj = makeProj(bbox);
  const a = fwd(proj, bbox[0], bbox[1]); const b = fwd(proj, bbox[2], bbox[3]);
  const bboxM: BBox = [a[0], a[1], b[0], b[1]];
  const features: PFeature[] = [];
  const byLayer = new Map<GeoLayer, PFeature[]>();
  const counts: Record<string, number> = {};
  for (const f of doc.geojson.features) {
    const layer = f.properties.layer as GeoLayer;
    const g = f.geometry;
    const polys: XY[][][] = []; const lines: XY[][] = []; const points: XY[] = [];
    switch (g.type) {
      case 'Polygon': polys.push((g.coordinates as unknown[]).map((r) => ringXY(proj, r))); break;
      case 'MultiPolygon': for (const pg of g.coordinates as unknown[][]) polys.push(pg.map((r) => ringXY(proj, r))); break;
      case 'LineString': lines.push(ringXY(proj, g.coordinates)); break;
      case 'MultiLineString': for (const l of g.coordinates as unknown[]) lines.push(ringXY(proj, l)); break;
      case 'Point': points.push(toXY(proj, g.coordinates)); break;
      case 'MultiPoint': for (const c of g.coordinates as unknown[]) points.push(toXY(proj, c)); break;
      default: continue;
    }
    const all: XY[] = [...polys.flatMap((pg) => pg[0] ?? []), ...lines.flat(), ...points];
    if (all.length === 0) continue;
    const pf: PFeature = {
      layer, osm: f.properties.osm ?? '', name: f.properties.name, kind: f.properties.kind, operator: f.properties.operator,
      areaM2: f.properties.area_m2, lengthM: f.properties.length_m, polys, lines, points, bbox: bboxOf(all),
    };
    features.push(pf);
    const arr = byLayer.get(layer); if (arr) arr.push(pf); else byLayer.set(layer, [pf]);
    counts[layer] = (counts[layer] ?? 0) + 1;
  }
  const coast = byLayer.get('coastline') ?? [];
  const land = coast.length ? buildLand(coast.flatMap((f) => f.lines), bboxM) : [];
  return { proj, bboxM, features, byLayer, counts, land, hasCoast: coast.length > 0 };
}

/* ── land from the coastline ────────────────────────────────────────── */
/* OSM puts land on the left of a coastline way. The server clips every way
 * to the port rectangle, so an open chain starts and ends on the rectangle
 * edge; closing each chain along the edge (counter-clockwise, the way land
 * lies on the left) gives one land ring per connected piece. Closed chains
 * are islands (counter-clockwise) or lagoons (clockwise); with the evenodd
 * rule both fill correctly. */
const SNAP_M = 2.5;

function mergeChains(lines: XY[][]): XY[][] {
  const chains = lines.filter((l) => l.length >= 2).map((l) => l.slice());
  let merged = true;
  while (merged) {
    merged = false;
    outer:
    for (let i = 0; i < chains.length; i += 1) {
      const ci = chains[i];
      if (Math.hypot(ci[0][0] - ci[ci.length - 1][0], ci[0][1] - ci[ci.length - 1][1]) <= SNAP_M) continue; // closed already
      const end = ci[ci.length - 1];
      for (let j = 0; j < chains.length; j += 1) {
        if (i === j) continue;
        const cj = chains[j];
        if (Math.hypot(cj[0][0] - end[0], cj[0][1] - end[1]) <= SNAP_M) {
          chains[i] = ci.concat(cj.slice(1));
          chains.splice(j, 1);
          merged = true;
          break outer;
        }
      }
    }
  }
  return chains;
}

/** Position of a point along the rectangle perimeter, counter-clockwise from
 *  the south-west corner (south edge, east edge, north edge, west edge). */
export function perimeterParam(x: number, y: number, r: BBox): number {
  const [x0, y0, x1, y1] = r; const w = x1 - x0; const h = y1 - y0;
  const dS = Math.abs(y - y0); const dE = Math.abs(x - x1); const dN = Math.abs(y - y1); const dW = Math.abs(x - x0);
  const m = Math.min(dS, dE, dN, dW);
  const cl = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
  if (m === dS) return cl(x - x0, 0, w);
  if (m === dE) return w + cl(y - y0, 0, h);
  if (m === dN) return w + h + cl(x1 - x, 0, w);
  return 2 * w + h + cl(y1 - y, 0, h);
}
function perimeterPoint(s: number, r: BBox): XY {
  const [x0, y0, x1, y1] = r; const w = x1 - x0; const h = y1 - y0;
  if (s <= w) return [x0 + s, y0];
  if (s <= w + h) return [x1, y0 + (s - w)];
  if (s <= 2 * w + h) return [x1 - (s - w - h), y1];
  return [x0, y1 - (s - 2 * w - h)];
}
/** Rectangle corners met when walking counter-clockwise from `a` to `b` (a to b, wrapping). */
function cornersBetween(a: number, b: number, r: BBox): XY[] {
  const w = r[2] - r[0]; const h = r[3] - r[1]; const per = 2 * (w + h);
  const marks = [0, w, w + h, 2 * w + h];
  const out: XY[] = [];
  let at = a; const target = b > a ? b : b + per;
  for (let lap = 0; lap < 2; lap += 1) {
    for (const m of marks) {
      const mm = m + lap * per;
      if (mm > at + 1e-6 && mm < target - 1e-6) out.push(perimeterPoint(m, r));
    }
  }
  return out.length ? out : [];
}

export function buildLand(lines: XY[][], r: BBox): XY[][] {
  const chains = mergeChains(lines);
  const rings: XY[][] = [];
  const open: Array<{ pts: XY[]; s0: number; s1: number }> = [];
  for (const c of chains) {
    const first = c[0]; const last = c[c.length - 1];
    if (Math.hypot(first[0] - last[0], first[1] - last[1]) <= SNAP_M) {
      rings.push(c.slice(0, -1));
    } else {
      open.push({ pts: c, s0: perimeterParam(first[0], first[1], r), s1: perimeterParam(last[0], last[1], r) });
    }
  }
  const used = new Array<boolean>(open.length).fill(false);
  const per = 2 * ((r[2] - r[0]) + (r[3] - r[1]));
  for (let i = 0; i < open.length; i += 1) {
    if (used[i]) continue;
    const ring: XY[] = [];
    let cur = i;
    for (let guard = 0; guard <= open.length; guard += 1) {
      used[cur] = true;
      ring.push(...open[cur].pts);
      // next chain START met going counter-clockwise from this chain's END
      let best = -1; let bestD = Infinity;
      for (let k = 0; k < open.length; k += 1) {
        if (used[k] && k !== i) continue;
        let d = open[k].s0 - open[cur].s1; if (d < 0) d += per;
        if (d < bestD) { bestD = d; best = k; }
      }
      if (best < 0) break;
      ring.push(...cornersBetween(open[cur].s1, open[best].s0, r));
      if (best === i) break;
      cur = best;
    }
    if (ring.length >= 3) rings.push(ring);
  }
  // no open chain: the rectangle is land (a lake or lagoon rings) or sea (islands only)
  if (open.length === 0 && rings.length > 0) {
    const hasLagoon = rings.some((q) => ringArea(q) < 0);
    const hasIsland = rings.some((q) => ringArea(q) > 0);
    if (hasLagoon && !hasIsland) rings.unshift([[r[0], r[1]], [r[2], r[1]], [r[2], r[3]], [r[0], r[3]]]);
  }
  return rings;
}

/* ── layer registry: the toggle groups ──────────────────────────────── */
export type LayerKey =
  | 'water' | 'land' | 'terminals' | 'quays' | 'berths' | 'cranes' | 'yards' | 'transport' | 'fairways' | 'anchorages';

export interface LayerDef { key: LayerKey; label: string; geo: GeoLayer[]; swatch: string }
/** The ten toggles, in the order the control lists them. `geo` = the OSM layers each switches. */
export const LAYER_DEFS: readonly LayerDef[] = [
  { key: 'water', label: 'Water', geo: ['water', 'coastline'], swatch: 'water' },
  { key: 'land', label: 'Land', geo: ['coastline', 'building'], swatch: 'land' },
  { key: 'terminals', label: 'Terminals', geo: ['terminal'], swatch: 'terminal' },
  { key: 'quays', label: 'Quays and piers', geo: ['quay', 'pier', 'breakwater'], swatch: 'quay' },
  { key: 'berths', label: 'Berths', geo: ['berth'], swatch: 'berth' },
  { key: 'cranes', label: 'Cranes', geo: ['crane'], swatch: 'crane' },
  { key: 'yards', label: 'Yards', geo: ['yard', 'storage'], swatch: 'yard' },
  { key: 'transport', label: 'Rail and roads', geo: ['rail', 'road'], swatch: 'road' },
  { key: 'fairways', label: 'Fairways', geo: ['fairway', 'lock'], swatch: 'fairway' },
  { key: 'anchorages', label: 'Anchorages', geo: ['anchorage'], swatch: 'anchorage' },
];

/** How many OSM objects each toggle controls in this port (0 = nothing mapped). */
export function layerCount(def: LayerDef, counts: Record<string, number>, hasCoast: boolean): number {
  if (def.key === 'land') return hasCoast ? (counts.coastline ?? 0) : (counts.building ?? 0);
  if (def.key === 'water') return (counts.water ?? 0) + (counts.coastline ?? 0);
  return def.geo.reduce((n, g) => n + (counts[g] ?? 0), 0);
}

export type LayerVis = Record<LayerKey, boolean>;
export const DEFAULT_VIS: LayerVis = {
  water: true, land: true, terminals: true, quays: true, berths: true, cranes: true, yards: true, transport: true, fairways: true, anchorages: true,
};
export function toggleLayer(v: LayerVis, k: LayerKey): LayerVis { return { ...v, [k]: !v[k] }; }

/* ── labels and hover targets ───────────────────────────────────────── */
export interface LabelCand { text: string; x: number; y: number; prio: number; layer: GeoLayer; osm: string }
/** Name candidates, highest priority first: terminals by area, anchorages, named piers and water. */
export function labelCandidates(p: Prepared): LabelCand[] {
  const out: LabelCand[] = [];
  for (const f of p.byLayer.get('terminal') ?? []) {
    if (!f.name) continue;
    const ring = f.polys[0]?.[0]; if (!ring) continue;
    const [x, y] = ringCentroid(ring);
    out.push({ text: f.name, x, y, prio: 1000 + Math.sqrt(Math.abs(f.areaM2 ?? ringArea(ring))), layer: 'terminal', osm: f.osm });
  }
  for (const f of p.byLayer.get('anchorage') ?? []) {
    const ring = f.polys[0]?.[0]; if (!ring) continue;
    const [x, y] = ringCentroid(ring);
    out.push({ text: f.name ?? 'Anchorage', x, y, prio: 900, layer: 'anchorage', osm: f.osm });
  }
  for (const f of p.byLayer.get('pier') ?? []) {
    if (!f.name) continue;
    const q: XY = f.lines[0] ? pointAtHalf(f.lines[0]) : f.polys[0] ? ringCentroid(f.polys[0][0]) : f.points[0];
    out.push({ text: f.name, x: q[0], y: q[1], prio: 300, layer: 'pier', osm: f.osm });
  }
  for (const f of p.byLayer.get('water') ?? []) {
    if (!f.name || !f.polys[0]) continue;
    const [x, y] = ringCentroid(f.polys[0][0]);
    out.push({ text: f.name, x, y, prio: 100 + Math.sqrt(f.areaM2 ?? 0) / 10, layer: 'water', osm: f.osm });
  }
  return out.sort((a, b) => b.prio - a.prio || (a.text < b.text ? -1 : 1));
}

/** Terminal under a metre coordinate (smallest polygon wins), or null. */
export function terminalAt(p: Prepared, x: number, y: number): PFeature | null {
  let best: PFeature | null = null; let bestA = Infinity;
  for (const f of p.byLayer.get('terminal') ?? []) {
    if (x < f.bbox[0] || x > f.bbox[2] || y < f.bbox[1] || y > f.bbox[3]) continue;
    const ring = f.polys[0]?.[0];
    if (ring && pointInRing(x, y, ring)) {
      const a = Math.abs(ringArea(ring));
      if (a < bestA) { best = f; bestA = a; }
    }
  }
  return best;
}

/* ── view transform ─────────────────────────────────────────────────── */
export interface View { cx: number; cy: number; scale: number; w: number; h: number }
export const toScreen = (v: View, x: number, y: number): XY => [v.w / 2 + (x - v.cx) * v.scale, v.h / 2 - (y - v.cy) * v.scale];
export const toWorld = (v: View, sx: number, sy: number): XY => [v.cx + (sx - v.w / 2) / v.scale, v.cy - (sy - v.h / 2) / v.scale];

/** Scale (px per metre) that fits the rectangle in w x h with a margin. */
export function fitScale(r: BBox, w: number, h: number, margin = 12): number {
  const sw = (w - 2 * margin) / Math.max(1, r[2] - r[0]);
  const sh = (h - 2 * margin) / Math.max(1, r[3] - r[1]);
  return Math.max(1e-4, Math.min(sw, sh));
}
export function fitView(r: BBox, w: number, h: number): View {
  return { cx: (r[0] + r[2]) / 2, cy: (r[1] + r[3]) / 2, scale: fitScale(r, w, h), w, h };
}
/** Zoom about a screen point, keeping the world point under it fixed. */
export function zoomAt(v: View, sx: number, sy: number, factor: number, min: number, max: number): View {
  const scale = Math.max(min, Math.min(max, v.scale * factor));
  const [wx, wy] = toWorld(v, sx, sy);
  return { ...v, scale, cx: wx - (sx - v.w / 2) / scale, cy: wy + (sy - v.h / 2) / scale };
}
/** Keep the view centre inside the rectangle (plus slack), so a drag cannot lose the port. */
export function clampView(v: View, r: BBox): View {
  const sx = (r[2] - r[0]) * 0.1; const sy = (r[3] - r[1]) * 0.1;
  return { ...v, cx: Math.max(r[0] - sx, Math.min(r[2] + sx, v.cx)), cy: Math.max(r[1] - sy, Math.min(r[3] + sy, v.cy)) };
}
