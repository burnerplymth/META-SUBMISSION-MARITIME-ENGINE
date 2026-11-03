/* Export registry types.
 * Pure types: no DOM, no React, no fetch. */
import type { ReportDoc } from './report/model';

export type ExportKind = 'chart' | 'table' | 'run' | 'plan' | 'document' | 'dataset' | 'media' | 'report';
export type ExportFormat = 'csv' | 'xlsx' | 'json' | 'svg' | 'png' | 'pdf' | 'html' | 'geojson' | 'md';
export type ExportLabel = 'Modelled' | 'Simulated' | 'Recorded' | 'Sourced' | 'Snapshot' | 'Live' | 'Not served';

export const FORMAT_LABEL: Readonly<Record<ExportFormat, string>> = {
  csv: 'CSV', xlsx: 'XLSX', json: 'JSON', svg: 'SVG', png: 'PNG', pdf: 'PDF', html: 'HTML', geojson: 'GeoJSON', md: 'Markdown',
};
export const FORMAT_EXT: Readonly<Record<ExportFormat, string>> = {
  csv: 'csv', xlsx: 'xlsx', json: 'json', svg: 'svg', png: 'png', pdf: 'pdf', html: 'html', geojson: 'geojson', md: 'md',
};
export const FORMAT_MIME: Readonly<Record<ExportFormat, string>> = {
  csv: 'text/csv;charset=utf-8',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  json: 'application/json',
  svg: 'image/svg+xml;charset=utf-8',
  png: 'image/png',
  pdf: 'application/pdf',
  html: 'text/html;charset=utf-8',
  geojson: 'application/geo+json',
  md: 'text/markdown;charset=utf-8',
};

export interface ExportProvenance {
  /** Standing label, shown as a chip in the preview and written into every file. */
  label: ExportLabel;
  /** Human source line, for example "Stored run record (causal chain)". */
  source: string;
  run_id?: string | null;
  sim_run_id?: string | null;
  /** Time of the data (never the export time). */
  as_of?: string | null;
  /** Stream hash or content hash of the underlying data, when known. */
  hash?: string | null;
  unit?: string | null;
}

export type ExportState = 'ready' | 'needs-run' | 'needs-sim-run' | 'empty' | 'not-served';
export const STATE_WORD: Readonly<Record<ExportState, string>> = {
  ready: 'Ready', 'needs-run': 'Needs run', 'needs-sim-run': 'Needs simulation run', empty: 'Empty', 'not-served': 'Not served',
};

export interface ExportDescriptor {
  /** One line, for example "32 events, 4 columns". */
  summary: string;
  rows?: number;
  columns?: string[];
  provenance: ExportProvenance;
  /** Anything but 'ready' disables selection. Default 'ready'. */
  state?: ExportState;
}

export type ExportPreview =
  | { kind: 'svg'; svg: string }
  | { kind: 'table'; columns: string[]; rows: Array<Array<string | number | null>>; total: number }
  | { kind: 'text'; lines: string[] }
  | { kind: 'image'; src: string; alt: string };

export interface ExportOptions {
  /** Include figures in documents and packs. */
  figures: boolean;
  /** XLSX Provenance sheet, JSON header object, HTML header block. */
  provenanceSheet: boolean;
  /** `# key: value` header lines at the top of CSV files. */
  csvHeader: boolean;
  /** Figure skin; light is the print skin. */
  theme: 'light' | 'dark';
  /** Tokens: {title} {kind} {window} {run} {date} {label}. */
  filenamePattern: string;
}

export const DEFAULT_FILENAME_PATTERN = 'supermap-{title}-{run}-{date}';
export const DEFAULT_OPTIONS: Readonly<ExportOptions> = {
  figures: true, provenanceSheet: true, csvHeader: true, theme: 'light', filenamePattern: DEFAULT_FILENAME_PATTERN,
};

export interface ExportFile { blob: Blob; filename: string; mime: string; format: ExportFormat }

export interface ExportableDef {
  /** Stable and unique: "<windowId>:<local id>", for example "chain:events". */
  id: string;
  /** Owning window (a WindowId). Groups the list and drives the per-panel menu. */
  windowId: string;
  /** Sentence-case noun phrase: no ids, no enum leftovers. */
  title: string;
  kind: ExportKind;
  formats: readonly ExportFormat[];
  /** Display group; defaults to the owning window's title. */
  group?: string;
  describe(): ExportDescriptor;
  /** Cheap and synchronous: whatever is already in memory. */
  preview?(): ExportPreview | null;
  /** Optional fetch of a richer preview (a served figure, the head of a dataset). Called when the row is focused. */
  loadPreview?(): Promise<ExportPreview | null>;
  build(format: ExportFormat, options: ExportOptions): Promise<ExportFile>;
  /** A run report (kind 'report'): the document behind the PDF and HTML formats, for a pack that joins several reports. */
  report?(options: ExportOptions): Promise<ReportDoc[]>;
  /** The browser's own Save as PDF (print dialog) for a run report. Resolves false when nothing could be started. */
  print?(options: ExportOptions): Promise<boolean>;
}

export type ExportErrorCode = 'not_served' | 'needs_run' | 'empty' | 'too_large' | 'failed' | 'unsupported';
export class ExportError extends Error {
  code: ExportErrorCode;
  constructor(code: ExportErrorCode, message?: string) { super(message ?? code); this.code = code; this.name = 'ExportError'; }
}
export const ERROR_WORD: Readonly<Record<ExportErrorCode, string>> = {
  not_served: 'Not served', needs_run: 'Needs run', empty: 'Empty', too_large: 'Too large', failed: 'Export failed', unsupported: 'Format not supported',
};

/** What the registry gives providers: the current run context. */
export interface ExportContext { runId: string | null; simRunId: string | null; asOf: string | null }

export interface ExportProvider {
  id: string;
  windowId: string;
  list(ctx: ExportContext): Promise<ExportableDef[]>;
}

export interface ExportResultRow {
  id: string; title: string; format: ExportFormat | 'pack'; filename: string; bytes: number; sha256: string; mime: string;
  /** for a pack: the member files with their own hashes */
  members?: Array<{ name: string; bytes: number; sha256: string }>;
  at: string;
}
