/**
 * CHAT STATE: a pure reducer over what the server tells us. The server is the
 * source of truth for threads, messages, steps, pending confirmations and
 * usage; the reducer only merges (steps by id, newest rev wins) and never
 * invents anything.
 */
import { mergeSteps } from '../agent/reducer';
import { isWorking, type ChatMessage, type ChatStep, type ThreadStepsDoc, type ThreadSummary, type ThreadView } from './types';

export interface ChatState {
  threads: ThreadSummary[];
  listLoaded: boolean;
  /** Machine code when the list could not be read. */
  listError: string | null;
  /** The open thread; null = a draft (nothing is created until the first send). */
  activeId: string | null;
  thread: ThreadView | null;
  /** Steps of the open thread (live tool cards), merged by id. */
  steps: ChatStep[];
  rev: number;
  busy: boolean;
  error: string | null;
  /** Web search choice for a draft; null = the server default for the mode. */
  draftWeb: boolean | null;
  /** Context chips the person removed (keys), see contextSources.ts. */
  dismissed: string[];
}

export const INITIAL_CHAT_STATE: ChatState = {
  threads: [], listLoaded: false, listError: null, activeId: null, thread: null, steps: [], rev: 0,
  busy: false, error: null, draftWeb: null, dismissed: [],
};

export type ChatAct =
  | { type: 'list'; threads: ThreadSummary[] }
  | { type: 'list_error'; code: string }
  | { type: 'draft' }
  | { type: 'open'; thread: ThreadView }
  | { type: 'view'; thread: ThreadView }
  | { type: 'steps'; doc: ThreadStepsDoc }
  | { type: 'removed'; id: string }
  | { type: 'request'; on: boolean }
  | { type: 'error'; code: string | null }
  | { type: 'draft_web'; on: boolean | null }
  | { type: 'dismissed'; keys: string[] };

export function summaryOf(v: ThreadView): ThreadSummary {
  const lastA = [...v.messages].reverse().find((m) => m.role === 'assistant' && !m.system);
  return {
    id: v.id, title: v.title, created_at: v.created_at, updated_at: v.updated_at, status: v.status,
    transport: v.transport, messages: v.messages.length, preview: (lastA?.text ?? '').replace(/\s?\[\d{1,2}\]/g, '').slice(0, 140),
    web_search: v.web_search, spent_usd: v.cost_usd,
  };
}

function upsert(list: readonly ThreadSummary[], s: ThreadSummary): ThreadSummary[] {
  const rest = list.filter((x) => x.id !== s.id);
  return [s, ...rest].sort((a, b) => (a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0));
}

export function chatReducer(state: ChatState, act: ChatAct): ChatState {
  switch (act.type) {
    case 'list': return { ...state, threads: act.threads, listLoaded: true, listError: null };
    case 'list_error': return { ...state, listLoaded: true, listError: act.code };
    case 'draft': return { ...state, activeId: null, thread: null, steps: [], rev: 0, error: null, draftWeb: null };
    case 'open':
      return { ...state, activeId: act.thread.id, thread: act.thread, steps: [], rev: 0, error: null,
        threads: upsert(state.threads, summaryOf(act.thread)) };
    case 'view': {
      const v = act.thread;
      if (state.activeId !== null && state.activeId !== v.id) return state;     // a stale answer for another chat
      return { ...state, activeId: v.id, thread: v, threads: upsert(state.threads, summaryOf(v)),
        error: null };
    }
    case 'steps': {
      const d = act.doc;
      if (state.activeId === null || state.activeId !== d.session_id || !state.thread) return state;
      const steps = mergeSteps(state.steps, d.steps) as ChatStep[];
      const top = steps.reduce((m, s) => (s.rev > m ? s.rev : m), 0);
      return { ...state, steps, rev: Math.max(state.rev, d.rev, top), error: null,
        thread: { ...state.thread, status: d.status, pending: d.pending, client_actions: d.client_actions ?? [] } };
    }
    case 'removed': {
      const threads = state.threads.filter((t) => t.id !== act.id);
      return state.activeId === act.id
        ? { ...state, threads, activeId: null, thread: null, steps: [], rev: 0 }
        : { ...state, threads };
    }
    case 'request': return state.busy === act.on ? state : { ...state, busy: act.on };
    case 'error': return { ...state, error: act.code };
    case 'draft_web': return { ...state, draftWeb: act.on };
    case 'dismissed': return { ...state, dismissed: act.keys };
  }
}

/* ── selectors ───────────────────────────────────────────────────────────── */

export const statusOf = (s: ChatState): ThreadView['status'] | 'none' => s.thread?.status ?? 'none';
export const working = (s: ChatState): boolean => isWorking(statusOf(s));

/** Tool steps of the turn in progress (the live cards); empty when idle. */
export function liveSteps(s: ChatState): ChatStep[] {
  if (!s.thread || !working(s)) return [];
  return s.steps.filter((x) => x.kind === 'tool' && x.uturn === s.thread!.turn);
}

export function lastAssistant(s: ChatState): ChatMessage | null {
  const ms = s.thread?.messages ?? [];
  for (let i = ms.length - 1; i >= 0; i -= 1) if (ms[i].role === 'assistant') return ms[i];
  return null;
}

/** Rail filter: title and preview, case-insensitive. */
export function filterThreads(list: readonly ThreadSummary[], q: string): ThreadSummary[] {
  const n = q.trim().toLowerCase();
  if (!n) return list as ThreadSummary[];
  return list.filter((t) => t.title.toLowerCase().includes(n) || t.preview.toLowerCase().includes(n));
}

export interface TurnTotals { cost: number; searches: number; tools: number }
export function totals(v: ThreadView | null): TurnTotals {
  const t = { cost: v?.cost_usd ?? 0, searches: v?.web?.searches_thread ?? 0, tools: 0 };
  for (const m of v?.messages ?? []) t.tools += m.tool_steps?.length ?? 0;
  return t;
}
