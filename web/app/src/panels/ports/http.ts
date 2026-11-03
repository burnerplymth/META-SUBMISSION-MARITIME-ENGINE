/* Tiny GET helper shared by the PORTS and LIVE panels. Read-only; a
 * timeout and the caller's AbortSignal are both honoured; the HTTP
 * status is kept on the error so the LIVE panels can tell the flag-off
 * 404 (section absent) from a transient failure (section shows DOWN). */
import { API_BASE } from '../../api';

export class HttpError extends Error {
  status: number; code: string;
  constructor(status: number, code: string, message: string) { super(message); this.status = status; this.code = code; }
}

export interface GetOpts { signal?: AbortSignal; timeoutMs?: number }

export async function getJson<T>(path: string, opts: GetOpts = {}): Promise<T> {
  const { signal, timeoutMs = 15000 } = opts;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  if (signal) {
    if (signal.aborted) ctrl.abort();
    else signal.addEventListener('abort', () => ctrl.abort(), { once: true });
  }
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { signal: ctrl.signal, headers: { Accept: 'application/json' } });
  } catch {
    throw new HttpError(0, signal?.aborted ? 'aborted' : 'network', signal?.aborted ? 'aborted' : 'service unreachable');
  } finally {
    clearTimeout(timer);
  }
  const body = (await res.json().catch(() => null)) as Record<string, unknown> | null;
  if (!res.ok) {
    const e = (body?.error ?? {}) as { code?: string; message?: string };
    throw new HttpError(res.status, e.code ?? `http_${res.status}`, e.message ?? `HTTP ${res.status}`);
  }
  return body as T;
}

export const isAbort = (e: unknown): boolean => e instanceof HttpError && e.code === 'aborted';
