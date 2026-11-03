/* Exports of the Monte Carlo window. Every file carries the mandatory label and
 * the request: figures (SVG, PNG) as a caption under the drawing, CSV as two
 * extra columns on every row, the workbook as a Manifest sheet. The chart
 * exports run through the shared machinery (charts/serialize.ts, exportDom.ts,
 * the ExportMenu); the table exports are plain text and bytes built here. */
import { INK, INK2, SURFACE, text } from '../../charts/draw';
import { clipLabel, textWidth } from '../../charts/format';
import { liveLookup, svgToPng, type ExportKind, type ExportResult, type ExportSkin } from '../../charts/exportDom';
import { captionParts, csvEscape, exportFilename } from '../../charts/serialize';
import { sourceLineFor } from '../../sources/credits';
import type { ExportTable, Provenance } from '../../charts/types';
import { h, toSvgString, translate, type VNode } from '../../charts/vnode';
import { saveBlob } from '../reports/client';
import type { CompareDoc } from './compare';
import { deltaRow, pairedValues, pairedWorse, sharedMetrics } from './compare';
import { digestShort, MC_LABEL, metricLabel, requestLine } from './fmt';
import { inputLabel } from './inputs';
import { METRIC_ORDER, type McJob, type McSample } from './types';
import { buildXlsx, type Cell } from './xlsx';

const PAD = 12;

export interface ExportCtx {
  /** the server's label string; the mandatory one when the document carries none */
  label: string;
  /** one line naming the scenario, engine, N and seed (both jobs for a comparison) */
  request: string;
  runId: string | null;
  digest: string | null;
  recorded: boolean;
}

export function ctxOf(job: McJob, nameOf: (id: string) => string, recorded: boolean): ExportCtx {
  return { label: job.label || MC_LABEL, request: requestLine(job, nameOf), runId: job.job_id, digest: job.results?.runs_digest ?? null, recorded };
}
export function ctxOfPair(a: McJob, b: McJob, nameOf: (id: string) => string, recorded: boolean): ExportCtx {
  return {
    label: a.label || MC_LABEL, request: `A ${requestLine(a, nameOf)} | B ${requestLine(b, nameOf)}`, runId: `${a.job_id}_${b.job_id}`,
    digest: a.results?.runs_digest && b.results?.runs_digest ? `${digestShort(a.results.runs_digest)} ${digestShort(b.results.runs_digest)}` : null, recorded,
  };
}

export function provenanceOf(c: ExportCtx): Provenance {
  return { chip: c.recorded ? 'RECORDED' : 'SIMULATED', source: c.request, runId: c.runId, scope: c.digest ? `DIGEST ${c.digest.slice(0, 17)}` : null };
}

/** Break text on spaces into lines that fit `maxW` px. Never clips: the label must stay whole. */
export function wrapWords(s: string, maxW: number, size = 9): string[] {
  const lines: string[] = []; let cur = '';
  for (const w of s.split(/\s+/).filter(Boolean)) {
    const next = cur ? `${cur} ${w}` : w;
    if (cur && textWidth(next, size) > maxW) { lines.push(cur); cur = w; } else cur = next;
  }
  if (cur) lines.push(cur);
  return lines;
}

/** The drawing with its title, the provenance caption and the whole label, as one standalone tree. */
export function frameChart(title: string, chart: { nodes: VNode[]; width: number; height: number }, c: ExportCtx): { nodes: VNode[]; width: number; height: number } {
  const inner = chart.width; const width = inner + PAD * 2;
  const prov: string[] = []; { let cur = '';
    for (const p of captionParts(provenanceOf(c))) { const next = cur ? `${cur} · ${p}` : p; if (cur && textWidth(next, 9) > inner) { prov.push(cur); cur = p; } else cur = next; }
    if (cur) prov.push(cur); }
  const lines = [...prov.map((l) => clipLabel(l, inner)), ...wrapWords(c.label, inner)];
  const y0 = PAD + 12; const body: VNode[] = [];
  body.push(text(PAD, PAD + 8, clipLabel(title.toUpperCase(), inner, 11), { size: 11, fill: INK, weight: 600, tracking: 0.5 }));
  body.push(translate(PAD, y0 + 4, chart.nodes));
  const yCap = y0 + 4 + chart.height + 6;
  body.push(h('line', { x1: PAD, x2: width - PAD, y1: yCap, y2: yCap, stroke: 'var(--line)', strokeWidth: 1 }));
  lines.forEach((l, i) => body.push(text(PAD, yCap + 13 + i * 12, l, { fill: INK2 })));
  const height = yCap + 13 + lines.length * 12 + PAD - 4;
  return { nodes: [h('rect', { x: 0, y: 0, width, height, fill: SURFACE }), ...body], width, height };
}

