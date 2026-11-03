/**
 * CHAT API: /agent/threads behind one small object whose `fetch` is injectable.
 * Errors are the agent's typed AgentApiError (machine code + HTTP status).
 */
import { API_BASE } from '../api';
import { AgentApiError, LONG_POLL_S, REQUEST_TIMEOUT_MS } from '../agent/api';
import type { ClientResult, DemoTasksDoc } from '../agent/types';
import type { ChatStatusDoc, ContextChip, ExportFormat, ThreadStepsDoc, ThreadSummary, ThreadView } from './types';

export { AgentApiError, LONG_POLL_S };

export interface ExportFile { blob: Blob; filename: string }

export interface ChatApi {
  status(): Promise<ChatStatusDoc>;
  demoTasks(): Promise<DemoTasksDoc>;
  list(): Promise<{ threads: ThreadSummary[]; suggestions: string[] }>;
  create(body: { web_search?: boolean; title?: string }): Promise<ThreadView>;
  get(id: string): Promise<ThreadView>;
  update(id: string, body: { title?: string; web_search?: boolean }): Promise<ThreadView>;
  remove(id: string): Promise<void>;
  post(id: string, body: { text: string; context?: ContextChip[]; web_search?: boolean }): Promise<ThreadView>;
  regenerate(id: string): Promise<ThreadView>;
  steps(id: string, afterRev: number, waitS: number, signal?: AbortSignal): Promise<ThreadStepsDoc>;
  confirm(id: string, actionId: string): Promise<ThreadView>;
  reject(id: string, actionId: string): Promise<ThreadView>;
  cancel(id: string): Promise<ThreadView>;
  clientResult(id: string, actionId: string, r: ClientResult): Promise<ThreadView>;
  exportThread(id: string, format: ExportFormat, messageId?: string): Promise<ExportFile>;
}

type FetchLike = typeof fetch;
const enc = encodeURIComponent;

export function makeChatApi(fetchImpl: FetchLike = (...a) => fetch(...a), base: string = API_BASE): ChatApi {
  async function raw(method: string, path: string, body?: unknown, opts: { signal?: AbortSignal; timeoutMs?: number } = {}): Promise<Response> {
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
    if (!res.ok) {
      let json: unknown = null;
      try { json = await res.json(); } catch { /* non-JSON body */ }
      const err = (json as { error?: { code?: unknown; message?: unknown } } | null)?.error;
      throw new AgentApiError(typeof err?.code === 'string' ? err.code : `http_${res.status}`, res.status,
        typeof err?.message === 'string' ? err.message : undefined);
    }
    return res;
  }
  async function call<T>(method: string, path: string, body?: unknown, opts: { signal?: AbortSignal; timeoutMs?: number } = {}): Promise<T> {
    const res = await raw(method, path, body, opts);
    let json: unknown = null;
    try { json = await res.json(); } catch { /* handled below */ }
    if (json === null || typeof json !== 'object') throw new AgentApiError('bad_response', res.status);
    return json as T;
  }
  const t = (id: string) => `/agent/threads/${enc(id)}`;
  return {
    status: () => call('GET', '/agent/status'),
    demoTasks: () => call('GET', '/agent/demo-tasks'),
    list: () => call('GET', '/agent/threads'),
    create: (body) => call('POST', '/agent/threads', body),
    get: (id) => call('GET', t(id)),
    update: (id, body) => call('PUT', t(id), body),
    remove: async (id) => { await call('DELETE', t(id)); },
    post: (id, body) => call('POST', `${t(id)}/messages`, body),
    regenerate: (id) => call('POST', `${t(id)}/regenerate`),
    steps: (id, afterRev, waitS, signal) =>
      call('GET', `${t(id)}/steps?after_rev=${Math.max(0, Math.floor(afterRev))}&wait_s=${waitS}`, undefined,
        { signal, timeoutMs: (waitS + 10) * 1000 }),
    confirm: (id, aid) => call('POST', `${t(id)}/actions/${enc(aid)}/confirm`),
    reject: (id, aid) => call('POST', `${t(id)}/actions/${enc(aid)}/reject`),
    cancel: (id) => call('POST', `${t(id)}/cancel`),
    clientResult: (id, aid, r) => call('POST', `${t(id)}/actions/${enc(aid)}/result`, r),
    exportThread: async (id, format, messageId) => {
      const q = `format=${enc(format)}${messageId ? `&message=${enc(messageId)}` : ''}`;
      const res = await raw('POST', `${t(id)}/export?${q}`, undefined, { timeoutMs: 40_000 });
      const cd = res.headers.get('content-disposition') ?? '';
      const m = /filename="([^"]+)"/.exec(cd);
      return { blob: await res.blob(), filename: m ? m[1] : `meta-ai-chat.${format}` };
    },
  };
}
