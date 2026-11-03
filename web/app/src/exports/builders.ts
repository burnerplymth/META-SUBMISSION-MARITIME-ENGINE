/* Builders shared by every exportable: turn rows, a
 * chart spec, text or a server route into an ExportFile that carries the
 * standard header block. Client-side only; no panel imports here. */
import { csvEscape, tableToCsv } from '../charts/serialize';
import { chartToSvg, captionText } from '../charts/serialize';
import { svgToPng } from '../charts/exportDom';
import { staticLookup } from '../charts/serialize';
import type { ChartSpec, ExportTable, Provenance } from '../charts/types';
import { CentreError, call } from '../panels/reports/client';
import { sha256Hex } from '../panels/reports/packVerify';
import { buildXlsx } from './xlsx';
import { dataSha256, escHtml, headerEntries, headerLine, headerObject, csvHeaderLines, shownEntries, type HeaderEntry } from './header';
import { fileNameFor } from './filename';
import {
  ExportError, FORMAT_MIME, type ExportFile, type ExportFormat, type ExportKind, type ExportLabel, type ExportOptions, type ExportProvenance,
} from './types';

/** What a builder needs to know about the item it is exporting. */
export interface ItemMeta { id: string; windowId: string; title: string; kind: ExportKind; prov: ExportProvenance }

export const itemName = (m: ItemMeta, format: ExportFormat, o: ExportOptions, suffix = ''): string =>
  fileNameFor(o.filenamePattern, { title: m.title, kind: m.kind, window: m.windowId, run: m.prov.run_id ?? m.prov.sim_run_id, date: m.prov.as_of, label: m.prov.label }, format, suffix);

export const mkFile = (m: ItemMeta, format: ExportFormat, o: ExportOptions, data: BlobPart, suffix = ''): ExportFile =>
  ({ blob: new Blob([data], { type: FORMAT_MIME[format] }), filename: itemName(m, format, o, suffix), mime: FORMAT_MIME[format], format });

/** Chart chip word -> the standing export label. A file outlives the moment it was made, so a LIVE chip is
 *  stamped Snapshot unless the caller has checked the feed is fresh now (`fresh`). */
export const labelOfChip = (chip: string | null | undefined): ExportLabel => labelOfFreshChip(chip, false);
export function labelOfFreshChip(chip: string | null | undefined, fresh: boolean): ExportLabel {
  switch ((chip ?? '').toUpperCase()) {
    case 'MODELLED': case 'MODELED': case 'ESTIMATE': case 'BACKTEST': return 'Modelled';
    case 'SIMULATED': return 'Simulated';
    case 'LEDGER': case 'RECORDED': return 'Recorded';
    case 'SOURCED': return 'Sourced';
    case 'LIVE': return fresh ? 'Live' : 'Snapshot';
    case 'NOT SERVED': case 'NO DATA': return 'Not served';
    default: return 'Snapshot';
  }
}
export const provOfChart = (p: Provenance): ExportProvenance => ({
  label: labelOfChip(p.chip), source: p.source ?? 'Not stated', run_id: p.runId ?? null, as_of: p.asOf ?? null,
});

type Cell = string | number | null;

/* ── rows ───────────────────────────────────────────────────────────── */
const cellOut = (v: unknown): Cell => (v == null ? null : typeof v === 'number' ? (Number.isFinite(v) ? v : null) : typeof v === 'boolean' ? String(v) : typeof v === 'object' ? JSON.stringify(v) : String(v));

/** Rows from objects; the column set is the union of keys in first-seen order. */
export function objectsToTable(rows: ReadonlyArray<Record<string, unknown>>): { columns: string[]; rows: Cell[][] } {
  const columns: string[] = [];
  const seen = new Set<string>();
  for (const r of rows) for (const k of Object.keys(r)) if (!seen.has(k)) { seen.add(k); columns.push(k); }
  return { columns, rows: rows.map((r) => columns.map((c) => cellOut(r[c]))) };
}

