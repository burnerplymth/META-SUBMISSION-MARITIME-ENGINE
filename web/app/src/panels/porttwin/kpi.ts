/* PORT TWIN KPI view models: formatting, ranges, the compare table. Pure.
 * Every figure here is SIMULATED (the API says so on every response); a
 * range is the spread across the named service-time variants low / base /
 * high that the API offers, never a made-up percentage. A value the API
 * leaves out is an en dash, never a zero. */
import type { CompareDoc, CompareRow, SimGroup, SimSummary } from './types';

export const DASH = '–';

export const fmtNum = (v: number | null | undefined, digits = 1): string =>
  v === null || v === undefined || !Number.isFinite(v) ? DASH : v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: digits });
export const fmtPct = (v: number | null | undefined, digits = 0): string =>
  v === null || v === undefined || !Number.isFinite(v) ? DASH : `${(v * 100).toFixed(digits)}%`;
export const fmtSigned = (v: number | null | undefined, digits = 1): string => {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH;
  const s = v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: digits });
  return v > 0 ? `+${s}` : v < 0 ? s.replace('-', '−') : '0';
};

export type Variant = 'low' | 'base' | 'high';
export const VARIANTS: readonly Variant[] = ['low', 'base', 'high'];
export type VariantSet = Partial<Record<Variant, SimSummary>>;

export interface Metric {
  key: string; label: string; unit: string;
  get: (s: SimSummary) => number | null;
  digits?: number;
}
const n = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

export const METRICS: Record<string, Metric> = {
  wait_mean: { key: 'wait_mean', label: 'Mean wait', unit: 'h', get: (s) => n(s.wait_h?.mean) },
  wait_p95: { key: 'wait_p95', label: 'Wait p95', unit: 'h', get: (s) => n(s.wait_h?.p95) },
  wait_max: { key: 'wait_max', label: 'Wait max', unit: 'h', get: (s) => n(s.wait_h?.max) },
  queue_peak: { key: 'queue_peak', label: 'Queue peak', unit: 'vessels', get: (s) => n(s.queue?.peak), digits: 0 },
  queue_mean: { key: 'queue_mean', label: 'Queue mean', unit: 'vessels', get: (s) => n(s.queue?.mean) },
  outside_peak: { key: 'outside_peak', label: 'Outside peak', unit: 'vessels', get: (s) => n(s.queue?.outside_peak), digits: 0 },
  port_time: { key: 'port_time', label: 'Port time', unit: 'h', get: (s) => n(s.port_time_h?.mean) },
  yard_mean: { key: 'yard_mean', label: 'Yard mean fill', unit: '%', get: (s) => n(s.yard?.mean_fill) },
  yard_peak: { key: 'yard_peak', label: 'Yard peak fill', unit: '%', get: (s) => n(s.yard?.peak_fill) },
  yard_throttle: { key: 'yard_throttle', label: 'Yard throttle min', unit: '', get: (s) => n(s.yard?.min_throttle) },
  teu: { key: 'teu', label: 'Container', unit: 'TEU/day', get: (s) => n(s.throughput?.container_teu_per_day), digits: 0 },
};

export interface RangeView { low: number; high: number; base: number | null }
/** [min, max] across the variants that ran, or null when fewer than two ran or they agree. */
export function rangeOf(m: Metric, vs: VariantSet): RangeView | null {
  const vals = VARIANTS.flatMap((v) => { const s = vs[v]; const x = s ? m.get(s) : null; return x === null ? [] : [x]; });
  if (vals.length < 2) return null;
  const low = Math.min(...vals); const high = Math.max(...vals);
  if (high - low < 1e-9) return null;
  const b = vs.base ? m.get(vs.base) : null;
  return { low, high, base: b };
}

export interface KpiLine { key: string; label: string; value: string; unit: string; range: string | null; rangeFrac: [number, number, number | null] | null; chip?: string }

/** Position of a range inside its own span [0, max], for the bar. */
function fracOf(r: RangeView | null, max: number): [number, number, number | null] | null {
  if (!r || max <= 0) return null;
  return [Math.max(0, r.low / max), Math.min(1, r.high / max), r.base === null ? null : Math.min(1, r.base / max)];
}

export function line(m: Metric, s: SimSummary, vs: VariantSet, pct = false): KpiLine {
  const v = m.get(s);
  const r = rangeOf(m, vs);
  const f = (x: number | null) => (pct ? fmtPct(x) : fmtNum(x, m.digits ?? 1));
  const max = r ? r.high * 1.15 : 0;
  return {
    key: m.key, label: m.label, unit: pct ? '' : m.unit, value: f(v),
    range: r ? `${f(r.low)}${pct ? '' : ''} ${DASH} ${f(r.high)}` : null, rangeFrac: fracOf(r, max),
  };
}

