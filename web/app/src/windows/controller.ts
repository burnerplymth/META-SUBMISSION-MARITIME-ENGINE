/**
 * The window manager's controller: one object that owns the dockview API,
 * the active preset, the user presets and every window operation. React
 * components subscribe to its immutable `state`; the store reaches it only
 * through windows/bridge.ts. No store import here.
 *
 * Remount law: every panel renders
 * with renderer 'always'; a preset switch is `fromJSON(json, {reuseExistingPanels:true})`;
 * the map is never removed: closing it hides its group (`setVisible(false)`).
 */
import type { DockviewApi, DockviewGroupPanel, IDockviewPanel } from 'dockview-react';
import { decodePreset, encodePreset } from './codec';
import { pickDockGroup, placeFloat, type Placement, type Rect } from './placement';
import { MAX_FLOATS, compileDock, decompileDock, mapVisible, normaliseDock, viewsOf, type DockModel, type FloatNode } from './model';
import {
  BUILTIN_PRESETS, CURRENT_KEY, addUserPreset, builtinFor, deleteUserPreset, duplicatePreset, gatedPreset, importPresetText,
  loadUserPresets, presetFileName, presetToFileText, renameUserPreset, saveUserPresets, validatePreset, type KV, type LinkGroupId, type Preset,
} from './presets';
import { WINDOWS, isWindowId, windowAvailable, type DockSide, type WindowGate, type WindowId } from './registry';
import { assignLink, forgetWindow, linkOf, linkStore, linksFromPreset, linksToPreset, type LinkOf } from './linkGroups';
import { wmBridge } from './bridge';

export interface WmState {
  attached: boolean;
  /** The legacy layout name the active preset stands for. */
  legacy: string;
  presetId: string;
  presetName: string;
  builtin: boolean;
  modified: boolean;
  open: WindowId[];
  mapHidden: boolean;
  maximized: WindowId | null;
  floats: number;
  userPresets: Preset[];
  /** Bumps on every applied preset, so effects can key on it. */
  epoch: number;
}

const INITIAL: WmState = {
  attached: false, legacy: 'SupermAP', presetId: 'overview', presetName: 'Overview', builtin: true, modified: false,
  open: [], mapHidden: false, maximized: null, floats: 0, userPresets: [], epoch: 0,
};

type Listener = () => void;
const round = (v: number, q: number) => Math.round(v / q) * q;

/** A structural fingerprint that ignores sub-5% geometry noise: used for the
 *  `Modified` mark, so rounding in dockview never flags a fresh preset. */
export function fingerprint(m: DockModel): string {
  const walk = (n: DockModel['root'] | DockModel['root']['children'][number]): unknown => n.t === 'tabs'
    ? ['t', n.views.join(','), n.visible === false ? 0 : 1, round(n.size, 0.05).toFixed(2)]
    : ['s', n.dir, round(n.size, 0.05).toFixed(2), n.children.map(walk)];
  const fl = (f: FloatNode) => [f.views.join(','), round(f.x, 0.1).toFixed(1), round(f.y, 0.1).toFixed(1), round(f.w, 0.1).toFixed(1), round(f.h, 0.1).toFixed(1)];
  return JSON.stringify([walk(m.root), m.floats.map(fl)]);
}

/** Float geometry for a window: its default size, capped to the workspace, kept fully inside it. */
export function floatRect(def: { w: number; h: number; minW: number; minH: number }, w: number, h: number, ordinalOffset: number) {
  const width = Math.max(Math.min(def.minW, w), Math.min(def.w, Math.round(w * 0.7)));
  const height = Math.max(Math.min(def.minH, h), Math.min(def.h, Math.round(h * 0.92)));
  const x = Math.max(0, Math.min(Math.round(w * 0.3) + ordinalOffset, w - width - 4));
  const y = Math.max(0, Math.min(Math.round(h * 0.06) + ordinalOffset, h - height - 4));
  return { x, y, width, height };
}
/** A docked slot is usable when it clears the window's minimum size with room for the tab strip. */
export const slotFits = (def: { minW: number; minH: number }, slotW: number, slotH: number): boolean =>
  !(slotW > 0 && slotH > 0) || (slotW >= def.minW && slotH >= def.minH + 40);

