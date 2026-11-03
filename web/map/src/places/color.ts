// Tiny colour helpers shared by the place / fleet layers.

export type RGBA = [number, number, number, number];
export type RGB = [number, number, number];

export const rgba = (c: ReadonlyArray<number>, a: number): RGBA => [c[0], c[1], c[2], a];

export function hexToRgba(hex: string, a: number): RGBA {
  const h = hex.replace("#", "");
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
    a,
  ];
}

export const clampByte = (v: number): number => (v < 0 ? 0 : v > 255 ? 255 : Math.round(v));

/** Additive blending (glow): src-alpha / one, so overlapping halos add
 *  light on dark grounds instead of covering each other. luma.gl 9
 *  parameter names (deck.gl 9). */
export const ADDITIVE_BLEND = {
  blend: true,
  blendColorOperation: "add",
  blendColorSrcFactor: "src-alpha",
  blendColorDstFactor: "one",
  blendAlphaOperation: "add",
  blendAlphaSrcFactor: "one",
  blendAlphaDstFactor: "one",
} as const;

/** Standard alpha blending (light grounds, where additive would wash
 *  out to white). */
export const NORMAL_BLEND = {
  blend: true,
  blendColorOperation: "add",
  blendColorSrcFactor: "src-alpha",
  blendColorDstFactor: "one-minus-src-alpha",
  blendAlphaOperation: "add",
  blendAlphaSrcFactor: "one",
  blendAlphaDstFactor: "one-minus-src-alpha",
} as const;

/* ---- the soft round glow sprite (radial falloff), one SVG ---- */
const GLOW_SVG =
  `<svg xmlns='http://www.w3.org/2000/svg' width='128' height='128' viewBox='0 0 128 128'>` +
  `<defs><radialGradient id='g' cx='50%' cy='50%' r='50%'>` +
  `<stop offset='0' stop-color='white' stop-opacity='1'/>` +
  `<stop offset='0.22' stop-color='white' stop-opacity='0.62'/>` +
  `<stop offset='0.5' stop-color='white' stop-opacity='0.2'/>` +
  `<stop offset='0.78' stop-color='white' stop-opacity='0.05'/>` +
  `<stop offset='1' stop-color='white' stop-opacity='0'/>` +
  `</radialGradient></defs>` +
  `<circle cx='64' cy='64' r='64' fill='url(#g)'/></svg>`;
export const GLOW_ATLAS_URL = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(GLOW_SVG)}`;
export const GLOW_MAPPING = {
  glow: { x: 0, y: 0, width: 128, height: 128, anchorY: 64, mask: true },
} as const;
