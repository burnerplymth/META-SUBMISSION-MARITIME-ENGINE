import { API_BASE, ApiError, api } from '../../api';
import type { Chain } from '../../types';
import { capabilityFromSchema, capabilityOf, type Capability } from './logic';
import type {
  CatalogueRun, ChainCompare, ChainJournal, ChainVerify, RunReceipt, ScenariosDoc, ShockCheck, ShockRules,
  SimCompare, SimForkReceipt, SimJournal, SimLineage, SimVerify,
} from './types';

/* Thin fetch layer of the scenario loop (SCN). It uses the api.ts error
 * class so every refusal keeps the server's `{error:{code,message}}`
 * verbatim, and its own timeouts: chain-loop reads are instant, the sim
 * compare / verify routes load 160+ MB records and get a long budget. */

export const FAST_MS = 15_000;
export const SLOW_MS = 180_000;

interface Raw<T> { status: number; body: T }

async function call<T>(path: string, init?: { method?: string; body?: unknown; timeoutMs?: number }): Promise<Raw<T>> {
  const timeoutMs = init?.timeoutMs ?? FAST_MS;
  const ctrl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; ctrl.abort(); }, timeoutMs);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: init?.method ?? 'GET',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      ...(init?.body !== undefined ? { body: JSON.stringify(init.body) } : {}),
      signal: ctrl.signal,
    });
  } catch {
    throw timedOut ? new ApiError('timeout', `No answer within ${Math.round(timeoutMs / 1000)} s`) : new ApiError('network', 'Service unreachable');
  } finally { clearTimeout(timer); }
  const body = (await res.json().catch(() => null)) as Record<string, unknown> | null;
  if (!res.ok) {
    const err = (body?.error ?? {}) as { code?: string; message?: string };
    throw new ApiError(err.code ?? `http_${res.status}`, err.message ?? `HTTP ${res.status}`);
  }
  return { status: res.status, body: body as T };
}
const get = async <T>(path: string, timeoutMs?: number): Promise<T> => (await call<T>(path, { timeoutMs })).body;
const enc = encodeURIComponent;

/** GET-probe one route: does it exist, and is it answering. Never throws. */
export async function probeCapability(path: string): Promise<Capability> {
  try {
    const res = await fetch(`${API_BASE}${path}`, { headers: { Accept: 'application/json' } });
    const isJson = /json/i.test(res.headers.get('content-type') ?? '');
    let code: string | null = null;
    if (isJson && !res.ok) {
      const b = (await res.json().catch(() => null)) as { error?: { code?: string } } | null;
      code = b?.error?.code ?? null;
    }
    return capabilityOf(res.status, code, isJson);
  } catch { return capabilityOf(null, null, false); }
}

/** The served OpenAPI document, fetched once per page. Reading the route list
 *  from it avoids probing routes that need a real run id, each of which would
 *  answer a 404 or 422 the browser logs as a console error. A server
 *  or proxy that does not serve it answers null and the caller probes. */
let schemaDoc: Promise<unknown> | null = null;
export const loadSchema = (): Promise<unknown> => {
  if (!schemaDoc) {
    schemaDoc = fetch(`${API_BASE}/openapi.json`, { headers: { Accept: 'application/json' } })
      .then(async (res) => (res.ok && /json/i.test(res.headers.get('content-type') ?? '') ? await res.json().catch(() => null) : null))
      .catch(() => null);
  }
  return schemaDoc;
};
/** Does the server serve `template` (an OpenAPI path)? Falls back to a GET
 *  probe of `probePath` when no schema is available. Never throws. */
export async function routeCapability(template: string, probePath: string): Promise<Capability> {
  return capabilityFromSchema(await loadSchema(), template) ?? probeCapability(probePath);
}

/* ── chain loop ─────────────────────────────────────────────────────── */
export const scn = {
  scenarios: (rev: 1 | 2) => get<ScenariosDoc>(`/scenarios?engine_rev=${rev}`),
  rules: () => get<ShockRules>('/shocks/rules'),
  /** POST /shocks/check: a refusal is a 200 answer with `accepted:false`. */
  check: async (body: unknown): Promise<ShockCheck> => (await call<ShockCheck>('/shocks/check', { method: 'POST', body })).body,
  run: async (body: unknown): Promise<RunReceipt> => (await call<RunReceipt>('/run', { method: 'POST', body })).body,
  fork: async (body: unknown): Promise<RunReceipt> => (await call<RunReceipt>('/fork', { method: 'POST', body })).body,
  chain: (runId: string): Promise<Chain> => api.chain(runId),
  journal: (runId: string) => get<ChainJournal>(`/run/${enc(runId)}/journal`),
  compare: (a: string, b: string) => get<ChainCompare>(`/compare/${enc(a)}/${enc(b)}`),
  verify: (runId: string) => get<ChainVerify>(`/run/${enc(runId)}/verify`, SLOW_MS),
  runs: async (): Promise<CatalogueRun[]> => (await api.runsCatalogue()).runs as CatalogueRun[],

  /* ── sim loop ───────────────────────────────────────────────────────── */
  simFork: async (simRunId: string, body: unknown): Promise<{ status: number; body: SimForkReceipt }> =>
    call<SimForkReceipt>(`/sim/run/${enc(simRunId)}/fork`, { method: 'POST', body }),
  simJournal: (simRunId: string) => get<SimJournal>(`/sim/run/${enc(simRunId)}/journal`, SLOW_MS),
  simLineage: (root: string) => get<SimLineage>(`/sim/runs/lineage?root=${enc(root)}`),
  simCompare: (a: string, b: string) => get<SimCompare>(`/sim/compare?a=${enc(a)}&b=${enc(b)}`, SLOW_MS),
  simVerify: (simRunId: string) => get<SimVerify>(`/sim/run/${enc(simRunId)}/verify`, SLOW_MS),
  simVerifyReplay: (simRunId: string) => call<{ sim_run_id: string; status: string }>(`/sim/run/${enc(simRunId)}/verify`, { method: 'POST', timeoutMs: SLOW_MS }),
};
