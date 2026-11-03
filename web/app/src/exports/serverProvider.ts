/* The server catalogue as exportables: run datasets, run figures and run
 * reports. Not tied to an open panel, so it is a provider. Builders are the
 * existing server routes. */
import { svgToPng } from '../charts/exportDom';
import { sentenceCase } from '../copy';
import { fetchDataset } from '../panels/dataviewer/dataClient';
import { call, fetchCatalogue, fetchTemplates } from '../panels/reports/client';
import { datasetJson } from '../panels/reports/exportPlan';
import { csvPath, defaultSelection, type ExportDataset, type ReportTemplate } from '../panels/reports/model';
import { catalogueQuery, figurePath, figureReady, parseChartCatalogue, pngFallback, svgSize, type ServerFigure } from '../panels/reports/serverCharts';
import { formatTimeFull } from '../charts/scale';
import { count } from './header';
import { exportErrorOf, labelOfChip, mkFile, rowsFile, serverFile, type ItemMeta } from './builders';
import { registerExportProvider } from './registry';
import { ExportError, type ExportContext, type ExportDescriptor, type ExportFile, type ExportFormat, type ExportOptions, type ExportPreview, type ExportState, type ExportableDef } from './types';

const stateOf = (s: string): ExportState => {
  const u = s.toUpperCase();
  if (u === 'HAS DATA') return 'ready';
  if (u === 'NEEDS RUN') return 'needs-run';
  if (u === 'NEEDS SIM RUN') return 'needs-sim-run';
  if (u === 'NO DATA') return 'empty';
  return 'not-served';
};

const firstWord = (p: string | undefined): string => (p ?? '').split(/[\s,/;]+/)[0];
const cap = 10000;

function datasetDef(d: ExportDataset, ctx: ExportContext): ExportableDef {
  const prov = { label: labelOfChip(firstWord(d.provenance)), source: d.provenance ? `Export dataset ${d.label}` : `Export dataset ${d.label}`, run_id: ctx.runId, sim_run_id: ctx.simRunId, as_of: ctx.asOf };
  const meta: ItemMeta = { id: `exports:ds-${d.id}`, windowId: 'exports', title: sentenceCase(d.label), kind: 'dataset', prov };
  const q = { runId: ctx.runId, simRunId: ctx.simRunId, from: null, to: null, rowsCap: cap };
  return {
    id: meta.id, windowId: 'exports', group: 'Run datasets', title: meta.title, kind: 'dataset', formats: ['csv', 'xlsx', 'json', 'html'],
    describe: (): ExportDescriptor => ({
      summary: `${d.rows != null ? count(d.rows, 'row') : 'Rows not served'}, ${count(d.columns?.length ?? 0, 'column')}`,
      rows: d.rows ?? undefined, columns: d.columns?.map((c) => (c.unit ? `${c.name} [${c.unit}]` : c.name)), provenance: prov, state: stateOf(d.state),
    }),
    loadPreview: async (): Promise<ExportPreview | null> => {
      try {
        const r = await fetchDataset(d, { ...q, rowsCap: 8 });
        return { kind: 'table', columns: r.ds.columns.filter((c) => !c.aux).map((c) => c.header), rows: r.ds.rows.map((row) => r.ds.columns.filter((c) => !c.aux).map((c) => row[c.index] as string | number | null)), total: r.result.available ?? r.ds.rows.length };
      } catch { return null; }
    },
    build: async (f: ExportFormat, o: ExportOptions): Promise<ExportFile> => {
      if (f === 'csv') return serverFile(meta, 'csv', csvPath(d, q), o);
      if (f === 'xlsx') return serverFile(meta, 'xlsx', csvPath(d, q).replace(/\.csv/, '.xlsx'), o);
      try {
        const r = await fetchDataset(d, q);
        if (f === 'json') {
          const j = datasetJson(r.ds, { runId: ctx.runId, simRunId: ctx.simRunId, provenance: d.provenance ?? r.result.provenance, fetchedAt: ctx.asOf ?? '', available: r.result.available });
          return rowsFile(meta, 'json', j.columns.map((c) => c.name), j.rows.map((row) => j.columns.map((c) => row[c.name])), o);
        }
        if (f === 'html') {
          return rowsFile(meta, 'html', r.ds.columns.map((c) => c.header), r.ds.rows.map((row) => r.ds.columns.map((c) => (c.type === 'time' && typeof row[c.index] === 'number' ? formatTimeFull(row[c.index] as number) : row[c.index]))), o);
        }
      } catch (e) { throw exportErrorOf(e); }
      throw new ExportError('unsupported');
    },
  };
}

async function figureSvgText(f: ServerFigure, ctx: ExportContext, theme: 'light' | 'dark'): Promise<string> {
  const res = await call(figurePath(f.id, 'svg', { runId: ctx.runId, simRunId: ctx.simRunId, theme }), { timeoutMs: 60000 });
  return res.text();
}

