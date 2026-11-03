/**
 * Placement of windows opened by the program (agent, command, handoff), as
 * pure geometry. A new float is placed where it covers the least of what the
 * person is working in: header controls and composers ("keep" rectangles)
 * first, header strips and the focused window second. When no spot clears the
 * keep rectangles, or the best spot still covers more than 30% of the focused
 * window, the window is tabbed into the dock instead. Windows the person drags
 * or floats by hand never come through here.
 */
export interface Rect { x: number; y: number; width: number; height: number }
export interface PlaceInput {
  /** Floating size, already capped to the workspace. */
  size: { width: number; height: number };
  workspace: { w: number; h: number };
  /** Cascade origin, the spot used when it is clear. */
  cascade: { x: number; y: number };
  /** Controls and composers that must stay clickable. */
  keep: readonly Rect[];
  /** Header strips: avoided when there is room. */
  soft: readonly Rect[];
  /** The window the person is working in. */
  focus: Rect | null;
}
export interface Placement { rect: Rect; tab: boolean; focusCover: number }

/** Largest share of the focused window a float may cover before it is tabbed instead. */
export const MAX_FOCUS_COVER = 0.3;
const STEP = 40;
const EDGE = 4;

export const area = (r: Rect): number => Math.max(0, r.width) * Math.max(0, r.height);
export function intersect(a: Rect, b: Rect): number {
  const w = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
  const h = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
  return w > 0 && h > 0 ? w * h : 0;
}
/** Share of `b` covered by `a` (0 to 1). */
export function overlapRatio(a: Rect, b: Rect): number {
  const ab = area(b);
  return ab > 0 ? intersect(a, b) / ab : 0;
}
const sum = (r: Rect, list: readonly Rect[]): number => list.reduce((n, k) => n + intersect(r, k), 0);

/** Header strip of a window rectangle. */
export const headerOf = (r: Rect, h = 40): Rect => ({ x: r.x, y: r.y, width: r.width, height: Math.min(h, r.height) });

const clampInto = (v: number, size: number, bound: number): number => Math.max(0, Math.min(v, bound - size - EDGE));

/** Free-rect search over a coarse grid plus the cascade origin. */
export function placeFloat(i: PlaceInput): Placement {
  const { size, workspace, cascade, keep, soft, focus } = i;
  const at = (x: number, y: number): Rect => ({ x: clampInto(x, size.width, workspace.w), y: clampInto(y, size.height, workspace.h), width: size.width, height: size.height });
  const cost = (r: Rect): number =>
    sum(r, keep) * 1000 + sum(r, soft) * 8 + (focus ? intersect(r, focus) : 0) + (Math.abs(r.x - cascade.x) + Math.abs(r.y - cascade.y)) * 0.01;
  const cands: Rect[] = [at(cascade.x, cascade.y)];
  const maxX = Math.max(0, workspace.w - size.width - EDGE), maxY = Math.max(0, workspace.h - size.height - EDGE);
  for (let x = 0; x <= maxX + STEP; x += STEP) for (let y = 0; y <= maxY + STEP; y += STEP) cands.push(at(x, y));
  let best = cands[0], bestCost = cost(best);
  for (const c of cands) { const k = cost(c); if (k < bestCost - 1e-6) { best = c; bestCost = k; } }
  const focusCover = focus ? overlapRatio(best, focus) : 0;
  const tab = sum(best, keep) > 0 || focusCover > MAX_FOCUS_COVER;
  return { rect: best, tab, focusCover };
}

/** A grid group as the dock sees it: what it holds and whether it shows. */
export interface DockCandidate<G> { group: G; ids: readonly string[]; visible: boolean }

/**
 * The grid group a window docks into: the last visible group holding a window of the same default side (the map
 * never counts). `exclude` skips groups holding only that window; `avoid` skips one group outright. A window opened
 * by the program passes the focused group as `avoid`, so the answer or composer the person is reading is never put
 * behind a new tab.
 */
export function pickDockGroup<G>(groups: readonly DockCandidate<G>[], side: string, sideOf: (id: string) => string | undefined, exclude?: string, avoid?: G): G | undefined {
  const ok = groups.filter((c) => c.ids.length > 0 && !c.ids.every((id) => id === exclude) && c.visible && c.group !== avoid);
  const bySide = ok.filter((c) => c.ids.some((id) => id !== 'map' && sideOf(id) === side));
  return bySide.length ? bySide[bySide.length - 1].group : undefined;
}