/** Pixels per keyboard resize press. */
export const RESIZE_STEP = 48;

export class WindowController {
  private api: DockviewApi | null = null;
  private listeners = new Set<Listener>();
  private _state: WmState = INITIAL;
  private gate: WindowGate = { liveTwin: false };
  private kv: KV | null = null;
  private base: Preset = BUILTIN_PRESETS[0];
  private baseFp = '';
  private applying = 0;
  private code: string | null = null;
  /** AUTO DEMO: no autosave until this time (the demo's storage restore is byte-exact). */
  private holdUntil = 0;
  private saveTimer: ReturnType<typeof setTimeout> | null = null;
  private syncTimer: ReturnType<typeof setTimeout> | null = null;
  private disposers: Array<() => void> = [];
  private floatOrdinal = 0;
  private noPlan = false;

  get state(): WmState { return this._state; }
  subscribe = (l: Listener): (() => void) => { this.listeners.add(l); return () => { this.listeners.delete(l); }; };
  getState = (): WmState => this._state;
  private set(patch: Partial<WmState>): void {
    const next = { ...this._state, ...patch };
    // shallow-equal guard so subscribers wake only on a real change
    let same = true;
    for (const k of Object.keys(next) as Array<keyof WmState>) {
      const a = next[k], b = this._state[k];
      if (Array.isArray(a) && Array.isArray(b)) { if (a.length !== b.length || a.some((v, i) => v !== b[i])) same = false; } else if (a !== b) same = false;
    }
    if (same) return;
    this._state = next;
    this.listeners.forEach((l) => l());
  }

  /* ── lifecycle ─────────────────────────────────────────────────────── */
  attach(api: DockviewApi, kv: KV, gate: WindowGate): void {
    this.detach();
    this.api = api; this.kv = kv; this.gate = gate;
    this.set({ attached: true, userPresets: loadUserPresets(kv) });
    const ev = [api.onDidLayoutChange(() => this.onLayoutChange()), api.onDidRemovePanel((p) => this.onRemoved(p.id))];
    this.disposers = ev.map((d) => () => d.dispose());
    wmBridge.attach({
      closeWindow: (id) => this.closeWindow(id), toggleMax: (id) => this.toggleMaximize(id),
      hideMap: () => this.hideMap(), showMap: () => this.showMap(),
      openWindow: (id, how, tabWith) => this.openWindow(id, how ?? 'dock', tabWith),
      captureLayout: () => { this.holdUntil = Infinity; return this.currentPreset(); },
      restoreLayout: (p) => { this.apply(p); this.holdUntil = Date.now() + 1500; }, forceCanonical: () => this.reset(),
      applyCode: (code) => { if (code) void this.applyCode(code); else if (this._state.modified) this.reset(); },
    });
  }
  detach(): void {
    this.disposers.forEach((d) => d()); this.disposers = [];
    if (this.saveTimer) clearTimeout(this.saveTimer);
    if (this.syncTimer) clearTimeout(this.syncTimer);
    if (this.api) wmBridge.detach();
    this.api = null;
    this.set({ attached: false, open: [], mapHidden: false });
  }
  setGate(g: WindowGate): void {
    const changed = g.liveTwin !== this.gate.liveTwin;
    this.gate = g;
    if (changed && this.api) {
      // LIVE TWIN law: flagged windows leave an open layout without a placeholder when the flag goes off.
      if (!g.liveTwin) for (const id of this.openIds()) if (!windowAvailable(id, g)) this.closeWindow(id);
      this.set({ userPresets: this.kv ? loadUserPresets(this.kv) : this._state.userPresets });
    }
  }

