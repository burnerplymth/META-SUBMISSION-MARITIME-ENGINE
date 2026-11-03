/**
 * Workspace geometry, pure.
 *
 * The workspace grid is four columns: rail | left | centre (the map) |
 * right. This module owns every geometry decision the shell makes —
 * column widths and their clamps, the grid template for each window
 * state (normal / map closed / panel or map maximized), which column a
 * panel lives in, and the drag / keyboard arithmetic for the dividers —
 * so the store, the App shell and the AUTO DEMO all read the same law
 * and the law is testable without a DOM.
 *
 * Persisted widths are untrusted input (same rule as theme/layout):
 * sanitiseColWidths / parseColWidths return a member of the
 * clamped range or the canonical default for ANY other value.
 */
import type { PanelId } from './types';

export interface ColWidths { left: number; right: number }

export const COL_LEFT_MIN = 200;
export const COL_LEFT_MAX = 480;
export const COL_RIGHT_MIN = 300;
export const COL_RIGHT_MAX = 640;
export const RAIL_W = 34;

/** Canonical geometry: the widths the workspace starts with, forced
 *  for the AUTO DEMO so a run is deterministic regardless of the
 *  presenter's saved layout. */
export const DEFAULT_COLW: ColWidths = { left: 282, right: 424 };
export const CANONICAL_COLW: ColWidths = { ...DEFAULT_COLW };

export interface LayoutGeometry { colW: ColWidths; mapHidden: boolean }
export const CANONICAL_GEOMETRY: LayoutGeometry = { colW: { ...CANONICAL_COLW }, mapHidden: false };

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

export function clampColWidths(w: ColWidths): ColWidths {
  return {
    left: clamp(Math.round(w.left), COL_LEFT_MIN, COL_LEFT_MAX),
    right: clamp(Math.round(w.right), COL_RIGHT_MIN, COL_RIGHT_MAX),
  };
}

/** Validate an untrusted widths value field by field: a finite number
 *  is clamped into range; anything else (string, NaN, null, missing)
 *  falls back to that field's canonical default — never a guess at
 *  what the garbage "meant". */
export function sanitiseColWidths(raw: unknown): ColWidths {
  const o = (raw !== null && typeof raw === 'object') ? raw as Record<string, unknown> : {};
  const num = (v: unknown, def: number) => (typeof v === 'number' && Number.isFinite(v) ? v : def);
  return clampColWidths({ left: num(o.left, DEFAULT_COLW.left), right: num(o.right, DEFAULT_COLW.right) });
}

/** Parse a persisted widths string; unparseable or absent input is
 *  the canonical default. */
export function parseColWidths(text: string | null | undefined): ColWidths {
  if (!text) return { ...DEFAULT_COLW };
  try { return sanitiseColWidths(JSON.parse(text)); } catch { return { ...DEFAULT_COLW }; }
}

export function serializeColWidths(w: ColWidths): string {
  return JSON.stringify(clampColWidths(w));
}

/** Which grid column each panel is rendered in (App.tsx column
 *  containers). The map is the centre column's only panel. */
export type ColumnKey = 'left' | 'centre' | 'right';
export const PANEL_COLUMN: Record<PanelId, ColumnKey> = {
  news: 'left', market: 'left', satwatch: 'left', health: 'left',
  map: 'centre',
  mapcontent: 'right', chaintrace: 'right', chain: 'right', whatif: 'right',
  ensemble: 'right', alerts: 'right', explain: 'right', proof: 'right',
  ledger: 'right', money: 'right', impact: 'right', sim: 'right', desks: 'right',
  livetwin: 'right',
  reports: 'right', exports: 'right', satdesk: 'right',
  ports: 'right', livefeed: 'right', livetape: 'right', livectx: 'right', dataviewer: 'right',
  laneplanner: 'right',
  shipments: 'right',
  watchlists: 'right',
  porttwin: 'right',
  agent: 'right',
  brief: 'right',
  montecarlo: 'right',
  research: 'right',
  replay: 'right',
  expt: 'right',
  scenariobuilder: 'right',
};

/** Explicit grid placement for the shell's four grid items — the
 *  track number styles.css assigns each column via `grid-column`
 *  (rail 1 · left 2 · centre 3 · right 4), matching the track order
 *  of every template gridTemplateColumns() returns. Placement MUST
 *  be explicit, never auto-placement: the centre column is hidden
 *  with `display:none` whenever the map section is not visible
 *  (map closed, a no-map layout like Proof, another panel
 *  maximized), and a `display:none` grid child is not a grid item —
 *  under auto-placement every later column shifts one track left,
 *  so the right column lands in the collapsed centre track and
 *  renders at 0 width while the wide track sits empty.
 *  Explicit placement pins
 *  each column to its own track in every window state; the phone
 *  stack resets all four to track 1 (styles.css, max-width:860px). */