export interface KpiSections {
  waiting: KpiLine[];
  yard: KpiLine[] | null;
  throughput: Array<{ label: string; value: string; unit: string }>;
  calls: Array<{ label: string; value: string }>;
  censored: number | null;
}
export function kpiSections(s: SimSummary, vs: VariantSet): KpiSections {
  const M = METRICS;
  const yardOk = s.yard && Number.isFinite(s.yard.mean_fill);
  const tonnes = s.throughput?.tonnes_per_day_by_class ?? {};
  const rows: KpiSections['throughput'] = [];
  if (typeof s.throughput?.container_teu_per_day === 'number') rows.push({ label: 'Container', value: fmtNum(s.throughput.container_teu_per_day, 0), unit: 'TEU/day' });
  for (const k of Object.keys(tonnes).sort()) rows.push({ label: k, value: fmtNum(tonnes[k], 0), unit: 't/day' });
  return {
    waiting: [line(M.wait_mean, s, vs), line(M.wait_p95, s, vs), line(M.queue_peak, s, vs), line(M.outside_peak, s, vs), line(M.port_time, s, vs)],
    yard: yardOk ? [line(M.yard_mean, s, vs, true), line(M.yard_peak, s, vs, true), line(M.yard_throttle, s, vs, true)] : null,
    throughput: rows,
    calls: [
      { label: 'Arrived', value: fmtNum(s.calls?.arrived, 0) },
      { label: 'Departed', value: fmtNum(s.calls?.departed, 0) },
      { label: 'In system', value: fmtNum(s.calls?.in_system_at_end, 0) },
    ],
    censored: n(s.wait_h?.censored_still_waiting),
  };
}

export interface UtilRow { id: string; name: string; kind: string; berths: number; util: number; baseline: number | null; delta: number | null }
/** Berth utilisation per group (largest first), with the baseline when a compare ran. */
export function utilRows(s: SimSummary, groups: readonly SimGroup[], cmp: CompareDoc | null): UtilRow[] {
  const byId = new Map(groups.map((g) => [g.id, g]));
  const ids = Object.keys(s.berth_utilisation ?? {});
  return ids.map((id) => {
    const g = byId.get(id);
    const c = cmp?.berth_utilisation?.[id];
    return { id, name: g?.name ?? id, kind: g?.kind ?? 'unknown', berths: g?.berths ?? 0, util: s.berth_utilisation[id], baseline: c ? c.baseline : null, delta: c ? c.delta : null };
  }).sort((a, b) => b.berths - a.berths || (a.id < b.id ? -1 : 1));
}
/** Berth-weighted utilisation over all groups. */
export function overallUtil(rows: readonly UtilRow[], pick: 'util' | 'baseline' = 'util'): number | null {
  let w = 0; let t = 0;
  for (const r of rows) { const v = r[pick]; if (v === null || !r.berths) continue; w += r.berths; t += v * r.berths; }
  return w > 0 ? t / w : null;
}

/* ── compare table ──────────────────────────────────────────────────── */
export const MEASURE_LABEL: Record<string, string> = {
  wait_mean_h: 'Mean wait', wait_p95_h: 'Wait p95', wait_max_h: 'Wait max', port_time_mean_h: 'Port time', queue_peak: 'Queue peak', queue_mean: 'Queue mean',
  waiting_outside_peak: 'Outside peak', waiting_outside_vessel_hours: 'Outside vessel-hours', calls_arrived: 'Calls arrived', calls_departed: 'Calls departed',
  in_system_at_end: 'In system at end', throughput_container_teu_per_day: 'Container throughput', yard_peak_fill: 'Yard peak fill', yard_mean_fill: 'Yard mean fill',
};
export type Direction = 'worse' | 'better' | 'same' | 'none';
export interface CompareLine { measure: string; label: string; unit: string; baseline: string; scenario: string; delta: string; pct: string; dir: Direction }

export function compareLines(rows: readonly CompareRow[]): CompareLine[] {
  return rows.filter((r) => !r.omitted).map((r) => {
    const frac = r.unit === 'fraction';
    const f = (v: number | null | undefined) => (frac ? fmtPct(v, 1) : fmtNum(v, r.unit === 'h' ? 1 : 0));
    const dir: Direction = r.direction === 'worse' || r.direction === 'better' || r.direction === 'same' ? r.direction : 'none';
    return {
      measure: r.measure, label: MEASURE_LABEL[r.measure] ?? r.measure.replace(/_/g, ' '), unit: frac ? '' : r.unit ?? '',
      baseline: f(r.baseline), scenario: f(r.scenario),
      delta: frac ? (r.delta === null || r.delta === undefined ? DASH : `${fmtSigned(r.delta * 100, 1)} pt`) : fmtSigned(r.delta, r.unit === 'h' ? 1 : 0),
      pct: r.delta_pct === null || r.delta_pct === undefined ? DASH : `${fmtSigned(r.delta_pct, 1)}%`, dir,
    };
  });
}

/** Counts of worse and better rows (the chip beside the table title). */
export function directionCounts(lines: readonly CompareLine[]): { worse: number; better: number } {
  return { worse: lines.filter((l) => l.dir === 'worse').length, better: lines.filter((l) => l.dir === 'better').length };
}

/* ── timeline series ────────────────────────────────────────────────── */
export interface Series { t: number[]; v: number[] }
export function seriesOf<T>(samples: readonly T[], tOf: (s: T) => number, vOf: (s: T) => number | null): Series {
  const t: number[] = []; const v: number[] = [];
  for (const s of samples) { const x = vOf(s); if (x === null || !Number.isFinite(x)) continue; t.push(tOf(s) / 3600); v.push(x); }
  return { t, v };
}
