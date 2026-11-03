/* Fetch layer of the lane planner. Speaks the house error shape; keeps the
 * `refusal` object of a 422 (the named reason and its places), which the
 * shared reports client drops. Nothing here throws into a panel. */
import { API_BASE } from '../../api';
import type { PlanRequest, PlanResult, Scenario } from './types';

const TIMEOUT_MS = 60000;

async function withTimeout(path: string, init: RequestInit, signal?: AbortSignal): Promise<Response> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  if (signal) {
    if (signal.aborted) ctrl.abort();
    else signal.addEventListener('abort', () => ctrl.abort(), { once: true });
  }
  try { return await fetch(`${API_BASE}${path}`, { ...init, signal: ctrl.signal }); } finally { clearTimeout(timer); }
}

/** POST /lanes/plan. 404 / 405 / 501, or a 2xx that is not JSON (the SPA
 *  fallback of a dev server), is NOT SERVED; a network error or 5xx is DOWN. */
export async function postPlan(body: PlanRequest, signal?: AbortSignal): Promise<PlanResult> {
  let res: Response;
  try {
    res = await withTimeout('/lanes/plan', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body) }, signal);
  } catch { return { kind: 'down', code: signal?.aborted ? 'aborted' : 'network' }; }
  if (res.status === 404 || res.status === 405 || res.status === 501) return { kind: 'notserved' };
  const ct = res.headers.get('content-type') ?? '';
  const j = (await res.json().catch(() => null)) as Record<string, unknown> | null;
  if (res.ok) {
    if (!/json/i.test(ct) || !j || j.schema !== 'lanes.plan/1' || !Array.isArray(j.routes)) return { kind: 'notserved' };
    return { kind: 'ok', doc: j as unknown as import('./types').PlanDoc };
  }
  const err = (j?.error ?? {}) as { code?: string; message?: string };
  if (res.status === 422 || res.status === 400) {
    return { kind: 'refused', code: err.code ?? `http_${res.status}`, message: err.message ?? '', refusal: (j?.refusal ?? {}) as Record<string, unknown> };
  }
  return { kind: 'down', code: err.code ?? `http_${res.status}` };
}

/** GET /scenarios (the curated library, engine rev 2). null = the feed is down or absent. */
export async function fetchScenarios(signal?: AbortSignal): Promise<Scenario[] | null> {
  try {
    const res = await withTimeout('/scenarios?engine_rev=2', { headers: { Accept: 'application/json' } }, signal);
    if (!res.ok || !/json/i.test(res.headers.get('content-type') ?? '')) return null;
    const j = (await res.json().catch(() => null)) as { scenarios?: unknown } | null;
    if (!j || !Array.isArray(j.scenarios)) return null;
    return (j.scenarios as Array<Record<string, unknown>>)
      .filter((s) => typeof s.id === 'string' && typeof s.label === 'string' && typeof s.place_id === 'string')
      .map((s) => ({
        id: s.id as string, label: s.label as string, place_id: s.place_id as string, kind: String(s.kind ?? 'closure'),
        default_duration_d: Number(s.default_duration_d ?? 14),
        duration_options_d: Array.isArray(s.duration_options_d) ? (s.duration_options_d as number[]) : undefined,
        default_capacity_loss: typeof s.default_capacity_loss === 'number' ? s.default_capacity_loss : undefined,
        capacity_loss_options: Array.isArray(s.capacity_loss_options) ? (s.capacity_loss_options as number[]) : undefined,
      }));
  } catch { return null; }
}
