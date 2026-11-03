import type {
  CatalogueRun, ChainCompare, ChainJournal, ChainVerify, CheckRefusal, ComparePlaceRow, EngineRev,
  ScenarioEntry, ScenariosDoc, ShockCheck, ShockRules, ShockShape, SimCompare, SimIntervention,
  SimInterventionKind, SimJournal, SimLineageRun, SimMeasure, SimVerify,
} from './types';
import { labelWord } from '../../copy';
import { failureSentence } from '../failureText';

/* Pure logic of the scenario loop UI (SCN): request builders, the live
 * preview label, refusal mapping, compare / journal / verify shaping and
 * the transport stepping. No React, no fetch.
 * Vocabulary: places, legs, causal chain. Labels are state words only. */

/* ── engine revision ────────────────────────────────────────────────── */
export const ENGINE_REVS: readonly EngineRev[] = [1, 2];
export const DEFAULT_ENGINE_REV: EngineRev = 2;
export const engineLabel = (rev: number | null | undefined): string => `Engine rev ${rev === 1 ? 1 : 2}`;
export const engineRevOf = (rev: unknown): EngineRev => (rev === 1 ? 1 : 2);

/* ── places ─────────────────────────────────────────────────────────── */
export interface PlaceOption { id: string; name: string; kind: string }

/** Places of the served network, sorted by name. Rows without an id are dropped. */
export function placeOptions(graph: { nodes?: unknown[] } | null | undefined): PlaceOption[] {
  const out: PlaceOption[] = [];
  for (const n of graph?.nodes ?? []) {
    if (!n || typeof n !== 'object') continue;
    const r = n as Record<string, unknown>;
    if (typeof r.id !== 'string' || !r.id) continue;
    out.push({ id: r.id, name: typeof r.name === 'string' && r.name ? r.name : r.id, kind: typeof r.kind === 'string' ? r.kind : '' });
  }
  return out.sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
}

/** Exact id, or exact name (case-insensitive). Anything else is not a place. */
export function resolvePlace(input: string, options: readonly PlaceOption[]): PlaceOption | null {
  const v = input.trim();
  if (!v) return null;
  const byId = options.find((o) => o.id === v);
  if (byId) return byId;
  const low = v.toLowerCase();
  return options.find((o) => o.name.toLowerCase() === low) ?? null;
}

export const placeNamer = (options: readonly PlaceOption[]) => {
  const m = new Map(options.map((o) => [o.id, o.name]));
  return (id: string | null | undefined): string => (id ? m.get(id) ?? id : '—');
};
export type NameOf = (id: string | null | undefined) => string;

/* ── shock kinds and sizes ──────────────────────────────────────────── */
const KIND_WORD: Record<string, string> = {
  closure: 'Closure', capacity_cut: 'Capacity cut', congestion: 'Congestion', strike: 'Strike',
  weather: 'Weather', accident: 'Accident', grounding: 'Grounding', policy: 'Policy', other: 'Other',
};
export const kindWord = (k: string): string => KIND_WORD[k] ?? (k ? k.replace(/_/g, ' ').replace(/^./, (c) => labelWord(c)) : '—');
export const SHAPES: readonly ShockShape[] = ['step', 'ramp', 'pulse'];

/** A closure forces the full loss (rules.capacity_loss.closure_forces). */
export const effectiveSize = (kind: string, size: number, rules?: ShockRules | null): number =>
  kind === 'closure' ? rules?.capacity_loss.closure_forces ?? 1 : size;
const pct = (x: number): string => `${Math.round(x * 1000) / 10}%`;

/** The UTC day after `now`, as YYYY-MM-DD: the default start of a new shock. */
export function defaultStartDate(now: Date): string {
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1)).toISOString().slice(0, 10);
}
/** YYYY-MM-DD to the ISO instant the API takes; anything else is undefined. */
export const startedAtIso = (date: string): string | undefined => (/^\d{4}-\d{2}-\d{2}$/.test(date) ? `${date}T00:00:00Z` : undefined);

/* ── chain loop: drafts and request bodies ──────────────────────────── */
export interface ChainShockDraft {
  placeId: string; kind: string; size: number; durationD: number; shape: ShockShape; startsOn: string;
}

export const newRunDisruption = (d: ChainShockDraft, rules?: ShockRules | null) => {
  const started = startedAtIso(d.startsOn);
  return {
    node_id: d.placeId, kind: d.kind, capacity_loss: effectiveSize(d.kind, d.size, rules),
    duration_d: d.durationD, shape: d.shape, ...(started ? { started_at: started } : {}),
  };
};
export const buildRunBody = (d: ChainShockDraft, rev: EngineRev, rules?: ShockRules | null) =>
  ({ engine_rev: rev, source: 'scenario' as const, scenario: { disruption: newRunDisruption(d, rules) } });
export const buildNewCheckBody = (d: ChainShockDraft, rev: EngineRev, rules?: ShockRules | null) =>
  ({ engine_rev: rev, disruption: newRunDisruption(d, rules) });

