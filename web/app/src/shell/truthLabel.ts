import type { FeedEvent, Health } from '../types';
import { adaptersOfSource } from './basis';
import { adapterFeedState, type FeedWord } from './feedState';
import type { StateKind } from './stateChips';
import type { ProbeState } from './tools';
import type { ShellMode } from './mode';

/* THE one place that decides what word an item wears (project law: a
 * recorded fixture or snapshot is never shown as live).
 *
 * The stored `mode` string says how the record was obtained WHEN it was
 * obtained. A genuine NOAA fetch from 24 Oct stays `mode: LIVE` in the
 * stored file forever, and that file is never rewritten. What the screen
 * may say is a different question and has three inputs:
 *
 *   1. the state of the feed that produced the item NOW (health board),
 *   2. the item's age (retrieved_at against the clock),
 *   3. the stored mode.
 *
 * LIVE needs all three: the stored mode is LIVE, the feed is LIVE now,
 * and the item is younger than that feed's freshness window. Anything
 * else is SNAPSHOT (the feed is serving a stored copy) or RECORDED (the
 * feed is down / unknown, or the item is old), always with its date.
 * Pure: no store import, the clock is a parameter. */

/** The feed's own state now: the health board word, or null when the item's
 *  source has no health adapter at all. */
export type FeedNow = FeedWord;
export type TruthWord = 'LIVE' | 'SNAPSHOT' | 'RECORDED' | 'MODELLED';

export interface TruthLabel {
  word: TruthWord;
  kind: StateKind;
  /** YYYY-MM-DD of the retrieval, when it parses. Shown beside every non-LIVE word. */
  asOf: string | null;
  /** True only for the one combination that earns LIVE. */
  live: boolean;
}

/** How old an item may be and still read LIVE. Mirrors server/live/hub.py
 *  DEFAULT_MAX_AGE_S (noaa 1 h, gdacs 6 h, unknown 6 h) so the board, the
 *  tape and the news list can never disagree. */
const MAX_AGE_S: Record<string, number> = { noaa: 3600, gdacs: 21600, opensky: 3600, aisstream: 3600, portwatch: 86400, cbp: 3600 };
const DEFAULT_MAX_AGE_S = 21600;
/** Tolerated clock skew before a retrieval time counts as "in the future". */
const SKEW_S = 300;

export function feedItemMaxAgeS(source: string | undefined | null): number {
  const names = adaptersOfSource(source ?? '');
  const key = names?.[0];
  return (key && MAX_AGE_S[key]) || DEFAULT_MAX_AGE_S;
}

const KIND: Record<TruthWord, StateKind> = { LIVE: 'live', SNAPSHOT: 'snapshot', RECORDED: 'snapshot', MODELLED: 'model' };

export interface DisplayInput {
  storedMode: string | null | undefined;
  /** The feed's state now; null/undefined = unknown. */
  feed: FeedNow | null | undefined;
  retrievedAt: string | null | undefined;
  now: number;
  /** The served source name (picks the freshness window). */
  source?: string | null;
}

const day = (iso: string | null | undefined): string | null => {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : new Date(t).toISOString().slice(0, 10);
};

/** The display label for one item, or null when the record states no mode
 *  (nothing is invented). */
export function displayLabel(i: DisplayInput): TruthLabel | null {
  const stored = String(i.storedMode ?? '').trim().toUpperCase();
  if (!stored) return null;
  const asOf = day(i.retrievedAt);
  const mk = (word: TruthWord, live = false): TruthLabel => ({ word, kind: KIND[word], asOf, live });
  if (stored === 'MODELED' || stored === 'MODELLED') return mk('MODELLED');
  if (stored === 'SNAPSHOT') return mk('SNAPSHOT');
  if (stored !== 'LIVE') return mk('RECORDED');
  /* stored LIVE: the record was fetched live when it was fetched. */
  const t = i.retrievedAt ? Date.parse(i.retrievedAt) : NaN;
  const ageS = Number.isNaN(t) ? null : (i.now - t) / 1000;
  const fresh = ageS !== null && ageS >= -SKEW_S && ageS <= feedItemMaxAgeS(i.source);
  if (i.feed === 'LIVE' && fresh) return mk('LIVE', true);
  if (i.feed === 'SNAPSHOT') return mk('SNAPSHOT');
  return mk('RECORDED');
}

