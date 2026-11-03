/**
 * CHAT LABELS: every status, stop reason and error becomes a short state word.
 * No sentences (copy style: chips and state labels only).
 */
import type { StateKind } from '../ui';
import { ERROR_LABEL, STOP_LABEL, errorLabel as agentErrorLabel } from '../agent/timeline';
import type { ThreadView, WebState } from './types';

const CHAT_STOP: Readonly<Record<string, string>> = {
  ...STOP_LABEL,
  web_search_cap: 'Web limit reached', client_spend_cap: 'Daily limit reached',
  daily_cap: 'Daily limit reached', max_steps: 'Step limit reached', max_tool_calls: 'Tool limit reached',
  max_wall_time: 'Time limit reached', max_spend: 'Chat spend limit',
};
const CHAT_ERROR: Readonly<Record<string, string>> = {
  ...ERROR_LABEL,
  thread_busy: 'Busy', thread_spend_cap: 'Chat spend limit', client_spend_cap: 'Daily limit reached',
  thread_limit: 'Chat limit reached', thread_cap: 'Live chats used today', demo_locked: 'Demo link needed',
  unknown_thread: 'Chat gone', message_cap: 'Message limit reached', invalid_request: 'Request refused',
};

export const errorWord = (code: string): string => CHAT_ERROR[code] ?? agentErrorLabel(code);

export interface ThreadLabel { word: string; kind: StateKind }

/** The one state label of the open chat. */
export function threadLabel(t: ThreadView | null, working: boolean): ThreadLabel {
  if (!t) return { word: 'Ready', kind: 'meta' };
  switch (t.status) {
    case 'running': return { word: 'Working', kind: 'live' };
    case 'awaiting_confirmation': return { word: 'Needs confirmation', kind: 'snapshot' };
    case 'awaiting_client': return { word: 'Updating view', kind: 'snapshot' };
    default: break;
  }
  if (working) return { word: 'Working', kind: 'live' };
  const lt = t.last_turn;
  if (lt && lt.status === 'cancelled') return { word: 'Stopped by you', kind: 'notserved' };
  if (lt && lt.status === 'stopped') return { word: CHAT_STOP[lt.reason ?? ''] ?? 'Stopped', kind: 'notserved' };
  if (lt && lt.status === 'failed') return { word: CHAT_STOP[lt.reason ?? ''] ?? 'Failed', kind: 'down' };
  if (lt && lt.status === 'interrupted') return { word: 'Server restarted', kind: 'notserved' };
  return t.messages.length ? { word: 'Done', kind: 'live' } : { word: 'Ready', kind: 'meta' };
}

export function webLabel(state: WebState | 'none'): { word: string; kind: StateKind } {
  switch (state) {
    case 'on': return { word: 'Live', kind: 'live' };
    case 'recorded': return { word: 'Recorded', kind: 'snapshot' };
    case 'daily_limit': return { word: 'Daily limit reached', kind: 'notserved' };
    default: return { word: 'Off', kind: 'meta' };
  }
}

/** "5 Oct" style clock for a message: UTC, no locale surprises. */
export function clockOf(ts: string): string {
  return /^\d{4}-\d\d-\d\dT\d\d:\d\d/.test(ts) ? `${ts.slice(11, 16)} UTC` : '';
}

export function dayOf(ts: string): string {
  return /^\d{4}-\d\d-\d\d/.test(ts) ? ts.slice(0, 10) : '';
}

export function money(v: number): string {
  if (v === 0) return '$0.00';
  if (v >= 1) return `$${v.toFixed(2)}`;
  if (v >= 0.0001) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(6)}`;
}
