import type { PanelId } from './types';
import { isPresetCode } from './windows/codec';
import { legacyNameFor } from './windows/presets';

/* URL state — the shareable slice of the workspace, kept in the hash.
 *
 * Grammar:  #key=value&key=value&flag
 *   layout  layout name            (validated against LAYOUTS + the flag-gated 'Live Twin';
 *                                   a preset id such as 'chain-room' aliases its layout name)
 *   preset  window layout code      (windows/codec.ts: base64url of the layout JSON, <= 2000 chars;
 *                                   additive: a link without it is unchanged)
 *   mode    live | sim             (shell mode; omitted when live, the default)
 *   place   selected place id
 *   run     chain run id           (GET /chain/:run_id)
 *   sim     sim run id             (GET /sim/run/:id)
 *   tick    sim playhead tick      (only meaningful with sim)
 *   focus   focused panel id
 *   rsh     Research window state: episode or places plus a date window
 *           (panels/research/urlHash.ts owns the value grammar)
 * Bare flags: light, replay (the pre-existing deep links) and child
 * (set on a tab the app opened itself, so ✕ may close it).
 *
 * Pure module: no DOM, no storage — the store wires it to history.
 * Everything parsed from a URL is untrusted input; unknown keys, bad
 * values and over-long ids are dropped rather than coerced. */

export const URL_FLAGS = ['light', 'replay', 'child'] as const;
export type UrlFlag = typeof URL_FLAGS[number];

export const URL_PANEL_IDS: readonly PanelId[] = [
  'news', 'market', 'map', 'mapcontent', 'chain', 'chaintrace', 'whatif', 'proof', 'ledger',
  'money', 'satwatch', 'explain', 'health', 'ensemble', 'alerts', 'sim', 'impact', 'desks', 'livetwin',
  'reports', 'exports', 'satdesk',
  'ports', 'livefeed', 'livetape', 'livectx', 'dataviewer', 'laneplanner',
  'porttwin', 'agent', 'brief', 'montecarlo', 'shipments', 'watchlists', 'research', 'expt', 'scenariobuilder', 'replay',
];

export type UrlMode = 'live' | 'sim';

export interface UrlState {
  layout: string | null;
  preset: string | null;
  mode: UrlMode | null;
  place: string | null;
  run: string | null;
  sim: string | null;
  tick: number | null;
  focus: PanelId | null;
  /** Research window: episode, or places and dates (opaque here; validated by panels/research/urlHash). */
  research: string | null;
  flags: UrlFlag[];
}

export const EMPTY_URL_STATE: UrlState = {
  layout: null, preset: null, mode: null, place: null, run: null, sim: null, tick: null, focus: null, research: null, flags: [],
};

/** Ids are opaque slugs (place ids, run_/sim run hashes); anything else is dropped. */
const ID_RE = /^[A-Za-z0-9_.:-]{1,128}$/;
const MAX_TICK = 100000;
const RSH_RE = /^[A-Za-z0-9_.:~+,-]{1,200}$/;

function decode(v: string): string | null {
  try { return decodeURIComponent(v); } catch { return null; }
}

export function parseUrlState(hash: string): UrlState {
  const out: UrlState = { ...EMPTY_URL_STATE, flags: [] };
  const body = hash.startsWith('#') ? hash.slice(1) : hash;
  if (!body) return out;
  for (const token of body.split('&')) {
    if (!token) continue;
    const eq = token.indexOf('=');
    if (eq < 0) {
      const f = decode(token);
      if (f && (URL_FLAGS as readonly string[]).includes(f) && !out.flags.includes(f as UrlFlag)) out.flags.push(f as UrlFlag);
      continue;
    }
    const key = token.slice(0, eq);
    const val = decode(token.slice(eq + 1));
    if (val === null || val === '') continue;
    switch (key) {
      case 'layout': if (val.length <= 40) out.layout = val; break;
      case 'preset': if (isPresetCode(val)) out.preset = val; break;
      case 'mode': if (val === 'live' || val === 'sim') out.mode = val; break;
      case 'place': if (ID_RE.test(val)) out.place = val; break;
      case 'run': if (ID_RE.test(val)) out.run = val; break;
      case 'sim': if (ID_RE.test(val)) out.sim = val; break;
      case 'tick': {
        if (/^\d{1,6}$/.test(val)) { const n = Number(val); if (n <= MAX_TICK) out.tick = n; }
        break;
      }
      case 'focus': if ((URL_PANEL_IDS as readonly string[]).includes(val)) out.focus = val as PanelId; break;
      case 'rsh': if (RSH_RE.test(val)) out.research = val; break;
      default: break; // unknown key: dropped
    }
  }
  out.flags.sort();
  // tick without a sim run has no meaning
  if (out.sim === null) out.tick = null;
  return out;
}

