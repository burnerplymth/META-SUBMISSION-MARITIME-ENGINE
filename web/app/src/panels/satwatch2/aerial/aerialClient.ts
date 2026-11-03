/* Fetches for the aerial SAM desk. Reads are public; fetch and live run carry the operator token
 * when this session holds one (read from sessionStorage, never logged or echoed). */
import { API_BASE } from '../../../api';
import { CentreError, getJson } from '../../reports/client';
import { OPERATOR_TOKEN_KEY } from '../client';
import { parsePlan, parseResult, parseScenes, parseSources, type AerialPlan, type AerialResult, type AerialSource, type ScenesDoc } from './aerialModel';

const need = <T>(v: T | null, what: string): T => { if (v === null) throw new CentreError(`bad_${what}`, 'Reply not usable.'); return v; };
function operatorHeaders(): Record<string, string> {
  try { const t = globalThis.sessionStorage?.getItem(OPERATOR_TOKEN_KEY); return t ? { Authorization: `Bearer ${t}` } : {}; } catch { return {}; }
}
export const hasToken = (): boolean => Object.keys(operatorHeaders()).length > 0;
export const imageSrc = (scene: string): string => `${API_BASE}/satwatch/aerial/image/${encodeURIComponent(scene)}`;
export { API_BASE };

export const fetchSources = async (signal?: AbortSignal): Promise<AerialSource[]> => need(parseSources(await getJson<unknown>('/satwatch/aerial/sources', { signal })), 'sources');
export const fetchScenes = async (signal?: AbortSignal): Promise<ScenesDoc> => need(parseScenes(await getJson<unknown>('/satwatch/aerial/scenes', { signal })), 'scenes');
export const fetchPlan = async (scene: string, phrases: readonly string[], signal?: AbortSignal): Promise<AerialPlan> =>
  need(parsePlan(await getJson<unknown>(`/satwatch/aerial/plan?scene=${encodeURIComponent(scene)}&phrases=${encodeURIComponent(phrases.join(','))}`, { signal })), 'plan');
export async function runRecorded(scene: string, phrases: readonly string[], signal?: AbortSignal): Promise<AerialResult> {
  return need(parseResult(await getJson<unknown>('/satwatch/aerial/run', { method: 'POST', body: { scene, phrases: [...phrases], mode: 'recorded' }, signal })), 'result');
}
export async function runLive(scene: string, phrases: readonly string[], confirmCostUsd: number, signal?: AbortSignal): Promise<AerialResult> {
  const body = { scene, phrases: [...phrases], mode: 'live', confirm_cost_usd: confirmCostUsd };
  return need(parseResult(await getJson<unknown>('/satwatch/aerial/run', { method: 'POST', body, headers: operatorHeaders(), signal, timeoutMs: 240000 })), 'result');
}
export async function fetchNative(place: string, signal?: AbortSignal): Promise<void> {
  await getJson<unknown>('/satwatch/aerial/fetch', { method: 'POST', body: { place }, headers: operatorHeaders(), signal, timeoutMs: 180000 });
}
