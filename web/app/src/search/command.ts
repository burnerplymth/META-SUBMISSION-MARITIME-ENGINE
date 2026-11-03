/**
 * Command bar grammar. Pure: no store, no DOM.
 *
 *   CODE [arg]      open a window; the arg focuses it          PRT Rotterdam
 *   open|close|float|dock|tab|maximize|restore ...             tab news with markets
 *   layout <name> | layout save|reset|rename|duplicate|export|link
 *   > theme dark | density comfortable | mode sim | export <id> | meta ai
 *   tour [quick|full]  the guided tour
 *   ?               the key sheet
 *   anything else   plain text: the existing grouped search (never intercepted)
 *
 * `parseCommand(text, ctx)` returns an intent union. Verbs read the window
 * registry; a window whose flag is off (LIVE TWIN) is absent from the grammar,
 * so `open live twin` is plain text while the flag is off.
 */
import { BUILTIN_PRESETS, presetAvailable, type Preset } from '../windows/presets';
import { parseTourCommand } from '../tour/deepLink';
import type { TourMode } from '../tour/engine';
import type { IconName } from '../ui/Icon';
import { WINDOWS, availableWindows, windowByCode, windowByWords, type DockSide, type WindowDef, type WindowGate, type WindowId } from '../windows/registry';

export interface CommandCtx {
  gate: WindowGate;
  /** User preset names (built-ins are always known). */
  userPresets?: readonly Pick<Preset, 'id' | 'name'>[];
}
export const DEFAULT_CTX: CommandCtx = { gate: { liveTwin: false } };

export type How = 'dock' | 'float' | 'tab';
export type LayoutOp = 'save' | 'reset' | 'rename' | 'duplicate' | 'export' | 'link' | 'import';
export type ActionName = 'theme' | 'density' | 'mode' | 'export' | 'meta-ai';

export type Intent =
  | { t: 'empty' }
  | { t: 'keys' }
  | { t: 'text'; text: string }
  | { t: 'open'; window: WindowId; arg?: string; how?: How }
  | { t: 'close'; window: WindowId }
  | { t: 'float'; window: WindowId }
  | { t: 'dock'; window: WindowId; side?: DockSide }
  | { t: 'tab'; window: WindowId; with: WindowId }
  | { t: 'maximize'; window: WindowId }
  | { t: 'restore'; window: WindowId }
  | { t: 'layout-apply'; preset: string; name: string; user: boolean }
  | { t: 'layout-op'; op: LayoutOp; name?: string }
  | { t: 'action'; action: ActionName; value?: string }
  | { t: 'tour'; mode: TourMode }
  /** A verb or `>` that names nothing known: the bar shows the matching
   *  suggestions instead of acting. */
  | { t: 'incomplete'; verb: string; rest: string };

const norm = (s: string) => s.trim().replace(/\s+/g, ' ');

const VERBS: Record<string, 'open' | 'close' | 'float' | 'dock' | 'tab' | 'maximize' | 'restore' | 'layout'> = {
  open: 'open', show: 'open', go: 'open', goto: 'open',
  close: 'close', hide: 'close',
  float: 'float', undock: 'float',
  dock: 'dock',
  tab: 'tab',
  maximize: 'maximize', max: 'maximize', maximise: 'maximize',
  restore: 'restore', unmaximize: 'restore',
  layout: 'layout', preset: 'layout', layouts: 'layout', presets: 'layout',
};
const SIDES: Record<string, DockSide> = { left: 'left', right: 'right', bottom: 'bottom', centre: 'centre', center: 'centre' };

/** Resolve a window by code or plain-language words. */
function resolveWindow(text: string, ctx: CommandCtx): WindowDef | null {
  const t = norm(text);
  if (!t) return null;
  return windowByCode(t, ctx.gate) ?? windowByWords(t, ctx.gate);
}

