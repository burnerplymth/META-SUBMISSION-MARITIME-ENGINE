/* Pure evaluation of the rules. A rule reads values the API already serves
 * and says nothing about the future: a condition is either met by the value
 * read now or it is not. A met condition is recorded once when it becomes met
 * (and again after it has stopped being met); a feed state change is recorded
 * when the state read now differs from the state read last time. The clock is
 * a parameter. */
import { displayLabel } from '../../shell/truthLabel';
import { MAX_TRIGGERED } from './storage';
import type { Basis, FeedWord, Memory, Observations, PlaceObs, RuleState, RuleType, Triggered, TransitObs, WatchItem, WatchList, WatchRule, WatchState } from './types';

/** The word a place's state chip wears. LIVE needs the cell's feed state LIVE, its as-of inside that feed's
 *  window and a host that is not serving a captured copy (the shared truth label); a LIVE cell that fails
 *  that test reads RECORDED, a snapshot cell reads SNAPSHOT. */
export function placeStateWord(obs: PlaceObs, snapshotMode: boolean, at?: string): 'LIVE' | 'SNAPSHOT' | 'RECORDED' {
  if (obs.feed === 'live') {
    const t = displayLabel({
      storedMode: 'LIVE', feed: snapshotMode ? 'SNAPSHOT' : 'LIVE', retrievedAt: obs.asOf,
      now: at ? Date.parse(at) : Date.now(), source: obs.source ?? 'IMF PortWatch',
    });
    return t?.live ? 'LIVE' : 'RECORDED';
  }
  return snapshotMode ? 'RECORDED' : 'SNAPSHOT';
}

export const RULE_WORD: Record<RuleType, string> = { feed_state: 'Feed state change', deviation: 'Deviation chip', added_transit: 'Added transit' };
export const LEVEL_WORD: Record<number, string> = { 0: 'In range', 1: 'Deviation low', 2: 'Deviation mid', 3: 'Deviation high' };
export const FEED_WORD: Record<FeedWord, string> = { live: 'Live', snapshot: 'Snapshot', notserved: 'Not served' };
export const BASIS_WORD: Record<Basis, string> = { live: 'Live', snapshot: 'Snapshot', recorded: 'Recorded', modelled: 'Modelled' };
/** The chip kind each basis wears on the one state grammar. */
export const BASIS_CHIP: Record<Basis, string> = { live: 'LIVE', snapshot: 'SNAPSHOT', recorded: 'SNAPSHOT', modelled: 'MODELLED' };

const placeItem = (i: WatchItem) => i.kind === 'place';
const transitItem = (i: WatchItem) => i.kind !== 'place';
export const appliesTo = (type: RuleType, item: WatchItem): boolean => (type === 'added_transit' ? transitItem(item) : placeItem(item));

export const fmtD = (v: number | null | undefined): string => (v == null || !Number.isFinite(v) ? '—' : v.toFixed(1));
export const fmtRange = (o: Pick<TransitObs, 'low' | 'high'>): string => (o.low == null || o.high == null ? '—' : `${fmtD(o.low)} to ${fmtD(o.high)} d`);
export const fmtStamp = (iso: string): string => { const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/.exec(iso); return m ? `${m[1]} ${m[2]}:${m[3]}Z` : '—'; };

/** Key of one leg or lane read against one rule's scenario and duration. */
export const transitKey = (r: Pick<WatchRule, 'scenarioId' | 'durationD'>, item: Pick<WatchItem, 'id'>): string => `${r.scenarioId}|${r.durationD}|${item.id}`;
/** Key of the latch of one rule on one item. */
export const memKey = (r: Pick<WatchRule, 'id'>, item: Pick<WatchItem, 'id'>): string => `${r.id}|${item.id}`;

/** What a feed-based value wears: a snapshot read from an API serving a captured copy is Recorded. */
export function feedBasis(feed: FeedWord, snapshotMode: boolean): Basis {
  return feed === 'live' && !snapshotMode ? 'live' : snapshotMode ? 'recorded' : 'snapshot';
}

export function ruleName(r: WatchRule, scenarioLabel?: (id: string) => string): string {
  if (r.type === 'feed_state') return RULE_WORD.feed_state;
  if (r.type === 'deviation') return `${RULE_WORD.deviation}, ${LEVEL_WORD[r.level ?? 1].toLowerCase()} or higher`;
  const s = r.scenarioId ? scenarioLabel?.(r.scenarioId) ?? r.scenarioId : '';
  return `Added transit above ${fmtD(r.thresholdD)} d, ${s} ${r.durationD ?? ''} d`.replace(/\s+/g, ' ').trim();
}

export interface EvalOut { triggered: Triggered[]; memory: Memory; states: Record<string, RuleState> }

