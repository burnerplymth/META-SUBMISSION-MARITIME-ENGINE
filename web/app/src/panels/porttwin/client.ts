/* PORT TWIN fetches. Read-only GETs plus the one POST (run a simulation).
 * Every function throws HttpError (status, code) so a section can tell
 * NOT SERVED (404) from DOWN (network / 5xx). */
import { API_BASE } from '../../api';
import { HttpError, getJson } from '../ports/http';
import type {
  CompareDoc, FramesDoc, GeoDoc, GeoIndex, Imagery, KpisDoc, PlaceHit, PortProfile, SimBuilding, SimProfileDoc, SimRequest, SimRun, VerifyDoc,
} from './types';

const enc = encodeURIComponent;

export const fetchGeoIndex = (signal?: AbortSignal) => getJson<GeoIndex>('/ports/geo/index', { signal });
export const fetchGeo = (id: string, signal?: AbortSignal) => getJson<GeoDoc>(`/ports/${enc(id)}/geo`, { signal, timeoutMs: 30000 });
export const fetchImagery = (id: string, signal?: AbortSignal) => getJson<Imagery>(`/ports/${enc(id)}/geo/imagery`, { signal });
export const fetchProfile = (id: string, signal?: AbortSignal) => getJson<PortProfile>(`/ports/${enc(id)}/profile`, { signal });
export const fetchSimProfile = (id: string, signal?: AbortSignal) => getJson<SimProfileDoc>(`/ports/${enc(id)}/sim-profile`, { signal });

/** URL of the Sentinel-2 PNG for an <img> / drawImage source. */
export const imageryPngUrl = (id: string): string => `${API_BASE}/ports/${enc(id)}/geo/imagery.png`;

/** A text search over sea ports (GET /ports), for places outside the mapped set. */
export async function searchSeaPlaces(q: string, signal?: AbortSignal): Promise<PlaceHit[]> {
  const p = new URLSearchParams({ mode: 'sea', kind: 'port', q, sort: 'name', order: 'asc', limit: '8' });
  const doc = await getJson<{ places?: Array<{ id?: string; place_id?: string; name?: string }> }>(`/ports?${p.toString()}`, { signal });
  return (doc.places ?? []).flatMap((r) => {
    const id = r.id ?? r.place_id;
    return id && r.name ? [{ id, name: r.name }] : [];
  });
}

async function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<{ status: number; body: T }> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 60000);
  if (signal) {
    if (signal.aborted) ctrl.abort();
    else signal.addEventListener('abort', () => ctrl.abort(), { once: true });
  }
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: 'POST', signal: ctrl.signal, headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
  } catch {
    throw new HttpError(0, signal?.aborted ? 'aborted' : 'network', signal?.aborted ? 'aborted' : 'service unreachable');
  } finally { clearTimeout(timer); }
  const json = (await res.json().catch(() => null)) as (T & { error?: { code?: string; message?: string } }) | null;
  if (!res.ok) throw new HttpError(res.status, json?.error?.code ?? `http_${res.status}`, json?.error?.message ?? `HTTP ${res.status}`);
  return { status: res.status, body: json as T };
}

const sleep = (ms: number, signal?: AbortSignal) => new Promise<void>((resolve, reject) => {
  const t = setTimeout(resolve, ms);
  signal?.addEventListener('abort', () => { clearTimeout(t); reject(new HttpError(0, 'aborted', 'aborted')); }, { once: true });
});

/** POST /ports/{id}/sim. A 202 "building" answer is polled on its self link
 *  (409 sim_not_ready until done), at most `maxPolls` times. */
export async function runSim(id: string, req: SimRequest, signal?: AbortSignal, maxPolls = 40): Promise<SimRun> {
  const first = await postJson<SimRun | SimBuilding>(`/ports/${enc(id)}/sim`, req, signal);
  if (first.status !== 202 && (first.body as SimRun).summary) return first.body as SimRun;
  const self = (first.body as SimBuilding).links?.self;
  if (!self) throw new HttpError(0, 'bad_response', 'no run link');
  for (let i = 0; i < maxPolls; i += 1) {
    await sleep(1500, signal);
    try { return await getJson<SimRun>(self, { signal }); }
    catch (e) { if (!(e instanceof HttpError && e.status === 409)) throw e; }
  }
  throw new HttpError(0, 'timeout', 'run not ready');
}

export const fetchFrames = (id: string, simId: string, fromH: number, toH: number, stepMin: number, signal?: AbortSignal) =>
  getJson<FramesDoc>(`/ports/${enc(id)}/sim/${enc(simId)}/frames?from=${fromH}&to=${toH}&step=${stepMin}`, { signal, timeoutMs: 45000 });
export const fetchKpis = (id: string, simId: string, signal?: AbortSignal) =>
  getJson<KpisDoc>(`/ports/${enc(id)}/sim/${enc(simId)}/kpis`, { signal });
export const fetchCompare = (id: string, simId: string, signal?: AbortSignal) =>
  getJson<CompareDoc>(`/ports/${enc(id)}/sim/${enc(simId)}/compare`, { signal, timeoutMs: 45000 });
export const fetchVerify = (id: string, simId: string, signal?: AbortSignal) =>
  getJson<VerifyDoc>(`/ports/${enc(id)}/sim/${enc(simId)}/verify`, { signal });
