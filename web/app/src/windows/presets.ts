/**
 * Layout presets. Pure: no React, no
 * store, storage injected. Built-in presets alias the old layout names, so
 * `#layout=Domino Room` and a stored `supermap.layout` keep working: the
 * store's `layout` string stays the legacy name and the preset resolves
 * from it.
 */
import { MAX_WINDOWS, floatOf, mapVisible, normaliseDock, split, tabs, viewsOf, type DockModel } from './model';
import { WINDOWS, type WindowFlag, type WindowGate, type WindowId } from './registry';

export const PRESET_SCHEMA = 'supermap.layout/1';
export const PRESETS_KEY = 'supermap.presets';
export const CURRENT_KEY = 'supermap.layout.current';
export const MAX_USER_PRESETS = 32;
export const MAX_PRESET_BYTES = 64 * 1024;
export const NAME_MAX = 40;

export type LinkGroupId = 'A' | 'B';
export type PresetMode = 'live' | 'sim' | 'both';

export interface WindowParams { link?: LinkGroupId | null }
export interface Preset {
  schema: typeof PRESET_SCHEMA;
  id: string;
  name: string;
  builtin: boolean;
  /** The old layout name this preset stands for (the store's `layout`). */
  legacy: string;
  mode: PresetMode;
  flags: WindowFlag[];
  /** Phone order: one window at a time, map first. */
  phone: WindowId[];
  dock: DockModel;
  windows: Partial<Record<WindowId, WindowParams>>;
  map: { visible: boolean };
  created: string;
}

/* ── built-ins ────────────────────────────────────────────────────────── */

const T = tabs;
const col = (size: number, ...c: ReturnType<typeof tabs>[]) => (c.length === 1 ? { ...c[0], size } : split('v', size, ...c));
const row = (...c: Array<ReturnType<typeof tabs> | ReturnType<typeof split>>) => split('h', 1, ...c);

interface BuiltinDef { id: string; name: string; legacy: string; mode: PresetMode; flags?: WindowFlag[]; dock: DockModel }
const hiddenMap = () => T(['map'], 0.2, undefined, false);

