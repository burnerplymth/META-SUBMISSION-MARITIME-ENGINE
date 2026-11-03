/* The request side of the Monte Carlo window: the disruption taken from the
 * loaded chain or a library scenario, the input ranges table (draft rows with
 * validation against the engine's own bounds), and the POST body. Pure. */
import type { DisruptionSpec } from '../../types';
import type { DistKind, McDisruption, McDist, McInput, McRequest } from './types';

export const N_STEPS = [50, 100, 200, 500] as const;
export const DEFAULT_N = 200;
export const DEFAULT_SEED = 1;
export const SEED_MAX = 2 ** 53;

/** Engine bounds per input, from the Monte Carlo API. `lo` is open for capacity_loss and duration_d. */
export const INPUT_BOUNDS: Readonly<Record<string, { lo: number; hi: number; openLo: boolean }>> = {
  duration_d: { lo: 0, hi: 3650, openLo: true },
  capacity_loss: { lo: 0, hi: 1, openLo: true },
  queue_sensitivity: { lo: 0.1, hi: 3, openLo: false },
};
export const INPUT_LABELS: Readonly<Record<string, string>> = {
  duration_d: 'Duration', capacity_loss: 'Capacity loss', queue_sensitivity: 'Queue sensitivity',
};
export const INPUT_UNITS: Readonly<Record<string, string>> = { duration_d: 'd', capacity_loss: '', queue_sensitivity: '' };
export const DIST_OPTIONS: ReadonlyArray<{ value: DistKind; label: string }> = [
  { value: 'triangular', label: 'Triangular' }, { value: 'uniform', label: 'Uniform' }, { value: 'fixed', label: 'Fixed' },
];

export const inputLabel = (name: string): string => INPUT_LABELS[name] ?? (name.replace(/_d$/, '').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()));
export const isDistKind = (v: string): v is DistKind => v === 'triangular' || v === 'uniform' || v === 'fixed';

/* ── disruption ───────────────────────────────────────────────────────── */

export const isClosure = (kind: string): boolean => kind === 'closure';

/** A DisruptionSpec of the loaded chain as the Monte Carlo disruption. A closure carries no sampled capacity loss. */
export function disruptionFromSpec(spec: Pick<DisruptionSpec, 'node_id' | 'kind' | 'capacity_loss' | 'duration_d'> & { shape?: string }): McDisruption {
  const d: McDisruption = { node_id: spec.node_id, kind: spec.kind, duration_d: spec.duration_d, shape: spec.shape ?? 'step' };
  d.capacity_loss = isClosure(spec.kind) ? 1 : spec.capacity_loss;
  return d;
}

/** A run journal (GET /run/{id}/journal) holds the disruption at the top, or under parent_journal for a fork. */
export function specFromJournal(j: unknown): McDisruption | null {
  const o = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : null);
  const root = o(j);
  const d = o(root?.disruption) ?? o(o(root?.parent_journal)?.disruption);
  if (!d || typeof d.node_id !== 'string' || typeof d.kind !== 'string' || typeof d.duration_d !== 'number') return null;
  return disruptionFromSpec({ node_id: d.node_id, kind: d.kind, capacity_loss: typeof d.capacity_loss === 'number' ? d.capacity_loss : 1, duration_d: d.duration_d, shape: typeof d.shape === 'string' ? d.shape : undefined });
}

export interface ScenarioLike { id: string; place_id: string; kind: string; default_duration_d: number; default_capacity_loss?: number }
export function disruptionFromScenario(s: ScenarioLike, duration: number, loss: number | null): McDisruption {
  return disruptionFromSpec({ node_id: s.place_id, kind: s.kind, duration_d: duration, capacity_loss: loss ?? s.default_capacity_loss ?? 1 });
}

