import { registerHandoff, type HandoffKind, type HandoffScenario } from '../panels/scenarios/bridge';
import { wm } from '../windows/controller';
import { mcRunHandoff, mcSlot, pickDisruption, shipRunSlot, shipSlot, type Slot, type PickedDisruption } from './pick';

/** Register the two hand-offs once at start. The window opens after the pick is stored, so it reads it on mount. */
export function installHandoffs(open: (id: 'montecarlo' | 'shipments') => void = defaultOpen): () => void {
  let seq = 0;
  const on = (kind: HandoffKind, slot: Slot<PickedDisruption>, win: 'montecarlo' | 'shipments') => registerHandoff(kind, (s: HandoffScenario) => {
    seq += 1;
    const p = pickDisruption(s, seq);
    if (!p) return;
    slot.put(p);
    open(win);
  });
  const offs = [on('monte-carlo', mcSlot, 'montecarlo'), on('shipments', shipSlot, 'shipments')];
  return () => offs.forEach((f) => f());
}

function defaultOpen(id: 'montecarlo' | 'shipments'): void {
  try { if (!wm.isOpen(id)) wm.openWindow(id, 'dock'); } catch { /* no window manager: the window is already shown */ }
}

let runSeq = 0;
/** Send a finished served Monte Carlo run to My shipments (job id and disruption); false when the run cannot be sent. */
export function sendRunToShipments(
  job: Parameters<typeof mcRunHandoff>[0], source: 'server' | 'recorded',
  open: (id: 'montecarlo' | 'shipments') => void = defaultOpen,
): boolean {
  runSeq += 1;
  const h = mcRunHandoff(job, source, runSeq);
  if (!h) return false;
  shipRunSlot.put(h);
  open('shipments');
  return true;
}
