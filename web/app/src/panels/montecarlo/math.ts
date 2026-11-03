/* Percentile, histogram and threshold arithmetic of the Monte Carlo window.
 * Pure: numbers in, numbers out. A share is always "k of N simulations";
 * k is exact when the per-simulation outputs are loaded, otherwise it is
 * read from the served exceedance table, otherwise from the histogram
 * (linear inside the bin that holds the threshold) and marked so. */
import type { ConvergenceRow, Exceedance, Histogram, MetricStats, SeriesPlace, Series } from './types';

export const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** Linear-interpolation percentile (p in 0..100) of an ascending array; NaN for an empty one. */
export function percentile(sortedAsc: readonly number[], p: number): number {
  const n = sortedAsc.length;
  if (n === 0) return NaN;
  if (n === 1) return sortedAsc[0];
  const q = Math.min(100, Math.max(0, p)) / 100;
  const pos = q * (n - 1);
  const lo = Math.floor(pos); const hi = Math.ceil(pos);
  return lo === hi ? sortedAsc[lo] : sortedAsc[lo] + (sortedAsc[hi] - sortedAsc[lo]) * (pos - lo);
}

export const sortedAscending = (values: readonly number[]): number[] => values.filter(finite).sort((a, b) => a - b);

/** Equal-width histogram of `values` over [lo, hi]; the top edge is inclusive. */
export function histogramOf(values: readonly number[], bins: number, lo?: number, hi?: number): Histogram {
  const v = values.filter(finite);
  const b = Math.max(1, Math.floor(bins));
  if (v.length === 0) return { edges: [0, 1], counts: [0] };
  const a = lo ?? Math.min(...v);
  let z = hi ?? Math.max(...v);
  if (z <= a) z = a + 1;
  const w = (z - a) / b;
  const edges = Array.from({ length: b + 1 }, (_, i) => a + i * w);
  edges[b] = z;
  const counts = new Array<number>(b).fill(0);
  for (const x of v) {
    if (x < a || x > z) continue;
    counts[Math.min(b - 1, Math.floor((x - a) / w))] += 1;
  }
  return { edges, counts };
}

export const histogramTotal = (h: Histogram): number => h.counts.reduce((s, c) => s + c, 0);

/** A histogram that can be read: edges one longer than counts, counts finite and non-negative. */
export function histogramValid(h: Histogram | null | undefined): h is Histogram {
  return !!h && Array.isArray(h.edges) && Array.isArray(h.counts) && h.counts.length >= 1 && h.edges.length === h.counts.length + 1
    && h.edges.every(finite) && h.counts.every((c) => finite(c) && c >= 0) && h.edges.every((e, i, a) => i === 0 || e >= a[i - 1]);
}

/** Simulations strictly above x, from the histogram: whole bins above plus the fraction of the bin holding x. */
export function countAboveHistogram(h: Histogram, x: number): number {
  let above = 0;
  for (let i = 0; i < h.counts.length; i++) {
    const a = h.edges[i]; const b = h.edges[i + 1];
    if (x <= a) above += h.counts[i];
    else if (x < b) above += h.counts[i] * ((b - x) / (b - a));
  }
  return above;
}

/** The value below which `q` (0..1) of the simulations fall, read from the histogram. */
export function quantileFromHistogram(h: Histogram, q: number): number {
  const total = histogramTotal(h);
  if (total === 0) return NaN;
  const target = Math.min(1, Math.max(0, q)) * total;
  let acc = 0;
  for (let i = 0; i < h.counts.length; i++) {
    const c = h.counts[i];
    if (acc + c >= target && c > 0) return h.edges[i] + ((target - acc) / c) * (h.edges[i + 1] - h.edges[i]);
    acc += c;
  }
  return h.edges[h.edges.length - 1];
}

export type ThresholdSource = 'samples' | 'exceedance' | 'histogram';
export interface ThresholdShare { k: number; n: number; share: number; source: ThresholdSource }

/** "k of N simulations above x". `values` (the per-simulation outputs) win, then an exact
 *  threshold row of the served exceedance table, then the histogram. */
export function thresholdShare(metric: Pick<MetricStats, 'histogram' | 'exceedance' | 'n'>, x: number, values?: readonly number[] | null): ThresholdShare {
  if (values && values.length > 0) {
    const v = values.filter(finite);
    const k = v.reduce((s, y) => s + (y > x ? 1 : 0), 0);
    return { k, n: v.length, share: v.length ? k / v.length : 0, source: 'samples' };
  }
  const row = metric.exceedance?.find((e: Exceedance) => e.threshold === x);
  if (row) return { k: row.k, n: row.n, share: row.n ? row.k / row.n : 0, source: 'exceedance' };
  const n = histogramTotal(metric.histogram) || metric.n;
  const k = Math.round(countAboveHistogram(metric.histogram, x));
  return { k, n, share: n ? k / n : 0, source: 'histogram' };
}

