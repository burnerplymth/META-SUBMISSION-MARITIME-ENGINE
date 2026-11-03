import { useEffect, useReducer, useRef, useState } from 'react';
import { API_BASE } from '../../api';
import { getJson, HttpError } from '../ports/http';
import { useAsync, useVisibleTick } from '../ports/useAsync';
import type { ContextIndexFeed, HealthDoc, SnapshotDoc } from './model';
import { SNAPSHOT_COUNTS_QUERY, gateOf, type LiveGate } from './model';
import { initialTape, reconnectDelayMs, reduceMessage, seedFromSnapshot, shouldConnect, streamPath, type SseMessage, type TapeState } from './stream';

/* Data hooks for the LIVE terminal panels. The flag law is enforced
 * here as well as in the store: a 404 from /live/health (the flag-off
 * answer) is gate 'off', and the slot renders nothing at all. */

export type { LiveGate };

export interface LiveData {
  gate: LiveGate;
  health: HealthDoc | null;
  snapshot: SnapshotDoc | null;
  ctxFeeds: ContextIndexFeed[] | null;
  /** True while the last health request failed (feeds then read DOWN). */
  healthError: HttpError | null;
}

export function useLiveData(enabled: boolean): LiveData {
  const [tick] = useVisibleTick(10000);
  const [slow] = useVisibleTick(60000);
  const health = useAsync<HealthDoc>((signal) => getJson<HealthDoc>('/live/health', { signal }), 'health', enabled, tick);
  const snapshot = useAsync<SnapshotDoc>((signal) => getJson<SnapshotDoc>(`/live/snapshot?${SNAPSHOT_COUNTS_QUERY}`, { signal }), 'snapshot', enabled && health.error?.status !== 404, tick);
  const feeds = useAsync<{ feeds: ContextIndexFeed[] }>((signal) => getJson<{ feeds: ContextIndexFeed[] }>('/feeds', { signal }), 'feeds', enabled && health.error?.status !== 404, slow);
  const gate = gateOf({ storeEnabled: enabled, healthStatus: health.error ? health.error.status : null, haveHealth: !!health.data });
  return { gate, health: health.data, snapshot: snapshot.data, ctxFeeds: feeds.data?.feeds ?? null, healthError: health.error };
}

/* ── tape: seed from the snapshot, then one SSE stream ──────────────── */

type Action = { t: 'msg'; msg: SseMessage; now: string } | { t: 'seed'; snap: Parameters<typeof seedFromSnapshot>[1] } | { t: 'conn'; conn: TapeState['conn'] } | { t: 'reset' };

function reducer(state: TapeState, a: Action): TapeState {
  switch (a.t) {
    case 'msg': return reduceMessage(state, a.msg, a.now);
    case 'seed': return seedFromSnapshot(state, a.snap);
    case 'conn': return state.conn === a.conn ? state : { ...state, conn: a.conn };
    case 'reset': return initialTape(state.max);
    default: return state;
  }
}

const TYPES = ['hello', 'events', 'health', 'heartbeat', 'reset', 'gap', 'bye'] as const;

export function useTape(enabled: boolean, paused: boolean) {
  const [state, dispatch] = useReducer(reducer, undefined, () => initialTape());
  const [seeded, setSeeded] = useState(false);
  const [hidden, setHidden] = useState(() => typeof document !== 'undefined' && document.hidden);
  const lastId = useRef<string | null>(null);
  lastId.current = state.lastEventId;
  const [resyncs, setResyncs] = useState(0);

  useEffect(() => {
    const on = () => setHidden(document.hidden);
    document.addEventListener('visibilitychange', on);
    return () => document.removeEventListener('visibilitychange', on);
  }, []);

  /* seed (first connect and every resync) */
  useEffect(() => {
    if (!enabled) { dispatch({ t: 'reset' }); setSeeded(false); return undefined; }
    const ctrl = new AbortController();
    getJson<Parameters<typeof seedFromSnapshot>[1]>('/live/snapshot?topics=events&entity_limit=0&events_limit=200', { signal: ctrl.signal })
      .then((snap) => { dispatch({ t: 'seed', snap }); setSeeded(true); })
      .catch(() => { if (!ctrl.signal.aborted) setSeeded(true); }); // no seed: the stream still opens, from its own start
    return () => ctrl.abort();
  }, [enabled, resyncs]);
  useEffect(() => { if (state.resync) setResyncs((n) => n + 1); }, [state.resync]);

  const connect = shouldConnect({ enabled: enabled && seeded, hidden, paused });
  useEffect(() => {
    if (!connect) { dispatch({ t: 'conn', conn: enabled ? 'paused' : 'idle' }); return undefined; }
    let es: EventSource | null = null;
    let timer: number | undefined;
    let attempt = 0;
    let stopped = false;
    const schedule = () => {
      if (stopped) return;
      dispatch({ t: 'conn', conn: 'reconnecting' });
      timer = window.setTimeout(open, reconnectDelayMs(attempt++, Math.random()));
    };
    const open = () => {
      if (stopped) return;
      dispatch({ t: 'conn', conn: 'connecting' });
      const src = new EventSource(`${API_BASE}${streamPath(lastId.current)}`);
      es = src;
      for (const type of TYPES) {
        src.addEventListener(type, (ev) => {
          const m = ev as MessageEvent<string>;
          if (type === 'hello') attempt = 0;
          dispatch({ t: 'msg', msg: { type, data: m.data, lastEventId: m.lastEventId || null }, now: new Date().toISOString() });
          if (type === 'bye') { src.close(); schedule(); }
        });
      }
      /* the browser's own retry is bypassed: a failed connect (a 503 at the
         client cap, a dropped proxy) is retried here with backoff and the
         resume point, so the behaviour is the same in every browser */
      src.onerror = () => { src.close(); schedule(); };
    };
    open();
    return () => { stopped = true; if (timer) window.clearTimeout(timer); es?.close(); };
  }, [connect, enabled]);

  return { state, hidden };
}
