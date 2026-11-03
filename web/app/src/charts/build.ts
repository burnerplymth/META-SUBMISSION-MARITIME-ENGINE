/* One entry point: spec -> geometry. Small multiples compose the other
 * builders, one cell per facet (a unit class, a series group, a place),
 * each with its OWN value axis: a facet is never put on a shared axis
 * with another unit. */
import { buildBar } from './bar';
import { buildCascade } from './cascade';
import { INK2, text } from './draw';
import { clipLabel } from './format';
import { buildLine } from './line';
import { buildScatter } from './scatter';
import { buildSpark } from './spark';
import { extent, type Domain } from './scale';
import type { BuildOpts, ChartModel, ChartSpec, Hit, MultiplesSpec } from './types';
import { translate, type VNode } from './vnode';

const GAP = 14; const CAPTION_H = 16;

export function multiplesLayout(width: number, n: number, minCell: number): { cols: number; cellW: number } {
  const cols = Math.max(1, Math.min(n, Math.floor((width + GAP) / (minCell + GAP))));
  return { cols, cellW: Math.floor((width - GAP * (cols - 1)) / cols) };
}

export function buildMultiples(spec: MultiplesSpec, o: BuildOpts): ChartModel {
  const { cols, cellW } = multiplesLayout(o.width, spec.facets.length, spec.minCellWidth ?? 240);
  const cellH = spec.cellHeight ?? 150;
  let shared: Domain | null = null;
  if (spec.sharedX) {
    const xs: number[] = [];
    for (const f of spec.facets) if (f.spec.type !== 'bar') for (const s of f.spec.series) for (const p of s.points) xs.push(p.x);
    shared = extent(xs);
  }
  const nodes: VNode[] = []; const hits: Hit[] = []; const rows: ChartModel['table']['rows'] = [];
  let legend: ChartModel['legend'] = [];
  let colsOut: string[] = [];
  const cellHeights: number[] = [];
  spec.facets.forEach((f, i) => {
    const m = buildAny({ ...f.spec, id: `${spec.id}-${f.key}`.replace(/[^\w-]/g, '_') } as ChartSpec, { width: cellW, height: cellH, domain: shared ?? o.domain ?? null, hidden: o.hidden });
    cellHeights.push(m.height);
    const col = i % cols; const row = Math.floor(i / cols);
    const ox = col * (cellW + GAP);
    let oy = 0;
    for (let r = 0; r < row; r++) {
      const rowMax = Math.max(...cellHeights.slice(r * cols, r * cols + cols), cellH);
      oy += rowMax + CAPTION_H + GAP;
    }
    nodes.push(translate(ox, oy, [text(2, 10, clipLabel(f.label, cellW - 8), { fill: INK2, tracking: 0.4 }), translate(0, CAPTION_H, m.nodes)]));
    for (const hit of m.hits) hits.push({ ...hit, x: hit.x + ox, y: hit.y + oy + CAPTION_H, facet: f.key, box: hit.box && { ...hit.box, x: hit.box.x + ox, y: hit.box.y + oy + CAPTION_H }, rows: hit.rows });
    colsOut = ['facet', ...m.table.columns];
    for (const r of m.table.rows) rows.push([f.label, ...r]);
    if (legend.length === 0 || m.legend.length > legend.length) legend = m.legend;
  });
  const nrows = Math.ceil(spec.facets.length / cols);
  let height = 0;
  for (let r = 0; r < nrows; r++) height += Math.max(...cellHeights.slice(r * cols, r * cols + cols), cellH) + CAPTION_H + (r < nrows - 1 ? GAP : 0);
  return {
    width: o.width, height: Math.max(height, 40), plot: { x: 0, y: 0, w: o.width, h: height }, nodes, hits, hitMode: 'xy',
    xScale: null, fullDomain: null, domain: null, brushable: false, legend, table: { columns: colsOut, rows },
  };
}

export function buildAny(spec: ChartSpec, o: BuildOpts): ChartModel {
  switch (spec.type) {
    case 'line': return buildLine(spec, o);
    case 'bar': return buildBar(spec, o);
    case 'scatter': return buildScatter(spec, o);
    case 'cascade': return buildCascade(spec, o);
    case 'spark': return buildSpark(spec, o);
    case 'multiples': return buildMultiples(spec, o);
  }
}
