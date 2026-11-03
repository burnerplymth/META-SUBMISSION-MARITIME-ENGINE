/* Registry entry for the Lane planner run report (PDF, HTML, Save as PDF). */
import { useMemo } from 'react';
import { reportExportable } from '../../exports/report';
import { useExportable } from '../../exports/registry';
import type { ExportableDef } from '../../exports/types';
import { provenanceOf } from './exportChart';
import { planTitle } from './logic';
import { laneReport } from './report';
import type { PlanDoc } from './types';

export function laneExportable(doc: PlanDoc): ExportableDef {
  const p = provenanceOf(doc);
  return reportExportable({
    id: 'laneplanner:report', windowId: 'laneplanner', title: 'Lane plan report',
    prov: { label: p.chip === 'SIMULATED' ? 'Simulated' : 'Modelled', source: p.source ?? 'Lane planner', run_id: doc.plan_id, hash: doc.journal.inputs_sha256 ?? null },
    summary: `${planTitle(doc)}, ${doc.routes.length} ${doc.routes.length === 1 ? 'option' : 'options'}`,
    outline: ['Transit time range chart', 'Options table', 'Voyage timeline', 'Distance checks', 'Named variants and sources'],
    docs: async () => [laneReport(doc)],
  });
}

export function useLaneReport(doc: PlanDoc | null): void {
  useExportable(useMemo(() => (doc ? laneExportable(doc) : null), [doc]));
}
