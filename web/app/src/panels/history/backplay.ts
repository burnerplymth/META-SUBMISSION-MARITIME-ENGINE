/* HISTORY backplay state: a tiny external store shared by the history
 * panel (which loads the window), the shell's bottom transport bar (which
 * drives the cursor) and the FORKED FROM chips (which read the lineage
 * registry). Kept out of the app store so a play tick re-renders only its
 * subscribers; the app store's sim slice is never touched by a tick.
 * `createBackplay` is a factory that makes an independent instance. */
import { useSyncExternalStore } from 'react';
import {
  clampIso, lineageEntry, playTick, registryAdd, speedMs, stepCursor,
  type DateRange, type EventTick, type LineageEntry, type Speed,
} from './logic';
import type { ForkResponse } from './types';

export interface BackplayState {
  /** The history panel is mounted: the transport bar shows backplay. */
  active: boolean;
  limits: DateRange | null;
  win: DateRange | null;
  cursor: string | null;
  playing: boolean;
  speed: Speed;
  ticks: EventTick[];
  lineage: LineageEntry[];
}
export const REGISTRY_KEY = 'supermap.history.forks';

export interface StorageLike { getItem(k: string): string | null; setItem(k: string, v: string): void }
function safeStore(): StorageLike | null {
  try { return typeof localStorage !== 'undefined' ? localStorage : null; } catch { return null; }
}
export function readRegistry(st: StorageLike | null): LineageEntry[] {
  try {
    const raw = st?.getItem(REGISTRY_KEY);
    if (!raw) return [];
    const v = JSON.parse(raw) as unknown;
    return Array.isArray(v) ? (v as LineageEntry[]).filter((e) => e && typeof e.lineage_id === 'string' && Array.isArray(e.events) && Array.isArray(e.chain_run_ids)) : [];
  } catch { return []; }
}

export function createBackplay(storage: StorageLike | null = safeStore()) {
  let state: BackplayState = {
    active: false, limits: null, win: null, cursor: null, playing: false, speed: 1, ticks: [], lineage: readRegistry(storage),
  };
  const subs = new Set<() => void>();
  const set = (patch: Partial<BackplayState>) => { state = { ...state, ...patch }; subs.forEach((f) => f()); };
  return {
    get: (): BackplayState => state,
    subscribe: (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; },
    setActive: (active: boolean) => set(active ? { active } : { active, playing: false }),
    setLimits: (limits: DateRange | null) => set({ limits }),
    /** A new window; the cursor keeps its place when still inside, else starts at the window start. */
    setWindow: (win: DateRange, cursor?: string) => {
      const c = cursor ?? (state.cursor && state.cursor >= win.from && state.cursor <= win.to ? state.cursor : win.from);
      set({ win, cursor: clampIso(c, win.from, win.to), playing: false });
    },
    setCursor: (d: string) => { if (state.win) set({ cursor: clampIso(d, state.win.from, state.win.to) }); },
    step: (delta: number) => { if (state.win && state.cursor) set({ cursor: stepCursor(state.cursor, delta, state.win), playing: false }); },
    goStart: () => { if (state.win) set({ cursor: state.win.from, playing: false }); },
    goEnd: () => { if (state.win) set({ cursor: state.win.to, playing: false }); },
    play: () => {
      if (!state.win || !state.cursor) return;
      // playing from the end restarts at the window start
      set({ playing: true, cursor: state.cursor >= state.win.to ? state.win.from : state.cursor });
    },
    pause: () => set({ playing: false }),
    toggle: () => { if (state.playing) set({ playing: false }); else if (state.win && state.cursor) set({ playing: true, cursor: state.cursor >= state.win.to ? state.win.from : state.cursor }); },
    setSpeed: (speed: Speed) => set({ speed }),
    setTicks: (ticks: EventTick[]) => set({ ticks }),
    /** One clock beat: advance a day, stop at the window end. */
    beat: () => {
      if (!state.playing || !state.win || !state.cursor) return;
      const next = playTick(state.cursor, state.win);
      if (next === null) set({ playing: false }); else set({ cursor: next });
    },
    intervalMs: () => speedMs(state.speed),
    /** Record a fork's lineage (in memory and, best effort, in storage). */
    recordFork: (resp: ForkResponse, chainRunIds: readonly string[], now: number = Date.now()) => {
      const lineage = registryAdd(state.lineage, lineageEntry(resp, chainRunIds, now));
      try { storage?.setItem(REGISTRY_KEY, JSON.stringify(lineage)); } catch { /* storage blocked: lineage stays in memory */ }
      set({ lineage });
    },
  };
}
export type Backplay = ReturnType<typeof createBackplay>;

export const backplay: Backplay = createBackplay();
export function useBackplay(bp: Backplay = backplay): BackplayState {
  return useSyncExternalStore(bp.subscribe, bp.get, bp.get);
}
