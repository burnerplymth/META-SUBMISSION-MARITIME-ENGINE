/* Satellite desk fetches. The scan route is admin-only and there
 * is no capability endpoint, so the probe is `POST /satwatch/scan {}`:
 * the route authenticates before it validates, so 401 = not allowed,
 * 422 = allowed (admin, nothing started). The operator bearer token, when
 * a deployment gives one to the browser, is read from sessionStorage and
 * is never logged, echoed or persisted by this module. A deployment that
 * injects the Authorization header at a proxy needs no token here. */
import { API_BASE } from '../../api';
import { CentreError, call, getJson } from '../reports/client';
import {
  FETCH_ROUTE, RECORD_ROUTE, parseAvailability, parseSourceRecord, parseSources,
  type AvailabilityDoc, type SourceId, type SourceRecord, type SourcesDoc,
} from './sources';
import {
  parseSamPlan, parseSamResult, parseVisionPlan, parseVisionResult,
  type SamPlan, type SamResult, type VisionPlan, type VisionResult,
} from './metaAi';
import {
  parseBrief, parseDetectors, parseJob, parseOverlay, parsePlaces, parseSignals, scanBody, scanCapability,
  type BriefDoc, type DetectorsDoc, type Overlay, type PlacesDoc, type ScanJob, type SignalsDoc,
} from './model';

export const OPERATOR_TOKEN_KEY = 'supermap.operator.token';
function operatorHeaders(): Record<string, string> {
  try {
    const t = globalThis.sessionStorage?.getItem(OPERATOR_TOKEN_KEY);
    return t ? { Authorization: `Bearer ${t}` } : {};
  } catch { return {}; }
}

/** Crop image URL from an overlay crop's served `url` ("/satwatch/crops/x.png"). */
export const cropSrc = (url: string): string => `${API_BASE}${url}`;

const need = <T>(v: T | null, what: string): T => { if (v === null) throw new CentreError(`bad_${what}`, 'Reply not usable.'); return v; };

export const fetchPlaces = async (signal?: AbortSignal): Promise<PlacesDoc> => need(parsePlaces(await getJson<unknown>('/satwatch/places', { signal })), 'places');
export const fetchDetectors = async (signal?: AbortSignal): Promise<DetectorsDoc> => need(parseDetectors(await getJson<unknown>('/satwatch/detectors', { signal })), 'detectors');
export const fetchSignals = async (signal?: AbortSignal): Promise<SignalsDoc> => need(parseSignals(await getJson<unknown>('/satwatch/signals', { signal })), 'signals');
export const fetchBrief = async (id: string, signal?: AbortSignal): Promise<BriefDoc> =>
  need(parseBrief(await getJson<unknown>(`/satwatch/brief/${encodeURIComponent(id)}`, { signal })), 'brief');
/** The overlay for one anchorage; a place with no stored scan answers
 *  404 scan_not_found, which the panel prints as NOT SCANNED. */
export async function fetchOverlay(id: string, signal?: AbortSignal): Promise<Overlay | null> {
  try {
    return need(parseOverlay(await getJson<unknown>(`/satwatch/overlay/${encodeURIComponent(id)}`, { signal })), 'overlay');
  } catch (e) {
    if (e instanceof CentreError && e.code === 'scan_not_found') return null;
    throw e;
  }
}

export async function probeScanCapability(signal?: AbortSignal): Promise<'allowed' | 'denied' | 'unknown'> {
  /* The server fails closed without a bearer token, so a session
   * that holds none is denied without asking; the probe would only log a 401. */
  if (!operatorHeaders().Authorization) return 'denied';
  try {
    await call('/satwatch/scan', { method: 'POST', body: {}, headers: operatorHeaders(), signal, timeoutMs: 8000 });
    return 'unknown'; // a 2xx on an empty body would be unexpected; do not show the control on it
  } catch (e) {
    return e instanceof CentreError && e.status ? scanCapability(e.status) : 'unknown';
  }
}
export async function startScan(target: string | 'all', days = 90): Promise<ScanJob> {
  return need(parseJob(await getJson<unknown>('/satwatch/scan', { method: 'POST', body: scanBody(target, days), headers: operatorHeaders(), timeoutMs: 20000 })), 'job');
}
export async function fetchJob(id: string, signal?: AbortSignal): Promise<ScanJob> {
  return need(parseJob(await getJson<unknown>(`/satwatch/scan/${encodeURIComponent(id)}`, { signal })), 'job');
}

