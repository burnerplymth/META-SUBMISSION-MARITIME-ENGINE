/**
 * EXPLAIN CHIPS: the row above a chain narrative
 * (POST /llm/explain). Built from the served provenance block:
 *
 *   model text passed the post-check  -> Meta AI badge (model, mode, prompt
 *                                        id, cost, cached in its popover)
 *                                        + Post-check pass
 *   post-check failed -> deterministic template served -> Template chip,
 *                                        and NOT the badge
 *   older server with no provenance   -> the legacy transport/cached
 *                                        words, exactly as before
 */
import React, { useEffect } from 'react';
import { transportLabel } from '../format';
import { Chip } from '../shell/Chip';
import { AiBadge } from './AiBadge';
import { recordAiCall } from './aiCalls';
import { explainMeta } from './provenance';

export const EXPLAIN_DID = 'Explained 1 run';

export function ExplainChips({ data }: {
  data: { run_id?: string; provenance?: unknown; narrative_source?: unknown; post_check?: unknown; transport?: string; cached?: boolean };
}) {
  const m = explainMeta(data);
  const legacy = !m.provenance && !m.template && typeof data.transport === 'string' && data.transport;
  const runId = typeof data.run_id === 'string' ? data.run_id : undefined;
  useEffect(() => {
    if (m.provenance) recordAiCall({ surface: 'explain', did: EXPLAIN_DID, provenance: m.provenance, key: runId ? `explain:${runId}` : undefined });
    // one log entry per answer that reached this page
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, m.provenance?.prompt_id, m.provenance?.transport]);
  return (
    <div className="explain-chips">
      {m.provenance && <AiBadge provenance={m.provenance} did={EXPLAIN_DID} />}
      {m.template && <Chip kind="meta" word="Rule-based answer" meta="No model text" />}
      {m.postCheck && <Chip kind="meta" word={`Post-check ${m.postCheck.replace(/^TEMPLATE_/, '').replace(/_/g, ' ').toLowerCase()}`} />}
      {legacy && (
        <>
          <Chip kind="meta" word={transportLabel(data.transport) === 'RECORDED' ? 'Recorded responses' : 'Live model'} />
          {typeof data.cached === 'boolean' && <Chip kind="meta" word={data.cached ? 'Cached' : 'Fresh'} />}
        </>)}
    </div>
  );
}
