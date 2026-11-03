/* The server's figure catalogue (GET /exports/charts)
 * and the requests this page makes against it. Pure: parsing, URL and
 * body building, and the rule for when a PNG falls back to a render in
 * this browser. */
import type { Dataset } from '../dataviewer/columns';
import { isGoodsClassColumn, type ChartView } from '../dataviewer/chartSpec';

export interface ServerFigure {
  id: string; title: string; type: string; dataset: string | null; chip: string; what: string | null; needs: string | null;
  state: string; reason: string | null;
}
export interface ServerCatalogue {
  runId: string | null; counter: string; themes: string[]; pngAvailable: boolean; pngReason: string | null; figures: ServerFigure[];
}

const ID = /^[A-Za-z0-9_.:-]{1,128}$/;
const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);

/** Only well-formed figures are kept; anything else in the list is dropped. */
export function parseChartCatalogue(doc: unknown): ServerCatalogue | null {
  const d = doc as Record<string, unknown> | null;
  if (!d || typeof d !== 'object' || !Array.isArray(d.charts)) return null;
  const figures: ServerFigure[] = [];
  for (const c of d.charts as unknown[]) {
    const o = c as Record<string, unknown>;
    if (!o || typeof o.id !== 'string' || !ID.test(o.id) || typeof o.state !== 'string') continue;
    figures.push({
      id: o.id, title: str(o.title) ?? o.id, type: str(o.type) ?? 'chart', dataset: str(o.dataset), chip: str(o.chip) ?? 'NOT SERVED',
      what: str(o.what), needs: str(o.needs), state: o.state, reason: str(o.reason),
    });
  }
  const png = (d.png ?? {}) as Record<string, unknown>;
  return {
    runId: str(d.run_id), counter: str(d.counter) ?? '', themes: Array.isArray(d.themes) ? (d.themes as unknown[]).filter((t): t is string => typeof t === 'string') : ['light', 'dark', 'auto'],
    pngAvailable: png.available === true, pngReason: str(png.reason), figures,
  };
}
export const figureReady = (f: ServerFigure): boolean => f.state === 'HAS DATA';

export type FigureExt = 'svg' | 'png' | 'csv' | 'json';
export interface FigureQuery { runId: string | null; simRunId: string | null; theme?: 'light' | 'dark' | 'auto'; scale?: number }
export function figurePath(id: string, ext: FigureExt, q: FigureQuery): string {
  const p = new URLSearchParams();
  if (q.runId) p.set('run_id', q.runId);
  if (q.simRunId) p.set('sim_run_id', q.simRunId);
  if ((ext === 'svg' || ext === 'png') && q.theme) p.set('theme', q.theme);
  if (ext === 'png' && q.scale) p.set('scale', String(Math.min(3, Math.max(1, Math.round(q.scale)))));
  const qs = p.toString();
  return `/exports/chart/${encodeURIComponent(id)}.${ext}${qs ? `?${qs}` : ''}`;
}
export const catalogueQuery = (runId: string | null, simRunId: string | null): string => {
  const p = new URLSearchParams({ spec: 'false' });
  if (runId) p.set('run_id', runId);
  if (simRunId) p.set('sim_run_id', simRunId);
  return `/exports/charts?${p.toString()}`;
};

/** A PNG the server cannot draw (no renderer 501, renderer failure 502,
 *  or a catalogue that says unavailable) is drawn here from the SVG. */
export const pngFallback = (code: string | null | undefined, catalogue?: Pick<ServerCatalogue, 'pngAvailable'> | null): boolean =>
  code === 'renderer_unavailable' || code === 'png_failed' || code === 'http_501' || code === 'http_502' || catalogue?.pngAvailable === false;

/* ── viewer view -> POST /exports/chart ─────────────────────────────── */
export type ServerTarget =
  | { t: 'figure'; id: string }
  | { t: 'post'; body: Record<string, unknown> }
  | { t: 'none'; reason: string };

/** Which server figure draws a dataset's default view. */
export const FIGURE_FOR_VIEW: Record<string, string> = { chain_events: 'cascade_timeline' };

/** What the viewer's current chart maps to on the server: the catalogue
 *  figure for the chain, otherwise a dataset-shaped body. The server draws
 *  the whole dataset (the viewer's filters do not travel) and refuses
 *  what the viewer also refuses: mixed unit classes, summed categories. */
export function serverTarget(ds: Dataset, view: ChartView, ctx: { runId: string | null; simRunId: string | null; rowIdx: readonly number[]; catalogueIds: readonly string[] }): ServerTarget {
  if (view.type === 'cascade') {
    const id = FIGURE_FOR_VIEW[ds.id];
    return id && ctx.catalogueIds.includes(id) ? { t: 'figure', id } : { t: 'none', reason: 'No server figure' };
  }
  const x = view.x != null ? ds.columns[view.x] : null;
  const y = view.y != null ? ds.columns[view.y] : null;
  if (!x || !y || y.type !== 'number') return { t: 'none', reason: 'Pick X and Y' };
  const series = view.series != null ? ds.columns[view.series] : null;
  const body: Record<string, unknown> = {
    dataset: ds.id, type: view.type === 'bar' && view.stacked && series && !isGoodsClassColumn(series) ? 'stacked_bar' : view.type, x: x.name, y: y.name,
    title: `${ds.id.replace(/_/g, ' ')} ${y.name.replace(/_/g, ' ')}`,
  };
  if (ctx.runId) body.run_id = ctx.runId;
  if (ctx.simRunId) body.sim_run_id = ctx.simRunId;
  if (series) body.series_by = series.name;
  if (body.type === 'stacked_bar') body.stack_class = y.unit ?? y.name;
  if (y.unitFrom != null) {
    const unitCol = ds.columns[y.unitFrom];
    const units = new Set(ctx.rowIdx.map((i) => ds.rows[i][unitCol.index]).filter((u) => u != null));
    if (units.size !== 1) return { t: 'none', reason: 'One class only' };
    body.where = { [unitCol.name]: [...units][0] };
  }
  return { t: 'post', body };
}

/** Pixel size of a stand-alone SVG from its root attributes (width / height, else viewBox). */
export function svgSize(svg: string): { width: number; height: number } | null {
  const root = /<svg\b[^>]*>/i.exec(svg)?.[0];
  if (!root) return null;
  const attr = (n: string) => new RegExp(`\\s${n}="([\\d.]+)(?:px)?"`, 'i').exec(root)?.[1];
  const w = Number(attr('width')); const h = Number(attr('height'));
  if (w > 0 && h > 0) return { width: w, height: h };
  const vb = /viewBox="([-\d.\s,]+)"/i.exec(root)?.[1]?.trim().split(/[\s,]+/).map(Number);
  return vb && vb.length === 4 && vb[2] > 0 && vb[3] > 0 ? { width: vb[2], height: vb[3] } : null;
}
