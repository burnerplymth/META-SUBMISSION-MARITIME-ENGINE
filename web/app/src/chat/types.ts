/**
 * CHAT: wire types for /agent/threads (server/agent/threads.py). Everything the
 * server sends is data: it is rendered as text nodes and never evaluated.
 */
import type { AgentAction, AgentLimits, AgentStep, AgentTransport, AgentUsage, SessionStatus } from '../agent/types';

export type { AgentAction, AgentTransport };

/** A live step also carries the user message it belongs to and, for web search, its sources. */
export interface ChatStep extends AgentStep {
  uturn?: number;
  sources?: WebSource[];
}

export interface WebSource { title: string; url: string; domain: string; snippet: string | null; date: string | null }

export interface Citation {
  n: number;
  title: string;
  url: string;
  domain: string;
  date: string | null;
  snippet: string | null;
  retrieved_at: string;
}

export const CONTEXT_KINDS = ['place', 'run', 'scenario', 'montecarlo', 'shipments', 'research', 'other'] as const;
export type ContextKind = (typeof CONTEXT_KINDS)[number];

/** A chip the person attaches: ids and key numbers, never a dump. */
export interface ContextChip {
  kind: ContextKind;
  id: string | null;
  label: string;
  summary: Record<string, string | number | boolean>;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  ts: string;
  turn: number;
  transport?: AgentTransport;
  model?: string;
  system?: boolean;
  /** Assistant: the tool calls of this message's turn. */
  tool_steps?: ChatStep[];
  citations?: Citation[];
  /** Indices of paragraphs that state web facts without a citation. */
  uncited?: number[];
  web?: { used: boolean; searches: number; flagged: boolean; sources: number; dropped_markers?: number };
  cost_usd?: number;
  model_turns?: number;
  /** User: the chips attached to the message. */
  context?: ContextChip[];
}

export type WebState = 'off' | 'on' | 'recorded' | 'daily_limit';
export interface WebView {
  enabled: boolean;
  state: WebState;
  searches_turn: number;
  searches_thread: number;
  daily: { cap: number; used_today: number; remaining: number; usd_per_call: number } | null;
}

export interface ThreadView {
  id: string;
  kind: 'thread';
  title: string;
  created_at: string;
  updated_at: string;
  status: SessionStatus;
  stop_reason: string | null;
  error: string | null;
  transport: AgentTransport;
  model: string;
  limits: AgentLimits;
  usage: AgentUsage;
  messages: ChatMessage[];
  pending: AgentAction[];
  client_actions: AgentAction[];
  rev: number;
  turn: number;
  last_turn: { status: string; reason: string | null; turn: number } | null;
  web_search: boolean;
  web: WebView;
  cost_usd: number;
}

export interface ThreadSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  status: SessionStatus;
  transport: AgentTransport;
  messages: number;
  preview: string;
  web_search: boolean;
  spent_usd: number;
}

export interface ThreadStepsDoc {
  session_id: string;
  status: SessionStatus;
  rev: number;
  transport: AgentTransport;
  steps: ChatStep[];
  pending: AgentAction[];
  client_actions: AgentAction[];
}

export interface ChatStatusDoc {
  enabled: boolean;
  transport_label?: AgentTransport | null;
  model?: string;
  unavailable?: string;
  chat?: {
    limits: AgentLimits;
    suggestions: string[];
    export_formats: string[];
    web_search: { cap: number; used_today: number; remaining: number; usd_per_call: number } | null;
    client_daily_usd: number;
    max_messages: number;
  };
}

export type ExportFormat = 'html' | 'md' | 'pdf' | 'json';
export const EXPORT_FORMATS: readonly ExportFormat[] = ['html', 'md', 'pdf', 'json'];
export const EXPORT_WORD: Readonly<Record<ExportFormat, string>> = { html: 'HTML', md: 'Markdown', pdf: 'PDF', json: 'JSON' };

export const WORKING: readonly SessionStatus[] = ['running', 'awaiting_confirmation', 'awaiting_client'];
export const isWorking = (s: SessionStatus | 'none'): boolean => s !== 'none' && WORKING.includes(s);

export type { AgentLimits, AgentUsage, SessionStatus };
