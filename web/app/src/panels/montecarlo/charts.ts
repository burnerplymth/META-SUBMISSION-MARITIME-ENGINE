/* The Monte Carlo window's drawings, as plain VNode trees from the shared
 * charts kit (draw.ts axes, scale.ts, vnode.ts): the same tree paints on screen,
 * and serialises for the SVG and PNG exports. Series A wears --cs-1 and series B
 * --cs-2; text always wears the text tokens. Pure: data in, geometry out. */
import { AXIS, GRID, INK, INK2, INK3, SURFACE, hatchDef, text, xAxis, yAxis, yAxisWidth } from '../../charts/draw';
import { clipLabel, formatTick, textWidth } from '../../charts/format';
import { linearScale, niceStep, niceTicks, valueDomain } from '../../charts/scale';
import type { ExportTable, Rect } from '../../charts/types';
import { h, linePath, round, type VNode } from '../../charts/vnode';
import { fmt, fmt1, fmtPct, fmtSigned, withUnit } from './fmt';
import { inputLabel as inputLabelOf } from './inputs';
import { exceedanceCurve, finite, histogramTotal } from './math';
import type { Histogram, SensitivityRow, SeriesPlace } from './types';

export const COLOR_A = 'var(--cs-1)';
export const COLOR_B = 'var(--cs-2)';
export const COLOR_MARK = 'var(--range)';
export const COLOR_THRESHOLD = 'var(--accent-text)';

export interface HoverCol { x0: number; x1: number; cx: number; title: string; rows: Array<{ k: string; v: string }> }
export interface McChart { nodes: VNode[]; width: number; height: number; plot: Rect; table: ExportTable; hover: HoverCol[]; x?: (v: number) => number }

const empty = (width: number, height: number, columns: string[]): McChart => ({ nodes: [], width, height, plot: { x: 0, y: 0, w: width, h: height }, table: { columns, rows: [] }, hover: [] });
const clampW = (w: number, min = 240): number => Math.max(min, Math.floor(w));

/* ── fan chart ────────────────────────────────────────────────────────── */

export interface FanInput { t: readonly number[]; place: SeriesPlace; unit: string }
/** Bands of one place over time: P10 to P90 light, P25 to P75 darker when the series carries it, the P50 line.
 *  With `other` the second scenario is drawn over the first in its own colour. */