/** The exceedance (survival) curve at every bin edge: share of simulations above the edge. */
export function exceedanceCurve(h: Histogram): Array<{ x: number; share: number }> {
  const total = histogramTotal(h);
  return h.edges.map((x) => ({ x, share: total ? countAboveHistogram(h, x) / total : 0 }));
}

/** Outcome bucket of one simulation against its metric's quartile cuts:
 *  0 at or below P25, 1 up to P75, 2 up to P90, 3 above P90. */
export function bucketOf(v: number | undefined, m: Pick<MetricStats, 'p25' | 'p75' | 'p90'>): 0 | 1 | 2 | 3 | null {
  if (!finite(v)) return null;
  if (v <= m.p25) return 0;
  if (v <= m.p75) return 1;
  if (v <= m.p90) return 2;
  return 3;
}
export const BUCKET_WORDS: readonly string[] = ['At or below P25', 'P25 to P75', 'P75 to P90', 'Above P90'];

/** A round threshold to start from: the middle row of the served exceedance table, else P75 rounded. */
export function defaultThreshold(m: Pick<MetricStats, 'p50' | 'p75' | 'min' | 'max' | 'exceedance'>): number {
  const rows = m.exceedance ?? [];
  if (rows.length > 0) return rows[Math.floor((rows.length - 1) / 2)].threshold;
  return niceRound(Number.isFinite(m.p75) ? m.p75 : m.p50, m.max - m.min);
}

/** Round to a tidy value for the span (a whole number, or a step of 0.5 / 0.1 for small spans). */
export function niceRound(v: number, span: number): number {
  const s = Math.abs(span);
  const step = s >= 8 ? 1 : s >= 2 ? 0.5 : s >= 0.4 ? 0.1 : 0.01;
  return Math.round(v / step) * step;
}

/** Slider geometry for a metric: from its minimum to its maximum, in readable steps. */
export function thresholdRange(m: Pick<MetricStats, 'min' | 'max'>): { min: number; max: number; step: number } {
  const span = m.max - m.min;
  const step = span >= 40 ? 1 : span >= 8 ? 0.5 : span >= 2 ? 0.1 : span >= 0.4 ? 0.05 : 0.01;
  const lo = Math.floor(m.min / step) * step; const hi = Math.ceil(m.max / step) * step;
  return { min: Number(lo.toFixed(4)), max: Number((hi > lo ? hi : lo + step).toFixed(4)), step };
}

/** Clamp a typed threshold into the slider range; NaN falls back to the minimum. */
export function clampThreshold(v: number, r: { min: number; max: number }): number {
  if (!finite(v)) return r.min;
  return Math.min(r.max, Math.max(r.min, v));
}

/** Relative change of the last convergence step, as a fraction (0.02 = 2%); null with fewer than two steps. */
export function lastStepChange(rows: readonly ConvergenceRow[], key: 'p50' | 'p90'): number | null {
  if (rows.length < 2) return null;
  const a = rows[rows.length - 2][key]; const b = rows[rows.length - 1][key];
  if (!finite(a) || !finite(b)) return null;
  return a === 0 ? (b === 0 ? 0 : null) : (b - a) / Math.abs(a);
}

/** The places with the largest median in the series, best first (stable on ties). */
export function topPlaces(series: Series | undefined | null, count = 5): SeriesPlace[] {
  const pl = series?.places ?? [];
  const peak = (p: SeriesPlace) => Math.max(0, ...p.p50.filter(finite));
  return pl.map((p, i) => ({ p, i, v: peak(p) })).sort((a, b) => b.v - a.v || a.i - b.i).slice(0, count).map((x) => x.p);
}

/** The place whose median changes most over the series (a flat band tells little); the first place when none moves. */
export function mostMoving<T extends { place: string; p50: readonly number[] }>(places: readonly T[]): T | null {
  let best: T | null = places[0] ?? null; let span = 0;
  for (const p of places) {
    const v = p.p50.filter(finite);
    const s = v.length ? Math.max(...v) - Math.min(...v) : 0;
    if (s > span + 1e-9) { span = s; best = p; }
  }
  return best;
}