/** Resolve a layout by id, built-in name, old layout name or user preset name. */
export function resolveLayout(text: string, ctx: CommandCtx): { id: string; name: string; user: boolean } | null {
  const t = norm(text).toLowerCase();
  if (!t) return null;
  const builtins = BUILTIN_PRESETS.filter((p) => presetAvailable(p, ctx.gate));
  const hit = builtins.find((p) => p.id === t || p.name.toLowerCase() === t || p.legacy.toLowerCase() === t);
  if (hit) return { id: hit.id, name: hit.name, user: false };
  const u = (ctx.userPresets ?? []).find((p) => p.name.toLowerCase() === t || p.id === t);
  if (u) return { id: u.id, name: u.name, user: true };
  const pre = [...builtins.map((p) => ({ id: p.id, name: p.name, user: false })), ...(ctx.userPresets ?? []).map((p) => ({ id: p.id, name: p.name, user: true }))]
    .filter((p) => p.name.toLowerCase().startsWith(t));
  return pre.length === 1 ? pre[0] : null;
}

function parseAction(rest: string): Intent {
  const t = norm(rest).toLowerCase();
  const [head, ...tail] = t.split(' ');
  const val = tail.join(' ');
  if (head === 'theme') {
    const v = val === 'dark' ? 'dark' : val === 'light' ? 'light' : null;
    return v ? { t: 'action', action: 'theme', value: v } : { t: 'incomplete', verb: '>', rest: t };
  }
  if (head === 'density') {
    const v = val === 'comfortable' || val === 'comfy' ? 'comfortable' : val === 'compact' ? 'compact' : null;
    return v ? { t: 'action', action: 'density', value: v } : { t: 'incomplete', verb: '>', rest: t };
  }
  if (head === 'mode') {
    const v = val === 'sim' || val === 'simulation' ? 'sim' : val === 'live' ? 'live' : null;
    return v ? { t: 'action', action: 'mode', value: v } : { t: 'incomplete', verb: '>', rest: t };
  }
  if (head === 'export') return val ? { t: 'action', action: 'export', value: val.replace(/[^a-z0-9_.-]/g, '').slice(0, 64) } : { t: 'incomplete', verb: '>', rest: t };
  if (head === 'meta' && (val === 'ai' || val === 'ai activity')) return { t: 'action', action: 'meta-ai' };
  return { t: 'incomplete', verb: '>', rest: t };
}

export function parseCommand(text: string, ctx: CommandCtx = DEFAULT_CTX): Intent {
  const raw = text.replace(/ /g, ' ');
  const t = norm(raw);
  if (!t) return { t: 'empty' };
  if (t[0] === '?') return { t: 'keys' };
  if (t[0] === '>') return parseAction(t.slice(1));
  const tour = parseTourCommand(t);
  if (tour) return { t: 'tour', mode: tour.mode };

  const [first, ...restWords] = t.split(' ');
  const rest = restWords.join(' ');
  const verb = VERBS[first.toLowerCase()];

  if (verb === 'layout') {
    const r = norm(rest).toLowerCase();
    if (!r) return { t: 'incomplete', verb: 'layout', rest: '' };
    if (r === 'save' || r.startsWith('save ')) return { t: 'layout-op', op: 'save', name: norm(rest).slice(4).trim() || undefined };
    if (r === 'reset') return { t: 'layout-op', op: 'reset' };
    if (r === 'duplicate' || r === 'copy') return { t: 'layout-op', op: 'duplicate' };
    if (r === 'export') return { t: 'layout-op', op: 'export' };
    if (r === 'link' || r === 'copy link' || r === 'share') return { t: 'layout-op', op: 'link' };
    if (r === 'import') return { t: 'layout-op', op: 'import' };
    if (r.startsWith('rename ')) return { t: 'layout-op', op: 'rename', name: norm(rest).slice(7).trim() || undefined };
    const l = resolveLayout(r, ctx);
    return l ? { t: 'layout-apply', preset: l.id, name: l.name, user: l.user } : { t: 'incomplete', verb: 'layout', rest: r };
  }

  if (verb === 'tab') {
    // tab news with markets
    const m = /^(.+?)\s+with\s+(.+)$/i.exec(norm(rest));
    if (!m) return { t: 'incomplete', verb: 'tab', rest: norm(rest) };
    const a = resolveWindow(m[1], ctx), b = resolveWindow(m[2], ctx);
    return a && b && a.id !== b.id ? { t: 'tab', window: a.id, with: b.id } : { t: 'incomplete', verb: 'tab', rest: norm(rest) };
  }

  if (verb === 'dock') {
    const words = norm(rest).split(' ');
    const last = words[words.length - 1]?.toLowerCase();
    const side = SIDES[last];
    const winText = side ? words.slice(0, -1).join(' ') : words.join(' ');
    const w = resolveWindow(winText, ctx);
    return w ? { t: 'dock', window: w.id, ...(side ? { side } : {}) } : { t: 'incomplete', verb: 'dock', rest: norm(rest) };
  }

  if (first.toLowerCase() === 'export') return { t: 'open', window: 'expt' };

  if (verb) {
    const w = resolveWindow(rest, ctx);
    if (!w) return { t: 'incomplete', verb: first.toLowerCase(), rest: norm(rest) };
    switch (verb) {
      case 'open': return { t: 'open', window: w.id };
      case 'close': return { t: 'close', window: w.id };
      case 'float': return { t: 'float', window: w.id };
      case 'maximize': return { t: 'maximize', window: w.id };
      case 'restore': return { t: 'restore', window: w.id };
      default: break;
    }
  }

  // CODE [arg]
  const byCode = windowByCode(first, ctx.gate);
  if (byCode && /^[A-Za-z]{3,4}$/.test(first)) return { t: 'open', window: byCode.id, ...(rest ? { arg: rest } : {}) };

  // `open ports` style without the verb is plain text on purpose: the search owns it.
  return { t: 'text', text: t };
}