export const GRID_COLUMN: Record<'rail' | ColumnKey, number> = { rail: 1, left: 2, centre: 3, right: 4 };

/** Is the map section visible (its cell has size)? The map component
 *  itself stays MOUNTED in every state — visibility is a grid/display
 *  fact here, never a mount fact. */
export function mapSectionVisible(g: LayoutGeometry, mapInLayout: boolean, maximized: PanelId | null): boolean {
  return mapInLayout && !g.mapHidden && (maximized === null || maximized === 'map');
}

/**
 * The grid template for a window state. Maximizing a panel gives its
 * column the full workspace (the other columns collapse to 0 but
 * their panels' mount state is the caller's concern — the map never
 * unmounts). A closed / out-of-layout map collapses the centre and
 * the right column flexes to take the freed width. Track ORDER is
 * fixed (rail · left · centre · right, GRID_COLUMN) and styles.css
 * places each column in its track explicitly, so a hidden centre
 * can never pull the right column into the collapsed track.
 */
export function gridTemplateColumns(g: LayoutGeometry, mapInLayout: boolean, maximized: PanelId | null, leftEmpty = false): string {
  if (maximized !== null) {
    const col = PANEL_COLUMN[maximized];
    if (col === 'left') return `${RAIL_W}px minmax(0,1fr) 0 0`;
    if (col === 'right') return `${RAIL_W}px 0 0 minmax(0,1fr)`;
    return `${RAIL_W}px 0 minmax(0,1fr) 0`;
  }
  /* A layout with no left-column panel (Live Twin,
   * Report Desk, Satellite Desk) gives the left track back instead of
   * leaving an empty 282 px strip. */
  const left = leftEmpty ? '0' : `${g.colW.left}px`;
  if (!mapSectionVisible(g, mapInLayout, null)) {
    return `${RAIL_W}px ${left} 0 minmax(0,1fr)`;
  }
  return `${RAIL_W}px ${left} minmax(0,1fr) ${g.colW.right}px`;
}

/** True when none of the visible panels lives in the left column. */
export function leftColumnEmpty(visible: readonly PanelId[]): boolean {
  return !visible.some((p) => PANEL_COLUMN[p] === 'left');
}

/** Divider drag arithmetic: the left divider widens its column as the
 *  pointer moves right; the right divider widens its column as the
 *  pointer moves LEFT (the column sits against the right edge). */
export function widthFromDrag(side: 'left' | 'right', startW: number, startX: number, clientX: number): number {
  const delta = side === 'left' ? clientX - startX : startX - clientX;
  const next = side === 'left' ? { left: startW + delta, right: DEFAULT_COLW.right } : { left: DEFAULT_COLW.left, right: startW + delta };
  return clampColWidths(next)[side];
}

/** Keyboard adjustment for a focused divider: arrows step 8px
 *  (32px with shift), Home restores the canonical width. Returns null
 *  for keys the divider does not handle. Direction is column-local:
 *  ArrowRight always widens, ArrowLeft always narrows. */
export function widthFromKey(side: 'left' | 'right', current: number, key: string, shift: boolean): number | null {
  if (key === 'Home') return DEFAULT_COLW[side];
  const step = shift ? 32 : 8;
  let delta = 0;
  if (key === 'ArrowRight') delta = step;
  else if (key === 'ArrowLeft') delta = -step;
  else return null;
  const probe = side === 'left' ? { left: current + delta, right: DEFAULT_COLW.right } : { left: DEFAULT_COLW.left, right: current + delta };
  return clampColWidths(probe)[side];
}

/** State transitions (pure; the store applies them). Closing the map
 *  also clears a map maximize — a closed window is not maximized.
 *  Restoring clears nothing else. Toggling maximize on the map
 *  restores a closed map first: ⤢ on a closed window is a restore. */
export function geometryAfterCloseMap(g: LayoutGeometry, maximized: PanelId | null): { geometry: LayoutGeometry; maximized: PanelId | null } {
  return { geometry: { ...g, mapHidden: true }, maximized: maximized === 'map' ? null : maximized };
}
export function geometryAfterRestoreMap(g: LayoutGeometry): LayoutGeometry {
  return { ...g, mapHidden: false };
}
export function geometryAfterToggleMax(g: LayoutGeometry, maximized: PanelId | null, p: PanelId): { geometry: LayoutGeometry; maximized: PanelId | null } {
  const next = maximized === p ? null : p;
  return { geometry: next === 'map' ? { ...g, mapHidden: false } : g, maximized: next };
}