/** A fork overrides the absolute loss, the duration and (when not step) the shape
 *  at the place of the chosen step. */
export const forkOverrides = (d: Pick<ChainShockDraft, 'size' | 'durationD' | 'shape'>): Record<string, unknown> =>
  ({ capacity_loss: d.size, duration_d: d.durationD, ...(d.shape !== 'step' ? { shape: d.shape } : {}) });
export const buildForkBody = (runId: string, atSeq: number, d: Pick<ChainShockDraft, 'size' | 'durationD' | 'shape'>) =>
  ({ run_id: runId, at_seq: atSeq, overrides: forkOverrides(d) });

/** The ready-made request of a scenario-library row, with the picked
 *  duration, size, shape and start; the engine revision is the toggle's. */
export function scenarioDraft(s: ScenarioEntry, pick: { durationD?: number; size?: number; shape?: ShockShape; startsOn: string }): ChainShockDraft {
  return {
    placeId: s.place_id, kind: s.kind,
    size: s.kind === 'closure' ? 1 : pick.size ?? s.default_capacity_loss ?? 0.5,
    durationD: pick.durationD ?? s.default_duration_d, shape: pick.shape ?? 'step', startsOn: pick.startsOn,
  };
}

/** Rows of a /scenarios answer that can be offered: an id, a verified place and a duration. */
export function parseScenarios(doc: unknown): ScenarioEntry[] {
  const rows = (doc as ScenariosDoc | null)?.scenarios;
  if (!Array.isArray(rows)) return [];
  return rows.filter((r): r is ScenarioEntry =>
    !!r && typeof r.id === 'string' && typeof r.place_id === 'string' && typeof r.kind === 'string'
    && Array.isArray(r.duration_options_d) && typeof r.default_duration_d === 'number' && r.verified !== false);
}

/** Live one-line preview of the shock being composed: a generated state
 *  label, never an explanation. Null until the draft names a place. */
export function chainPreview(d: ChainShockDraft, nameOf: NameOf,
  at: { t: 'date' } | { t: 'step'; seq: number }, rules?: ShockRules | null): string | null {
  if (!d.placeId) return null;
  const size = at.t === 'step' ? d.size : effectiveSize(d.kind, d.size, rules);
  const word = at.t === 'step' ? (size >= 1 ? 'Closure' : 'Capacity cut') : kindWord(d.kind);
  const sized = word === 'Closure' ? '' : ` ${pct(size)}`;
  const from = at.t === 'step' ? `step ${at.seq}` : d.startsOn || 'default start';
  return `${word}${sized} at ${nameOf(d.placeId)}, ${d.durationD} d, from ${from}${d.shape === 'step' ? '' : `, ${d.shape}`}`;
}

/* ── sim loop: drafts and request bodies ────────────────────────────── */
export interface SimShockDraft {
  type: SimInterventionKind; placeId: string; toPlaceId: string; kind: string;
  size: number; durationD: number; radiusKm: number; atTick: number;
}
export const SIM_TYPE_WORD: Record<SimInterventionKind, string> = { disruption: 'Disruption', airspace: 'Airspace', storm_cell: 'Storm cell' };

export function buildSimIntervention(d: SimShockDraft, rules?: ShockRules | null): SimIntervention {
  if (d.type === 'airspace') return { kind: 'airspace', at_tick: d.atTick, spec: { anchor: d.placeId, radius_km: d.radiusKm, duration_d: d.durationD } };
  if (d.type === 'storm_cell') {
    return { kind: 'storm_cell', at_tick: d.atTick, spec: { from_node: d.placeId, to_node: d.toPlaceId, radius_km: d.radiusKm, duration_d: d.durationD } };
  }
  return {
    kind: 'disruption', at_tick: d.atTick,
    spec: { node_id: d.placeId, kind: d.kind, capacity_loss: effectiveSize(d.kind, d.size, rules), duration_d: d.durationD },
  };
}
/** POST /sim/run/{id}/fork: the fork point is the first shock's tick. */
export const buildSimForkBody = (atTick: number, interventions: SimIntervention[], remove?: number[]) =>
  ({ at_tick: atTick, interventions, ...(remove && remove.length ? { remove } : {}) });

export function simPreview(d: SimShockDraft, nameOf: NameOf, rules?: ShockRules | null): string | null {
  if (!d.placeId) return null;
  const from = `from tick ${d.atTick}`;
  if (d.type === 'airspace') return `Airspace closure ${d.radiusKm} km around ${nameOf(d.placeId)}, ${d.durationD} d, ${from}`;
  if (d.type === 'storm_cell') {
    if (!d.toPlaceId) return null;
    return `Storm cell ${d.radiusKm} km on ${nameOf(d.placeId)} to ${nameOf(d.toPlaceId)}, ${d.durationD} d, ${from}`;
  }
  const size = effectiveSize(d.kind, d.size, rules);
  return `${size >= 1 && d.kind === 'closure' ? 'Closure' : kindWord(d.kind)}${d.kind === 'closure' ? '' : ` ${pct(size)}`} at ${nameOf(d.placeId)}, ${d.durationD} d, ${from}`;
}

