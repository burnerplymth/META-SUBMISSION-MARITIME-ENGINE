/* The watchlists, rules and triggered entries live in this browser only
 * (localStorage, one key). Reads are validated field by field; a blocked or
 * corrupt store reads as empty. The same validator reads an imported file. */
import { BASES, RULE_TYPES, type Basis, type Memory, type RuleType, type Triggered, type WatchItem, type WatchList, type WatchRule, type WatchState } from './types';

export const STORE_KEY = 'supermap.watch.v1';
export const SCHEMA = 'supermap.watch/1';
export const MAX_LISTS = 20;
export const MAX_ITEMS = 50;
export const MAX_RULES = 60;
export const MAX_TRIGGERED = 200;
export const MAX_NAME = 60;
export const MAX_FILE_BYTES = 600_000;
export interface KV { get(key: string): string | null; set(key: string, value: string): void; remove(key: string): void }

export const EMPTY: WatchState = { lists: [], rules: [], triggered: [], memory: { met: {}, feed: {} }, active: null };
export const emptyState = (): WatchState => ({ lists: [], rules: [], triggered: [], memory: { met: {}, feed: {} }, active: null });

/** Free text: control and bidirectional characters out, spaces collapsed, no leading formula character. */
export function clean(v: unknown, max = MAX_NAME): string {
  // eslint-disable-next-line no-control-regex
  let t = String(v ?? '').replace(/[\u0000-\u001f\u007f-\u009f​-‏‪-‮⁦-⁩﻿]/g, ' ');
  t = t.split(/\s+/).filter(Boolean).join(' ');
  while (t && '=+-@'.includes(t[0])) t = t.slice(1).trimStart();
  return t.slice(0, max);
}
const ID_RE = /^[A-Za-z0-9._:|-]{1,80}$/;
const idOf = (v: unknown): string | null => (typeof v === 'string' && ID_RE.test(v) ? v : null);
const num = (v: unknown, lo: number, hi: number): number | undefined => (typeof v === 'number' && Number.isFinite(v) && v >= lo && v <= hi ? v : undefined);

export function sanitiseItem(r: unknown): WatchItem | null {
  if (!r || typeof r !== 'object') return null;
  const o = r as Record<string, unknown>;
  const id = idOf(o.id); const a = clean(o.a, 120);
  const kind = o.kind === 'place' || o.kind === 'leg' || o.kind === 'lane' ? o.kind : null;
  if (!id || !kind || !a) return null;
  const b = clean(o.b, 120);
  if (kind !== 'place' && !b) return null;
  const item: WatchItem = { id, kind, label: clean(o.label, 120) || (b ? `${a} to ${b}` : a), a };
  if (kind !== 'place') item.b = b;
  const an = clean(o.aName, 80); if (an) item.aName = an;
  const bn = clean(o.bName, 80); if (bn && kind !== 'place') item.bName = bn;
  return item;
}

export function sanitiseRule(r: unknown, listIds: ReadonlySet<string>): WatchRule | null {
  if (!r || typeof r !== 'object') return null;
  const o = r as Record<string, unknown>;
  const id = idOf(o.id); const listId = idOf(o.listId);
  const type = RULE_TYPES.find((t) => t === o.type) as RuleType | undefined;
  if (!id || !listId || !type || !listIds.has(listId)) return null;
  const rule: WatchRule = { id, listId, type, enabled: o.enabled !== false };
  if (type === 'deviation') rule.level = o.level === 2 ? 2 : o.level === 3 ? 3 : 1;
  if (type === 'added_transit') {
    const sid = typeof o.scenarioId === 'string' && /^[a-z0-9-]{1,64}$/.test(o.scenarioId) ? o.scenarioId : null;
    if (!sid) return null;
    rule.scenarioId = sid;
    rule.durationD = Math.round(num(o.durationD, 1, 3650) ?? 14);
    rule.thresholdD = num(o.thresholdD, 0, 3650) ?? 3;
  }
  return rule;
}