/** Duration and capacity loss that can be sent: a duration inside (0, 3650], a loss inside (0, 1]. */
export function validateDisruption(d: McDisruption): string | null {
  if (!d.node_id) return 'No place';
  if (!(Number.isFinite(d.duration_d) && d.duration_d > 0 && d.duration_d <= 3650)) return 'Duration out of range';
  if (!isClosure(d.kind) && !(d.capacity_loss !== undefined && d.capacity_loss > 0 && d.capacity_loss <= 1)) return 'Capacity loss out of range';
  return null;
}

/** The ranges the server uses for any input a request leaves out: duration from half to twice the
 *  value, capacity loss plus or minus 25% (fixed at 1 for a closure), queue sensitivity 0.5 / 1 / 2. */
export function defaultInputs(d: McDisruption): McInput[] {
  const dur = d.duration_d;
  const loss = d.capacity_loss ?? 1;
  return [
    { name: 'duration_d', dist: 'triangular', low: dur / 2, mode: dur, high: Math.min(3650, dur * 2), source: 'default' },
    isClosure(d.kind)
      ? { name: 'capacity_loss', dist: 'fixed', low: 1, mode: 1, high: 1, source: 'default' }
      : { name: 'capacity_loss', dist: 'triangular', low: loss * 0.75, mode: loss, high: Math.min(1, loss * 1.25), source: 'default' },
    { name: 'queue_sensitivity', dist: 'triangular', low: 0.5, mode: 1, high: 2, source: 'default' },
  ];
}

/* ── ranges table ─────────────────────────────────────────────────────── */

/** One editable row: the numbers are kept as typed so a half-typed value is not rewritten under the cursor. */
export interface RangeDraft { name: string; dist: DistKind; low: string; mode: string; high: string; source: string }

const txt = (v: number | null | undefined): string => (v === null || v === undefined || !Number.isFinite(v) ? '' : String(Number(v.toFixed(6))));

export function draftsFrom(inputs: readonly McInput[]): RangeDraft[] {
  return inputs.map((i) => {
    const dist: DistKind = isDistKind(i.dist) ? i.dist : 'fixed';
    return {
      name: i.name, dist, source: i.source ?? 'default',
      low: txt(dist === 'fixed' ? null : i.low), mode: txt(dist === 'uniform' ? null : (i.mode ?? (dist === 'fixed' ? i.low : null))), high: txt(dist === 'fixed' ? null : i.high),
    };
  });
}

/** The row as the server lists it, for the "changed" comparison. */
export const sameDraft = (a: RangeDraft, b: RangeDraft): boolean =>
  a.dist === b.dist && Number(a.low) === Number(b.low) && Number(a.mode) === Number(b.mode) && Number(a.high) === Number(b.high) && a.low === b.low ? true
    : a.dist === b.dist && (a.dist === 'fixed' ? Number(a.mode) === Number(b.mode) : a.dist === 'uniform' ? Number(a.low) === Number(b.low) && Number(a.high) === Number(b.high)
      : Number(a.low) === Number(b.low) && Number(a.mode) === Number(b.mode) && Number(a.high) === Number(b.high));

export interface RangeCheck { ok: boolean; low?: string; mode?: string; high?: string }
const num = (s: string): number => (s.trim() === '' ? NaN : Number(s));

/** Validation of one row: finite numbers, low <= mode <= high, low < high, and the engine's bounds. */
export function checkDraft(r: RangeDraft): RangeCheck {
  const out: RangeCheck = { ok: true };
  const b = INPUT_BOUNDS[r.name];
  const inBounds = (v: number): boolean => !b || (v <= b.hi && (b.openLo ? v > b.lo : v >= b.lo));
  const fail = (k: 'low' | 'mode' | 'high', msg: string) => { out.ok = false; out[k] ??= msg; };
  const lo = num(r.low); const mo = num(r.mode); const hi = num(r.high);
  const need = (k: 'low' | 'mode' | 'high', v: number) => {
    if (!Number.isFinite(v)) fail(k, 'Number needed');
    else if (!inBounds(v)) fail(k, b ? `${b.openLo ? 'Above' : 'From'} ${b.lo} to ${b.hi}` : 'Out of range');
  };
  if (r.dist === 'fixed') { need('mode', mo); return out; }
  need('low', lo); need('high', hi);
  if (r.dist === 'triangular') need('mode', mo);
  if (out.ok) {
    if (!(lo < hi)) { fail('low', 'Low below high'); fail('high', 'Low below high'); }
    if (r.dist === 'triangular') {
      if (mo < lo) fail('mode', 'Mode at or above low');
      if (mo > hi) fail('mode', 'Mode at or below high');
    }
  }
  return out;
}

