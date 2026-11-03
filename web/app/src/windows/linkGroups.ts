/**
 * Link groups A and B. A window belongs to
 * group A, group B, or none. Group A is the app's own selection (the store's
 * selectedNode, which also drives the map, the URL `place=` and the fetched
 * place data); group B has its own selected place; an unlinked window keeps
 * a private one. Selecting a place in a window moves every window of the
 * same group and leaves the others where they were.
 *
 * Pure state machine + a tiny external store (useSyncExternalStore friendly).
 */
import type { LinkGroupId } from './presets';

export type LinkOf = LinkGroupId | null;
export interface Cell { place: string | null; seq: number | null }
export interface LinkState {
  /** Per-window link; a missing entry means group A. */
  links: Readonly<Record<string, LinkOf>>;
  /** Group B's selection, and each unlinked window's own. */
  cells: Readonly<Record<string, Cell>>;
}
export const EMPTY_LINKS: LinkState = { links: {}, cells: {} };
export const EMPTY_CELL: Cell = { place: null, seq: null };

export const linkOf = (s: LinkState, wid: string): LinkOf => (wid in s.links ? s.links[wid] : 'A');
/** The key of the cell a window reads: group B's, or its own when unlinked; null for group A (the store). */
export const cellKey = (s: LinkState, wid: string): string | null => {
  const l = linkOf(s, wid);
  return l === 'A' ? null : l === 'B' ? 'B' : `w:${wid}`;
};
export const nextLink = (l: LinkOf): LinkOf => (l === 'A' ? 'B' : l === 'B' ? null : 'A');

/** Put a window into a group. Joining B (when B is empty) or going private
 *  seeds the cell with the selection the window was showing, so nothing goes
 *  blank. */
export function assignLink(s: LinkState, wid: string, to: LinkOf, current: Cell): LinkState {
  const links = { ...s.links, [wid]: to };
  const next: LinkState = { links, cells: s.cells };
  const key = cellKey(next, wid);
  if (key === null) return next;
  if (key.startsWith('w:') || !(key in s.cells) || s.cells[key].place === null) return { links, cells: { ...s.cells, [key]: current } };
  return next;
}

export const setCell = (s: LinkState, key: string, c: Cell): LinkState => ({ links: s.links, cells: { ...s.cells, [key]: c } });
export const forgetWindow = (s: LinkState, wid: string): LinkState => {
  const links = { ...s.links }; delete links[wid];
  const cells = { ...s.cells }; delete cells[`w:${wid}`];
  return { links, cells };
};
/** Load links from a preset's window params (a preset never carries places). */
export function linksFromPreset(windows: Partial<Record<string, { link?: LinkOf }>>): LinkState {
  const links: Record<string, LinkOf> = {};
  for (const [id, p] of Object.entries(windows)) if (p && p.link !== undefined && p.link !== 'A') links[id] = p.link;
  return { links, cells: {} };
}
export function linksToPreset(s: LinkState, ids: readonly string[]): Record<string, { link: LinkOf }> {
  return Object.fromEntries(ids.map((id) => [id, { link: linkOf(s, id) }]));
}

/* ── external store ──────────────────────────────────────────────────── */
type Listener = () => void;
let state: LinkState = EMPTY_LINKS;
const listeners = new Set<Listener>();
const emit = () => { listeners.forEach((l) => l()); };
export const linkStore = {
  get: (): LinkState => state,
  set(next: LinkState): void { if (next !== state) { state = next; emit(); } },
  subscribe(l: Listener): () => void { listeners.add(l); return () => { listeners.delete(l); }; },
  reset(): void { state = EMPTY_LINKS; emit(); },
};