const BUILTIN_DEFS: BuiltinDef[] = [
  /* Overview = the rail workspace: News over Ports on the left, the
   * map as the hero over the causal chain in the centre, Charts, Impact and Source health on the right. */
  { id: 'overview', name: 'Overview', legacy: 'SupermAP', mode: 'live', dock: {
    root: row(
      col(0.3, T(['news', 'market'], 0.5, 'news'), T(['ports'], 0.5)),
      col(0.37, T(['map'], 0.585), T(['chain', 'chaintrace', 'whatif', 'explain', 'ensemble', 'sim'], 0.415, 'chain')),
      col(0.33, T(['charts'], 0.33), T(['impact', 'money'], 0.34, 'impact'), T(['health', 'alerts', 'mapcontent'], 0.33, 'health'))),
    floats: [] } },
  { id: 'chain-room', name: 'Chain room', legacy: 'Domino Room', mode: 'both', dock: {
    root: row(
      col(0.5, T(['map'], 0.62), T(['charts'], 0.38)),
      col(0.5, T(['chain', 'chaintrace'], 0.55, 'chain'), T(['mapcontent', 'whatif', 'ensemble'], 0.45, 'whatif'))),
    floats: [] } },
  { id: 'place-desk', name: 'Place desk', legacy: 'Place Desk', mode: 'sim', dock: {
    root: row(
      col(0.22, T(['health', 'alerts'], 1)),
      col(0.46, T(['map'], 1)),
      col(0.32, T(['chain', 'chaintrace'], 0.5, 'chain'), T(['mapcontent', 'explain', 'sim'], 0.5, 'explain'))),
    floats: [] } },
  { id: 'markets', name: 'Markets', legacy: 'Market Desk', mode: 'live', dock: {
    root: row(
      col(0.26, T(['news', 'market'], 0.55, 'market'), T(['satwatch'], 0.45)),
      col(0.46, T(['map'], 1)),
      col(0.28, T(['money', 'impact'], 0.55, 'money'), T(['mapcontent'], 0.45))),
    floats: [] } },
  { id: 'replay', name: 'Replay', legacy: 'Replay', mode: 'live', dock: {
    root: row(
      col(0.22, T(['map'], 1)),
      col(0.46, T(['replay'], 1)),
      col(0.32, T(['chain', 'chaintrace', 'mapcontent', 'ledger'], 1, 'chain'))),
    floats: [] } },
  { id: 'history', name: 'History', legacy: 'History', mode: 'live', dock: {
    root: row(
      col(0.48, T(['map'], 0.6), T(['tool-history'], 0.4)),
      col(0.52, T(['chain', 'chaintrace'], 0.5, 'chain'), T(['proof', 'ledger', 'mapcontent'], 0.5, 'ledger'))),
    floats: [] } },
  { id: 'proof', name: 'Proof', legacy: 'Proof', mode: 'live', dock: {
    root: row(
      col(0.5, T(['proof'], 0.6), T(['ledger'], 0.4)),
      col(0.5, T(['chain', 'chaintrace'], 1, 'chain')),
      hiddenMap()),
    floats: [] } },
  { id: 'analysis', name: 'Analysis desks', legacy: 'Analysis Desks', mode: 'live', dock: {
    root: row(
      col(0.42, T(['map'], 0.6), T(['chain', 'chaintrace', 'mapcontent'], 0.4, 'chain')),
      col(0.58, T(['desks'], 1))),
    floats: [] } },
  { id: 'scenario', name: 'Scenario', legacy: 'Simulation', mode: 'sim', dock: {
    root: row(
      col(0.2, T(['health'], 1)),
      col(0.44, T(['map'], 0.66), T(['charts'], 0.34)),
      col(0.36, T(['sim', 'tool-scenarios'], 0.34, 'sim'), T(['chain', 'chaintrace'], 0.33, 'chain'), T(['whatif', 'ensemble', 'impact', 'mapcontent'], 0.33, 'whatif'))),
    floats: [] } },
  { id: 'reports', name: 'Reports', legacy: 'Report Desk', mode: 'live', dock: {
    root: row(T(['reports'], 0.5), T(['exports', 'expt'], 0.5), hiddenMap()), floats: [] } },
  { id: 'data', name: 'Data', legacy: 'Data Desk', mode: 'live', dock: {
    root: row(T(['dataviewer'], 0.58), T(['exports', 'reports', 'expt'], 0.42), hiddenMap()), floats: [] } },
  { id: 'lanes', name: 'Lanes', legacy: 'Lane Desk', mode: 'live', dock: {
    root: row(T(['laneplanner'], 1), hiddenMap()), floats: [] } },
  { id: 'exposure', name: 'My shipments', legacy: 'Shipments Desk', mode: 'live', dock: {
    root: row(T(['shipments'], 1), hiddenMap()), floats: [] } },
  { id: 'watchlists', name: 'Watchlists', legacy: 'Watch Desk', mode: 'live', dock: {
    root: row(T(['watchlists'], 1), hiddenMap()), floats: [] } },
  { id: 'satellite', name: 'Satellite', legacy: 'Satellite Desk', mode: 'live', dock: {
    root: row(T(['satdesk'], 1), hiddenMap()), floats: [] } },
  { id: 'port-twin', name: 'Port twin', legacy: 'Port Twin', mode: 'live', dock: {
    root: row(T(['porttwin'], 1), hiddenMap()), floats: [] } },
  { id: 'agent', name: 'Meta AI', legacy: 'Agent', mode: 'both', dock: {
    root: row(
      col(0.5, T(['map'], 0.62), T(['chain', 'chaintrace', 'mapcontent'], 0.38, 'chain')),
      col(0.5, T(['agent'], 1))),
    floats: [] } },
  { id: 'demo', name: 'Demo', legacy: 'Demo', mode: 'both', dock: {
    root: row(
      col(0.62, T(['map'], 0.68), T(['chain', 'chaintrace', 'mapcontent'], 0.32, 'chain')),
      col(0.38, T(['agent'], 1))),
    floats: [] } },
  { id: 'research', name: 'Research', legacy: 'Research', mode: 'both', dock: {
    root: row(
      col(0.3, T(['map'], 0.58), T(['chain', 'chaintrace', 'mapcontent'], 0.42, 'chain')),
      col(0.7, T(['research'], 1))),
    floats: [] } },
  { id: 'builder', name: 'Builder', legacy: 'Builder', mode: 'both', dock: {
    root: row(
      col(0.46, T(['map'], 0.6), T(['chain', 'chaintrace', 'mapcontent'], 0.4, 'chain')),
      col(0.54, T(['scenariobuilder'], 1))),
    floats: [] } },
  { id: 'ports', name: 'Ports', legacy: 'Ports Board', mode: 'live', dock: {
    root: row(
      col(0.46, T(['map'], 0.62), T(['charts'], 0.38)),
      col(0.54, T(['ports'], 0.7), T(['mapcontent'], 0.3))),
    floats: [] } },
  { id: 'live-twin', name: 'Live twin', legacy: 'Live Twin', mode: 'live', flags: ['livetwin'], dock: {
    root: row(
      col(0.4, T(['map'], 1)),
      col(0.6, T(['livetwin', 'livefeed'], 0.5, 'livetwin'), T(['livetape', 'livectx', 'mapcontent'], 0.5, 'livetape'))),
    floats: [] } },
];