  /* ── reading ───────────────────────────────────────────────────────── */
  private openIds(): WindowId[] {
    return (this.api?.panels ?? []).map((p) => p.id).filter(isWindowId);
  }
  private panel(id: WindowId): IDockviewPanel | undefined { return this.api?.getPanel(id) ?? undefined; }
  isOpen(id: WindowId): boolean { return !!this.panel(id); }
  /** The current layout as a validated model (null before attach). */
  snapshot(): DockModel | null {
    if (!this.api) return null;
    const m = decompileDock(this.api.toJSON());
    if (!m) return null;
    const n = normaliseDock(m, { liveTwin: true });
    if (!n) return null;
    const mx = this.maximizedId();
    return mx ? { ...n.dock, maximized: mx } : n.dock;
  }
  private maximizedId(): WindowId | null {
    const a = this.api;
    if (!a) return null;
    for (const g of a.groups) if (g.api.isMaximized()) { const id = g.activePanel?.id; return id && isWindowId(id) ? id : null; }
    return null;
  }
  currentPreset(): Preset {
    const dock = this.snapshot() ?? this.base.dock;
    const ids = viewsOf(dock);
    return { ...this.base, dock, windows: linksToPreset(linkStore.get(), ids), phone: this.base.phone, map: { visible: mapVisible(dock) } };
  }

  /* ── applying presets ─────────────────────────────────────────────── */
  size(): { w: number; h: number } {
    const w = this.api?.width ?? 0, h = this.api?.height ?? 0;
    return { w: w > 50 ? w : 1280, h: h > 50 ? h : 700 };
  }

  /** Switch to a preset. The map panel is reused, never remounted. */
  apply(preset: Preset): void {
    const api = this.api;
    if (!api) return;
    const p = gatedPreset(preset, this.gate);
    const { w, h } = this.size();
    const json = compileDock(p.dock, w, h);
    this.applying += 1;
    try {
      api.fromJSON(json as never, { reuseExistingPanels: true } as never);
      linkStore.set(linksFromPreset(p.windows));
      this.base = p;
      this.baseFp = fingerprint(p.dock);
      const mx = p.dock.maximized ? this.panel(p.dock.maximized) : undefined;
      mx?.group.api.maximize();
    } finally { this.applying -= 1; }
    this.set({ legacy: p.legacy, presetId: p.id, presetName: p.name, builtin: p.builtin, modified: false, epoch: this._state.epoch + 1 });
    this.syncNow();
  }
  applyLegacy(name: string): boolean {
    const b = builtinFor(name);
    if (!b) return false;
    this.apply(b);
    return true;
  }
  /** Boot: the saved current layout when it is valid and belongs to the
   *  same legacy layout; otherwise the built-in. */
  restoreCurrent(legacyHint: string, preferSaved: boolean): void {
    if (preferSaved && this.kv) {
      try {
        const raw = this.kv.get(CURRENT_KEY);
        const o = raw ? (JSON.parse(raw) as { preset?: unknown; id?: unknown }) : null;
        const c = o?.preset ? validatePreset(o.preset, this.gate, { allowBuiltinFlag: true }) : null;
        if (c && c.ok && c.preset.legacy === legacyHint) {
          const id = typeof o?.id === 'string' ? o.id : c.preset.id;
          this.apply({ ...c.preset, id, builtin: BUILTIN_PRESETS.some((b) => b.id === id) });
          const origin = builtinFor(id) ?? this._state.userPresets.find((p) => p.id === id) ?? c.preset;
          this.base = origin; this.baseFp = fingerprint(origin.dock);
          this.set({ presetId: origin.id, presetName: origin.name, builtin: origin.builtin });
          this.onLayoutChange();
          return;
        }
      } catch { /* fall through to the built-in */ }
    }
    this.applyLegacy(legacyHint) || this.apply(BUILTIN_PRESETS[0]);
  }
  /** Reset to the built-in the active preset came from (or the legacy one). */
  reset(): void {
    const b = builtinFor(this.base.builtin ? this.base.id : this.base.legacy) ?? BUILTIN_PRESETS[0];
    this.apply(b);
  }

