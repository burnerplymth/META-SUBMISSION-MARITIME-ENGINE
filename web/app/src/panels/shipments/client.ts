/* Fetch layer of the shipments window. Speaks the house error shape; a 404 /
 * 405 / 501, or a 2xx that is not the expected JSON, is NOT SERVED; a network
 * error, a rate refusal or a 5xx is DOWN. Nothing here throws into a panel and
 * no server text reaches the screen (codes only). */
import { API_BASE } from '../../api';
import type { EvalDoc, EvalResult, ResolveItem, ResolveRow, Shipment } from './types';

const TIMEOUT_MS = 60000;

async function send(path: string, init: RequestInit, signal?: AbortSignal): Promise<Response> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  if (signal) { if (signal.aborted) ctrl.abort(); else signal.addEventListener('abort', () => ctrl.abort(), { once: true }); }
  try { return await fetch(`${API_BASE}${path}`, { ...init, signal: ctrl.signal }); } finally { clearTimeout(timer); }
}
const post = (path: string, body: unknown, signal?: AbortSignal) =>
  send(path, { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body) }, signal);

export type ScenarioBody = { scenario_id: string; duration_d?: number; capacity_loss?: number }
  | { disruption: { kind: string; place: string; duration_d: number; capacity_loss?: number } };
export interface EvalRequest { shipments: Shipment[]; scenario?: ScenarioBody; mc_job_id?: string }

export async function postEvaluate(body: EvalRequest, signal?: AbortSignal): Promise<EvalResult> {
  let res: Response;
  try { res = await post('/exposure/evaluate', body, signal); } catch { return { kind: 'down', code: signal?.aborted ? 'aborted' : 'network' }; }
  if (res.status === 404 || res.status === 405 || res.status === 501) return { kind: 'notserved' };
  const ct = res.headers.get('content-type') ?? '';
  const j = (await res.json().catch(() => null)) as Record<string, unknown> | null;
  if (res.ok) {
    if (!/json/i.test(ct) || !j || j.schema !== 'exposure.evaluate/1' || !Array.isArray(j.shipments)) return { kind: 'notserved' };
    return { kind: 'ok', doc: j as unknown as EvalDoc };
  }
  const code = String(((j?.error ?? {}) as { code?: string }).code ?? `http_${res.status}`);
  if (res.status === 429) return { kind: 'down', code: 'rate_limited' };
  if (res.status === 422 || res.status === 413 || res.status === 400) return { kind: 'refused', code };
  return { kind: 'down', code };
}

export async function postResolve(items: ResolveItem[], signal?: AbortSignal): Promise<ResolveRow[] | null> {
  try {
    const res = await post('/exposure/resolve', { items }, signal);
    if (!res.ok || !/json/i.test(res.headers.get('content-type') ?? '')) return null;
    const j = (await res.json().catch(() => null)) as { results?: unknown } | null;
    return j && Array.isArray(j.results) ? (j.results as ResolveRow[]) : null;
  } catch { return null; }
}

export async function fetchSample(signal?: AbortSignal): Promise<Shipment[] | null> {
  try {
    const res = await send('/exposure/sample', { headers: { Accept: 'application/json' } }, signal);
    if (!res.ok || !/json/i.test(res.headers.get('content-type') ?? '')) return null;
    const j = (await res.json().catch(() => null)) as { shipments?: unknown } | null;
    return j && Array.isArray(j.shipments) ? (j.shipments as Shipment[]) : null;
  } catch { return null; }
}

/** The server-built file of the current list and scenario (formula-guarded). null = not served. */
export async function postExport(body: EvalRequest, format: 'csv' | 'xlsx', signal?: AbortSignal): Promise<Blob | null> {
  try {
    const res = await post('/exposure/export', { ...body, format }, signal);
    return res.ok ? await res.blob() : null;
  } catch { return null; }
}
