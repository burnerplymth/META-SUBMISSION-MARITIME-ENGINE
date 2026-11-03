import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react';
import { api, ApiError, pollBackoffMs, SIM_RUN_POLL_INTERVAL_MS } from './api';
import type { PanelId } from './types';
import {
  HISTORY_MAX_KEY, SIM_INFLIGHT_KEY, SIM_INFLIGHT_MAX_AGE_MS,
  canBack, canForward, carriedFlags, initialHistoryPos, navKind, parseHistoryMax, parseSimInflight,
  parseUrlState, posAfterNav, posAfterPop, readHistoryIdx, resolveUrlLayout, serializeSimInflight,
  serializeUrlState, simInflightFresh, EMPTY_URL_STATE,
  type HistoryPos, type UrlState,
} from './urlState';

/* URL ↔ store wiring (the pure grammar is urlState.ts). Everything here
 * is guarded with typeof checks: location / history /
 * sessionStorage / window.addEventListener may be missing (a minimal
 * or non-browser DOM), and a missing
 * browser API must turn the feature off, never throw. */

const hasHistory = (): boolean =>
  typeof history !== 'undefined' && typeof history.pushState === 'function' && typeof location !== 'undefined';

/** The hash of this document as a UrlState (empty when there is none). */
export function readLocationUrlState(): UrlState {
  try {
    return typeof location !== 'undefined' && typeof location.hash === 'string'
      ? parseUrlState(location.hash) : { ...EMPTY_URL_STATE, flags: [] };
  } catch { return { ...EMPTY_URL_STATE, flags: [] }; }
}

function readStoredMax(): number | null {
  try { return typeof sessionStorage === 'undefined' ? null : parseHistoryMax(sessionStorage.getItem(HISTORY_MAX_KEY)); }
  catch { return null; }
}
function writeStoredMax(n: number): void {
  try { if (typeof sessionStorage !== 'undefined') sessionStorage.setItem(HISTORY_MAX_KEY, String(n)); } catch { /* blocked: ‹ › still work in-memory */ }
}

function writeHistory(kind: 'push' | 'replace', idx: number, hash: string): boolean {
  try {
    const url = location.pathname + location.search + hash;
    if (kind === 'push') history.pushState({ sm: idx }, '', url);
    else history.replaceState({ sm: idx }, '', url);
    return true;
  } catch { return false; /* sandboxed frame / throttled: the URL is a convenience */ }
}

/** '+': a real new browser tab on the given hash. Returns false when
 *  the environment has no window.open. */
export function openAppTab(hash: string): boolean {
  if (typeof window === 'undefined' || typeof window.open !== 'function' || typeof location === 'undefined') return false;
  window.open(location.pathname + location.search + hash, '_blank');
  return true;
}
/** '✕': close THIS browser tab. Only offered on a tab the app opened. */
export function closeAppTab(): void {
  if (typeof window !== 'undefined' && typeof window.close === 'function') window.close();
}

/* ── Applying a URL state (popstate / hashchange) ──────────────────── */

export interface UrlApplyDeps {
  layoutNames: readonly string[]; liveTwinName: string; liveTwinEnabled: boolean;
  /** The live values, read fresh at each step (the apply awaits). */
  cur: () => { mode?: 'live' | 'sim'; preset?: string | null; layout: string; focus: PanelId | null; place: string | null; run: string | null; sim: string | null; simTick: number; simActive: boolean };
  /** Optional: drive the shell mode without touching the layout. */
  setMode?: (m: 'live' | 'sim') => void;
  /** Optional: apply or clear a `preset=` window layout. */
  setPreset?: (code: string | null) => void;
  hasPlace: (id: string) => boolean;
  setLayout: (name: string) => void; setFocus: (p: PanelId | null) => void; selectNode: (id: string | null) => void;
  openChain: (runId: string) => Promise<boolean>;
  exitSim: () => void;
  /** Open a stored sim run (or follow it while it builds), then seek. */
  restoreSim: (id: string, tick: number | null) => Promise<void>;
  seekSim: (tick: number) => Promise<void>;
}

/** Drive the store to a parsed URL state. Order matters: leaving SIM
 *  first (it restores the desk chain), then the desk chain, then the
 *  place, then entering a sim run last (it snapshots the desk chain).
 *  `isCurrent` goes false when a newer navigation superseded this one. */
