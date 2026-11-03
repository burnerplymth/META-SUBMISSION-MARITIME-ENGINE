/**
 * Floating windows snap while they are dragged: to the
 * workspace edges and to the edges of other floats within 8 px, else to an
 * 8 px grid. Pure, passed to dockview's `transformFloatingGroupDrag` (a core
 * option; dockview's Smart Guides module is enterprise and is not used, so
 * there are no guide lines). Holding Alt while dragging turns snapping off.
 */
export const SNAP_PX = 8;
export interface Box { left: number; top: number; width: number; height: number }
export interface SnapInput {
  proposed: Box;
  container: { width: number; height: number };
  others: readonly Box[];
  alt?: boolean;
}

const nearest = (v: number, targets: readonly number[]): number | null => {
  let best: number | null = null;
  for (const t of targets) if (Math.abs(t - v) <= SNAP_PX && (best === null || Math.abs(t - v) < Math.abs(best - v))) best = t;
  return best;
};

export function snapFloat({ proposed, container, others, alt }: SnapInput): { left: number; top: number } {
  if (alt) return { left: proposed.left, top: proposed.top };
  const { width: w, height: h } = proposed;
  const xs = [0, ...others.flatMap((o) => [o.left, o.left + o.width])];
  const ys = [0, ...others.flatMap((o) => [o.top, o.top + o.height])];
  // a window's left or right edge may meet an x target; its top or bottom edge a y target
  const l = nearest(proposed.left, xs), r = nearest(proposed.left + w, [container.width, ...xs]);
  const t = nearest(proposed.top, ys), b = nearest(proposed.top + h, [container.height, ...ys]);
  const left = l !== null && (r === null || Math.abs(l - proposed.left) <= Math.abs(r - w - proposed.left)) ? l : r !== null ? r - w : Math.round(proposed.left / SNAP_PX) * SNAP_PX;
  const top = t !== null && (b === null || Math.abs(t - proposed.top) <= Math.abs(b - h - proposed.top)) ? t : b !== null ? b - h : Math.round(proposed.top / SNAP_PX) * SNAP_PX;
  return { left: Math.max(0, left), top: Math.max(0, top) };
}
