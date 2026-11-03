/* Shapes of the watchlists window. Everything here is kept in this browser
 * (localStorage, one key) and read against feeds that are already served:
 * GET /ports/{place} for a watched place and POST /exposure/evaluate for a
 * leg or lane. Nothing is sent anywhere except those read requests. */

export type ItemKind = 'place' | 'leg' | 'lane';
/** A watched thing. A place keeps its id in `a`; a leg keeps the place ids of
 *  both ends; a lane (from My shipments) keeps the origin and destination
 *  texts the shipment list uses. */
export interface WatchItem { id: string; kind: ItemKind; label: string; a: string; b?: string; aName?: string; bName?: string }
export interface WatchList { id: string; name: string; items: WatchItem[] }

export type RuleType = 'feed_state' | 'deviation' | 'added_transit';
export const RULE_TYPES: readonly RuleType[] = ['feed_state', 'deviation', 'added_transit'];
export interface WatchRule {
  id: string; listId: string; type: RuleType; enabled: boolean;
  /** deviation: lowest level that counts, 1 Low, 2 Mid, 3 High */
  level?: 1 | 2 | 3;
  /** added_transit: library scenario, its duration and the threshold in days */
  scenarioId?: string; durationD?: number; thresholdD?: number;
}

/** How the value behind a triggered entry was obtained. */
export type Basis = 'live' | 'snapshot' | 'recorded' | 'modelled';
export const BASES: readonly Basis[] = ['live', 'snapshot', 'recorded', 'modelled'];
export interface Triggered {
  id: string; at: string; ruleId: string; listName: string; itemLabel: string; rule: string; detail: string; basis: Basis;
}

/** What the last evaluation remembered, so a condition fires once when it becomes met. */
export interface Memory { met: Record<string, boolean>; feed: Record<string, string> }

export interface WatchState {
  lists: WatchList[]; rules: WatchRule[]; triggered: Triggered[]; memory: Memory; active: string | null;
}

export type FeedWord = 'live' | 'snapshot' | 'notserved';
/** One watched place as read from the served ports board. */
export interface PlaceObs { feed: FeedWord; deviation: 0 | 1 | 2 | 3 | null; asOf: string | null; /** The feed that produced the state cell (picks the freshness window). */ source?: string }
export type PlaceResult = { kind: 'ok'; obs: PlaceObs } | { kind: 'notserved' } | { kind: 'down'; code: string };
/** One leg or lane read against a library scenario: modelled added transit, days. */
export interface TransitObs { median: number | null; low: number | null; high: number | null; label: string }

export interface Observations {
  places: Record<string, PlaceObs | undefined>;
  /** keyed by transitKey(rule, item) */
  transit: Record<string, TransitObs | undefined>;
  /** the API serves a captured copy: a snapshot reads Recorded */
  snapshotMode: boolean;
}

export type RuleStatus = 'met' | 'not_met' | 'not_evaluated';
export interface RuleState { status: RuleStatus; items: number; met: number }
