/**
 * ONE live read of GET /llm/status for every Meta AI surface: the
 * status-bar indicator, the window badge and the activity window share it,
 * so the model transport is never worded two ways on one screen. Refreshed
 * every 30 s and right after any model call this page makes. Unreachable is
 * its own state (`Unavailable`), never a fake zero.
 */
import { useEffect, useSyncExternalStore } from 'react';
import { api } from '../api';
import { useDeskStore } from '../store';
import { llmStatusView, type LlmStatusDoc, type StatusSource, type StatusChipView } from './statusView';

export const META_CALL_EVENT = 'supermap:meta-call';
/** Surfaces call this after a model call so the status read is fresh. */
export const noteMetaCall = (): void => {
  try { window.dispatchEvent(new Event(META_CALL_EVENT)); } catch { /* no window */ }
};

interface Snapshot { doc: LlmStatusDoc | null; source: StatusSource }
let snap: Snapshot | null = null;
const listeners = new Set<() => void>();
const set = (s: Snapshot) => { snap = s; for (const l of listeners) l(); };

function refresh(): void {
  api.llmStatus().then(async (doc) => {
    const sam = await api.samUsage().then((u) => (typeof u?.calls_today === 'number' ? u.calls_today : undefined)).catch(() => undefined);
    set({ doc: { ...(doc as LlmStatusDoc), ...(sam === undefined ? {} : { sam_calls_today: sam }) }, source: 'live' });
  })
    .catch(() => set({ doc: null, source: 'unreachable' }));
}

let users = 0;
let timer: ReturnType<typeof setInterval> | null = null;
const subscribe = (fn: () => void): (() => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; };
const get = (): Snapshot | null => snap;

/** The latest status read; until the first answer it follows the store's boot read. */
export function useAiStatus(): { doc: LlmStatusDoc | null; source: StatusSource; view: StatusChipView | null } {
  const s = useDeskStore();
  const fresh = useSyncExternalStore(subscribe, get, get);
  useEffect(() => {
    users += 1;
    if (users === 1) {
      refresh();
      timer = setInterval(refresh, 30_000);
      window.addEventListener(META_CALL_EVENT, refresh);
    }
    return () => {
      users -= 1;
      if (users === 0) {
        if (timer) clearInterval(timer);
        timer = null;
        window.removeEventListener(META_CALL_EVENT, refresh);
      }
    };
  }, []);
  const doc = fresh ? fresh.doc : (s.llmStatus as LlmStatusDoc | null);
  const source: StatusSource = fresh ? fresh.source : s.llmStatusSource;
  return { doc, source, view: llmStatusView(doc, source) };
}
