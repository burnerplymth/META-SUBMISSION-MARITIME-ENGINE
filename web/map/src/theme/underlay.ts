import type { Map as MlMap } from "maplibre-gl";
import landGeoJson from "../data/land-110m.geo.json";
import { tokens } from "./tokens";
import type { BasemapMode } from "../types";

/**
 * Built-in coastline UNDERLAY.
 *
 * A lightweight Natural Earth 110m land polygon set is bundled with the
 * app (src/data/land-110m.geo.json — public domain; provenance in
 * src/data/README-land-underlay.md) and drawn as a GeoJSON fill + outline
 * at the BOTTOM of the MapLibre layer stack, beneath the live basemap
 * tiles. Purpose: a machine whose tile CDNs are blocked must never show
 * lanes/nodes on an empty field.
 *
 * It is a FALLBACK, never a blend and never the basemap:
 *  - dark/light: opaque CARTO tiles paint over it when they load; the
 *    component hides it once the current style has loaded and the
 *    basemap status is "ok" (see SupermAPMap's visibility rule), so no
 *    double coastline can appear over real tiles.
 *  - SAT: hidden while satellite imagery reports "ok"; shown when imagery is
 *    unreachable so the map still has geography.
 * Visibility is driven BY the basemap-status signal — the underlay never
 * feeds back into it. The BASEMAP UNREACHABLE chip keeps reporting TILE
 * status exactly as before, underlay or not.
 */

export const UNDERLAY_SOURCE_ID = "supermap-coastline-underlay";
/** geojson-vt line simplification tolerance. The default (0.375)
 *  simplifies the big Afro-Eurasian ring at world zoom into
 *  self-intersections that tessellate into black wedges; the 110 m
 *  data is already coarse, so it is drawn unsimplified. */
export const UNDERLAY_TILE_TOLERANCE = 0;
export const UNDERLAY_BG_LAYER_ID = "supermap-underlay-bg";
export const UNDERLAY_FILL_LAYER_ID = "supermap-underlay-land-fill";
export const UNDERLAY_OUTLINE_LAYER_ID = "supermap-underlay-land-outline";
export const UNDERLAY_LAYER_IDS = [
  UNDERLAY_FILL_LAYER_ID,
  UNDERLAY_OUTLINE_LAYER_ID,
] as const;

/** The Natural Earth land rings that cross the antimeridian (Russia's
 *  Chukotka, Fiji, Wrangel) arrive already cut at +/-180, so consecutive
 *  vertices jump by ~360 degrees of longitude where the ring crosses the
 *  meridian. A fill draws each jump as a band across the whole map and
 *  the tessellator turns the ring into wedges. The ring is split at its
 *  jumps into chains; each chain lies on one side and is closed along
 *  the +/-180 meridian (end -> meridian -> start), giving one simple
 *  polygon per side. Polar rings (Antarctica: a 358 degree closing edge
 *  at -84.6, and Web Mercator cannot draw the pole) are dropped: no place
 *  of the network lies south of -60. Exterior rings come out
 *  counter-clockwise and holes clockwise (RFC 7946; the TopoJSON source
 *  winds the other way). Pure; does not mutate its input. */
