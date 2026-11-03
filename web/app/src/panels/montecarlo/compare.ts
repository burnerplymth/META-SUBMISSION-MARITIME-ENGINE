/* Compare mode: two finished jobs, A and B, read side by side. Everything the
 * window shows is computed here from the two jobs (their results and samples);
 * the served comparison (GET /montecarlo/compare) supplies what only the server
 * knows: whether the pair shares its random draws, and the shift interval. Pure. */
import { finite, histogramTotal } from './math';
import type { Histogram, McJob, McSample, MetricStats, PerPlace } from './types';

export interface Quantiles { p10: number; p50: number; p90: number; mean?: number }
export interface PairedShare { k: number; n: number; share: number }
export interface CompareMetric {
  a: Quantiles; b: Quantiles; delta_p50: number; delta_p90: number;
  overlap?: number; share_b_worse?: PairedShare | null; shift_ci?: [number, number] | null;
}
/** The fans of both jobs on one time grid, for places in the top of both. */
export interface CompareSeries { t_days: number[]; places: Array<{ place: string; name: string; a: BandSeries; b: BandSeries }> }
export interface BandSeries { p10: number[]; p50: number[]; p90: number[] }
export interface PlaceDelta { place: string; name: string; delta_p50: number; delta_p90: number }
/** The served comparison, read defensively; every part is optional. */
export interface CompareDoc {
  label?: string; paired: boolean; a?: string; b?: string; threshold?: number; commonInputs?: string[];
  metrics: Record<string, CompareMetric>; per_place: PlaceDelta[];
  histograms?: { edges: number[]; a_counts: number[]; b_counts: number[] } | null;
  series?: CompareSeries | null;
}

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const quant = (v: unknown): Quantiles | null => {
  if (!isObj(v) || !finite(v.p10) || !finite(v.p50) || !finite(v.p90)) return null;
  return { p10: v.p10, p50: v.p50, p90: v.p90, mean: finite(v.mean) ? v.mean : undefined };
};
const share = (v: unknown): PairedShare | null => (isObj(v) && finite(v.k) && finite(v.n) && finite(v.share) ? { k: v.k, n: v.n, share: v.share } : null);
const nums = (v: unknown): number[] | null => (Array.isArray(v) && v.every(finite) ? (v as number[]) : null);
const band = (v: unknown): BandSeries | null => {
  if (!isObj(v)) return null;
  const p10 = nums(v.p10); const p50 = nums(v.p50); const p90 = nums(v.p90);
  return p10 && p50 && p90 ? { p10, p50, p90 } : null;
};
const ci = (v: unknown): [number, number] | null => {
  if (Array.isArray(v) && v.length === 2 && finite(v[0]) && finite(v[1])) return [v[0], v[1]];
  if (isObj(v) && finite(v.lo) && finite(v.hi)) return [v.lo, v.hi];
  if (isObj(v) && finite(v.low) && finite(v.high)) return [v.low, v.high];
  return null;
};

/** Read GET /montecarlo/compare. Null when the body is not a comparison. */
export function parseCompare(j: unknown): CompareDoc | null {
  if (!isObj(j) || !isObj(j.metrics)) return null;
  const metrics: Record<string, CompareMetric> = {};
  for (const [k, v] of Object.entries(j.metrics)) {
    if (!isObj(v)) continue;
    const a = quant(v.a); const b = quant(v.b);
    if (!a || !b) continue;
    metrics[k] = {
      a, b, delta_p50: finite(v.delta_p50) ? v.delta_p50 : b.p50 - a.p50, delta_p90: finite(v.delta_p90) ? v.delta_p90 : b.p90 - a.p90,
      overlap: finite(v.overlap) ? v.overlap : undefined, share_b_worse: share(v.share_b_worse), shift_ci: ci(v.shift_ci),
    };
  }
  const per_place = (Array.isArray(j.per_place) ? j.per_place : []).filter(isObj)
    .filter((r) => typeof r.place === 'string' && finite(r.delta_p50) && finite(r.delta_p90))
    .map((r) => ({ place: r.place as string, name: typeof r.name === 'string' ? r.name : String(r.place), delta_p50: r.delta_p50 as number, delta_p90: r.delta_p90 as number }));
  const h = isObj(j.histograms) && Array.isArray(j.histograms.edges) && Array.isArray(j.histograms.a_counts) && Array.isArray(j.histograms.b_counts)
    ? { edges: j.histograms.edges as number[], a_counts: j.histograms.a_counts as number[], b_counts: j.histograms.b_counts as number[] } : null;
  const idOf = (v: unknown): string | undefined => (typeof v === 'string' ? v : isObj(v) && typeof v.job_id === 'string' ? v.job_id : undefined);
  const t = nums(isObj(j.series) ? j.series.t_days : null);
  const sp = isObj(j.series) && Array.isArray(j.series.places) ? j.series.places.filter(isObj).flatMap((p) => {
    const a = band(p.a); const b = band(p.b);
    return a && b && typeof p.place === 'string' ? [{ place: p.place, name: typeof p.name === 'string' ? p.name : p.place, a, b }] : [];
  }) : [];
  return {
    label: typeof j.label === 'string' ? j.label : undefined, paired: j.paired === true, a: idOf(j.a), b: idOf(j.b),
    threshold: finite(j.threshold) ? j.threshold : undefined, commonInputs: Array.isArray(j.common_inputs) ? j.common_inputs.filter((x): x is string => typeof x === 'string') : undefined,
    metrics, per_place, histograms: h, series: t && sp.length ? { t_days: t, places: sp } : null,
  };
}