  /* ── user presets ─────────────────────────────────────────────────── */
  private persistUser(list: Preset[]): void {
    if (this.kv) saveUserPresets(this.kv, list);
    this.set({ userPresets: list });
  }
  saveCurrent(name: string): Preset | null {
    const r = addUserPreset(this._state.userPresets, this.currentPreset().dock, this.currentPreset().windows, { ...this.base, builtin: false }, name);
    if (!r.ok) return null;
    this.persistUser(r.list);
    this.base = r.preset; this.baseFp = fingerprint(r.preset.dock);
    this.set({ presetId: r.preset.id, presetName: r.preset.name, builtin: false, modified: false });
    return r.preset;
  }
  /** Overwrite the active user preset with the current layout. */
  updateActive(): void {
    const cur = this._state.userPresets.find((p) => p.id === this._state.presetId);
    if (!cur) return;
    const snap = this.currentPreset();
    const next = { ...cur, dock: snap.dock, windows: snap.windows, phone: snap.phone, map: snap.map };
    this.persistUser(this._state.userPresets.map((p) => (p.id === cur.id ? next : p)));
    this.base = next; this.baseFp = fingerprint(next.dock);
    this.set({ modified: false });
  }
  rename(id: string, name: string): void {
    const r = renameUserPreset(this._state.userPresets, id, name);
    if (!r.ok) return;
    this.persistUser(r.list);
    if (this._state.presetId === id) { this.base = r.preset; this.set({ presetName: r.preset.name }); }
  }
  duplicate(): Preset | null {
    const src = this._state.builtin ? { ...this.currentPreset(), builtin: false } : this.currentPreset();
    const r = duplicatePreset(this._state.userPresets, src);
    if (!r.ok) return null;
    this.persistUser(r.list);
    this.base = r.preset; this.baseFp = fingerprint(r.preset.dock);
    this.set({ presetId: r.preset.id, presetName: r.preset.name, builtin: false, modified: false });
    return r.preset;
  }
  remove(id: string): void {
    this.persistUser(deleteUserPreset(this._state.userPresets, id));
    if (this._state.presetId === id) this.reset();
  }
  applyUser(id: string): boolean {
    const p = this._state.userPresets.find((x) => x.id === id);
    if (!p) return false;
    this.apply(p);
    return true;
  }
  importText(text: string): Preset | null {
    const r = importPresetText(text, this._state.userPresets);
    if (!r.ok) return null;
    this.persistUser(r.list);
    this.apply(r.preset);
    return r.preset;
  }
  exportFile(): { name: string; text: string } {
    const p = this.currentPreset();
    return { name: presetFileName(p), text: presetToFileText({ ...p, builtin: false }) };
  }
  /** The `preset=` code for the current layout, or null when too large. */
  async shareCode(): Promise<string | null> { return encodePreset(this.currentPreset()); }
  /** Apply a code from a link (validated like every other load). */
  async applyCode(code: string): Promise<boolean> {
    const raw = await decodePreset(code);
    const c = raw ? validatePreset(raw, this.gate) : null;
    if (!c || !c.ok) return false;
    this.apply({ ...c.preset, id: `u-link`, builtin: false });
    return true;
  }

  activeWindowId(): WindowId | null {
    const id = this.api?.activePanel?.id;
    return id && isWindowId(id) ? id : null;
  }

  /* ── window operations ────────────────────────────────────────────── */
  private groupsOnGrid(): DockviewGroupPanel[] { return (this.api?.groups ?? []).filter((g) => g.api.location.type === 'grid'); }

  private pickGroupFor(side: DockSide, exclude?: string, avoid?: DockviewGroupPanel): DockviewGroupPanel | undefined {
    return pickDockGroup(
      this.groupsOnGrid().map((g) => ({ group: g, ids: g.panels.map((p) => p.id), visible: g.api.isVisible })),
      side, (id) => (isWindowId(id) ? WINDOWS[id].defaultDock : undefined), exclude, avoid);
  }