/* ── suggestions for the dropdown ────────────────────────────────────── */
export interface CommandRow {
  key: string;
  intent: Intent;
  label: string;
  /** Right-aligned code or hint. */
  code?: string;
  icon: IconName;
  keys?: string;
}

const winRow = (d: WindowDef, how?: How, arg?: string): CommandRow => ({
  key: `open:${d.id}${how ? `:${how}` : ''}${arg ? `:${arg}` : ''}`,
  intent: { t: 'open', window: d.id, ...(arg ? { arg } : {}), ...(how ? { how } : {}) },
  label: `${d.title}${arg ? `  ${arg}` : ''}`, code: d.code, icon: d.icon,
});

export function layoutRows(ctx: CommandCtx): CommandRow[] {
  const rows: CommandRow[] = BUILTIN_PRESETS.filter((p) => presetAvailable(p, ctx.gate)).map((p) => ({
    key: `layout:${p.id}`, intent: { t: 'layout-apply', preset: p.id, name: p.name, user: false }, label: p.name, code: 'layout', icon: 'layout-dashboard',
  }));
  for (const u of ctx.userPresets ?? []) rows.push({ key: `layout:${u.id}`, intent: { t: 'layout-apply', preset: u.id, name: u.name, user: true }, label: u.name, code: 'layout', icon: 'layout-dashboard' });
  return rows;
}

const ACTION_ROWS: CommandRow[] = [
  { key: 'a:theme:dark', intent: { t: 'action', action: 'theme', value: 'dark' }, label: 'Theme dark', code: '> theme dark', icon: 'sun' },
  { key: 'a:theme:light', intent: { t: 'action', action: 'theme', value: 'light' }, label: 'Theme light', code: '> theme light', icon: 'sun' },
  { key: 'a:density:comfortable', intent: { t: 'action', action: 'density', value: 'comfortable' }, label: 'Density comfortable', code: '> density comfortable', icon: 'terminal' },
  { key: 'a:density:compact', intent: { t: 'action', action: 'density', value: 'compact' }, label: 'Density compact', code: '> density compact', icon: 'terminal' },
  { key: 'a:mode:sim', intent: { t: 'action', action: 'mode', value: 'sim' }, label: 'Mode simulation', code: '> mode sim', icon: 'flask-conical' },
  { key: 'a:mode:live', intent: { t: 'action', action: 'mode', value: 'live' }, label: 'Mode live', code: '> mode live', icon: 'radio' },
  { key: 'a:meta-ai', intent: { t: 'action', action: 'meta-ai' }, label: 'Meta AI activity', code: '> meta ai', icon: 'ai' },
];
const TOUR_ROWS: CommandRow[] = [
  { key: 'tour:quick', intent: { t: 'tour', mode: 'quick' }, label: 'Tour quick', code: 'tour quick', icon: 'play' },
  { key: 'tour:full', intent: { t: 'tour', mode: 'full' }, label: 'Tour full', code: 'tour full', icon: 'play' },
];
const LAYOUT_OP_ROWS: CommandRow[] = [
  { key: 'l:save', intent: { t: 'layout-op', op: 'save' }, label: 'Save layout', code: 'layout save', icon: 'save' },
  { key: 'l:reset', intent: { t: 'layout-op', op: 'reset' }, label: 'Reset layout', code: 'layout reset', icon: 'rotate-ccw' },
  { key: 'l:duplicate', intent: { t: 'layout-op', op: 'duplicate' }, label: 'Duplicate layout', code: 'layout duplicate', icon: 'copy' },
  { key: 'l:export', intent: { t: 'layout-op', op: 'export' }, label: 'Export layout file', code: 'layout export', icon: 'download' },
  { key: 'l:link', intent: { t: 'layout-op', op: 'link' }, label: 'Copy layout link', code: 'layout link', icon: 'link' },
];

