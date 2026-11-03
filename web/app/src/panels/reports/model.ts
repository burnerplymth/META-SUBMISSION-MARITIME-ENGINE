/* Pure logic for the REPORT CENTRE and EXPORT CENTRE panels.
 * No DOM, no fetch, no React: everything here is pure and runs in a plain
 * node environment against the real example payloads of the ports and
 * exports API.
 *
 * Doctrine carried: no total across places, unit classes or goods
 * classes is ever computed here; sizes are labelled ESTIMATE until a
 * build returns the actual bytes; a missing value is null (rendered
 * NOT SERVED by the panel), never zero. */

/* ── provenance chips ─────────────────────────────────────────────── */
export type ChipTone = 'live' | 'snapshot' | 'sourced' | 'model' | 'sim' | 'down' | 'notserved' | 'meta' | 'test' | 'flag';

/** Which of the one `Status` forms a chip word takes: the
 *  mark is drawn by `ui/Status`, never typed, so there is no glyph table here. */

export function chipTone(chip: string): ChipTone {
  switch (chip.toUpperCase()) {
    case 'LIVE': case 'HAS DATA': case 'MATCH': return 'live';
    case 'SNAPSHOT': return 'snapshot';
    case 'SOURCED': return 'sourced';
    case 'MODELLED': case 'MODELED': case 'ESTIMATE': return 'model';
    case 'SIMULATED': return 'sim';
    case 'DOWN': return 'down';
    case 'FLAG': case 'CUT': return 'flag';
    case 'NOT SERVED': case 'NO DATA': case 'NEEDS RUN': case 'NEEDS SIM RUN': return 'notserved';
    default: return 'meta';
  }
}

/* ── bytes / hashes ───────────────────────────────────────────────── */
/** Size label: bytes below 1 KB, then KB (1 decimal below 100 KB, none
 *  above), then MB. 1 KB = 1024 B. Null / negative / non-finite is
 *  null so the caller prints NOT SERVED, never "0 B". */