/* ── tables as text ───────────────────────────────────────────────────── */

const csvLine = (cells: ReadonlyArray<string | number | null | undefined>): string => cells.map(csvEscape).join(',');

/** The file form: a `# Source:` line first, then the table. */
export const tableCsvFile = (t: ExportTable, c: ExportCtx): string => `# ${sourceLineFor(provenanceOf(c).source)}\n${tableCsv(t, c)}`;

/** A table with provenance, label and request on every row. */
export function tableCsv(t: ExportTable, c: ExportCtx): string {
  const prov = provenanceOf(c).chip;
  return `${[csvLine([...t.columns, 'provenance', 'label', 'request']), ...t.rows.map((r) => csvLine([...r, prov, c.label, c.request]))].join('\n')}\n`;
}
export function tableTsv(t: ExportTable, c: ExportCtx): string {
  const clean = (v: string | number | null | undefined) => (v == null ? '' : String(v).replace(/[\t\r\n]+/g, ' '));
  const prov = provenanceOf(c).chip;
  return `${[[...t.columns, 'provenance', 'label', 'request'].join('\t'), ...t.rows.map((r) => [...r, prov, c.label, c.request].map(clean).join('\t'))].join('\n')}\n`;
}

export function samplesTable(job: McJob, rows: readonly McSample[]): ExportTable {
  const inNames = job.inputs.map((i) => i.name);
  const outNames = [...new Set([...METRIC_ORDER.filter((k) => job.results?.metrics[k]), ...Object.keys(job.results?.metrics ?? {})])];
  return {
    columns: ['simulation', ...inNames, ...outNames, 'stream_hash'],
    rows: rows.map((r) => [r.i, ...inNames.map((k) => r.inputs[k] ?? null), ...outNames.map((k) => r.outputs[k] ?? null), r.stream_hash]),
  };
}

const ord = (k: string): number => { const i = METRIC_ORDER.indexOf(k); return i < 0 ? 99 : i; };
/** The summary table of a result: one row per metric. */
export function summaryTable(job: McJob): ExportTable {
  const m = job.results?.metrics ?? {};
  return {
    columns: ['metric', 'unit', 'n', 'mean', 'std', 'min', 'p05', 'p10', 'p25', 'p50', 'p75', 'p90', 'p95', 'max'],
    rows: Object.keys(m).sort((a, b) => ord(a) - ord(b)).map((k) => [k, m[k].unit, m[k].n, m[k].mean, m[k].std, m[k].min, m[k].p05, m[k].p10, m[k].p25, m[k].p50, m[k].p75, m[k].p90, m[k].p95, m[k].max]),
  };
}
export function placesTable(job: McJob): ExportTable {
  return {
    columns: ['place', 'name', 'p10_added_delay_d', 'p50_added_delay_d', 'p90_added_delay_d', 'affected_share', 'affected_k'],
    rows: (job.results?.per_place ?? []).map((p) => [p.place, p.name, p.added_delay_d.p10, p.added_delay_d.p50, p.added_delay_d.p90, p.affected_share, p.affected_k ?? null]),
  };
}

