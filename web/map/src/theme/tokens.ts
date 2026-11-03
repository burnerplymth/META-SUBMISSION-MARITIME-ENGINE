import type { ThemeName } from "../types";
/* SupermAP theme tokens — re-tinted to the Rail
 * identity: one variable set drives both
 * themes (light mode is a requirement, not a stretch). Dark = matte
 * graphite. Amber-orange #f0902e (dark) / #d9710f (light) is the mark
 * colour of the causal chain (arcs, rings, halos); azure #29a8d6 /
 * #0b79a3 is selection and the sea-port glow; red only for real
 * disruption.
 * Type: IBM Plex Mono / ui-monospace for data, IBM Plex Sans for prose.
 * Type scale: 8.5 / 9 / 11 / 16px. Spacing discipline: 4px grid.
 */

export const tokens = {
  dark: {
    land: "#000000",
    ocean: "#0b0b0c",
    panel: "#0c0c0c",
    panelAlt: "#121212",
    border: "#3d434b",
    text: "#e8e8e8",
    textDim: "#9aa0a6",
    textFaint: "#7d8590",
    /* Dark amber accent. The map's own geography tokens (land #000,
       landline #3d434b above, ocean) are separate from it. */
    accent: "#f0902e",
    accentText: "#0b0b0c",
    danger: "#d1605c",
    vessel: "#3db8de",
    sea: "#6fd8f0",
    selection: "#29a8d6",
    badgeBg: "#0b0b0b",
    badgeText: "#e8e8e8",
    live: "#35d07f",
  },
  light: {
    land: "#ffffff",
    ocean: "#dbe7f3",
    panel: "#ffffff",
    panelAlt: "#f4f6f8",
    border: "#c3ccd4",
    text: "#17202a",
    textDim: "#4b5563",
    textFaint: "#5f6b76",
    accent: "#d9710f",
    accentText: "#1b1d1f",
    danger: "#d1605c",
    vessel: "#0b79a3",
    sea: "#0b7a96",
    selection: "#0b79a3",
    badgeBg: "#ffffff",
    badgeText: "#141a21",
    live: "#15803d",
  },
  /* RETRO family: same token shape, HUD register —
   * retro-dark = blue-black ground, cyan frames/vessel, orange accent;
   * retro-light = lavender-white ground, pink-red accent, purple
   * vessel. Danger keeps the red severity family in both. */
  "retro-dark": {
    land: "#010203",
    ocean: "#04070d",
    panel: "#070d18",
    panelAlt: "#0a1424",
    border: "#1e7d8c",
    text: "#e8fbff",
    textDim: "#9fd8e8",
    textFaint: "#79b8c9",
    accent: "#ff9e2c",
    accentText: "#140a00",
    danger: "#ff7a6e",
    vessel: "#22e6ff",
    sea: "#4fe3ff",
    selection: "#00d5e8",
    badgeBg: "#070d18",
    badgeText: "#e8fbff",
    live: "#3dff9a",
  },
  "retro-light": {
    land: "#fcfbfe",
    ocean: "#d9d4ea",
    panel: "#ffffff",
    panelAlt: "#f1eef8",
    border: "#c9c2d9",
    text: "#14101f",
    textDim: "#4a4459",
    textFaint: "#5d5670",
    accent: "#b3124f",
    accentText: "#ffffff",
    danger: "#b91c1c",
    vessel: "#7b1fa2",
    sea: "#8e24aa",
    selection: "#e0218a",
    badgeBg: "#ffffff",
    badgeText: "#14101f",
    live: "#0a8f4d",
  },
} as const;

export type ThemeTokens = (typeof tokens)["dark"];

/** Channel colours, the Rail sheet's causal-channel hues (dark): congestion
 *  #f0902e (the chain-arc amber-orange) · queue #e0665c · reroute #d6b34a ·
 *  mode-shift #9d92c8 · inventory #5fae78 · cost #52b3d1. Reroute ARCS
 *  render dashed green (the Rail inventory green); the reroute
 *  channel hue stays the gold for badges, rings and the legend. */
export const channelColors: Record<string, [number, number, number]> = {
  queue: [224, 102, 92],
  congestion: [240, 144, 46],
  reroute: [214, 179, 74],
  "mode-shift": [157, 146, 200],
  inventory: [95, 174, 120],
  cost: [82, 179, 209],
};

/** The reroute-arc green, the Rail inventory green. */
export const REROUTE_ARC: [number, number, number] = [95, 174, 120];

export const modeColors: Record<string, [number, number, number]> = {
  sea: [82, 179, 209],
  air: [154, 160, 166],
  ground: [107, 114, 128],
};

/** Theme families: the retro themes pair with the institutional
 *  basemaps (retro-dark ↔ dark tiles, retro-light ↔ light tiles) and
 *  share their dark/light paint semantics everywhere a binary choice
 *  is made (icon variants, outline colours, alpha ladders). */
export type ThemeFamily = "dark" | "light";
export function themeFamily(t: ThemeName): ThemeFamily {
  return t === "light" || t === "retro-light" ? "light" : "dark";
}
export function isDarkTheme(t: ThemeName): boolean {
  return themeFamily(t) === "dark";
}
/** Basemap mode a theme pairs with (sat is never a pairing). */
export function pairedBasemap(t: ThemeName): ThemeFamily {
  return themeFamily(t);
}
/** The theme a basemap switch lands on, preserving the family the
 *  user is in (retro stays retro, institutional stays institutional). */
export function themeForBasemapMode(current: ThemeName, m: ThemeFamily): ThemeName {
  const retro = current === "retro-dark" || current === "retro-light";
  return retro ? (m === "light" ? "retro-light" : "retro-dark") : m;
}

/* ---- PLACE KIND COLOURS ----
 * Places are glowing points whose COLOUR carries the kind: sea port
 * (the vessel token of the theme), air hub, ground node, chokepoint
 * (the theme's text colour, with a ring). Only the theme tokens and
 * these per-family constants feed the place layer. */
export type PlaceKindKey = "port" | "air_hub" | "ground_node" | "chokepoint";
type RGB3 = [number, number, number];
const hexToRgb3 = (h: string): RGB3 => {
  const s = h.replace("#", "");
  return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16), parseInt(s.slice(4, 6), 16)];
};
const PLACE_AIR: Record<ThemeName, RGB3> = {
  dark: [207, 211, 216],
  light: [89, 98, 107],
  "retro-dark": [255, 122, 217],
  "retro-light": [224, 33, 138],
};
const PLACE_GROUND: Record<ThemeName, RGB3> = {
  dark: [201, 173, 120],
  light: [138, 109, 31],
  "retro-dark": [255, 210, 63],
  "retro-light": [179, 107, 0],
};
export function placeKindColors(theme: ThemeName): Record<PlaceKindKey, RGB3> {
  const t = tokens[theme];
  return {
    port: hexToRgb3(t.vessel),
    air_hub: PLACE_AIR[theme],
    ground_node: PLACE_GROUND[theme],
    chokepoint: hexToRgb3(t.text),
  };
}