/** Default window links: everything follows group A (the global selection). */
const defaultLinks = (m: DockModel): Partial<Record<WindowId, WindowParams>> =>
  Object.fromEntries(viewsOf(m).map((id) => [id, { link: 'A' as LinkGroupId }]));

export function phoneOrderOf(dock: DockModel): WindowId[] {
  const ids = [...new Set(viewsOf(dock))].filter((id) => id !== 'map');
  ids.sort((a, b) => WINDOWS[a].phoneOrder - WINDOWS[b].phoneOrder);
  return ['map', ...ids];
}

const EPOCH = '2026-01-01T00:00:00Z';
export const BUILTIN_PRESETS: readonly Preset[] = BUILTIN_DEFS.map((d) => {
  const norm = normaliseDock(d.dock, { liveTwin: true })!;
  return {
    schema: PRESET_SCHEMA, id: d.id, name: d.name, builtin: true, legacy: d.legacy, mode: d.mode, flags: d.flags ?? [],
    phone: phoneOrderOf(norm.dock), dock: norm.dock, windows: defaultLinks(norm.dock), map: { visible: mapVisible(norm.dock) }, created: EPOCH,
  };
});

/** Old layout name or preset id -> built-in. Case-sensitive on legacy names
 *  (as the old URL validation was); ids are lower-case slugs. */
export function builtinFor(nameOrId: string | null | undefined): Preset | null {
  if (!nameOrId) return null;
  return BUILTIN_PRESETS.find((p) => p.legacy === nameOrId || p.id === nameOrId) ?? null;
}
/** The legacy layout name for a built-in id (identity for a legacy name). */
export const legacyNameFor = (nameOrId: string): string | null => builtinFor(nameOrId)?.legacy ?? null;
export const LEGACY_NAMES: readonly string[] = BUILTIN_PRESETS.map((p) => p.legacy);

/** A built-in is offered only while its flags are on (LIVE TWIN law). */
export const presetAvailable = (p: Preset, gate: WindowGate): boolean => !p.flags.includes('livetwin') || gate.liveTwin;

/* ── validation ───────────────────────────────────────────────────────── */

export type PresetCheck = { ok: true; preset: Preset; dropped: string[] } | { ok: false; reason: string };

const cleanName = (v: unknown): string => String(v ?? '').replace(/[\u0000-\u001f\u007f]/g, '').replace(/\s+/g, ' ').trim().slice(0, NAME_MAX);
const ID_RE = /^[a-z0-9][a-z0-9_:-]{0,47}$/;

