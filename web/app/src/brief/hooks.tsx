/* The Brief button and the open hook other windows use (the causal chain, the
 * scenario loop and, when they exist, the Monte Carlo and shipments windows).
 * A window that has a run id (and optionally a Monte Carlo job id, a lane or an
 * exposure summary) renders <BriefButton request={...} /> or calls openBrief().
 * Nothing here depends on those windows being present. */
import React from 'react';
import { useDeskStore } from '../store';
import { Button } from '../ui';
import { wm } from '../windows/controller';
import { getBrief } from './controller';
import type { BriefRequest } from './types';

/** Write (or fetch) the brief for a request and show the Brief window. */
export function openBrief(req: BriefRequest): void {
  void getBrief().request(req);
  wm.openWindow('brief', 'dock');
}

/** Show a stored brief by id (the agent's open_brief). */
export async function openBriefById(id: string): Promise<boolean> {
  const ok = await getBrief().loadById(id);
  if (ok) wm.openWindow('brief', 'dock');
  return ok;
}

export function BriefButton({ request, size = 'sm', className = '' }: { request: BriefRequest | null; size?: 'sm' | 'md'; className?: string }) {
  return (
    <Button size={size} icon="file-text" className={`brief-btn${className ? ` ${className}` : ''}`} data-brief-button disabled={!request}
      aria-label="Brief" onClick={() => { if (request) openBrief(request); }}>Brief</Button>
  );
}

/** Header action of the causal chain window: the loaded run. */
export function ChainBriefAction() {
  const s = useDeskStore();
  const run = s.chain?.run_id;
  return <BriefButton request={run ? { run_id: run } : null} />;
}