/* ── refusals ───────────────────────────────────────────────────────── */
export type RefusalField = 'place' | 'size' | 'duration' | 'shape' | 'kind' | 'step' | 'tick' | 'radius' | 'override' | 'run' | null;
export interface Refusal { code: string; reason: string; field: RefusalField; origin: 'server' | 'local' }

/** Which input a server code points at. param_out_of_bounds names its
 *  parameter in the reason text; the code itself is shown verbatim. */
export function refusalField(code: string, reason: string): RefusalField {
  switch (code) {
    case 'unknown_node': return 'place';
    case 'invalid_kind': return 'kind';
    case 'missing_capacity_loss': return 'size';
    case 'invalid_shape': return 'shape';
    case 'at_seq_out_of_range': return 'step';
    case 'empty_overrides': case 'unknown_override': case 'unknown_param': return 'override';
    case 'unknown_run': case 'unknown_sim_run': return 'run';
    case 'intervention_refused': return 'tick';
    case 'param_out_of_bounds': case 'invalid_request': {
      const r = reason.toLowerCase();
      if (/at_tick|tick/.test(r)) return 'tick';
      if (/radius/.test(r)) return 'radius';
      if (/duration/.test(r)) return 'duration';
      if (/capacity|loss|size/.test(r)) return 'size';
      if (/shape/.test(r)) return 'shape';
      if (/place|node|anchor/.test(r)) return 'place';
      return null;
    }
    default: return null;
  }
}
export const mapRefusal = (code: string, reason: string, origin: 'server' | 'local' = 'server'): Refusal =>
  ({ code, reason, field: refusalField(code, reason), origin });
/** The refusal of a /shocks/check answer, or null when it was accepted. */
export const refusalOfCheck = (c: ShockCheck): Refusal | null => {
  if (c.accepted) return null;
  const r: CheckRefusal = c.refused ?? { code: 'refused', reason: 'refused' };
  return mapRefusal(r.code, r.reason);
};
/** The one sentence a refusal shows. Server wording (field names, ids, raw
 *  validation text) is never printed; a local check already speaks plainly. */
export function refusalSentence(r: Refusal): string {
  if (r.origin === 'local') return r.reason;
  switch (r.code) {
    case 'unknown_node': return 'Place not recognised.';
    case 'invalid_kind': return 'Disruption kind not recognised.';
    case 'missing_capacity_loss': return 'Capacity loss is required.';
    case 'invalid_shape': return 'Shape not recognised.';
    case 'at_seq_out_of_range': return 'Step outside the stored range.';
    case 'empty_overrides': return 'No overrides given.';
    case 'unknown_override': case 'unknown_param': return 'Override not recognised.';
    case 'unknown_run': case 'unknown_sim_run': return 'Run not found.';
    case 'intervention_refused': return 'Start tick not accepted for this run.';
    case 'rate_limited': return 'The call limit is reached.';
    case 'param_out_of_bounds': case 'invalid_request':
      switch (r.field) {
        case 'tick': return 'Start tick outside the allowed range.';
        case 'radius': return 'Radius outside the allowed range.';
        case 'duration': return 'Duration outside the allowed range.';
        case 'size': return 'Capacity loss outside the allowed range.';
        case 'shape': return 'Shape not recognised.';
        case 'place': return 'Place not recognised.';
        default: return 'Request not accepted.';
      }
    default: return 'Request not accepted.';
  }
}
/** `[code] message` (a stored error line) as one plain sentence for display. */
export function plainLine(line: string): string {
  const m = /^\[([^\]]+)\]\s*(.*)$/s.exec(line);
  const code = m ? m[1] : 'error';
  if (/^(timeout|network)$/.test(code) || /^http_/.test(code)) return failureSentence(code);
  if (code === 'error') return 'Request not completed.';
  return refusalSentence(mapRefusal(code, m ? m[2] : line));
}
/** One display line for any thrown error: `[code] message`. */
export const errorLine = (e: unknown): string => {
  const x = e as { code?: unknown; message?: unknown } | null;
  const code = typeof x?.code === 'string' ? x.code : 'error';
  return `[${code}] ${typeof x?.message === 'string' ? x.message : String(e)}`;
};

/** The checks the server will make on a sim shock, run locally (no sim
 *  dry-run route exists). Codes are the server's own. */