function figureDef(f: ServerFigure, ctx: ExportContext, pngAvailable: boolean): ExportableDef {
  const prov = { label: labelOfChip(f.chip), source: f.what ?? 'Export figure', run_id: ctx.runId, sim_run_id: ctx.simRunId, as_of: ctx.asOf };
  const meta: ItemMeta = { id: `exports:fig-${f.id}`, windowId: 'exports', title: sentenceCase(f.title), kind: 'chart', prov };
  const q = { runId: ctx.runId, simRunId: ctx.simRunId };
  return {
    id: meta.id, windowId: 'exports', group: 'Run figures', title: meta.title, kind: 'chart', formats: ['svg', 'png', 'csv', 'json'],
    describe: () => ({ summary: sentenceCase(f.type.replace(/_/g, ' ')), provenance: prov, state: figureReady(f) ? 'ready' : stateOf(f.state) }),
    loadPreview: async () => { try { return { kind: 'svg', svg: await figureSvgText(f, ctx, 'light') }; } catch { return null; } },
    build: async (fmt, o) => {
      if (fmt === 'png') {
        try {
          if (!pngAvailable) throw new ExportError('unsupported');
          return await serverFile(meta, 'png', figurePath(f.id, 'png', { ...q, theme: o.theme, scale: 2 }), o);
        } catch (e) {
          const err = exportErrorOf(e);
          if (!(err.code === 'unsupported' || pngFallback(err.message))) throw err;
          const svg = await figureSvgText(f, ctx, o.theme);
          const size = svgSize(svg);
          if (!size) throw new ExportError('failed');
          return mkFile(meta, 'png', o, await svgToPng(svg, size.width, size.height, 2));
        }
      }
      if (fmt === 'svg' || fmt === 'csv' || fmt === 'json') return serverFile(meta, fmt, figurePath(f.id, fmt, { ...q, theme: o.theme }), o);
      throw new ExportError('unsupported');
    },
  };
}

function reportDef(t: ReportTemplate, ctx: ExportContext, pdf: boolean): ExportableDef {
  const prov = { label: 'Modelled' as const, source: 'Stored run record and run datasets', run_id: ctx.runId, as_of: ctx.asOf };
  const meta: ItemMeta = { id: `exports:report-${t.id}`, windowId: 'reports', title: sentenceCase(t.title), kind: 'document', prov };
  const sections = defaultSelection(t);
  return {
    id: meta.id, windowId: 'reports', group: 'Run reports', title: meta.title, kind: 'document', formats: pdf ? ['html', 'md', 'pdf'] : ['html', 'md'],
    describe: () => ({ summary: `${count(sections.length, 'section')}${t.estimate_bytes_default ? `, about ${Math.round(t.estimate_bytes_default / 1024)} KB` : ''}`, provenance: prov, state: ctx.runId ? 'ready' : 'needs-run' }),
    build: async (f, o) => {
      if (!ctx.runId) throw new ExportError('needs_run');
      if (f === 'pdf') return serverFile(meta, 'pdf', '/reports/pdf', o, { method: 'POST', body: { run_id: ctx.runId, template: t.id, sections }, timeoutMs: 120000 });
      if (f === 'html' || f === 'md') return serverFile(meta, f, '/reports/build', o, { method: 'POST', body: { run_id: ctx.runId, template: t.id, sections, format: f, as: 'raw' } });
      throw new ExportError('unsupported');
    },
  };
}

/** Lists the run's datasets, figures and reports. A feed that is down contributes nothing. */
export async function listServerExportables(ctx: ExportContext): Promise<ExportableDef[]> {
  const out: ExportableDef[] = [];
  const [cat, figs, tpls] = await Promise.all([
    fetchCatalogue(ctx.runId, ctx.simRunId).catch(() => null),
    call(catalogueQuery(ctx.runId, ctx.simRunId), { timeoutMs: 20000 }).then((r) => r.json()).then(parseChartCatalogue).catch(() => null),
    ctx.runId ? fetchTemplates(ctx.runId).catch(() => null) : Promise.resolve(null),
  ]);
  if (cat) for (const d of cat.datasets) out.push(datasetDef(d, ctx));
  if (figs) for (const f of figs.figures) out.push(figureDef(f, ctx, figs.pngAvailable));
  if (tpls) for (const t of tpls.templates) out.push(reportDef(t, ctx, tpls.pdf?.available === true));
  return out;
}

export const SERVER_PROVIDER_ID = 'server-catalogue';
let registered = false;
/** Idempotent; called by the Export window. */
export function ensureServerProvider(): void {
  if (registered) return;
  registered = true;
  registerExportProvider({ id: SERVER_PROVIDER_ID, windowId: 'exports', list: listServerExportables });
}
