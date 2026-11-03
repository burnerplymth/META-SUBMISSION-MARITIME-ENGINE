/* Scenario builder wording and small derivations. Pure. The server's refusal
 * reasons are engine text; the builder shows its own short phrases keyed by the
 * refusal code and the field, so the words stay in the product's vocabulary
 * (places, legs, causal chain) and never carry an identifier. */
import type { RulesDoc, ShockVerdictRow, Verdict } from './types';

const fmt = (n: number): string => n.toLocaleString('en-US', { maximumFractionDigits: 4 });

export type Field = 'start' | 'duration' | 'loss' | 'place' | 'kind' | 'shape' | 'other';
export function fieldOf(reason: string | null): Field {
  const r = reason ?? '';
  if (r.startsWith('start_offset_d')) return 'start';
  if (r.startsWith('duration_d')) return 'duration';
  if (r.startsWith('capacity_loss')) return 'loss';
  if (/^unknown node|^unknown_node|SHELL/.test(r)) return 'place';
  if (/disruption kind/.test(r)) return 'kind';
  if (/shape/.test(r)) return 'shape';
  return 'other';
}

/** One short phrase for a refusal: what is wrong and, where it helps, the bound. */
export function refusalText(v: Verdict, rules: RulesDoc | null, target: 'chain' | 'sim'): string {
  const code = v.code ?? '';
  const f = fieldOf(v.reason);
  const dmax = rules?.duration_d.max ?? 3650;
  const smax = rules?.start_offset_d.max ?? 365;
  if (code === 'unknown_node') return target === 'chain' ? 'Place not modelled by the chain engine' : 'Place not on the map';
  if (code === 'missing_capacity_loss') return 'Capacity loss required';
  if (code === 'invalid_kind') return 'Unknown kind';
  if (code === 'invalid_shape') return target === 'sim' ? 'Simulator takes the step shape only' : 'Unknown shape';
  if (code === 'span_exceeds_horizon') return `Ends after the ${rules?.sim.horizon_d ?? 180} d simulator horizon`;
  if (code === 'param_out_of_bounds' || code === 'invalid_request') {
    if (f === 'duration') return `Duration above 0 and at most ${fmt(dmax)} d`;
    if (f === 'loss') return 'Capacity loss above 0 and at most 100%';
    if (f === 'start') return `Start from 0 to ${fmt(smax)} d`;
    return 'Value out of bounds';
  }
  return 'Refused';
}

export interface RowChip { target: 'chain' | 'sim'; ok: boolean; word: string; reason: string | null }
/** The two chips of a row: Chain and Sim, accepted or refused with the reason. */
export function rowChips(row: ShockVerdictRow | undefined, rules: RulesDoc | null): RowChip[] {
  if (!row) return [];
  return (['chain', 'sim'] as const).map((t) => {
    const v = row.verdicts[t];
    return { target: t, ok: v.ok, word: v.ok ? 'Accepted' : 'Refused', reason: v.ok ? null : refusalText(v, rules, t) };
  });
}

/** The run targets that can run this scenario. */
export function targetsFor(accepted: { chain: boolean; sim: boolean } | undefined, runnable: boolean): Array<'chain' | 'sim'> {
  if (!accepted || !runnable) return [];
  return (['chain', 'sim'] as const).filter((t) => accepted[t]);
}

/** Percent of a fraction, as the loss field shows it. */
export const toPct = (loss: number): number => Math.round(loss * 10000) / 100;
export const fromPct = (pct: number): number => Math.round(pct * 100) / 10000;

export const fmtD = (d: number): string => `${fmt(Math.round(d * 100) / 100)} d`;
export const plural = (n: number, one: string, many = `${one}s`): string => `${fmt(n)} ${n === 1 ? one : many}`;

/** A saved scenario's one-line summary. */
export const savedLine = (r: { shock_count: number; total_span_d: number }): string => `${plural(r.shock_count, 'shock')} · ${fmtD(r.total_span_d)}`;

/** Stamp text for the window header. */
export function stampFor(opts: { shocks: number; span: number; state: 'idle' | 'checking' | 'ok' | 'error' | 'notserved' }): string {
  if (opts.state === 'notserved') return 'Not served';
  if (opts.shocks === 0) return 'No shocks';
  return `${plural(opts.shocks, 'shock')} · ${fmtD(opts.span)}`;
}

