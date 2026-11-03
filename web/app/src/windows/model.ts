/**
 * Layout model (pure). The preset's `dock` is SupermAP's own small tree,
 * not dockview's raw JSON: it is what gets validated on every load (disk,
 * URL, import), what the built-ins are written in, and what is compiled to
 * dockview's `SerializedDockview` for `fromJSON(..., {reuseExistingPanels})`
 * and read back from `toJSON()`. Sizes are fractions of the parent, so a
 * preset is resolution independent. Nothing here imports dockview at runtime.
 */
import { WINDOWS, isWindowId, windowAvailable, type WindowGate, type WindowId } from './registry';

export interface TabsNode { t: 'tabs'; views: WindowId[]; active?: WindowId; size: number; visible?: boolean }
export interface SplitNode { t: 'split'; dir: 'h' | 'v'; children: LayoutNode[]; size: number }
export type LayoutNode = TabsNode | SplitNode;
/** A floating group; x, y, w, h are fractions of the workspace. */
export interface FloatNode { views: WindowId[]; active?: WindowId; x: number; y: number; w: number; h: number }
export interface DockModel { root: SplitNode; floats: FloatNode[]; maximized?: WindowId }

export const MAX_WINDOWS = 24;
export const MAX_FLOATS = 6;
export const MAX_DEPTH = 6;
export const MIN_FRACTION = 0.04;

export const tabs = (views: WindowId[], size: number, active?: WindowId, visible?: boolean): TabsNode =>
  ({ t: 'tabs', views, size, ...(active ? { active } : {}), ...(visible === false ? { visible: false } : {}) });
export const split = (dir: 'h' | 'v', size: number, ...children: LayoutNode[]): SplitNode => ({ t: 'split', dir, children, size });
export const floatOf = (views: WindowId[], x: number, y: number, w: number, h: number, active?: WindowId): FloatNode =>
  ({ views, x, y, w, h, ...(active ? { active } : {}) });

export function viewsOf(m: DockModel): WindowId[] {
  const out: WindowId[] = [];
  const walk = (n: LayoutNode) => { if (n.t === 'tabs') out.push(...n.views); else n.children.forEach(walk); };
  walk(m.root);
  for (const f of m.floats) out.push(...f.views);
  return out;
}

export function mapVisible(m: DockModel): boolean {
  let vis = false;
  const walk = (n: LayoutNode) => { if (n.t === 'tabs') { if (n.views.includes('map') && n.visible !== false) vis = true; } else n.children.forEach(walk); };
  walk(m.root);
  return vis || m.floats.some((f) => f.views.includes('map'));
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const num = (v: unknown, d: number) => (typeof v === 'number' && Number.isFinite(v) ? v : d);

/**
 * Validate and normalise an untrusted dock model. Never throws.
 * - unknown or unavailable (flag off) window ids are dropped without a
 *   placeholder, duplicates keep their first occurrence;
 * - empty groups and single-child splits collapse;
 * - sizes clamp to fractions and renormalise per split;
 * - at most MAX_WINDOWS windows and MAX_FLOATS floats; depth at most MAX_DEPTH;
 * - the map window is forced to exist (hidden when the input had none).
 */
export function normaliseDock(raw: unknown, gate: WindowGate): { dock: DockModel; dropped: string[] } | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const seen = new Set<WindowId>();
  const dropped: string[] = [];
  let count = 0;
  const takeViews = (v: unknown): WindowId[] => {
    const out: WindowId[] = [];
    if (!Array.isArray(v)) return out;
    for (const x of v) {
      if (typeof x !== 'string') continue;
      if (!isWindowId(x) || !windowAvailable(x, gate)) { dropped.push(x.slice(0, 40)); continue; }
      if (seen.has(x)) continue;
      if (count >= MAX_WINDOWS) { dropped.push(x); continue; }
      seen.add(x); count += 1; out.push(x);
    }
    return out;
  };
  const node = (n: unknown, depth: number): LayoutNode | null => {
    if (!n || typeof n !== 'object' || depth > MAX_DEPTH) return null;
    const o = n as Record<string, unknown>;
    const size = clamp(num(o.size, 0.5), MIN_FRACTION, 1);
    if (o.t === 'tabs') {
      const views = takeViews(o.views);
      if (!views.length) return null;
      const active = typeof o.active === 'string' && views.includes(o.active as WindowId) ? (o.active as WindowId) : undefined;
      return tabs(views, size, active, o.visible === false ? false : undefined);
    }
    if (o.t === 'split' && Array.isArray(o.children)) {
      const dir = o.dir === 'v' ? 'v' : 'h';
      const kids = o.children.map((c) => node(c, depth + 1)).filter((c): c is LayoutNode => c !== null);
      if (!kids.length) return null;
      if (kids.length === 1) return { ...kids[0], size };
      return { t: 'split', dir, children: kids, size };
    }
    return null;
  };
  let root: SplitNode;
  const rr = node(r.root, 0);
  if (rr && rr.t === 'split') root = rr;
  else if (rr) root = split('h', 1, rr);
  else root = split('h', 1);
  const floats: FloatNode[] = [];
  if (Array.isArray(r.floats)) {
    for (const f of r.floats) {
      if (floats.length >= MAX_FLOATS) break;
      if (!f || typeof f !== 'object') continue;
      const o = f as Record<string, unknown>;
      const views = takeViews(o.views);
      if (!views.length) continue;
      const w = clamp(num(o.w, 0.3), 0.12, 1), h = clamp(num(o.h, 0.35), 0.12, 1);
      floats.push({
        views, w, h, x: clamp(num(o.x, 0.3), 0, 1 - w), y: clamp(num(o.y, 0.2), 0, 1 - h),
        ...(typeof o.active === 'string' && views.includes(o.active as WindowId) ? { active: o.active as WindowId } : {}),
      });
    }
  }
  // Force the map: a layout without one gets a hidden map group.
  const dock: DockModel = { root, floats };
  if (!viewsOf(dock).includes('map')) root.children.push(tabs(['map'], 0.2, undefined, false));
  const mx = typeof r.maximized === 'string' && isWindowId(r.maximized) && viewsOf(dock).includes(r.maximized) ? r.maximized : undefined;
  if (mx) dock.maximized = mx;
  normaliseSizes(dock.root);
  return { dock, dropped };
}

