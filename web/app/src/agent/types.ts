/**
 * AGENT (headline feature): wire types for GET/POST /agent/*, as served by
 * server/agent. Everything the server sends is
 * treated as data: it is rendered as text and never evaluated.
 */

/** LIVE = the http transport answered (a real model call); RECORDED = the scripted fixture. */
export type AgentTransport = 'LIVE' | 'RECORDED';

export type SessionStatus =
  | 'created' | 'running' | 'awaiting_confirmation' | 'awaiting_client'
  | 'idle' | 'stopped' | 'failed' | 'cancelled' | 'interrupted';

export type StepStatus = 'ok' | 'error' | 'pending_confirmation' | 'pending_client' | 'rejected' | 'cancelled';
export type StepConfirmation = 'not_needed' | 'pending' | 'confirmed' | 'rejected';

export interface AgentStep {
  id: string;
  seq: number;
  rev: number;
  ts: string;
  kind: 'model' | 'tool';
  name: string;
  tool: string | null;
  arguments: Record<string, unknown>;
  result_summary: string;
  status: StepStatus;
  confirmation: StepConfirmation;
  cost_usd: number;
  estimated_cost_usd: number;
  transport: AgentTransport;
  prompt_id: string | null;
  duration_ms: number;
  call_id?: string;
  action_id?: string;
  flags: string[];
  executed_in?: 'browser';
  tokens_in?: number;
  tokens_out?: number;
}

/** A pending confirmation (state-changing action) or a client action sent to the browser. */
export interface AgentAction {
  id: string;
  tool: string;
  arguments: Record<string, unknown>;
  summary: string;
  kind: 'action' | 'client_action' | 'read';
  can_spend: boolean;
  est_cost_usd: number;
  transport: AgentTransport;
  state: string;
  step_id: string;
  call_id: string;
}

export interface AgentMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  ts: string;
  transport?: AgentTransport;
  model?: string;
  system?: boolean;
}

export interface AgentUsage {
  model_turns: number;
  tool_calls: number;
  spent_usd: number;
  accrued_usd: number;
  wall_s: number;
  model_turns_left?: number;
}

export interface AgentLimits { max_steps: number; max_tool_calls: number; max_wall_s: number; max_spend_usd: number }

export interface SessionView {
  id: string;
  created_at: string;
  status: SessionStatus;
  stop_reason: string | null;
  error: string | null;
  transport: AgentTransport;
  model: string;
  prompt_id: string;
  limits: AgentLimits;
  usage: AgentUsage;
  messages: AgentMessage[];
  pending: AgentAction[];
  client_actions: AgentAction[];
  rev: number;
  steps_count: number;
}

export interface StepsDoc {
  session_id: string;
  status: SessionStatus;
  rev: number;
  transport: AgentTransport;
  steps: AgentStep[];
  pending: AgentAction[];
  client_actions: AgentAction[];
}

export interface AgentStatusDoc {
  enabled: boolean;
  limits: AgentLimits;
  tools: number;
  transport?: string | null;
  transport_label?: AgentTransport | null;
  model?: string;
  cap_usd?: number | null;
  cap_reached?: boolean;
  llm_disabled?: boolean;
  unavailable?: string;
  /** The demo link gate: when enabled and not unlocked, a new session is Recorded whatever the transport. */
  demo_gate?: { enabled?: boolean; unlocked?: boolean };
}

export interface DemoTasksDoc { tasks: string[]; research_tasks?: string[]; label: 'RECORDED' }

/** What the browser reports after performing (or failing) a client action. */
export interface ClientResult { ok: boolean; summary: string; code?: string }

/** Statuses after which the loop is parked for good (the user may start a new task). */
export const TERMINAL_STATUSES: readonly SessionStatus[] = ['idle', 'stopped', 'failed', 'cancelled', 'interrupted'];
export const isTerminal = (s: SessionStatus | 'none'): boolean => s !== 'none' && TERMINAL_STATUSES.includes(s);
/** A session that can take another message. */
export const isOpen = (s: SessionStatus | 'none'): boolean => s === 'created' || s === 'idle';
