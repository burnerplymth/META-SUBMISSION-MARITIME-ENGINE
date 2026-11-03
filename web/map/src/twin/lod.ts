// TWIN level-of-detail (pure). One function maps the camera zoom to
// what the fleet renders, so thousands of entities stay legible and
// cheap. The edges are the map tier edges (places/tiers.ts TIER_FROM):
//   z < 2.4        WORLD: density heat under 1-2 px glowing dots
//   2.4 <= z < 7   OCEAN / REGION: glowing dots
//   7 <= z < 11    PORT: small oriented hulls / aircraft glyphs + trails
//   z >= 11        BERTH: glyphs + trails + labels (to z ~18)

import { TIER_FROM, smoothstep } from "../places/tiers";

export type TwinLodLevel = "heat" | "dots" | "glyphs" | "labels";

export interface TwinLod {
  level: TwinLodLevel;
  heat: boolean;
  dots: boolean;
  glyphs: boolean;
  trails: boolean;
  labels: boolean;
}

export const LOD_HEAT_BELOW = 2.4;
export const LOD_GLYPH_FROM = TIER_FROM.port;
export const LOD_LABEL_FROM = TIER_FROM.berth;

export function lodForZoom(zoom: number): TwinLod {
  if (zoom < LOD_HEAT_BELOW) {
    return { level: "heat", heat: true, dots: true, glyphs: false, trails: false, labels: false };
  }
  if (zoom < LOD_GLYPH_FROM) {
    return { level: "dots", heat: false, dots: true, glyphs: false, trails: false, labels: false };
  }
  if (zoom < LOD_LABEL_FROM) {
    return { level: "glyphs", heat: false, dots: false, glyphs: true, trails: true, labels: false };
  }
  return { level: "labels", heat: false, dots: false, glyphs: true, trails: true, labels: true };
}

/** Dot core radius in px by zoom: 1-2 px at world zoom, growing gently
 *  (kept small: thousands overlap). The glow halo is drawn around it. */
export function dotRadiusPx(zoom: number): number {
  return zoom < LOD_HEAT_BELOW ? 0.9 : 0.9 + Math.min(1.3, (zoom - LOD_HEAT_BELOW) * 0.3);
}

/** Glow halo diameter (px) around a dot. */
export function dotHaloPx(zoom: number): number {
  return dotRadiusPx(zoom) * 6.5 + 2;
}

/** Glyph edge size in px: small hulls at the port tier, growing
 *  smoothly to hull-level detail at the berth tier and beyond.
 *  mode 0 = sea hull, 1 = aircraft. */
export function glyphSizePx(zoom: number, mode: 0 | 1): number {
  const t = smoothstep(LOD_GLYPH_FROM, 17, zoom);
  const base = mode === 1 ? 9 : 8;
  return base + t * (mode === 1 ? 15 : 17);
}

/** Trail window in HOURS of travel by mode (bounded: see trails.ts
 *  MAX_TRAIL_KM) and the vertex budget per trail. */
export const TRAIL_VERTS = 8;
export const TRAIL_HOURS = { sea: 14, air: 1.0 } as const;
export const MAX_TRAIL_KM = { sea: 900, air: 1500 } as const;

/** Heat cell size (degrees) by zoom. */
export function heatCellDeg(zoom: number): number {
  return zoom < 1.2 ? 8 : 5;
}

/** Labels never exceed this many entities on screen. */
export const LABEL_CAP = 120;
