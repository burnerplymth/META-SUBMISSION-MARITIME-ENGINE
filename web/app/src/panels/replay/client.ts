/* Fetch layer of the Replay window. Speaks the house error shape; nothing here throws into a panel.
 * 404 / 405 / 501, or a 2xx that is not the expected document (the SPA fallback of a dev server), is NOT SERVED;
 * a network error or a 5xx is DOWN. */
import { API_BASE } from '../../api';
import type { EpisodeDoc, EpisodeList } from './types';

const TIMEOUT_MS = 30000;

export type Fetched<T> = { kind: 'ok'; doc: T } | { kind: 'notserved' } | { kind: 'down'; code: string };

async function getJson(path: string, signal?: AbortSignal): Promise<{ res: Response; body: Record<string, unknown> | null } | 'network'> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  if (signal) {
    if (signal.aborted) ctrl.abort();
    else signal.addEventListener('abort', () => ctrl.abort(), { once: true });
  }
  try {
    const res = await fetch(`${API_BASE}${path}`, { headers: { Accept: 'application/json' }, signal: ctrl.signal });
    const body = (await res.json().catch(() => null)) as Record<string, unknown> | null;
    return { res, body };
  } catch { return 'network'; } finally { clearTimeout(timer); }
}

function settle<T>(r: Awaited<ReturnType<typeof getJson>>, ok: (b: Record<string, unknown>) => boolean): Fetched<T> {
  if (r === 'network') return { kind: 'down', code: 'network' };
  const { res, body } = r;
  if (res.status === 404 || res.status === 405 || res.status === 501) return { kind: 'notserved' };
  if (!res.ok) return { kind: 'down', code: `http_${res.status}` };
  if (!body || !/json/i.test(res.headers.get('content-type') ?? '') || !ok(body)) return { kind: 'notserved' };
  return { kind: 'ok', doc: body as unknown as T };
}

/** GET /replay/episodes. Route-restriction episodes only when asked for. */
export async function fetchEpisodes(includeRestricted: boolean, signal?: AbortSignal): Promise<Fetched<EpisodeList>> {
  const q = includeRestricted ? '?include_route_restrictions=1' : '';
  return settle<EpisodeList>(await getJson(`/replay/episodes${q}`, signal), (b) => b.schema === 'supermap.replay.episodes/1' && Array.isArray(b.episodes));
}

/** GET /replay/episodes/{id}. */
export async function fetchEpisode(id: string, signal?: AbortSignal): Promise<Fetched<EpisodeDoc>> {
  return settle<EpisodeDoc>(await getJson(`/replay/episodes/${encodeURIComponent(id)}`, signal),
    (b) => b.schema === 'supermap.replay.episode/1' && Array.isArray(b.engines) && !!b.observed);
}