/** Rows for the dropdown. [] for plain text: the search takes over. */
export function suggestCommands(text: string, ctx: CommandCtx = DEFAULT_CTX, recents: readonly string[] = []): CommandRow[] {
  const t = norm(text);
  const pool = availableWindows(ctx.gate);
  if (!t) return recentRows(recents, ctx);
  const intent = parseCommand(t, ctx);
  switch (intent.t) {
    case 'keys': return [{ key: 'keys', intent, label: 'Key sheet', code: '?', icon: 'keyboard' }];
    case 'open': return [winRow(WINDOWS[intent.window], undefined, intent.arg)];
    case 'close': case 'float': case 'maximize': case 'restore': case 'dock': {
      const d = WINDOWS[intent.window];
      const verb = intent.t === 'dock' && intent.side ? `dock ${d.code} ${intent.side}` : `${intent.t} ${d.code}`;
      return [{ key: `${intent.t}:${d.id}`, intent, label: `${intent.t[0].toUpperCase()}${intent.t.slice(1)} ${d.title}`, code: verb, icon: d.icon }];
    }
    case 'tab': return [{ key: `tab:${intent.window}:${intent.with}`, intent, label: `Tab ${WINDOWS[intent.window].title} with ${WINDOWS[intent.with].title}`, code: 'tab', icon: WINDOWS[intent.window].icon }];
    case 'layout-apply': return [{ key: `layout:${intent.preset}`, intent, label: intent.name, code: 'layout', icon: 'layout-dashboard' }];
    case 'layout-op': return LAYOUT_OP_ROWS.filter((r) => r.intent.t === 'layout-op' && r.intent.op === intent.op).map((r) => ({ ...r, intent }));
    case 'tour': return TOUR_ROWS.filter((r) => t.toLowerCase() === 'tour' || (r.intent.t === 'tour' && r.intent.mode === intent.mode));
    case 'action': return ACTION_ROWS.filter((r) => r.intent.t === 'action' && r.intent.action === intent.action && (intent.value === undefined || r.intent.value === intent.value));
    case 'incomplete': {
      const rest = intent.rest.toLowerCase();
      if (intent.verb === '>') return ACTION_ROWS.filter((r) => r.code!.slice(2).startsWith(rest) || r.label.toLowerCase().includes(rest));
      if (intent.verb === 'layout') return [...layoutRows(ctx).filter((r) => !rest || r.label.toLowerCase().includes(rest)), ...LAYOUT_OP_ROWS.filter((r) => !rest || r.label.toLowerCase().includes(rest) || r.code!.includes(rest))].slice(0, 30);
      const how: How | undefined = intent.verb === 'float' ? 'float' : undefined;
      const matches = pool.filter((d) => !rest || d.title.toLowerCase().includes(rest) || d.code.toLowerCase().startsWith(rest) || d.words.some((w) => w.includes(rest)));
      const verbIntent = (d: WindowDef): Intent => {
        switch (intent.verb) {
          case 'close': case 'hide': return { t: 'close', window: d.id };
          case 'float': return { t: 'float', window: d.id };
          case 'dock': return { t: 'dock', window: d.id };
          case 'maximize': case 'max': return { t: 'maximize', window: d.id };
          case 'restore': return { t: 'restore', window: d.id };
          default: return { t: 'open', window: d.id, ...(how ? { how } : {}) };
        }
      };
      return matches.slice(0, 10).map((d) => ({ key: `${intent.verb}:${d.id}`, intent: verbIntent(d), label: `${intent.verb} ${d.title}`, code: d.code, icon: d.icon }));
    }
    case 'text': {
      // A bare prefix of a code or a verb offers completions; ordinary words offer none.
      const low = t.toLowerCase();
      if (/^[a-z]{1,3}$/i.test(t)) {
        const hits = pool.filter((d) => d.code.toLowerCase().startsWith(low) || d.aliases?.some((a) => a.toLowerCase().startsWith(low)));
        if (hits.length && hits.length <= 6) return hits.map((d) => winRow(d));
      }
      return [];
    }
    default: return [];
  }
}