export function validateSimDraft(d: SimShockDraft,
  ctx: { ticks: number; places: ReadonlySet<string>; rules: ShockRules | null }): Refusal | null {
  const L = (code: string, reason: string) => mapRefusal(code, reason, 'local');
  if (!Number.isInteger(d.atTick) || d.atTick < 0 || d.atTick > ctx.ticks - 1) {
    return L('invalid_request', `Start tick outside 0 to ${ctx.ticks - 1}.`);
  }
  if (!d.placeId || !ctx.places.has(d.placeId)) return L('unknown_node', 'Place not recognised.');
  if (d.type === 'storm_cell' && (!d.toPlaceId || !ctx.places.has(d.toPlaceId))) return L('unknown_node', 'Destination place not recognised.');
  const maxDur = ctx.rules?.duration_d.max ?? 3650;
  if (!(d.durationD > 0 && d.durationD <= maxDur)) return L('param_out_of_bounds', `Duration outside 0 to ${maxDur} d.`);
  if (d.type === 'disruption') {
    if (ctx.rules && !ctx.rules.kinds.includes(d.kind)) return L('invalid_kind', 'Disruption kind not recognised.');
    const size = effectiveSize(d.kind, d.size, ctx.rules);
    if (!(size > 0 && size <= 1)) return L('param_out_of_bounds', 'Capacity loss outside 0 to 1.');
  } else if (!(d.radiusKm > 0 && d.radiusKm <= 20000)) {
    return L('param_out_of_bounds', 'Radius outside 0 to 20,000 km.');
  }
  return null;
}

/* ── time and numbers ───────────────────────────────────────────────── */
export function fmtElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
export function fmtNum(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  if (Number.isInteger(v)) return v.toLocaleString('en-US');
  return v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: Math.abs(v) >= 1000 ? 0 : digits });
}
export function fmtSigned(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  if (v === 0) return '0';
  return `${v > 0 ? '+' : '−'}${fmtNum(Math.abs(v), digits)}`;
}
export const fmtDays = (v: number | null | undefined): string => (v === null || v === undefined ? '—' : `${fmtNum(v, 1)} d`);
/** `3 shocks`, `1 shock`: a count and its noun, plural in lower case. */
export const countWord = (n: number, noun: string): string => `${n} ${noun}${n === 1 ? '' : 's'}`;
export const shortHash = (h: string | null | undefined, n = 12): string => (h ? `${h.slice(0, n)}…` : '—');
export const shortId = (id: string | null | undefined): string => (id ? (id.length > 22 ? `${id.slice(0, 14)}…${id.slice(-4)}` : id) : '—');

/* ── compare (chain) ────────────────────────────────────────────────── */
export interface CmpPlaceRow {
  placeId: string; name: string; presence: 'Both' | 'Only A' | 'Only B';
  channelA: string; channelB: string; firstSeqA: string; firstSeqB: string;
  delayA: string; delayB: string; delayDelta: string; deltaSign: -1 | 0 | 1;
  magnitudeDelta: string; costDelta: string; changed: boolean;
}
export interface ChannelRow { channel: string; a: number; b: number; delta: number }
export interface ChainCompareView {
  identical: boolean; divergesFromSeq: number | null; sharedPrefix: number;
  sameRev: boolean; revA: string; revB: string; eventsA: number; eventsB: number;
  places: CmpPlaceRow[]; changedCount: number; channels: ChannelRow[];
}
const cell = (v: string | number | null): string => (v === null || v === undefined ? '—' : String(v));
const sign = (v: number): -1 | 0 | 1 => (v > 0 ? 1 : v < 0 ? -1 : 0);

export function shapeChainCompare(doc: ChainCompare, nameOf: NameOf): ChainCompareView {
  const places: CmpPlaceRow[] = doc.places.map((p: ComparePlaceRow) => {
    const changed = p.only_in !== null || p.delay_delta_d !== 0 || p.magnitude_delta !== 0 || p.cost_delta !== 0
      || p.channel_a !== p.channel_b || p.first_seq_a !== p.first_seq_b;
    return {
      placeId: p.place_id, name: nameOf(p.place_id),
      presence: p.only_in === 'a' ? 'Only A' : p.only_in === 'b' ? 'Only B' : 'Both',
      channelA: cell(p.channel_a), channelB: cell(p.channel_b),
      firstSeqA: cell(p.first_seq_a), firstSeqB: cell(p.first_seq_b),
      delayA: p.delay_d_a === null ? '—' : fmtDays(p.delay_d_a), delayB: p.delay_d_b === null ? '—' : fmtDays(p.delay_d_b),
      delayDelta: p.delay_delta_d === 0 ? '0' : `${fmtSigned(p.delay_delta_d, 2)} d`, deltaSign: sign(p.delay_delta_d),
      magnitudeDelta: fmtSigned(p.magnitude_delta, 2), costDelta: fmtSigned(p.cost_delta, 4), changed,
    } satisfies CmpPlaceRow;
  });
  const keys = [...new Set([...Object.keys(doc.channels.a), ...Object.keys(doc.channels.b)])];
  return {
    identical: doc.identical, divergesFromSeq: doc.diverges_from_seq, sharedPrefix: doc.shared_prefix_len,
    sameRev: doc.same_engine_rev, revA: engineLabel(doc.a.engine_rev), revB: engineLabel(doc.b.engine_rev),
    eventsA: doc.a.event_count, eventsB: doc.b.event_count,
    places, changedCount: places.filter((p) => p.changed).length,
    channels: keys.map((k) => ({ channel: k, a: doc.channels.a[k] ?? 0, b: doc.channels.b[k] ?? 0, delta: doc.channels.delta[k] ?? 0 })),
  };
}

