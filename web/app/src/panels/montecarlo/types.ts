/* Shapes of the Monte Carlo routes (POST /montecarlo, GET /montecarlo/{job_id},
 * GET /montecarlo/{job_id}/samples, DELETE /montecarlo/{job_id}), read
 * defensively: a missing field is NOT SERVED in the view, never a zero. */

/** Metrics the server defines, in display order. A metric the server lists under `metrics_not_defined` is shown as Not served, never filled. */
export const METRIC_ORDER: readonly string[] = [
  'worst_added_transit_d', 'total_added_delay_d', 'places_affected_count', 'chain_length_events', 'queue_peak_delay_d',
];
export type MetricKey = string;

export type JobStatus = 'running' | 'done' | 'interrupted' | 'failed';
export type DistKind = 'triangular' | 'uniform' | 'fixed';

/** The disruption object POST /run takes: the place is `node_id`; a closure fixes the capacity loss at 1. */
export interface McDisruption { node_id: string; kind: string; capacity_loss?: number; duration_d: number; shape?: string; started_at?: string }
/** One uncertain input as the server lists it. `source` says whether the range is the default or came from the request. */
export interface McInput { name: string; dist: string; low: number | null; mode: number | null; high: number | null; source?: string }
/** A range the request overrides, keyed by input name in `distributions`. */
export interface McDist { dist: DistKind; low?: number; mode?: number; high?: number }
export interface McRequest {
  disruption: McDisruption; engine_rev: 1 | 2; profile?: string | null; n: number; seed: number;
  distributions?: Record<string, McDist>;
  /** a job id: the new job takes that job's seed and n, so the two share their random draws */
  pair_with?: string;
}
export interface McQuote { n: number; est_seconds: number; s_per_run?: number; basis?: string }
export interface McStart { job_id: string; status: JobStatus; quote: McQuote; owner_token?: string }

export interface Exceedance { threshold: number; k: number; n: number; share: number }
export interface Histogram { edges: number[]; counts: number[] }
export interface MetricStats {
  unit: string; n: number; mean: number; std: number; min: number; max: number;
  p05: number; p10: number; p25: number; p50: number; p75: number; p90: number; p95: number;
  histogram: Histogram; exceedance: Exceedance[];
}
export interface PlaceBand { p10: number; p50: number; p90: number }
export interface PerPlace { place: string; name: string; added_delay_d: PlaceBand; affected_share: number; affected_k?: number }
export interface SeriesPlace { place: string; name: string; p10: number[]; p50: number[]; p90: number[]; p25?: number[]; p75?: number[] }
export interface Series { t_days: number[]; places: SeriesPlace[]; unit?: string }
/** Both figures are null for an input held fixed. The swing is signed (days): mean output of the highest tenth of the input minus that of the lowest tenth. `swing_p10_p90` is the deprecated name of the same value. */
export interface SensitivityRow { input: string; rank_corr: number | null; swing_top_vs_bottom_tenth?: number | null; swing_p10_p90?: number | null }
export interface ConvergenceRow { n: number; p50: number; p90: number }
export interface McResults {
  n: number; seed: number; runs_digest: string;
  metrics: Record<string, MetricStats>;
  metrics_not_defined?: Array<{ metric: string; reason?: string }>;
  per_place: PerPlace[]; series: Series; sensitivity: SensitivityRow[]; convergence: ConvergenceRow[];
}
export interface McJob {
  job_id: string; status: JobStatus; progress: { done: number; n: number }; label: string;
  request: McRequest; inputs: McInput[]; quote?: McQuote;
  engine?: { engine_rev?: number; profile?: string | null; engine_label?: string };
  results?: McResults | null; error?: { code?: string; message?: string } | null;
}
/** An output is null when the metric is not defined for the run. */
export interface McSample { i: number; inputs: Record<string, number>; outputs: Record<string, number | null>; stream_hash: string }
export interface McSamples { job_id?: string; total: number; offset?: number; limit?: number; rows: McSample[] }

/** Recorded fixture file: the job document plus every sample, and where it came from. */
export interface McFixture {
  recorded: { source: string; note: string };
  job: McJob; samples: McSample[];
}

/** Outcome of one fetch: served, refused (a named reason), not served (route absent), down. */
export type Fetched<T> =
  | { kind: 'ok'; doc: T }
  | { kind: 'refused'; code: string; message: string }
  | { kind: 'notserved' }
  | { kind: 'down'; code: string };
