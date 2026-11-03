import React from 'react';
import { useDeskStore } from '../../store';
import { nodeLabel } from '../../format';
import { Chip } from '../../shell/Chip';
import { useBackplay } from './backplay';
import { lineageChipMeta, lineageForChain, lineageForSim } from './logic';
import './history.css';

/** FORKED FROM lineage chip (history fork -> run): the place and start
 *  day of each source event. Mounted beside a chain or sim run that was
 *  created from a historical fork; whatever it sits on is SIMULATED, so
 *  the SIMULATED chip travels with it. */
export function ForkedFromChip({ meta, lineageId, className }: { meta: string; lineageId?: string; className?: string }) {
  return (
    <span className={`hx-lineage ${className ?? ''}`.trim()} data-lineage={lineageId ?? ''}>
      <Chip kind="meta" word="Forked from" meta={meta} className="chip--fork" title={lineageId ?? 'Fork'} />
      <Chip kind="sim" />
    </span>
  );
}

/** Renders nothing unless this chain run id was created from a fork. */
export function ChainLineageChip({ runId }: { runId: string | null | undefined }) {
  const bp = useBackplay();
  const d = useDeskStore();
  const e = runId ? lineageForChain(bp.lineage, runId) : null;
  return e ? <ForkedFromChip meta={lineageChipMeta(e, (id) => nodeLabel(id, d.graph))} lineageId={e.lineage_id} /> : null;
}

/** Renders nothing unless this sim journal matches a recorded fork (same
 *  start and same set of disruption places). */
export function SimLineageChip({ journal }: { journal: { start_at: string; disruptions: Array<{ node_id?: string }> } | null | undefined }) {
  const bp = useBackplay();
  const d = useDeskStore();
  const e = lineageForSim(bp.lineage, journal);
  return e ? <ForkedFromChip meta={lineageChipMeta(e, (id) => nodeLabel(id, d.graph))} lineageId={e.lineage_id} /> : null;
}