export function buildFan(a: FanInput, width: number, height: number, other?: FanInput | null): McChart {
  const W = clampW(width); const H = Math.max(140, height);
  const ts = a.t.filter(finite);
  if (ts.length < 2) return empty(W, H, ['t_days']);
  const all = [a, other].filter((x): x is FanInput => !!x);
  const ymax = Math.max(0, ...all.flatMap((s) => [...s.place.p90, ...(s.place.p75 ?? [])].filter(finite)));
  const dom = valueDomain([0, ymax || 1], { zero: true, count: 4 });
  const ticks = niceTicks(dom[0], dom[1], 4); const step = niceStep(dom[1] - dom[0], 4);
  const left = yAxisWidth(ticks, step) + 14; const plot: Rect = { x: left, y: 18, w: Math.max(60, W - left - 14), h: H - 18 - 34 };
  const xs = linearScale([ts[0], ts[ts.length - 1]], [plot.x, plot.x + plot.w]);
  const ys = linearScale(dom, [plot.y + plot.h, plot.y]);
  const nodes: VNode[] = [...yAxis(ticks, ys, plot, step)];
  const ax = xAxis('linear', xs, plot, [ts[0], ts[ts.length - 1]], 6);
  nodes.push(...ax.nodes);
  nodes.push(text(plot.x + plot.w / 2, plot.y + plot.h + 29, 'Day from the start of the disruption', { anchor: 'middle', fill: INK3 }));
  nodes.push(text(plot.x - 6, plot.y - 10, a.unit === 'd' ? 'd' : a.unit, { anchor: 'end', fill: INK3 }));
  const band = (s: FanInput, lo: readonly number[], hi: readonly number[], color: string, op: number): VNode => {
    const n = Math.min(s.t.length, lo.length, hi.length);
    const up = Array.from({ length: n }, (_, i) => `${i === 0 ? 'M' : 'L'}${round(xs(s.t[i]))} ${round(ys(hi[i]))}`).join('');
    const dn = Array.from({ length: n }, (_, k) => { const i = n - 1 - k; return `L${round(xs(s.t[i]))} ${round(ys(lo[i]))}`; }).join('');
    return h('path', { d: `${up}${dn}Z`, fill: color, fillOpacity: op, stroke: 'none' });
  };
  const draw = (s: FanInput, color: string, tag: string) => {
    nodes.push(band(s, s.place.p10, s.place.p90, color, other ? 0.16 : 0.2));
    if (s.place.p25 && s.place.p75) nodes.push(band(s, s.place.p25, s.place.p75, color, other ? 0.26 : 0.34));
    const n = Math.min(s.t.length, s.place.p50.length);
    nodes.push(h('path', { d: linePath(Array.from({ length: n }, (_, i) => ({ x: xs(s.t[i]), y: ys(s.place.p50[i]) }))), fill: 'none', stroke: color, strokeWidth: 2, strokeLinejoin: 'round' }));
    const last = n - 1;
    if (last >= 0) nodes.push(text(Math.min(plot.x + plot.w, xs(s.t[last])) - 3, ys(s.place.p50[last]) - 6, other ? `${tag} P50` : 'P50', { anchor: 'end', fill: INK, size: 9 }));
  };
  draw(a, COLOR_A, 'A');
  if (other) draw(other, COLOR_B, 'B');
  const hover: HoverCol[] = ts.map((t, i) => {
    const x = xs(t); const half = (plot.w / (ts.length - 1)) / 2;
    const rows = [{ k: other ? 'A P10 to P90' : 'P10 to P90', v: `${fmt1(a.place.p10[i])} to ${fmt1(a.place.p90[i])} ${a.unit}` }, { k: other ? 'A P50' : 'P50', v: `${fmt1(a.place.p50[i])} ${a.unit}` }];
    if (other) rows.push({ k: 'B P10 to P90', v: `${fmt1(other.place.p10[i])} to ${fmt1(other.place.p90[i])} ${a.unit}` }, { k: 'B P50', v: `${fmt1(other.place.p50[i])} ${a.unit}` });
    return { x0: x - half, x1: x + half, cx: x, title: `Day ${fmt1(t)}`, rows };
  });
  const cols = ['t_days', `${a.place.place}_p10`, `${a.place.place}_p50`, `${a.place.place}_p90`, ...(other ? [`${other.place.place}_b_p10`, `${other.place.place}_b_p50`, `${other.place.place}_b_p90`] : [])];
  const rows = ts.map((t, i) => [t, a.place.p10[i] ?? null, a.place.p50[i] ?? null, a.place.p90[i] ?? null, ...(other ? [other.place.p10[i] ?? null, other.place.p50[i] ?? null, other.place.p90[i] ?? null] : [])]);
  return { nodes, width: W, height: H, plot, table: { columns: cols, rows }, hover, x: xs };
}

/* ── histogram ────────────────────────────────────────────────────────── */

export interface Marker { label: string; value: number; color: string; dash?: string; kind?: 'pct' | 'threshold' | 'variant' }
export interface HistInput {
  h: Histogram; unit: string; markers: Marker[]; threshold: number | null; cumulative: boolean;
}
const markerSlots = (ms: Marker[], xs: (v: number) => number, plot: Rect): number[] => {
  /* staggered label rows so neighbours do not overprint */
  const order = ms.map((m, i) => ({ x: xs(m.value), i })).sort((p, q) => p.x - q.x);
  const rowOf = new Array<number>(ms.length).fill(0);
  let prev = -Infinity; let r = 0;
  for (const o of order) { r = o.x - prev < 34 ? (r + 1) % 3 : 0; rowOf[o.i] = r; prev = o.x; }
  void plot;
  return rowOf;
};

