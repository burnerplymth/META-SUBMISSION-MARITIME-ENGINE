/* Hover logic: pure functions over a model's hit list. */
import type { Hit } from './types';

export interface Hover { hits: Hit[]; anchorX: number; anchorY: number; crosshairX: number | null }

/** Nearest x-column: every hit within half a pixel of the closest
 *  x-position, so a crosshair lists all series at that moment. */
export function hoverByX(hits: readonly Hit[], px: number, maxDist = 40): Hover | null {
  let best: Hit | null = null; let bd = Infinity;
  for (const h of hits) {
    const d = Math.abs(h.x - px);
    if (d < bd) { bd = d; best = h; }
  }
  if (!best || bd > maxDist) return null;
  const group = hits.filter((h) => Math.abs(h.x - best!.x) < 0.5);
  return { hits: group, anchorX: best.x, anchorY: Math.min(...group.map((h) => h.y)), crosshairX: best.x };
}

/** Box containment first (bars, cascade rows), else nearest centre. */
export function hoverByXY(hits: readonly Hit[], px: number, py: number, maxDist = 18): Hover | null {
  const inside = hits.filter((h) => h.box && px >= h.box.x && px <= h.box.x + h.box.w && py >= h.box.y && py <= h.box.y + h.box.h);
  if (inside.length) {
    // grouped bars: pick the sub-band under the pointer; cascade rows are one hit each
    let best = inside[0]; let bd = Infinity;
    for (const h of inside) { const d = Math.hypot(h.x - px, h.y - py); if (d < bd) { bd = d; best = h; } }
    const sameCat = inside.filter((h) => h.title === best.title && h.facet === best.facet);
    return { hits: sameCat.length > 1 && best.box && best.box.w > 0 ? sameCat : [best], anchorX: best.x, anchorY: best.y, crosshairX: null };
  }
  let best: Hit | null = null; let bd = Infinity;
  for (const h of hits) { const d = Math.hypot(h.x - px, h.y - py); if (d < bd) { bd = d; best = h; } }
  if (!best || bd > maxDist) return null;
  return { hits: [best], anchorX: best.x, anchorY: best.y, crosshairX: null };
}

/** Sorted distinct x positions, for keyboard stepping. */
export function hitColumns(hits: readonly Hit[]): number[] {
  return [...new Set(hits.map((h) => Math.round(h.x * 2) / 2))].sort((a, b) => a - b);
}

/** Step the crosshair to the neighbouring column (dir +1 / -1). */
export function stepColumn(cols: readonly number[], cur: number | null, dir: 1 | -1): number | null {
  if (cols.length === 0) return null;
  if (cur == null) return dir > 0 ? cols[0] : cols[cols.length - 1];
  const i = cols.findIndex((c) => Math.abs(c - cur) < 0.75);
  const j = Math.min(cols.length - 1, Math.max(0, (i < 0 ? 0 : i) + dir));
  return cols[j];
}

/** Tooltip box placement: flip to the other side near the right / bottom edge. */
export function placeTooltip(anchorX: number, anchorY: number, box: { w: number; h: number }, size: { w: number; h: number }): { left: number; top: number } {
  const gap = 12;
  let left = anchorX + gap;
  if (left + size.w > box.w) left = anchorX - gap - size.w;
  if (left < 0) left = Math.max(0, Math.min(box.w - size.w, anchorX - size.w / 2));
  let top = anchorY - size.h / 2;
  top = Math.max(0, Math.min(box.h - size.h, top));
  return { left, top };
}
