/* Registry entry for the causal chain run report (PDF, HTML, Save as PDF). */
import { useMemo } from 'react';
import { reportExportable } from '../../exports/report';
import { useExportable } from '../../exports/registry';
import type { ExportableDef } from '../../exports/types';
import { chainReport, type ChainReportInput } from './report';

export function chainExportable(inp: ChainReportInput, windowId: 'chain' | 'chaintrace' = 'chain'): ExportableDef {
  return reportExportable({
    id: `${windowId}:report`, windowId, title: windowId === 'chaintrace' ? 'Chain diagram report' : 'Causal chain report',
    prov: { label: 'Modelled', source: 'Stored run record (causal chain)', run_id: inp.chain.run_id, hash: inp.chain.stream_hash, as_of: inp.asOf },
    summary: `${inp.chain.events.length} steps, ${new Set(inp.chain.events.map((e) => e.node_id)).size} places`,
    outline: ['Diagram of steps and legs', 'Cascade timeline', 'Steps table', 'Legs table', 'Provenance'],
    docs: async () => { const d = chainReport(inp); return d ? [d] : []; },
  });
}

export function useChainReport(inp: ChainReportInput | null, windowId: 'chain' | 'chaintrace' = 'chain'): void {
  useExportable(useMemo(() => (inp && inp.chain.events.length ? chainExportable(inp, windowId) : null), [inp?.chain, inp?.label, inp?.mode, inp?.spec, inp?.asOf, windowId])); // eslint-disable-line react-hooks/exhaustive-deps
}
