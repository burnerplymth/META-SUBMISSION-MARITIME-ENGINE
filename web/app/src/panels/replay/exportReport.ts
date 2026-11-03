/* Registry entry for the Replay run report (PDF, HTML, Save as PDF). */
import { useMemo } from 'react';
import { reportExportable } from '../../exports/report';
import { useExportable } from '../../exports/registry';
import type { ExportableDef } from '../../exports/types';
import type { ExportInput } from './exportCompare';
import { replayReport } from './report';

export function replayExportable(inp: ExportInput, windowId: 'replay' | 'proof' = 'replay'): ExportableDef {
  return reportExportable({
    id: `${windowId}:report`, windowId, title: 'Replay run report',
    prov: { label: 'Recorded', source: `Replay · ${inp.doc.title} · ${inp.engine?.label ?? 'No engine'}`, run_id: inp.engine?.chain?.run_id ?? null, hash: inp.engine?.chain?.stream_hash ?? null, as_of: inp.doc.window.end },
    summary: `${inp.doc.title}, ${inp.rows.length} compared rows`,
    outline: ['Three columns on one time axis', 'Dated reports and modelled steps', 'Published misses', 'Full comparison table', 'Provenance and hashes'],
    docs: async () => [replayReport(inp)],
  });
}

export function useReplayReport(inp: ExportInput | null, windowId: 'replay' | 'proof' = 'replay'): void {
  useExportable(useMemo(() => (inp ? replayExportable(inp, windowId) : null), [inp, windowId]));
}
