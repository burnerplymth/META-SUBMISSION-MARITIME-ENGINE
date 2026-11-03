/* Bar chart: single, grouped, or stacked WITHIN one class. A stack is
 * never totalled (no sum label anywhere), and a missing value draws a
 * hatched NOT SERVED stub, never a zero-length bar. */
import { AXIS, GRID, INK, INK2, INK3, SURFACE, hatchDef, text, yAxis } from './draw';
import { axisTitle, CHART_FS, clipLabel, formatTick, formatValue, textWidth } from './format';
import { legendOf } from './line';
import { bandScale, extent, linearScale, niceStep, niceTicks, valueDomain } from './scale';
import type { BarSpec, BuildOpts, ChartModel, ExportTable, Hit, Rect } from './types';
import { h, round, type VNode } from './vnode';

export const ROW_H = 22;

/** Horizontal unless asked otherwise: category labels read left to right. */
export const isHorizontal = (s: BarSpec): boolean => s.horizontal ?? true;

/** Content height for a bar spec at the given series count. */
export function barHeight(spec: BarSpec, hiddenCount = 0): number {
  const n = spec.categories.length;
  const k = Math.max(1, spec.series.length - hiddenCount);
  if (!isHorizontal(spec)) return 220;
  const per = spec.mode === 'grouped' ? Math.max(ROW_H, k * 12 + 8) : ROW_H;
  return 20 + n * per + 28;
}