export function sanitiseTriggered(r: unknown): Triggered | null {
  if (!r || typeof r !== 'object') return null;
  const o = r as Record<string, unknown>;
  const id = idOf(o.id); const ruleId = idOf(o.ruleId);
  const at = typeof o.at === 'string' && !Number.isNaN(Date.parse(o.at)) ? new Date(Date.parse(o.at)).toISOString() : null;
  const basis = BASES.find((b) => b === o.basis) as Basis | undefined;
  if (!id || !ruleId || !at || !basis) return null;
  return { id, at, ruleId, basis, listName: clean(o.listName), itemLabel: clean(o.itemLabel, 120), rule: clean(o.rule, 120), detail: clean(o.detail, 200) };
}

function sanitiseMemory(m: unknown): Memory {
  const out: Memory = { met: {}, feed: {} };
  if (!m || typeof m !== 'object') return out;
  const o = m as Record<string, unknown>;
  if (o.met && typeof o.met === 'object') for (const [k, v] of Object.entries(o.met as object).slice(0, 4000)) if (ID_RE.test(k) || /^[\w.:|-]{1,200}$/.test(k)) out.met[k] = v === true;
  if (o.feed && typeof o.feed === 'object') for (const [k, v] of Object.entries(o.feed as object).slice(0, 2000)) if (/^[\w.:|-]{1,200}$/.test(k) && (v === 'live' || v === 'snapshot' || v === 'notserved')) out.feed[k] = v;
  return out;
}

export function sanitiseState(j: unknown): WatchState {
  const out = emptyState();
  if (!j || typeof j !== 'object') return out;
  const o = j as Record<string, unknown>;
  const seen = new Set<string>();
  for (const l of Array.isArray(o.lists) ? o.lists : []) {
    if (!l || typeof l !== 'object' || out.lists.length >= MAX_LISTS) continue;
    const lo = l as Record<string, unknown>;
    const id = idOf(lo.id); const name = clean(lo.name);
    if (!id || !name || seen.has(id)) continue;
    seen.add(id);
    const items: WatchItem[] = []; const itemIds = new Set<string>();
    for (const it of Array.isArray(lo.items) ? lo.items : []) {
      const s = sanitiseItem(it);
      if (!s || itemIds.has(s.id) || items.length >= MAX_ITEMS) continue;
      itemIds.add(s.id); items.push(s);
    }
    out.lists.push({ id, name, items });
  }
  const ruleIds = new Set<string>();
  for (const r of Array.isArray(o.rules) ? o.rules : []) {
    const s = sanitiseRule(r, seen);
    if (!s || ruleIds.has(s.id) || out.rules.length >= MAX_RULES) continue;
    ruleIds.add(s.id); out.rules.push(s);
  }
  const tIds = new Set<string>();
  for (const t of Array.isArray(o.triggered) ? o.triggered : []) {
    const s = sanitiseTriggered(t);
    if (!s || tIds.has(s.id) || out.triggered.length >= MAX_TRIGGERED) continue;
    tIds.add(s.id); out.triggered.push(s);
  }
  out.memory = sanitiseMemory(o.memory);
  out.active = typeof o.active === 'string' && seen.has(o.active) ? o.active : out.lists[0]?.id ?? null;
  return out;
}

export function loadState(kv: KV): WatchState {
  try {
    const raw = kv.get(STORE_KEY);
    if (!raw) return emptyState();
    const j = JSON.parse(raw) as Record<string, unknown>;
    return j && j.v === 1 ? sanitiseState(j) : emptyState();
  } catch { return emptyState(); }
}

export function saveState(kv: KV, s: WatchState): void {
  try { kv.set(STORE_KEY, JSON.stringify({ v: 1, ...s })); } catch { /* storage full or blocked: the state stays in memory */ }
}

export function clearState(kv: KV): void { try { kv.remove(STORE_KEY); } catch { /* nothing to clear */ } }

/* ── export and import (JSON file) ───────────────────────────────────── */

export function exportDoc(s: WatchState, now: Date): Record<string, unknown> {
  return { schema: SCHEMA, exported_at: now.toISOString(), lists: s.lists, rules: s.rules, triggered: s.triggered };
}