/** Make each split's visible children sum to 1. */
function normaliseSizes(n: LayoutNode): void {
  if (n.t !== 'split') return;
  const vis = n.children.filter((c) => !(c.t === 'tabs' && c.visible === false));
  const total = vis.reduce((a, c) => a + c.size, 0) || 1;
  for (const c of n.children) {
    if (!(c.t === 'tabs' && c.visible === false)) c.size = clamp(c.size / total, MIN_FRACTION, 1);
    normaliseSizes(c);
  }
}

/* ── dockview JSON <-> model ─────────────────────────────────────────── */

type Json = Record<string, unknown>;
const ORIENT = { h: 'HORIZONTAL', v: 'VERTICAL' } as const;

export interface CompiledDock {
  grid: { root: Json; width: number; height: number; orientation: 'HORIZONTAL' | 'VERTICAL' };
  panels: Record<string, Json>;
  activeGroup?: string;
  floatingGroups: Json[];
}

/** Compile a (validated) model to dockview's SerializedDockview for a
 *  workspace of W x H pixels. */
export function compileDock(m: DockModel, W: number, H: number, titleOf: (id: WindowId) => string = (id) => WINDOWS[id].title): CompiledDock {
  W = Math.max(200, Math.round(W)); H = Math.max(200, Math.round(H));
  let gid = 0;
  const panels: Record<string, Json> = {};
  const panelFor = (id: WindowId) => {
    const d = WINDOWS[id];
    panels[id] = {
      id, contentComponent: 'window', tabComponent: 'window', title: titleOf(id), renderer: 'always', params: { wid: id },
      minimumWidth: d.minW, minimumHeight: d.minH,
    };
  };
  let firstActive: string | undefined;
  const leaf = (n: TabsNode, size: number): Json => {
    n.views.forEach(panelFor);
    const id = `g${(gid += 1)}`;
    const activeView = n.active ?? n.views[0];
    if (!firstActive || n.views.includes('map')) firstActive = id;
    return { type: 'leaf', size, ...(n.visible === false ? { visible: false } : {}), data: { views: n.views, activeView, id } };
  };
  const build = (n: LayoutNode, parentDir: 'h' | 'v', sizeAlong: number, w: number, h: number): Json => {
    if (n.t === 'tabs') return leaf(n, sizeAlong);
    const dir = n.dir;
    const along = dir === 'h' ? w : h;
    const vis = n.children.filter((c) => !(c.t === 'tabs' && c.visible === false));
    const total = vis.reduce((a, c) => a + c.size, 0) || 1;
    let used = 0;
    const children = n.children.map((c) => {
      const hidden = c.t === 'tabs' && c.visible === false;
      let px = hidden ? Math.round(along * Math.min(c.size, 0.2)) : Math.round((c.size / total) * along);
      if (!hidden && c === vis[vis.length - 1]) px = Math.max(1, along - used);
      if (!hidden) used += px;
      return build(c, dir, px, dir === 'h' ? px : w, dir === 'h' ? h : px);
    });
    void parentDir;
    return { type: 'branch', size: sizeAlong, data: children };
  };
  const root = build(m.root, 'h', W, W, H);
  const floatingGroups: Json[] = m.floats.map((f) => {
    f.views.forEach(panelFor);
    const id = `f${(gid += 1)}`;
    return {
      data: { views: f.views, activeView: f.active ?? f.views[0], id },
      position: { left: Math.round(f.x * W), top: Math.round(f.y * H), width: Math.round(f.w * W), height: Math.round(f.h * H) },
    };
  });
  return { grid: { root, width: W, height: H, orientation: ORIENT[m.root.dir] }, panels, ...(firstActive ? { activeGroup: firstActive } : {}), floatingGroups };
}