/** Histogram of one metric with percentile markers and the threshold line; `cumulative` draws the exceedance curve instead. */
export function buildHistogram(inp: HistInput, width: number, height: number): McChart {
  const W = clampW(width); const H = Math.max(150, height);
  const hist = inp.h;
  if (!hist || hist.counts.length === 0) return empty(W, H, ['bin_low', 'bin_high', 'simulations']);
  const total = histogramTotal(hist) || 1;
  const e0 = hist.edges[0]; const e1 = hist.edges[hist.edges.length - 1];
  const extra = inp.markers.map((m) => m.value).filter(finite);
  const lo = Math.min(e0, ...extra); const hi = Math.max(e1, ...extra, inp.threshold ?? -Infinity);
  const cum = inp.cumulative;
  const ymax = cum ? 100 : Math.max(1, ...hist.counts);
  const dom = cum ? [0, 100] as const : valueDomain([0, ymax], { zero: true, count: 4 });
  const ticks = niceTicks(dom[0], dom[1], 4); const step = niceStep(dom[1] - dom[0], 4);
  const left = yAxisWidth(ticks, step) + 18; const plot: Rect = { x: left, y: 28, w: Math.max(60, W - left - 14), h: H - 28 - 36 };
  const xs = linearScale([lo, hi], [plot.x, plot.x + plot.w]);
  const ys = linearScale([dom[0], dom[1]], [plot.y + plot.h, plot.y]);
  const nodes: VNode[] = [...yAxis(ticks, ys, plot, step)];
  nodes.push(...xAxis('linear', xs, plot, [lo, hi], 7).nodes);
  nodes.push(text(plot.x + plot.w / 2, plot.y + plot.h + 30, inp.unit === 'd' ? 'Days' : inp.unit, { anchor: 'middle', fill: INK3 }));
  nodes.push(text(plot.x - 6, plot.y - 16, cum ? 'Share of simulations above' : 'Simulations', { anchor: 'start', fill: INK3 }));
  const hover: HoverCol[] = [];
  const table: ExportTable = cum
    ? { columns: ['value', 'share_above_pct'], rows: [] } : { columns: ['bin_low', 'bin_high', 'simulations'], rows: [] };
  if (cum) {
    const pts = exceedanceCurve(hist);
    const path = linePath(pts.map((p) => ({ x: xs(p.x), y: ys(p.share * 100) })));
    nodes.push(h('path', { d: `${path}L${round(xs(pts[pts.length - 1].x))} ${round(plot.y + plot.h)}L${round(xs(pts[0].x))} ${round(plot.y + plot.h)}Z`, fill: COLOR_A, fillOpacity: 0.16, stroke: 'none' }));
    nodes.push(h('path', { d: path, fill: 'none', stroke: COLOR_A, strokeWidth: 2, strokeLinejoin: 'round' }));
    pts.forEach((p, i) => {
      table.rows.push([round(p.x), round(p.share * 100)]);
      const x = xs(p.x); const half = plot.w / pts.length / 2;
      hover.push({ x0: x - half, x1: x + half, cx: x, title: withUnit(p.x, inp.unit), rows: [{ k: 'Above', v: fmtPct(p.share) }] });
      void i;
    });
  } else {
    hist.counts.forEach((c, i) => {
      const a = hist.edges[i]; const b = hist.edges[i + 1];
      const x = xs(a); const w = Math.max(1, xs(b) - xs(a) - 1);
      const above = inp.threshold !== null && a >= inp.threshold;
      const y = ys(c);
      if (c > 0) nodes.push(h('rect', { x: round(x), y: round(y), width: round(w), height: round(plot.y + plot.h - y), fill: COLOR_A, fillOpacity: above ? 0.9 : 0.5 }));
      table.rows.push([round(a), round(b), c]);
      hover.push({ x0: xs(a), x1: xs(b), cx: (xs(a) + xs(b)) / 2, title: `${fmt(a, inp.unit)} to ${fmt(b, inp.unit)} ${inp.unit === 'd' ? 'd' : inp.unit}`, rows: [{ k: 'Simulations', v: `${c} of ${total}` }] });
    });
  }
  const rowsPx = [0, 11, 22];
  const slots = markerSlots(inp.markers, xs, plot);
  inp.markers.forEach((m, i) => {
    if (!finite(m.value)) return;
    const x = round(xs(m.value));
    nodes.push(h('line', { x1: x, x2: x, y1: plot.y - 2, y2: plot.y + plot.h, stroke: m.color, strokeWidth: m.kind === 'variant' ? 1.5 : 1.25, strokeDasharray: m.dash }));
    /* a label that would run past the right edge sits left of its line */
    const flip = x + 3 + textWidth(m.label, 9) > plot.x + plot.w + 10;
    nodes.push(text(flip ? x - 3 : x + 3, plot.y + 9 + rowsPx[slots[i]], m.label, { fill: INK, size: 9, anchor: flip ? 'end' : 'start' }));
  });
  if (inp.threshold !== null && finite(inp.threshold)) {
    const x = round(xs(Math.min(hi, Math.max(lo, inp.threshold))));
    nodes.push(h('line', { x1: x, x2: x, y1: plot.y - 4, y2: plot.y + plot.h, stroke: COLOR_THRESHOLD, strokeWidth: 2 }));
    nodes.push(h('rect', { x: x - 3, y: plot.y - 8, width: 6, height: 6, fill: COLOR_THRESHOLD }));
  }
  return { nodes, width: W, height: H, plot, table, hover, x: xs };
}

