/**
 * The real ViewDeps: the window controller plus the pieces of the store the
 * client actions need. DockWorkspace registers a factory over the latest
 * store value (singleton.ts), so an action always acts on live state.
 */
import { focusPlaceOnMap } from '../mapFlyTo';
import { builtinFor } from '../windows/presets';
import { wm } from '../windows/controller';
import { openBriefById } from '../brief/hooks';
import { researchNav } from '../panels/research/researchNav';
import type { WindowGate } from '../windows/registry';
import { scenarioOpen, scenarioOrganise } from '../panels/scenarios/bridge';
import type { ViewDeps } from './clientActions';

export interface StoreForView {
  graph: { nodes: unknown[] };
  setMode: (m: 'live' | 'sim') => void;
  selectNode: (id: string | null) => void;
  restoreMap: () => void;
  openChain: (runId: string) => Promise<boolean>;
  liveTwinEnabled: boolean;
}

export function makeViewDeps(s: StoreForView): ViewDeps {
  let ids: Set<string> | null = null;
  const places = (): Set<string> => {
    if (!ids) ids = new Set(s.graph.nodes.map((n) => (n as { id?: unknown }).id).filter((v): v is string => typeof v === 'string'));
    return ids;
  };
  const builder = {
    hasPlace: (id: string) => places().has(id),
    showBuilder: () => { if (!wm.isOpen('scenariobuilder')) wm.openWindow('scenariobuilder', 'dock'); },
  };
  return {
    gate: (): WindowGate => ({ liveTwin: s.liveTwinEnabled }),
    isOpen: (id) => wm.isOpen(id),
    isMaximized: (id) => wm.isMaximized(id),
    isFloating: (id) => wm.isFloating(id),
    openWindow: (id, how, tabWith) => wm.openWindow(id, how, tabWith),
    closeWindow: (id) => wm.closeWindow(id),
    floatWindow: (id) => wm.floatWindow(id),
    dockWindow: (id, side) => wm.dockWindow(id, side),
    tabWindow: (id, w) => wm.tabWindow(id, w),
    toggleMaximize: (id) => wm.toggleMaximize(id),
    applyPreset: (id) => { const b = builtinFor(id); if (!b) return false; wm.apply(b); return true; },
    setMode: (m) => s.setMode(m),
    hasPlace: (id) => places().has(id),
    focusPlace: (id) => { s.restoreMap(); s.selectNode(id); focusPlaceOnMap(id); },
    openBrief: (id) => openBriefById(id),
    showRun: async (runId) => {
      const ok = await s.openChain(runId);
      if (ok && !wm.isOpen('chain')) wm.openWindow('chain', 'dock');
      return ok;
    },
    openResearch: (ask) => { researchNav.requestOpen(ask); wm.openWindow('research', 'dock'); },
    scenarioOpen: (args) => scenarioOpen(args, builder),
    scenarioOrganise: (args) => scenarioOrganise(args, builder),
  };
}
