/* Thin fetch layer for the report centre, export centre and
 * satellite desk. Lives beside the panels so the shared api.ts stays
 * untouched; it reuses API_BASE and speaks the house error shape
 * {"error": {"code", "message"}}. Binary and header-bearing responses
 * (CSV, zip, pdf) need the raw Response, which api.ts's JSON-only
 * req() does not expose. */
import { API_BASE } from '../../api';
import {
  csvResultFromHeaders, parseBuildEnvelope, parseCatalogue, parseTemplates,
  type BuildBody, type BuildEnvelope, type CsvResult, type ExportCatalogue, type ReportTemplatesDoc,
} from './model';

export class CentreError extends Error {
  code: string; status: number;
  constructor(code: string, message: string, status = 0) { super(message); this.code = code; this.status = status; }
}

export interface CallOpts { method?: 'GET' | 'POST'; body?: unknown; headers?: Record<string, string>; timeoutMs?: number; signal?: AbortSignal }

/** One request with a timeout. Resolves with the Response for 2xx;
 *  rejects with a CentreError carrying the served error code (or
 *  http_<status>, timeout, network, aborted). */
export async function call(path: string, opts: CallOpts = {}): Promise<Response> {
  const { method = 'GET', body, headers = {}, timeoutMs = 30000, signal } = opts;
  const ctrl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; ctrl.abort(); }, timeoutMs);
  if (signal) {
    if (signal.aborted) ctrl.abort();
    else signal.addEventListener('abort', () => ctrl.abort(), { once: true });
  }
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method, signal: ctrl.signal,
      headers: body !== undefined ? { 'Content-Type': 'application/json', ...headers } : headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    if (timedOut) throw new CentreError('timeout', `timed out after ${timeoutMs} ms`);
    if (signal?.aborted) throw new CentreError('aborted', 'aborted');
    throw new CentreError('network', 'service unreachable');
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    const j = (await res.json().catch(() => null)) as { error?: { code?: string; message?: string } } | null;
    throw new CentreError(j?.error?.code ?? `http_${res.status}`, j?.error?.message ?? `HTTP ${res.status}`, res.status);
  }
  return res;
}

export async function getJson<T>(path: string, opts: CallOpts = {}): Promise<T> {
  const res = await call(path, opts);
  const j = (await res.json().catch(() => null)) as T | null;
  if (j === null) throw new CentreError('bad_body', 'response was not JSON', res.status);
  return j;
}

/** Hand a Blob to the browser as a download. */
export function saveBlob(name: string, blob: Blob): void {
  const a = document.createElement('a');
  const url = URL.createObjectURL(blob);
  a.href = url; a.download = name; a.rel = 'noopener';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

/* ── reports ─────────────────────────────────────────────────────── */
export async function fetchTemplates(runId: string | null, signal?: AbortSignal): Promise<ReportTemplatesDoc> {
  const doc = parseTemplates(await getJson<unknown>(`/reports/templates${runId ? `?run_id=${encodeURIComponent(runId)}` : ''}`, { signal }));
  if (!doc) throw new CentreError('bad_templates', 'Reply not usable.');
  return doc;
}
export async function buildReport(body: BuildBody, signal?: AbortSignal): Promise<BuildEnvelope> {
  const env = parseBuildEnvelope(await getJson<unknown>('/reports/build', { method: 'POST', body, signal, timeoutMs: 60000 }));
  if (!env) throw new CentreError('bad_build', 'Reply not usable.');
  return env;
}
/** POST /reports/pdf: application/pdf bytes. 501 renderer_unavailable
 *  and 502 pdf_failed surface as CentreError codes. */
export async function buildPdf(body: Omit<BuildBody, 'format'>, signal?: AbortSignal): Promise<Blob> {
  const res = await call('/reports/pdf', { method: 'POST', body, signal, timeoutMs: 90000 });
  return res.blob();
}

/* ── exports ─────────────────────────────────────────────────────── */
export async function fetchCatalogue(runId: string | null, simRunId: string | null, signal?: AbortSignal): Promise<ExportCatalogue> {
  const p = new URLSearchParams();
  if (runId) p.set('run_id', runId);
  if (simRunId) p.set('sim_run_id', simRunId);
  const qs = p.toString();
  const cat = parseCatalogue(await getJson<unknown>(`/exports/datasets${qs ? `?${qs}` : ''}`, { signal }));
  if (!cat) throw new CentreError('bad_catalogue', 'Reply not usable.');
  return cat;
}
export interface CsvDownload { blob: Blob; filename: string; result: CsvResult }
export async function fetchCsv(path: string, signal?: AbortSignal): Promise<CsvDownload> {
  const res = await call(path, { signal, timeoutMs: 60000 });
  const cd = res.headers.get('content-disposition') ?? '';
  const m = /filename="?([^";]+)"?/.exec(cd);
  return { blob: await res.blob(), filename: m?.[1] ?? 'dataset.csv', result: csvResultFromHeaders((n) => res.headers.get(n)) };
}
export interface PackDownload { blob: Blob; bytes: Uint8Array; filename: string; asOf: string | null }
export async function buildPack(body: Record<string, unknown>, signal?: AbortSignal): Promise<PackDownload> {
  const res = await call('/exports/pack', { method: 'POST', body, signal, timeoutMs: 120000 });
  const cd = res.headers.get('content-disposition') ?? '';
  const m = /filename="?([^";]+)"?/.exec(cd);
  const buf = await res.arrayBuffer();
  return { blob: new Blob([buf], { type: 'application/zip' }), bytes: new Uint8Array(buf), filename: m?.[1] ?? 'supermap-pack.zip', asOf: res.headers.get('x-pack-as-of') };
}