/** Sentence to command for the Meta AI box: trimmed, single line, bounded. */
export function cleanInstruction(text: string, max = 280): string {
  return text.replace(/\s+/g, ' ').trim().slice(0, max);
}

/** Diff rows for display: only the shocks that move, in list order. */
export function diffRows(diff: Array<{ id: string; from_d: number; to_d: number; moved: boolean }>): Array<{ id: string; from: number; to: number }> {
  return diff.filter((d) => d.moved).map((d) => ({ id: d.id, from: d.from_d, to: d.to_d }));
}

/* ── the Arrange box: what a finished Meta AI task amounts to ─────────────
 * A task ends in one of these. Silence is never one of them: every outcome
 * without a proposal carries a short reason chip. */
export type ArrangeKind = 'proposal' | 'unchanged' | 'unusable' | 'unavailable' | 'stopped' | 'no_rule';
export interface ArrangeOutcome { kind: ArrangeKind; status: string; reason: string | null }
interface OutcomeStep { kind: string; tool: string | null; status: string; result_summary: string }
export interface OutcomeInput { status: string; stopReason: string | null; steps: readonly OutcomeStep[]; hasPending: boolean }

const RULE_REFUSAL: Readonly<Record<string, string>> = {
  invalid_rule: 'Rule not valid', invalid_arguments: 'Rule not valid', unknown_shock: 'Shock not on the timeline',
  offset_out_of_bounds: 'Start outside 0 to 365 d', unknown_tool: 'Tool not available', client_timeout: 'Browser did not answer',
  invalid_shocks: 'Timeline not readable', empty_builder: 'No shocks on the timeline', organise_failed: 'Rule refused',
};
const STOP_REASON: Readonly<Record<string, string>> = {
  max_steps: 'Step limit reached', max_tool_calls: 'Tool limit reached', max_wall_time: 'Time limit reached', max_spend: 'Spend limit reached',
  daily_cap: 'Daily limit reached', llm_disabled: 'Meta AI off', too_many_tool_errors: 'Too many refusals', cancelled_by_user: 'Stopped by you',
  server_restarted: 'Server restarted', call_too_large: 'Request too large', web_search_cap: 'Daily limit reached',
};

/** The reason chip for a failed model step: "model_error: Responses API HTTP 502 (server_error)". */
export function modelFailureReason(summary: string): string {
  const http = /HTTP (\d{3})/.exec(summary);
  if (http) return `Upstream error ${http[1]}`;
  if (/unreachable/i.test(summary)) return 'Meta AI not reachable';
  if (/non-JSON/i.test(summary)) return 'Upstream reply unreadable';
  return 'Model error';
}

export function arrangeOutcome(i: OutcomeInput): ArrangeOutcome {
  if (i.hasPending) return { kind: 'proposal', status: 'Proposal ready. Accept or discard it below.', reason: null };
  const organise = [...i.steps].reverse().find((s) => s.kind === 'tool' && s.tool === 'scenario_organise');
  if (organise) {
    if (organise.status === 'ok') return { kind: 'unchanged', status: 'No change proposed.', reason: 'Starts already match the rule' };
    const code = /^(?:refused\s+)?([a-z0-9_]{1,40}):/.exec(organise.result_summary)?.[1] ?? '';
    return { kind: 'unusable', status: 'Proposal not usable.', reason: RULE_REFUSAL[code] ?? 'Rule refused' };
  }
  const failed = [...i.steps].reverse().find((s) => s.kind === 'model' && s.status === 'error');
  if (failed || i.stopReason === 'model_error') {
    return { kind: 'unavailable', status: 'Meta AI unavailable.', reason: failed ? modelFailureReason(failed.result_summary) : 'Model error' };
  }
  if (i.status === 'stopped' || i.status === 'failed' || i.status === 'cancelled' || i.status === 'interrupted') {
    return { kind: 'stopped', status: 'Stopped before a proposal.', reason: STOP_REASON[i.stopReason ?? ''] ?? 'Task ended' };
  }
  return { kind: 'no_rule', status: 'No proposal.', reason: 'No rule returned' };
}
