// MAP LEVELS OF DETAIL (pure). One place for every zoom decision the
// place layer makes, so thresholds are explicit:
//
//   WORLD   z < 2.8   glowing points for the top capacity bands only,
//                     fleet as density heat
//   OCEAN   z < 4.6   every CORE place, faint legs
//   REGION  z < 7     SHELL places join, legs, labels for top places,
//                     fleet dots
//   PORT    z < 11    full labels, fleet as small oriented hulls and
//                     aircraft glyphs with trails
//   BERTH   z >= 11   hull-level detail and labels (to z ~18)
//
// Sizes and opacities interpolate smoothly between those edges (no
// popping): a place fades in over APPEAR_FADE_ZOOM before its appear
// zoom, and its radius grows by one smooth ramp.

import type { GraphNode, NodeKind } from "../types";

export type ZoomTier = "world" | "ocean" | "region" | "port" | "berth";

export const TIER_ORDER: readonly ZoomTier[] = ["world", "ocean", "region", "port", "berth"];

/** Lower zoom edge of each tier. */
export const TIER_FROM: Readonly<Record<ZoomTier, number>> = {
  world: 0,
  ocean: 2.8,
  region: 4.6,
  port: 7,
  berth: 11,
};

/** zoomed all the way out the world still fits a small panel */
export const MAP_MIN_ZOOM = 0.6;
/** CARTO vector tiles over-zoom cleanly; imagery tops out lower. */
export const MAP_MAX_ZOOM = 18;
export const MAP_MAX_ZOOM_SAT = 18;

/** Opening frame: the whole world at zoom 1.35 when the
 *  container is wide enough, zoomed out just far enough to keep the world
 *  in a narrower panel (never below the map's minZoom). */
export function fitWorldZoom(containerWidthPx: number): number {
  return Math.max(MAP_MIN_ZOOM, Math.min(1.35, Math.log2(Math.max(200, containerWidthPx) / 512) + 0.3));
}

/** Zoom from which the tiny kind glyph is drawn inside the core. */
export const KIND_GLYPH_FROM = 9.5;
/** Clusters collapse overlapping places below this zoom. */
export const CLUSTER_BELOW = 6.2;
/** Cluster grid cell, in screen pixels. */
export const CLUSTER_CELL_PX = 34;
/** A place fades in over this many zoom levels before it appears. */
export const APPEAR_FADE_ZOOM = 0.6;

export function zoomTier(zoom: number): ZoomTier {
  if (zoom < TIER_FROM.ocean) return "world";
  if (zoom < TIER_FROM.region) return "ocean";
  if (zoom < TIER_FROM.port) return "region";
  if (zoom < TIER_FROM.berth) return "port";
  return "berth";
}

export function tierAtLeast(tier: ZoomTier, min: ZoomTier): boolean {
  return TIER_ORDER.indexOf(tier) >= TIER_ORDER.indexOf(min);
}

export const TIER_LABEL: Readonly<Record<ZoomTier, string>> = {
  world: "World",
  ocean: "Ocean",
  region: "Region",
  port: "Port",
  berth: "Berth",
};

/** Zoom quantised to a step (default 0.25) so layer stacks rebuild a
 *  bounded number of times per zoom level while sizes, which deck
 *  scales per frame, stay continuous. */
export function quantizeZoom(zoom: number, step = 0.25): number {
  return Math.round(zoom / step) * step;
}

export function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

export function smoothstep(edge0: number, edge1: number, x: number): number {
  if (edge1 === edge0) return x < edge0 ? 0 : 1;
  const t = clamp01((x - edge0) / (edge1 - edge0));
  return t * t * (3 - 2 * t);
}

/* ---- capacity bands ---------------------------------------------- */

export type CapacityBand = 0 | 1 | 2 | 3 | 4;

/** Cumulative share of a kind's places (largest first) per band edge:
 *  top 5% = band 4, next 15% = band 3, next 30% = band 2, next 30%
 *  = band 1, the rest band 0. Bands are ranked WITHIN a kind because
 *  capacity units differ (TEU, tonnes, trucks, vessels per day). */
const BAND_CUT: ReadonlyArray<[number, CapacityBand]> = [
  [0.05, 4],
  [0.2, 3],
  [0.5, 2],
  [0.8, 1],
];

/** Deterministic capacity bands: capacity descending, id ascending on
 *  ties; a kind with few places still gets its top band (at least
 *  one place). Chokepoints are a 12-place class: all band 4. */
