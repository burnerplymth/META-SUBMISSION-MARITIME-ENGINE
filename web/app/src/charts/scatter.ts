/* Scatter: one dot per row, coloured by series, labelled when sparse. */
import { INK, INK2, SURFACE, text, xAxis, yAxis, yAxisWidth } from './draw';
import { axisTitle, clipLabel, formatValue, textWidth } from './format';
import { legendOf } from './line';
import { extent, formatTimeFull, linearScale, niceStep, niceTicks, valueDomain, type Domain } from './scale';
import type { BuildOpts, ChartModel, ExportTable, Hit, Rect, ScatterSpec } from './types';
import { h, round, type VNode } from './vnode';

export function buildScatter(spec: ScatterSpec, o: BuildOpts): ChartModel {
  const legend = legendOf(spec.series);
  const colorOf = new Map(legend.map((l) => [l.key, l.color]));
  const shown = spec.series.filter((s) => !o.hidden?.has(s.key));
  const xe = extent(shown.flatMap((s) => s.points.map((p) => p.x)));
  const full: Domain | null = xe ? (xe[0] === xe[1] ? [xe[0] - 1, xe[1] + 1] : xe) : null;
  const domain: Domain = o.domain ?? full ?? [0, 1];
  const pts = shown.map((s) => ({ s, p: s.points.filter((q) => q.y != null && q.x >= domain[0] && q.x <= domain[1]) }));
  const yDom = valueDomain(extent(pts.flatMap((v) => v.p.map((q) => q.y))), {});
  const yStep = niceStep(yDom[1] - yDom[0], 5);
  const yTicks = niceTicks(yDom[0], yDom[1], 5);
  const plot: Rect = { x: yAxisWidth(yTicks, yStep) + 4, y: 20, w: 0, h: 0 };
  plot.w = Math.max(40, o.width - plot.x - 12);
  plot.h = Math.max(60, o.height - plot.y - 24);
  const xs = linearScale(domain, [plot.x, plot.x + plot.w]);
  const ys = linearScale(yDom, [plot.y + plot.h, plot.y]);
  const clipId = `clip-${spec.id}`;
  const nodes: VNode[] = [h('defs', null, h('clipPath', { id: clipId }, h('rect', { x: plot.x - 4, y: plot.y - 4, width: plot.w + 8, height: plot.h + 8 })))];
  const yTitle = axisTitle(spec.yLabel, spec.yUnit);
  if (yTitle) nodes.push(text(plot.x, 11, clipLabel(yTitle, plot.w / 2), { fill: INK2, tracking: 0.4 }));
  if (spec.xLabel) nodes.push(text(plot.x + plot.w, 11, clipLabel(`${spec.xLabel}${spec.xUnit ? ` [${spec.xUnit}]` : ''}`, plot.w / 2), { fill: INK2, anchor: 'end', tracking: 0.4 }));
  nodes.push(...yAxis(yTicks, ys, plot, yStep));
  nodes.push(...xAxis(spec.xKind, xs, plot, domain, Math.max(2, Math.floor(plot.w / 90))).nodes);

  const hits: Hit[] = []; const body: VNode[] = [];
  const total = pts.reduce((a, v) => a + v.p.length, 0);
  for (const v of pts) {
    const color = colorOf.get(v.s.key) ?? INK;
    for (const q of v.p) {
      const cx = round(xs(q.x)); const cy = round(ys(q.y as number));
      body.push(h('circle', { cx, cy, r: 4, fill: color, fillOpacity: 0.9, stroke: SURFACE, strokeWidth: 1.5 }));
      if (total <= 14 && q.label) {
        const right = cx + textWidth(q.label) + 10 < plot.x + plot.w;
        body.push(text(right ? cx + 7 : cx - 7, cy - 6, clipLabel(q.label, 90), { fill: INK, anchor: right ? 'start' : 'end' }));
      }
      hits.push({
        x: cx, y: cy, dx: q.x, seriesKey: v.s.key, seriesLabel: v.s.label, color, placeId: q.placeId, title: q.label ?? v.s.label,
        rows: [
          { k: spec.xLabel ?? 'X', v: spec.xKind === 'time' ? formatTimeFull(q.x) : formatValue(q.x, spec.xUnit) },
          { k: spec.yLabel ?? 'Y', v: formatValue(q.y, spec.yUnit) },
          ...(shown.length > 1 ? [{ k: 'Series', v: v.s.label }] : []),
        ],
      });
    }
  }
  nodes.push(h('g', { clipPath: `url(#${clipId})` }, ...body));
  const table: ExportTable = {
    columns: ['series', 'label', `${spec.xLabel ?? 'x'}${spec.xUnit ? ` [${spec.xUnit}]` : ''}`, `${spec.yLabel ?? 'y'}${spec.yUnit ? ` [${spec.yUnit}]` : ''}`],
    rows: pts.flatMap((v) => v.p.map((q) => [v.s.label, q.label ?? '', spec.xKind === 'time' ? formatTimeFull(q.x) : q.x, q.y] as Array<string | number | null>)),
  };
  return { width: o.width, height: o.height, plot, nodes, hits, hitMode: 'xy', xScale: xs, fullDomain: full, domain, brushable: true, legend, table };
}