/** `Diverges from step n` chip text. */
export const chainDivergenceChip = (v: { identical: boolean; divergesFromSeq: number | null }): string =>
  v.identical || v.divergesFromSeq === null ? 'Identical' : `Diverges from step ${v.divergesFromSeq}`;

/** Where "jump to first difference" lands in the (already loaded) chain of
 *  B: the place of the event at the divergence seq, falling back to the
 *  compare table's first-seq columns. Null when the runs are identical. */
export function chainJumpTarget(doc: ChainCompare, eventsB: ReadonlyArray<{ seq: number; node_id: string }> | null | undefined):
  { seq: number; placeId: string | null } | null {
  const n = doc.diverges_from_seq;
  if (n === null || n === undefined) return null;
  const ev = eventsB?.find((e) => e.seq === n);
  if (ev) return { seq: n, placeId: ev.node_id };
  const row = doc.places.find((p) => p.first_seq_b === n) ?? doc.places.find((p) => p.first_seq_a === n);
  return { seq: n, placeId: row?.place_id ?? null };
}

/* ── compare (sim) ──────────────────────────────────────────────────── */
export interface SimMeasureRow {
  id: string; label: string; unit: string; basis: string; a: string; b: string; difference: string; deltaSign: -1 | 0 | 1;
  firstDiffers: string;
}
export interface SimCompareView {
  identical: boolean; divergesFromTick: number | null; relation: string | null; forkedAtTick: number | null;
  rows: SimMeasureRow[]; omitted: string[]; differingEntities: number; placesChanged: number;
  firstChanged: Array<{ placeId: string; tick: number }>;
  firstDifference: null | { tick: number; entityId: string; mode: string; placeId: string | null; aKind: string; bKind: string };
}
const RELATION_WORD: Record<string, string> = { B_IS_FORK_OF_A: 'B forks A', A_IS_FORK_OF_B: 'A forks B' };

/** First-differs cell: `Tick n`, `Never` (a per-tick measure that never
 *  differs) or `—` (a measure that is not per tick). */
export const firstDiffersCell = (m: Pick<SimMeasure, 'per_tick' | 'first_differs_tick'>): string =>
  !m.per_tick ? '—' : m.first_differs_tick === null ? 'Never' : `Tick ${m.first_differs_tick}`;

export function shapeSimCompare(doc: SimCompare): SimCompareView {
  const rows = doc.measures.map((m): SimMeasureRow => ({
    id: m.id, label: m.label, unit: m.unit, basis: m.basis,
    a: fmtNum(m.a, 1), b: fmtNum(m.b, 1), difference: fmtSigned(m.difference, 1),
    deltaSign: sign(m.difference ?? 0), firstDiffers: firstDiffersCell(m),
  }));
  const fd = doc.first_difference;
  const kindOf = (e: Record<string, unknown> | null) => (e && typeof e.kind === 'string' ? e.kind : '—');
  return {
    identical: doc.diverges_from_tick === null, divergesFromTick: doc.diverges_from_tick,
    relation: doc.lineage ? RELATION_WORD[doc.lineage.relation] ?? doc.lineage.relation : null,
    forkedAtTick: doc.lineage?.forked_at_tick ?? null,
    rows, omitted: doc.omitted.map((o) => (typeof o === 'string' ? o : `${o.id ?? 'measure'}${o.reason ? ` ${o.reason}` : ''}`)),
    differingEntities: doc.differing_entities, placesChanged: doc.places_changed.count,
    firstChanged: doc.places_changed.first.map((p) => ({ placeId: p.place_id, tick: p.first_differs_tick })),
    firstDifference: fd ? { tick: fd.tick, entityId: fd.entity_id, mode: fd.mode, placeId: fd.place_id, aKind: kindOf(fd.a_event), bKind: kindOf(fd.b_event) } : null,
  };
}
export const simDivergenceChip = (v: { divergesFromTick: number | null }): string =>
  v.divergesFromTick === null ? 'Identical' : `Diverges from tick ${v.divergesFromTick}`;
export const simJumpTarget = (doc: SimCompare): { runId: string; tick: number; placeId: string | null } | null =>
  doc.diverges_from_tick === null ? null : { runId: doc.b, tick: doc.diverges_from_tick, placeId: doc.first_difference?.place_id ?? null };

/* ── journal ────────────────────────────────────────────────────────── */
export interface JournalRowView { n: number; at: string; kind: string; what: string }
export interface LineageRowView { id: string; parent: string | null; at: string; rev: string; current: boolean }

const paramWord: Record<string, string> = { hit_threshold: 'Hit threshold', queue_sensitivity: 'Queue sensitivity' };

