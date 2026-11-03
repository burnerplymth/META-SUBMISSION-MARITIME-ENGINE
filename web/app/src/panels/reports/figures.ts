/* Figures for the report and the snapshot page: the same chart specs
 * the viewer draws, serialised as inline SVG. A figure that cannot be
 * drawn (no rows, no numeric column) is listed as NOT SERVED, never
 * skipped silently. */
import { chartToSvg, STATIC_TOKENS, type FramedSvg } from '../../charts/serialize';
import type { ChartSpec } from '../../charts/types';
import { escXml, type VarLookup } from '../../charts/vnode';
import type { Dataset } from '../dataviewer/columns';
import { buildChartSpec, defaultView, type ProvIn } from '../dataviewer/chartSpec';
import printCss from './printCss.json';

/** The figure set of a run: the chain, its legs, impact per place. */
export const FIGURE_DATASETS = ['chain_events', 'legs', 'impact_per_place'] as const;
export type FigureDatasetId = typeof FIGURE_DATASETS[number];

export interface FigureResult { id: string; spec: ChartSpec | null; reason: string | null }

export function figureSpecs(sets: Partial<Record<string, Dataset>>, prov: (id: string) => ProvIn, ids: readonly string[] = FIGURE_DATASETS): FigureResult[] {
  return ids.map((id) => {
    const ds = sets[id];
    if (!ds) return { id, spec: null, reason: 'Not served' };
    const idx = ds.rows.map((_r, i) => i);
    const r = buildChartSpec({ ds, idx, view: defaultView(ds), prov: prov(id) });
    return { id, spec: r.spec, reason: r.reason };
  });
}

/** An SVG document as inline markup: no XML prolog. */
export const inlineSvg = (svg: string): string => svg.replace(/^<\?xml[^>]*\?>\s*/, '');

export interface FigureHtml { html: string; drawn: number; missing: Array<{ id: string; reason: string }> }

export function figuresHtml(results: readonly FigureResult[], lookup: VarLookup, width = 760): FigureHtml {
  const parts: string[] = []; const missing: Array<{ id: string; reason: string }> = [];
  for (const r of results) {
    if (!r.spec) { missing.push({ id: r.id, reason: r.reason ?? 'Not served' }); continue; }
    const f: FramedSvg = chartToSvg(r.spec, { width, chartHeight: r.spec.type === 'cascade' ? 280 : 240 }, lookup);
    parts.push(`<figure class="sm-fig" data-figure="${escXml(r.id)}">${inlineSvg(f.svg)}</figure>`);
  }
  for (const m of missing) parts.push(`<p class="sm-fig-ns" data-figure="${escXml(m.id)}">${escXml(m.id.toUpperCase())} · NOT SERVED · ${escXml(m.reason)}</p>`);
  const css = `<style>${printCss.figures}</style>`;
  return { html: `<section class="sm-figures" id="sm-figures" aria-label="Figures">${css}<h2>FIGURES</h2>${parts.join('')}</section>`, drawn: parts.length - missing.length, missing };
}

/** Put the figure section at the end of the report body. The report is
 *  otherwise untouched, so its own hash and sections stand. */
export function injectFigures(reportHtml: string, section: string): string {
  const i = reportHtml.toLowerCase().lastIndexOf('</body>');
  return i < 0 ? `${reportHtml}${section}` : `${reportHtml.slice(0, i)}${section}${reportHtml.slice(i)}`;
}

export const printLookup: VarLookup = (n) => STATIC_TOKENS.light[n];
