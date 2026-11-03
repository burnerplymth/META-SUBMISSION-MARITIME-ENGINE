/* LANE GEOMETRY: the lane polylines of the sea legs, fetched once per page and handed to the map.
 * The simplified set comes first (world zoom), the full set a moment later; a failed fetch leaves the map on
 * the chords it always had (the map's own Approx. chip is absent then: no lane set, nothing is approximate). */
import { useEffect, useState } from 'react';
import { api } from './api';
import { buildLaneIndex, type LaneSet } from '../../map/src/lanes/laneIndex';

export type LaneGeometryState = 'idle' | 'loading' | 'ready' | 'notserved';
export interface LaneGeometryView { lanes: LaneSet | null; state: LaneGeometryState }

let cached: LaneSet | null = null;
let pending: Promise<LaneSet | null> | null = null;
let failed = false;

/** Fetch both detail levels once (shared across mounts). Resolves null when the service has none. */
export function loadLaneSet(fullDelayMs = 1200): Promise<LaneSet | null> {
  if (cached) return Promise.resolve(cached);
  if (failed) return Promise.resolve(null);
  pending ??= (async () => {
    try {
      const coarse = buildLaneIndex(await api.laneGeometry('coarse'));
      cached = { coarse, full: null };
      return cached;
    } catch {
      failed = true;
      return null;
    }
  })();
  void pending.then((set) => {
    if (!set) return;
    setTimeout(() => {
      api.laneGeometry('full').then((doc) => { cached = { coarse: set.coarse, full: buildLaneIndex(doc) }; listeners.forEach((l) => l(cached)); }, () => undefined);
    }, fullDelayMs);
  });
  return pending;
}

const listeners = new Set<(s: LaneSet | null) => void>();

export function useLaneGeometry(enabled: boolean): LaneGeometryView {
  const [view, setView] = useState<LaneGeometryView>(() => ({ lanes: cached, state: cached ? 'ready' : 'idle' }));
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    const onSet = (s: LaneSet | null) => { if (live && s) setView({ lanes: s, state: 'ready' }); };
    listeners.add(onSet);
    if (!cached) setView((v) => (v.state === 'idle' ? { lanes: null, state: 'loading' } : v));
    void loadLaneSet().then((s) => { if (live) setView(s ? { lanes: s, state: 'ready' } : { lanes: null, state: 'notserved' }); });
    return () => { live = false; listeners.delete(onSet); };
  }, [enabled]);
  return view;
}