export async function applyUrlTarget(t: UrlState, d: UrlApplyDeps, isCurrent: () => boolean): Promise<void> {
  const layout = resolveUrlLayout(t.layout, d.layoutNames, d.liveTwinName, d.liveTwinEnabled);
  if (layout !== null && layout !== d.cur().layout) d.setLayout(layout);
  if (d.setPreset && t.preset !== (d.cur().preset ?? null)) d.setPreset(t.preset);
  if (t.focus !== d.cur().focus) d.setFocus(t.focus);
  /* Shell mode: an explicit mode wins; a sim run implies SIMULATION; no mode and
   * no run is LIVE (the grammar omits the default). setMode before exitSim so the
   * mode switch and the sim-slice exit are one step, not two. */
  if (d.setMode) {
    const want: 'live' | 'sim' = t.mode ?? (t.sim !== null ? 'sim' : 'live');
    const have = d.cur().mode;
    if (have !== undefined && want !== have && !(want === 'sim' && t.sim !== null)) d.setMode(want);
  }
  if (t.sim === null && d.cur().simActive) d.exitSim();
  if (t.run && t.run !== d.cur().run) {
    await d.openChain(t.run);
    if (!isCurrent()) return;
  }
  if (t.place === null) { if (d.cur().place !== null) d.selectNode(null); }
  else if (t.place !== d.cur().place && d.hasPlace(t.place)) d.selectNode(t.place);
  if (t.sim !== null) {
    if (d.cur().sim !== t.sim) { await d.restoreSim(t.sim, t.tick); return; }
    if (t.tick !== null && t.tick !== d.cur().simTick) await d.seekSim(t.tick);
  }
}

/* ── The history writer + popstate reader ──────────────────────────── */

export interface UrlSyncInput {
  /** False during boot restore: nothing is written until the URL has been read and applied. */
  ready: boolean;
  /** The state to publish (flags are derived here). */
  current: UrlState;
  /** The theme is light: mirrored as the #light flag. */
  light: boolean;
  /** True during the AUTO DEMO: no history writes, popstate ignored. */
  suspended: MutableRefObject<boolean>;
  apply: (target: UrlState, isCurrent: () => boolean) => Promise<void>;
}

export function useUrlSync(p: UrlSyncInput) {
  const [pos, setPosState] = useState<HistoryPos>({ idx: 0, max: 0 });
  const posRef = useRef(pos);
  const setPos = useCallback((n: HistoryPos) => { posRef.current = n; setPosState(n); writeStoredMax(n.max); }, []);
  /** >0 while an async popstate apply is in flight: the writer is
   *  suspended so a late load cannot rewrite (and truncate) forward history. */
  const applying = useRef(0);
  const applySeq = useRef(0);
  /** The next write is a replace even if navKind says push (storage
   *  sync, post-apply resync, post-demo resync). */
  const forceReplace = useRef(false);
  const started = useRef(false);
  const lastHash = useRef('');
  const [bump, setBump] = useState(0);
  const latest = useRef(p); latest.current = p;

  const isChild = readLocationUrlState().flags.includes('child');
  const next: UrlState = { ...p.current, flags: carriedFlags(readLocationUrlState().flags, p.light) };
  const hash = serializeUrlState(next);

  useEffect(() => {
    if (!p.ready || !hasHistory() || applying.current > 0 || p.suspended.current) return;
    if (!started.current) {
      // First write after the restore: adopt this entry (replace) and read
      // the tab's position from the entry's own state (a reload keeps it).
      started.current = true;
      setPos(initialHistoryPos(history.state, readStoredMax()));
      writeHistory('replace', posRef.current.idx, hash);
      lastHash.current = hash;
      return;
    }
    let kind = navKind(readLocationUrlState(), next);
    if (forceReplace.current) { forceReplace.current = false; if (kind === 'push') kind = 'replace'; }
    if (kind === 'none') return;
    const nextPos = posAfterNav(posRef.current, kind);
    if (writeHistory(kind, nextPos.idx, hash)) { setPos(nextPos); lastHash.current = hash; }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.ready, hash, bump]);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.addEventListener !== 'function' || !hasHistory()) return;
    const run = async (state: unknown) => {
      if (!latest.current.ready || latest.current.suspended.current) return;
      setPos(posAfterPop(posRef.current, state));
      if (readHistoryIdx(state) === null) writeHistory('replace', posRef.current.idx, location.hash);
      const target = readLocationUrlState();
      lastHash.current = serializeUrlState(target);
      const seq = ++applySeq.current;
      applying.current += 1;
      try { await latest.current.apply(target, () => applySeq.current === seq); }
      catch { /* each step reports its own failure */ }
      finally {
        applying.current -= 1;
        // Whatever the apply could not reach, the URL now states what is
        // on screen: a replace, so forward history is never truncated.
        if (applySeq.current === seq) { forceReplace.current = true; setBump((b) => b + 1); }
      }
    };
    const onPop = (ev: PopStateEvent) => { void run(ev.state); };
    // popstate also fires for a fragment navigation; hashchange is only the
    // fallback for an environment that fires it alone.
    const onHash = () => { if (serializeUrlState(readLocationUrlState()) !== lastHash.current) void run(history.state); };
    window.addEventListener('popstate', onPop);
    window.addEventListener('hashchange', onHash);
    return () => { window.removeEventListener('popstate', onPop); window.removeEventListener('hashchange', onHash); };
  }, [setPos]);

  const back = useCallback(() => { if (hasHistory() && canBack(posRef.current)) history.back(); }, []);
  const forward = useCallback(() => { if (hasHistory() && canForward(posRef.current)) history.forward(); }, []);
  const replaceNext = useCallback(() => { forceReplace.current = true; }, []);
  const resync = useCallback(() => { forceReplace.current = true; setBump((b) => b + 1); }, []);
  return { pos, isChild, back, forward, replaceNext, resync };
}