export function unwrapAntimeridian(fc: GeoJSON.FeatureCollection): GeoJSON.FeatureCollection {
  const signedArea = (r: number[][]): number => {
    let a = 0;
    for (let i = 0; i < r.length - 1; i++) a += r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1];
    return a / 2;
  };
  const MIN_AREA = 1e-6;
  /** a ring split at its antimeridian jumps: one closed simple ring per chain */
  const splitRing = (ring: number[][]): number[][][] => {
    const pts = ring.length > 1 && ring[0][0] === ring[ring.length - 1][0] && ring[0][1] === ring[ring.length - 1][1]
      ? ring.slice(0, -1)
      : ring;
    const n = pts.length;
    const jumpAt: number[] = []; // i such that pts[i-1] -> pts[i] is a jump (cyclic)
    for (let i = 0; i < n; i++) {
      if (Math.abs(pts[i][0] - pts[(i + n - 1) % n][0]) > 180) jumpAt.push(i);
    }
    if (jumpAt.length === 0) return [ring];
    const chains: number[][][] = [];
    for (let k = 0; k < jumpAt.length; k++) {
      const from = jumpAt[k];
      const to = jumpAt[(k + 1) % jumpAt.length];
      const chain: number[][] = [];
      for (let i = from; ; i = (i + 1) % n) {
        chain.push(pts[i]);
        if ((i + 1) % n === to) break;
        if (chain.length > n) break;
      }
      chains.push(chain);
    }
    const out: number[][][] = [];
    for (const chain of chains) {
      const mean = chain.reduce((a, p) => a + p[0], 0) / chain.length;
      const side = mean >= 0 ? 180 : -180;
      const first = chain[0];
      const last = chain[chain.length - 1];
      const closed: number[][] = [...chain];
      if (last[0] !== side) closed.push([side, last[1]]);
      if (first[0] !== side) closed.push([side, first[1]]);
      closed.push(first);
      if (Math.abs(signedArea(closed)) >= MIN_AREA) out.push(closed);
    }
    return out;
  };
  const rewind = (ring: number[][], exterior: boolean): number[][] =>
    (signedArea(ring) > 0) === exterior ? ring : [...ring].reverse();
  const fixPolygon = (poly: number[][][]): number[][][][] => {
    const [outer, ...holes] = poly;
    if (outer.every((p) => p[1] < -60)) return [];
    const pieces = splitRing(outer);
    /* the holes (e.g. the Caspian) belong to the largest piece */
    let big = 0;
    pieces.forEach((r, i) => {
      if (Math.abs(signedArea(r)) > Math.abs(signedArea(pieces[big]))) big = i;
    });
    return pieces.map((ring, i) =>
      [rewind(ring, true), ...(i === big ? holes.map((h) => rewind(h, false)) : [])],
    );
  };
  return {
    ...fc,
    features: fc.features.map((f) => {
      const g = f.geometry;
      if (g.type === "Polygon") {
        const polys = fixPolygon(g.coordinates);
        return { ...f, geometry: { type: "MultiPolygon" as const, coordinates: polys } };
      }
      if (g.type === "MultiPolygon") {
        return { ...f, geometry: { ...g, coordinates: g.coordinates.flatMap(fixPolygon) } };
      }
      return f;
    }),
  };
}

const LAND_DATA = unwrapAntimeridian(landGeoJson as unknown as GeoJSON.FeatureCollection);

interface UnderlayPaint {
  fill: string;
  outline: string;
  outlineWidth: number;
}

/** Theme-aware paint from the theme tokens. Dark (paint law): land #000
 *  on ocean #0b0b0c — nearly the same value, so the coastline STROKE
 *  (border token) carries the readability. Light: Positron-like white
 *  land on the pale-blue ocean token — but the light border token
 *  (#c3ccd4, 1.30:1 against the ocean) is effectively invisible as a
 *  0.8px stroke, and in the variant where a real style has loaded but
 *  its tiles failed, the underlay sits on that style's WHITE background
 *  — white fill on white, where only the stroke can draw the continents
 *  at all. The light stroke therefore uses the theme's mid-slate
 *  (textDim #4b5563: 6.0:1 vs the ocean, 7.6:1 vs the land fill) at
 *  1.1px. SAT falls back to the dark scheme
 *  (its style background is the dark ocean token). */
export function underlayPaint(mode: BasemapMode): UnderlayPaint {
  if (mode === "light") {
    return {
      fill: tokens.light.land,
      outline: tokens.light.textDim,
      outlineWidth: 1.1,
    };
  }
  /* Offline dark: the land lifts a step above the ocean so the
   * continents read as masses (the live tiles' land is pure black on
   * #0b0b0c, which only the strokes carry), with a quiet coastline. */
  return {
    fill: "#14181d",
    outline: "#3a424c",
    outlineWidth: 0.8,
  };
}

/**
 * Idempotently (re)assert the underlay source + layers on a map whose
 * style has (re)loaded. `setStyle` wipes custom sources/layers, so this
 * must run on every style load / theme switch — SupermAPMap calls it from
 * `style.load`, the initial `load` handler, and the basemap-switch
 * `styledata` handler. Layers are inserted beneath every basemap
 * geography layer: above the style's own background paint (an opaque
 * background layer would otherwise cover them), below the first
 * non-background layer.
 */