export function formatBytes(n: number | null | undefined): string | null {
  if (n == null || !Number.isFinite(n) || n < 0) return null;
  if (n < 1024) return `${Math.round(n)} B`;
  const kb = n / 1024;
  if (kb < 1024) return kb < 100 ? `${kb.toFixed(1)} KB` : `${Math.round(kb)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

/** First `n` hex chars of a digest, then an ellipsis; the full digest
 *  stays in the title attribute. Null stays null. */
export function shortHash(h: string | null | undefined, n = 12): string | null {
  if (!h) return null;
  return h.length > n ? `${h.slice(0, n)}…` : h;
}

/* ── report templates + section selection ─────────────────────────── */
export type ReportFormat = 'html' | 'md' | 'pdf-print-view';

export interface FormatTab { id: ReportFormat; label: string; ext: string; mime: string }
export const FORMAT_TABS: readonly FormatTab[] = [
  { id: 'html', label: 'HTML', ext: 'html', mime: 'text/html' },
  { id: 'md', label: 'MD', ext: 'md', mime: 'text/markdown' },
  { id: 'pdf-print-view', label: 'PDF print view', ext: 'html', mime: 'text/html' },
];

export interface ReportSection {
  id: string; title: string; chip: string; locked: boolean; default_on: boolean; estimate_bytes: number;
}
export interface ReportTemplate {
  id: string; title: string; description?: string;
  caps?: Record<string, unknown>;
  sections: ReportSection[];
  estimate_bytes_default: number;
  estimate_note?: string;
}
export interface ReportPdfBlock { available: boolean; renderer?: string | null; reason?: string | null; print_view?: string }
export interface ReportTemplatesDoc {
  schema: string; formats: string[]; templates: ReportTemplate[]; pdf?: ReportPdfBlock; stats_basis?: string;
}

/** Contract check for GET /reports/templates: the envelope must carry
 *  at least one template whose sections are objects with an id. */
export function parseTemplates(body: unknown): ReportTemplatesDoc | null {
  const b = body as ReportTemplatesDoc | null;
  if (!b || typeof b !== 'object' || !Array.isArray(b.templates) || b.templates.length === 0) return null;
  for (const t of b.templates) {
    if (!t || typeof t.id !== 'string' || !Array.isArray(t.sections)) return null;
    for (const s of t.sections) if (!s || typeof s.id !== 'string') return null;
  }
  return b;
}

const registryOrder = (t: ReportTemplate): string[] => t.sections.map((s) => s.id);

/** Order a selection by registry order, drop ids the template does not
 *  have, force every locked section in. */
export function normaliseSelection(t: ReportTemplate, sel: Iterable<string>): string[] {
  const want = new Set(sel);
  return t.sections.filter((s) => s.locked || want.has(s.id)).map((s) => s.id);
}
export const defaultSelection = (t: ReportTemplate): string[] =>
  t.sections.filter((s) => s.locked || s.default_on).map((s) => s.id);
/** INCLUDE ALL. */
export const includeAll = (t: ReportTemplate): string[] => registryOrder(t);
/** LEAVE ALL OUT: the locked section stays (the report always carries
 *  its limits and caveats). */
export const leaveAllOut = (t: ReportTemplate): string[] => t.sections.filter((s) => s.locked).map((s) => s.id);
/** Toggle one section; a locked one cannot be toggled. */
export function toggleSection(t: ReportTemplate, sel: readonly string[], id: string): string[] {
  const sec = t.sections.find((s) => s.id === id);
  if (!sec || sec.locked) return normaliseSelection(t, sel);
  const has = sel.includes(id);
  return normaliseSelection(t, has ? sel.filter((x) => x !== id) : [...sel, id]);
}
export function sameSelection(t: ReportTemplate, a: readonly string[], b: readonly string[]): boolean {
  const x = normaliseSelection(t, a); const y = normaliseSelection(t, b);
  return x.length === y.length && x.every((v, i) => v === y[i]);
}

export interface SelectionCounter { included: number; total: number; label: string }
export function selectionCounter(t: ReportTemplate, sel: readonly string[]): SelectionCounter {
  const included = normaliseSelection(t, sel).length;
  const total = t.sections.length;
  return { included, total, label: `${included} of ${total} sections included` };
}

/** Per-document overhead: the default estimate minus the default
 *  sections' own estimates (document shell, styles, header). */
export function overheadBytes(t: ReportTemplate): number {
  const sum = t.sections.filter((s) => s.locked || s.default_on).reduce((a, s) => a + s.estimate_bytes, 0);
  return Math.max(0, t.estimate_bytes_default - sum);
}
/** HTML-size ESTIMATE for a selection. The default selection returns
 *  the server's own figure exactly; any other selection re-sums the
 *  sections over the same overhead. The build returns the actual. */
export function estimateBytes(t: ReportTemplate, sel: readonly string[]): number {
  const norm = normaliseSelection(t, sel);
  if (sameSelection(t, norm, defaultSelection(t))) return t.estimate_bytes_default;
  const chosen = new Set(norm);
  return overheadBytes(t) + t.sections.filter((s) => chosen.has(s.id)).reduce((a, s) => a + s.estimate_bytes, 0);
}
export function estimateLabel(t: ReportTemplate, sel: readonly string[]): string {
  return `≈ ${formatBytes(estimateBytes(t, sel))}`;
}

export interface BuildBody { run_id: string; template: string; sections: string[]; format: ReportFormat }
export function buildBody(runId: string, t: ReportTemplate, sel: readonly string[], format: ReportFormat): BuildBody {
  return { run_id: runId, template: t.id, sections: normaliseSelection(t, sel), format };
}

/** POST /reports/build envelope (default response). */
export interface BuildEnvelope {
  schema: string; generated_at: string;
  meta: {
    run_id: string; template: string; format: string; sections: string[];
    locked_sections_added: boolean; bytes: number; sha256: string; estimate_bytes?: number;
  };
  filename: string; media_type: string; content: string;
}
export function parseBuildEnvelope(body: unknown): BuildEnvelope | null {
  const b = body as BuildEnvelope | null;
  if (!b || typeof b !== 'object' || typeof b.content !== 'string' || !b.meta || typeof b.filename !== 'string') return null;
  return b;
}

/** Actual vs estimate, for the post-build line. The ratio is never
 *  shown as a claim of accuracy, only the two figures. */
export function buildSummary(env: BuildEnvelope): {
  bytes: string | null; estimate: string | null; sha: string | null; sections: number; lockedAdded: boolean;
} {
  return {
    bytes: formatBytes(env.meta.bytes),
    estimate: formatBytes(env.meta.estimate_bytes ?? null),
    sha: shortHash(env.meta.sha256),
    sections: env.meta.sections.length,
    lockedAdded: env.meta.locked_sections_added === true,
  };
}

/* ── honest error states ──────────────────────────────────────────── */
export interface StateLabel { word: string; chip: 'DOWN' | 'NOT SERVED' | 'INFO'; code: string }
/** Map an API failure (ApiError code or HTTP-ish code) to a one-word
 *  state; the panel prints the word, the chip and the machine code,
 *  never a sentence. */
export function failureState(code: string | undefined): StateLabel {
  const c = code ?? 'unknown';
  if (c === 'renderer_unavailable') return { word: 'PDF renderer not served', chip: 'NOT SERVED', code: c };
  if (c === 'pdf_failed') return { word: 'PDF render failed', chip: 'DOWN', code: c };
  if (c === 'run_not_found') return { word: 'Run not found', chip: 'NOT SERVED', code: c };
  if (c === 'unknown_sim_run') return { word: 'SIM run not found', chip: 'NOT SERVED', code: c };
  if (c === 'dataset_not_found') return { word: 'Dataset not found', chip: 'NOT SERVED', code: c };
  if (c === 'invalid_request') return { word: 'Request refused', chip: 'DOWN', code: c };
  if (c === 'network' || c === 'timeout') return { word: 'Down', chip: 'DOWN', code: c };
  if (c === 'http_404' || c === 'not_found') return { word: 'Not served', chip: 'NOT SERVED', code: c };
  return { word: 'Error', chip: 'DOWN', code: c };
}

/** The PDF download is only offered when the templates doc says the
 *  renderer is available; the print view is always available. */
export function pdfAvailability(pdf: ReportPdfBlock | undefined): { available: boolean; reason: string | null } {
  if (!pdf) return { available: false, reason: 'renderer state not served' };
  return { available: pdf.available === true, reason: pdf.available ? null : (pdf.reason ?? 'renderer unavailable') };
}

/** Download filename for a build in a given tab. */
export function reportFilename(env: BuildEnvelope | null, runId: string, t: ReportTemplate, f: ReportFormat): string {
  if (env?.filename) return env.filename;
  const tab = FORMAT_TABS.find((x) => x.id === f) ?? FORMAT_TABS[0];
  return `${runId}-${t.id}.${tab.ext}`;
}

/* ── export centre: catalogue ─────────────────────────────────────── */
export interface ExportDataset {
  id: string; label: string; description?: string; state: string; rows: number | null;
  provenance?: string; needs?: string | null; columns?: ExportColumn[]; filters?: string[]; csv?: string;
  /** optional catalogue stamps (served when the exports service provides them) */
  size_bytes?: number | null; updated_at?: string | null; formats?: string[];
}
export interface ExportColumn { name: string; unit?: string | null; header?: string }
export interface ExportCatalogue {
  schema?: string; run_id: string | null; sim_run_id: string | null;
  default_rows_cap: number; max_rows_cap: number; counter: string; datasets: ExportDataset[];
}
export function parseCatalogue(body: unknown): ExportCatalogue | null {
  const b = body as ExportCatalogue | null;
  if (!b || typeof b !== 'object' || !Array.isArray(b.datasets)) return null;
  for (const d of b.datasets) if (!d || typeof d.id !== 'string' || typeof d.state !== 'string') return null;
  return {
    ...b,
    default_rows_cap: Number.isFinite(b.default_rows_cap) ? b.default_rows_cap : 10000,
    max_rows_cap: Number.isFinite(b.max_rows_cap) ? b.max_rows_cap : 200000,
  };
}
export const hasData = (d: ExportDataset): boolean => d.state === 'HAS DATA';

/** Row-count label: a number with thousands separators, or null when
 *  the catalogue serves none (the panel prints NOT SERVED, not 0). */
export function rowsLabel(rows: number | null | undefined): string | null {
  if (rows == null || !Number.isFinite(rows)) return null;
  return Math.round(rows).toLocaleString('en-US');
}
/** "N of M datasets selected" for the pack checklist. */
export function packCounter(cat: ExportCatalogue, sel: readonly string[]): string {
  const avail = cat.datasets.filter(hasData).length;
  return `${sel.filter((id) => cat.datasets.some((d) => d.id === id && hasData(d))).length} of ${avail} datasets selected`;
}
export const defaultPackSelection = (cat: ExportCatalogue): string[] => cat.datasets.filter(hasData).map((d) => d.id);
export const packIncludeAll = defaultPackSelection;
export const packLeaveAllOut = (): string[] => [];
export function togglePack(cat: ExportCatalogue, sel: readonly string[], id: string): string[] {
  const d = cat.datasets.find((x) => x.id === id);
  if (!d || !hasData(d)) return cat.datasets.filter((x) => hasData(x) && sel.includes(x.id)).map((x) => x.id);
  const next = sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id];
  return cat.datasets.filter((x) => hasData(x) && next.includes(x.id)).map((x) => x.id);
}

/** Clamp a typed rows cap into 1..max; empty / garbage falls back to
 *  the catalogue default. Integers only. */
export function clampRowsCap(raw: string | number, cat: Pick<ExportCatalogue, 'default_rows_cap' | 'max_rows_cap'>): number {
  const n = typeof raw === 'number' ? raw : Number.parseInt(String(raw).replace(/[,\s_]/g, ''), 10);
  if (!Number.isFinite(n)) return cat.default_rows_cap;
  return Math.min(cat.max_rows_cap, Math.max(1, Math.trunc(n)));
}

/** Accept an ISO date (YYYY-MM-DD) or timestamp (YYYY-MM-DDTHH:MM:SSZ);
 *  anything else is invalid. '' is "no bound" (null). */
export type TimeBound = { ok: true; value: string | null } | { ok: false };
export function parseTimeBound(raw: string): TimeBound {
  const t = raw.trim();
  if (t === '') return { ok: true, value: null };
  if (!/^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$/.test(t)) return { ok: false };
  const probe = Date.parse(t.length === 10 ? `${t}T00:00:00Z` : t.replace(' ', 'T'));
  if (!Number.isFinite(probe)) return { ok: false };
  return { ok: true, value: t.replace(' ', 'T') };
}
/** from must not be after to (string compare is safe for ISO). */
export function boundsOrdered(from: string | null, to: string | null): boolean {
  if (!from || !to) return true;
  return Date.parse(from.length === 10 ? `${from}T00:00:00Z` : from) <= Date.parse(to.length === 10 ? `${to}T00:00:00Z` : to);
}
/** Does the catalogue say this dataset accepts the filter? */
export const supportsFilter = (d: ExportDataset, name: string): boolean => (d.filters ?? []).includes(name);

export interface CsvQuery {
  runId: string | null; simRunId: string | null; from: string | null; to: string | null; rowsCap: number | null;
}
/** Query string for GET /exports/dataset/{id}.csv; only filters the
 *  dataset declares are sent (a filter the dataset does not take would
 *  be a silent no-op, so it is not sent at all). */
export function csvPath(d: ExportDataset, q: CsvQuery): string {
  const p = new URLSearchParams();
  if (q.runId) p.set('run_id', q.runId);
  if (q.simRunId) p.set('sim_run_id', q.simRunId);
  if (q.from && supportsFilter(d, 'from')) p.set('from', q.from);
  if (q.to && supportsFilter(d, 'to')) p.set('to', q.to);
  if (q.rowsCap != null) p.set('rows_cap', String(q.rowsCap));
  const qs = p.toString();
  return `/exports/dataset/${encodeURIComponent(d.id)}.csv${qs ? `?${qs}` : ''}`;
}

export interface CsvResult { written: number | null; available: number | null; cut: number | null; provenance: string | null }
const hdrInt = (v: string | null): number | null => {
  if (v == null || v.trim() === '') return null;
  const n = Number.parseInt(v, 10);
  return Number.isFinite(n) ? n : null;
};
/** Read the X-Rows-* / X-Provenance headers of a CSV response. */
export function csvResultFromHeaders(get: (name: string) => string | null): CsvResult {
  return {
    written: hdrInt(get('x-rows-written')), available: hdrInt(get('x-rows-available')),
    cut: hdrInt(get('x-rows-cut')), provenance: get('x-provenance'),
  };
}

export interface PackRequest {
  runId: string | null; simRunId: string | null; datasets: readonly string[]; rowsCap: number;
  includeReport: boolean; template: string | null; from: string | null; to: string | null;
  /** pack options (omitted = the server default) */
  includeCharts?: boolean; includeXlsx?: boolean; includePng?: boolean; asOf?: string | null;
}
export function packBody(r: PackRequest): Record<string, unknown> {
  const body: Record<string, unknown> = { datasets: [...r.datasets], rows_cap: r.rowsCap, include_report: r.includeReport && !!r.runId };
  if (r.runId) body.run_id = r.runId;
  if (r.simRunId) body.sim_run_id = r.simRunId;
  if (r.includeReport && r.runId && r.template) body.template = r.template;
  const filters: Record<string, string> = {};
  if (r.from) filters.from = r.from;
  if (r.to) filters.to = r.to;
  if (Object.keys(filters).length) body.filters = filters;
  if (r.includeCharts !== undefined) body.include_charts = r.includeCharts;
  if (r.includeXlsx !== undefined) body.include_xlsx = r.includeXlsx;
  if (r.includePng !== undefined) body.include_png = r.includePng;
  if (r.asOf) body.as_of = r.asOf;
  return body;
}

/* ── export centre: manifest summary ──────────────────────────────── */
export interface ManifestDataset {
  id: string; label?: string; file: string; rows_available: number; rows_written: number; cut: number;
  bytes: number; sha256: string; provenance?: string; columns?: string[];
}
export interface PackManifest {
  pack?: string; schema?: string; run_id?: string | null; as_of?: string; generated_at?: string; rows_cap?: number;
  stream_hash?: string | null;
  graph?: { journal_graph_hash?: string; current_map_hash?: string; map_file_sha256?: string; map_version?: string };
  datasets: ManifestDataset[];
  cut?: Array<{ id: string; rows_cap: number; rows_cut: number }>;
  skipped?: Array<string | { id: string; reason?: string; state?: string }>;
  files?: Record<string, { bytes: number; sha256: string }>;
  report?: { file: string; template: string; sections: string[]; md_file?: string; bytes?: number } | null;
  /** exports-pack/2 */
  charts?: Array<{ id: string; title: string; type: string; chip: string; dataset?: string; points?: number; files?: Record<string, string> }>;
  charts_png?: { requested?: boolean; available?: boolean; reason?: string | null };
  workbook?: { file: string; bytes: number; sha256: string; sheets: string[] } | null;
  engine?: { engine_rev?: number | null; engine_label?: string | null };
  provenance?: { law?: string[]; chips?: string[] };
}

export interface ManifestDatasetRow {
  id: string; file: string; label: string; rowsWritten: number; rowsAvailable: number; cut: number; truncated: boolean;
  bytes: string | null; sha12: string | null; sha256: string; provenance: string | null;
}
export interface ManifestSummary {
  asOf: string | null; generatedAt: string | null; rowsCap: number | null; runId: string | null;
  streamHash: string | null; mapHash: string | null;
  datasets: ManifestDatasetRow[];
  /** Datasets where the rows cap removed rows; empty = nothing cut. */
  cutDatasets: Array<{ id: string; rowsCap: number; rowsCut: number }>;
  skipped: Array<{ id: string; reason: string | null }>;
  files: Array<{ name: string; bytes: string | null; sha12: string | null; sha256: string }>;
  report: { file: string; template: string; sections: number; mdFile: string | null } | null;
  schema: string | null;
  charts: Array<{ id: string; title: string; type: string; chip: string; points: number | null }>;
  workbook: { file: string; bytes: string | null; sheets: string[] } | null;
  /** requested / state of the PNG figures in the pack */
  png: 'NOT REQUESTED' | 'INCLUDED' | 'NOT SERVED';
}

/** Fold the pack's manifest.json into what the panel shows: per
 *  dataset the rows written / available / cut, short + full sha256,
 *  and what was cut. No cross-dataset row total is computed. */
export function summariseManifest(m: PackManifest): ManifestSummary {
  const datasets: ManifestDatasetRow[] = (m.datasets ?? []).map((d) => ({
    id: d.id, file: d.file, label: d.label ?? d.id,
    rowsWritten: d.rows_written, rowsAvailable: d.rows_available, cut: d.cut, truncated: d.cut > 0,
    bytes: formatBytes(d.bytes), sha12: shortHash(d.sha256), sha256: d.sha256, provenance: d.provenance ?? null,
  }));
  const cutDatasets = (m.cut ?? []).filter((c) => c.rows_cut > 0).map((c) => ({ id: c.id, rowsCap: c.rows_cap, rowsCut: c.rows_cut }));
  const skipped = (m.skipped ?? []).map((s) => (typeof s === 'string'
    ? { id: s, reason: null }
    : { id: s.id, reason: s.reason ?? s.state ?? null }));
  const files = Object.entries(m.files ?? {}).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([name, f]) => ({ name, bytes: formatBytes(f.bytes), sha12: shortHash(f.sha256), sha256: f.sha256 }));
  return {
    asOf: m.as_of ?? null, generatedAt: m.generated_at ?? null, rowsCap: m.rows_cap ?? null, runId: m.run_id ?? null,
    streamHash: m.stream_hash ?? null, mapHash: m.graph?.current_map_hash ?? null,
    datasets, cutDatasets, skipped, files,
    report: m.report ? { file: m.report.file, template: m.report.template, sections: m.report.sections.length, mdFile: m.report.md_file ?? null } : null,
    schema: m.schema ?? null,
    charts: (m.charts ?? []).map((c) => ({ id: c.id, title: c.title, type: c.type, chip: c.chip, points: c.points ?? null })),
    workbook: m.workbook ? { file: m.workbook.file, bytes: formatBytes(m.workbook.bytes), sheets: m.workbook.sheets } : null,
    png: !m.charts_png?.requested ? 'NOT REQUESTED' : m.charts_png.available === false ? 'NOT SERVED' : 'INCLUDED',
  };
}
