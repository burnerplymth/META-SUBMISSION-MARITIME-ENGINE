/**
 * META CLIENT: the three model-edge calls the new surfaces make,
 * kept out of api.ts so the shared file is untouched. Same base URL as
 * api.ts (VITE_API_BASE, default same-origin through the dev proxy).
 * No call here invents a value: errors surface as a typed MetaCallError
 * (machine code + HTTP status), timeouts as code 'timeout'.
 */
import { API_BASE } from '../api';
import { validateNlReply, type NlOutcome } from './nlQuery';

export class MetaCallError extends Error {
  code: string;
  status: number;
  constructor(code: string, status: number, message = code) { super(message); this.code = code; this.status = status; }
}

export const NL_TIMEOUT_MS = 8000;

async function post(path: string, body: unknown, timeoutMs: number, signal?: AbortSignal): Promise<unknown> {
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
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body), signal: ctrl.signal,
    });
  } catch (e) {
    clearTimeout(timer);
    if (timedOut) throw new MetaCallError('timeout', 0);
    if (signal?.aborted) throw new MetaCallError('aborted', 0);
    throw new MetaCallError('unreachable', 0, e instanceof Error ? e.message : 'unreachable');
  }
  clearTimeout(timer);
  let json: unknown = null;
  try { json = await res.json(); } catch { /* non-JSON body */ }
  if (!res.ok) {
    const err = (json as { error?: { code?: unknown } } | null)?.error;
    throw new MetaCallError(typeof err?.code === 'string' ? err.code : `http_${res.status}`, res.status);
  }
  return json;
}

/** POST /llm/query {text} -> a validated outcome (never a raw model reply). */
export async function queryNl(text: string, signal?: AbortSignal): Promise<NlOutcome> {
  try {
    const raw = await post('/llm/query', { text }, NL_TIMEOUT_MS, signal);
    return validateNlReply(raw, text, Date.now());
  } catch (e) {
    if (e instanceof MetaCallError) {
      if (e.code === 'timeout') return { status: 'timeout' };
      if (e.code === 'unreachable' || e.code === 'aborted') return { status: 'unreachable' };
      return { status: 'refused', code: e.code };
    }
    return { status: 'unreachable' };
  }
}

/** POST /llm/classify/preview -> the pre-call estimate (the API provides it). */
export const classifyPreview = (b: ClassifyBody, signal?: AbortSignal): Promise<unknown> =>
  post('/llm/classify/preview', b, 10_000, signal);

/** POST /llm/classify -> the classifier candidate + provenance. */
export const classifyText = (b: ClassifyBody, signal?: AbortSignal): Promise<unknown> =>
  post('/llm/classify', b, 20_000, signal);

export interface ClassifyBody { text: string; source: string; retrieved_at: string }

/** POST /llm/ask/preview -> the pre-call estimate (or the free refusal). */
export const askChainPreview = (b: AskChainBody, signal?: AbortSignal): Promise<unknown> =>
  post('/llm/ask/preview', b, 10_000, signal);

/** POST /llm/ask {question, scope_node_ids, run_id} -> the chain-grounded answer. */
export const askChainCall = (b: AskChainBody, signal?: AbortSignal): Promise<unknown> =>
  post('/llm/ask', b, ASK_TIMEOUT_MS, signal);

export const ASK_TIMEOUT_MS = 45_000;
export interface AskChainBody { question: string; scope_node_ids: string[]; run_id: string }

/** GET /satwatch/detectors -> the detector registry (public, read-only). */
export async function getDetectors(signal?: AbortSignal): Promise<unknown> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 8000);
  if (signal) signal.addEventListener('abort', () => ctrl.abort(), { once: true });
  try {
    const res = await fetch(`${API_BASE}/satwatch/detectors`, { signal: ctrl.signal });
    if (!res.ok) throw new MetaCallError(`http_${res.status}`, res.status);
    return await res.json();
  } catch (e) {
    if (e instanceof MetaCallError) throw e;
    throw new MetaCallError('unreachable', 0);
  } finally { clearTimeout(timer); }
}
