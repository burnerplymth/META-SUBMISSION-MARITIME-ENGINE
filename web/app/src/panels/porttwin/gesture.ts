/* PORT TWIN touch gestures: two-finger pinch zoom and two-finger pan from pointer events. Pure (no DOM): the
 * map feeds it pointer ids and canvas positions and draws the view it returns. One finger keeps panning through the map's own drag code; this tracker only acts when two
 * pointers are down. */
import { toWorld, type View } from './geo';

export interface Pt { x: number; y: number }

const mid = (a: Pt, b: Pt): Pt => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
const dist = (a: Pt, b: Pt): number => Math.hypot(a.x - b.x, a.y - b.y);

/** The view after the two touch points moved from (a0, b0) to (a1, b1): the scale follows the change of their
 *  distance, and the world point that was under the old midpoint stays under the new one (so a moving midpoint
 *  pans). Scale is clamped to [min, max]; a degenerate start distance changes nothing. */
export function pinchView(v: View, a0: Pt, b0: Pt, a1: Pt, b1: Pt, min: number, max: number): View {
  const d0 = dist(a0, b0);
  if (d0 < 1) return v;
  const factor = dist(a1, b1) / d0;
  const scale = Math.max(min, Math.min(max, v.scale * (Number.isFinite(factor) && factor > 0 ? factor : 1)));
  const m0 = mid(a0, b0); const m1 = mid(a1, b1);
  const [wx, wy] = toWorld(v, m0.x, m0.y);
  return { ...v, scale, cx: wx - (m1.x - v.w / 2) / scale, cy: wy + (m1.y - v.h / 2) / scale };
}

export interface PinchTracker {
  down(id: number, p: Pt): void;
  up(id: number): void;
  /** Number of pointers currently down. */
  count(): number;
  /** True while two or more pointers are down (the map then ignores one-finger drag and hover). */
  active(): boolean;
  /** A pointer moved: the next view when two pointers are down, else null. */
  move(id: number, p: Pt, v: View, min: number, max: number): View | null;
  reset(): void;
}

export function createPinchTracker(): PinchTracker {
  const pts = new Map<number, Pt>();
  /** The first two pointers down; a third is ignored until one of them lifts. */
  const pair = (): [number, Pt, Pt] | null => {
    const ids = [...pts.keys()];
    return ids.length >= 2 ? [ids[0], pts.get(ids[0])!, pts.get(ids[1])!] : null;
  };
  return {
    down(id, p) { pts.set(id, { ...p }); },
    up(id) { pts.delete(id); },
    count: () => pts.size,
    active: () => pts.size >= 2,
    move(id, p, v, min, max) {
      if (!pts.has(id)) return null;
      const before = pair();
      pts.set(id, { ...p });
      if (!before) return null;
      const ids = [...pts.keys()];
      if (id !== ids[0] && id !== ids[1]) return null;
      const after = pair()!;
      return pinchView(v, before[1], before[2], after[1], after[2], min, max);
    },
    reset() { pts.clear(); },
  };
}