const manifestRows = (c: ExportCtx, job: McJob, extra: Array<[string, Cell]> = []): Cell[][] => [
  ['key', 'value'],
  ['label', c.label], ['request', c.request], ['provenance', provenanceOf(c).chip],
  ['engine', job.engine?.engine_label ?? `Rev ${job.request.engine_rev}${job.request.profile ? ` ${job.request.profile}` : ''}`],
  ['n', job.request.n], ['seed', Math.abs(job.request.seed)], ['job', job.job_id], ['runs_digest', job.results?.runs_digest ?? null],
  ['inputs', job.inputs.map((i) => `${inputLabel(i.name)} ${i.dist} ${i.low ?? ''} ${i.mode ?? ''} ${i.high ?? ''}`.replace(/\s+/g, ' ').trim()).join('; ')],
  ...extra,
];
const sheetOf = (name: string, t: ExportTable): { name: string; rows: Cell[][] } => ({ name, rows: [t.columns, ...t.rows] });

export function resultWorkbook(job: McJob, rows: readonly McSample[], c: ExportCtx): Uint8Array {
  return buildXlsx([
    { name: 'Manifest', rows: manifestRows(c, job) }, sheetOf('Summary', summaryTable(job)), sheetOf('Places', placesTable(job)), sheetOf('Samples', samplesTable(job, rows)),
  ]);
}

/* ── comparison tables ────────────────────────────────────────────────── */

export function compareTable(a: McJob, b: McJob, doc: CompareDoc | null, samplesA: readonly McSample[] | null, samplesB: readonly McSample[] | null, x: number): ExportTable {
  const keys = sharedMetrics(a, b, METRIC_ORDER);
  const paired = !!doc?.paired && !!samplesA && !!samplesB;
  return {
    columns: ['metric', 'unit', 'a_p10', 'a_p50', 'a_p90', 'b_p10', 'b_p50', 'b_p90', 'delta_p10', 'delta_p50', 'delta_p90', 'threshold_d', 'paired_k', 'paired_n', 'shift_ci_low', 'shift_ci_high', 'paired'],
    rows: keys.map((k) => {
      const d = deltaRow(k, a.results!.metrics[k], b.results!.metrics[k]);
      const p = paired && d.unit === 'd' ? (() => { const v = pairedValues(samplesA!, samplesB!, k); return pairedWorse(v.a, v.b, x); })() : null;
      const ci = doc?.metrics[k]?.shift_ci ?? null;
      return [k, d.unit, d.a.p10, d.a.p50, d.a.p90, d.b.p10, d.b.p50, d.b.p90, d.d10, d.d50, d.d90, p ? x : null, p ? p.k : null, p ? p.n : null, ci ? ci[0] : null, ci ? ci[1] : null, doc?.paired ? 'yes' : 'no'];
    }),
  };
}
export function compareWorkbook(a: McJob, b: McJob, doc: CompareDoc | null, samplesA: readonly McSample[] | null, samplesB: readonly McSample[] | null, x: number, c: ExportCtx): Uint8Array {
  return buildXlsx([
    { name: 'Manifest', rows: manifestRows(c, a, [['paired', doc?.paired ? 'yes' : 'no'], ['b_job', b.job_id], ['b_runs_digest', b.results?.runs_digest ?? null], ['b_seed', Math.abs(b.request.seed)], ['b_n', b.request.n]]) },
    sheetOf('Comparison', compareTable(a, b, doc, samplesA, samplesB, x)),
    sheetOf('Summary A', summaryTable(a)), sheetOf('Summary B', summaryTable(b)),
  ]);
}

/* ── runners (DOM) ────────────────────────────────────────────────────── */

export interface McExportInput { ctx: ExportCtx; title: string; kind: string; chart: { nodes: VNode[]; width: number; height: number; table: ExportTable } }
const nameOf = (i: McExportInput, ext: 'svg' | 'png' | 'csv', scale?: number) => exportFilename({ title: i.title, type: i.kind, runId: i.ctx.runId, ext, scale });

