/* Causal-chain cascade timeline. One row per event in stream order; a
 * dot at the moment it hits, sized by magnitude (share of the place's
 * own capacity, so sizes are never added up); an elbow connector from
 * the parent event's dot to the child's, in the child's channel
 * colour. Time runs left to right. */
import { AXIS, GRID, INK, INK2, SURFACE, text } from './draw';
import { clipLabel, formatShare, formatValue, textWidth } from './format';
import { legendOf } from './line';
import { extent, formatTimeFull, formatTimeTick, linearScale, timeTicks, type Domain } from './scale';
import type { BuildOpts, CascadeSpec, ChartModel, ExportTable, Hit, Rect } from './types';
import { h, round, type VNode } from './vnode';

export const CASCADE_ROW = 16;
export const cascadeHeight = (n: number): number => 22 + Math.max(4, n) * CASCADE_ROW + 26;

export const radiusFor = (magnitude: number | null): number =>
  magnitude == null || !Number.isFinite(magnitude) ? 3 : 3 + Math.sqrt(Math.min(1, Math.max(0, magnitude))) * 4;

export function buildCascade(spec: CascadeSpec, o: BuildOpts): ChartModel {
  const events = [...spec.events].sort((a, b) => a.seq - b.seq);
  const channels = [...new Set(events.map((e) => e.channel))];
  const legend = legendOf(channels.map((c) => ({ key: c, label: c })));
  const colorOf = new Map(legend.map((l) => [l.key, l.color]));
  const shownEvents = events.filter((e) => !o.hidden?.has(e.channel));
  const height = cascadeHeight(shownEvents.length);
  const xe = extent(shownEvents.map((e) => e.at));
  const full: Domain | null = xe ? (xe[0] === xe[1] ? [xe[0] - 3.6e6, xe[1] + 3.6e6] : xe) : null;
  const domain: Domain = o.domain ?? full ?? [0, 1];
  const plot: Rect = { x: 10, y: 22, w: Math.max(60, o.width - 20), h: shownEvents.length * CASCADE_ROW + 4 };
  const xs = linearScale(domain, [plot.x + 8, plot.x + plot.w - 8]);
  const rowY = new Map(shownEvents.map((e, i) => [e.seq, plot.y + i * CASCADE_ROW + CASCADE_ROW / 2] as const));

  const nodes: VNode[] = [h('defs', null, h('clipPath', { id: `clip-${spec.id}` }, h('rect', { x: plot.x, y: plot.y, width: plot.w, height: plot.h })))];
  nodes.push(text(plot.x, 11, clipLabel(`${spec.yLabel ?? 'Causal chain'} · stream order`, plot.w), { fill: INK2, tracking: 0.4 }));
  const tt = timeTicks(domain[0], domain[1], Math.max(2, Math.floor(plot.w / 110)));
  let lastX = -Infinity;
  for (const t of tt.ticks) {
    const x = round(xs(t));
    if (x < plot.x || x > plot.x + plot.w) continue;
    nodes.push(h('line', { x1: x, x2: x, y1: plot.y, y2: plot.y + plot.h, stroke: GRID, strokeWidth: 1, strokeOpacity: 0.7 }));
    if (x - lastX >= 70) { nodes.push(text(x, plot.y + plot.h + 14, formatTimeTick(t, tt.unit, tt.step), { anchor: 'middle' })); lastX = x; }
  }
  nodes.push(h('line', { x1: plot.x, x2: plot.x + plot.w, y1: plot.y + plot.h, y2: plot.y + plot.h, stroke: AXIS, strokeWidth: 1 }));

  const body: VNode[] = []; const hits: Hit[] = [];
  const bySeq = new Map(shownEvents.map((e) => [e.seq, e] as const));
  for (const e of shownEvents) {
    const color = colorOf.get(e.channel) ?? INK;
    const cx = xs(e.at); const cy = rowY.get(e.seq) as number;
    const parent = e.parentSeq != null ? bySeq.get(e.parentSeq) : undefined;
    if (parent) {
      const px = xs(parent.at); const py = rowY.get(parent.seq) as number;
      body.push(h('path', { d: `M${round(px)} ${round(py)}H${round(cx)}V${round(cy)}`, fill: 'none', stroke: color, strokeWidth: 1.5, strokeOpacity: 0.85 }));
    }
  }
  for (const e of shownEvents) {
    const color = colorOf.get(e.channel) ?? INK;
    const cx = round(xs(e.at)); const cy = round(rowY.get(e.seq) as number);
    const r = radiusFor(e.magnitude);
    body.push(h('circle', { cx, cy, r: round(r), fill: e.magnitude == null ? 'none' : color, stroke: e.magnitude == null ? color : SURFACE, strokeWidth: e.magnitude == null ? 1.5 : 1.25 }));
    const lbl = clipLabel(`${e.place} · ${e.channel}`, 150);
    const right = cx + r + 6 + textWidth(lbl) < plot.x + plot.w;
    body.push(text(right ? cx + r + 5 : cx - r - 5, cy, lbl, { fill: INK, baseline: 'middle', anchor: right ? 'start' : 'end' }));
    hits.push({
      x: cx, y: cy, dx: e.at, seq: e.seq, placeId: e.placeId, seriesKey: e.channel, seriesLabel: e.channel, color,
      box: { x: plot.x, y: cy - CASCADE_ROW / 2, w: plot.w, h: CASCADE_ROW }, title: `${e.place}`,
      rows: [
        { k: 'Seq', v: `${e.seq}${e.parentSeq != null ? ` from ${e.parentSeq}` : ''}` },
        { k: 'At', v: formatTimeFull(e.at) },
        { k: 'Channel', v: e.channel },
        ...(e.mechanism ? [{ k: 'Mechanism', v: e.mechanism }] : []),
        { k: 'Delay', v: formatValue(e.delayD, 'd') },
        { k: 'Magnitude', v: e.magnitude == null ? 'NOT SERVED' : `${formatShare(e.magnitude)} of capacity` },
      ],
    });
  }
  nodes.push(h('g', { clipPath: `url(#clip-${spec.id})` }, ...body));
  const table: ExportTable = {
    columns: ['seq', 'parent_seq', 'at [UTC]', 'place', 'channel', 'mechanism', 'delay_d [days]', 'magnitude [share of capacity]'],
    rows: shownEvents.filter((e) => e.at >= domain[0] && e.at <= domain[1]).map((e) => [e.seq, e.parentSeq, formatTimeFull(e.at), e.place, e.channel, e.mechanism ?? '', e.delayD, e.magnitude]),
  };
  return { width: o.width, height, plot, nodes, hits, hitMode: 'xy', xScale: xs, fullDomain: full, domain, brushable: true, legend, table };
}
