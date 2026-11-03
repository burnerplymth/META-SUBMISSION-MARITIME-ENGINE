/* Scenario builder fetch layer (/scenarios/*). Refusals keep the server's
 * `{error:{code,message}}` verbatim through the api.ts error class. */
import { API_BASE, ApiError } from '../../api';
import type { BuilderDoc, OrganiseDoc, OrganiseRule, RulesDoc, RunDoc, SavedList, ValidateDoc, WireScenario } from './types';
import { wireBody } from './doc';

const FAST_MS = 15_000;

async function call<T>(path: string, init?: { method?: string; body?: unknown; signal?: AbortSignal; timeoutMs?: number }): Promise<T> {
  const ctrl = new AbortController();
  let timedOut = false;
  const ms = init?.timeoutMs ?? FAST_MS;
  const timer = setTimeout(() => { timedOut = true; ctrl.abort(); }, ms);
  init?.signal?.addEventListener('abort', () => ctrl.abort(), { once: true });
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: init?.method ?? 'GET',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      ...(init?.body !== undefined ? { body: JSON.stringify(init.body) } : {}),
      signal: ctrl.signal,
    });
  } catch {
    if (init?.signal?.aborted) throw new ApiError('aborted', 'Superseded');
    throw timedOut ? new ApiError('timeout', `No answer within ${Math.round(ms / 1000)} s`) : new ApiError('network', 'Service unreachable');
  } finally { clearTimeout(timer); }
  const body = (await res.json().catch(() => null)) as Record<string, unknown> | null;
  if (!res.ok) {
    const err = (body?.error ?? {}) as { code?: string; message?: string };
    throw new ApiError(err.code ?? `http_${res.status}`, err.message ?? `HTTP ${res.status}`);
  }
  return body as T;
}
const enc = encodeURIComponent;

export interface RunOptions { target: 'chain' | 'sim'; engineRev?: 1 | 2 }

export const scenarioApi = {
  rules: (signal?: AbortSignal) => call<RulesDoc>('/scenarios/rules', { signal }),
  validate: (doc: BuilderDoc, engineRev: 1 | 2 = 1, signal?: AbortSignal) =>
    call<ValidateDoc>('/scenarios/validate', { method: 'POST', body: { scenario: wireBody(doc), engine_rev: engineRev }, signal }),
  save: (doc: BuilderDoc) => call<{ id: string; created: boolean; scenario: WireScenario; owner_token?: string }>('/scenarios', { method: 'POST', body: { scenario: wireBody(doc) } }),
  saved: (signal?: AbortSignal) => call<SavedList>('/scenarios/saved', { signal }),
  get: (id: string) => call<{ id: string; scenario: WireScenario }>(`/scenarios/${enc(id)}`),
  /** The token travels as a query parameter: CORS allows only Content-Type. */
  remove: (id: string, ownerToken: string) => call<{ id: string; deleted: boolean }>(`/scenarios/${enc(id)}?owner_token=${enc(ownerToken)}`, { method: 'DELETE' }),
  organise: (shocks: Array<{ id: string; place_id: string; start_offset_d: number; duration_d: number }>, instruction: OrganiseRule, signal?: AbortSignal) =>
    call<OrganiseDoc>('/scenarios/organise', { method: 'POST', body: { shocks, instruction }, signal }),
  /** Run the document as it is (nothing is stored). */
  run: (doc: BuilderDoc, o: RunOptions) =>
    call<RunDoc>('/scenarios/run', { method: 'POST', body: { scenario: wireBody(doc), target: o.target, ...(o.engineRev === 2 ? { engine_rev: 2 } : {}) }, timeoutMs: 60_000 }),
  runSaved: (id: string, o: RunOptions) =>
    call<RunDoc>(`/scenarios/${enc(id)}/run`, { method: 'POST', body: { target: o.target, ...(o.engineRev === 2 ? { engine_rev: 2 } : {}) }, timeoutMs: 60_000 }),
  simStatus: (id: string) => call<{ status: string }>(`/sim/run/${enc(id)}/status`),
};

/** Is /scenarios/validate served? A 404 hides the builder's run controls. */
export async function probeScenarios(): Promise<'served' | 'notserved' | 'down'> {
  try {
    const res = await fetch(`${API_BASE}/scenarios/rules`, { headers: { Accept: 'application/json' } });
    if (res.status === 404 || res.status === 405) return 'notserved';
    return res.ok && /json/i.test(res.headers.get('content-type') ?? '') ? 'served' : 'down';
  } catch { return 'down'; }
}