function chainShockDetail(s: ChainJournal['shocks'][number]): string {
  if (s.kind === 'FORK SHOCK') {
    const o = s.overrides ?? {};
    return [
      typeof o.capacity_loss === 'number' ? `size ${pct(o.capacity_loss)}` : null,
      typeof o.duration_d === 'number' ? `${o.duration_d} d` : null,
      typeof o.shape === 'string' ? o.shape : null,
    ].filter(Boolean).join(', ');
  }
  return [
    s.shock_kind ? kindWord(s.shock_kind) : null,
    typeof s.size === 'number' && s.shock_kind !== 'closure' ? pct(s.size) : null,
    typeof s.duration_d === 'number' ? `${s.duration_d} d` : null,
    s.shape && s.shape !== 'step' ? s.shape : null,
    s.starts_at ? `from ${s.starts_at.slice(0, 10)}` : null,
  ].filter(Boolean).join(', ');
}
const chainShockWhat = (s: ChainJournal['shocks'][number], nameOf: NameOf): string => `${chainShockDetail(s)} at ${nameOf(s.place_id)}`;

/** Settings first, then the shocks in order (the journal is the recipe). */
export function chainJournalRows(doc: ChainJournal, nameOf: NameOf): JournalRowView[] {
  const rows: JournalRowView[] = [];
  let n = 0;
  rows.push({ n: ++n, at: '—', kind: 'SETTING', what: engineLabel(doc.engine_rev) });
  for (const [k, v] of Object.entries(doc.settings.params ?? {})) rows.push({ n: ++n, at: '—', kind: 'SETTING', what: `${paramWord[k] ?? labelWord(k)} ${v}` });
  for (const s of doc.shocks) {
    rows.push({ n: ++n, at: s.at_seq === null || s.at_seq === undefined ? 'Start' : `Step ${s.at_seq}`, kind: s.kind, what: chainShockWhat(s, nameOf) });
  }
  return rows;
}
export function chainLineageRows(doc: ChainJournal): LineageRowView[] {
  return doc.lineage.map((l) => ({
    id: l.run_id, parent: l.parent_run_id, at: l.at_seq === null ? '—' : `Step ${l.at_seq}`,
    rev: engineLabel(l.engine_rev), current: l.run_id === doc.run_id,
  }));
}

function simShockDetail(r: SimJournal['rows'][number]): string {
  const spec = r.spec ?? {};
  const dur = r.duration_d ?? spec.duration_d ?? '?';
  if (r.shock === 'airspace') return `Airspace closure ${spec.radius_km ?? '?'} km, ${dur} d`;
  if (r.shock === 'storm_cell') return `Storm cell ${spec.radius_km ?? '?'} km, ${dur} d`;
  const kind = r.disruption_kind ?? String(spec.kind ?? '');
  const loss = typeof r.capacity_loss === 'number' && kind !== 'closure' ? ` ${pct(r.capacity_loss)}` : '';
  return `${kindWord(kind)}${loss}, ${dur} d`;
}
const simPlaces = (r: SimJournal['rows'][number], nameOf: NameOf): string => (r.place_ids ?? []).map((p) => nameOf(p)).join(' to ');
const simShockWhat = (r: SimJournal['rows'][number], nameOf: NameOf): string => `${simShockDetail(r)} at ${simPlaces(r, nameOf)}`;
export function simJournalRows(doc: SimJournal, nameOf: NameOf): JournalRowView[] {
  return doc.rows.map((r) => ({
    n: r.n, at: `Tick ${r.tick}`, kind: r.kind,
    what: r.kind === 'SETTING' ? `${labelWord(r.setting ?? '')} ${/^[A-Z ]+$/.test(String(r.value ?? '')) ? String(r.value).toLowerCase() : String(r.value ?? '')}`.trim() : `${simShockWhat(r, nameOf)}${r.source ? `  [${r.source}]` : ''}`,
  }));
}

/* ── shock book ─────────────────────────────────────────────────────── */
export type BookStatus = 'APPLIED' | 'FIRED' | 'PENDING' | 'ACCEPTED' | 'REFUSED' | 'INJECTED' | 'BUILDING' | 'FAILED';
export interface BookRow { key: string; n: string; at: string; kind: string; place: string; what: string; status: BookStatus; code?: string; session: boolean }

/** The shocks of a stored chain run: every journaled shock is APPLIED. */
export function chainBook(doc: ChainJournal | null, nameOf: NameOf): BookRow[] {
  return (doc?.shocks ?? []).map((s) => ({
    key: `j${s.n}`, n: String(s.n), at: s.at_seq === null || s.at_seq === undefined ? 'Start' : `Step ${s.at_seq}`,
    kind: s.kind, place: nameOf(s.place_id), what: chainShockDetail(s), status: 'APPLIED', session: false,
  }));
}
/** The shocks of a stored sim run, FIRED once the playhead tick reached them. */
export function simBook(doc: SimJournal | null, nameOf: NameOf, playTick: number): BookRow[] {
  return (doc?.rows ?? []).filter((r) => r.kind === 'SHOCK').map((r) => ({
    key: `j${r.n}`, n: String(r.n), at: `Tick ${r.tick}`, kind: 'SHOCK',
    place: simPlaces(r, nameOf), what: simShockDetail(r),
    status: r.tick <= playTick ? 'FIRED' : 'PENDING', session: false,
  }));
}