/* ── recents (last 8) ────────────────────────────────────────────────── */
export const RECENTS_KEY = 'supermap.cmd.recents';
export const MAX_RECENTS = 8;
export function pushRecent(list: readonly string[], text: string): string[] {
  const t = norm(text);
  if (!t || t.length > 80) return [...list];
  return [t, ...list.filter((x) => x !== t)].slice(0, MAX_RECENTS);
}
export function parseRecents(raw: string | null): string[] {
  if (!raw) return [];
  try {
    const v = JSON.parse(raw) as unknown;
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && x.length > 0 && x.length <= 80).slice(0, MAX_RECENTS) : [];
  } catch { return []; }
}
/** Empty focus: recents first (those that still parse to a command), then the layouts. */
export function recentRows(recents: readonly string[], ctx: CommandCtx): CommandRow[] {
  const out: CommandRow[] = [];
  for (const r of recents.slice(0, MAX_RECENTS)) {
    const i = parseCommand(r, ctx);
    if (i.t === 'text' || i.t === 'empty' || i.t === 'incomplete') continue;
    const row = suggestCommands(r, ctx)[0];
    if (row) out.push({ ...row, key: `recent:${r}`, code: row.code, icon: 'history' });
  }
  return [...out, ...layoutRows(ctx)];
}

/* ── the key sheet (`?`) ─────────────────────────────────────────────── */
export interface KeyRow { label: string; keys: string }
export const KEY_SHEET: ReadonlyArray<{ group: string; rows: readonly KeyRow[] }> = [
  { group: 'Command bar', rows: [
    { label: 'Focus', keys: 'Ctrl+K' }, { label: 'Focus', keys: '/' }, { label: 'Run', keys: 'Enter' },
    { label: 'Run as float', keys: 'Shift+Enter' }, { label: 'Run as tab', keys: 'Alt+Enter' }, { label: 'Accept completion', keys: 'Tab' }, { label: 'Clear, then leave', keys: 'Esc' },
  ] },
  { group: 'Windows', rows: [
    { label: 'Next window', keys: 'F6' }, { label: 'Previous window', keys: 'Shift+F6' }, { label: 'Next tab', keys: 'Ctrl+]' }, { label: 'Previous tab', keys: 'Ctrl+[' },
    { label: 'Float or dock', keys: 'Alt+Shift+F' }, { label: 'Maximize', keys: 'Alt+Shift+M' }, { label: 'Close', keys: 'Alt+Shift+X' }, { label: 'Resize', keys: 'Alt+Shift+Arrows' }, { label: 'Layouts', keys: 'Alt+L' },
    { label: 'Next place on map', keys: ']' }, { label: 'Previous place on map', keys: '[' },
  ] },
  { group: 'Shell', rows: [
    { label: 'Live', keys: 'Alt+1' }, { label: 'Simulation', keys: 'Alt+2' }, { label: 'Light or dark', keys: 'Alt+Shift+L' },
  ] },
  { group: 'Grammar', rows: [
    { label: 'CODE [place]', keys: 'PRT Rotterdam' }, { label: 'Verbs', keys: 'open close float dock tab maximize layout' }, { label: 'Actions', keys: '> theme dark' },
  ] },
];