/* ── math ─────────────────────────────────────────────────────────────── */

/** Both sample sets binned on the same equal-width edges. */
export function sharedHistogram(a: readonly number[], b: readonly number[], bins = 24): { edges: number[]; a: number[]; b: number[] } {
  const all = [...a, ...b].filter(finite);
  if (all.length === 0) return { edges: [0, 1], a: [0], b: [0] };
  const lo = Math.floor(Math.min(...all)); const hi = Math.max(Math.ceil(Math.max(...all)), lo + 1);
  const n = Math.max(1, Math.floor(bins)); const w = (hi - lo) / n;
  const edges = Array.from({ length: n + 1 }, (_, i) => lo + i * w);
  const bin = (vals: readonly number[]): number[] => {
    const c = new Array<number>(n).fill(0);
    for (const y of vals) if (finite(y)) c[Math.min(n - 1, Math.max(0, Math.floor((y - lo) / w)))] += 1;
    return c;
  };
  return { edges, a: bin(a), b: bin(b) };
}

/** Overlap of two distributions on shared bins: the sum over bins of the smaller share, 0 (apart) to 1 (identical). */
export function overlapCoefficient(a: readonly number[], b: readonly number[]): number {
  const sa = a.reduce((s, c) => s + c, 0); const sb = b.reduce((s, c) => s + c, 0);
  if (sa === 0 || sb === 0) return 0;
  return a.reduce((s, c, i) => s + Math.min(c / sa, (b[i] ?? 0) / sb), 0);
}

/** Values of one metric of two jobs, paired by simulation index `i` (the same draw in both). */
export function pairedValues(a: readonly McSample[], b: readonly McSample[], metric: string): { a: number[]; b: number[] } {
  const bm = new Map(b.map((r) => [r.i, r.outputs[metric]] as const));
  const va: number[] = []; const vb: number[] = [];
  for (const r of a) {
    const x = r.outputs[metric]; const y = bm.get(r.i);
    if (finite(x) && finite(y)) { va.push(x); vb.push(y); }
  }
  return { a: va, b: vb };
}

/** "k of N paired simulations where B is worse than A by more than x": strictly more than x. */
export function pairedWorse(a: readonly number[], b: readonly number[], x: number): PairedShare {
  const n = Math.min(a.length, b.length);
  let k = 0;
  for (let i = 0; i < n; i++) if (b[i] - a[i] > x) k += 1;
  return { k, n, share: n ? k / n : 0 };
}

export interface DeltaRow {
  metric: string; unit: string;
  a: Quantiles; b: Quantiles; d10: number; d50: number; d90: number;
}
/** Quantiles of A and B for one metric and the differences B minus A. */
export function deltaRow(metric: string, a: MetricStats, b: MetricStats): DeltaRow {
  return {
    metric, unit: a.unit || b.unit,
    a: { p10: a.p10, p50: a.p50, p90: a.p90, mean: a.mean }, b: { p10: b.p10, p50: b.p50, p90: b.p90, mean: b.mean },
    d10: b.p10 - a.p10, d50: b.p50 - a.p50, d90: b.p90 - a.p90,
  };
}

/** Metrics both jobs define, in the order of `order` (unknown ones last, alphabetical). */
export function sharedMetrics(a: McJob | null, b: McJob | null, order: readonly string[]): string[] {
  const ka = Object.keys(a?.results?.metrics ?? {}); const kb = new Set(Object.keys(b?.results?.metrics ?? {}));
  const both = ka.filter((k) => kb.has(k));
  const rank = (k: string) => { const i = order.indexOf(k); return i < 0 ? order.length : i; };
  return both.sort((x, y) => rank(x) - rank(y) || x.localeCompare(y));
}

/** Per-place differences B minus A over the places either job lists; a place missing in one counts 0 there. */
export function placeDeltas(a: readonly PerPlace[], b: readonly PerPlace[]): PlaceDelta[] {
  const ma = new Map(a.map((r) => [r.place, r])); const mb = new Map(b.map((r) => [r.place, r]));
  const ids = [...new Set([...ma.keys(), ...mb.keys()])];
  return ids.map((id) => {
    const x = ma.get(id); const y = mb.get(id);
    return {
      place: id, name: (y ?? x)!.name,
      delta_p50: (y?.added_delay_d.p50 ?? 0) - (x?.added_delay_d.p50 ?? 0), delta_p90: (y?.added_delay_d.p90 ?? 0) - (x?.added_delay_d.p90 ?? 0),
    };
  }).sort((p, q) => Math.abs(q.delta_p90) - Math.abs(p.delta_p90) || p.place.localeCompare(q.place));
}