/* ── already in force ─────────────────────────────────────── */
/** A shock the loaded run's journal already holds that the draft would repeat. */
export interface InForceHit { n: number; at: string }

/** Chain loop, FORK AT STEP: the journal shocks that already sit on the
 *  fork step's place and apply by that step (a start shock applies from
 *  the first step, a fork shock from its own step). */
export function chainShocksInForce(doc: ChainJournal | null | undefined, placeId: string, atSeq: number | null): InForceHit[] {
  if (!doc || !placeId) return [];
  return doc.shocks
    .filter((s) => s.place_id === placeId && (s.at_seq === null || s.at_seq === undefined || atSeq === null || s.at_seq <= atSeq))
    .map((s) => ({ n: s.n, at: s.at_seq === null || s.at_seq === undefined ? 'Start' : `Step ${s.at_seq}` }));
}

/** Sim loop: the journal shocks of the same type on the same place (and the
 *  same disruption kind) whose window covers the draft's start tick.
 *  `ticksPerDay` converts the journal's duration in days to ticks. */
export function simShocksInForce(
  doc: SimJournal | null | undefined,
  draft: Pick<SimShockDraft, 'type' | 'placeId' | 'kind' | 'atTick'>,
  ticksPerDay = 1,
): InForceHit[] {
  if (!doc || !draft.placeId) return [];
  const out: InForceHit[] = [];
  for (const r of doc.rows) {
    if (r.kind !== 'SHOCK') continue;
    const type = r.shock === 'airspace' || r.shock === 'storm_cell' ? r.shock : 'disruption';
    if (type !== draft.type) continue;
    if ((r.place_ids ?? [])[0] !== draft.placeId) continue;
    if (type === 'disruption' && (r.disruption_kind ?? String(r.spec?.kind ?? '')) !== draft.kind) continue;
    const dur = r.duration_d ?? (typeof r.spec?.duration_d === 'number' ? r.spec.duration_d : null);
    const end = typeof dur === 'number' ? r.tick + dur * ticksPerDay : Infinity;
    if (r.tick <= draft.atTick && draft.atTick < end) out.push({ n: r.n, at: `Tick ${r.tick}` });
  }
  return out;
}

/** The chip text for a non-empty hit list: word and meta. */
export const inForceChip = (hits: readonly InForceHit[]): { word: string; meta: string } | null =>
  hits.length === 0 ? null : { word: 'Already in force', meta: hits.length === 1 ? `#${hits[0].n} · ${hits[0].at}` : `${hits.length} shocks` };

/* ── verify ─────────────────────────────────────────────────────────── */
export type ReplayState = 'NOT CHECKED' | 'BUILDING' | 'MATCH' | 'MISMATCH' | 'FAILED';
export const chainReplayState = (v: ChainVerify | null): ReplayState => (v === null ? 'NOT CHECKED' : v.verified ? 'MATCH' : 'MISMATCH');
export function simReplayState(v: SimVerify | null, posting = false): ReplayState {
  if (posting || v?.replay_job?.status === 'building') return 'BUILDING';
  if (v === null) return 'NOT CHECKED';
  if (v.replay_job?.status === 'failed') return 'FAILED';
  return v.replay_check === 'MATCH' ? 'MATCH' : v.replay_check === 'MISMATCH' ? 'MISMATCH' : 'NOT CHECKED';
}
export const replayStateKind = (s: ReplayState): 'live' | 'down' | 'meta' | 'sim' =>
  s === 'MATCH' ? 'live' : s === 'MISMATCH' || s === 'FAILED' ? 'down' : s === 'BUILDING' ? 'sim' : 'meta';

/** The three hashes of a chain verify, labelled as served. */
export function chainHashRows(v: ChainVerify): Array<{ label: string; value: string }> {
  return [
    { label: 'Stream SHA-256 stored', value: v.stored_stream_hash },
    { label: 'Stream SHA-256 recomputed', value: v.recomputed_stream_hash },
    { label: 'Stream SHA-256 replay', value: v.replay_stream_hash },
  ];
}

/* ── catalogue ──────────────────────────────────────────────────────── */
export interface CatalogueRowView {
  id: string; kind: 'chain' | 'sim'; title: string; created: string; state: string; facts: string;
}
export const stateWord = (status: string): string => labelWord((status || 'unknown').replace(/[_-]/g, ' '));

