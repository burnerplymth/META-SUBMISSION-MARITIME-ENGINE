/* Run reports in the export registry. A window describes its report once (a function that builds ReportDoc values);
 * `reportExportable` turns that into a registry entry with a PDF format (one-click file, vector, no print dialog), an
 * HTML format, and a "Save as PDF" print fallback that uses the browser's own print helper. */
import { mkFile, type ItemMeta } from '../builders';
import { exportClock } from '../header';
import { ExportError, type ExportDescriptor, type ExportFile, type ExportFormat, type ExportOptions, type ExportPreview, type ExportProvenance, type ExportState, type ExportableDef } from '../types';
import type { ReportDoc } from './model';

export interface ReportSource {
  /** "<window>:report" */
  id: string;
  windowId: string;
  title: string;
  prov: ExportProvenance;
  /** one line for the list, for example "12 shipments, 3 charts" */
  summary: string;
  /** section names, shown as the preview */
  outline: readonly string[];
  state?: ExportState;
  /** the documents; more than one for a bundle of reports */
  docs: (o: ExportOptions) => Promise<ReportDoc[]>;
}

export const REPORT_FORMATS: readonly ExportFormat[] = ['pdf', 'html'];

export function reportExportable(src: ReportSource): ExportableDef {
  const meta: ItemMeta = { id: src.id, windowId: src.windowId, title: src.title, kind: 'report', prov: src.prov };
  const load = async (o: ExportOptions): Promise<ReportDoc[]> => {
    let docs: ReportDoc[];
    try { docs = await src.docs(o); } catch (e) { throw e instanceof ExportError ? e : new ExportError('failed'); }
    if (!docs.length) throw new ExportError('empty');
    return docs;
  };
  return {
    id: src.id, windowId: src.windowId, title: src.title, kind: 'report', formats: REPORT_FORMATS,
    describe: (): ExportDescriptor => ({ summary: src.summary, provenance: src.prov, state: src.state ?? 'ready' }),
    preview: (): ExportPreview => ({ kind: 'text', lines: [...src.outline] }),
    report: load,
    build: async (f: ExportFormat, o: ExportOptions): Promise<ExportFile> => {
      const docs = await load(o);
      if (f === 'html') {
        const [{ reportsToHtml }, { loadHtmlFontCss }] = await Promise.all([import('./html'), import('./fonts')]);
        const fontCss = await loadHtmlFontCss().catch(() => '');
        return mkFile(meta, 'html', o, reportsToHtml(docs, { fontCss }));
      }
      if (f === 'pdf') {
        try {
          const { renderReportsPdf } = await import('./render');
          const r = await renderReportsPdf(docs);
          return mkFile(meta, 'pdf', o, r.blob);
        } catch (e) { throw e instanceof ExportError ? e : new ExportError('failed'); }
      }
      throw new ExportError('unsupported');
    },
    print: async (o: ExportOptions): Promise<boolean> => {
      const docs = await load(o);
      const { printReports } = await import('./render');
      return printReports(docs);
    },
  };
}

/** One PDF holding the reports of several items, with a contents page. */
export async function buildPackPdf(items: readonly ExportableDef[], o: ExportOptions, packTitle = 'Run pack'): Promise<{ blob: Blob; pages: number; reports: number }> {
  const docs: ReportDoc[] = [];
  const seen = new Set<string>();
  for (const it of items) {
    if (!it.report) continue;
    try {
      for (const d of await it.report(o)) {
        const key = `${d.id}|${d.runId ?? ''}|${d.simRunId ?? ''}`;
        if (!seen.has(key)) { seen.add(key); docs.push(d); }
      }
    } catch { /* an item that cannot be built is left out of the pack */ }
  }
  if (!docs.length) throw new ExportError('empty');
  const { renderReportsPdf } = await import('./render');
  const r = await renderReportsPdf(docs, { packTitle });
  return { blob: r.blob, pages: r.pages, reports: docs.length };
}

export const packFileName = (runId: string | null, date?: string | null): string =>
  `supermap-run-pack-${(runId ?? 'no-run').replace(/[^A-Za-z0-9_-]+/g, '-').slice(0, 24)}-${(date ?? exportClock.now()).slice(0, 10).replace(/-/g, '')}.pdf`;
