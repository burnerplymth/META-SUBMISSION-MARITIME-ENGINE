/* Shared drawing pieces for the chart builders: palette, text styles,
 * axes, gridlines, label spreading. Pure VNode producers. */
import { CHART_FS, formatTick, textWidth } from './format';
import type { Domain, LinearScale } from './scale';
import { formatTimeTick, niceTicks, timeTicks } from './scale';
import type { Rect, XKind } from './types';
import { h, round, type VNode } from './vnode';

export const FONT = '"IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif';

/** Categorical slots (index.css in charts.css defines --cs-1 .. --cs-8
 *  per skin). Colour follows the entity, never its rank: callers pass a
 *  stable index (position in the FULL key list), not the filtered one. */
export const SERIES_SLOTS = 8;
export const seriesVar = (i: number): string => `var(--cs-${(((i % SERIES_SLOTS) + SERIES_SLOTS) % SERIES_SLOTS) + 1})`;

/** The chain's channels keep the colours the rest of the app gives them. */
export const CHANNEL_COLOR: Record<string, string> = {
  queue: 'var(--bar-queue)', congestion: 'var(--bar-congestion)', reroute: 'var(--bar-reroute)',
  inventory: 'var(--bar-inventory)', cost: 'var(--bar-cost)', mode_shift: 'var(--bar-mode-shift)', 'mode-shift': 'var(--bar-mode-shift)',
};
export function colorFor(key: string, index: number, explicit?: string): string {
  if (explicit) return explicit;
  return CHANNEL_COLOR[key.toLowerCase()] ?? seriesVar(index);
}

/* Text tokens: values and labels wear text tokens, never the series colour. */
export const INK = 'var(--text)';
export const INK2 = 'var(--text-2)';
export const INK3 = 'var(--text-3)';
export const GRID = 'var(--line)';
export const AXIS = 'var(--line-strong)';
export const SURFACE = 'var(--bg-panel)';

export function text(x: number, y: number, s: string, o: { size?: number; fill?: string; anchor?: 'start' | 'middle' | 'end'; weight?: number | string; baseline?: string; opacity?: number; tracking?: number } = {}): VNode {
  return h('text', {
    x: round(x), y: round(y), fontFamily: FONT, fontSize: o.size ?? CHART_FS, fill: o.fill ?? INK2,
    textAnchor: o.anchor ?? 'start', fontWeight: o.weight, dominantBaseline: o.baseline, fillOpacity: o.opacity,
    letterSpacing: o.tracking, style: 'font-variant-numeric:tabular-nums',
  }, s);
}

export const hatchDef = (id: string): VNode =>
  h('pattern', { id, width: 4, height: 4, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)' },
    h('rect', { width: 4, height: 4, fill: 'none' }), h('line', { x1: 0, y1: 0, x2: 0, y2: 4, stroke: INK3, strokeWidth: 1.2 }));

/** Left value axis with horizontal gridlines. Returns the nodes and the
 *  widest label so callers can size their margin. */
export function yAxisWidth(ticks: number[], step: number): number {
  return Math.max(16, ...ticks.map((t) => textWidth(formatTick(t, step), CHART_FS))) + 10;
}

export function yAxis(ticks: number[], scale: LinearScale, plot: Rect, step: number, grid = true): VNode[] {
  const out: VNode[] = [];
  for (const t of ticks) {
    const y = round(scale(t));
    if (grid) out.push(h('line', { x1: plot.x, x2: plot.x + plot.w, y1: y, y2: y, stroke: GRID, strokeWidth: 1, strokeOpacity: 0.7 }));
    out.push(text(plot.x - 6, y, formatTick(t, step), { anchor: 'end', baseline: 'middle' }));
  }
  return out;
}

/** Bottom axis for time / linear x with light vertical ticks. */
export function xAxis(kind: XKind, scale: LinearScale, plot: Rect, domain: Domain, count = 5): { nodes: VNode[]; ticks: number[] } {
  const nodes: VNode[] = [h('line', { x1: plot.x, x2: plot.x + plot.w, y1: plot.y + plot.h, y2: plot.y + plot.h, stroke: AXIS, strokeWidth: 1 })];
  let ticks: number[]; let label: (t: number) => string;
  if (kind === 'time') {
    const tt = timeTicks(domain[0], domain[1], count);
    ticks = tt.ticks; label = (t) => formatTimeTick(t, tt.unit, tt.step);
  } else {
    const span = domain[1] - domain[0];
    ticks = niceLinear(domain, count); const step = ticks.length > 1 ? ticks[1] - ticks[0] : span || 1;
    label = (t) => formatTick(t, step);
  }
  const minGap = 46; let lastX = -Infinity;
  for (const t of ticks) {
    const x = round(scale(t));
    if (x < plot.x - 0.5 || x > plot.x + plot.w + 0.5) continue;
    nodes.push(h('line', { x1: x, x2: x, y1: plot.y + plot.h, y2: plot.y + plot.h + 3, stroke: AXIS, strokeWidth: 1 }));
    if (x - lastX >= minGap) {
      nodes.push(text(x, plot.y + plot.h + 14, label(t), { anchor: 'middle' }));
      lastX = x;
    }
  }
  return { nodes, ticks };
}

const niceLinear = (d: Domain, count: number): number[] => niceTicks(d[0], d[1], count);

/** Spread label y positions so none closer than `gap`, staying in [lo, hi]. */
export function spreadLabels(ys: number[], gap: number, lo: number, hi: number): number[] {
  const idx = ys.map((y, i) => ({ y, i })).sort((a, b) => a.y - b.y);
  for (let k = 0; k < idx.length; k++) {
    const prev = k === 0 ? lo - gap : idx[k - 1].y;
    if (idx[k].y < prev + gap) idx[k].y = prev + gap;
  }
  for (let k = idx.length - 1; k >= 0; k--) {
    const next = k === idx.length - 1 ? hi + gap : idx[k + 1].y;
    if (idx[k].y > next - gap) idx[k].y = next - gap;
  }
  const out = new Array<number>(ys.length);
  for (const { y, i } of idx) out[i] = Math.min(hi, Math.max(lo, y));
  return out;
}

/** Reading-order x for a categorical legend row (export frame). */
export function legendRow(items: Array<{ label: string; color: string }>, x: number, y: number, maxW: number): { nodes: VNode[]; height: number } {
  const nodes: VNode[] = [];
  let cx = x; let cy = y;
  for (const it of items) {
    const w = 14 + textWidth(it.label, CHART_FS) + 12;
    if (cx + w > x + maxW && cx > x) { cx = x; cy += 14; }
    nodes.push(h('rect', { x: cx, y: cy - 7, width: 9, height: 9, fill: it.color }));
    nodes.push(text(cx + 13, cy, it.label, { fill: INK2, baseline: 'middle', size: CHART_FS }));
    cx += w;
  }
  return { nodes, height: cy - y + 14 };
}