export function buildBar(spec: BarSpec, o: BuildOpts): ChartModel {
  const legend = legendOf(spec.series);
  const colorOf = new Map(legend.map((l) => [l.key, l.color]));
  const shown = spec.series.filter((s) => !o.hidden?.has(s.key));
  const cats = spec.categories;
  const horiz = isHorizontal(spec);
  const stacked = spec.mode === 'stacked' && shown.length > 1;
  const height = horiz ? barHeight(spec, spec.series.length - shown.length) : o.height;

  // value extents: stacked -> per-category positive / negative sums; else raw
  const vals = (ci: number) => shown.map((s) => s.values[ci]);
  let lo = 0; let hi = 0;
  cats.forEach((_c, ci) => {
    if (stacked) {
      const pos = vals(ci).reduce<number>((a, v) => (v != null && v > 0 ? a + v : a), 0);
      const neg = vals(ci).reduce<number>((a, v) => (v != null && v < 0 ? a + v : a), 0);
      hi = Math.max(hi, pos); lo = Math.min(lo, neg);
    } else {
      const e = extent(vals(ci)); if (e) { hi = Math.max(hi, e[1]); lo = Math.min(lo, e[0]); }
    }
  });
  const dom = valueDomain([lo, hi], { zero: true });
  const step = niceStep(dom[1] - dom[0], horiz ? 4 : 5);
  const ticks = niceTicks(dom[0], dom[1], horiz ? 4 : 5);

  const labelFull = cats.map((c) => c.label);
  const labW = horiz ? Math.min(Math.max(...labelFull.map((l) => textWidth(clipLabel(l, 170))), 30) + 10, o.width * 0.38) : 0;
  const valLabelRoom = 46;
  const plot: Rect = horiz
    ? { x: labW + 6, y: 20, w: Math.max(40, o.width - labW - 6 - valLabelRoom), h: height - 20 - 28 }
    : { x: 44, y: 20, w: Math.max(40, o.width - 44 - 10), h: height - 20 - 30 };
  const vs = horiz ? linearScale(dom, [plot.x, plot.x + plot.w]) : linearScale(dom, [plot.y + plot.h, plot.y]);
  const band = bandScale(cats.map((c) => c.key), horiz ? [plot.y, plot.y + plot.h] : [plot.x, plot.x + plot.w], 0.28);

  const nodes: VNode[] = [h('defs', null, hatchDef(`ns-${spec.id}`))];
  const yTitle = axisTitle(spec.yLabel, spec.yUnit);
  if (yTitle) nodes.push(text(horiz ? plot.x : 4, 11, clipLabel(yTitle, o.width - 8), { fill: INK2, tracking: 0.4 }));
  if (spec.scope) nodes.push(text(o.width - 4, 11, clipLabel(`Within ${spec.scope}`, o.width / 2), { fill: INK2, anchor: 'end', tracking: 0.4 }));

  // value axis
  if (horiz) {
    nodes.push(h('line', { x1: plot.x, x2: plot.x, y1: plot.y, y2: plot.y + plot.h, stroke: AXIS, strokeWidth: 1 }));
    for (const t of ticks) {
      const x = round(vs(t));
      nodes.push(h('line', { x1: x, x2: x, y1: plot.y, y2: plot.y + plot.h, stroke: GRID, strokeWidth: 1, strokeOpacity: 0.7 }));
      nodes.push(text(x, plot.y + plot.h + 14, formatTick(t, step), { anchor: 'middle' }));
    }
  } else {
    nodes.push(...yAxis(ticks, vs, plot, step));
  }
  const zero = round(vs(0));
  nodes.push(horiz
    ? h('line', { x1: zero, x2: zero, y1: plot.y, y2: plot.y + plot.h, stroke: AXIS, strokeWidth: 1 })
    : h('line', { x1: plot.x, x2: plot.x + plot.w, y1: zero, y2: zero, stroke: AXIS, strokeWidth: 1 }));

  const hits: Hit[] = [];
  const unit = spec.yUnit;
  cats.forEach((c, ci) => {
    const b0 = band(c.key); const bw = band.bandwidth;
    // category label
    if (horiz) nodes.push(text(plot.x - 6, b0 + bw / 2, clipLabel(c.label, labW - 8), { anchor: 'end', baseline: 'middle', fill: INK }));
    else nodes.push(text(b0 + bw / 2, plot.y + plot.h + 14, clipLabel(c.label, band.step - 2), { anchor: 'middle', fill: INK }));
    let pos = 0; let neg = 0;
    let nsWord = false; /* one NOT SERVED word per category row: stacked slots of a few pixels each overprinted it */
    shown.forEach((s, si) => {
      const v = s.values[ci];
      const color = colorOf.get(s.key) ?? INK;
      const slots = spec.colorBy && !stacked ? Math.max(1, shown.filter((q) => !Number.isNaN(q.values[ci])).length) : shown.length;
      const slot = spec.colorBy && !stacked ? shown.slice(0, si).filter((q) => !Number.isNaN(q.values[ci])).length : si;
      const sub = stacked ? bw : bw / slots;
      const off = stacked ? 0 : slot * sub;
      const along = b0 + off;
      let v0 = 0; let v1 = 0;
      if (v != null) {
        if (stacked) { if (v >= 0) { v0 = pos; pos += v; v1 = pos; } else { v0 = neg; neg += v; v1 = neg; } } else { v0 = 0; v1 = v; }
      }
      if (v != null && Number.isNaN(v)) return; // no row for this pair: nothing to draw, nothing to say
      if (v == null) {
        // NOT SERVED stub
        if (horiz) {
          nodes.push(h('rect', { x: zero, y: round(along), width: 40, height: round(Math.max(2, sub - 1)), fill: `url(#ns-${spec.id})`, stroke: INK3, strokeWidth: 0.75 }));
          if (!nsWord) { nsWord = true; nodes.push(text(zero + 46, sub < CHART_FS + 3 ? b0 + bw / 2 : along + sub / 2, 'NOT SERVED', { fill: INK2, baseline: 'middle', size: CHART_FS })); }
        } else {
          nodes.push(h('rect', { x: round(along), y: zero - 24, width: round(Math.max(2, sub - 1)), height: 24, fill: `url(#ns-${spec.id})`, stroke: INK3, strokeWidth: 0.75 }));
        }
      } else {
        const a = vs(v0); const bpx = vs(v1);
        const rx = horiz ? Math.min(a, bpx) : round(along); const ry = horiz ? round(along) : Math.min(a, bpx);
        const rw = horiz ? Math.max(1, Math.abs(bpx - a) - (stacked ? 1 : 0)) : round(Math.max(1, sub - (shown.length > 1 ? 1 : 0)));
        const rh = horiz ? round(Math.max(1, sub - (shown.length > 1 ? 1 : 0))) : Math.max(1, Math.abs(bpx - a) - (stacked ? 1 : 0));
        nodes.push(h('rect', { x: round(rx), y: round(ry), width: round(rw), height: round(rh), fill: color, stroke: stacked ? SURFACE : undefined, strokeWidth: stacked ? 1 : undefined }));
        // direct value label: never a total for a stack
        const showVal = !stacked && cats.length * shown.length <= 36;
        if (showVal) {
          const lbl = formatValue(v, null);
          if (horiz) {
            const endX = v >= 0 ? rx + rw : rx;
            nodes.push(text(v >= 0 ? endX + 4 : endX - 4, ry + rh / 2, lbl, { fill: INK, baseline: 'middle', anchor: v >= 0 ? 'start' : 'end' }));
          } else {
            nodes.push(text(rx + rw / 2, v >= 0 ? ry - 4 : ry + rh + 10, lbl, { fill: INK, anchor: 'middle' }));
          }
        } else if (stacked && (horiz ? rw : rh) > 34) {
          nodes.push(text(rx + rw / 2, ry + rh / 2, formatValue(v, null), { fill: INK, anchor: 'middle', baseline: 'middle', size: CHART_FS }));
        }
      }
      const cx = horiz ? (v == null ? zero : vs(v1)) : along + sub / 2;
      const cy = horiz ? along + sub / 2 : (v == null ? zero : vs(v1));
      hits.push({
        x: round(cx), y: round(cy), seriesKey: s.key, seriesLabel: s.label, color, placeId: c.placeId,
        box: horiz ? { x: plot.x, y: along, w: plot.w, h: sub } : { x: along, y: plot.y, w: sub, h: plot.h },
        title: c.label,
        rows: [{ k: shown.length > 1 ? s.label : (spec.yLabel ?? 'Value'), v: formatValue(v, unit) }],
      });
    });
  });

  const table: ExportTable = {
    columns: ['category', 'series', `${spec.yLabel ?? 'value'}${unit ? ` [${unit}]` : ''}`],
    rows: cats.flatMap((c, ci) => shown.filter((s) => !Number.isNaN(s.values[ci])).map((s) => [c.label, s.label, s.values[ci]] as Array<string | number | null>)),
  };
  return {
    width: o.width, height, plot, nodes, hits, hitMode: 'xy', xScale: null,
    fullDomain: null, domain: null, brushable: false, legend, table,
  };
}
