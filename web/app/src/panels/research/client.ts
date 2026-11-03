/* Calls of the Research window. The service answers first; when it does not answer (network, timeout, route
 * absent) the recorded copy answers and the result is marked Recorded. A refusal from the service is shown as
 * a refusal, never swapped for the recorded copy. */
import { api, ApiError } from '../../api';
import { defaultPlan, requestKey, type AnalysisSpec, type AssembleRequest } from './plan';
import { recordedAnalysis, recordedAssembly, recordedEpisodes, recordedScan } from './recorded';
import { parseAnalyze, parseAssembly, parseEpisodes, parseScan, type AnalysisResult, type Assembly, type Episode, type ScanDoc } from './types';

export type Origin = 'server' | 'recorded';
export interface Loaded<T> { value: T; origin: Origin }
export interface Failure { code: string; recordedMissing?: boolean }

const unreachable = (e: unknown): boolean => e instanceof ApiError && (e.code === 'network' || e.code === 'timeout' || e.code === 'http_404' || e.code === 'http_502' || e.code === 'http_503');
export const failureOf = (e: unknown): Failure => ({ code: e instanceof ApiError ? e.code : 'network' });

export async function loadEpisodes(): Promise<Loaded<Episode[]>> {
  try {
    const eps = parseEpisodes(await api.researchEpisodes());
    if (eps && eps.length) return { value: eps, origin: 'server' };
    throw new ApiError('bad_reply', '');
  } catch (e) {
    if (e instanceof ApiError && !unreachable(e) && e.code !== 'bad_reply') throw e;
    return { value: recordedEpisodes(), origin: 'recorded' };
  }
}

export async function loadAssembly(req: AssembleRequest): Promise<Loaded<Assembly>> {
  try {
    const a = parseAssembly(await api.researchAssemble(req));
    if (!a) throw new ApiError('bad_reply', '');
    return { value: a, origin: 'server' };
  } catch (e) {
    if (e instanceof ApiError && !unreachable(e)) throw e;
    const r = recordedAssembly(req);
    if (r) return { value: r, origin: 'recorded' };
    throw new ApiError('recorded_only', 'Not in the recorded copy');
  }
}

export async function runAnalysis(a: Assembly, origin: Origin, spec: AnalysisSpec): Promise<AnalysisResult> {
  if (origin === 'recorded') {
    return recordedAnalysis(a.id, spec) ?? { id: `rec_${spec.kind}`, kind: spec.kind, label: '', inputs: {}, n: null, nUnit: null, estimate: {}, ci: null, hasPointwiseCi: false, chart: null, caveats: [], error: { code: 'recorded_only', message: '' } };
  }
  const rs = parseAnalyze(await api.researchAnalyze({ assembly_id: a.id, analyses: [spec] }));
  if (!rs || !rs[0]) throw new ApiError('bad_reply', '');
  return rs[0];
}

export async function loadScan(a: Assembly, origin: Origin): Promise<ScanDoc | null> {
  if (origin === 'recorded') return recordedScan(a.id);
  const usable = a.scenes.filter((s) => s.usable);
  const place = usable[0]?.place ?? a.places[0]?.id;
  const ids = usable.filter((s) => s.place === place).map((s) => s.sceneId).slice(0, 40);
  if (!ids.length || !place) return null;
  try {
    return parseScan(await api.researchScan({ place, scene_ids: ids, detector: 'sam', mode: 'recorded' }));
  } catch { return null; }
}

export { defaultPlan, requestKey };
