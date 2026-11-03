/* Per-panel export: the rows the window shows, as CSV or JSON, and the server's export URL. */
import { API_BASE } from '../../api';
import { DESCRIPTIVE_LABEL } from './types';

export type ExportFormat = 'csv' | 'json' | 'xlsx';
export interface Table { columns: string[]; rows: Array<Array<string | number | null>> }

/** One guard for every cell: a leading = + - @ tab or CR gets a quote (spreadsheet formula injection). */
export function guardCell(v: string | number | null): string {
  if (v === null) return '';
  const s = String(v);
  return /^[=+\-@\t\r]/.test(s) ? `'${s}` : s;
}
const csvCell = (v: string | number | null): string => { const s = guardCell(v); return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };

export function tableCsv(t: Table, manifest: string[] = []): string {
  const head = [`# ${DESCRIPTIVE_LABEL}`, ...manifest.map((m) => `# ${m.replace(/[\r\n]+/g, ' ')}`)];
  return [...head, t.columns.map(csvCell).join(','), ...t.rows.map((r) => r.map(csvCell).join(','))].join('\n') + '\n';
}
export function tableJson(t: Table, manifest: Record<string, unknown> = {}): string {
  return JSON.stringify({ label: DESCRIPTIVE_LABEL, manifest, columns: t.columns, rows: t.rows.map((r) => r.map((c) => (typeof c === 'string' ? guardCell(c) : c))) }, null, 1);
}

export function exportUrl(assemblyId: string, fmt: ExportFormat): string {
  return `${API_BASE}/research/export?assembly_id=${encodeURIComponent(assemblyId)}&format=${fmt}`;
}

export function download(name: string, mime: string, body: string | Blob): void {
  const blob = body instanceof Blob ? body : new Blob([body], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name; a.rel = 'noopener';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
