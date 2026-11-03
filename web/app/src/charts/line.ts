/* Line chart with event markers, direct end labels and a hit list for
 * the hover crosshair. */
import { AXIS, INK, INK2, INK3, SURFACE, colorFor, spreadLabels, text, xAxis, yAxis, yAxisWidth } from './draw';
import { axisTitle, CHART_FS, clipLabel, formatValue, textWidth } from './format';
import { extent, formatTimeFull, linearScale, niceStep, niceTicks, valueDomain, type Domain } from './scale';
import type { BuildOpts, ChartModel, ExportTable, Hit, LegendItem, LineSpec, Rect, SeriesIn } from './types';
import { h, linePath, round, type VNode } from './vnode';

export const xTitle = (kind: 'time' | 'linear', x: number, unit?: string | null): string =>
  kind === 'time' ? formatTimeFull(x) : formatValue(x, unit);

/** Legend entries with colours indexed by the FULL series list, so
 *  hiding a series never repaints the others. */
export function legendOf(series: Array<{ key: string; label: string; color?: string }>): LegendItem[] {
  return series.map((s, i) => ({ key: s.key, label: s.label, color: colorFor(s.key, i, s.color) }));
}

export function buildLine(spec: LineSpec, o: BuildOpts): ChartModel {
  const legend = legendOf(spec.series);
  const colorOf = new Map(legend.map((l) => [l.key, l.color]));
  const shown: SeriesIn[] = spec.series.filter((s) => !o.hidden?.has(s.key));
  const allX = [...shown.flatMap((s) => s.points.map((p) => p.x)), ...(spec.events ?? []).map((e) => e.x)];
  const fullExt = extent(allX);
  let full: Domain | null = fullExt ? (fullExt[0] === fullExt[1] ? [fullExt[0] - 1, fullExt[1] + 1] : fullExt) : null;
  const domain: Domain = o.domain ?? full ?? [0, 1];
  if (!full) full = null;

  const visible = shown.map((s) => ({
    s, pts: s.points.filter((p) => p.x >= domain[0] && p.x <= domain[1]),
    // neighbours just outside the window keep the line entering the plot
    all: s.points,
  }));
  const yExt = extent(visible.flatMap((v) => v.pts.map((p) => p.y)));
  const yDom = valueDomain(yExt, { zero: spec.zeroBase });
  const yStep = niceStep(yDom[1] - yDom[0], 5);
  const yTicks = niceTicks(yDom[0], yDom[1], 5);

  const withLabels = o.width >= 380 && shown.length >= 2 && shown.length <= 6; /* a lone series is named by the chart title: its end label only printed a clipped copy */
  const labelW = withLabels ? Math.min(110, Math.max(...shown.map((s) => textWidth(clipLabel(s.label, 100), CHART_FS))) + 12, o.width * 0.28) : 0;
  const hasEvents = (spec.events?.length ?? 0) > 0;
  const plot: Rect = {
    x: yAxisWidth(yTicks, yStep) + 4, y: 20 + (hasEvents ? 12 : 0),
    w: 0, h: 0,
  };
  plot.w = Math.max(40, o.width - plot.x - 10 - labelW);
  plot.h = Math.max(60, o.height - plot.y - 24);
  const xs = linearScale(domain, [plot.x, plot.x + plot.w]);
  const ys = linearScale(yDom, [plot.y + plot.h, plot.y]);

  const nodes: VNode[] = [];
  const clipId = `clip-${spec.id}`;
  nodes.push(h('defs', null, h('clipPath', { id: clipId }, h('rect', { x: plot.x, y: plot.y - 2, width: plot.w, height: plot.h + 4 }))));
  // axis title row
  const yTitle = axisTitle(spec.yLabel, spec.yUnit);
  if (yTitle) nodes.push(text(plot.x, 11, clipLabel(yTitle, spec.xLabel ? plot.w / 2 : plot.w + labelW), { fill: INK2, tracking: 0.4 }));
  nodes.push(...yAxis(yTicks, ys, plot, yStep));
  const ax = xAxis(spec.xKind, xs, plot, domain, Math.max(2, Math.floor(plot.w / 90)));
  nodes.push(...ax.nodes);
  if (spec.xLabel) nodes.push(text(plot.x + plot.w, 11, clipLabel(`${spec.xLabel}${spec.xUnit ? ` [${spec.xUnit}]` : ''}`, plot.w / 2), { fill: INK2, anchor: 'end', tracking: 0.4 }));
  // zero line when the domain crosses zero
  if (yDom[0] < 0 && yDom[1] > 0) nodes.push(h('line', { x1: plot.x, x2: plot.x + plot.w, y1: round(ys(0)), y2: round(ys(0)), stroke: AXIS, strokeWidth: 1 }));

  const hits: Hit[] = [];
  const body: VNode[] = [];
  const ends: Array<{ y: number; label: string; color: string; x: number }> = [];
  for (const v of visible) {
    const color = colorOf.get(v.s.key) ?? INK;
    const px = v.all.map((p) => ({ x: xs(p.x), y: p.y == null ? null : ys(p.y) }));
    body.push(h('path', { d: linePath(px, spec.step), fill: 'none', stroke: color, strokeWidth: 2, strokeLinejoin: 'round', strokeLinecap: 'round' }));
    const dots = v.pts.length <= 60;
    v.pts.forEach((p, i) => {
      if (p.y == null) return;
      const last = i === v.pts.length - 1;
      const cx = round(xs(p.x)); const cy = round(ys(p.y));
      if (dots || last) body.push(h('circle', { cx, cy, r: last ? 3.5 : 2.5, fill: color, stroke: SURFACE, strokeWidth: 1.5 }));
      hits.push({
        x: cx, y: cy, dx: p.x, seriesKey: v.s.key, seriesLabel: v.s.label, color, placeId: p.placeId,
        title: xTitle(spec.xKind, p.x, spec.xUnit),
        rows: [{ k: v.s.label, v: formatValue(p.y, spec.yUnit) }, ...(p.label ? [{ k: 'Label', v: p.label }] : [])],
      });
    });
    const lastP = [...v.pts].reverse().find((p) => p.y != null);
    if (lastP && lastP.y != null) ends.push({ y: ys(lastP.y), label: v.s.label, color, x: xs(lastP.x) });
  }
  // missing stretches are marked, never bridged and never zero
  for (const v of visible) {
    const gaps = v.pts.filter((p) => p.y == null);
    if (gaps.length) {
      const color = colorOf.get(v.s.key) ?? INK;
      for (const g of gaps) body.push(h('line', { x1: round(xs(g.x)), x2: round(xs(g.x)), y1: plot.y + plot.h - 5, y2: plot.y + plot.h, stroke: color, strokeWidth: 1, strokeDasharray: '1 2' }));
    }
  }
  nodes.push(h('g', { clipPath: `url(#${clipId})` }, ...body));

  // event markers
  (spec.events ?? []).forEach((e, i) => {
    if (e.x < domain[0] || e.x > domain[1]) return;
    const x = round(xs(e.x));
    nodes.push(h('line', { x1: x, x2: x, y1: plot.y, y2: plot.y + plot.h, stroke: INK3, strokeWidth: 1, strokeDasharray: '3 3' }));
    nodes.push(h('path', { d: `M${x - 3} ${plot.y - 1}L${x + 3} ${plot.y - 1}L${x} ${plot.y + 4}Z`, fill: INK2 }));
    /* One event keeps its label; several are numbered markers (the events list names them), so labels never overprint. */
    const many = (spec.events?.length ?? 0) > 1;
    const lbl = many ? String(i + 1) : clipLabel(e.label, 96, CHART_FS);
    const right = many || x + textWidth(lbl) + 6 < plot.x + plot.w;
    nodes.push(text(many ? x : right ? x + 5 : x - 5, plot.y - 6, lbl, { fill: INK2, anchor: many ? 'middle' : right ? 'start' : 'end' }));
    hits.push({ x, y: plot.y + 2, dx: e.x, seriesKey: '__event', seriesLabel: 'Event', color: INK2, title: xTitle(spec.xKind, e.x, spec.xUnit), rows: [{ k: 'Event', v: e.label }] });
  });

  // direct labels at the line ends
  if (withLabels && ends.length) {
    const ly = spreadLabels(ends.map((e) => e.y), 11, plot.y + 4, plot.y + plot.h - 2);
    ends.forEach((e, i) => {
      nodes.push(h('line', { x1: round(e.x + 4), y1: round(e.y), x2: round(plot.x + plot.w + 3), y2: round(ly[i]), stroke: e.color, strokeWidth: 1, strokeOpacity: 0.6 }));
      nodes.push(text(plot.x + plot.w + 6, ly[i], clipLabel(e.label, labelW - 8), { fill: INK, baseline: 'middle' }));
    });
  }

  const table: ExportTable = {
    columns: ['series', spec.xKind === 'time' ? 'at [UTC]' : (spec.xLabel ?? 'x') + (spec.xUnit ? ` [${spec.xUnit}]` : ''), `${spec.yLabel ?? 'y'}${spec.yUnit ? ` [${spec.yUnit}]` : ''}`],
    rows: visible.flatMap((v) => v.pts.map((p) => [v.s.label, spec.xKind === 'time' ? formatTimeFull(p.x) : p.x, p.y] as Array<string | number | null>)),
  };
  return {
    width: o.width, height: o.height, plot, nodes, hits, hitMode: 'x', xScale: xs,
    fullDomain: full, domain, brushable: true, legend, table,
  };
}
