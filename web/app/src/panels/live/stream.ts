/* LIVE tape — SSE reducer, bounded buffer, reconnect and visibility
 * policy. All pure: the hook in tape.tsx only wires an EventSource to
 * these functions, so the stream protocol is plain, testable logic
 * that needs no browser.
 *
 * Law kept here: a tape row is a SERVED live record (event / health
 * change) with its provenance mode; nothing is interpolated or
 * relabelled; a gap or reset sets `resync` so the consumer re-reads
 * /live/snapshot instead of pretending the tape is complete. */

import type { HealthFeed, Provenance, SnapshotEvent } from './model';
import { normState } from './model';
import { parseCitation, type Citation } from '../../citation';

export const TAPE_MAX = 200;

export type TapeKind = 'event' | 'health';
export interface TapeItem {
  key: string;              // stable identity (event id, or health:<feed>:<seq>)
  kind: TapeKind;
  at: string;               // ISO time the record carries (detected_at / retrieved_at)
  seq: number | null;       // stream seq it arrived on (null when seeded from a snapshot)
  title: string;
  nodeId: string | null;
  eventKind: string;        // weather / disaster / ... or 'health'
  severity: number | null;
  status: string;           // active / resolved / ENDED / state word
  source: string;
  mode: string;             // LIVE / SNAPSHOT as served
  retrievedAt?: string;     // provenance retrieval time (the age the LIVE chip is judged on)
  citation: Citation | null;
  ended: boolean;
}

export type ConnStatus = 'idle' | 'connecting' | 'open' | 'paused' | 'reconnecting' | 'resync' | 'off';

export interface TapeState {
  items: TapeItem[];
  max: number;
  epoch: string | null;
  lastEventId: string | null;
  conn: ConnStatus;
  resync: boolean;
  /** last known feed state by id, to turn a changed state into one tape row */
  feedStates: Record<string, string>;
  counters: { received: number; rejected: number; gaps: number; dropped: number; resets: number; heartbeats: number };
  lastHeartbeatAt: string | null;
  lastReason: string | null;
}

export const initialTape = (max = TAPE_MAX): TapeState => ({
  items: [], max, epoch: null, lastEventId: null, conn: 'idle', resync: false, feedStates: {},
  counters: { received: 0, rejected: 0, gaps: 0, dropped: 0, resets: 0, heartbeats: 0 }, lastHeartbeatAt: null, lastReason: null,
});

/** One message as the EventSource hands it over. */
export interface SseMessage { type: string; data: string; lastEventId?: string | null }

/* ── bounded buffer ─────────────────────────────────────────────────── */

/** Newest-first order by the record's own time, then arrival (seq) desc,
 *  then key — deterministic whatever the arrival order. */
export function compareTape(a: TapeItem, b: TapeItem): number {
  if (a.at !== b.at) return a.at < b.at ? 1 : -1;
  const sa = a.seq ?? -1, sb = b.seq ?? -1;
  if (sa !== sb) return sb - sa;
  return a.key < b.key ? -1 : a.key > b.key ? 1 : 0;
}

/** Merge items into the buffer: an item with a known key replaces the
 *  old row in place (an upsert of the same event never duplicates it),
 *  new keys are added, the buffer is sorted newest first and the oldest
 *  rows beyond `max` are dropped. Returns a new array. */
export function mergeBounded(buffer: readonly TapeItem[], incoming: readonly TapeItem[], max: number): TapeItem[] {
  const byKey = new Map<string, TapeItem>();
  for (const it of buffer) byKey.set(it.key, it);
  for (const it of incoming) byKey.set(it.key, it);
  return [...byKey.values()].sort(compareTape).slice(0, Math.max(1, max));
}

/* ── mapping served records to rows ─────────────────────────────────── */

const provOf = (prov: Provenance[] | undefined, p: number | undefined): Provenance | undefined => prov?.[p ?? 0];

export function eventItem(e: SnapshotEvent, prov: Provenance[] | undefined, seq: number | null): TapeItem {
  const pr = provOf(prov, e.p);
  return {
    key: e.id, kind: 'event', at: e.detected_at, seq, title: e.title, nodeId: e.node_id ?? null, eventKind: e.kind,
    severity: typeof e.severity === 'number' ? e.severity : null, status: e.status ?? '', source: pr?.source ?? '', mode: pr?.mode ?? '', ...(pr?.retrieved_at ? { retrievedAt: pr.retrieved_at } : {}),
    citation: parseCitation(e.citation) ?? null, ended: e.status === 'resolved',
  };
}

/** Seed the tape from GET /live/snapshot events (newest first) and take
 *  the resume point from it. Clears a pending resync. */
export function seedFromSnapshot(state: TapeState, snap: { epoch: string; last_event_id: string; events?: { items?: SnapshotEvent[]; provenance?: Provenance[] } }): TapeState {
  const items = (snap.events?.items ?? []).map((e) => eventItem(e, snap.events?.provenance, null));
  return { ...state, items: mergeBounded([], items, state.max), epoch: snap.epoch, lastEventId: snap.last_event_id, resync: false };
}

const safeParse = (s: string): Record<string, unknown> | null => {
  try { const v = JSON.parse(s); return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null; } catch { return null; }
};

/** Apply one SSE message. Unknown events and malformed data are counted
 *  as rejected and change nothing else. */