  openWindow(id: WindowId, how: 'dock' | 'float' | 'tab' = 'dock', tabWith?: WindowId): void {
    const api = this.api;
    if (!api || !windowAvailable(id, this.gate)) return;
    const def = WINDOWS[id];
    if (id === 'map') { this.showMap(); this.focusWindow('map'); return; }
    const existing = this.panel(id);
    if (existing) {
      if (how === 'float' && existing.api.location.type !== 'floating') this.floatWindow(id);
      else if (how === 'tab' && tabWith && tabWith !== id) this.tabWindow(id, tabWith);
      else this.focusWindow(id);
      return;
    }
    const base = { id, component: 'window', tabComponent: 'window', title: def.title, renderer: 'always' as const, params: { wid: id }, minimumWidth: def.minW, minimumHeight: def.minH };
    const { w, h } = this.size();
    let forcedDock = false;
    /* The group the person is working in: a float that cannot be placed clear of it joins the dock beside it, never as a tab over it. */
    const focusGroup = api.activePanel?.group;
    if (how === 'dock' && def.defaultDock === 'float') how = 'float'; // float-only windows (Meta AI activity)
    if (how === 'float' && this._state.floats >= MAX_FLOATS) how = 'dock'; // at most six floats
    if (how === 'float') {
      const o = (this.floatOrdinal += 1) % 6 * 28;
      const fr = floatRect(def, w, h, o);
      /* A program-opened float keeps clear of the controls the person is using, or joins the dock. */
      const plan = this.noPlan ? null : this.planFloat(fr);
      if (plan && plan.tab && def.defaultDock !== 'float') { how = 'dock'; forcedDock = true; }
      else {
        api.addPanel({ ...base, floating: plan ? { ...fr, x: plan.rect.x, y: plan.rect.y } : fr } as never);
        this.focusWindow(id);
        return;
      }
    }
    if (how === 'tab' && tabWith && this.panel(tabWith)) {
      api.addPanel({ ...base, position: { referencePanel: tabWith, direction: 'within' } } as never);
      return;
    }
    const grp = this.pickGroupFor(def.defaultDock, undefined, forcedDock ? focusGroup : undefined);
    /* A slot too small for the window floats it instead of squeezing it into a tab. */
    /* With six floats already open the float retry would fall straight back to this branch (unbounded recursion, a
     * RangeError in the click handler): the window then docks into the small slot, which is still usable. */
    if (grp && !slotFits(def, grp.api.width, grp.api.height) && this._state.floats < MAX_FLOATS) {
      /* the dock was the fallback of a float that would cover the focused window: float it anyway, placed */
      if (forcedDock) { this.noPlan = true; try { this.openWindow(id, 'float'); } finally { this.noPlan = false; } return; }
      this.openWindow(id, 'float'); return;
    }
    if (grp) { api.addPanel({ ...base, position: { referenceGroup: grp, direction: 'within' } } as never); return; }
    const anchor = this.panel('map') && this.panel('map')!.group.api.isVisible ? 'map' : this.groupsOnGrid()[0]?.activePanel?.id;
    if (def.defaultDock === 'bottom' && anchor) { api.addPanel({ ...base, position: { referencePanel: anchor, direction: 'below' } } as never); return; }
    if (def.defaultDock === 'left') { api.addPanel({ ...base, position: { direction: 'left' } } as never); return; }
    api.addPanel({ ...base, position: { direction: 'right' } } as never);
  }

