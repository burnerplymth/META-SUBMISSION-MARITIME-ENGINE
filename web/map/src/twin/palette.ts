// TWIN palette (pure). Per-entity colour is a small integer CODE chosen
// at ingest (state, speed bin, flags); the RGBA is resolved from a flat
// Uint8Array palette built once per theme from the theme TOKENS (no hex
// literals here). Speed colour: moving entities ramp from a dim mix
// (slow) to a bright mix (fast) of the mode's base colour; state colours
// follow the established vocabulary (amber = queued, red = delayed).

import type { ThemeName } from "../types";
import { isDarkTheme, placeKindColors, tokens } from "../theme/tokens";

export const SPEED_BINS = 16;
export const CODES_PER_MODE = 24;
export const CODE_DELAYED = 16;
export const CODE_QUEUED = 17;
export const CODE_BERTHED = 18;
export const CODE_INPORT = 19;
export const CODE_WX = 20;

/** Nominal top speeds (km/h) for the ramp: sea ~ 27 kn, air ~ Mach .85. */
export const SPEED_MAX_KMH: readonly [number, number] = [50, 1000];

export function speedBin(speedKmh: number, mode: 0 | 1): number {
  const f = speedKmh / SPEED_MAX_KMH[mode];
  const b = Math.round((f < 0 ? 0 : f > 1 ? 1 : f) * (SPEED_BINS - 1));
  return b;
}

type RGB = [number, number, number];

function hexRgb(h: string): RGB {
  const s = h.replace("#", "");
  const v = s.length === 3 ? s.split("").map((c) => c + c).join("") : s;
  return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)];
}
const mix = (a: RGB, b: RGB, t: number): RGB => [
  Math.round(a[0] + (b[0] - a[0]) * t),
  Math.round(a[1] + (b[1] - a[1]) * t),
  Math.round(a[2] + (b[2] - a[2]) * t),
];

const cache = new Map<string, Uint8Array>();

/** Flat RGBA palette: index (mode*CODES_PER_MODE + code) * 4. */
export function paletteFor(theme: ThemeName): Uint8Array {
  const hit = cache.get(theme);
  if (hit) return hit;
  const t = tokens[theme];
  const dark = isDarkTheme(theme);
  const ocean = hexRgb(t.ocean);
  const text = hexRgb(t.text);
  const accent = hexRgb(t.accent);
  const danger = hexRgb(t.danger);
  const bases: RGB[] = [hexRgb(t.vessel), placeKindColors(theme).air_hub as RGB];
  const out = new Uint8Array(2 * CODES_PER_MODE * 4);
  for (let m = 0; m < 2; m++) {
    const base = bases[m];
    const dim = mix(base, ocean, dark ? 0.5 : 0.45);
    const bright = mix(base, text, dark ? 0.7 : 0.3);
    const put = (code: number, c: RGB, a: number) => {
      const o = (m * CODES_PER_MODE + code) * 4;
      out[o] = c[0];
      out[o + 1] = c[1];
      out[o + 2] = c[2];
      out[o + 3] = a;
    };
    for (let b = 0; b < SPEED_BINS; b++) put(b, mix(dim, bright, b / (SPEED_BINS - 1)), 225);
    put(CODE_DELAYED, mix(bright, danger, 0.62), 240);
    put(CODE_QUEUED, accent, 240);
    put(CODE_BERTHED, mix(base, ocean, 0.35), 165);
    put(CODE_INPORT, mix(base, ocean, 0.55), 115);
    put(CODE_WX, mix(accent, danger, 0.5), 240);
    for (let c = 21; c < CODES_PER_MODE; c++) put(c, base, 200);
  }
  cache.set(theme, out);
  return out;
}

/** Legend ramp stops (CSS rgb strings) for the SPEED chip, slow to fast. */
export function speedRampCss(theme: ThemeName, mode: 0 | 1): string[] {
  const p = paletteFor(theme);
  const out: string[] = [];
  for (let b = 0; b < SPEED_BINS; b += 5) {
    const o = (mode * CODES_PER_MODE + b) * 4;
    out.push(`rgb(${p[o]},${p[o + 1]},${p[o + 2]})`);
  }
  return out;
}

/** LIVE layer colours (distinct family: observed = text-colour core +
 *  a live-green ring; never the sim cyan / lilac). */
export function liveColors(theme: ThemeName): { core: RGB; ring: RGB } {
  const t = tokens[theme];
  return { core: hexRgb(t.text), ring: hexRgb(t.live) };
}
