/* Export page planning: dataset search, mini-preview values, the live
 * pack size ESTIMATE, build progress words. Pure. A size is always an
 * estimate until a build returns the real bytes; an unknown size is
 * unknown (the page says NOT SERVED), never zero. */
import type { Dataset } from '../dataviewer/columns';
import { defaultView } from '../dataviewer/chartSpec';
import { formatBytes } from '../../charts/format';
import { formatTimeFull } from '../../charts/scale';
import { hasData, type ExportCatalogue, type ExportDataset } from './model';

/* ── catalogue search ───────────────────────────────────────────────── */
/** Every word typed must appear in the id, label, description, state or provenance. */
export function searchDatasets(list: readonly ExportDataset[], query: string): ExportDataset[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [...list];
  return list.filter((d) => {
    const hay = `${d.id} ${d.label} ${d.description ?? ''} ${d.state} ${d.provenance ?? ''}`.toLowerCase().replace(/_/g, ' ');
    return words.every((w) => hay.includes(w.replace(/_/g, ' ')));
  });
}

/* ── previews ───────────────────────────────────────────────────────── */
export interface PreviewInfo {
  sampleRows: number; bytesPerRow: number | null; columns: number;
  /** first values of the dataset's default y column, in row order */
  spark: Array<number | null>; sparkLabel: string | null; sparkKind: 'line' | 'bars';
}
export const PREVIEW_ROWS = 60;

export function previewFromDataset(ds: Dataset, text: string): PreviewInfo {
  const v = defaultView(ds);
  const served = (i: number) => ds.rows.slice(0, PREVIEW_ROWS).some((r) => r[i] != null);
  // the default y column when the sample serves it, else the first numeric column that has values
  const y = (v.y != null && ds.columns[v.y].type === 'number' && served(v.y) ? ds.columns[v.y] : null)
    ?? ds.columns.find((c) => c.type === 'number' && !c.aux && served(c.index)) ?? null;
  const values = y ? ds.rows.slice(0, PREVIEW_ROWS).map((r) => r[y.index] as number | null) : [];
  const head = text.indexOf('\n') + 1;
  const bpr = ds.rows.length > 0 ? Math.max(1, (text.length - head) / ds.rows.length) : null;
  return {
    sampleRows: ds.rows.length, bytesPerRow: bpr, columns: ds.columns.length, spark: values,
    sparkLabel: y ? `${y.name}${y.unit && y.unit !== 'UTC' ? ` [${y.unit}]` : ''}` : null,
    sparkKind: v.type === 'line' ? 'line' : 'bars',
  };
}

/* ── pack estimate ──────────────────────────────────────────────────── */
export interface PackEstimate {
  bytes: number | null;
  /** datasets with no sample yet: their size is not in the figure */
  unknown: string[];
  parts: Array<{ id: string; rows: number | null; bytes: number | null }>;
  reportBytes: number | null;
}
export function estimatePack(cat: ExportCatalogue, selected: readonly string[], previews: Readonly<Record<string, PreviewInfo | undefined>>, rowsCap: number, report: { include: boolean; bytes: number | null }): PackEstimate {
  const parts: PackEstimate['parts'] = []; const unknown: string[] = [];
  let sum = 0; let known = 0;
  for (const id of selected) {
    const d = cat.datasets.find((x) => x.id === id);
    if (!d || !hasData(d)) continue;
    const rows = d.rows == null ? null : Math.min(d.rows, rowsCap);
    const bpr = previews[id]?.bytesPerRow ?? null;
    const bytes = rows != null && bpr != null ? Math.round(rows * bpr) : null;
    parts.push({ id, rows, bytes });
    if (bytes == null) unknown.push(id); else { sum += bytes; known++; }
  }
  const rb = report.include ? report.bytes : null;
  if (rb != null) { sum += rb; known++; }
  return { bytes: known > 0 ? sum : null, unknown, parts, reportBytes: rb };
}
export const estimateLabel = (e: PackEstimate): string => {
  const b = formatBytes(e.bytes);
  return b == null ? 'Not served' : `${e.unknown.length ? '≥ ' : '≈ '}${b}`;
};

/* ── build progress ─────────────────────────────────────────────────── */
export type PackPhase = 'idle' | 'building' | 'reading' | 'done' | 'failed';
export interface Stage { id: PackPhase; label: string; state: 'todo' | 'now' | 'done' | 'failed' }
const ORDER: ReadonlyArray<{ id: PackPhase; label: string }> = [
  { id: 'building', label: 'Building ZIP' }, { id: 'reading', label: 'Reading manifest' }, { id: 'done', label: 'Done' },
];
export function packStages(phase: PackPhase): Stage[] {
  const at = ORDER.findIndex((s) => s.id === phase);
  return ORDER.map((s, i) => ({
    ...s,
    state: phase === 'failed' ? (i === 0 ? 'failed' : 'todo') : phase === 'done' ? 'done' : at < 0 ? 'todo' : i < at ? 'done' : i === at ? 'now' : 'todo',
  }));
}

/* ── time words ─────────────────────────────────────────────────────── */
export const stampShort = (iso: string | null | undefined): string | null => (iso ? `${iso.slice(0, 10)} ${iso.slice(11, 16)}Z` : null);

/* ── JSON download of one dataset (client side, from the CSV rows) ──── */
export interface DatasetJson {
  schema: 'supermap-dataset/1'; dataset: string; run_id: string | null; sim_run_id: string | null; provenance: string | null;
  fetched_at: string; rows_written: number; rows_available: number | null;
  columns: Array<{ name: string; unit: string | null; unit_from: string | null; type: string }>;
  rows: Array<Record<string, number | string | null>>;
}
/** Absent cells stay null (never 0); times are UTC strings; numbers stay numbers. */
export function datasetJson(ds: Dataset, ctx: { runId: string | null; simRunId: string | null; provenance: string | null; fetchedAt: string; available: number | null }): DatasetJson {
  return {
    schema: 'supermap-dataset/1', dataset: ds.id, run_id: ctx.runId, sim_run_id: ctx.simRunId, provenance: ctx.provenance, fetched_at: ctx.fetchedAt,
    rows_written: ds.rows.length, rows_available: ctx.available,
    columns: ds.columns.map((c) => ({ name: c.name, unit: c.unit, unit_from: c.unitFrom != null ? ds.columns[c.unitFrom].name : null, type: c.type })),
    rows: ds.rows.map((r) => Object.fromEntries(ds.columns.map((c) => [c.name, r[c.index] == null ? null : c.type === 'time' ? formatTimeFull(r[c.index] as number) : (r[c.index] as number | string)]))),
  };
}
