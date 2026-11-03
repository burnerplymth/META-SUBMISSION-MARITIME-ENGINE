/**
 * The editorial layer of the feed: the same advisory is grouped rather
 * than printed several times in full, raw source boilerplate gives way
 * to an essence line, and each item shows one timezone. This module is
 * the desk's editorial pass over the served records, and it is DISPLAY-ONLY: grouping, essence
 * extraction and ranking read served fields and change nothing that is
 * stored, exported or graded. The panel export still carries the raw
 * records, full text, original order.
 *
 * Every fixed string the feed and the rail's ranked/narrative sections
 * render lives here (FEED_COPY / RAIL_COPY), kept to the vocabulary law
 * in the same way as the search copy (search/copy.ts).
 */
import type { FeedEvent } from '../types';

/* ── Static copy ─────────────────────────────────────────────── */
export const FEED_COPY = {
  moreAreas: (n: number) => `+${n} more area${n === 1 ? '' : 's'}`,
  records: (n: number) => `${n} records`,
} as const;

export const RAIL_COPY = {
  rankedTitle: 'Ranked now',
  rankedEmpty: 'No active disruptions',
  watchHead: 'Watchlists',
  watchUnavailable: 'Watchlists not served',
  watchNone: 'No watchlists',
  watchMore: (n: number) => `+${n} more places`,
  activeAt: (sev: number) => `Sev ${sev} active`,
  quietAt: 'Quiet',
  narrativeTitle: 'Narrative',
  narrativeNone: 'No chain',
  narrativeLoading: 'Loading',
  narrativeUnavailable: 'Not served',
  narrativeFull: 'Full record in Explain',
} as const;

/* ── Essence: the event itself, boilerplate moved off the headline ──── */

/** The issuing-office segment of an NWS-style headline ("… by NWS
 *  Charleston SC") — a name, carried into the meta row, never parsed
 *  for time. Returns null when the served text carries no office. */
export function officeOf(f: FeedEvent): string | null {
  const text = f.nws?.headline ?? f.headline ?? '';
  const m = text.match(/\bby\s+(NWS\s+.+?)\s*$/);
  return m ? m[1] : null;
}

/** Strip the issue/validity boilerplate from a headline that arrives
 *  without a structured payload: everything from " issued <date> … by
 *  NWS …" (or " in effect …") to the end is furniture, not the event.
 *  Conservative by construction — a headline matching no known
 *  boilerplate shape is returned untouched. */
export function stripBoilerplate(headline: string): string {
  const h = headline.trim();
  const byOffice = h.match(/^(.+?)\s+issued\s+.*\bby\s+NWS\b.*$/i);
  if (byOffice) return byOffice[1].trim();
  const issued = h.match(/^(.+?)\s+issued\s+(?:on\s+)?[A-Z][a-z]+ \d{1,2}\b.*$/);
  if (issued) return issued[1].trim();
  const inEffect = h.match(/^(.+?)\s+in effect\b.*$/i);
  if (inEffect) return inEffect[1].trim();
  return h;
}

/** The display headline: the served event name when the record carries
 *  a structured payload (NWS `event`), else the headline with its
 *  boilerplate stripped. Derived only from the record's own fields. */
export function essenceOf(f: FeedEvent): string {
  const ev = f.nws?.event?.trim();
  if (ev) return ev;
  return stripBoilerplate(f.headline ?? '');
}

/* ── Grouping ──────────────────────────────────────────────────── */

export interface FeedGroup {
  key: string;
  /** Representative record: the group's latest-detected member. */
  rep: FeedEvent;
  members: FeedEvent[];
  essence: string;
  maxSev: number;
  /** Latest detected_at across members (ISO — string compare is safe). */
  latest: string;
  /** Distinct served areas (NWS zone descriptions), member order. */
  areas: string[];
  office: string | null;
}

/** Group the loaded feed for display: exact duplicate records collapse
 *  first (same id, then same dedupe key — a record stored twice is one
 *  advisory, never a count of two), then records sharing source + kind +
 *  place + essence group into one entry. Groups are ordered the way a
 *  desk scans: highest severity first, then most recently detected. */
export function groupFeed(feed: FeedEvent[]): FeedGroup[] {
  const seenIds = new Set<string>();
  const seenKeys = new Set<string>();
  const unique: FeedEvent[] = [];
  for (const f of feed) {
    if (seenIds.has(f.id)) continue;
    if (f.dedupe_key && seenKeys.has(f.dedupe_key)) continue;
    seenIds.add(f.id);
    if (f.dedupe_key) seenKeys.add(f.dedupe_key);
    unique.push(f);
  }
  const byKey = new Map<string, FeedGroup>();
  for (const f of unique) {
    const essence = essenceOf(f);
    const key = `${f.source}|${f.kind}|${f.node_id}|${essence.toLowerCase()}`;
    const g = byKey.get(key);
    if (!g) {
      byKey.set(key, {
        key, rep: f, members: [f], essence,
        maxSev: f.severity, latest: f.detected_at,
        areas: f.nws?.area ? [f.nws.area] : [], office: officeOf(f),
      });
      continue;
    }
    g.members.push(f);
    if (f.severity > g.maxSev) g.maxSev = f.severity;
    if (f.detected_at >= g.latest) { g.latest = f.detected_at; g.rep = f; }
    if (f.nws?.area && !g.areas.includes(f.nws.area)) g.areas.push(f.nws.area);
    if (!g.office) g.office = officeOf(f);
  }
  return [...byKey.values()].sort((a, b) =>
    b.maxSev - a.maxSev || (a.latest < b.latest ? 1 : a.latest > b.latest ? -1 : 0));
}

/* ── Ranking (rail top level) ───────────────────────────────────── */

export interface RankedPlace {
  node_id: string;
  maxSev: number;
  latest: string;
  /** Kind of the place's most severe active record. */
  kind: string;
  activeCount: number;
}

/** Active disruptions ranked by place: severity first, recency second.
 *  Reads the served feed only — the same ranking the feed's editorial
 *  order uses, at place grain. */
export function rankedPlaces(feed: FeedEvent[], limit = 6): RankedPlace[] {
  const byNode = new Map<string, RankedPlace>();
  for (const f of feed) {
    if (f.status !== 'active') continue;
    const cur = byNode.get(f.node_id);
    if (!cur) {
      byNode.set(f.node_id, { node_id: f.node_id, maxSev: f.severity, latest: f.detected_at, kind: f.kind, activeCount: 1 });
      continue;
    }
    cur.activeCount += 1;
    if (f.severity > cur.maxSev) { cur.maxSev = f.severity; cur.kind = f.kind; }
    if (f.detected_at > cur.latest) cur.latest = f.detected_at;
  }
  return [...byNode.values()]
    .sort((a, b) => b.maxSev - a.maxSev || (a.latest < b.latest ? 1 : a.latest > b.latest ? -1 : 0))
    .slice(0, limit);
}

/** Active state of one place from the served feed (watchlist rows):
 *  the highest active severity there, or null when nothing is active. */
export function activeSevAt(feed: FeedEvent[], nodeId: string): number | null {
  let best: number | null = null;
  for (const f of feed) {
    if (f.node_id === nodeId && f.status === 'active') best = best === null ? f.severity : Math.max(best, f.severity);
  }
  return best;
}
