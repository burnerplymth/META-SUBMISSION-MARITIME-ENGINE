import React, { useEffect, useRef, useState } from 'react';
import { Chip } from '../reports/chips';
import { cropSrc } from './client';

/* NO-DATA DISPLAY. A satellite pass that did not cover the whole crop leaves fully black or fully transparent
 * pixels. They are painted over with a neutral hatch and the frame carries a "No data" chip, so an empty wedge never
 * reads as dark water. Display only: the crop is fetched a second time, read on a small private copy to find the
 * empty region, and nothing here changes a native pixel, feeds a detection or a count, or leaves the browser. */

const SAMPLE_W = 160;
/** A pixel with no data: transparent, or black in every channel. */
export const isNoDataPixel = (r: number, g: number, b: number, a: number): boolean => a < 8 || (r <= 2 && g <= 2 && b <= 2);
/** The share of cells (0..1) that are no-data, and the cell map. Pure: the caller owns the pixels. */
export function noDataMask(rgba: Uint8ClampedArray | Uint8Array, w: number, h: number): { cells: Uint8Array; frac: number } {
  const cells = new Uint8Array(w * h);
  let n = 0;
  for (let i = 0; i < w * h; i += 1) {
    if (isNoDataPixel(rgba[i * 4], rgba[i * 4 + 1], rgba[i * 4 + 2], rgba[i * 4 + 3])) { cells[i] = 1; n += 1; }
  }
  return { cells, frac: w * h ? n / (w * h) : 0 };
}
/** Below this share the frame is treated as covered (stray black pixels are not a wedge). */
export const NO_DATA_MIN_FRAC = 0.02;

interface Mask { w: number; h: number; cells: Uint8Array }

export function NoDataOverlay({ url }: { url: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [mask, setMask] = useState<Mask | null>(null);
  useEffect(() => {
    const ctrl = new AbortController();
    let alive = true;
    setMask(null);
    (async () => {
      try {
        const res = await fetch(cropSrc(url), { signal: ctrl.signal });
        if (!res.ok) return;
        const bmp = await createImageBitmap(await res.blob());
        const w = Math.min(SAMPLE_W, bmp.width);
        const h = Math.max(1, Math.round((bmp.height / bmp.width) * w));
        const c = document.createElement('canvas'); c.width = w; c.height = h;
        const ctx = c.getContext('2d', { willReadFrequently: true });
        if (!ctx) return;
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(bmp, 0, 0, w, h);
        bmp.close?.();
        const m = noDataMask(ctx.getImageData(0, 0, w, h).data, w, h);
        if (alive && m.frac >= NO_DATA_MIN_FRAC) setMask({ w, h, cells: m.cells });
      } catch { /* no overlay: the frame shows as served */ }
    })();
    return () => { alive = false; ctrl.abort(); };
  }, [url]);
  useEffect(() => {
    const cv = ref.current;
    if (!cv || !mask) return;
    const draw = () => {
      const bw = Math.max(1, cv.clientWidth), bh = Math.max(1, cv.clientHeight);
      cv.width = bw; cv.height = bh;
      const ctx = cv.getContext('2d');
      if (!ctx) return;
      const cs = getComputedStyle(cv);
      const ink = cs.getPropertyValue('--text-3').trim() || '#888', ground = cs.getPropertyValue('--bg-panel').trim() || '#222';
      const tile = document.createElement('canvas'); tile.width = 8; tile.height = 8;
      const t = tile.getContext('2d');
      if (!t) return;
      t.fillStyle = ground; t.fillRect(0, 0, 8, 8);
      t.strokeStyle = ink; t.globalAlpha = 0.55; t.lineWidth = 1;
      t.beginPath(); t.moveTo(0, 8); t.lineTo(8, 0); t.moveTo(-1, 1); t.lineTo(1, -1); t.moveTo(7, 9); t.lineTo(9, 7); t.stroke();
      const pat = ctx.createPattern(tile, 'repeat');
      if (!pat) return;
      ctx.fillStyle = pat;
      const cw = bw / mask.w, ch = bh / mask.h;
      for (let y = 0; y < mask.h; y += 1) {
        let x = 0;
        while (x < mask.w) {
          if (!mask.cells[y * mask.w + x]) { x += 1; continue; }
          let x2 = x;
          while (x2 < mask.w && mask.cells[y * mask.w + x2]) x2 += 1;
          ctx.fillRect(Math.floor(x * cw), Math.floor(y * ch), Math.ceil((x2 - x) * cw) + 1, Math.ceil(ch) + 1);
          x = x2;
        }
      }
    };
    draw();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(draw);
    ro.observe(cv);
    return () => ro.disconnect();
  }, [mask]);
  if (!mask) return null;
  return (
    <>
      <canvas ref={ref} className="sw2-nodata" aria-hidden="true" data-nodata="1" />
      <span className="sw2-nodata-chip"><Chip chip="INFO" word="No data" /></span>
    </>
  );
}
