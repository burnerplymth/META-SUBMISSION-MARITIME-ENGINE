/**
 * AGENT TIMELINE: pure view models. A step becomes a row of facts (tool,
 * arguments, result, cost, duration, LIVE or RECORDED) and every status,
 * stop reason or error code becomes a short state label. No sentences.
 */
import type { StateKind } from '../ui';
import { humaniseAgentText } from '../format';
import type { AgentAction, AgentStep, AgentTransport, SessionStatus } from './types';

/** Words for the tools, in the app's vocabulary (places, legs, causal chain). */
export const TOOL_LABEL: Readonly<Record<string, string>> = {
  search_places: 'Search places', get_place: 'Read place', list_scenarios: 'List scenarios', list_runs: 'List runs',
  run_summary: 'Summarise run', compare_runs: 'Compare runs', verify_run: 'Verify run', check_shock: 'Check shock',
  lane_plan: 'Plan lane', shipment_exposure: 'Shipment exposure', port_scenario: 'Port scenario', list_reports: 'List reports', explain_chain: 'Explain chain',
  start_run: 'Start run', apply_shock: 'Apply shock', start_sim_run: 'Start simulation', build_report: 'Build report',
  export_pack: 'Export pack',
  open_window: 'Open window', arrange_window: 'Arrange window', apply_layout: 'Apply layout', set_mode: 'Switch mode',
  focus_place: 'Focus place', show_run: 'Show run',
  research_catalog: 'Read catalogue', research_episodes: 'Read episodes', research_assemble: 'Assemble data', research_analyze: 'Run analysis',
  research_export_link: 'Export link', research_scene_scan: 'Scan scenes', write_research_note: 'Write report', open_research: 'Open Research',
};
export const toolLabel = (tool: string | null | undefined): string =>
  !tool ? '' : TOOL_LABEL[tool] ?? (tool.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()));

/** Tools that run in the browser and only change what is on screen. */
export const UI_TOOLS: readonly string[] = ['open_window', 'arrange_window', 'apply_layout', 'set_mode', 'focus_place', 'show_run', 'open_research'];
export const isUiTool = (tool: string | null | undefined): boolean => !!tool && UI_TOOLS.includes(tool);

const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

function valueText(v: unknown): string {
  if (typeof v === 'string') return clip(v, 28);
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  if (Array.isArray(v)) return clip(v.map(valueText).join(','), 28);
  if (v && typeof v === 'object') return clip(Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k}:${valueText(x)}`).join(','), 28);
  return '';
}

/** `key value` pairs, never a raw JSON dump; long values are clipped. */
export function argsSummary(args: Record<string, unknown> | null | undefined, max = 90): string {
  if (!args || typeof args !== 'object') return '';
  const calls = (args as { tool_calls?: unknown }).tool_calls;
  if (Array.isArray(calls)) return calls.length ? clip(calls.map((c) => toolLabel(String(c))).join(', '), max) : '';
  const parts = Object.entries(args).map(([k, v]) => `${k.replace(/_id$/, '').replace(/_/g, ' ')} ${valueText(v)}`);
  return clip(parts.join(' · '), max);
}

export function fmtDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '–';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
}

export function fmtCost(usd: number): string {
  if (usd === 0) return '$0.00';
  if (usd >= 1) return `$${usd.toFixed(2)}`;
  if (usd >= 0.0001) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(6)}`;
}

export const MODE_WORD: Record<AgentTransport, string> = { LIVE: 'Live', RECORDED: 'Recorded' };

export type RowTone = 'ok' | 'error' | 'wait' | 'muted';
export interface StepRow {
  id: string;
  seq: number;
  kind: 'model' | 'tool';
  /** Model steps show the model name; tool steps the tool word. */
  title: string;
  args: string;
  result: string;
  /** Settled cost; null = not shown (RECORDED never shows a cost). */
  cost: string | null;
  duration: string;
  mode: AgentTransport;
  /** Where it ran: the browser for UI control, else the server. */
  where: 'browser' | 'server';
  state: { word: string; tone: RowTone; kind: StateKind };
  flags: string[];
  /** The step is parked for the user (confirmation) or the browser. */
  waiting: boolean;
}

export function stepState(s: Pick<AgentStep, 'status' | 'confirmation'>): StepRow['state'] {
  switch (s.status) {
    case 'ok': return { word: 'Done', tone: 'ok', kind: 'meta' };
    case 'error': return { word: 'Failed', tone: 'error', kind: 'down' };
    case 'pending_confirmation': return { word: 'Needs confirmation', tone: 'wait', kind: 'snapshot' };
    case 'pending_client': return { word: 'In browser', tone: 'wait', kind: 'snapshot' };
    case 'rejected': return { word: 'Declined', tone: 'muted', kind: 'notserved' };
    case 'cancelled': return { word: 'Stopped', tone: 'muted', kind: 'notserved' };
  }
}