/** Validate an untrusted preset (disk, URL, import). Never throws, never
 *  fetches or executes anything from the value. */
export function validatePreset(raw: unknown, gate: WindowGate, opts: { allowBuiltinFlag?: boolean } = {}): PresetCheck {
  try {
    if (!raw || typeof raw !== 'object') return { ok: false, reason: 'not an object' };
    const o = raw as Record<string, unknown>;
    if (o.schema !== PRESET_SCHEMA) return { ok: false, reason: 'unknown schema' };
    if (JSON.stringify(raw).length > MAX_PRESET_BYTES) return { ok: false, reason: 'too large' };
    const name = cleanName(o.name);
    if (!name) return { ok: false, reason: 'no name' };
    const id = typeof o.id === 'string' && ID_RE.test(o.id) ? o.id : `u-${hashId(name)}`;
    const nd = normaliseDock(o.dock, gate);
    if (!nd) return { ok: false, reason: 'no dock' };
    if (viewsOf(nd.dock).length > MAX_WINDOWS) return { ok: false, reason: 'too many windows' };
    const legacy = typeof o.legacy === 'string' && LEGACY_NAMES.includes(o.legacy) ? o.legacy : 'SupermAP';
    const windows: Preset['windows'] = {};
    const present = new Set(viewsOf(nd.dock));
    const wraw = o.windows && typeof o.windows === 'object' ? (o.windows as Record<string, unknown>) : {};
    for (const wid of present) {
      const p = wraw[wid];
      const link = p && typeof p === 'object' ? (p as Record<string, unknown>).link : undefined;
      windows[wid] = { link: link === 'A' || link === 'B' ? link : link === null ? null : 'A' };
    }
    const mode: PresetMode = o.mode === 'live' || o.mode === 'sim' ? o.mode : 'both';
    const flags = (Array.isArray(o.flags) ? o.flags : []).filter((f): f is WindowFlag => f === 'livetwin');
    return {
      ok: true, dropped: nd.dropped,
      preset: {
        schema: PRESET_SCHEMA, id, name, builtin: opts.allowBuiltinFlag === true && o.builtin === true, legacy, mode, flags,
        phone: phoneOrderOf(nd.dock), dock: nd.dock, windows, map: { visible: mapVisible(nd.dock) },
        created: typeof o.created === 'string' && /^\d{4}-\d{2}-\d{2}T[\d:.]+Z$/.test(o.created) ? o.created : EPOCH,
      },
    };
  } catch { return { ok: false, reason: 'invalid' }; }
}

function hashId(s: string): string {
  let h = 2166136261;
  for (let i = 0; i < s.length; i += 1) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return (h >>> 0).toString(36);
}

/** The built-in as seen with the flag gate applied (flagged windows dropped
 *  without placeholder). */
export function gatedPreset(p: Preset, gate: WindowGate): Preset {
  const c = validatePreset({ ...p, builtin: p.builtin }, gate, { allowBuiltinFlag: true });
  return c.ok ? { ...c.preset, builtin: p.builtin } : p;
}

/* ── user presets ─────────────────────────────────────────────────────── */

export interface KV { get(k: string): string | null; set(k: string, v: string): void; remove(k: string): void }

export function loadUserPresets(kv: KV): Preset[] {
  let raw: string | null = null;
  try { raw = kv.get(PRESETS_KEY); } catch { raw = null; }
  if (!raw) return [];
  try {
    const v = JSON.parse(raw) as unknown;
    const arr = v && typeof v === 'object' && Array.isArray((v as { presets?: unknown }).presets) ? (v as { presets: unknown[] }).presets : [];
    const out: Preset[] = [];
    for (const p of arr) {
      if (out.length >= MAX_USER_PRESETS) break;
      const c = validatePreset(p, { liveTwin: true });
      if (c.ok && !out.some((x) => x.id === c.preset.id)) out.push({ ...c.preset, builtin: false });
    }
    return out;
  } catch { return []; }
}