export function rowsToCsv(columns: readonly string[], rows: ReadonlyArray<ReadonlyArray<unknown>>, label: string, header?: readonly HeaderEntry[]): string {
  const lines = [[...columns, 'label'].map((c) => csvEscape(c)).join(',')];
  for (const r of rows) lines.push([...columns.map((_, i) => cellOut(r[i])), label].map((c) => csvEscape(c as string | number | null)).join(','));
  return `${header?.length ? csvHeaderLines(header) : ''}${lines.join('\n')}\n`;
}

const PAGE_CSS = ':root{color-scheme:light dark;--bg:#fff;--ink:#14181f;--ink2:#566070;--line:#d7dce3;--chip:#eef1f5}'
  + '@media(prefers-color-scheme:dark){:root{--bg:#10141a;--ink:#e6e9ee;--ink2:#9aa4b2;--line:#2a313b;--chip:#1b212a}}'
  + '@media print{:root{--bg:#fff;--ink:#14181f;--ink2:#566070;--line:#d7dce3;--chip:#eef1f5}}'
  + 'body{margin:0;padding:24px 32px;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,sans-serif;font-variant-numeric:tabular-nums}'
  + 'h1{font-size:20px;margin:0 0 4px}.chip{display:inline-block;padding:1px 8px;border:1px solid var(--line);background:var(--chip);border-radius:3px;font-size:12px}'
  + 'table{border-collapse:collapse;margin:12px 0;font-size:13px}th,td{border:1px solid var(--line);padding:3px 8px;text-align:left;vertical-align:top}'
  + 'th{background:var(--chip)}td.n{text-align:right}.hdr th{width:140px;font-weight:600}.cap{color:var(--ink2);font-size:12px;margin:6px 0 14px}svg{max-width:100%;height:auto}';

export const headerTableHtml = (entries: readonly HeaderEntry[]): string =>
  `<table class="hdr" aria-label="Export information">${entries.map(([k, v]) => `<tr><th scope="row">${escHtml(k)}</th><td>${escHtml(v)}</td></tr>`).join('')}</table>`;

