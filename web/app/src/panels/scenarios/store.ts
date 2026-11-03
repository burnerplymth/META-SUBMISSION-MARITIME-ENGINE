/* Scenario builder state: one document with undo and redo, the chosen places,
 * the selected rows and the arrangement waiting for Accept. A module-level
 * store (read with useSyncExternalStore), so a closed window keeps its draft
 * and the agent's browser steps can fill it. The draft lives in browser
 * storage as a convenience only; every read tolerates a missing or blocked
 * storage and renders without it. */
import { emptyDoc, parseDraft, setStarts, DEFAULT_START_DATE } from './doc';
import type { BuilderDoc, PendingArrangement } from './types';

export const DRAFT_KEY = 'supermap.scb.draft';
export const PLACES_KEY = 'supermap.scb.places';
export const RECENT_KEY = 'supermap.scb.recent';
export const HISTORY_MAX = 100;
export const RECENT_MAX = 8;

export interface BuilderState {
  doc: BuilderDoc;
  /** Chosen places (the multi-select); each can become a shock row. */
  places: string[];
  /** Selected shock rows for the bulk edit. */
  selected: string[];
  recents: string[];
  pending: PendingArrangement | null;
  canUndo: boolean;
  canRedo: boolean;
}

export interface Storage2 { get(k: string): string | null; set(k: string, v: string): void }
const browserStorage = (): Storage2 => ({
  get(k) { try { return globalThis.localStorage?.getItem(k) ?? null; } catch { return null; } },
  set(k, v) { try { globalThis.localStorage?.setItem(k, v); } catch { /* blocked storage: the draft is session-only */ } },
});

const ids = (text: string | null): string[] => {
  try { const v = JSON.parse(text ?? '[]'); return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(x)).slice(0, 24) : []; } catch { return []; }
};

export class BuilderStore {
  private past: BuilderDoc[] = [];
  private future: BuilderDoc[] = [];
  private lastKey: string | null = null;
  private listeners = new Set<() => void>();
  private snap: BuilderState;

  constructor(private storage: Storage2 = browserStorage()) {
    const doc = parseDraft(storage.get(DRAFT_KEY)) ?? emptyDoc();
    this.snap = { doc, places: ids(storage.get(PLACES_KEY)), selected: [], recents: ids(storage.get(RECENT_KEY)).slice(0, RECENT_MAX), pending: null, canUndo: false, canRedo: false };
  }

  subscribe = (l: () => void): (() => void) => { this.listeners.add(l); return () => { this.listeners.delete(l); }; };
  getState = (): BuilderState => this.snap;

  private emit(patch: Partial<BuilderState>): void {
    this.snap = { ...this.snap, ...patch, canUndo: this.past.length > 0, canRedo: this.future.length > 0 };
    this.storage.set(DRAFT_KEY, JSON.stringify(this.snap.doc));
    this.storage.set(PLACES_KEY, JSON.stringify(this.snap.places));
    this.storage.set(RECENT_KEY, JSON.stringify(this.snap.recents));
    this.listeners.forEach((l) => l());
  }

  /** Replace the document. Edits sharing a `key` (a drag, typing in one field) are one undo step. */
  commit(next: BuilderDoc, key?: string): void {
    const cur = this.snap.doc;
    if (next === cur) return;
    if (!(key && key === this.lastKey)) {
      this.past.push(cur);
      if (this.past.length > HISTORY_MAX) this.past.shift();
    }
    this.future = [];
    this.lastKey = key ?? null;
    const live = new Set(next.shocks.map((s) => s.id));
    this.emit({ doc: next, selected: this.snap.selected.filter((i) => live.has(i)) });
  }
  /** Close the current coalescing group: the next edit is a new undo step. */
  endGesture(): void { this.lastKey = null; }

  undo(): boolean {
    const prev = this.past.pop();
    if (!prev) return false;
    this.future.push(this.snap.doc);
    this.lastKey = null;
    this.emit({ doc: prev });
    return true;
  }
  redo(): boolean {
    const next = this.future.pop();
    if (!next) return false;
    this.past.push(this.snap.doc);
    this.lastKey = null;
    this.emit({ doc: next });
    return true;
  }

  /** A fresh document (Open, New): one undo step, so it can be taken back. */
  load(doc: BuilderDoc): void { this.commit(doc); this.emit({ pending: null }); }
  clear(): void { this.load(emptyDoc(this.snap.doc.startDate || DEFAULT_START_DATE)); }

  /* places and rows */
  setPlaces(places: string[]): void { this.emit({ places: places.slice(0, 24) }); }
  addPlace(id: string): void {
    if (this.snap.places.includes(id) || this.snap.places.length >= 24) return;
    this.emit({ places: [...this.snap.places, id] });
  }
  removePlace(id: string): void { this.emit({ places: this.snap.places.filter((p) => p !== id) }); }
  noteRecent(placeIds: readonly string[]): void {
    const merged = [...placeIds, ...this.snap.recents.filter((r) => !placeIds.includes(r))].slice(0, RECENT_MAX);
    this.emit({ recents: merged });
  }
  setSelected(rows: string[]): void { this.emit({ selected: rows }); }
  toggleSelected(id: string): void {
    const s = this.snap.selected;
    this.emit({ selected: s.includes(id) ? s.filter((x) => x !== id) : [...s, id] });
  }

  /* the arrangement waiting for Accept */
  setPending(p: PendingArrangement | null): void { this.emit({ pending: p }); }
  acceptPending(): boolean {
    const p = this.snap.pending;
    if (!p) return false;
    this.commit(setStarts(this.snap.doc, p.starts));
    this.endGesture();
    this.emit({ pending: null });
    return true;
  }
  discardPending(): void { if (this.snap.pending) this.emit({ pending: null }); }
}

let singleton: BuilderStore | null = null;
export function getBuilder(): BuilderStore {
  if (!singleton) singleton = new BuilderStore();
  return singleton;
}
