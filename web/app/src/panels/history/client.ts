/* HISTORY / FORK / REALISM: HTTP client. Every call resolves to one of
 * three states and never throws:
 *   ok         the route answered with data;
 *   notserved  HTTP 200 `{served:false, state:"NOT SERVED", reason}`, or the
 *              route does not exist (404/405/501): shown as NOT SERVED,
 *              nothing simulated or backfilled;
 *   error      transport failure or a 4xx/5xx with the house error shape.
 * The fetch function is injectable. */
import { API_BASE } from '../../api';
import { neutraliseDoc } from '../../contentGuard';
import type {
  ForkRequestBody, ForkResponse, HistEventsDoc, HistExamplesDoc, HistNotServed, HistObservations, HistSourcesDoc, HistTimelineDoc,
  RealismCard, RealismResultsDoc, RealismRuleDoc,
} from './types';

export type Fetched<T> =
  | { kind: 'ok'; body: T }
  | { kind: 'notserved'; reason: string; detail: string; source: string }
  | { kind: 'error'; code: string; message: string; status: number | null };

export interface FetchLikeResponse { status: number; ok: boolean; json(): Promise<unknown> }
export type FetchFn = (url: string, init?: { method?: string; headers?: Record<string, string>; body?: string; signal?: AbortSignal }) => Promise<FetchLikeResponse>;

const defaultFetch: FetchFn = (url, init) => fetch(url, init) as unknown as Promise<FetchLikeResponse>;
export const HISTORY_TIMEOUT_MS = 20_000;

export async function call<T>(path: string, init?: { method?: 'GET' | 'POST'; body?: unknown }, fetchFn: FetchFn = defaultFetch, base: string = API_BASE): Promise<Fetched<T>> {
  const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), HISTORY_TIMEOUT_MS) : null;
  try {
    const res = await fetchFn(`${base}${path}`, {
      method: init?.method ?? 'GET',
      headers: { accept: 'application/json', ...(init?.body !== undefined ? { 'content-type': 'application/json' } : {}) },
      ...(init?.body !== undefined ? { body: JSON.stringify(init.body) } : {}),
      ...(ctrl ? { signal: ctrl.signal } : {}),
    });
    let json: unknown = null;
    try { json = await res.json(); } catch { json = null; }
    if (res.status === 404 || res.status === 405 || res.status === 501) {
      const e = errOf(json);
      // a typed 404 (`unknown_event`, `run_not_found`) is an answer, not a missing route
      if (e && e.code !== `http_${res.status}` && e.code !== 'not_found') return { kind: 'error', code: e.code, message: e.message, status: res.status };
      return { kind: 'notserved', reason: 'route_missing', detail: path, source: '' };
    }
    if (!res.ok) {
      const e = errOf(json);
      return { kind: 'error', code: e?.code ?? `http_${res.status}`, message: e?.message ?? `HTTP ${res.status}`, status: res.status };
    }
    if (json && typeof json === 'object' && (json as { served?: unknown }).served === false) {
      const ns = json as HistNotServed;
      return { kind: 'notserved', reason: ns.reason ?? 'not_served', detail: ns.detail ?? '', source: ns.provenance?.source ?? '' };
    }
    if (json === null || typeof json !== 'object') return { kind: 'error', code: 'bad_payload', message: 'not JSON', status: res.status };
    return { kind: 'ok', body: neutraliseDoc(json) as T };
  } catch {
    return { kind: 'error', code: 'unreachable', message: 'service not answering', status: null };
  } finally {
    if (timer) clearTimeout(timer);
  }
}
function errOf(json: unknown): { code: string; message: string } | null {
  const e = (json as { error?: { code?: string; message?: string } } | null)?.error;
  return e && typeof e === 'object' ? { code: e.code ?? 'error', message: e.message ?? '' } : null;
}

const qs = (o: Record<string, string | number | undefined | null>): string => {
  const p = Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== '').map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`);
  return p.length ? `?${p.join('&')}` : '';
};

export const historyApi = {
  sources: (f?: FetchFn) => call<HistSourcesDoc>('/history/sources', undefined, f),
  examples: (f?: FetchFn) => call<HistExamplesDoc>('/history/examples', undefined, f),
  events: (a: { from: string; to: string; places?: string[]; sources?: string[]; infer?: boolean }, f?: FetchFn) =>
    call<HistEventsDoc>(`/history/events${qs({ from: a.from, to: a.to, places: a.places?.join(','), sources: a.sources?.join(','), infer: a.infer === false ? 0 : undefined })}`, undefined, f),
  observations: (a: { place: string; metric?: string; from: string; to: string }, f?: FetchFn) =>
    call<HistObservations>(`/history/observations${qs({ place: a.place, metric: a.metric, from: a.from, to: a.to })}`, undefined, f),
  timeline: (a: { date: string; places?: string[]; lookback?: number }, f?: FetchFn) =>
    call<HistTimelineDoc>(`/history/timeline${qs({ date: a.date, places: a.places?.join(','), lookback_days: a.lookback })}`, undefined, f),
  fork: (body: ForkRequestBody, f?: FetchFn) => call<ForkResponse>('/history/fork', { method: 'POST', body }, f),
  realismResults: (f?: FetchFn) => call<RealismResultsDoc>('/realism/results', undefined, f),
  realismCard: (id: string, f?: FetchFn) => call<RealismCard>(`/realism/results/${encodeURIComponent(id)}`, undefined, f),
  realismRule: (f?: FetchFn) => call<RealismRuleDoc>('/realism/rule', undefined, f),
  realismRun: (body: Record<string, unknown>, f?: FetchFn) => call<RealismCard>('/realism', { method: 'POST', body }, f),
  /** The chain run for ENGINE REV 2: POST /run with the fork's own body + engine_rev. */
  runChain: (body: Record<string, unknown>, f?: FetchFn) => call<{ run_id: string; status?: string; engine_rev?: number }>('/run', { method: 'POST', body }, f),
};