/** Are two jobs a pair: the same number of simulations and the same seed (the draws line up index by index)? */
export const sameDraws = (a: McJob, b: McJob): boolean => a.request.n === b.request.n && a.request.seed === b.request.seed;

/** Histogram of one job's metric rebinned onto the edges of another (shares of simulations per bin, linear within a bin). */
export function rebin(h: Histogram, edges: readonly number[]): number[] {
  const total = histogramTotal(h) || 1;
  return edges.slice(0, -1).map((lo, i) => {
    const hi = edges[i + 1]; let s = 0;
    for (let j = 0; j < h.counts.length; j++) {
      const a = h.edges[j]; const b = h.edges[j + 1];
      const o = Math.min(hi, b) - Math.max(lo, a);
      if (o > 0 && b > a) s += h.counts[j] * (o / (b - a));
    }
    return s / total;
  });
}

/* ── side by side views ───────────────────────────────────────────────── */

export interface RunData { job: McJob; samples: readonly McSample[] | null }
export interface SharedHist { edges: number[]; a: number[]; b: number[] }

const valuesOf = (samples: readonly McSample[] | null, metric: string): number[] | null =>
  (samples ? samples.map((r) => r.outputs[metric]).filter(finite) : null);

/** Both runs' outputs of one metric on shared bins: from the samples when both are loaded, else the served comparison
 *  (primary metric), else each run's own histogram rebinned onto the combined range. Null when a run lacks the metric. */
export function metricHist(metric: string, a: RunData, b: RunData, doc: CompareDoc | null, primary = 'worst_added_transit_d', bins = 24): SharedHist | null {
  const ma = a.job.results?.metrics[metric]; const mb = b.job.results?.metrics[metric];
  if (!ma || !mb) return null;
  const va = valuesOf(a.samples, metric); const vb = valuesOf(b.samples, metric);
  if (va && vb && va.length > 0 && vb.length > 0) return sharedHistogram(va, vb, bins);
  if (doc?.histograms && metric === primary && doc.histograms.a_counts.length > 0) return { edges: doc.histograms.edges, a: doc.histograms.a_counts, b: doc.histograms.b_counts };
  const lo = Math.min(ma.min, mb.min); const hi = Math.max(ma.max, mb.max, lo + 1);
  const edges = Array.from({ length: 21 }, (_, i) => lo + ((hi - lo) * i) / 20);
  return { edges, a: rebin(ma.histogram, edges).map((s) => s * ma.n), b: rebin(mb.histogram, edges).map((s) => s * mb.n) };
}

/** B is higher in k of N paired simulations, by more than x. Null unless the runs share their draws and both sample sets are loaded. */
export function pairedFor(metric: string, a: RunData, b: RunData, x: number): PairedShare | null {
  if (!sameDraws(a.job, b.job) || !a.samples || !b.samples) return null;
  const v = pairedValues(a.samples, b.samples, metric);
  return v.a.length > 0 ? pairedWorse(v.a, v.b, x) : null;
}

/** Linear interpolation of a series onto another time grid; values outside the grid hold the nearest end. */
export function resample(tFrom: readonly number[], v: readonly number[], tTo: readonly number[]): number[] {
  const n = Math.min(tFrom.length, v.length);
  if (n === 0) return tTo.map(() => 0);
  return tTo.map((t) => {
    if (t <= tFrom[0]) return v[0];
    if (t >= tFrom[n - 1]) return v[n - 1];
    let i = 1; while (i < n - 1 && tFrom[i] < t) i++;
    const t0 = tFrom[i - 1]; const t1 = tFrom[i];
    return t1 === t0 ? v[i] : v[i - 1] + ((v[i] - v[i - 1]) * (t - t0)) / (t1 - t0);
  });
}

export interface FanPair {
  t: number[];
  places: Array<{ place: string; name: string; a: BandSeries; b: BandSeries }>;
}
/** The fans of both runs on one grid. The served comparison already has one; without it, B is resampled onto A's grid. */
export function fanPair(a: McJob, b: McJob, doc: CompareDoc | null): FanPair {
  if (doc?.series) return { t: doc.series.t_days, places: doc.series.places };
  const sa = a.results?.series; const sb = b.results?.series;
  if (!sa || !sb) return { t: [], places: [] };
  const places = sa.places.flatMap((pa) => {
    const pb = sb.places.find((p) => p.place === pa.place);
    return pb ? [{ place: pa.place, name: pa.name, a: { p10: pa.p10, p50: pa.p50, p90: pa.p90 }, b: { p10: resample(sb.t_days, pb.p10, sa.t_days), p50: resample(sb.t_days, pb.p50, sa.t_days), p90: resample(sb.t_days, pb.p90, sa.t_days) } }] : [];
  });
  return { t: sa.t_days, places };
}

/** The served comparison fits two runs only when it names exactly their job ids. */
export const docFits = (doc: CompareDoc | null, a: McJob, b: McJob): doc is CompareDoc => !!doc && doc.a === a.job_id && doc.b === b.job_id;