export function catalogueRows(runs: readonly CatalogueRun[] | null | undefined,
  opts: { kind?: 'chain' | 'sim' | null; query?: string; nameOf?: NameOf } = {}): CatalogueRowView[] {
  const q = (opts.query ?? '').trim().toLowerCase();
  const out: CatalogueRowView[] = [];
  for (const r of runs ?? []) {
    if (r.kind !== 'chain' && r.kind !== 'sim') continue;
    if (opts.kind && r.kind !== opts.kind) continue;
    const f = r.facts ?? {};
    const facts = r.kind === 'chain'
      ? [typeof f.node_id === 'string' ? (opts.nameOf ? opts.nameOf(f.node_id) : f.node_id) : null,
        typeof f.event_count === 'number' ? `${f.event_count} steps` : null].filter(Boolean).join(' · ')
      : [typeof f.ticks === 'number' ? `${f.ticks} ${String(f.tick ?? 'tick')} ticks` : null,
        typeof f.disruptions === 'number' ? `${f.disruptions} shocks` : null,
        typeof f.seed === 'number' ? `seed ${f.seed}` : null].filter(Boolean).join(' · ');
    const row: CatalogueRowView = { id: r.id, kind: r.kind, title: r.title, created: (r.created_at ?? '').slice(0, 16).replace('T', ' '), state: stateWord(r.status), facts };
    if (q && !`${row.id} ${row.title} ${row.facts}`.toLowerCase().includes(q)) continue;
    out.push(row);
  }
  return out;
}

/** Sim lineage rows: the fork tree under a root, indented by depth. */
export function lineageTree(runs: readonly SimLineageRun[]): Array<SimLineageRun & { depth: number }> {
  const byParent = new Map<string | null, SimLineageRun[]>();
  for (const r of runs) byParent.set(r.parent_id, [...(byParent.get(r.parent_id) ?? []), r]);
  const ids = new Set(runs.map((r) => r.sim_run_id));
  const out: Array<SimLineageRun & { depth: number }> = [];
  const walk = (parent: string | null, depth: number, seen: Set<string>) => {
    for (const r of byParent.get(parent) ?? []) {
      if (seen.has(r.sim_run_id)) continue;
      seen.add(r.sim_run_id);
      out.push({ ...r, depth });
      walk(r.sim_run_id, depth + 1, seen);
    }
  };
  const seen = new Set<string>();
  walk(null, 0, seen);
  for (const r of runs) if (r.parent_id !== null && !ids.has(r.parent_id) && !seen.has(r.sim_run_id)) { seen.add(r.sim_run_id); out.push({ ...r, depth: 0 }); walk(r.sim_run_id, 1, seen); }
  return out;
}

/* ── transport (chain) ──────────────────────────────────────────────── */
export type StepAction = 'first' | 'prev' | 'next' | 'last';
/** The seq to select after a transport action over the chain's steps (in
 *  served order). With nothing selected, `next` lands on the first step and
 *  `prev` on the last. Null for an empty chain. */
export function chainStep(events: ReadonlyArray<{ seq: number }>, current: number | null, action: StepAction): number | null {
  if (!events.length) return null;
  const idx = current === null ? -1 : events.findIndex((e) => e.seq === current);
  const last = events.length - 1;
  switch (action) {
    case 'first': return events[0].seq;
    case 'last': return events[last].seq;
    case 'next': return events[Math.min(last, idx + 1)].seq;
    case 'prev': return events[idx <= 0 ? (idx === -1 ? last : 0) : idx - 1].seq;
  }
}
export function chainReadout(events: ReadonlyArray<{ seq: number }>, current: number | null): string {
  if (!events.length) return 'No run';
  const idx = current === null ? -1 : events.findIndex((e) => e.seq === current);
  return idx === -1 ? `Step — / ${events.length}` : `Step ${idx + 1} / ${events.length}`;
}

/* ── capabilities ───────────────────────────────────────────────────── */
export type Capability = 'served' | 'notserved' | 'down';
/** Capability from the served OpenAPI document: a route
 *  template listed in `paths` is served, one that is not is not served; a
 *  document that is not an OpenAPI object answers null (the caller probes). */
export function capabilityFromSchema(doc: unknown, template: string): Capability | null {
  const paths = (doc && typeof doc === 'object' ? (doc as { paths?: unknown }).paths : null);
  if (!paths || typeof paths !== 'object') return null;
  return Object.prototype.hasOwnProperty.call(paths, template) ? 'served' : 'notserved';
}

/** Classify one GET probe of a route. A route that answers with its own
 *  error shape (422 missing parameter, 404 unknown run) exists; a plain
 *  404 `not_found`, a non-JSON answer or 405/501 does not; anything else
 *  (network, 5xx, 401/403) is down. */
export function capabilityOf(status: number | null, errorCode: string | null, isJson: boolean): Capability {
  if (status === null) return 'down';
  if (status >= 200 && status < 300) return isJson ? 'served' : 'notserved';
  if (status === 405 || status === 501) return 'notserved';
  if (status === 404) return isJson && errorCode && errorCode !== 'not_found' ? 'served' : 'notserved';
  if (status === 400 || status === 409 || status === 422) return isJson ? 'served' : 'notserved';
  return 'down';
}