/* ── compare: overlaid histograms ─────────────────────────────────────── */

export interface CompareHistInput { edges: readonly number[]; a: readonly number[]; b: readonly number[]; unit: string; p50a: number | null; p50b: number | null; nameA: string; nameB: string }
/** Two distributions on shared bins; the overlap (the smaller of the two in every bin) is hatched. */
export function buildCompareHistogram(inp: CompareHistInput, width: number, height: number): McChart {
  const W = clampW(width); const H = Math.max(150, height);
  const n = inp.a.length;
  if (n === 0 || inp.edges.length !== n + 1) return empty(W, H, ['bin_low', 'bin_high', 'a_simulations', 'b_simulations']);
  const sa = inp.a.reduce((s, c) => s + c, 0) || 1; const sb = inp.b.reduce((s, c) => s + c, 0) || 1;
  const share = (c: number, tot: number) => (c / tot) * 100;
  const pa = inp.a.map((c) => share(c, sa)); const pb = inp.b.map((c) => share(c, sb));
  const dom = valueDomain([0, Math.max(1, ...pa, ...pb)], { zero: true, count: 4 });
  const ticks = niceTicks(dom[0], dom[1], 4); const step = niceStep(dom[1] - dom[0], 4);
  const left = yAxisWidth(ticks, step) + 18; const plot: Rect = { x: left, y: 28, w: Math.max(60, W - left - 14), h: H - 28 - 36 };
  const lo = inp.edges[0]; const hi = inp.edges[n];
  const xs = linearScale([lo, hi], [plot.x, plot.x + plot.w]);
  const ys = linearScale([dom[0], dom[1]], [plot.y + plot.h, plot.y]);
  const nodes: VNode[] = [h('defs', null, hatchDef('mc-ov')), ...yAxis(ticks, ys, plot, step)];
  nodes.push(...xAxis('linear', xs, plot, [lo, hi], 7).nodes);
  nodes.push(text(plot.x + plot.w / 2, plot.y + plot.h + 30, inp.unit === 'd' ? 'Days' : inp.unit, { anchor: 'middle', fill: INK3 }));
  nodes.push(text(plot.x - 6, plot.y - 16, 'Share of simulations per bin, %', { fill: INK3 }));
  const hover: HoverCol[] = []; const table: ExportTable = { columns: ['bin_low', 'bin_high', 'a_simulations', 'b_simulations'], rows: [] };
  for (let i = 0; i < n; i++) {
    const x = xs(inp.edges[i]); const w = Math.max(1, xs(inp.edges[i + 1]) - x - 1);
    if (pa[i] > 0) nodes.push(h('rect', { x: round(x), y: round(ys(pa[i])), width: round(w), height: round(plot.y + plot.h - ys(pa[i])), fill: COLOR_A, fillOpacity: 0.45 }));
    if (pb[i] > 0) nodes.push(h('rect', { x: round(x), y: round(ys(pb[i])), width: round(w), height: round(plot.y + plot.h - ys(pb[i])), fill: COLOR_B, fillOpacity: 0.45 }));
    const m = Math.min(pa[i], pb[i]);
    if (m > 0) nodes.push(h('rect', { x: round(x), y: round(ys(m)), width: round(w), height: round(plot.y + plot.h - ys(m)), fill: 'url(#mc-ov)', stroke: INK3, strokeWidth: 0.5 }));
    table.rows.push([round(inp.edges[i]), round(inp.edges[i + 1]), inp.a[i], inp.b[i]]);
    hover.push({ x0: xs(inp.edges[i]), x1: xs(inp.edges[i + 1]), cx: x + w / 2, title: `${fmt(inp.edges[i], inp.unit)} to ${fmt(inp.edges[i + 1], inp.unit)} ${inp.unit === 'd' ? 'd' : inp.unit}`, rows: [{ k: 'A', v: `${inp.a[i]} of ${sa}` }, { k: 'B', v: `${inp.b[i]} of ${sb}` }] });
  }
  const mk = (v: number | null, color: string, tag: string, row: number) => {
    if (v === null || !finite(v)) return;
    const x = round(xs(v));
    nodes.push(h('line', { x1: x, x2: x, y1: plot.y - 2, y2: plot.y + plot.h, stroke: color, strokeWidth: 2 }));
    const lab = `${tag} P50 ${fmt(v, inp.unit)}`;
    const flip = x + 3 + textWidth(lab, 9) > plot.x + plot.w + 10;
    nodes.push(text(flip ? x - 3 : x + 3, plot.y + 9 + row * 11, lab, { fill: INK, size: 9, anchor: flip ? 'end' : 'start' }));
  };
  mk(inp.p50a, COLOR_A, 'A', 0); mk(inp.p50b, COLOR_B, 'B', 1);
  return { nodes, width: W, height: H, plot, table, hover, x: xs };
}

