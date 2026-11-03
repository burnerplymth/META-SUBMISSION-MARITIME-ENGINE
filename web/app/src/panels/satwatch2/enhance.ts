/* ENHANCED VIEW, client-side presentation only.
 *
 * Pure pixel functions: a denoise blend, an unsharp mask and the size
 * plan for a canvas upscale. Everything here takes a pixel buffer and
 * returns a NEW buffer; it never mutates its input and it never talks to
 * the server. Enhanced pixels are synthetic: they exist only to be looked
 * at. Detection and counts come from the server, computed on the original
 * pixels; nothing in this module reads a count, a detection or a record,
 * and the panel never sends a canvas anywhere. The enhanced frame carries
 * a permanent SYNTHETIC ENHANCEMENT chip (see EnhancedImage.tsx).
 *
 * No DOM, no fetch, no React. */

export interface EnhanceOptions {
  /** 0..1: how far toward the 3x3 binomial blur each pixel is pulled (speckle / grain). */
  denoise: number;
  /** >= 0: unsharp mask strength applied after the denoise. */
  sharpen: number;
}

/** Presentation presets per source kind. SAR is speckled, so it gets the
 *  stronger denoise; optical and aerial get a light touch. */
export const PRESETS: Record<'sar' | 'optical' | 'aerial', EnhanceOptions> = {
  sar: { denoise: 0.7, sharpen: 1.1 },
  optical: { denoise: 0.25, sharpen: 0.8 },
  aerial: { denoise: 0.15, sharpen: 0.6 },
};

export const presetFor = (kind: string): EnhanceOptions => (kind === 'sar' ? PRESETS.sar : kind === 'aerial' ? PRESETS.aerial : PRESETS.optical);

/** Largest pixel count an enhanced canvas may reach (keeps the main thread responsive). */
export const MAX_ENHANCED_PIXELS = 4_500_000;
export const MAX_UPSCALE = 2;

/** Integer-free upscale factor: up to MAX_UPSCALE, never beyond the pixel budget, never below 1. */
export function upscalePlan(w: number, h: number): { factor: number; width: number; height: number } {
  if (!(w > 0) || !(h > 0)) return { factor: 1, width: Math.max(0, w), height: Math.max(0, h) };
  const budget = Math.sqrt(MAX_ENHANCED_PIXELS / (w * h));
  const factor = Math.max(1, Math.min(MAX_UPSCALE, budget));
  return { factor, width: Math.max(1, Math.round(w * factor)), height: Math.max(1, Math.round(h * factor)) };
}

/** 3x3 binomial blur ([1 2 1] x [1 2 1] / 16) on RGBA; alpha is copied, edges clamp. */
export function blur3(src: Uint8ClampedArray, w: number, h: number): Uint8ClampedArray<ArrayBuffer> {
  const tmp = new Float32Array(w * h * 3);
  const out = new Uint8ClampedArray(src.length);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const xl = x > 0 ? x - 1 : 0; const xr = x < w - 1 ? x + 1 : w - 1;
      const i = (y * w + x) * 4; const il = (y * w + xl) * 4; const ir = (y * w + xr) * 4; const o = (y * w + x) * 3;
      for (let c = 0; c < 3; c++) tmp[o + c] = (src[il + c] + 2 * src[i + c] + src[ir + c]) / 4;
    }
  }
  for (let y = 0; y < h; y++) {
    const yu = y > 0 ? y - 1 : 0; const yd = y < h - 1 ? y + 1 : h - 1;
    for (let x = 0; x < w; x++) {
      const o = (y * w + x) * 3; const ou = (yu * w + x) * 3; const od = (yd * w + x) * 3; const i = (y * w + x) * 4;
      for (let c = 0; c < 3; c++) out[i + c] = (tmp[ou + c] + 2 * tmp[o + c] + tmp[od + c]) / 4;
      out[i + 3] = src[i + 3];
    }
  }
  return out;
}

/** Denoise then unsharp-mask. Returns a new buffer of the same size; the input is untouched. */
export function enhanceRgba(src: Uint8ClampedArray, w: number, h: number, opts: EnhanceOptions): Uint8ClampedArray<ArrayBuffer> {
  if (src.length !== w * h * 4) throw new RangeError('buffer does not match width x height RGBA');
  const d = Math.min(1, Math.max(0, opts.denoise));
  const s = Math.max(0, opts.sharpen);
  let cur: Uint8ClampedArray<ArrayBuffer> = new Uint8ClampedArray(src);
  if (d > 0) {
    const b = blur3(cur, w, h);
    const mixed = new Uint8ClampedArray(cur.length);
    for (let i = 0; i < cur.length; i++) mixed[i] = (i & 3) === 3 ? cur[i] : cur[i] * (1 - d) + b[i] * d;
    cur = mixed;
  }
  if (s > 0) {
    const b = blur3(cur, w, h);
    const sharp = new Uint8ClampedArray(cur.length);
    for (let i = 0; i < cur.length; i++) sharp[i] = (i & 3) === 3 ? cur[i] : cur[i] + s * (cur[i] - b[i]);
    cur = sharp;
  }
  return cur;
}
