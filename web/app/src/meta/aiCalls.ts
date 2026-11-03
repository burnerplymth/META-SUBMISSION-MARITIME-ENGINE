/**
 * META AI SESSION LOG. Every model call this page made, newest
 * first, kept in memory only: nothing is persisted and nothing here is
 * invented. The `aiactivity` window and the status-bar popover read it.
 *
 * An entry is created from a served provenance block alone (see
 * provenance.ts): a reply with no provenance, or one where no model ran
 * (transport NONE), is not a call and is never logged. The mode is derived
 * by `effectiveTransport`, so a recorded fixture can never be logged Live.
 */
import { useSyncExternalStore } from 'react';
import { effectiveTransport, parseProvenance } from './provenance';

export type AiSurface = 'classify' | 'ask' | 'explain' | 'query' | 'brief' | 'voice' | 'sam' | 'vision' | 'agent';
export type AiMode = 'Live' | 'Recorded';

export interface AiCall {
  id: number;
  /** Epoch ms when the reply reached this page. */
  at: number;
  surface: AiSurface;
  /** One-line summary of what the call did: "Classified 1 headline". */
  did: string;
  mode: AiMode;
  model: string | null;
  promptId: string | null;
  /** Settled cost as served (USD), null when not served. */
  cost: number | null;
  cached: boolean | null;
}

export const SURFACE_WORD: Record<AiSurface, string> = {
  classify: 'Classify', ask: 'Ask the chain', explain: 'Explain', query: 'Search', brief: 'Brief', voice: 'Voice', sam: 'SAM', vision: 'Vision read', agent: 'Meta AI task',
};

export const AI_LOG_MAX = 200;
let seq = 0;
let calls: readonly AiCall[] = [];
const seen = new Set<string>();
const listeners = new Set<() => void>();
const emit = () => { for (const l of listeners) l(); };

export interface RecordInput {
  surface: AiSurface;
  did: string;
  provenance: unknown;
  at?: number;
  /** Same key twice logs once (a re-render of the same answer is not a new call). */
  key?: string;
}

/** Log one call. Returns the entry, or null when no model ran or the key repeats. */
export function recordAiCall(input: RecordInput): AiCall | null {
  const p = parseProvenance(input.provenance);
  const transport = effectiveTransport(p);
  if (!p || !transport) return null;
  if (input.key !== undefined) {
    if (seen.has(input.key)) return null;
    seen.add(input.key);
  }
  const call: AiCall = {
    id: ++seq,
    at: input.at ?? Date.now(),
    surface: input.surface,
    did: input.did,
    mode: transport === 'LIVE' ? 'Live' : 'Recorded',
    model: p.model && p.model.trim() ? p.model : null,
    promptId: p.prompt_id ?? null,
    cost: p.cost_usd !== undefined ? p.cost_usd : null,
    cached: p.cached === undefined ? null : p.cached,
  };
  calls = [call, ...calls].slice(0, AI_LOG_MAX);
  emit();
  return call;
}

export const getAiCalls = (): readonly AiCall[] => calls;
export const subscribeAiCalls = (fn: () => void): (() => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; };
export const useAiCalls = (): readonly AiCall[] => useSyncExternalStore(subscribeAiCalls, getAiCalls, getAiCalls);

export interface AiSessionStats { total: number; live: number; recorded: number; liveCostUsd: number | null }
export function sessionStats(list: readonly AiCall[]): AiSessionStats {
  let live = 0; let recorded = 0; let cost = 0; let costSeen = false;
  for (const c of list) {
    if (c.mode === 'Live') { live += 1; if (c.cost !== null) { cost += c.cost; costSeen = true; } } else recorded += 1;
  }
  return { total: list.length, live, recorded, liveCostUsd: costSeen ? cost : null };
}

/** HH:MM:SS UTC for a log row. */
export const clockUtc = (ms: number): string => new Date(ms).toISOString().slice(11, 19);