/* ── sensitivity (tornado) ────────────────────────────────────────────── */

export const TORNADO_ROW = 26;
/** Inputs ranked by the size of their swing; a bar to the right raises the output, to the left lowers it. Fixed inputs list as Fixed. */
/** The swing of a sensitivity row: the current field, else the deprecated one; null for a fixed input. */
export const swingOf = (r: SensitivityRow): number | null => (finite(r.swing_top_vs_bottom_tenth) ? (r.swing_top_vs_bottom_tenth as number) : finite(r.swing_p10_p90) ? (r.swing_p10_p90 as number) : null);

export function buildTornado(rows: readonly SensitivityRow[], unit: string, width: number): McChart {
  const W = clampW(width, 280);
  const live = rows.filter((r) => swingOf(r) !== null).sort((p, q) => Math.abs(swingOf(q) as number) - Math.abs(swingOf(p) as number));
  const fixed = rows.filter((r) => swingOf(r) === null);
  const list = [...live, ...fixed];
  /* the axis title sits above the tick labels with a clear line between them (it overprinted the ticks before) */
  const H = 28 + Math.max(1, list.length) * TORNADO_ROW + 6;
  const labW = Math.min(150, Math.max(...list.map((r) => textWidth(inputLabelOf(r.input), 10)), 60) + 8);
  const plot: Rect = { x: labW + 10, y: 28, w: Math.max(80, W - labW - 10 - 70), h: list.length * TORNADO_ROW };
  const m = Math.max(1, ...live.map((r) => Math.abs(swingOf(r) as number)));
  const signed = live.some((r) => (swingOf(r) as number) < 0);
  const dom: [number, number] = signed ? [-m, m] : [0, m];
  const xs = linearScale(dom, [plot.x, plot.x + plot.w]);
  const x0 = round(xs(0));
  const nodes: VNode[] = [];
  const ticks = niceTicks(dom[0], dom[1], 4);
  for (const t of ticks) {
    nodes.push(h('line', { x1: round(xs(t)), x2: round(xs(t)), y1: plot.y - 2, y2: plot.y + plot.h, stroke: GRID, strokeWidth: 1, strokeOpacity: 0.7 }));
    nodes.push(text(xs(t), plot.y - 8, formatTick(t), { anchor: 'middle', fill: INK3 }));
  }
  nodes.push(h('line', { x1: x0, x2: x0, y1: plot.y - 2, y2: plot.y + plot.h, stroke: AXIS, strokeWidth: 1 }));
  nodes.push(text(plot.x + plot.w, 8, `Swing, top vs bottom tenth of input, ${unit}`, { anchor: 'end', fill: INK3 }));
  const table: ExportTable = { columns: ['input', 'rank_corr', 'swing_top_vs_bottom_tenth'], rows: [] };
  list.forEach((r, i) => {
    const y = plot.y + i * TORNADO_ROW;
    nodes.push(text(labW, y + TORNADO_ROW / 2, clipLabel(inputLabelOf(r.input), labW, 10), { anchor: 'end', baseline: 'middle', fill: INK, size: 10 }));
    table.rows.push([r.input, r.rank_corr, swingOf(r)]);
    const v = swingOf(r);
    if (v === null) { nodes.push(text(plot.x + 6, y + TORNADO_ROW / 2, 'Fixed', { baseline: 'middle', fill: INK2 })); return; }
    const xa = xs(Math.min(0, v)); const xb = xs(Math.max(0, v));
    nodes.push(h('rect', { x: round(xa), y: round(y + 5), width: round(Math.max(1, xb - xa)), height: TORNADO_ROW - 10, fill: v >= 0 ? COLOR_A : COLOR_B, fillOpacity: 0.85 }));
    const label = `${fmtSigned(v, 'd')}${finite(r.rank_corr) ? `  ρ ${(r.rank_corr as number).toFixed(2)}` : ''}`;
    nodes.push(text(v >= 0 ? xb + 5 : xa - 5, y + TORNADO_ROW / 2, label, { anchor: v >= 0 ? 'start' : 'end', baseline: 'middle', fill: INK, size: 9 }));
  });
  return { nodes, width: W, height: H, plot, table, hover: [] };
}

