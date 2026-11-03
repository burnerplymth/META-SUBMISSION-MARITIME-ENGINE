/* Registry entry for the Research workbench run report (PDF, HTML, Save as PDF). Analyses the window has not run yet are run when the report is built. */
import { useMemo, useRef } from 'react';
import { reportExportable } from '../../exports/report';
import { useExportable } from '../../exports/registry';
import type { ExportableDef } from '../../exports/types';
import { runAnalysis, type Origin } from './client';
import { analysisSpec, type AnalysisCtx } from './plan';
import { researchReport } from './report';
import { ANALYSIS_KINDS, type AnalysisKind, type AnalysisResult, type Assembly, type Episode, type ScanDoc } from './types';

export interface ResearchExportInput {
  assembly: Assembly; origin: Origin; episode: Episode | null; ctx: AnalysisCtx; loaded: Partial<Record<AnalysisKind, AnalysisResult | null>>; scan: ScanDoc | null;
}

export function researchExportable(inp: ResearchExportInput): ExportableDef {
  const recorded = inp.origin === 'recorded';
  const a = inp.assembly;
  return reportExportable({
    id: 'research:report', windowId: 'research', title: 'Research run report',
    prov: { label: recorded ? 'Recorded' : 'Snapshot', source: 'Historical archive, descriptive analyses', run_id: a.id, as_of: a.to },
    summary: `${inp.episode?.title ?? a.label}, ${a.series.length} series, ${a.events.length} events`,
    outline: ['Episode and dated events', 'Aligned series with event markers', 'Analyses with n and caveats', 'Scenes, detected not verified'],
    docs: async () => {
      const results: Partial<Record<AnalysisKind, AnalysisResult | null>> = { ...inp.loaded };
      await Promise.all(ANALYSIS_KINDS.map(async (k) => {
        if (results[k]) return;
        const spec = analysisSpec(k, inp.ctx);
        if (!spec) return;
        try { const r = await runAnalysis(a, inp.origin, spec); results[k] = r.error ? null : r; } catch { results[k] = null; }
      }));
      return [researchReport({ episode: inp.episode, assembly: a, recorded, ctx: inp.ctx, results, scan: inp.scan })];
    },
  });
}

export function useResearchReport(inp: ResearchExportInput | null): void {
  const ref = useRef(inp); ref.current = inp;
  useExportable(useMemo(() => (inp ? researchExportable(inp) : null), [inp?.assembly, inp?.origin, inp?.episode, inp?.ctx, inp?.loaded, inp?.scan])); // eslint-disable-line react-hooks/exhaustive-deps
}