export function htmlPage(title: string, body: string): string {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escHtml(title)}</title><style>${PAGE_CSS}</style></head><body>${body}</body></html>\n`;
}

/** An HTML page lists at most this many rows; CSV, XLSX and JSON keep every row. */
export const HTML_MAX_ROWS = 5000;

export function tableHtml(m: ItemMeta, entries: readonly HeaderEntry[], columns: readonly string[], rows: ReadonlyArray<ReadonlyArray<Cell>>, o: ExportOptions, svg?: string): string {
  const head = `<h1>${escHtml(m.title)}</h1><div><span class="chip">${escHtml(m.prov.label)}</span></div>`;
  const hdr = headerTableHtml(shownEntries(o.provenanceSheet, entries));
  const fig = svg && o.figures ? `<figure>${svg.replace(/^<\?xml[^>]*\?>\s*/, '')}<figcaption class="cap">${escHtml(headerLine(entries))}</figcaption></figure>` : '';
  const shown = rows.length > HTML_MAX_ROWS ? rows.slice(0, HTML_MAX_ROWS) : rows;
  const tbl = `<table><thead><tr>${columns.map((c) => `<th scope="col">${escHtml(c)}</th>`).join('')}</tr></thead><tbody>`
    + shown.map((r) => `<tr>${columns.map((_, i) => { const v = r[i]; return `<td${typeof v === 'number' ? ' class="n"' : ''}>${v == null ? '' : escHtml(String(v))}</td>`; }).join('')}</tr>`).join('') + '</tbody></table>'
    + (shown.length < rows.length ? `<p class="cap">${`First ${shown.length.toLocaleString('en-US')} of ${rows.length.toLocaleString('en-US')} rows.`}</p>` : '');
  return htmlPage(m.title, head + hdr + fig + tbl);
}

export interface RowsOpts { note?: string | null; svg?: string; sheet?: string }

/** A table as CSV, XLSX, JSON or HTML, each carrying the header block. */
export async function rowsFile(m: ItemMeta, format: ExportFormat, columns: readonly string[], rows: ReadonlyArray<ReadonlyArray<unknown>>, o: ExportOptions, opts: RowsOpts = {}): Promise<ExportFile> {
  const cells = rows.map((r) => columns.map((_, i) => cellOut(r[i])));
  const dh = await dataSha256(columns, cells);
  const entries = headerEntries({ title: m.title, kind: m.kind, windowId: m.windowId, prov: m.prov, rows: rows.length, columns: columns.length, dataHash: dh, note: opts.note });
  switch (format) {
    case 'csv': return mkFile(m, 'csv', o, rowsToCsv(columns, cells, m.prov.label, shownEntries(o.csvHeader, entries)));
    case 'xlsx': {
      const bytes = buildXlsx({ columns: [...columns, 'label'], rows: cells.map((r) => [...r, m.prov.label]), name: opts.sheet ?? 'Data', header: shownEntries(o.provenanceSheet, entries) });
      return mkFile(m, 'xlsx', o, bytes as BlobPart);
    }
    case 'json': {
      const env = { export: headerObject(shownEntries(o.provenanceSheet, entries)), label: m.prov.label, columns, rows: cells.map((r) => Object.fromEntries(columns.map((c, i) => [c, r[i]]))) };
      return mkFile(m, 'json', o, `${JSON.stringify(env, null, 2)}\n`);
    }
    case 'html': return mkFile(m, 'html', o, tableHtml(m, entries, columns, cells, o, opts.svg));
    default: throw new ExportError('unsupported');
  }
}

/** Arbitrary JSON data inside the export envelope. */
export async function jsonFile(m: ItemMeta, data: unknown, o: ExportOptions, note?: string | null): Promise<ExportFile> {
  const body = JSON.stringify(data ?? null);
  const entries = headerEntries({ title: m.title, kind: m.kind, windowId: m.windowId, prov: m.prov, dataHash: await sha256Hex(new TextEncoder().encode(body)), note });
  return mkFile(m, 'json', o, `${JSON.stringify({ export: headerObject(shownEntries(o.provenanceSheet, entries)), label: m.prov.label, data }, null, 2)}\n`);
}

export function textFile(m: ItemMeta, format: ExportFormat, text: string, o: ExportOptions, suffix = ''): ExportFile {
  return mkFile(m, format, o, text, suffix);
}

/* ── charts ─────────────────────────────────────────────────────────── */
export interface ChartFrameOpt { width?: number; height?: number }

/** The chart as drawn by the shared chart kit: standalone SVG with title, units, chip, source, run and as-of in its caption. */
export function chartSvg(spec: ChartSpec, o: ExportOptions, frame: ChartFrameOpt = {}): { svg: string; width: number; height: number; table: ExportTable } {
  const r = chartToSvg(spec, { width: frame.width ?? 960, chartHeight: frame.height ?? 320 }, staticLookup(o.theme));
  return { svg: r.svg, width: r.width, height: r.height, table: r.model.table };
}

/** A chart as SVG, PNG, CSV (the plotted data), JSON (the spec) or an HTML page with the figure, its header block and its data. */
export async function chartFile(m: ItemMeta, format: ExportFormat, spec: ChartSpec, o: ExportOptions, frame: ChartFrameOpt = {}): Promise<ExportFile> {
  const r = chartSvg(spec, o, frame);
  const prov = provOfChart(spec.provenance);
  const meta: ItemMeta = { ...m, prov: { ...prov, ...m.prov, unit: m.prov.unit ?? spec.yUnit ?? null } };
  const dh = await dataSha256(r.table.columns, r.table.rows);
  const entries = headerEntries({ title: m.title, kind: 'chart', windowId: m.windowId, prov: meta.prov, rows: r.table.rows.length, columns: r.table.columns.length, dataHash: dh, note: captionText(spec.provenance) });
  switch (format) {
    case 'svg': return mkFile(meta, 'svg', o, r.svg);
    case 'png': return mkFile(meta, 'png', o, await svgToPng(r.svg, r.width, r.height, 2));
    case 'csv': {
      const csv = tableToCsv(r.table, spec.provenance);
      return mkFile(meta, 'csv', o, `${csvHeaderLines(shownEntries(o.csvHeader, entries))}${csv}`);
    }
    case 'json': return mkFile(meta, 'json', o, `${JSON.stringify({ export: headerObject(shownEntries(o.provenanceSheet, entries)), label: meta.prov.label, spec }, null, 2)}\n`);
    case 'xlsx': return mkFile(meta, 'xlsx', o, buildXlsx({ columns: [...r.table.columns, 'label'], rows: r.table.rows.map((row) => [...row, meta.prov.label]), name: 'Data', header: shownEntries(o.provenanceSheet, entries) }) as BlobPart);
    case 'html': return mkFile(meta, 'html', o, tableHtml(meta, entries, r.table.columns, r.table.rows, { ...o, figures: true }, r.svg));
    default: throw new ExportError('unsupported');
  }
}

/* ── server routes ──────────────────────────────────────────────────── */
export function exportErrorOf(e: unknown): ExportError {
  if (e instanceof ExportError) return e;
  const code = (e as CentreError | undefined)?.code ?? '';
  if (code === 'renderer_unavailable' || code === 'http_501') return new ExportError('unsupported', code);
  if (code === 'invalid_request' || code === 'http_422') return new ExportError('needs_run', code);
  if (code === 'run_not_found' || code === 'no_data' || code === 'not_found' || code === 'not_served' || code === 'dataset_not_found' || code === 'chart_not_found' || code === 'http_404') return new ExportError('not_served', code);
  if (code === 'rate_limited' || code === 'http_429') return new ExportError('failed', 'rate_limited');
  return new ExportError('failed', code || 'failed');
}

const dispositionName = (res: Response): string | null => /filename="?([^";]+)"?/.exec(res.headers.get('content-disposition') ?? '')?.[1] ?? null;

/** One file from a server builder. The file name follows the pattern; the served name is the fallback.
 *  A served CSV gets the header block (from the response headers) when `csvHeader` is on. */
export async function serverFile(m: ItemMeta, format: ExportFormat, path: string, o: ExportOptions, init?: { method?: 'GET' | 'POST'; body?: unknown; timeoutMs?: number }): Promise<ExportFile> {
  let res: Response;
  try { res = await call(path, { method: init?.method ?? 'GET', body: init?.body, timeoutMs: init?.timeoutMs ?? 90000 }); } catch (e) { throw exportErrorOf(e); }
  if (format === 'csv') {
    const text = await res.text();
    const bytes = new TextEncoder().encode(text);
    const entries = headerEntries({
      title: m.title, kind: m.kind, windowId: m.windowId, prov: m.prov,
      rows: Number.parseInt(res.headers.get('x-rows-written') ?? '', 10) || null, dataHash: await sha256Hex(bytes),
      note: res.headers.get('x-provenance') ? `Served provenance ${res.headers.get('x-provenance')}` : null,
    });
    return mkFile(m, 'csv', o, csvHeaderLines(shownEntries(o.csvHeader, entries)) + text);
  }
  const blob = await res.blob();
  const f = mkFile(m, format, o, blob);
  return { ...f, blob: new Blob([blob], { type: FORMAT_MIME[format] }), filename: f.filename || dispositionName(res) || `${m.id}.${format}` };
}

/** sha256 of a Blob. */
export async function blobSha256(b: Blob): Promise<string> { return sha256Hex(new Uint8Array(await b.arrayBuffer())); }
