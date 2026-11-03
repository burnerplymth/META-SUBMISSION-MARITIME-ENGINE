/* Replay window: the three drawings, built as VNode trees (charts/vnode) so the screen and the SVG / PNG export paint
 * the same tree. All three share one time axis (logic.Axis) and one left margin, so a date sits at the same x in the
 * observed chart, the modelled steps and the simulation lines; the cursor is a React overlay (not part of the tree). */
import { CHANNEL_COLOR, INK, INK2, INK3, SURFACE, seriesVar, text, xAxis, yAxis } from '../../charts/draw';
import { clipLabel } from '../../charts/format';
import { extent, linearScale, niceStep, niceTicks, valueDomain } from '../../charts/scale';
import type { ExportTable, Rect } from '../../charts/types';
import { h, linePath, round, type VNode } from '../../charts/vnode';
import { DAY_MS, isoDay, toMs, type Axis, type Step } from './logic';
import type { EpisodeDoc, ObsEvent, ObsSeries, SimSeries } from './types';

export const LEFT = 46;
const RIGHT = 12;
const TOP = 18;
const BOTTOM = 24;

export interface ChartOut {
  nodes: VNode[]; width: number; height: number; plot: Rect;
  /** time (ms) at a pixel x, and pixel x at a time, on the shared axis */
  fromX: (px: number) => number; xOf: (ms: number) => number;
  table: ExportTable;
  /** modelled steps that fall outside the axis */
  beyond: number;
}

function frame(axis: Axis, width: number, height: number, yDomain: [number, number], yTitle: string, yTicksCount = 4) {
  const plot: Rect = { x: LEFT, y: TOP, w: Math.max(60, width - LEFT - RIGHT), h: Math.max(60, height - TOP - BOTTOM) };
  const xs = linearScale([axis.t0, axis.t1], [plot.x, plot.x + plot.w]);
  const ys = linearScale(yDomain, [plot.y + plot.h, plot.y]);
  const step = niceStep(yDomain[1] - yDomain[0], yTicksCount);
  const ticks = niceTicks(yDomain[0], yDomain[1], yTicksCount);
  const nodes: VNode[] = [];
  nodes.push(text(2, 10, clipLabel(yTitle, plot.w), { fill: INK2, tracking: 0.4 }));
  nodes.push(...yAxis(ticks, ys, plot, step));
  nodes.push(...xAxis('time', xs, plot, [axis.t0, axis.t1], Math.max(2, Math.floor(plot.w / 90))).nodes);
  return { plot, xs, ys, nodes };
}

/** The stated window of the preregistered scenario (start to start + duration), drawn behind the data. */
function windowBand(doc: EpisodeDoc, axis: Axis, xs: (v: number) => number, plot: Rect): VNode[] {
  if (!doc.disruption) return [];
  const a = Math.max(axis.t0, toMs(doc.disruption.started_at));
  const b = Math.min(axis.t1, toMs(doc.disruption.started_at) + doc.disruption.duration_d * DAY_MS);
  if (!(b > a)) return [];
  return [h('rect', { x: round(xs(a)), y: plot.y, width: Math.max(1, round(xs(b) - xs(a))), height: plot.h, fill: 'var(--range-fill)', 'data-band': 'stated-window' })];
}

/** Vertical rule plus a numbered disc per dated report; discs on nearby dates are staggered so none overlaps. */
function eventMarks(events: readonly ObsEvent[], axis: Axis, xs: (v: number) => number, plot: Rect): VNode[] {
  const out: VNode[] = [];
  const placed: Array<{ x: number; level: number }> = [];
  events.forEach((e, i) => {
    const ms = toMs(e.date);
    if (ms < axis.t0 || ms > axis.t1) return;
    const x = xs(ms);
    let level = 0;
    while (placed.some((p) => p.level === level && Math.abs(p.x - x) < 17)) level += 1;
    placed.push({ x, level });
    const y = plot.y + 9 + level * 17;
    out.push(h('line', { x1: round(x), x2: round(x), y1: plot.y, y2: plot.y + plot.h, stroke: INK3, strokeWidth: 1, strokeDasharray: '3 3', 'data-event': i + 1 }));
    out.push(h('circle', { cx: round(x), cy: y, r: 7.5, fill: SURFACE, stroke: INK, strokeWidth: 1.2 }));
    out.push(text(x, y + 0.5, String(i + 1), { anchor: 'middle', baseline: 'middle', fill: INK, size: 9, weight: 600 }));
  });
  return out;
}

