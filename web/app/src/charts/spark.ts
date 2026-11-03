/* Sparkline: a line (or tiny bars) with min / max / last marked. No
 * axes: it sits in a catalogue row beside a number that carries the
 * unit. Missing values break the line. */
import { INK, INK2, SURFACE, text } from './draw';
import { formatValue } from './format';
import { extent, linearScale } from './scale';
import type { BuildOpts, ChartModel, SparkSpec } from './types';
import { h, linePath, round, type VNode } from './vnode';

export function buildSpark(spec: SparkSpec, o: BuildOpts): ChartModel {
  const vals = spec.values;
  const e = extent(vals);
  const padX = 3; const padY = 4;
  const showLast = o.width >= 150;
  const plot = { x: padX, y: padY, w: Math.max(10, o.width - padX * 2 - (showLast ? 40 : 0)), h: Math.max(8, o.height - padY * 2) };
  const n = Math.max(1, vals.length - 1);
  const xs = linearScale([0, n], [plot.x, plot.x + plot.w]);
  const dom: [number, number] = e ? (e[0] === e[1] ? [e[0] - 1, e[1] + 1] : [e[0], e[1]]) : [0, 1];
  const ys = linearScale(dom, [plot.y + plot.h, plot.y]);
  const color = 'var(--accent-text)';
  const nodes: VNode[] = [];
  const pts = vals.map((v, i) => ({ x: xs(i), y: v == null ? null : ys(v) }));
  if (spec.kind === 'bars') {
    const bw = Math.max(1, plot.w / vals.length - 1);
    vals.forEach((v, i) => {
      if (v == null) { nodes.push(h('rect', { x: round(xs(i) - bw / 2), y: plot.y + plot.h - 2, width: round(bw), height: 2, fill: INK2, fillOpacity: 0.6 })); return; }
      const y = ys(v); const y0 = ys(Math.max(dom[0], Math.min(0, dom[1])));
      nodes.push(h('rect', { x: round(xs(i) - bw / 2), y: round(Math.min(y, y0)), width: round(bw), height: round(Math.max(1, Math.abs(y0 - y))), fill: color }));
    });
  } else {
    nodes.push(h('path', { d: linePath(pts), fill: 'none', stroke: color, strokeWidth: 1.5, strokeLinejoin: 'round' }));
    if (e) {
      const imin = vals.findIndex((v) => v === e[0]); const imax = vals.findIndex((v) => v === e[1]);
      for (const i of new Set([imin, imax])) nodes.push(h('circle', { cx: round(xs(i)), cy: round(ys(vals[i] as number)), r: 2, fill: INK2 }));
    }
  }
  const li = (() => { for (let i = vals.length - 1; i >= 0; i--) if (vals[i] != null) return i; return -1; })();
  if (li >= 0 && spec.kind !== 'bars') nodes.push(h('circle', { cx: round(xs(li)), cy: round(ys(vals[li] as number)), r: 3, fill: color, stroke: SURFACE, strokeWidth: 1.5 }));
  if (showLast && li >= 0) nodes.push(text(o.width - 2, plot.y + plot.h / 2, formatValue(vals[li], null), { anchor: 'end', baseline: 'middle', fill: INK }));
  return {
    width: o.width, height: o.height, plot, nodes, hits: [], hitMode: 'x', xScale: null, fullDomain: null, domain: null, brushable: false, legend: [],
    table: { columns: ['index', spec.yLabel ?? 'value'], rows: vals.map((v, i) => [i, v]) },
  };
}
