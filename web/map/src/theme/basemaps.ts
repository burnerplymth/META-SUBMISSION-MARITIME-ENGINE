import { mapCreditHtml, mapCreditText } from "./credits";
import type { StyleSpecification } from "maplibre-gl";
import { tokens } from "./tokens";
import type { BasemapMode } from "../types";
import {
  EOX_MAXZOOM,
  EOX_TILES,
  GIBS_MAXZOOM,
  gibsTileUrl,
  imageryAttribution,
  type SatSource,
} from "./imagery";

/**
 * Basemap styles.
 *  - dark:  CARTO dark-matter GL style, re-painted on load to the theme paint law
 *           (land #000, ocean #0b0b0c) by `applyDarkPaint` below.
 *  - light: CARTO Positron GL style (white land, pale-blue ocean).
 *  - sat:   imagery CONTEXT, never the product's own detections. Two sources
 *           (theme/imagery.ts): STATIC = EOX Sentinel-2 cloudless 2016
 *           (CC BY 4.0), DAILY = NASA GIBS true-colour for an explicit day.
 *           Esri World Imagery was removed (terms); see THIRD-PARTY.md.
 */

export const CARTO_DARK_STYLE =
  "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";
export const CARTO_LIGHT_STYLE =
  "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";

/** The satellite attribution line for the default (static) source. */
export const SAT_ATTRIBUTION = imageryAttribution("static");

/** Registry ids of the credits one map state shows, in order. */
export function attributionIds(
  mode: BasemapMode,
  underlayVisible: boolean,
  satSource: SatSource = "static",
  portwatch = false,
): string[] {
  const ids =
    mode === "sat"
      ? [satSource === "daily" ? "nasa-gibs" : "eox-cloudless"]
      : ["openstreetmap", "carto"];
  if (underlayVisible) ids.push("natural-earth");
  if (portwatch) ids.push("imf-portwatch");
  return ids;
}

/** The legal attribution line, per basemap style, as text: rendered ONLY inside the map's
 *  attribution control. Every credit comes from the shared registry. The bundled coastline
 *  underlay is credited while it is visible; the PortWatch line (with its transformation
 *  note) while PortWatch-derived places are drawn. */
export function attributionFor(
  mode: BasemapMode,
  underlayVisible: boolean,
  satSource: SatSource = "static",
  portwatch = false,
): string {
  return attributionIds(mode, underlayVisible, satSource, portwatch)
    .map((id) => mapCreditText(id, id === "imf-portwatch"))
    .join(" · ");
}

/** The same line as links, for the attribution control. */
export function attributionHtml(
  mode: BasemapMode,
  underlayVisible: boolean,
  satSource: SatSource = "static",
  portwatch = false,
): string {
  return attributionIds(mode, underlayVisible, satSource, portwatch)
    .map((id) => mapCreditHtml(id, id === "imf-portwatch"))
    .join(" · ");
}

/** Raster style for the SATELLITE basemap. `day` is the GIBS acquisition day
 *  (daily source only); a daily style without a day has no tile source. */
export function satelliteStyle(
  source: SatSource = "static",
  day?: string | null,
): StyleSpecification {
  const sources: Record<string, unknown> = {};
  const layers: unknown[] = [
    { id: "bg", type: "background", paint: { "background-color": tokens.dark.ocean } },
  ];
  if (source === "static") {
    sources.imagery = {
      type: "raster",
      tiles: [EOX_TILES],
      tileSize: 256,
      maxzoom: EOX_MAXZOOM,
    };
    layers.push({ id: "imagery", type: "raster", source: "imagery" });
  } else if (day) {
    sources.imagery = {
      type: "raster",
      tiles: [gibsTileUrl(day)],
      tileSize: 256,
      maxzoom: GIBS_MAXZOOM,
    };
    layers.push({ id: "imagery", type: "raster", source: "imagery" });
  }
  return {
    version: 8,
    glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
    sources,
    layers,
  } as unknown as StyleSpecification;
}

/**
 * Re-paint the CARTO dark vector style to the theme paint: water/background -> ocean
 * #0b0b0c, land-ish fills -> pure black #000. Roads/labels stay muted grey.
 * Layer ids in the CARTO dark-matter style are stable (water, landcover_*,
 * landuse_*, background); matching is by id fragment so minor style updates
 * degrade gracefully instead of breaking.
 */
export function applyDarkPaint(map: maplibregl.Map): void {
  const t = tokens.dark;
  const style = map.getStyle();
  if (!style?.layers) return;
  for (const layer of style.layers) {
    const id = layer.id.toLowerCase();
    try {
      if (layer.type === "background") {
        map.setPaintProperty(layer.id, "background-color", t.ocean);
      } else if (id.includes("water")) {
        if (layer.type === "fill")
          map.setPaintProperty(layer.id, "fill-color", t.ocean);
        if (layer.type === "line")
          map.setPaintProperty(layer.id, "line-color", t.ocean);
      } else if (
        id.includes("landcover") ||
        id.includes("landuse") ||
        id === "land"
      ) {
        if (layer.type === "fill")
          map.setPaintProperty(layer.id, "fill-color", t.land);
      }
    } catch {
      /* a renamed layer must never break the map */
    }
  }
}

/** Light theme: Positron already matches the theme paint (white land, pale-blue water).
 *  Nudge both surfaces to the exact tokens: water -> the pale-blue ocean
 *  token, background -> the LAND token (white).
 *
 *  Note: in CARTO Positron the `background` layer IS the land — the style has
 *  no full-coverage land fill (native background #fafaf8; the only land
 *  fill is a sparse 50%-alpha `landcover`). Setting background to the
 *  OCEAN token #dbe7f3 AND water to the same #dbe7f3 would paint land and
 *  sea the identical colour (contrast 1.00:1) and erase the geography.
 *  (Dark is the opposite shape — dark-matter's background reads as the
 *  ocean field and its landmass comes from landcover fills — which is
 *  why `applyDarkPaint` maps background -> ocean and this one must map
 *  background -> land. Do not "symmetrise" them.) */
export function applyLightPaint(map: maplibregl.Map): void {
  const style = map.getStyle();
  if (!style?.layers) return;
  for (const layer of style.layers) {
    const id = layer.id.toLowerCase();
    try {
      if (layer.type === "background") {
        map.setPaintProperty(layer.id, "background-color", tokens.light.land);
      } else if (id.includes("water") && layer.type === "fill") {
        map.setPaintProperty(layer.id, "fill-color", tokens.light.ocean);
      }
    } catch {
      /* noop */
    }
  }
}