export function ensureUnderlay(
  map: MlMap,
  mode: BasemapMode,
  visible: boolean,
): void {
  try {
    if (!map.getSource(UNDERLAY_SOURCE_ID)) {
      map.addSource(UNDERLAY_SOURCE_ID, {
        type: "geojson",
        data: LAND_DATA,
        tolerance: UNDERLAY_TILE_TOLERANCE,
      } as never);
    }
    const paint = underlayPaint(mode);
    const style = map.getStyle();
    const firstGeoLayer = style?.layers?.find((l) => l.type !== "background");
    const beforeId = firstGeoLayer?.id;
    if (!map.getLayer(UNDERLAY_FILL_LAYER_ID)) {
      map.addLayer(
        {
          id: UNDERLAY_FILL_LAYER_ID,
          type: "fill",
          source: UNDERLAY_SOURCE_ID,
          paint: { "fill-color": paint.fill, "fill-opacity": 1 },
        } as never,
        beforeId,
      );
    }
    if (!map.getLayer(UNDERLAY_OUTLINE_LAYER_ID)) {
      map.addLayer(
        {
          id: UNDERLAY_OUTLINE_LAYER_ID,
          type: "line",
          source: UNDERLAY_SOURCE_ID,
          paint: {
            "line-color": paint.outline,
            "line-width": paint.outlineWidth,
            "line-opacity": 0.9,
          },
        } as never,
        beforeId,
      );
    }
    // Re-assert paint + visibility even when the layers already existed
    // (mode changed under a persisted style, or visibility flipped).
    map.setPaintProperty(UNDERLAY_FILL_LAYER_ID, "fill-color", paint.fill);
    map.setPaintProperty(
      UNDERLAY_OUTLINE_LAYER_ID,
      "line-color",
      paint.outline,
    );
    map.setPaintProperty(
      UNDERLAY_OUTLINE_LAYER_ID,
      "line-width",
      paint.outlineWidth,
    );
    setUnderlayVisibility(map, visible);
  } catch {
    /* the underlay is a fallback — it must never break the map */
  }
}

/**
 * Minimal LOCAL fallback style: ocean background + the underlay source
 * and its fill/outline layers, nothing else. Needed because a MapLibre
 * map whose remote style JSON failed to load has NO style at all
 * (`map.getStyle()` is undefined) and cannot accept sources/layers — so
 * when a basemap attempt goes unreachable with no style loaded, the
 * component swaps this style in and the underlay becomes the whole
 * (bottom) stack. It carries no tile layers and no labels, and the
 * component's fallback flag ensures its load is never mistaken for a
 * real basemap load (the UNREACHABLE chip stays up). As soon as a real
 * style is (re)tried, `applyBasemap` replaces it wholesale.
 */
export function buildFallbackStyle(mode: BasemapMode): object {
  const paint = underlayPaint(mode);
  const ocean =
    mode === "light" ? tokens.light.ocean : tokens.dark.ocean;
  return {
    version: 8,
    sources: {
      [UNDERLAY_SOURCE_ID]: { type: "geojson", data: LAND_DATA, tolerance: UNDERLAY_TILE_TOLERANCE },
    },
    layers: [
      {
        id: UNDERLAY_BG_LAYER_ID,
        type: "background",
        paint: { "background-color": ocean },
      },
      {
        id: UNDERLAY_FILL_LAYER_ID,
        type: "fill",
        source: UNDERLAY_SOURCE_ID,
        paint: { "fill-color": paint.fill, "fill-opacity": 1 },
      },
      {
        id: UNDERLAY_OUTLINE_LAYER_ID,
        type: "line",
        source: UNDERLAY_SOURCE_ID,
        paint: {
          "line-color": paint.outline,
          "line-width": paint.outlineWidth,
          "line-opacity": 0.9,
        },
      },
    ],
  };
}

/**
 * Ensure the map's style is the local fallback style FOR `mode` whenever
 * there is no real basemap to show. Returns true when the fallback style
 * is the active style after the call. Cases:
 *  - no style at all (remote style JSON failed): install the fallback.
 *  - the fallback style is active but painted for a DIFFERENT mode
 *    (a theme switch whose new remote style is still hanging/failing —
 *    a pending setStyle leaves the old style on screen): re-install it
 *    in the current mode so the fallback follows the theme.
 *  - a real style is active (only its tiles failed): leave it alone —
 *    the underlay layers inside it are the fallback there.
 */
export function syncFallbackStyle(map: MlMap, mode: BasemapMode): boolean {
  let style: ReturnType<MlMap["getStyle"]> | undefined;
  try {
    style = map.getStyle();
  } catch {
    style = undefined;
  }
  const bg = style?.layers?.find((l) => l.id === UNDERLAY_BG_LAYER_ID);
  const isFallback = !!bg;
  if (style && !isFallback) return false;
  const wantOcean =
    mode === "light" ? tokens.light.ocean : tokens.dark.ocean;
  const curOcean = (
    bg as { paint?: Record<string, unknown> } | undefined
  )?.paint?.["background-color"];
  if (style && isFallback && curOcean === wantOcean) return true;
  try {
    map.setStyle(buildFallbackStyle(mode) as never);
  } catch {
    return isFallback;
  }
  return true;
}

/** Show/hide the underlay layers if they exist on the current style. */
export function setUnderlayVisibility(map: MlMap, visible: boolean): void {
  for (const id of UNDERLAY_LAYER_IDS) {
    try {
      if (map.getLayer(id)) {
        map.setLayoutProperty(
          id,
          "visibility",
          visible ? "visible" : "none",
        );
      }
    } catch {
      /* noop */
    }
  }
}