export type ImportResult = { ok: true; state: WatchState; counts: { lists: number; rules: number; triggered: number } } | { ok: false; reason: 'not_json' | 'wrong_schema' | 'too_large' };

/** Reads an exported file and merges it into the current state by id (the file wins on a shared id). */
export function importDoc(text: string, current: WatchState): ImportResult {
  if (text.length > MAX_FILE_BYTES) return { ok: false, reason: 'too_large' };
  let j: unknown;
  try { j = JSON.parse(text); } catch { return { ok: false, reason: 'not_json' }; }
  if (!j || typeof j !== 'object' || (j as Record<string, unknown>).schema !== SCHEMA) return { ok: false, reason: 'wrong_schema' };
  const inc = sanitiseState(j);
  const lists = [...current.lists.filter((l) => !inc.lists.some((x) => x.id === l.id)), ...inc.lists].slice(0, MAX_LISTS);
  const ids = new Set(lists.map((l) => l.id));
  const rules = [...current.rules.filter((r) => !inc.rules.some((x) => x.id === r.id)), ...inc.rules].filter((r) => ids.has(r.listId)).slice(0, MAX_RULES);
  const triggered = [...inc.triggered, ...current.triggered.filter((t) => !inc.triggered.some((x) => x.id === t.id))]
    .sort((a, b) => b.at.localeCompare(a.at)).slice(0, MAX_TRIGGERED);
  const state: WatchState = { lists, rules, triggered, memory: { met: { ...current.memory.met }, feed: { ...current.memory.feed } }, active: current.active && ids.has(current.active) ? current.active : lists[0]?.id ?? null };
  return { ok: true, state, counts: { lists: inc.lists.length, rules: inc.rules.length, triggered: inc.triggered.length } };
}

/* ── id helpers ──────────────────────────────────────────────────────── */

export function nextId(prefix: string, used: Iterable<string>): string {
  const set = new Set(used);
  let n = set.size + 1;
  while (set.has(`${prefix}-${String(n).padStart(3, '0')}`)) n += 1;
  return `${prefix}-${String(n).padStart(3, '0')}`;
}
export const listIdFor = (lists: readonly WatchList[]) => nextId('L', lists.map((l) => l.id));

/** Places the sample watchlist starts from (chokepoints and ports); ids absent from the graph are skipped. */
export const SAMPLE_PLACE_IDS: readonly string[] = ['chk_suez', 'chk_panama', 'chk_malacca', 'sea_rotterdam', 'sea_singapore', 'sea_losangeles'];
export const SAMPLE_LIST_NAME = 'Key places';

/** State after adding the sample list: the named places and a feed-state rule and a deviation rule on it. Pure; the list is
 *  appended, never merged into an existing one, and nothing is added when no sample place exists in the graph. */
export function withSample(state: WatchState, places: ReadonlyArray<{ id: string; name: string }>): WatchState {
  if (state.lists.length >= MAX_LISTS || state.rules.length + 2 > MAX_RULES) return state;
  const byId = new Map(places.map((p) => [p.id, p.name]));
  const found = SAMPLE_PLACE_IDS.filter((id) => byId.has(id));
  if (found.length === 0) return state;
  const used = state.lists.flatMap((l) => l.items.map((i) => i.id));
  const items: WatchItem[] = found.map((id) => {
    const itemId = nextId('I', used); used.push(itemId);
    const name = clean(byId.get(id), 80) || id;
    return { id: itemId, kind: 'place', label: name, a: id, aName: name };
  });
  const listId = listIdFor(state.lists);
  const ruleIds = state.rules.map((r) => r.id);
  const r1 = nextId('R', ruleIds); ruleIds.push(r1);
  const r2 = nextId('R', ruleIds);
  return {
    ...state,
    lists: [...state.lists, { id: listId, name: SAMPLE_LIST_NAME, items }],
    rules: [...state.rules, { id: r1, listId, type: 'feed_state', enabled: true }, { id: r2, listId, type: 'deviation', enabled: true, level: 2 }],
    active: listId,
  };
}
