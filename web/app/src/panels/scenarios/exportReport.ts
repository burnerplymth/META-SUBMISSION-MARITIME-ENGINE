/* Registry entry for the Scenario timeline run report (PDF, HTML, Save as PDF). */
import { useMemo } from 'react';
import { reportExportable } from '../../exports/report';
import { useExportable } from '../../exports/registry';
import type { ExportableDef } from '../../exports/types';
import { scenarioReport, type ScenarioReportInput } from './report';

export function scenarioExportable(inp: ScenarioReportInput): ExportableDef | null {
  if (inp.doc.shocks.length === 0) return null;
  const run = inp.run;
  return reportExportable({
    id: 'scenariobuilder:report', windowId: 'scenariobuilder', title: 'Scenario timeline report',
    prov: { label: run?.target === 'sim' ? 'Simulated' : 'Modelled', source: 'Scenario builder', run_id: run?.target === 'chain' ? run.runs[0]?.run_id ?? null : null, sim_run_id: run?.target === 'sim' ? run.sim_run_id : null, as_of: inp.doc.startDate },
    summary: `${inp.doc.shocks.length} ${inp.doc.shocks.length === 1 ? 'shock' : 'shocks'}${run ? `, ${run.target === 'sim' ? 'simulation' : 'chain'} run` : ', no run'}`,
    outline: ['Shock timeline chart', 'Shock table with verdicts', 'Overlaps', 'Per-shock chain results', 'Combined delay by place, Approximated'],
    docs: async () => [scenarioReport(inp)],
  });
}

export function useScenarioReport(inp: ScenarioReportInput): void {
  useExportable(useMemo(() => scenarioExportable(inp), [inp.doc, inp.nameOf, inp.validation, inp.rules, inp.run, inp.engineRev])); // eslint-disable-line react-hooks/exhaustive-deps
}