export function saveUserPresets(kv: KV, list: readonly Preset[]): void {
  try { kv.set(PRESETS_KEY, JSON.stringify({ v: 1, presets: list.slice(0, MAX_USER_PRESETS) })); } catch { /* storage blocked: session only */ }
}

export const uniqueName = (name: string, taken: readonly string[]): string => {
  const base = cleanName(name) || 'Layout';
  if (!taken.includes(base)) return base;
  for (let i = 2; i < 100; i += 1) {
    const suffix = ` ${i}`;
    const cand = base.slice(0, NAME_MAX - suffix.length) + suffix;
    if (!taken.includes(cand)) return cand;
  }
  return base.slice(0, NAME_MAX - 3) + ' 99';
};

const newId = (list: readonly Preset[]): string => {
  for (;;) {
    const id = `u-${Math.random().toString(36).slice(2, 8)}`;
    if (!list.some((p) => p.id === id)) return id;
  }
};

export type ListResult = { ok: true; list: Preset[]; preset: Preset } | { ok: false; reason: string };

export function addUserPreset(list: readonly Preset[], dock: DockModel, windows: Preset['windows'], base: Preset, name: string): ListResult {
  if (list.length >= MAX_USER_PRESETS) return { ok: false, reason: 'limit' };
  const nm = cleanName(name);
  if (!nm) return { ok: false, reason: 'no name' };
  const taken = [...BUILTIN_PRESETS.map((p) => p.name), ...list.map((p) => p.name)];
  const preset: Preset = {
    ...base, id: newId(list), name: uniqueName(nm, taken), builtin: false, dock, windows, phone: phoneOrderOf(dock),
    map: { visible: mapVisible(dock) }, created: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
  };
  return { ok: true, list: [...list, preset], preset };
}

export function renameUserPreset(list: readonly Preset[], id: string, name: string): ListResult {
  const cur = list.find((p) => p.id === id);
  const nm = cleanName(name);
  if (!cur || !nm) return { ok: false, reason: 'no such preset' };
  const preset = { ...cur, name: uniqueName(nm, [...BUILTIN_PRESETS.map((p) => p.name), ...list.filter((p) => p.id !== id).map((p) => p.name)]) };
  return { ok: true, list: list.map((p) => (p.id === id ? preset : p)), preset };
}

export function duplicatePreset(list: readonly Preset[], src: Preset): ListResult {
  if (list.length >= MAX_USER_PRESETS) return { ok: false, reason: 'limit' };
  const taken = [...BUILTIN_PRESETS.map((p) => p.name), ...list.map((p) => p.name)];
  const preset: Preset = { ...src, id: newId(list), name: uniqueName(`${src.name} copy`, taken), builtin: false, created: new Date().toISOString().replace(/\.\d+Z$/, 'Z') };
  return { ok: true, list: [...list, preset], preset };
}

export const deleteUserPreset = (list: readonly Preset[], id: string): Preset[] => list.filter((p) => p.id !== id);

/** Import a file's text or a pasted string. */
export function importPresetText(text: string, list: readonly Preset[]): ListResult {
  if (text.length > MAX_PRESET_BYTES) return { ok: false, reason: 'too large' };
  let raw: unknown;
  try { raw = JSON.parse(text); } catch { return { ok: false, reason: 'not json' }; }
  const c = validatePreset(raw, { liveTwin: true });
  if (!c.ok) return c;
  if (list.length >= MAX_USER_PRESETS) return { ok: false, reason: 'limit' };
  const taken = [...BUILTIN_PRESETS.map((p) => p.name), ...list.map((p) => p.name)];
  const preset: Preset = { ...c.preset, id: newId(list), builtin: false, name: uniqueName(c.preset.name, taken) };
  return { ok: true, list: [...list, preset], preset };
}

/** File form of a preset (Export file). */
export const presetToFileText = (p: Preset): string => JSON.stringify({ ...p, builtin: false }, null, 2);
export const presetFileName = (p: Preset): string => `supermap-layout-${p.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'layout'}.json`;