/** The sentence-case word a label wears on screen. */
export function truthWordText(w: TruthWord): string {
  return w === 'LIVE' ? 'Live' : w === 'SNAPSHOT' ? 'Snapshot' : w === 'MODELLED' ? 'Modelled' : 'Recorded';
}

/** The chip kind a feed record's stored mode may wear in a feed panel. LIVE only
 *  when the stored mode is LIVE and the record is younger than its feed's
 *  freshness window; a stale LIVE record, or a SNAPSHOT, reads SNAPSHOT; a
 *  record with no mode reads NOT SERVED. Pure: the clock is a parameter. */
export function liveChipKind(mode: string | null | undefined, retrievedAt: string | null | undefined, source: string | null | undefined, now: number): 'LIVE' | 'SNAPSHOT' | 'NOT SERVED' {
  const stored = String(mode ?? '').trim().toUpperCase();
  if (stored !== 'LIVE' && stored !== 'SNAPSHOT') return 'NOT SERVED';
  if (stored === 'SNAPSHOT') return 'SNAPSHOT';
  const t = retrievedAt ? Date.parse(retrievedAt) : NaN;
  const ageS = Number.isNaN(t) ? null : (now - t) / 1000;
  return ageS !== null && ageS >= -SKEW_S && ageS <= feedItemMaxAgeS(source) ? 'LIVE' : 'SNAPSHOT';
}

/** The label for a bare provenance block ({source, mode, retrieved_at}). */
export function provenanceLabel(p: { source?: string | null; mode?: string | null; retrieved_at?: string | null } | null | undefined, health: Health | null | undefined, snapshotMode: boolean, now: number): TruthLabel | null {
  if (!p) return null;
  const source = p.source ?? '';
  return displayLabel({ storedMode: p.mode, feed: feedNowOfSource(source, health, snapshotMode), retrievedAt: p.retrieved_at, now, source });
}

/** The feed state now for a served source, off the health board. A board
 *  that has not answered, or a source with no health adapter, gives no
 *  LIVE proof. `snapshotMode` (a captured-copy host) caps every feed at
 *  SNAPSHOT. */
export function feedNowOfSource(source: string, health: Health | null | undefined, snapshotMode = false): FeedNow | null {
  const names = adaptersOfSource(source);
  if (!names) return null;
  return adapterFeedState(health, names, snapshotMode).word;
}

/** The display label for a served feed event. */
export function eventLabel(e: Pick<FeedEvent, 'source' | 'mode' | 'retrieved_at' | 'provenance'>, health: Health | null | undefined, snapshotMode: boolean, now: number): TruthLabel | null {
  const source = e.source || e.provenance?.source || '';
  return displayLabel({
    storedMode: e.mode ?? e.provenance?.mode,
    feed: feedNowOfSource(source, health, snapshotMode),
    retrievedAt: e.retrieved_at ?? e.provenance?.retrieved_at,
    now, source,
  });
}

/** One word for a set of items (KPI tile, pack provenance): LIVE only when
 *  every labelled item is LIVE; else SNAPSHOT when every non-live one is a
 *  snapshot; else RECORDED. Null when nothing is labelled. */
export function aggregateWord(labels: ReadonlyArray<TruthLabel | null>): TruthWord | null {
  const ls = labels.filter((l): l is TruthLabel => l !== null);
  if (ls.length === 0) return null;
  if (ls.every((l) => l.word === 'LIVE')) return 'LIVE';
  const rest = ls.filter((l) => l.word !== 'LIVE');
  if (rest.every((l) => l.word === 'SNAPSHOT')) return 'SNAPSHOT';
  if (rest.every((l) => l.word === 'MODELLED')) return 'MODELLED';
  return 'RECORDED';
}

/* ── chain titles ───────────────────────────────────────────────────── */

/** What a desk run is built from, kept so its title can be re-derived at
 *  display time from the feed's state then. */
