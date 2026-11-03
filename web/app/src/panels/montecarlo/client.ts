/* Fetch layer of the Monte Carlo window. Speaks the house error shape
 * {"error": {"code", "message"}}; a route that is absent (404 / 405 / 501, or a
 * 2xx that is not JSON: the SPA fallback of a dev server) is NOT SERVED, a
 * network error or 5xx is DOWN, a named 4xx is a refusal with its code. Nothing
 * here throws into a panel. */
import { API_BASE } from '../../api';
import { neutraliseDoc } from '../../contentGuard';
import { parseCompare, type CompareDoc } from './compare';
import type { Fetched, McJob, McRequest, McSample, McSamples, McStart } from './types';

const TIMEOUT_MS = 30000;
export const SAMPLE_PAGE = 1000;

async function call(path: string, init: RequestInit, signal?: AbortSignal): Promise<Response> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  if (signal) {
    if (signal.aborted) ctrl.abort();
    else signal.addEventListener('abort', () => ctrl.abort(), { once: true });
  }
  try { return await fetch(`${API_BASE}${path}`, { ...init, signal: ctrl.signal }); } finally { clearTimeout(timer); }
}

/** Classify one response. `shape` decides whether a 2xx body is the document wanted. */
async function settle<T>(res: Response, shape: (j: Record<string, unknown>) => boolean): Promise<Fetched<T>> {
  const ct = res.headers.get('content-type') ?? '';
  const j = (await res.json().catch(() => null)) as Record<string, unknown> | null;
  const err = (j?.error ?? null) as { code?: string; message?: string } | null;
  if (res.ok) {
    if (!/json/i.test(ct) || !j || !shape(j)) return { kind: 'notserved' };
    return { kind: 'ok', doc: neutraliseDoc(j) as unknown as T };
  }
  if (res.status === 405 || res.status === 501 || (res.status === 404 && !err?.code)) return { kind: 'notserved' };
  if (res.status >= 500) return { kind: 'down', code: err?.code ?? `http_${res.status}` };
  return { kind: 'refused', code: err?.code ?? `http_${res.status}`, message: err?.message ?? '' };
}

const down = (signal?: AbortSignal): Fetched<never> => ({ kind: 'down', code: signal?.aborted ? 'aborted' : 'network' });

const isJob = (j: Record<string, unknown>): boolean => typeof j.job_id === 'string' && typeof j.status === 'string';

/** POST /montecarlo: starts a job (or answers the identical existing one). */
export async function startJob(body: McRequest, signal?: AbortSignal): Promise<Fetched<McStart>> {
  try {
    const res = await call('/montecarlo', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body) }, signal);
    return await settle<McStart>(res, (j) => typeof j.job_id === 'string' && typeof j.quote === 'object');
  } catch { return down(signal); }
}

/** GET /montecarlo/{job_id}. */
export async function getJob(id: string, signal?: AbortSignal): Promise<Fetched<McJob>> {
  try {
    const res = await call(`/montecarlo/${encodeURIComponent(id)}`, { headers: { Accept: 'application/json' } }, signal);
    return await settle<McJob>(res, isJob);
  } catch { return down(signal); }
}

/** GET /montecarlo/{job_id}/samples. */
export async function getSamples(id: string, offset: number, limit: number, signal?: AbortSignal): Promise<Fetched<McSamples>> {
  try {
    const res = await call(`/montecarlo/${encodeURIComponent(id)}/samples?offset=${offset}&limit=${limit}`, { headers: { Accept: 'application/json' } }, signal);
    return await settle<McSamples>(res, (j) => Array.isArray(j.rows) && typeof j.total === 'number');
  } catch { return down(signal); }
}

/** Every sample of a finished job, page by page. Null when any page is not served. */
export async function getAllSamples(id: string, signal?: AbortSignal): Promise<McSample[] | null> {
  const out: McSample[] = [];
  for (let offset = 0; offset < 100000; ) {
    const r = await getSamples(id, offset, SAMPLE_PAGE, signal);
    if (r.kind !== 'ok') return null;
    out.push(...r.doc.rows);
    offset += r.doc.rows.length;
    if (r.doc.rows.length === 0 || offset >= r.doc.total) break;
  }
  return out;
}

/** DELETE /montecarlo/{job_id}: cancels a running job, removes a finished one. Needs the owner token
 *  the server returned when it created the job (X-Montecarlo-Token); anyone else is refused (403). */
export async function cancelJob(id: string, ownerToken: string, signal?: AbortSignal): Promise<Fetched<{ job_id: string; status: string }>> {
  try {
    const res = await call(`/montecarlo/${encodeURIComponent(id)}`, { method: 'DELETE', headers: { Accept: 'application/json', 'X-Montecarlo-Token': ownerToken } }, signal);
    return await settle(res, (j) => typeof j.job_id === 'string');
  } catch { return down(signal); }
}

/** One row of GET /montecarlo: a job without its results. */
export type McJobRow = Omit<McJob, 'results'> & { created_at?: string };

/** GET /montecarlo: the recent jobs, newest first. */
export async function listJobs(signal?: AbortSignal): Promise<Fetched<McJobRow[]>> {
  try {
    const res = await call('/montecarlo', { headers: { Accept: 'application/json' } }, signal);
    const r = await settle<{ jobs: McJobRow[] }>(res, (j) => Array.isArray(j.jobs));
    return r.kind === 'ok' ? { kind: 'ok', doc: r.doc.jobs.filter((j) => j && typeof j.job_id === 'string' && j.request) } : r;
  } catch { return down(signal); }
}

/** GET /montecarlo/compare?a=&b=[&threshold=]. */
export async function getCompare(a: string, b: string, threshold?: number, signal?: AbortSignal): Promise<Fetched<CompareDoc>> {
  const q = `a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}${threshold !== undefined && Number.isFinite(threshold) ? `&threshold=${threshold}` : ''}`;
  try {
    const res = await call(`/montecarlo/compare?${q}`, { headers: { Accept: 'application/json' } }, signal);
    const r = await settle<Record<string, unknown>>(res, (j) => typeof j.metrics === 'object');
    if (r.kind !== 'ok') return r;
    const doc = parseCompare(r.doc);
    return doc ? { kind: 'ok', doc } : { kind: 'notserved' };
  } catch { return down(signal); }
}

/** A variant of the base scenario: a name and the fields it overrides. */
export interface McVariant { name: string; overrides: Record<string, unknown> }
export interface McBatch { jobs: Array<{ job_id: string; name?: string; quote?: McStart['quote'] }> }

/** POST /montecarlo/batch: a base request and up to four variants, created as paired jobs (common random numbers). */
export async function startBatch(base: McRequest, variants: McVariant[], signal?: AbortSignal): Promise<Fetched<McBatch>> {
  try {
    const res = await call('/montecarlo/batch', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify({ base, variants }) }, signal);
    const r = await settle<Record<string, unknown>>(res, (j) => Array.isArray(j.jobs) || Array.isArray(j.job_ids));
    if (r.kind !== 'ok') return r;
    const raw = (Array.isArray(r.doc.jobs) ? r.doc.jobs : (r.doc.job_ids as unknown[]).map((id) => ({ job_id: id }))) as Array<Record<string, unknown>>;
    const jobs = raw.filter((j) => j && typeof j.job_id === 'string').map((j) => ({ job_id: j.job_id as string, name: typeof j.name === 'string' ? j.name : undefined, quote: j.quote as McStart['quote'] | undefined }));
    return jobs.length ? { kind: 'ok', doc: { jobs } } : { kind: 'notserved' };
  } catch { return down(signal); }
}
