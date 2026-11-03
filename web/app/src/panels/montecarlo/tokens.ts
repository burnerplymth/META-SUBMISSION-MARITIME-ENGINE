/* Owner tokens of Monte Carlo jobs. The server returns a token once, when it
 * creates a job; it is kept per job id in this browser and sent on delete
 * (same pattern as the scenario tokens). A blocked or corrupt store reads as no
 * token: the job then cannot be deleted from here ("Not yours"). */
import type { Fetched, McStart } from './types';

export const TOKEN_KEY = 'supermap.montecarloTokens.v1';
export const MAX_TOKENS = 100;
const ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const TOKEN_RE = /^[A-Za-z0-9_.~-]{8,200}$/;
export interface KV { get(key: string): string | null; set(key: string, value: string): void }

export function loadTokens(kv: KV): Record<string, string> {
  try {
    const j = JSON.parse(kv.get(TOKEN_KEY) ?? 'null') as unknown;
    if (!j || typeof j !== 'object' || Array.isArray(j)) return {};
    const out: Record<string, string> = {};
    for (const [id, t] of Object.entries(j as Record<string, unknown>)) {
      if (ID_RE.test(id) && typeof t === 'string' && TOKEN_RE.test(t)) out[id] = t;
      if (Object.keys(out).length >= MAX_TOKENS) break;
    }
    return out;
  } catch { return {}; }
}

/** Keeps the newest MAX_TOKENS entries (insertion order). Never throws. */
export function rememberToken(kv: KV, id: string, token: string): void {
  if (!ID_RE.test(id) || !TOKEN_RE.test(token)) return;
  try {
    const cur = loadTokens(kv);
    delete cur[id];
    cur[id] = token;
    const keys = Object.keys(cur);
    for (const k of keys.slice(0, Math.max(0, keys.length - MAX_TOKENS))) delete cur[k];
    kv.set(TOKEN_KEY, JSON.stringify(cur));
  } catch { /* storage full or blocked: the job stays undeletable here */ }
}

export function forgetToken(kv: KV, id: string): void {
  try {
    const cur = loadTokens(kv);
    if (!(id in cur)) return;
    delete cur[id];
    kv.set(TOKEN_KEY, JSON.stringify(cur));
  } catch { /* nothing to forget */ }
}

/** Wraps a start call so the token the server returns for a NEW job is kept under its id. */
export function keepingToken<A extends unknown[]>(kv: KV, start: (...a: A) => Promise<Fetched<McStart>>): (...a: A) => Promise<Fetched<McStart>> {
  return async (...a: A) => {
    const r = await start(...a);
    if (r.kind === 'ok' && r.doc.owner_token) rememberToken(kv, r.doc.job_id, r.doc.owner_token);
    return r;
  };
}