/* ── multi-source desk ───────────────────────────────────
 * Reads are public. The two POST routes need the operator token and are
 * only offered when the scan capability probe allowed it. Nothing here
 * sends pixels anywhere: the enhanced view is canvas-local (enhance.ts). */
export const fetchSources = async (signal?: AbortSignal): Promise<SourcesDoc> => need(parseSources(await getJson<unknown>('/satwatch/sources', { signal })), 'sources');
/** `catalogue: false` answers from stored products at once (no live catalogue call); the default adds the live lookup. */
export const fetchAvailability = async (place: string, signal?: AbortSignal, catalogue = true): Promise<AvailabilityDoc> =>
  need(parseAvailability(await getJson<unknown>(`/satwatch/sources/${encodeURIComponent(place)}${catalogue ? '' : '?catalogue=false'}`, { signal, timeoutMs: catalogue ? 20000 : 8000 })), 'availability');

/** The stored product for one source and place; no stored product is null (NOT SCANNED / NOT FETCHED). */
export async function fetchSourceRecord(source: Exclude<SourceId, 'sentinel-2'>, place: string, signal?: AbortSignal): Promise<SourceRecord | null> {
  try {
    return need(parseSourceRecord(await getJson<unknown>(`${RECORD_ROUTE[source]}/${encodeURIComponent(place)}`, { signal })), 'record');
  } catch (e) {
    if (e instanceof CentreError && e.code === 'scan_not_found') return null;
    throw e;
  }
}

/** Admin: fetch (and for Sentinel-1 scan) one place. Slow: the server downloads a crop. */
export async function runSourceFetch(source: Exclude<SourceId, 'sentinel-2'>, place: string): Promise<SourceRecord> {
  return need(parseSourceRecord(await getJson<unknown>(FETCH_ROUTE[source], { method: 'POST', body: { place }, headers: operatorHeaders(), timeoutMs: 180000 })), 'record');
}

/* ── Meta AI on one native crop ──────────────────────────
 * Plans are public reads. A RECORDED run replays a stored real reply (free,
 * public). A LIVE run spends: it carries the operator token and the cost the
 * person confirmed; the server refuses without both. */
export interface CropQuery { source: string; place: string; role: 'now' | 'baseline' | null }
const cropQs = (c: CropQuery, extra?: Record<string, string>): string => {
  const q = new URLSearchParams({ source: c.source, place: c.place });
  if (c.role) q.set('role', c.role);
  for (const [k, v] of Object.entries(extra ?? {})) q.set(k, v);
  return q.toString();
};
export const fetchSamPlan = async (c: CropQuery, prompt: string | null, signal?: AbortSignal): Promise<SamPlan> =>
  need(parseSamPlan(await getJson<unknown>(`/satwatch/sam/plan?${cropQs(c, prompt ? { prompt } : undefined)}`, { signal })), 'plan');
export const fetchVisionPlan = async (c: CropQuery, signal?: AbortSignal): Promise<VisionPlan> =>
  need(parseVisionPlan(await getJson<unknown>(`/satwatch/vision/plan?${cropQs(c)}`, { signal })), 'plan');
export async function runSam(c: CropQuery, prompt: string, live: { confirmCostUsd: number } | null, signal?: AbortSignal): Promise<SamResult> {
  const body = { ...c, prompt, mode: live ? 'live' : 'recorded', ...(live ? { confirm_cost_usd: live.confirmCostUsd } : {}) };
  return need(parseSamResult(await getJson<unknown>('/satwatch/sam/run', { method: 'POST', body, headers: live ? operatorHeaders() : undefined, signal, timeoutMs: 150000 })), 'result');
}
export async function runVision(c: CropQuery, live: { confirmCostUsd: number } | null, signal?: AbortSignal): Promise<VisionResult> {
  const body = { ...c, mode: live ? 'live' : 'recorded', ...(live ? { confirm_cost_usd: live.confirmCostUsd } : {}) };
  return need(parseVisionResult(await getJson<unknown>('/satwatch/vision/run', { method: 'POST', body, headers: live ? operatorHeaders() : undefined, signal, timeoutMs: 150000 })), 'result');
}
/** True when this session holds an operator token (the live run buttons need one). */
export const hasOperatorToken = (): boolean => Object.keys(operatorHeaders()).length > 0;