/** Read a live `api.toJSON()` back into a model. Unknown ids are dropped by
 *  the caller's validator pass (normaliseDock). */
export function decompileDock(j: unknown): DockModel | null {
  if (!j || typeof j !== 'object') return null;
  const o = j as Json;
  const grid = o.grid as Json | undefined;
  if (!grid || typeof grid !== 'object') return null;
  const W = Math.max(1, num(grid.width, 1)), H = Math.max(1, num(grid.height, 1));
  const rootOrient: 'h' | 'v' = grid.orientation === 'VERTICAL' ? 'v' : 'h';
  /** Convert the children of a container laid out along `dir` inside a w x h box. */
  const kidsOf = (data: Json[], dir: 'h' | 'v', w: number, h: number): LayoutNode[] => {
    const along = dir === 'h' ? w : h;
    const out: LayoutNode[] = [];
    for (const c of data) {
      const px = num(c.size, 0);
      const size = clamp(px / Math.max(1, along), 0.01, 1);
      const cw = dir === 'h' ? px : w, ch = dir === 'h' ? h : px;
      if (c.type === 'leaf') {
        const d = c.data as Json;
        const views = (Array.isArray(d.views) ? d.views : []).filter((v): v is WindowId => typeof v === 'string' && isWindowId(v));
        if (!views.length) continue;
        const active = typeof d.activeView === 'string' && views.includes(d.activeView as WindowId) ? (d.activeView as WindowId) : undefined;
        out.push(tabs(views, size, active, c.visible === false ? false : undefined));
      } else if (c.type === 'branch' && Array.isArray(c.data)) {
        const childDir: 'h' | 'v' = dir === 'h' ? 'v' : 'h';
        const kids = kidsOf(c.data as Json[], childDir, cw, ch);
        if (kids.length) out.push({ t: 'split', dir: childDir, children: kids, size });
      }
    }
    return out;
  };
  const rootRaw = grid.root as Json;
  if (!rootRaw || rootRaw.type !== 'branch' || !Array.isArray(rootRaw.data)) return null;
  const root: SplitNode = { t: 'split', dir: rootOrient, children: kidsOf(rootRaw.data as Json[], rootOrient, W, H), size: 1 };
  const floats: FloatNode[] = [];
  if (Array.isArray(o.floatingGroups)) {
    for (const fg of o.floatingGroups as Json[]) {
      const d = fg.data as Json | undefined;
      const p = fg.position as Json | undefined;
      if (!d || !p) continue;
      const views = (Array.isArray(d.views) ? d.views : []).filter((v): v is WindowId => typeof v === 'string' && isWindowId(v));
      if (!views.length) continue;
      const left = num(p.left, num(p.right, 0)), top = num(p.top, num(p.bottom, 0));
      floats.push({
        views, x: left / W, y: top / H, w: num(p.width, W * 0.3) / W, h: num(p.height, H * 0.35) / H,
        ...(typeof d.activeView === 'string' && views.includes(d.activeView as WindowId) ? { active: d.activeView as WindowId } : {}),
      });
    }
  }
  return { root, floats };
}
