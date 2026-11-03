import type { FeedEvent, Health } from '../types';
import { adapterFeedState, type FeedState } from './feedState';

/* A panel header states its real BASIS, never one global LIVE
 * word. A composite panel (Disruption News) reads from several feeds: its
 * header says COMPOSITE and a chip popover lists each feed with its own
 * state. A panel over a stored collection (ledger, alerts) says LOCAL. Pure,
 * no store import: the rows are computed from the served records and the
 * source-health board. */

export type BasisWord = 'Composite' | 'Local';

export interface BasisRow {
  /** The feed as the records name it (served `source`). */
  label: string;
  /** The feed's own state on the health board; null when the source has no health adapter. */
  state: FeedState | null;
  /** Records in the panel that came from this feed. */
  count: number;
}

/** Served `source` strings mapped onto health-board adapters (registry keys). */
const SOURCE_ADAPTERS: ReadonlyArray<[RegExp, readonly string[]]> = [
  [/(?<![a-z])(noaa|nws)(?![a-z])/i, ['noaa']],
  [/(?<![a-z])gdacs(?![a-z])/i, ['gdacs']],
  [/(?<![a-z])gdelt(?![a-z])/i, ['gdelt']],
  [/(?<![a-z])cbp(?![a-z])/i, ['cbp']],
  [/portwatch/i, ['portwatch', 'portwatch_daily']],
  [/(?<![a-z])eia(?![a-z])/i, ['eia']],
  [/(?<![a-z])opensky(?![a-z])/i, ['opensky']],
  [/(?<![a-z])ais(stream)?(?![a-z])/i, ['aisstream']],
];

export const adaptersOfSource = (source: string): readonly string[] | null =>
  SOURCE_ADAPTERS.find(([re]) => re.test(source))?.[1] ?? null;

/** One row per distinct feed in the records, most records first (ties by name). */
export function feedBasisRows(
  feed: ReadonlyArray<Pick<FeedEvent, 'source'>>, health: Health | null | undefined, snapshotMode = false,
): BasisRow[] {
  const counts = new Map<string, number>();
  for (const f of feed) {
    const label = (f.source || '').trim();
    if (label) counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([label, count]) => {
      const names = adaptersOfSource(label);
      return { label, count, state: names ? adapterFeedState(health, names, snapshotMode) : null };
    })
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

/** The header stamp for a composite panel: COMPOSITE and the feed count. */
export const compositeMeta = (rows: readonly BasisRow[]): string =>
  `${rows.length} ${rows.length === 1 ? 'FEED' : 'FEEDS'}`;