export async function runMcExport(kind: ExportKind, skin: ExportSkin, i: McExportInput): Promise<ExportResult> {
  try {
    if (kind === 'copy-link') return { ok: false, word: 'NO LINK' };
    if (kind === 'srv-svg' || kind === 'srv-png' || kind === 'srv-csv') return { ok: false, word: 'NOT SERVED' };
    const framed = frameChart(i.title, i.chart, i.ctx);
    const { lookup, done } = liveLookup(skin);
    let svg: string;
    try { svg = toSvgString(framed.nodes, framed, lookup, { role: 'img', 'aria-label': i.title }); } finally { done(); }
    if (kind === 'svg') { const file = nameOf(i, 'svg'); saveBlob(file, new Blob([svg], { type: 'image/svg+xml;charset=utf-8' })); return { ok: true, word: 'SAVED SVG', file }; }
    if (kind === 'png1' || kind === 'png2') {
      const scale = kind === 'png2' ? 2 : 1;
      const png = await svgToPng(svg, framed.width, framed.height, scale);
      const file = nameOf(i, 'png', scale); saveBlob(file, png); return { ok: true, word: `SAVED PNG ${scale}X`, file };
    }
    if (kind === 'csv') { const file = nameOf(i, 'csv'); saveBlob(file, new Blob([tableCsvFile(i.chart.table, i.ctx)], { type: 'text/csv;charset=utf-8' })); return { ok: true, word: 'SAVED CSV', file }; }
    if (kind === 'copy-data') { await navigator.clipboard.writeText(tableTsv(i.chart.table, i.ctx)); return { ok: true, word: 'DATA COPIED' }; }
    if (kind === 'copy-image') {
      const png = await svgToPng(svg, framed.width, framed.height, 2);
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': png })]);
      return { ok: true, word: 'IMAGE COPIED' };
    }
    return { ok: false, word: 'UNKNOWN' };
  } catch (e) {
    const name = (e as Error)?.name;
    return { ok: false, word: name === 'NotAllowedError' || name === 'SecurityError' ? 'COPY BLOCKED' : 'EXPORT FAILED' };
  }
}

export interface FileResult { ok: boolean; word: string; file?: string }
const mime = { csv: 'text/csv;charset=utf-8', xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' } as const;
const dl = (file: string, data: BlobPart, type: string): FileResult => { saveBlob(file, new Blob([data], { type })); return { ok: true, word: `Saved ${file.split('.').pop()?.toUpperCase()}`, file }; };

export function saveSamplesCsv(job: McJob, rows: readonly McSample[], c: ExportCtx): FileResult {
  return dl(exportFilename({ title: 'monte carlo samples', type: 'samples', runId: c.runId, ext: 'csv' }), tableCsvFile(samplesTable(job, rows), c), mime.csv);
}
export function saveWorkbook(job: McJob, rows: readonly McSample[], c: ExportCtx): FileResult {
  return dl(exportFilename({ title: 'monte carlo', type: 'workbook', runId: c.runId, ext: 'csv' }).replace(/\.csv$/, '.xlsx'), resultWorkbook(job, rows, c) as BlobPart, mime.xlsx);
}
export function saveCompareCsv(a: McJob, b: McJob, doc: CompareDoc | null, sa: readonly McSample[] | null, sb: readonly McSample[] | null, x: number, c: ExportCtx): FileResult {
  return dl(exportFilename({ title: 'monte carlo comparison', type: 'compare', runId: c.runId, ext: 'csv' }), tableCsvFile(compareTable(a, b, doc, sa, sb, x), c), mime.csv);
}
export function saveCompareWorkbook(a: McJob, b: McJob, doc: CompareDoc | null, sa: readonly McSample[] | null, sb: readonly McSample[] | null, x: number, c: ExportCtx): FileResult {
  return dl(exportFilename({ title: 'monte carlo comparison', type: 'compare', runId: c.runId, ext: 'csv' }).replace(/\.csv$/, '.xlsx'), compareWorkbook(a, b, doc, sa, sb, x, c) as BlobPart, mime.xlsx);
}

export { metricLabel };
