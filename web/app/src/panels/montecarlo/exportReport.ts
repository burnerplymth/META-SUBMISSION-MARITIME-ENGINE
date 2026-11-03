/* Registry entries for the Monte Carlo run report and the comparison report (PDF, HTML, Save as PDF). */
import { useMemo } from 'react';
import { reportExportable } from '../../exports/report';
import { useExportable } from '../../exports/registry';
import type { ExportableDef } from '../../exports/types';
import type { CompareDoc } from './compare';
import { fmtInt } from './fmt';
import { mcCompareReport, mcRunReport } from './report';
import { runTitle, type RunRec } from './runs';

const OUTLINE_RUN = ['Cover, inputs, seed and digest', 'Added delay and chain size, P10 P50 P90', 'Fan chart per place', 'Distribution and share above a threshold', 'Drivers: sensitivity and convergence', 'Provenance'];
const OUTLINE_CMP = ['Side by side', 'B against A, shared draws', 'Distribution overlay', 'Fan overlay per place', 'Difference by place', 'Inputs and provenance'];

export function mcRunExportable(run: RunRec, nameOf: (id: string) => string): ExportableDef | null {
  if (!run.job.results) return null;
  const rec = run.source === 'recorded';
  return reportExportable({
    id: 'montecarlo:report', windowId: 'montecarlo', title: 'Monte Carlo run report',
    prov: { label: rec ? 'Recorded' : 'Simulated', source: rec ? 'Recorded Monte Carlo run' : 'Seeded input-uncertainty simulation', run_id: run.job.job_id, hash: run.job.results.runs_digest },
    summary: `${runTitle(run, nameOf)}, ${fmtInt(run.job.request.n)} simulations`, outline: OUTLINE_RUN,
    docs: async () => { const d = await mcRunReport(run, nameOf); return d ? [d] : []; },
  });
}

export function mcCompareExportable(a: RunRec, b: RunRec, doc: CompareDoc | null, nameOf: (id: string) => string): ExportableDef | null {
  if (!a.job.results || !b.job.results) return null;
  const rec = a.source === 'recorded' || b.source === 'recorded';
  return reportExportable({
    id: 'montecarlo:compare-report', windowId: 'montecarlo', title: 'Monte Carlo comparison report',
    prov: { label: rec ? 'Recorded' : 'Simulated', source: rec ? 'Recorded Monte Carlo runs' : 'Seeded input-uncertainty simulations', run_id: `${a.job.job_id} and ${b.job.job_id}`, hash: a.job.results.runs_digest },
    summary: `${runTitle(a, nameOf)} and ${runTitle(b, nameOf)}`, outline: OUTLINE_CMP,
    docs: async () => { const d = mcCompareReport(a, b, doc, nameOf); return d ? [d] : []; },
  });
}

export function useMcReportExportables(cur: RunRec | null, a: RunRec | null, b: RunRec | null, doc: CompareDoc | null, nameOf: (id: string) => string): void {
  useExportable(useMemo(() => (cur ? mcRunExportable(cur, nameOf) : null), [cur, nameOf]));
  useExportable(useMemo(() => (a && b ? mcCompareExportable(a, b, doc, nameOf) : null), [a, b, doc, nameOf]));
}