/* ── convergence ──────────────────────────────────────────────────────── */

/** One estimate (P50 or P90) against the number of simulations used: a sparkline with its first and last values. */
export function buildConvergence(rows: ReadonlyArray<{ n: number; v: number }>, unit: string, width: number, height = 64, color: string = COLOR_A): McChart {
  const W = clampW(width, 120); const H = Math.max(40, height);
  if (rows.length < 2) return empty(W, H, ['n', 'value']);
  const pad = { l: 6, r: 52, t: 8, b: 16 };
  const plot: Rect = { x: pad.l, y: pad.t, w: W - pad.l - pad.r, h: H - pad.t - pad.b };
  const vs = rows.map((r) => r.v);
  let lo = Math.min(...vs); let hi = Math.max(...vs);
  if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
  const padv = (hi - lo) * 0.15;
  const xs = linearScale([rows[0].n, rows[rows.length - 1].n], [plot.x, plot.x + plot.w]);
  const ys = linearScale([lo - padv, hi + padv], [plot.y + plot.h, plot.y]);
  const nodes: VNode[] = [
    h('line', { x1: plot.x, x2: plot.x + plot.w, y1: plot.y + plot.h, y2: plot.y + plot.h, stroke: AXIS, strokeWidth: 1 }),
    h('path', { d: linePath(rows.map((r) => ({ x: xs(r.n), y: ys(r.v) }))), fill: 'none', stroke: color, strokeWidth: 2, strokeLinejoin: 'round' }),
  ];
  rows.forEach((r) => nodes.push(h('circle', { cx: round(xs(r.n)), cy: round(ys(r.v)), r: 2, fill: color })));
  const last = rows[rows.length - 1];
  nodes.push(h('circle', { cx: round(xs(last.n)), cy: round(ys(last.v)), r: 3.5, fill: color, stroke: SURFACE, strokeWidth: 1.5 }));
  nodes.push(text(xs(last.n) + 8, ys(last.v), withUnit(last.v, unit), { baseline: 'middle', fill: INK, size: 10 }));
  nodes.push(text(plot.x, H - 3, `n ${rows[0].n}`, { fill: INK3 }));
  nodes.push(text(plot.x + plot.w, H - 3, `n ${last.n}`, { anchor: 'end', fill: INK3 }));
  return { nodes, width: W, height: H, plot, table: { columns: ['n', 'value'], rows: rows.map((r) => [r.n, r.v]) }, hover: [], x: xs };
}

/* ── compare: per-place delta bars ────────────────────────────────────── */

