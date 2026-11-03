/**
 * AGENT API: the /agent/* endpoints behind one small object whose `fetch` is
 * injectable (a mock can be passed; the app passes the real one). Errors are a
 * typed AgentApiError (machine code + HTTP status), never a thrown string;
 * a network failure is code 'unreachable', a timeout 'timeout'.
 */
import { API_BASE } from '../api';
import type { AgentStatusDoc, ClientResult, DemoTasksDoc, SessionView, StepsDoc } from './types';

export class AgentApiError extends Error {
  code: string;
  status: number;
  constructor(code: string, status: number, message = code) { super(message); this.code = code; this.status = status; }
}

export interface ArrangeShock { id: string; place_id: string; start_offset_d: number; duration_d: number }

export const REQUEST_TIMEOUT_MS = 20_000;
/** Server cap for the long poll is 15 s; ask for a little less. */
export const LONG_POLL_S = 12;

export interface AgentApi {
  status(): Promise<AgentStatusDoc>;
  demoTasks(): Promise<DemoTasksDoc>;
  /** `purpose` starts a narrow task session (the Scenario builder's Arrange box). */
  createSession(purpose?: 'scenario_arrange'): Promise<SessionView>;
  getSession(id: string): Promise<SessionView>;
  /** `timeline` is the shocks on the Scenario builder timeline (purpose sessions only). */
  postMessage(id: string, text: string, timeline?: ArrangeShock[]): Promise<SessionView>;
  steps(id: string, afterRev: number, waitS: number, signal?: AbortSignal): Promise<StepsDoc>;
  confirm(id: string, actionId: string): Promise<SessionView>;
  reject(id: string, actionId: string): Promise<SessionView>;
  cancel(id: string): Promise<SessionView>;
  clientResult(id: string, actionId: string, r: ClientResult): Promise<SessionView>;
  /** Research report routes (server/agent/research_routes.py). The documents are untrusted: callers parse them. */
  evidence(id: string): Promise<unknown>;
  researchReport(id: string): Promise<unknown>;
  /** Queue the note (202); poll `researchReport` until the state settles. */
  writeResearchReport(id: string): Promise<unknown>;
}

type FetchLike = typeof fetch;
const enc = encodeURIComponent;

export function makeAgentApi(fetchImpl: FetchLike = (...a) => fetch(...a), base: string = API_BASE): AgentApi {
  async function call<T>(method: 'GET' | 'POST', path: string, body?: unknown, opts: { signal?: AbortSignal; timeoutMs?: number } = {}): Promise<T> {
    const ctrl = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; ctrl.abort(); }, opts.timeoutMs ?? REQUEST_TIMEOUT_MS);
    if (opts.signal) {
      if (opts.signal.aborted) ctrl.abort();
      else opts.signal.addEventListener('abort', () => ctrl.abort(), { once: true });
    }
    let res: Response;
    try {
      res = await fetchImpl(`${base}${path}`, {
        method, signal: ctrl.signal,
        headers: body === undefined ? undefined : { 'content-type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (e) {
      clearTimeout(timer);
      if (timedOut) throw new AgentApiError('timeout', 0);
      if (opts.signal?.aborted) throw new AgentApiError('aborted', 0);
      throw new AgentApiError('unreachable', 0, e instanceof Error ? e.message : 'unreachable');
    }
    clearTimeout(timer);
    let json: unknown = null;
    try { json = await res.json(); } catch { /* non-JSON body */ }
    if (!res.ok) {
      const err = (json as { error?: { code?: unknown; message?: unknown } } | null)?.error;
      throw new AgentApiError(typeof err?.code === 'string' ? err.code : `http_${res.status}`, res.status,
        typeof err?.message === 'string' ? err.message : undefined);
    }
    if (json === null || typeof json !== 'object') throw new AgentApiError('bad_response', res.status);
    return json as T;
  }
  const s = (id: string) => `/agent/sessions/${enc(id)}`;
  return {
    status: () => call('GET', '/agent/status'),
    demoTasks: () => call('GET', '/agent/demo-tasks'),
    createSession: (purpose) => call('POST', '/agent/sessions', purpose ? { purpose } : {}),
    getSession: (id) => call('GET', s(id)),
    postMessage: (id, text, timeline) => call('POST', `${s(id)}/messages`, timeline ? { text, timeline } : { text }),
    steps: (id, afterRev, waitS, signal) =>
      call('GET', `${s(id)}/steps?after_rev=${Math.max(0, Math.floor(afterRev))}&wait_s=${waitS}`, undefined,
        { signal, timeoutMs: (waitS + 10) * 1000 }),
    confirm: (id, aid) => call('POST', `${s(id)}/actions/${enc(aid)}/confirm`),
    reject: (id, aid) => call('POST', `${s(id)}/actions/${enc(aid)}/reject`),
    cancel: (id) => call('POST', `${s(id)}/cancel`),
    clientResult: (id, aid, r) => call('POST', `${s(id)}/actions/${enc(aid)}/result`, r),
    evidence: (id) => call('GET', `${s(id)}/evidence`),
    researchReport: (id) => call('GET', `${s(id)}/research-report`),
    writeResearchReport: (id) => call('POST', `${s(id)}/research-report`),
  };
}
