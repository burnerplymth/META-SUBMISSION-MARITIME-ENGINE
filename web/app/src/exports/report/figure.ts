/* Figures for the report: the shared chart kit's drawings, serialised as standalone vector SVG in the light skin.
 * A figure is never a raster. Chart builders in each window hand over VNode trees or chart-kit specs. */
import { buildAny } from '../../charts/build';
import { legendRow } from '../../charts/draw';
import type { ChartSpec } from '../../charts/types';
import { toSvgString, translate, type VNode } from '../../charts/vnode';
import type { FigureBlock, ReportChip } from './model';
import { lookupReportToken } from './tokens';

export interface DrawnNodes { nodes: VNode[]; width: number; height: number }

/** Standalone SVG of a VNode tree: tokens resolved to concrete light colours, no script, no external reference. */
export function nodesToSvg(c: DrawnNodes, label: string): string {
  const withBg: VNode[] = [{ t: 'rect', a: { x: 0, y: 0, width: c.width, height: c.height, fill: '#ffffff' }, c: [] }, ...c.nodes];
  return toSvgString(withBg, { width: c.width, height: c.height }, lookupReportToken, { role: 'img', 'aria-label': label });
}

export function figureFromNodes(title: string, c: DrawnNodes, o: { chips?: ReportChip[]; caption?: string } = {}): FigureBlock {
  return { t: 'figure', title, chips: o.chips, caption: o.caption, svg: nodesToSvg(c, title), width: c.width, height: c.height };
}

/** A chart-kit spec drawn at `width` with a plot height of `height` (no kit title or caption: the report supplies its own). */
export function figureFromSpec(spec: ChartSpec, width: number, height: number, o: { title?: string; chips?: ReportChip[]; caption?: string } = {}): FigureBlock {
  const m = buildAny(spec, { width, height });
  /* the screen draws the legend as buttons; a document draws it as a key row above the plot */
  const showKey = spec.type !== 'spark' && spec.type !== 'multiples' && m.legend.length > 1;
  const key = showKey ? legendRow(m.legend, 0, 9, m.width) : null;
  const nodes = key ? [...key.nodes, translate(0, key.height + 2, m.nodes)] : m.nodes;
  const h = key ? m.height + key.height + 2 : m.height;
  return figureFromNodes(o.title ?? spec.title, { nodes, width: m.width, height: h }, { chips: o.chips, caption: o.caption });
}
