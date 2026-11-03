/* Recorded copies of the Research API's replies (fixtures/*.json).
 * They back the window when the service does not answer. Whatever comes from here carries the
 * Recorded chip; it is never shown as live. */
import episodesDoc from './fixtures/episodes.json';
import egDoc from './fixtures/ever_given_2021.json';
import laDoc from './fixtures/la_lb_congestion_2021.json';
import balDoc from './fixtures/baltimore_bridge_2024.json';
import { parseAnalyze, parseAssembly, parseEpisodes, parseResult, parseScan, type AnalysisResult, type Assembly, type Episode, type ScanDoc } from './types';
import { requestKey, specKey, type AnalysisSpec, type AssembleRequest } from './plan';

interface Doc { episode: string; request: AssembleRequest; assemble: unknown; analyses: Record<string, unknown>; scan: unknown }
const DOCS: Doc[] = [egDoc, laDoc, balDoc] as unknown as Doc[];

export const recordedEpisodes = (): Episode[] => parseEpisodes(episodesDoc) ?? [];

export function recordedAssembly(req: AssembleRequest): Assembly | null {
  const k = requestKey(req);
  const d = DOCS.find((x) => requestKey(x.request) === k);
  return d ? parseAssembly(d.assemble) : null;
}
export function recordedDoc(assemblyId: string): Doc | null {
  return DOCS.find((d) => (d.assemble as { assembly_id?: string }).assembly_id === assemblyId) ?? null;
}
export function recordedAnalysis(assemblyId: string, spec: AnalysisSpec): AnalysisResult | null {
  const d = recordedDoc(assemblyId);
  const r = d ? d.analyses[specKey(spec)] : null;
  return r ? parseResult(r) : null;
}
export function recordedScan(assemblyId: string): ScanDoc | null {
  const d = recordedDoc(assemblyId);
  return d && d.scan ? parseScan(d.scan) : null;
}
export const recordedPlans = (): AssembleRequest[] => DOCS.map((d) => d.request);
export { parseAnalyze };