/** Canonical serialisation: fixed key order, nulls omitted, flags last
 *  and sorted — so equal states always serialise to the same string
 *  (the history writer compares strings to avoid duplicate entries). */
export function serializeUrlState(s: UrlState): string {
  const parts: string[] = [];
  const kv = (k: string, v: string | number | null) => {
    if (v !== null && v !== '') parts.push(`${k}=${encodeURIComponent(String(v))}`);
  };
  kv('layout', s.layout);
  kv('preset', s.preset);
  kv('mode', s.mode);
  kv('place', s.place);
  kv('run', s.run);
  kv('sim', s.sim);
  if (s.sim !== null) kv('tick', s.tick);
  kv('focus', s.focus);
  kv('rsh', s.research);
  for (const f of [...s.flags].sort()) parts.push(encodeURIComponent(f));
  return parts.length ? `#${parts.join('&')}` : '';
}

export function sameUrlState(a: UrlState, b: UrlState): boolean {
  return serializeUrlState(a) === serializeUrlState(b);
}

export type NavKind = 'push' | 'replace' | 'none';

/** History policy. A new history entry for meaningful navigation —
 *  layout, place, chain run, sim run, focused panel. Scrubbing the sim
 *  playhead only rewrites the current entry (a scrub is not a place you
 *  go back to). Identical states touch nothing. */
export function navKind(prev: UrlState, next: UrlState): NavKind {
  if (sameUrlState(prev, next)) return 'none';
  /* `mode` is deliberately NOT a push trigger: it is a view family that
   * follows the sim slice (opening a sim run flips it a render later), so
   * pushing on it would give one navigation two history entries. A manual
   * mode switch that matters changes layout or focus, which do push. */
  if (prev.layout !== next.layout || prev.place !== next.place || prev.run !== next.run
    || prev.sim !== next.sim || prev.focus !== next.focus) return 'push';
  return 'replace';
}

/** The live-twin law at the URL boundary: a 'Live Twin' layout in a URL
 *  resolves to SupermAP while the flag is off — never a ghost section.
 *  Returns null for a name that is not a layout at all. */
export function resolveUrlLayout(
  name: string | null, layoutNames: readonly string[], liveTwinName: string, liveTwinEnabled: boolean,
): string | null {
  if (name === null) return null;
  name = legacyNameFor(name) ?? name; // a preset id resolves to its old layout name
  if (name === liveTwinName) return liveTwinEnabled ? name : 'SupermAP';
  return layoutNames.includes(name) ? name : null;
}

/** Back/forward availability, tracked from history.state (each entry the
 *  app creates carries its index). `max` is the highest index seen in
 *  this tab. At index 0 there is nothing inside the app to go back to,
 *  so ‹ is disabled instead of leaving the app. */
export interface HistoryPos { idx: number; max: number }
export const canBack = (p: HistoryPos): boolean => p.idx > 0;
export const canForward = (p: HistoryPos): boolean => p.idx < p.max;

export function readHistoryIdx(state: unknown): number | null {
  if (state && typeof state === 'object' && 'sm' in state) {
    const n = (state as { sm: unknown }).sm;
    if (typeof n === 'number' && Number.isInteger(n) && n >= 0 && n < 1_000_000) return n;
  }
  return null;
}

/** sessionStorage key holding this tab's highest history index (survives
 *  a reload of the tab, which keeps its history entries). */
export const HISTORY_MAX_KEY = 'supermap.hist.max';
export function parseHistoryMax(raw: string | null): number | null {
  if (raw === null || !/^\d{1,6}$/.test(raw)) return null;
  const n = Number(raw);
  return n < 1_000_000 ? n : null;
}

/** The position when the app loads. A tab opened fresh has no
 *  history.state from us, so it starts at 0/0 — the stored max is only
 *  trusted on a reload (state present): a tab opened by window.open can
 *  inherit its opener's sessionStorage, which must not enable › at 0. */
