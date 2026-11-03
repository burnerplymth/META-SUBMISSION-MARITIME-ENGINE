/* The playhead lives outside React state: playback moves it every animation
 * frame and only the map, the scrubber and the chart marker read it, so the
 * rest of the panel does not re-render 60 times a second. */
import { useSyncExternalStore } from 'react';

export interface Playhead {
  get(): number;
  set(hours: number): void;
  subscribe(fn: () => void): () => void;
}
export function createPlayhead(initial = 0): Playhead {
  let v = initial;
  const subs = new Set<() => void>();
  return {
    get: () => v,
    set: (h) => { if (h === v) return; v = h; for (const f of subs) f(); },
    subscribe: (fn) => { subs.add(fn); return () => { subs.delete(fn); }; },
  };
}
export function usePlayhead(p: Playhead): number {
  return useSyncExternalStore(p.subscribe, p.get, p.get);
}

/** Playback speeds in simulated hours per real second. */
export const SPEEDS = [6, 24, 72] as const;
export type Speed = typeof SPEEDS[number];

/** Next playhead position after `dtMs` of real time, clamped to the run; `end` marks that playback ran out. */
export function advance(h: number, dtMs: number, speed: number, endH: number): { h: number; end: boolean } {
  const n = h + (dtMs / 1000) * speed;
  return n >= endH ? { h: endH, end: true } : { h: n, end: false };
}
