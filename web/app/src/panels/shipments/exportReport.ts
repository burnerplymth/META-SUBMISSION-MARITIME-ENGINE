/* Registry entry for the My shipments run report (PDF, HTML, Save as PDF). */
import { useMemo } from 'react';
import { reportExportable } from '../../exports/report';
import { useExportable } from '../../exports/registry';
import type { ExportableDef } from '../../exports/types';
import type { Row } from './logic';
import { shipmentsReport } from './report';
import type { EvalDoc } from './types';

export function shipmentsExportable(doc: EvalDoc, rows: readonly Row[], sample: boolean, scenarioLabel: string): ExportableDef {
  return reportExportable({
    id: 'shipments:report', windowId: 'shipments', title: 'Shipment exposure report',
    prov: { label: 'Modelled', source: 'Exposure evaluation of the listed shipments', run_id: doc.mc?.job_id ?? null },
    summary: `${doc.summary.n} shipments, ${doc.summary.n_affected} affected, ${scenarioLabel}`,
    outline: ['Summary tiles', 'Most exposed places', 'Added transit per lane', 'Exposed shipments table', 'Monte Carlo overlay and caveats'],
    docs: async () => [shipmentsReport({ doc, rows, sample, scenarioLabel })],
  });
}

export function useShipmentsReport(doc: EvalDoc | null, rows: readonly Row[], sample: boolean, scenarioLabel: string): void {
  useExportable(useMemo(() => (doc ? shipmentsExportable(doc, rows, sample, scenarioLabel) : null), [doc, rows, sample, scenarioLabel]));
}
