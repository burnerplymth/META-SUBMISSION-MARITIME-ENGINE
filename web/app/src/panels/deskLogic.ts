import { adapterLabel, prettyKind } from '../format';
import type { FiredAlert, GraphData, LedgerEntry } from '../types';
import { displayValue } from './display';

/* Pure logic for the desk panels (kept out of desk.tsx). */

/** Alert-rule source states. `value` is the server's canonical enum member
 *  (server/alerts/validate.py ADAPTER_STATES, case sensitive); `label` is the display word. */
export const ALERT_STATE_OPTIONS = [
  { value: 'LIVE', label: 'Live' },
  { value: 'SNAPSHOT', label: 'Snapshot' },
  { value: 'DOWN', label: 'Down' },
] as const;
export type AlertState = (typeof ALERT_STATE_OPTIONS)[number]['value'];
export const ALERT_STATE_DEFAULT: AlertState = 'DOWN';

export interface LedgerCounts { entries: number; graded: number; hits: number; misses: number }

/** Counts only. The ledger publishes no rate: grading outcomes are tallied, never divided. */
export function ledgerCounts(entries: ReadonlyArray<Pick<LedgerEntry, 'grade'>>): LedgerCounts {
  const graded = entries.filter((e) => e.grade);
  return {
    entries: entries.length,
    graded: graded.length,
    hits: graded.reduce((n, e) => n + (e.grade?.hit_steps.length ?? 0), 0),
    misses: graded.reduce((n, e) => n + (e.grade?.missed_steps.length ?? 0), 0),
  };
}

export function ledgerCountsLine(c: LedgerCounts): string {
  const g = `${c.graded} graded ${c.graded === 1 ? 'entry' : 'entries'}`;
  if (c.graded === 0 || c.hits + c.misses === 0) return g;
  return `${g} · ${c.hits} matched · ${c.misses} unmatched`;
}

const dash = (v: unknown): string => (v === null || v === undefined || v === '' ? '—' : String(v));

/** A fired alert as a short fixed label built from its structured fact (the server's
 *  sentence is never shown). Place ids and enums are mapped to display words. */
export function alertFactLabel(a: Pick<FiredAlert, 'type' | 'fact'>, graph?: GraphData | null): string {
  const fact = (a.fact ?? {}) as Record<string, unknown>;
  switch (a.type) {
    case 'adapter_state': {
      const names = Array.isArray(fact.adapters) ? (fact.adapters as unknown[]).map((x) => adapterLabel(String(x))) : [];
      return `${names.join(', ') || 'Source'} · ${displayValue(fact.state, graph)}`;
    }
    case 'event':
      return [fact.node_id ? displayValue(fact.node_id, graph) : 'Place', fact.kind ? prettyKind(String(fact.kind)) : null,
        fact.severity != null ? `Sev ${dash(fact.severity)}` : null].filter(Boolean).join(' · ');
    case 'run_outcome': {
      const v = typeof fact.value_d === 'number' ? `+${fact.value_d.toFixed(1)} d` : '—';
      const t = typeof fact.threshold_d === 'number' ? `${fact.threshold_d.toFixed(1)} d` : '—';
      return `${fact.node_id ? displayValue(fact.node_id, graph) : 'Run'} · ${v} over ${t}`;
    }
    case 'ledger':
      return fact.graded_at ? 'Entry graded' : 'Entry locked';
    default:
      return 'Alert';
  }
}
