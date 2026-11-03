// LANE GEOMETRY, client side. Pure functions, no state.
//
// GET /graph/lanes/geometry?scope=sea serves one polyline per unordered pair of places that a sea leg
// joins (smoothed lane waypoints checked against 1:50m land, MODELLED). This module indexes that
// document and answers the three questions the map has:
//   - the legs layer: the segments of every lane pair (the lane, not the chord);
//   - a chain arc between two sea places: the shortest path of lane legs between them, as one or more
//     [lon, lat] parts split at the antimeridian;
//   - which sea legs have no lane geometry (they stay chords, drawn dashed and labelled Approx.).
// A pair id is "a|b" with a < b; the polyline runs a -> b.

import type { GraphData } from "../types";
import type { LonLat } from "../sim/geometry";

export interface LanePairRow {
  id: string;
  a: string;
  b: string;
  nm: number;
  parts: LonLat[][];
}

/** The served body of GET /graph/lanes/geometry?scope=sea (the fields read here). */
export interface LanesGeometryDoc {
  schema: string;
  detail: "full" | "coarse";
  label?: string;
  pairs: LanePairRow[];
  lane_geometry?: { content_sha256: string };
}

export interface LaneIndex {
  detail: "full" | "coarse";
  sha: string;
  pairs: Map<string, LanePairRow>;
  adj: Map<string, Array<[string, number]>>;
  routes: Map<string, LaneRoute | null>;
}

export interface LaneRoute {
  path: string[];
  nm: number;
  /** [lon, lat] parts from the first place to the last, split at the antimeridian */
  parts: LonLat[][];
}

/** Both detail levels of the lane polylines; the legs layer picks one by zoom. */
export interface LaneSet {
  coarse: LaneIndex | null;
  full: LaneIndex | null;
}

/** Zoom from which the full polylines are drawn (below it the simplified ones). */
export const LANE_FULL_ZOOM = 4;

export const pairKey = (a: string, b: string): string => (a < b ? `${a}|${b}` : `${b}|${a}`);

export function buildLaneIndex(doc: LanesGeometryDoc): LaneIndex {
  const pairs = new Map<string, LanePairRow>();
  const adj = new Map<string, Array<[string, number]>>();
  for (const p of doc.pairs ?? []) {
    pairs.set(p.id, p);
    (adj.get(p.a) ?? adj.set(p.a, []).get(p.a)!).push([p.b, p.nm]);
    (adj.get(p.b) ?? adj.set(p.b, []).get(p.b)!).push([p.a, p.nm]);
  }
  for (const l of adj.values()) l.sort((x, y) => (x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : 0));
  return { detail: doc.detail, sha: doc.lane_geometry?.content_sha256 ?? "", pairs, adj, routes: new Map() };
}

/** The index to draw at this zoom: full from LANE_FULL_ZOOM when it has arrived, else the coarse one. */
export function laneIndexAt(set: LaneSet | null | undefined, zoom: number): LaneIndex | null {
  if (!set) return null;
  if (zoom >= LANE_FULL_ZOOM && set.full) return set.full;
  return set.coarse ?? set.full;
}

const reversedParts = (parts: LonLat[][]): LonLat[][] => parts.map((p) => p.slice().reverse()).reverse();

/** Parts of one pair, oriented from `from` to `to` (null when the pair has no lane). */
export function pairParts(idx: LaneIndex, from: string, to: string): LonLat[][] | null {
  const row = idx.pairs.get(pairKey(from, to));
  if (!row) return null;
  return row.a === from ? row.parts : reversedParts(row.parts);
}

const near = (p: LonLat, q: LonLat): boolean => Math.abs(p[0] - q[0]) < 1e-6 && Math.abs(p[1] - q[1]) < 1e-6;

/** Join consecutive legs' parts into continuous parts (a join across the antimeridian stays a break). */
export function joinParts(legs: LonLat[][][]): LonLat[][] {
  const out: LonLat[][] = [];
  for (const parts of legs) {
    for (const part of parts) {
      const last = out[out.length - 1];
      if (last && near(last[last.length - 1], part[0])) {
        for (let i = 1; i < part.length; i++) last.push(part[i]);
      } else out.push(part.slice());
    }
  }
  return out;
}

/** Shortest path of lane legs by lane nautical miles between two places; null when there is none. */
export function laneRoute(idx: LaneIndex, a: string, b: string): LaneRoute | null {
  if (a === b) return null;
  const key = `${a}>${b}`;
  const memo = idx.routes.get(key);
  if (memo !== undefined) return memo;
  let result: LaneRoute | null = null;
  if (idx.adj.has(a) && idx.adj.has(b)) {
    const best = new Map<string, number>([[a, 0]]);
    const prev = new Map<string, string>();
    const done = new Set<string>();
    // small graph (about 770 places): a linear-scan Dijkstra keeps the tie-break deterministic (id order)
    const open = new Set<string>([a]);
    while (open.size) {
      let u = "";
      let ud = Infinity;
      for (const n of open) {
        const d = best.get(n)!;
        if (d < ud || (d === ud && n < u)) { u = n; ud = d; }
      }
      open.delete(u);
      if (done.has(u)) continue;
      done.add(u);
      if (u === b) break;
      for (const [v, w] of idx.adj.get(u) ?? []) {
        if (done.has(v)) continue;
        const nd = ud + w;
        if (nd < (best.get(v) ?? Infinity) - 1e-9) {
          best.set(v, nd);
          prev.set(v, u);
          open.add(v);
        }
      }
    }
    if (done.has(b)) {
      const path = [b];
      while (path[path.length - 1] !== a) path.push(prev.get(path[path.length - 1])!);
      path.reverse();
      const legs: LonLat[][][] = [];
      for (let i = 0; i + 1 < path.length; i++) legs.push(pairParts(idx, path[i], path[i + 1]) ?? []);
      result = { path, nm: best.get(b)!, parts: joinParts(legs) };
    }
  }
  idx.routes.set(key, result);
  return result;
}

/** One straight segment of a leg, as the legs layer draws it. */
export interface LaneSegment {
  source: LonLat;
  target: LonLat;
  pair: string;
}

const segCache = new WeakMap<LaneIndex, LaneSegment[]>();
/** Every segment of every lane pair of the index (cached per index). */
export function laneSegments(idx: LaneIndex): LaneSegment[] {
  const hit = segCache.get(idx);
  if (hit) return hit;
  const out: LaneSegment[] = [];
  for (const row of idx.pairs.values()) {
    for (const part of row.parts) {
      for (let i = 1; i < part.length; i++) out.push({ source: part[i - 1], target: part[i], pair: row.id });
    }
  }
  segCache.set(idx, out);
  return out;
}

/** Sea legs of the graph (one per direction) with no lane pair in the index: they stay chords and are
 *  labelled Approx. (no geometry in the sea-routes sidecar, or a polyline that meets land). */
export function approxSeaLegs(graph: GraphData, idx: LaneIndex): number {
  let n = 0;
  for (const e of graph.edges) {
    if (e.mode === "sea" && !idx.pairs.has(pairKey(e.from, e.to))) n++;
  }
  return n;
}
