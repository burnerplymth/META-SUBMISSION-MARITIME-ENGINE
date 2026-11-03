/**
 * AGENT STATE: a pure reducer over what the server tells us. The server is
 * the source of truth for status, steps, pending confirmations and caps; the
 * reducer only merges (steps by id, newest rev wins), never invents. Nothing
 * here renders prose: every status, stop reason and error becomes a short
 * state label (labels.ts).
 */
import type {
  AgentAction, AgentLimits, AgentMessage, AgentStep, AgentTransport, AgentUsage, SessionStatus, SessionView, StepsDoc,
} from './types';

export interface AgentState {
  sessionId: string | null;
  status: SessionStatus | 'none';
  transport: AgentTransport | null;
  model: string | null;
  /** Highest step revision applied; the next long poll asks for `after_rev=rev`. */
  rev: number;
  steps: AgentStep[];
  pending: AgentAction[];
  clientActions: AgentAction[];
  messages: AgentMessage[];
  usage: AgentUsage | null;
  limits: AgentLimits | null;
  stopReason: string | null;
  /** Machine code of the last failure (server error, unreachable); null when fine. */
  error: string | null;
  /** True while a request of ours is in flight (create, send, confirm...). */
  busy: boolean;
}

export const INITIAL_AGENT_STATE: AgentState = {
  sessionId: null, status: 'none', transport: null, model: null, rev: 0, steps: [], pending: [], clientActions: [],
  messages: [], usage: null, limits: null, stopReason: null, error: null, busy: false,
};

export type AgentAct =
  | { type: 'reset' }
  | { type: 'request'; on: boolean }
  | { type: 'session'; session: SessionView }
  | { type: 'steps'; doc: StepsDoc }
  | { type: 'error'; code: string | null };

/** Merge changed steps into the ordered list: same id keeps the higher rev. */
export function mergeSteps(prev: readonly AgentStep[], incoming: readonly AgentStep[]): AgentStep[] {
  if (!incoming.length) return prev as AgentStep[];
  const byId = new Map(prev.map((s) => [s.id, s] as const));
  for (const s of incoming) {
    const cur = byId.get(s.id);
    if (!cur || s.rev >= cur.rev) byId.set(s.id, s);
  }
  return [...byId.values()].sort((a, b) => a.seq - b.seq);
}

const maxRev = (steps: readonly AgentStep[], base: number) => steps.reduce((m, s) => (s.rev > m ? s.rev : m), base);

export function agentReducer(state: AgentState, act: AgentAct): AgentState {
  switch (act.type) {
    case 'reset': return INITIAL_AGENT_STATE;
    case 'request': return state.busy === act.on ? state : { ...state, busy: act.on };
    case 'error': return { ...state, error: act.code };
    case 'session': {
      const v = act.session;
      if (state.sessionId !== null && state.sessionId !== v.id) return state;   // a stale answer for an older task
      return {
        ...state, sessionId: v.id, status: v.status, transport: v.transport, model: v.model, usage: v.usage, limits: v.limits,
        messages: v.messages, pending: v.pending, clientActions: v.client_actions ?? [], stopReason: v.stop_reason,
        error: v.status === 'failed' ? v.stop_reason ?? 'failed' : null,
      };
    }
    case 'steps': {
      const d = act.doc;
      if (state.sessionId !== null && state.sessionId !== d.session_id) return state;
      const steps = mergeSteps(state.steps, d.steps);
      return {
        ...state, sessionId: d.session_id, status: d.status, transport: d.transport, steps,
        rev: Math.max(state.rev, d.rev, maxRev(d.steps, 0)), pending: d.pending, clientActions: d.client_actions ?? [], error: null,
      };
    }
  }
}

/* ── selectors ───────────────────────────────────────────────────────────── */

/** Working: the loop is running, parked for a confirmation, or waiting for the browser. */
export const isWorking = (s: AgentState): boolean =>
  s.status === 'running' || s.status === 'awaiting_confirmation' || s.status === 'awaiting_client';

/** The final answer: the last assistant message after the last user message, if any. */
export function finalAnswer(s: AgentState): AgentMessage | null {
  let lastUser = -1;
  s.messages.forEach((m, i) => { if (m.role === 'user') lastUser = i; });
  for (let i = s.messages.length - 1; i > lastUser; i -= 1) if (s.messages[i].role === 'assistant') return s.messages[i];
  return null;
}

/** A client action the browser has not performed yet (its id is not in `handled`). */
export function unhandledClientActions(s: AgentState, handled: ReadonlySet<string>): AgentAction[] {
  return s.clientActions.filter((a) => a.state === 'dispatched' && !handled.has(a.id));
}

/** Caps as chips: used / limit for turns, tool calls, working seconds, spend. */
export interface CapChip { key: 'steps' | 'tools' | 'time' | 'spend'; label: string; used: number; limit: number; text: string; hot: boolean }
export function capChips(s: AgentState): CapChip[] {
  if (!s.limits || !s.usage) return [];
  const u = s.usage, l = s.limits;
  const mk = (key: CapChip['key'], label: string, used: number, limit: number, text: string): CapChip =>
    ({ key, label, used, limit, text, hot: limit > 0 && used / limit >= 0.8 });
  // Spend shown is what was charged (LIVE); a RECORDED session charges nothing, so it shows no accrual.
  const spent = s.transport === 'LIVE' ? u.spent_usd : 0;
  return [
    mk('steps', 'Steps', u.model_turns, l.max_steps, `${u.model_turns}/${l.max_steps}`),
    mk('tools', 'Tools', u.tool_calls, l.max_tool_calls, `${u.tool_calls}/${l.max_tool_calls}`),
    mk('time', 'Time', u.wall_s, l.max_wall_s, `${Math.round(u.wall_s)}/${Math.round(l.max_wall_s)} s`),
    mk('spend', 'Spend', spent, l.max_spend_usd, `${fmtMoney(spent)}/${fmtMoney(l.max_spend_usd)}`),
  ];
}

export function fmtMoney(v: number): string {
  if (v === 0) return '$0.00';
  if (v >= 1) return `$${v.toFixed(2)}`;
  if (v >= 0.0001) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(6)}`;
}