  /** Placement for a float opened by the program: measured from the live dock, null without a layout to measure. */
  private planFloat(fr: { x: number; y: number; width: number; height: number }): Placement | null {
    if (typeof document === 'undefined' || !this.api) return null;
    const dock = document.querySelector('.wm-dock');
    if (!dock) return null;
    const origin = dock.getBoundingClientRect();
    if (origin.width < 50 || origin.height < 50) return null;
    const rel = (e: Element): Rect | null => {
      const b = e.getBoundingClientRect();
      return b.width > 0 && b.height > 0 ? { x: b.left - origin.left, y: b.top - origin.top, width: b.width, height: b.height } : null;
    };
    const all = (sel: string): Rect[] => Array.from(dock.querySelectorAll(sel)).map(rel).filter((r): r is Rect => r !== null);
    const g = this.api.activePanel?.group.element;
    const focus = g ? rel(g) : null;
    return placeFloat({
      size: { width: fr.width, height: fr.height }, workspace: { w: origin.width, h: origin.height }, cascade: { x: fr.x, y: fr.y },
      keep: [...all('[data-wm-keep]'), ...all('.wm-actions')], soft: all('.dv-tabs-and-actions-container'), focus,
    });
  }

  focusWindow(id: WindowId): void {
    const p = this.panel(id);
    if (!p) return;
    p.api.setActive();
    if (!p.group.api.isVisible) p.group.api.setVisible(true);
  }

  closeWindow(id: WindowId): void {
    if (id === 'map') { this.hideMap(); return; }
    const p = this.panel(id);
    if (p) this.api?.removePanel(p);
  }
  private onRemoved(id: string): void { if (isWindowId(id)) linkStore.set(forgetWindow(linkStore.get(), id)); }

  hideMap(): void {
    const g = this.panel('map')?.group;
    if (!g) return;
    if (g.api.isMaximized()) g.api.exitMaximized();
    g.api.setVisible(false);
    this.syncNow();
  }
  showMap(): void {
    const g = this.panel('map')?.group;
    if (g && !g.api.isVisible) { g.api.setVisible(true); this.syncNow(); }
  }
  get mapHidden(): boolean { const g = this.panel('map')?.group; return g ? !g.api.isVisible : false; }

  floatWindow(id: WindowId): void {
    const p = this.panel(id);
    const api = this.api;
    if (!p || !api || p.api.location.type === 'floating' || this._state.floats >= MAX_FLOATS) return;
    const def = WINDOWS[id];
    const { w, h } = this.size();
    const o = (this.floatOrdinal += 1) % 6 * 28;
    api.addFloatingGroup(p, floatRect(def, w, h, o));
    p.api.setActive();
  }
  /** Dock a floating window into the grid (tab with its side's group, or a new split). */
  dockWindow(id: WindowId, side?: DockSide): void {
    const p = this.panel(id);
    if (!p || p.api.location.type !== 'floating') return;
    const want = side && side !== 'float' ? side : WINDOWS[id].defaultDock;
    const grp = this.pickGroupFor(want, id);
    if (grp) { p.api.moveTo({ group: grp, position: 'center' }); return; }
    const mg = this.panel('map')?.group;
    const ref = mg && mg !== p.group ? mg : this.groupsOnGrid()[0];
    if (ref) p.api.moveTo({ group: ref, position: want === 'left' ? 'left' : want === 'bottom' ? 'bottom' : 'right' });
  }
  tabWindow(id: WindowId, withId: WindowId): void {
    const p = this.panel(id), t = this.panel(withId);
    if (!p || !t || p === t || p.group === t.group) return;
    p.api.moveTo({ group: t.group, position: 'center' });
    p.api.setActive();
  }
  toggleFloat(id: WindowId): void {
    const p = this.panel(id);
    if (!p) return;
    if (p.api.location.type === 'floating') this.dockWindow(id); else this.floatWindow(id);
  }
  isFloating(id: WindowId): boolean { return this.panel(id)?.api.location.type === 'floating'; }
  toggleMaximize(id: WindowId): void {
    if (id === 'map') this.showMap();
    const g = this.panel(id)?.group;
    if (!g) return;
    if (g.api.isMaximized()) g.api.exitMaximized(); else { g.api.maximize(); this.panel(id)?.api.setActive(); }
    this.syncNow();
  }
  isMaximized(id: WindowId): boolean { return !!this.panel(id)?.group.api.isMaximized(); }
  /** Keyboard resize of a docked window's group: right and down grow, left and up shrink, never below the window's
   *  minimum size. A floating or maximized window is left alone (its Dock and Restore keys come first). */
  resizeWindow(id: WindowId, dir: 'left' | 'right' | 'up' | 'down', step = RESIZE_STEP): boolean {
    const g = this.panel(id)?.group;
    if (!g || g.api.location.type !== 'grid' || g.api.isMaximized()) return false;
    const def = WINDOWS[id];
    const horizontal = dir === 'left' || dir === 'right';
    const sign = dir === 'right' || dir === 'down' ? 1 : -1;
    const next = Math.max(horizontal ? def.minW : def.minH, (horizontal ? g.api.width : g.api.height) + sign * step);
    g.api.setSize(horizontal ? { width: next } : { height: next });
    return true;
  }