export interface LiveRunRef { source: string; retrieved_at?: string; mode?: string; what: string }
interface RunMetaLike { label: string; mode: string; liveRef?: LiveRunRef }

/** Run meta for a chain seeded from a feed event. The stored `label` is the
 *  conservative RECORDED form (never LIVE), so any reader that does not
 *  re-derive it is still honest; `mode` is the desk mode and is unchanged. */
export function liveRunMeta(item: Pick<FeedEvent, 'source' | 'kind' | 'mode' | 'retrieved_at' | 'provenance'> | null, place: string): { label: string; mode: 'LIVE'; liveRef?: LiveRunRef } {
  if (!item) return { label: `RECORDED — disruption at ${place}`, mode: 'LIVE' };
  const what = `${item.source} ${item.kind} at ${place}`;
  return {
    label: `RECORDED — ${what}`, mode: 'LIVE',
    liveRef: { source: item.source, retrieved_at: item.retrieved_at ?? item.provenance?.retrieved_at, mode: item.mode ?? item.provenance?.mode, what },
  };
}

/** The word a desk run wears now: its mode unless it was built from a feed
 *  event, in which case the event's display word. */
export function runWord(m: RunMetaLike, health: Health | null | undefined, snapshotMode: boolean, now: number): string {
  if (m.mode === 'CANNED') return 'REPLAY';
  if (m.mode !== 'LIVE') return m.mode;
  const r = m.liveRef;
  if (!r) return 'RECORDED';
  return eventLabel({ source: r.source, mode: r.mode, retrieved_at: r.retrieved_at, provenance: undefined }, health, snapshotMode, now)?.word ?? 'RECORDED';
}

/** The title a run header shows now. */
export function runTitle(m: RunMetaLike, health: Health | null | undefined, snapshotMode: boolean, now: number): string {
  if (m.mode !== 'LIVE' || !m.liveRef) return m.label;
  return `${runWord(m, health, snapshotMode, now)} — ${m.liveRef.what}`;
}

/* ── shell chrome that must not overclaim ─────────────────────────────── */

/** True while some adapter on the health board is LIVE and the host is not
 *  serving a captured copy. */
export function anyFeedLive(health: Health | null | undefined, snapshotMode: boolean): boolean {
  return !snapshotMode && !!health?.adapters.some((a) => String(a.state).toUpperCase() === 'LIVE');
}

/** The word the NOW strip wears: Live only when every active feed record earns
 *  LIVE now (feed state, age and stored mode), otherwise Recorded. One feed
 *  being live is not enough. */
export function nowStripWord(feed: ReadonlyArray<Pick<FeedEvent, 'source' | 'mode' | 'retrieved_at' | 'provenance' | 'status'>>, health: Health | null | undefined, snapshotMode: boolean, now: number): 'LIVE' | 'RECORDED' {
  const labels = feed.filter((f) => f.status === 'active').map((f) => eventLabel(f, health, snapshotMode, now));
  return aggregateWord(labels) === 'LIVE' ? 'LIVE' : 'RECORDED';
}

/** How a mode cell is marked: 'feed' (state colour) only for the selected LIVE
 *  cell while a feed is LIVE now; every other cell is neutral. */
export function modeCellTone(cell: ShellMode, selected: ShellMode, health: Health | null | undefined, snapshotMode: boolean): 'feed' | 'neutral' {
  return cell === 'live' && selected === 'live' && anyFeedLive(health, snapshotMode) ? 'feed' : 'neutral';
}

/** The chip for a tool route probe. A route that answers 200 is "Served"; it
 *  wears a feed state only when the tool names a feed and that feed's state
 *  now is known. */
export function toolProbeChip(state: ProbeState, feed: FeedNow | null | undefined): { kind: StateKind; word?: string } {
  if (state === 'notserved') return { kind: 'notserved' };
  if (state === 'down') return { kind: 'down' };
  if (state === 'checking') return { kind: 'meta', word: 'Checking' };
  if (feed === 'LIVE') return { kind: 'live' };
  if (feed === 'SNAPSHOT' || feed === 'RECORDED') return { kind: 'snapshot' };
  if (feed === 'DOWN') return { kind: 'down' };
  return { kind: 'meta', word: 'Served' };
}
