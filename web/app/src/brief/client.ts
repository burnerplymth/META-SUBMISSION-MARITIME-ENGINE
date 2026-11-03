/* Fetch layer of the brief window. Speaks the house error shape; a 404 on the
 * route (a server without the brief package) is NOT SERVED, a network error or
 * 5xx is DOWN. Nothing here throws into a panel. `fetch` is injectable. */
import { API_BASE } from '../api';
import type { BriefDoc, BriefEstimate, BriefRequest, BriefResult, BriefStatus } from './types';

const TIMEOUT_MS = 90_000;
type FetchLike = typeof fetch;

export interface BriefApi {
  status(): Promise<BriefResult<BriefStatus>>;
  estimate(req: BriefRequest): Promise<BriefResult<BriefEstimate>>;
  write(req: BriefRequest, regenerate?: boolean): Promise<BriefResult<BriefDoc>>;
  get(id: string): Promise<BriefResult<BriefDoc>>;
  /** Download an export (html, pdf, json) as a Blob. */
  download(url: string): Promise<BriefResult<Blob>>;
}

const BRIEF_ID = /^brf_[0-9a-f]{16}$/;
export const isBriefId = (v: unknown): v is string => typeof v === 'string' && BRIEF_ID.test(v);

/** Export paths are served by this API only; anything else is refused. */
export const EXPORT_PATH = /^\/brief\/brf_[0-9a-f]{16}\/export\?format=(html|pdf|json)$/;

export function makeBriefApi(fetchImpl: FetchLike = (...a) => fetch(...a), base: string = API_BASE): BriefApi {
  async function call<T>(method: 'GET' | 'POST', path: string, body?: unknown, parse: (r: Response) => Promise<T> = (r) => r.json() as Promise<T>): Promise<BriefResult<T>> {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
    let res: Response;
    try {
      res = await fetchImpl(`${base}${path}`, {
        method, signal: ctrl.signal, headers: body === undefined ? { Accept: 'application/json' } : { 'content-type': 'application/json', Accept: 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch { clearTimeout(timer); return { kind: 'down', code: 'network' }; }
    clearTimeout(timer);
    if (res.ok) {
      /* A dev server answers an unknown route with its HTML shell: not served. */
      if (!/export\?/.test(path) && !/json/i.test(res.headers.get('content-type') ?? '')) return { kind: 'notserved' };
      try { return { kind: 'ok', doc: await parse(res) }; } catch { return { kind: 'down', code: 'bad_response' }; }
    }
    const j = (await res.json().catch(() => null)) as { error?: { code?: string; message?: string } } | null;
    const err = j?.error;
    /* A route the server does not have answers 404 / 405 with no house error body. */
    if (!err && (res.status === 404 || res.status === 405 || res.status === 501)) return { kind: 'notserved' };
    const code = err?.code ?? `http_${res.status}`;
    if (res.status >= 500 && res.status !== 503) return { kind: 'down', code };
    return { kind: 'refused', code, message: err?.message ?? '' };
  }
  return {
    status: () => call<BriefStatus>('GET', '/brief/status'),
    estimate: (req) => call<BriefEstimate>('POST', '/brief/estimate', req),
    write: async (req, regenerate = false) => {
      const r = await call<BriefDoc>('POST', '/brief', regenerate ? { ...req, regenerate: true } : req);
      if (r.kind === 'ok' && (r.doc?.schema !== 'brief/1' || !isBriefId(r.doc.brief_id))) return { kind: 'notserved' };
      return r;
    },
    get: async (id) => {
      if (!isBriefId(id)) return { kind: 'refused', code: 'invalid_request', message: '' };
      const r = await call<BriefDoc>('GET', `/brief/${id}`);
      if (r.kind === 'ok' && r.doc?.schema !== 'brief/1') return { kind: 'notserved' };
      return r;
    },
    download: (url) => {
      if (!EXPORT_PATH.test(url)) return Promise.resolve({ kind: 'refused', code: 'invalid_request', message: '' } as BriefResult<Blob>);
      return call<Blob>('GET', url, undefined, (r) => r.blob());
    },
  };
}

/** Short state words for an error code (no raw codes shown to a person). */
export function errorWord(code: string): string {
  switch (code) {
    case 'run_not_found': return 'Run not found';
    case 'spend_cap_reached': return 'Call limit reached';
    case 'rate_limited': case 'http_429': return 'Rate limited';
    case 'busy': return 'Renderer busy';
    case 'renderer_unavailable': return 'Renderer not served';
    case 'brief_disabled': return 'Briefs off';
    case 'network': return 'Not reachable';
    case 'print_blocked': return 'Print blocked';
    case 'brief_not_found': return 'Brief not found';
    default: return 'Not available';
  }
}

/** Verifier reason codes in words. */
export function reasonWord(code: string): string {
  const base = code.split(':')[0];
  const words: Record<string, string> = {
    number_not_in_facts: 'Number not in the register', number_word_not_in_facts: 'Number not in the register',
    place_not_in_facts: 'Place not in the register', place_id_in_text: 'Place id in text', source_not_in_facts: 'Source not in the register',
    url_in_text: 'Link in text', unknown_fact: 'Unknown fact', malformed_placeholder: 'Malformed reference', no_fact_reference: 'No fact referenced',
    causal_wording: 'Causal wording', accuracy_wording: 'Claim wording', prediction_wording: 'Claim wording', vocabulary: 'Vocabulary',
    hype_wording: 'Register', first_person: 'Register', style: 'Style', too_long: 'Too long', empty: 'Empty', section_missing: 'Section missing',
    unreadable_reply: 'Reply not readable', derived_quantity_wording: 'Derived quantity',
  };
  return words[base] ?? 'Not accepted';
}

/** An SVG from this server is drawn inline only when it carries no script,
 *  event handler, external reference or embedded object. */
export function safeSvg(svg: string): string | null {
  if (typeof svg !== 'string' || !svg.trimStart().startsWith('<svg')) return null;
  if (/<script|<foreignObject|<iframe|<object|<embed|[\s/"']on[a-z]+\s*=|javascript:|@import|url\(\s*["']?(?!#)|(?:href|src)\s*=\s*["']?(?:https?:|data:|\/\/)/i.test(svg)) return null;
  return svg;
}