export function initialHistoryPos(state: unknown, storedMax: number | null): HistoryPos {
  const idx = readHistoryIdx(state);
  if (idx === null) return { idx: 0, max: 0 };
  return { idx, max: Math.max(idx, storedMax ?? 0) };
}

/** A push creates entry idx+1 and drops everything ahead of it. */
export function posAfterNav(pos: HistoryPos, kind: NavKind): HistoryPos {
  return kind === 'push' ? { idx: pos.idx + 1, max: pos.idx + 1 } : pos;
}

/** A popstate lands on an entry. One that carries our index is a known
 *  entry; one that does not (a hash typed into the address bar) is a new
 *  entry the browser inserted after the current one. */
export function posAfterPop(pos: HistoryPos, state: unknown): HistoryPos {
  const idx = readHistoryIdx(state);
  if (idx === null) return { idx: pos.idx + 1, max: pos.idx + 1 };
  return { idx, max: Math.max(pos.max, idx) };
}

/** Flags the history writer carries forward: `child` survives from the
 *  URL as loaded (the tab stays closable), `light` mirrors the theme, and
 *  `replay` is a boot-only deep link — the run it loaded is in `run=`. */
export function carriedFlags(prev: readonly UrlFlag[], light: boolean): UrlFlag[] {
  const out: UrlFlag[] = [];
  if (prev.includes('child')) out.push('child');
  if (light) out.push('light');
  return out.sort();
}

/** The URL a new browser tab opens on: this tab's current state, marked
 *  as app-opened so its ✕ is allowed to close it. */
export function newTabHash(current: UrlState): string {
  return serializeUrlState({ ...current, flags: current.flags.includes('child') ? current.flags : [...current.flags, 'child'] });
}

/* ── In-flight sim run (cross-tab discovery) ─────────────────────────
 * localStorage key written when a create-then-poll build is accepted
 * (HTTP 202) and removed when the build settles. `sim_run_id` is a hash
 * of the journal core (which includes the pinned weather snapshot hash),
 * so another tab cannot re-derive it — it has to be told. */
export const SIM_INFLIGHT_KEY = 'supermap.sim.inflight';
export interface SimInflight { simRunId: string; since: number }

export function parseSimInflight(raw: string | null): SimInflight | null {
  if (!raw) return null;
  try {
    const v = JSON.parse(raw) as unknown;
    if (!v || typeof v !== 'object') return null;
    const { simRunId, since } = v as Record<string, unknown>;
    if (typeof simRunId !== 'string' || !ID_RE.test(simRunId)) return null;
    if (typeof since !== 'number' || !Number.isFinite(since)) return null;
    return { simRunId, since };
  } catch { return null; }
}

/** Cross-tab storage events the app reacts to. `live` is the key's
 *  CURRENT stored value: an event whose newValue is no longer what is
 *  stored is stale (a later write superseded it and has its own event),
 *  and acting on it is how two tabs would ping-pong. */
export const STORAGE_LAYOUT_KEY = 'supermap.layout';
export const STORAGE_COLW_KEY = 'supermap.colw';
export type StorageSyncAction =
  | { kind: 'layout'; raw: string } | { kind: 'colw'; raw: string } | { kind: 'inflight' } | null;
export function classifyStorageEvent(
  e: { key: string | null; newValue: string | null }, live: (key: string) => string | null,
): StorageSyncAction {
  if (e.key === null) return null; // storage.clear(): nothing to follow
  if (e.key === SIM_INFLIGHT_KEY) return { kind: 'inflight' };
  if (e.key !== STORAGE_LAYOUT_KEY && e.key !== STORAGE_COLW_KEY) return null;
  if (e.newValue === null || live(e.key) !== e.newValue) return null;
  return e.key === STORAGE_LAYOUT_KEY ? { kind: 'layout', raw: e.newValue } : { kind: 'colw', raw: e.newValue };
}

export const serializeSimInflight = (v: SimInflight): string => JSON.stringify({ simRunId: v.simRunId, since: v.since });

/** A stored in-flight marker older than this is treated as stale (the
 *  tab that wrote it died, or the server restarted and forgot the job).
 *  A full build is ~140 s; the poll deadline is the upper bound. */
export const SIM_INFLIGHT_MAX_AGE_MS = 15 * 60 * 1000;
export const simInflightFresh = (v: SimInflight, now: number): boolean =>
  now - v.since >= 0 && now - v.since <= SIM_INFLIGHT_MAX_AGE_MS;