/* ── In-flight sim run: follow a build another tab started ─────────── */

export interface SimInflightState { simRunId: string; state: 'building' | 'ready' | 'failed'; since: number }
export interface KV { get: (k: string) => string | null; set: (k: string, v: string) => void; remove: (k: string) => void }

export interface SimInflightInput {
  storage: KV;
  /** False while this tab has a sim loaded or is creating one itself. */
  canFollow: () => boolean;
  onReady: (simRunId: string, autoOpen: boolean) => void;
}

export function useSimInflight(o: SimInflightInput) {
  const [inflight, setInflight] = useState<SimInflightState | null>(null);
  const token = useRef(0);
  /** Wakes the poll loop's sleep: a storage event is delivered even to a
   *  background tab whose timers are throttled to once a minute. */
  const wake = useRef<(() => void) | null>(null);
  const cur = useRef<SimInflightState | null>(null);
  const put = useCallback((v: SimInflightState | null) => { cur.current = v; setInflight(v); }, []);
  const latest = useRef(o); latest.current = o;

  /** Poll GET /sim/run/{id}/status until the build settles. */
  const follow = useCallback((simRunId: string, since: number, autoOpen: boolean) => {
    const t = ++token.current;
    // Re-following the same build (the URL names it too) keeps its original start.
    if (cur.current?.simRunId === simRunId) since = cur.current.since;
    put({ simRunId, state: 'building', since });
    void (async () => {
      let failures = 0;
      for (;;) {
        try {
          const st = await api.simRunStatus(simRunId);
          if (token.current !== t) return;
          if (st.status === 'ready') { put({ simRunId, state: 'ready', since }); latest.current.onReady(simRunId, autoOpen); return; }
          if (st.status === 'failed') { put({ simRunId, state: 'failed', since }); return; }
          failures = 0;
        } catch (e) {
          if (token.current !== t) return;
          // A marker the server has never heard of (restarted, or a stale
          // write): drop it silently — nothing to follow.
          if (e instanceof ApiError && e.code === 'unknown_sim_run') {
            put(null);
            if (parseSimInflight(latest.current.storage.get(SIM_INFLIGHT_KEY))?.simRunId === simRunId) latest.current.storage.remove(SIM_INFLIGHT_KEY);
            return;
          }
          failures += 1;
        }
        if (Date.now() - since > SIM_INFLIGHT_MAX_AGE_MS || failures >= 5) { put(null); return; }
        await new Promise<void>((r) => { wake.current = r; setTimeout(r, pollBackoffMs(failures, SIM_RUN_POLL_INTERVAL_MS)); });
        if (token.current !== t) return;
      }
    })();
  }, [put]);

  /** This tab started the build (202): publish it for other tabs. */
  const begin = useCallback((simRunId: string, since: number) => {
    token.current += 1;
    latest.current.storage.set(SIM_INFLIGHT_KEY, serializeSimInflight({ simRunId, since }));
    put({ simRunId, state: 'building', since });
  }, [put]);
  /** The build settled in this tab (loaded or failed): retract it. */
  const end = useCallback((simRunId: string) => {
    if (parseSimInflight(latest.current.storage.get(SIM_INFLIGHT_KEY))?.simRunId === simRunId) latest.current.storage.remove(SIM_INFLIGHT_KEY);
    if (cur.current?.simRunId === simRunId && cur.current.state === 'building') { token.current += 1; put(null); }
  }, [put]);
  const dismiss = useCallback(() => { token.current += 1; put(null); }, [put]);

  /** Read the marker (boot / storage event); follow it when fresh. */
  const check = useCallback(() => {
    wake.current?.(); // the other tab's marker changed (a build settled): re-poll now
    const m = parseSimInflight(latest.current.storage.get(SIM_INFLIGHT_KEY));
    if (!m || !simInflightFresh(m, Date.now())) return;
    if (cur.current?.simRunId === m.simRunId || !latest.current.canFollow()) return;
    follow(m.simRunId, m.since, false);
  }, [follow]);

  useEffect(() => () => { token.current += 1; }, []);
  return { inflight, begin, end, follow, dismiss, check };
}

/* ── Cross-tab storage events ──────────────────────────────────────── */

export function useStorageEvents(handler: (e: { key: string | null; newValue: string | null }) => void) {
  const latest = useRef(handler); latest.current = handler;
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') return;
    const on = (e: StorageEvent) => { latest.current({ key: e.key, newValue: e.newValue }); };
    window.addEventListener('storage', on);
    return () => window.removeEventListener('storage', on);
  }, []);
}