  /** F6 / Shift+F6: activate the next or previous visible window group. */
  cycleGroup(dir: 1 | -1): void {
    const gs = (this.api?.groups ?? []).filter((g) => g.api.isVisible && g.panels.length);
    if (!gs.length) return;
    const cur = gs.findIndex((g) => g === this.api?.activeGroup);
    const next = gs[(cur + dir + gs.length) % gs.length];
    next.activePanel?.api.setActive();
  }
  /** Ctrl+] / Ctrl+[: next or previous tab in the active group. */
  cycleTab(dir: 1 | -1): void {
    const g = this.api?.activeGroup;
    if (!g || g.panels.length < 2) return;
    const i = g.panels.findIndex((p) => p === g.activePanel);
    g.panels[(i + dir + g.panels.length) % g.panels.length].api.setActive();
  }

  /* ── link groups ──────────────────────────────────────────────────── */
  setLink(id: WindowId, to: LinkOf, current: { place: string | null; seq: number | null }): void {
    linkStore.set(assignLink(linkStore.get(), id, to, current));
    this.onLayoutChange(true);
  }
  linkOf(id: WindowId): LinkGroupId | null { return linkOf(linkStore.get(), id); }

  /* ── change tracking, autosave ────────────────────────────────────── */
  private syncNow(): void {
    const open = this.openIds().sort();
    const mapHidden = this.mapHidden;
    let floats = 0;
    for (const g of this.api?.groups ?? []) if (g.api.location.type === 'floating') floats += 1;
    this.set({ open, mapHidden, floats, maximized: this.maximizedId() });
    wmBridge.report({ open, mapHidden, code: this._state.modified ? this.code : null });
  }
  private onLayoutChange(force = false): void {
    if (!this.api) return;
    this.syncNow();
    if (this.applying > 0) return;
    if (this.syncTimer) clearTimeout(this.syncTimer);
    this.syncTimer = setTimeout(() => {
      const snap = this.snapshot();
      if (!snap) return;
      const modified = this.baseFp !== '' && fingerprint(snap) !== this.baseFp;
      this.set({ modified: force && !modified ? this._state.modified : modified });
      if (this._state.modified) { void this.shareCode().then((c) => { this.code = c; this.syncNow(); }); } else if (this.code) { this.code = null; this.syncNow(); }
      this.scheduleSave();
    }, 250);
  }
  private scheduleSave(): void {
    if (Date.now() < this.holdUntil) return;
    if (this.saveTimer) clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.saveNow(), 500);
  }
  saveNow(): void {
    if (!this.kv || !this.api || Date.now() < this.holdUntil) return;
    try {
      const p = this.currentPreset();
      this.kv.set(CURRENT_KEY, JSON.stringify({ v: 1, id: this._state.presetId, preset: { ...p, id: this._state.presetId, name: this._state.presetName } }));
    } catch { /* blocked storage: the layout is session-only */ }
  }
}

export const wm = new WindowController();