export function reduceMessage(state: TapeState, msg: SseMessage, nowIso: string): TapeState {
  const body = safeParse(msg.data);
  if (!body) return { ...state, counters: { ...state.counters, rejected: state.counters.rejected + 1 } };
  const withId = msg.lastEventId ? { lastEventId: msg.lastEventId } : {};
  const counted = { ...state.counters, received: state.counters.received + 1 };
  switch (msg.type) {
    case 'hello': {
      const epoch = typeof body.epoch === 'string' ? body.epoch : state.epoch;
      /* a fresh client carries an id; a resumed one keeps ours */
      const lastEventId = msg.lastEventId || (state.lastEventId && epoch === state.epoch ? state.lastEventId : (typeof body.last_event_id === 'string' ? body.last_event_id : state.lastEventId));
      return { ...state, epoch, lastEventId, conn: 'open', counters: counted, lastReason: null };
    }
    case 'heartbeat':
      return { ...state, conn: 'open', counters: { ...counted, heartbeats: counted.heartbeats + 1 }, lastHeartbeatAt: nowIso };
    case 'events': {
      const seq = typeof body.seq === 'number' ? body.seq : null;
      const prov = body.provenance as Provenance[] | undefined;
      const upserts = Array.isArray(body.items) ? (body.items as SnapshotEvent[]).filter((e) => e && typeof e.id === 'string').map((e) => eventItem(e, prov, seq)) : [];
      let items = mergeBounded(state.items, upserts, state.max);
      if (Array.isArray(body.removed)) {
        const gone = new Set(body.removed as string[]);
        items = items.map((it) => (gone.has(it.key) ? { ...it, ended: true, status: 'ENDED' } : it));
      }
      return { ...state, items, counters: counted, ...withId, conn: 'open' };
    }
    case 'health': {
      const seq = typeof body.seq === 'number' ? body.seq : null;
      const feeds = (Array.isArray(body.feeds) ? body.feeds : []) as HealthFeed[];
      const states = { ...state.feedStates };
      const rows: TapeItem[] = [];
      for (const f of feeds) {
        if (!f || typeof f.id !== 'string') continue;
        const now = normState(f.state);
        const was = states[f.id];
        states[f.id] = now;
        if (was !== undefined && was !== now) {
          rows.push({
            key: `health:${f.id}:${seq ?? nowIso}`, kind: 'health', at: f.retrieved_at ?? nowIso, seq, title: `${f.label ?? f.id}: ${was} → ${now}`,
            nodeId: null, eventKind: 'health', severity: null, status: now, source: f.source ?? '', mode: f.mode ?? '', ...(f.retrieved_at ? { retrievedAt: f.retrieved_at } : {}), citation: null, ended: false,
          });
        }
      }
      return { ...state, items: rows.length ? mergeBounded(state.items, rows, state.max) : state.items, feedStates: states, counters: counted, ...withId, conn: 'open' };
    }
    case 'reset':
      return { ...state, resync: true, conn: 'resync', counters: { ...counted, resets: counted.resets + 1 }, lastReason: typeof body.reason === 'string' ? body.reason : 'reset',
        lastEventId: typeof body.last_event_id === 'string' ? body.last_event_id : state.lastEventId };
    case 'gap':
      return { ...state, resync: true, counters: { ...counted, gaps: counted.gaps + 1, dropped: counted.dropped + (typeof body.dropped === 'number' ? body.dropped : 0) },
        lastReason: typeof body.reason === 'string' ? body.reason : 'gap' };
    case 'bye':
      return { ...state, conn: 'reconnecting', counters: counted, lastReason: typeof body.reason === 'string' ? body.reason : 'bye' };
    default:
      /* vessels / aircraft / ports / borders are not subscribed on the tape; anything else is unknown */
      return { ...state, counters: { ...state.counters, rejected: state.counters.rejected + 1 } };
  }
}

/* ── connection policy ──────────────────────────────────────────────── */

/** Reconnect delay: 1 s doubling to a 30 s cap; `jitter` in [0,1) adds
 *  up to 20% so reconnecting tabs do not stampede. */
export function reconnectDelayMs(attempt: number, jitter = 0): number {
  const base = Math.min(30000, 1000 * 2 ** Math.max(0, attempt));
  return Math.round(base * (1 + 0.2 * Math.min(0.999, Math.max(0, jitter))));
}

/** Whether a stream should be open right now. */
export function shouldConnect(o: { enabled: boolean; hidden: boolean; paused: boolean }): boolean {
  return o.enabled && !o.hidden && !o.paused;
}

/** Chip kind for the connection: a connection is not data freshness, so an open
 *  stream is the neutral chip and never LIVE; the feed chips beside it say what
 *  state the data is in. */
export function connKind(c: ConnStatus): 'SOURCED' | 'NOT SERVED' | 'DOWN' {
  return c === 'open' ? 'SOURCED' : c === 'paused' || c === 'idle' ? 'NOT SERVED' : 'DOWN';
}

export function connLabel(c: ConnStatus, hidden: boolean): string {
  if (hidden && c === 'paused') return 'Paused, hidden';
  return ({ idle: 'Idle', connecting: 'Connecting', open: 'Stream open', paused: 'Paused', reconnecting: 'Reconnecting', resync: 'Resync', off: 'Off' } as Record<ConnStatus, string>)[c];
}

/** `/live/stream` URL. The first connect carries the resume point as a
 *  query (EventSource cannot set headers); the topics are the tape's
 *  two: events and health. */
export function streamPath(lastEventId: string | null, topics = 'events,health'): string {
  const q = new URLSearchParams({ topics });
  if (lastEventId) q.set('lastEventId', lastEventId);
  return `/live/stream?${q.toString()}`;
}

/** Tape row filter: by minimum severity and kind. */
export function filterTape(items: readonly TapeItem[], o: { minSeverity: number; kinds: readonly string[] }): TapeItem[] {
  return items.filter((i) => (i.kind === 'health' || (i.severity ?? 0) >= o.minSeverity) && (o.kinds.length === 0 || o.kinds.includes(i.kind === 'health' ? 'health' : i.eventKind)));
}