export const draftsValid = (rows: readonly RangeDraft[]): boolean => rows.every((r) => checkDraft(r).ok);

/** The request's `distributions`: only the rows that differ from the defaults and are valid. A closure never overrides capacity loss. */
export function overridesFrom(rows: readonly RangeDraft[], defaults: readonly RangeDraft[], closure: boolean): Record<string, McDist> | undefined {
  const out: Record<string, McDist> = {};
  for (const r of rows) {
    if (closure && r.name === 'capacity_loss') continue;
    const d = defaults.find((x) => x.name === r.name);
    if (d && sameDraft(r, d)) continue;
    if (!checkDraft(r).ok) continue;
    if (r.dist === 'fixed') out[r.name] = { dist: 'fixed', mode: Number(r.mode) };
    else if (r.dist === 'uniform') out[r.name] = { dist: 'uniform', low: Number(r.low), high: Number(r.high) };
    else out[r.name] = { dist: 'triangular', low: Number(r.low), mode: Number(r.mode), high: Number(r.high) };
  }
  return Object.keys(out).length ? out : undefined;
}

/* ── request ──────────────────────────────────────────────────────────── */

export const clampN = (v: number): number => (Number.isFinite(v) ? Math.min(1000, Math.max(20, Math.round(v))) : DEFAULT_N);
export function clampSeed(v: number): number {
  if (!Number.isFinite(v)) return DEFAULT_SEED;
  return Math.max(-SEED_MAX, Math.min(SEED_MAX, Math.trunc(v)));
}

/** A fresh seed from a clock-free source the caller supplies (0..1), so the result is deterministic. */
export const reseedFrom = (r: number): number => 1 + Math.floor(Math.max(0, Math.min(0.999999, r)) * 999999);

export interface RequestParts {
  disruption: McDisruption; engineRev: 1 | 2; profile: string | null; n: number; seed: number; distributions?: Record<string, McDist>;
}
export function buildRequest(p: RequestParts): McRequest {
  const body: McRequest = { disruption: p.disruption, engine_rev: p.engineRev, n: clampN(p.n), seed: clampSeed(p.seed) };
  if (p.engineRev === 2 && p.profile) body.profile = p.profile;
  if (p.distributions) body.distributions = p.distributions;
  return body;
}

/** Engine selector choices of the rail: revision 1, revision 2, and the rev 2 profiles the server accepts. */
export const ENGINE_CHOICES: ReadonlyArray<{ value: string; label: string; rev: 1 | 2; profile: string | null }> = [
  { value: 'r1', label: 'Rev 1', rev: 1, profile: null },
  { value: 'r2', label: 'Rev 2', rev: 2, profile: null },
  { value: 'r2c', label: 'Rev 2 c', rev: 2, profile: 'c' },
];

/** The refusal codes of the route as short chip words. */
export const CODE_WORDS: Readonly<Record<string, string>> = {
  rate_limited: 'Rate limited', montecarlo_too_large: 'Too large', invalid_request: 'Request refused', input_not_exposed: 'Input not exposed',
  unknown_axis: 'Unknown input', param_out_of_bounds: 'Out of bounds', montecarlo_not_found: 'Job not found', montecarlo_not_ready: 'Not ready',
  unknown_node: 'Unknown place', invalid_kind: 'Unknown kind', montecarlo_unavailable: 'Unavailable', missing_capacity_loss: 'Capacity loss needed',
};
export const codeWord = (code: string | null | undefined): string => (code && CODE_WORDS[code]) || 'Request refused';