export function capacityBands(
  nodes: ReadonlyArray<Pick<GraphNode, "id" | "kind" | "capacity">>,
): Map<string, CapacityBand> {
  const byKind = new Map<NodeKind, Array<Pick<GraphNode, "id" | "kind" | "capacity">>>();
  for (const n of nodes) {
    const arr = byKind.get(n.kind);
    if (arr) arr.push(n);
    else byKind.set(n.kind, [n]);
  }
  const out = new Map<string, CapacityBand>();
  for (const [kind, arr] of byKind) {
    const sorted = [...arr].sort(
      (a, b) => b.capacity - a.capacity || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
    );
    const total = sorted.length;
    sorted.forEach((n, i) => {
      if (kind === "chokepoint") {
        out.set(n.id, 4);
        return;
      }
      const rank = (i + 1) / total;
      let band: CapacityBand = 0;
      for (const [cut, b] of BAND_CUT) {
        if (rank <= cut) {
          band = b;
          break;
        }
      }
      if (i === 0) band = 4;
      out.set(n.id, band);
    });
  }
  return out;
}

/* ---- appearance ---------------------------------------------------- */

/** Zoom at which a place is fully visible. CORE places arrive by
 *  band (top band always, the rest through the ocean tier); SHELL
 *  places arrive in the region and port tiers, larger first.
 *  Chokepoints are the narrative and are always on. */
const APPEAR_CORE: Readonly<Record<CapacityBand, number>> = { 4: 0, 3: 1.6, 2: 2.9, 1: 3.4, 0: 4.0 };
const APPEAR_SHELL: Readonly<Record<CapacityBand, number>> = { 4: 4.8, 3: 5.4, 2: 6.0, 1: 6.7, 0: 7.4 };

export function appearZoom(
  node: Pick<GraphNode, "kind" | "tier">,
  band: CapacityBand,
): number {
  if (node.kind === "chokepoint") return 0;
  return (node.tier === "shell" ? APPEAR_SHELL : APPEAR_CORE)[band];
}

/** 0..1 opacity of a place at a zoom given its appear zoom. */
export function appearAlpha(zoom: number, appear: number): number {
  if (appear <= 0) return 1;
  return smoothstep(appear - APPEAR_FADE_ZOOM, appear, zoom);
}

/* ---- marker sizing ------------------------------------------------- */

/** Core radius (px) at the world zoom per capacity band. */
export const BAND_RADIUS_PX: Readonly<Record<CapacityBand, number>> = {
  0: 1.5,
  1: 1.9,
  2: 2.5,
  3: 3.2,
  4: 4.0,
};

/** Smooth growth factor: 1 at world zoom, 1.8 from the berth tier. */
export function zoomGrowth(zoom: number): number {
  return 1 + 0.8 * smoothstep(1.5, 12, zoom);
}

/** Core radius in px for a place: band radius x zoom growth, SHELL
 *  slightly smaller. Always within [MIN, MAX] so a marker never
 *  becomes a blob nor vanishes. */
export const MARKER_MIN_PX = 1.2;
export const MARKER_MAX_PX = 8.5;
export function markerRadiusPx(band: CapacityBand, tier: GraphNode["tier"], zoom: number): number {
  const base = BAND_RADIUS_PX[band] * (tier === "shell" ? 0.84 : 1) * zoomGrowth(zoom);
  return Math.min(MARKER_MAX_PX, Math.max(MARKER_MIN_PX, base));
}

/** Halo radius (px): a soft falloff around the core. */
export function haloRadiusPx(corePx: number): number {
  return corePx * 3.4 + 2;
}

/** CORE places read brighter than SHELL places. */
export function tierBrightness(tier: GraphNode["tier"]): number {
  return tier === "shell" ? 0.62 : 1;
}

/* ---- legs ---------------------------------------------------------- */

/** Base alpha (0..255) of a leg by zoom: faint at world, a little
 *  firmer as the user zooms in; scaled by the leg's flow share (sqrt so
 *  thin legs stay visible but heavy legs read heavier). Legs are context
 *  for the places, never louder than them. */
export function legAlpha(zoom: number, share: number): number {
  const base = 12 + 34 * smoothstep(1.2, 8, zoom);
  const flow = 0.5 + 0.8 * Math.sqrt(clamp01(share));
  return Math.round(Math.min(96, base * flow));
}

/** Leg width in px by flow share (hairlines; no growth with zoom). */
export function legWidthPx(zoom: number, share: number): number {
  void zoom;
  return 0.4 + 0.9 * clamp01(share);
}