export interface PlaceBarRow { place: string; name: string; d50: number; d90: number }
/** Difference B minus A in P50 (light) and P90 (solid) per place; to the right means B is later. */
export function buildPlaceDeltas(rows: readonly PlaceBarRow[], unit: string, width: number, max = 8): McChart {
  const W = clampW(width, 280);
  const list = rows.slice(0, max);
  const H = 26 + Math.max(1, list.length) * 24 + 6;
  const labW = Math.min(150, Math.max(...list.map((r) => textWidth(r.name, 10)), 60) + 8);
  const plot: Rect = { x: labW + 10, y: 24, w: Math.max(80, W - labW - 10 - 64), h: list.length * 24 };
  const m = Math.max(1, ...list.flatMap((r) => [Math.abs(r.d50), Math.abs(r.d90)]));
  const dom: [number, number] = list.some((r) => r.d50 < 0 || r.d90 < 0) ? [-m, m] : [0, m];
  const xs = linearScale(dom, [plot.x, plot.x + plot.w]);
  const x0 = round(xs(0));
  const nodes: VNode[] = [];
  for (const t of niceTicks(dom[0], dom[1], 4)) {
    nodes.push(h('line', { x1: round(xs(t)), x2: round(xs(t)), y1: plot.y - 2, y2: plot.y + plot.h, stroke: GRID, strokeWidth: 1, strokeOpacity: 0.7 }));
    nodes.push(text(xs(t), plot.y - 8, formatTick(t), { anchor: 'middle', fill: INK3 }));
  }
  nodes.push(h('line', { x1: x0, x2: x0, y1: plot.y - 2, y2: plot.y + plot.h, stroke: AXIS, strokeWidth: 1 }));
  nodes.push(text(plot.x + plot.w, 8, `B minus A, ${unit}`, { anchor: 'end', fill: INK3 }));
  const table: ExportTable = { columns: ['place', 'name', 'delta_p50', 'delta_p90'], rows: [] };
  list.forEach((r, i) => {
    const y = plot.y + i * 24;
    nodes.push(text(labW, y + 12, clipLabel(r.name, labW, 10), { anchor: 'end', baseline: 'middle', fill: INK, size: 10 }));
    const bar = (v: number, dy: number, hh: number, op: number) => {
      const a = xs(Math.min(0, v)); const b = xs(Math.max(0, v));
      nodes.push(h('rect', { x: round(a), y: round(y + dy), width: round(Math.max(1, b - a)), height: hh, fill: COLOR_B, fillOpacity: op }));
    };
    bar(r.d50, 3, 7, 0.45); bar(r.d90, 11, 7, 0.9);
    nodes.push(text(xs(Math.max(0, r.d90)) + 5, y + 12, fmtSigned(r.d90, 'd'), { baseline: 'middle', fill: INK, size: 9 }));
    table.rows.push([r.place, r.name, round(r.d50), round(r.d90)]);
  });
  return { nodes, width: W, height: H, plot, table, hover: [] };
}

/* ── k of N strip layout ──────────────────────────────────────────────── */

export interface StripLayout { cols: number; rows: number; cell: number; gap: number; width: number; height: number; at: (i: number) => { x: number; y: number }; indexAt: (x: number, y: number) => number }
/** N cells in rows of equal width. The cell shrinks until all N fit the width at no more than `maxRows` rows. */
export function stripLayout(n: number, width: number, maxCell = 12, gap = 2, maxRows = 12): StripLayout {
  const count = Math.max(1, n);
  let cell = maxCell;
  let cols = Math.max(1, Math.floor((width + gap) / (cell + gap)));
  while (Math.ceil(count / cols) > maxRows && cell > 4) { cell -= 1; cols = Math.max(1, Math.floor((width + gap) / (cell + gap))); }
  const rows = Math.ceil(count / cols);
  const step = cell + gap;
  return {
    cols, rows, cell, gap, width: cols * step - gap, height: rows * step - gap,
    at: (i) => ({ x: (i % cols) * step, y: Math.floor(i / cols) * step }),
    indexAt: (x, y) => {
      const c = Math.floor(x / step); const r = Math.floor(y / step);
      if (c < 0 || r < 0 || c >= cols || r >= rows) return -1;
      const i = r * cols + c;
      return i < count ? i : -1;
    },
  };
}
