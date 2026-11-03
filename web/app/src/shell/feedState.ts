import type { Health } from '../types';
import type { StateKind } from './stateChips';

/* One feed's own state, read from the source-health board (GET /health)
 * or from a payload's provenance, never from one global word. A panel
 * header, a map-bar chip and the status bar all call this, so they can
 * never disagree about the same feed. Pure, no store import. */

export type FeedWord = 'LIVE' | 'SNAPSHOT' | 'DOWN' | 'NOT SERVED' | 'RECORDED' | 'SIMULATED' | 'CHECKING';
export interface FeedState {
  word: FeedWord;
  kind: StateKind;
  /** The board's note for this adapter (NEEDS-KEY, NEEDS-CONFIG, ...), when it carries one. */
  note: string | null;
  /** True when the board says the feed is down for want of a key or config. */
  needsKey: boolean;
  /** A DOWN feed that is simply not connected (never succeeded, no failure recorded, or the whole board is a
   *  recorded copy): shown calm and neutral, never as an outage. Red stays for a feed that is failing. */
  calm?: 'not-served' | 'offline';
}

const KIND: Record<FeedWord, StateKind> = {
  LIVE: 'live', SNAPSHOT: 'snapshot', DOWN: 'down', 'NOT SERVED': 'notserved',
  RECORDED: 'snapshot', SIMULATED: 'sim', CHECKING: 'meta',
};
export const feedState = (word: FeedWord, note: string | null = null): FeedState =>
  ({ word, kind: KIND[word], note, needsKey: !!note && /NEEDS-(KEY|CONFIG)/i.test(note) });

/** The state of ONE adapter on the health board. `names` are tried in
 *  order (registry keys, exact match). No board yet = CHECKING; a board
 *  that does not list the adapter = NOT SERVED. When the whole API is
 *  serving a captured copy (`snapshotMode`) a feed that is not DOWN reads
 *  SNAPSHOT, never LIVE. */
export function adapterFeedState(health: Health | null | undefined, names: string | readonly string[], snapshotMode = false): FeedState {
  if (!health) return feedState('CHECKING');
  const list = typeof names === 'string' ? [names] : names;
  const a = health.adapters.find((x) => list.includes(x.adapter.toLowerCase()));
  if (!a) return feedState('NOT SERVED');
  const note = a.note ?? null;
  const st = String(a.state).toUpperCase();
  if (st === 'DOWN') return downState(a, health.adapters, note, snapshotMode);
  if (st === 'SNAPSHOT' || snapshotMode) return feedState('SNAPSHOT', note);
  if (st === 'LIVE') return feedState('LIVE', note);
  return downState(a, health.adapters, note, snapshotMode);
}

/** A feed the board lists as down. It reads as a failure (red) only when it is configured and failing; a feed that
 *  never connected and recorded no failure is NOT SERVED, and in a recorded or offline copy (no feed live on the
 *  whole board) a feed that never connected is an offline copy. */
function downState(a: { last_success_at?: string | null }, board: ReadonlyArray<{ state: string }>, note: string | null, snapshotMode: boolean): FeedState {
  const f = feedState('DOWN', note);
  const never = !a.last_success_at;
  if (never && (!note || f.needsKey)) return { ...f, calm: 'not-served' };
  if (never && (snapshotMode || (board.length > 0 && board.every((x) => String(x.state).toUpperCase() !== 'LIVE')))) return { ...f, calm: 'offline' };
  return f;
}

/** What a chip shows for a feed: kind, sentence-case label, and whether it reads calm (muted, not an outage). */
export interface FeedView { kind: StateKind; label: string; calm: boolean; className: string }
export function feedView(f: FeedState): FeedView {
  if (f.word === 'DOWN' && f.calm === 'not-served') return { kind: 'notserved', label: 'Not served', calm: true, className: '' };
  if (f.word === 'DOWN' && f.calm === 'offline') return { kind: 'snapshot', label: 'Offline copy', calm: true, className: 'chip--offline' };
  return { kind: f.kind, label: WORD_SENTENCE[f.word], calm: false, className: '' };
}

/** The Mode cell of a Source health row. A feed that is not served has no mode
 *  to state, so it reads "—" rather than the stored copy's mode. */
export function healthModeCell(view: Pick<FeedView, 'kind'>, mode: string | null | undefined, word: (m: string) => string): string {
  if (view.kind === 'notserved') return '—';
  return mode ? word(mode) : '—';
}

/** The same view for one row of the health board (status bar, Source health). */
export function adapterRowView(health: Health, adapter: string, snapshotMode = false): FeedView {
  return feedView(adapterFeedState(health, adapter.toLowerCase(), snapshotMode));
}

/** A panel-header stamp: the feed's own state word, then the source. */
const WORD_SENTENCE: Record<FeedWord, string> = {
  LIVE: 'Live', SNAPSHOT: 'Snapshot', DOWN: 'Down', 'NOT SERVED': 'Not served', RECORDED: 'Recorded', SIMULATED: 'Simulated', CHECKING: 'Checking',
};
export const feedStamp = (f: FeedState, source: string): string => `${feedView(f).label} · ${source}`;

/** The adapters behind each header (registry keys on the health board). */
export const FEED_ADAPTERS = {
  edgar: ['edgar'],
  portwatchDaily: ['portwatch_daily'],
  ais: ['aisstream'],
} as const;