export function stepRow(s: AgentStep): StepRow {
  const live = s.transport === 'LIVE';
  const cost = live && s.cost_usd > 0 ? fmtCost(s.cost_usd) : live && s.kind === 'model' ? fmtCost(0) : null;
  return {
    id: s.id, seq: s.seq, kind: s.kind,
    title: s.kind === 'model' ? (live ? s.name || 'Meta AI' : 'Meta AI') : toolLabel(s.tool ?? s.name),
    args: humaniseAgentText(argsSummary(s.arguments)),
    result: humaniseAgentText(s.result_summary),
    cost, duration: fmtDuration(s.duration_ms), mode: s.transport,
    where: s.executed_in === 'browser' || isUiTool(s.tool) ? 'browser' : 'server',
    state: stepState(s), flags: s.flags ?? [],
    waiting: s.status === 'pending_confirmation' || s.status === 'pending_client',
  };
}

/* ── state labels ───────────────────────────────────────────────────────── */

export const STOP_LABEL: Readonly<Record<string, string>> = {
  max_steps: 'Step cap reached', max_tool_calls: 'Tool cap reached', max_wall_time: 'Time cap reached', max_spend: 'Spend cap reached',
  daily_cap: 'Daily cap reached', llm_disabled: 'Meta AI off', spend_store_unreadable: 'Spend store down',
  too_many_tool_errors: 'Too many errors', model_error: 'Model error', internal_error: 'Internal error',
  cancelled_by_user: 'Stopped by you', server_restarted: 'Server restarted',
};

export const ERROR_LABEL: Readonly<Record<string, string>> = {
  unreachable: 'Not served', timeout: 'Timed out', agent_disabled: 'Meta AI off', unknown_session: 'Session gone',
  session_busy: 'Busy', session_closed: 'Session closed', message_cap: 'Message cap reached', rate_limited: 'Rate limited',
  bad_response: 'Bad reply',
};
export const errorLabel = (code: string): string =>
  ERROR_LABEL[code] ?? STOP_LABEL[code] ?? (code.startsWith('http_') ? `Error ${code.slice(5)}` : code.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()));

export interface StateLabel { word: string; kind: StateKind; tone: RowTone }
/** The session's one state label. */
export function statusLabel(status: SessionStatus | 'none', stopReason: string | null): StateLabel {
  switch (status) {
    case 'none': return { word: 'Ready', kind: 'meta', tone: 'muted' };
    case 'created': return { word: 'Ready', kind: 'meta', tone: 'muted' };
    case 'running': return { word: 'Working', kind: 'meta', tone: 'wait' };
    case 'awaiting_confirmation': return { word: 'Needs confirmation', kind: 'snapshot', tone: 'wait' };
    case 'awaiting_client': return { word: 'Updating view', kind: 'snapshot', tone: 'wait' };
    case 'idle': return { word: 'Done', kind: 'meta', tone: 'ok' };
    case 'stopped': return { word: STOP_LABEL[stopReason ?? ''] ?? 'Stopped', kind: 'notserved', tone: 'error' };
    case 'failed': return { word: stopReason ? STOP_LABEL[stopReason] ?? errorLabel(stopReason) : 'Failed', kind: 'down', tone: 'error' };
    case 'cancelled': return { word: 'Stopped by you', kind: 'notserved', tone: 'muted' };
    case 'interrupted': return { word: STOP_LABEL[stopReason ?? ''] ?? 'Interrupted', kind: 'notserved', tone: 'error' };
  }
}

/** One pending-confirmation card. */
export interface ConfirmCard {
  id: string;
  tool: string;
  title: string;
  /** The server's own one-line description of what will happen. */
  summary: string;
  args: string;
  mode: AgentTransport;
  /** "Up to $0.0040": only when the action can spend (LIVE explain). */
  estimate: string | null;
}
export function confirmCard(a: AgentAction): ConfirmCard {
  return {
    id: a.id, tool: a.tool, title: toolLabel(a.tool), summary: humaniseAgentText(a.summary), args: humaniseAgentText(argsSummary(a.arguments, 120)), mode: a.transport,
    estimate: a.transport === 'LIVE' && a.can_spend && a.est_cost_usd > 0 ? fmtCost(a.est_cost_usd) : null,
  };
}