export function buildObserved(doc: EpisodeDoc, series: ObsSeries, axis: Axis, width: number, height = 190): ChartOut {
  const pts = series.points.map(([d, v]) => ({ x: toMs(d), y: v })).filter((p) => p.x >= axis.t0 && p.x <= axis.t1);
  const yDom = valueDomain(extent(pts.map((p) => p.y)), { zero: true });
  const f = frame(axis, width, height, yDom as [number, number], `${series.label} [${series.unit}]`);
  const nodes: VNode[] = [...windowBand(doc, axis, f.xs, f.plot), ...f.nodes];
  nodes.push(h('path', { d: linePath(pts.map((p) => ({ x: f.xs(p.x), y: f.ys(p.y) }))), fill: 'none', stroke: seriesVar(0), strokeWidth: 1.5, strokeLinejoin: 'round', 'data-series': series.id }));
  nodes.push(...eventMarks(doc.observed.events, axis, f.xs, f.plot));
  return { nodes, width, height, plot: f.plot, fromX: f.xs.invert, xOf: f.xs, beyond: 0,
    table: { columns: ['Date', `${series.label} [${series.unit}]`], rows: series.points.filter(([d]) => toMs(d) >= axis.t0 && toMs(d) <= axis.t1).map(([d, v]) => [d, v]) } };
}

const channelColor = (c: string): string => CHANNEL_COLOR[c.toLowerCase()] ?? seriesVar(1);

export function buildModel(doc: EpisodeDoc, steps: readonly Step[], name: (id: string) => string, axis: Axis, width: number, height = 190): ChartOut {
  const inside = steps.filter((s) => { const t = toMs(s.at); return t >= axis.t0 && t <= axis.t1; });
  const top = Math.max(1, ...inside.map((s) => s.addedD));
  const yDom = valueDomain([0, top], { zero: true });
  const f = frame(axis, width, height, yDom as [number, number], 'Added days [d]');
  const nodes: VNode[] = [...windowBand(doc, axis, f.xs, f.plot), ...f.nodes];
  const labelled = inside.length <= 12;
  steps.forEach((s, i) => {
    const t = toMs(s.at);
    if (t < axis.t0 || t > axis.t1) return;
    const x = f.xs(t); const y = f.ys(s.addedD);
    nodes.push(h('line', { x1: round(x), x2: round(x), y1: round(f.ys(0)), y2: round(y), stroke: channelColor(s.channel), strokeWidth: 1.4 }));
    nodes.push(h('circle', { cx: round(x), cy: round(y), r: 4.5, fill: channelColor(s.channel), stroke: SURFACE, strokeWidth: 1, 'data-step': i + 1 }));
    if (labelled) nodes.push(text(x, y - 9, String(i + 1), { anchor: 'middle', fill: INK, size: 9, weight: 600 }));
  });
  return { nodes, width, height, plot: f.plot, fromX: f.xs.invert, xOf: f.xs, beyond: steps.length - inside.length,
    table: { columns: ['Step', 'Place', 'Channel', 'Added days [d]', 'Date'], rows: steps.map((s, i) => [i + 1, name(s.placeId), s.channel, s.addedD, s.date]) } };
}

export function buildSim(doc: EpisodeDoc, sim: SimSeries, axis: Axis, width: number, height = 150): ChartOut {
  const t0 = toMs(sim.start);
  const row = (v: number[]) => v.map((y, i) => ({ x: t0 + i * DAY_MS, y })).filter((p) => p.x >= axis.t0 && p.x <= axis.t1);
  const dis = row(sim.disrupted); const twin = row(sim.twin);
  const yDom = valueDomain(extent([...dis, ...twin].map((p) => p.y)), { zero: true });
  const f = frame(axis, width, height, yDom as [number, number], `${sim.label} [${sim.unit}]`, 3);
  const nodes: VNode[] = [...windowBand(doc, axis, f.xs, f.plot), ...f.nodes];
  const path = (pts: Array<{ x: number; y: number }>) => linePath(pts.map((p) => ({ x: f.xs(p.x), y: f.ys(p.y) })));
  nodes.push(h('path', { d: path(twin), fill: 'none', stroke: INK3, strokeWidth: 1.2, strokeDasharray: '4 3', 'data-series': 'twin' }));
  nodes.push(h('path', { d: path(dis), fill: 'none', stroke: seriesVar(2), strokeWidth: 1.6, strokeLinejoin: 'round', 'data-series': 'disrupted' }));
  return { nodes, width, height, plot: f.plot, fromX: f.xs.invert, xOf: f.xs, beyond: 0,
    table: { columns: ['Date', `${sim.label}, disrupted [${sim.unit}]`, `${sim.label}, undisturbed [${sim.unit}]`],
      rows: sim.disrupted.map((v, i) => [isoDay(t0 + i * DAY_MS), v, sim.twin[i] ?? null]).filter((r) => toMs(r[0] as string) >= axis.t0 && toMs(r[0] as string) <= axis.t1) } };
}

