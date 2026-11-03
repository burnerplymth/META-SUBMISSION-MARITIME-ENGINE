// TWIN deck.gl layers. The fleet is drawn from the engine's reusable
// typed-array DrawSets as BINARY attributes: no per-entity objects are
// created per frame. Sim fleet = hulls / aircraft glyphs (or dots at low
// zoom) tinted by speed/state, with fading trails; the LIVE fleet is a
// separate family (white diamonds + a live-green ring and glow, fading
// with observation age) so the two can never be mistaken for one
// another. Visual language: dots are small GLOWING points
// (additive halo sprite + core), trails are luminous lines, hulls and
// aircraft appear from the port tier.

import { IconLayer, LineLayer, PathLayer, PolygonLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import type { Layer } from "@deck.gl/core";
import type { ThemeName } from "../types";
import { isDarkTheme, tokens } from "../theme/tokens";
import { hexToRgba } from "../components/layers";
import { ADDITIVE_BLEND, GLOW_ATLAS_URL, GLOW_MAPPING, NORMAL_BLEND } from "../places/color";
import { glyphSizePx, dotRadiusPx, dotHaloPx, type TwinLod } from "./lod";
import type { DrawSet } from "./fleet";
import type { TrailBuffer } from "./trails";
import type { HeatBuffer } from "./density";
import { liveColors } from "./palette";
import type { RoutePreview } from "./select";

/* ---- icon atlas (one SVG, four 64px cells: hull, aircraft, diamond, ring) */

const ATLAS_SVG =
  `<svg xmlns='http://www.w3.org/2000/svg' width='256' height='64' viewBox='0 0 256 64'>` +
  `<polygon points='32,2 46,24 44,60 20,60 18,24' fill='white'/>` +
  `<path d='M96 3 L100 22 L124 36 L124 42 L100 36 L99 52 L108 58 L108 62 L96 59 L84 62 L84 58 L93 52 L92 36 L68 42 L68 36 L92 22 Z' fill='white'/>` +
  `<polygon points='160,4 186,32 160,60 134,32' fill='white'/>` +
  `<circle cx='224' cy='32' r='25' fill='none' stroke='white' stroke-width='6'/>` +
  `</svg>`;
export const ATLAS_URL = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(ATLAS_SVG)}`;
export const ATLAS_MAPPING = {
  hull: { x: 0, y: 0, width: 64, height: 64, anchorY: 32, mask: true },
  plane: { x: 64, y: 0, width: 64, height: 64, anchorY: 32, mask: true },
  diamond: { x: 128, y: 0, width: 64, height: 64, anchorY: 32, mask: true },
  ring: { x: 192, y: 0, width: 64, height: 64, anchorY: 32, mask: true },
} as const;

export interface TwinPickInfo {
  index: number;
  x: number;
  y: number;
}
export type TwinPickHandler = (src: "sim" | "live", set: DrawSet, info: TwinPickInfo | null) => void;

export interface TwinLayerInput {
  theme: ThemeName;
  zoom: number;
  lod: TwinLod;
  frameNo: number;
  simSea: DrawSet | null;
  simAir: DrawSet | null;
  liveSea: DrawSet | null;
  liveAir: DrawSet | null;
  trails: TrailBuffer | null;
  heat: HeatBuffer | null;
  night: [number, number][] | null;
  route: RoutePreview | null;
  selectedPos: [number, number] | null;
  labels: { position: [number, number]; text: string }[] | null;
  onHover?: TwinPickHandler;
  onClick?: TwinPickHandler;
}

type Bin = { length: number; attributes: Record<string, { value: ArrayLike<number>; size: number }> };

/** Binary data for a DrawSet. A fresh wrapper each frame (cheap: one
 *  small object) so deck re-uploads the typed arrays; the arrays
 *  themselves are the engine's reusable buffers. */
function bin(set: DrawSet, withAngle: boolean, colorAttr: "getColor" | "getFillColor" = "getColor"): Bin {
  const attributes: Bin["attributes"] = {
    getPosition: { value: set.pos, size: 2 },
    [colorAttr]: { value: set.color, size: 4 },
  };
  if (withAngle) attributes.getAngle = { value: set.angle, size: 1 };
  return { length: set.n, attributes };
}

function pickHandlers(src: "sim" | "live", set: DrawSet, input: TwinLayerInput) {
  return {
    onHover: (info: { index: number; x: number; y: number }) =>
      input.onHover?.(src, set, info.index >= 0 ? { index: info.index, x: info.x, y: info.y } : null),
    onClick: (info: { index: number; x: number; y: number }) =>
      input.onClick?.(src, set, info.index >= 0 ? { index: info.index, x: info.x, y: info.y } : null),
  };
}

/** Split a vertex list at antimeridian jumps into drawable sub-paths. */
export function splitWrap(pts: ReadonlyArray<[number, number]>): [number, number][][] {
  const out: [number, number][][] = [];
  let cur: [number, number][] = [];
  for (let i = 0; i < pts.length; i++) {
    if (i > 0 && Math.abs(pts[i][0] - pts[i - 1][0]) > 180) {
      if (cur.length > 1) out.push(cur);
      cur = [];
    }
    cur.push(pts[i]);
  }
  if (cur.length > 1) out.push(cur);
  return out;
}

const trailCache = new WeakMap<TrailBuffer, { version: number; cap: number; data: Bin }>();
/** Stable data wrapper per trail buffer version: deck re-uploads only
 *  when the trails were actually recomputed. */
function trailData(tr: TrailBuffer): Bin {
  const hit = trailCache.get(tr);
  if (hit && hit.version === tr.version && hit.cap === tr.cap) return hit.data;
  const data: Bin = {
    length: tr.n,
    attributes: {
      getSourcePosition: { value: tr.src, size: 2 },
      getTargetPosition: { value: tr.dst, size: 2 },
      getColor: { value: tr.color, size: 4 },
    },
  };
  trailCache.set(tr, { version: tr.version, cap: tr.cap, data });
  return data;
}

export function buildTwinLayers(input: TwinLayerInput): Layer[] {
  const layers: Layer[] = [];
  const t = tokens[input.theme];
  const { lod, zoom } = input;
  /* glow blends additively on dark grounds, normally on light ones */
  const blend = (isDarkTheme(input.theme) ? ADDITIVE_BLEND : NORMAL_BLEND) as never;

  /* day/night shading (optional) */
  if (input.night) {
    layers.push(
      new PolygonLayer({
        id: "twin-night",
        data: [{ polygon: input.night }],
        getPolygon: (d: { polygon: [number, number][] }) => d.polygon,
        getFillColor: hexToRgba(t.ocean, input.theme === "light" ? 70 : 120),
        stroked: false,
        pickable: false,
        updateTriggers: { getFillColor: input.theme },
      }),
    );
  }

  /* density heat (very low zoom) */
  if (lod.heat && input.heat && input.heat.n > 0) {
    const h = input.heat;
    layers.push(
      new IconLayer({
        id: "twin-heat",
        data: {
          length: h.n,
          attributes: {
            getPosition: { value: h.pos, size: 2 },
            getColor: { value: h.color, size: 4 },
            getSize: { value: h.radius, size: 1 },
          },
        } as never,
        iconAtlas: GLOW_ATLAS_URL,
        iconMapping: GLOW_MAPPING,
        getIcon: "glow" as never,
        sizeUnits: "pixels",
        sizeScale: 3.6,
        pickable: false,
        parameters: blend,
      }),
    );
  }

  /* trails (region zoom and deeper) */
  if (lod.trails && input.trails && input.trails.n > 0) {
    const tr = input.trails;
    layers.push(
      new LineLayer({
        id: "twin-trails",
        data: trailData(tr) as never,
        getWidth: 1.2,
        widthUnits: "pixels",
        pickable: false,
        parameters: blend,
      }),
    );
  }

  /* selected route preview: travelled (dim) then remaining (bright) */
  if (input.route) {
    const done = splitWrap(input.route.done);
    const ahead = splitWrap(input.route.ahead);
    const acc = hexToRgba(t.accent, 255);
    if (done.length) {
      layers.push(
        new PathLayer({
          id: "twin-route-done",
          data: done,
          getPath: (d: [number, number][]) => d,
          getColor: hexToRgba(t.accent, 110),
          getWidth: 2,
          widthUnits: "pixels",
          pickable: false,
        }),
      );
    }
    if (ahead.length) {
      layers.push(
        new PathLayer({
          id: "twin-route-ahead",
          data: ahead,
          getPath: (d: [number, number][]) => d,
          getColor: acc,
          getWidth: 3,
          widthUnits: "pixels",
          pickable: false,
        }),
      );
    }
  }

  /* SIM fleet: glyphs at region zoom and deeper, dots otherwise */
  const simSets: Array<[DrawSet | null, 0 | 1]> = [
    [input.simSea, 0],
    [input.simAir, 1],
  ];
  for (const [set, mode] of simSets) {
    if (!set || set.n === 0) continue;
    const base = mode === 0 ? "twin-sea" : "twin-air";
    if (lod.glyphs) {
      layers.push(
        new IconLayer({
          id: `${base}-glow`,
          data: bin(set, false) as never,
          iconAtlas: GLOW_ATLAS_URL,
          iconMapping: GLOW_MAPPING,
          getIcon: "glow" as never,
          getSize: glyphSizePx(zoom, mode) * 2.4,
          sizeUnits: "pixels",
          opacity: 0.2,
          pickable: false,
          parameters: blend,
        }),
        new IconLayer({
          id: `${base}-glyph`,
          data: bin(set, true) as never,
          iconAtlas: ATLAS_URL,
          iconMapping: ATLAS_MAPPING,
          getIcon: (mode === 0 ? "hull" : "plane") as never,
          getSize: glyphSizePx(zoom, mode),
          sizeUnits: "pixels",
          pickable: true,
          ...pickHandlers("sim", set, input),
        }),
      );
    } else {
      layers.push(
        new IconLayer({
          id: `${base}-halo`,
          data: bin(set, false) as never,
          iconAtlas: GLOW_ATLAS_URL,
          iconMapping: GLOW_MAPPING,
          getIcon: "glow" as never,
          getSize: dotHaloPx(zoom),
          sizeUnits: "pixels",
          opacity: 0.26,
          pickable: false,
          parameters: blend,
        }),
        new ScatterplotLayer({
          id: `${base}-dot`,
          data: bin(set, false, "getFillColor") as never,
          getRadius: dotRadiusPx(zoom),
          radiusUnits: "pixels",
          pickable: true,
          ...pickHandlers("sim", set, input),
        }),
      );
    }
  }

  /* selected entity ring */
  if (input.selectedPos) {
    layers.push(
      new ScatterplotLayer({
        id: "twin-selected",
        data: [{ p: input.selectedPos }],
        getPosition: (d: { p: [number, number] }) => d.p,
        getRadius: 13,
        radiusUnits: "pixels",
        getFillColor: hexToRgba(t.accent, 40),
        getLineColor: hexToRgba(t.accent, 255),
        lineWidthUnits: "pixels",
        getLineWidth: 2,
        stroked: true,
        filled: true,
        pickable: false,
      }),
    );
  }

  /* SHIP-band labels */
  if (lod.labels && input.labels && input.labels.length) {
    layers.push(
      new TextLayer({
        id: "twin-labels",
        data: input.labels,
        getPosition: (d: { position: [number, number] }) => d.position,
        getText: (d: { text: string }) => d.text,
        getSize: 9,
        sizeUnits: "pixels",
        getColor: hexToRgba(t.text, 235),
        getPixelOffset: [0, -14],
        fontFamily: "'IBM Plex Mono', ui-monospace, monospace",
        pickable: false,
        updateTriggers: { getColor: input.theme },
      }),
    );
  }

  /* LIVE fleet: its own family. Diamonds (vessel) and ringed diamonds
   * (aircraft), white core with age fade; never tinted like sim. */
  const lc = liveColors(input.theme);
  const liveSets: Array<[DrawSet | null, 0 | 1]> = [
    [input.liveSea, 0],
    [input.liveAir, 1],
  ];
  for (const [set, mode] of liveSets) {
    if (!set || set.n === 0) continue;
    const glyphs = lod.glyphs;
    const size = glyphs ? Math.max(7, glyphSizePx(zoom, mode) * 0.62) : 5;
    layers.push(
      new IconLayer({
        id: `twin-live-glow-${mode === 0 ? "sea" : "air"}`,
        data: { length: set.n, attributes: { getPosition: { value: set.pos, size: 2 } } } as never,
        iconAtlas: GLOW_ATLAS_URL,
        iconMapping: GLOW_MAPPING,
        getIcon: "glow" as never,
        getSize: size * 2.4,
        getColor: [lc.ring[0], lc.ring[1], lc.ring[2], glyphs ? 110 : 70],
        sizeUnits: "pixels",
        pickable: false,
        parameters: blend,
      }));
    if (glyphs) layers.push(
      new IconLayer({
        id: `twin-live-ring-${mode === 0 ? "sea" : "air"}`,
        data: { length: set.n, attributes: { getPosition: { value: set.pos, size: 2 } } } as never,
        iconAtlas: ATLAS_URL,
        iconMapping: ATLAS_MAPPING,
        getIcon: "ring" as never,
        getSize: size + 5,
        getColor: [lc.ring[0], lc.ring[1], lc.ring[2], mode === 1 ? 235 : 150],
        sizeUnits: "pixels",
        pickable: false,
      }));
    layers.push(
      new IconLayer({
        id: `${mode === 0 ? "twin-live-sea" : "twin-live-air"}-${glyphs ? "glyph" : "dot"}`,
        data: bin(set, mode === 1) as never,
        iconAtlas: ATLAS_URL,
        iconMapping: ATLAS_MAPPING,
        getIcon: (mode === 1 && glyphs ? "plane" : "diamond") as never,
        getSize: size,
        sizeUnits: "pixels",
        pickable: true,
        ...pickHandlers("live", set, input),
      }),
    );
  }

  return layers;
}