/** Evaluates every enabled rule against the observations. `triggered` holds only the entries new in this run. */
export function evaluate(state: WatchState, obs: Observations, nowIso: string, scenarioLabel?: (id: string) => string): EvalOut {
  const memory: Memory = { met: { ...state.memory.met }, feed: { ...state.memory.feed } };
  const out: Triggered[] = [];
  const states: Record<string, RuleState> = {};
  const listOf = new Map(state.lists.map((l) => [l.id, l]));
  /* the feed memory is per item, updated once per run after every feed-state rule has read it */
  const feedNext: Record<string, string> = {};
  for (const r of state.rules) {
    const list = listOf.get(r.listId);
    const items = (list?.items ?? []).filter((i) => appliesTo(r.type, i));
    const st: RuleState = { status: 'not_evaluated', items: items.length, met: 0 };
    states[r.id] = st;
    if (!list || !r.enabled) continue;
    const rname = ruleName(r, scenarioLabel);
    const push = (item: WatchItem, basis: Basis, detail: string) =>
      out.push({ id: `${nowIso}|${r.id}|${item.id}`, at: nowIso, ruleId: r.id, listName: list.name, itemLabel: item.label, rule: rname, detail, basis });
    let evaluated = 0;
    for (const item of items) {
      if (r.type === 'feed_state') {
        const o = obs.places[item.a];
        if (!o) continue;
        evaluated += 1;
        const prev = memory.feed[item.id] as FeedWord | undefined;
        feedNext[item.id] = o.feed;
        if (prev && prev !== o.feed) {
          st.met += 1;
          push(item, feedBasis(o.feed, obs.snapshotMode), `Feed state ${FEED_WORD[prev].toLowerCase()} to ${FEED_WORD[o.feed].toLowerCase()}`);
        }
      } else if (r.type === 'deviation') {
        const o = obs.places[item.a];
        if (!o || o.deviation == null) continue;
        evaluated += 1;
        const met = o.deviation >= (r.level ?? 1);
        const k = memKey(r, item);
        if (met) st.met += 1;
        if (met && memory.met[k] !== true) push(item, feedBasis(o.feed, obs.snapshotMode), `${LEVEL_WORD[o.deviation]} on vessel activity`);
        memory.met[k] = met;
      } else {
        const o = obs.transit[transitKey(r, item)];
        if (!o || o.median == null) continue;
        evaluated += 1;
        const met = o.median > (r.thresholdD ?? 0);
        const k = memKey(r, item);
        if (met) st.met += 1;
        if (met && memory.met[k] !== true) push(item, 'modelled', `Added transit ${fmtD(o.median)} d, range ${fmtRange(o)}, above ${fmtD(r.thresholdD)} d`);
        memory.met[k] = met;
      }
    }
    st.status = evaluated === 0 ? 'not_evaluated' : st.met > 0 ? 'met' : 'not_met';
  }
  Object.assign(memory.feed, feedNext);
  return { triggered: out, memory, states };
}

/** Folds one run into the state: new entries first, the list capped, memory replaced. */
export function applyRun(state: WatchState, run: EvalOut): WatchState {
  const known = new Set(state.triggered.map((t) => t.id));
  const fresh = run.triggered.filter((t) => !known.has(t.id));
  return { ...state, triggered: [...fresh.sort((a, b) => a.itemLabel.localeCompare(b.itemLabel)), ...state.triggered].slice(0, MAX_TRIGGERED), memory: run.memory };
}

/* ── what to fetch ───────────────────────────────────────────────────── */

/** Distinct watched place ids (place items only, rules or not: the list shows the current state of each). */
export function placeIds(lists: readonly WatchList[]): string[] {
  return [...new Set(lists.flatMap((l) => l.items.filter(placeItem).map((i) => i.a)))];
}

export interface TransitGroup { scenarioId: string; durationD: number; items: WatchItem[]; keys: string[] }
/** Legs and lanes of enabled added-transit rules, grouped by scenario and duration (one request per group, at most 50 lanes each). */
export function transitGroups(state: WatchState): TransitGroup[] {
  const map = new Map<string, TransitGroup>();
  const owner = new Map<string, string>(); // transit key -> item id (a suffix match could hit another item)
  for (const r of state.rules) {
    if (!r.enabled || r.type !== 'added_transit' || !r.scenarioId) continue;
    const list = state.lists.find((l) => l.id === r.listId);
    if (!list) continue;
    const gk = `${r.scenarioId}|${r.durationD}`;
    const g = map.get(gk) ?? { scenarioId: r.scenarioId, durationD: r.durationD ?? 14, items: [], keys: [] };
    for (const i of list.items.filter(transitItem)) {
      const key = transitKey(r, i);
      if (g.keys.includes(key)) continue;
      g.keys.push(key);
      owner.set(`${gk}\u0000${key}`, i.id);
      if (!g.items.some((x) => x.id === i.id)) g.items.push(i);
    }
    map.set(gk, g);
  }
  const out: TransitGroup[] = [];
  for (const g of map.values()) {
    for (let n = 0; n < g.items.length; n += 50) {
      const items = g.items.slice(n, n + 50);
      out.push({ ...g, items, keys: g.keys.filter((k) => items.some((i) => owner.get(`${g.scenarioId}|${g.durationD}\u0000${k}`) === i.id)) });
    }
  }
  return out;
}

/** The deviation level of a served place status label, or null for NO DATA. */
export function levelOf(label: string): 0 | 1 | 2 | 3 | null {
  switch (String(label).toUpperCase()) {
    case 'IN RANGE': return 0; case 'DEVIATION LOW': return 1; case 'DEVIATION MID': return 2; case 'DEVIATION HIGH': return 3; default: return null;
  }
}

export const obsOfPlace = (o: PlaceObs | undefined): { feed: string; level: string } =>
  ({ feed: o ? FEED_WORD[o.feed] : 'Not read', level: o && o.deviation != null ? LEVEL_WORD[o.deviation] : 'No data' });
