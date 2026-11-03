/* HISTORY backplay, FORK and realism tester: client types.
 * Shapes mirror the history API EXACTLY as served. Optional fields are optional
 * because the server omits them on some paths. */

export type HistState = 'HISTORY-CAPABLE' | 'CURRENT-ONLY' | 'BLOCKED';
export type HistMode = 'SNAPSHOT' | 'MODELED' | string;

export interface HistProvenance { source?: string; retrieved_at?: string | null; mode?: HistMode | null }

export interface HistSource {
  source: string; label: string; state: HistState | string;
  history_window?: string; supports?: string[];
  verified?: { checked_on?: string; result?: string; how?: string };
  notes?: string;
  credential?: string | null;
  cache?: { entries: number; files?: string[] };
  budget?: { cap: number; used_today: number; remaining: number } | null;
  fetch_enabled?: boolean;
}
export interface HistSourcesDoc {
  sources: HistSource[];
  summary: Record<string, number>;
  fetch_mode?: string;
  checked_on?: string;
}

export interface HistExample {
  event_id: string; replay_id?: string; title: string; node_id: string; started_at: string; mode: HistMode;
  watch_places?: string[]; realism_cards?: string[];
}
export interface HistExamplesDoc { examples: HistExample[]; realism_cards?: unknown }

export interface HistEvent {
  id: string; node_id: string; kind: string; severity: number; status: string;
  started_at: string; detected_at?: string; expected_duration_d?: number | null;
  capacity_loss?: number | null; mode: HistMode; historical?: boolean;
  replay_id?: string; title?: string; source: string;
  watch_places?: string[]; control_places?: string[];
  capacity_loss_basis?: { kind?: string; source?: string; text?: string };
  modeled_notice?: string;
  provenance?: HistProvenance;
  status_as_of?: string; day_of_event?: number;
}
export interface HistEventSourceStatus {
  source: string; served: boolean; count?: number; mode?: string | null; reason?: string; unmapped_count?: number | null;
  provenance?: HistProvenance;
}
export interface HistEventsDoc {
  from: string; to: string; places?: string[]; count: number; events: HistEvent[];
  sources?: HistEventSourceStatus[]; label?: string;
}

/** A refusal: HTTP 200, `served:false`; no series/events key exists. */
export interface HistNotServed {
  served: false; state: 'NOT SERVED'; reason: string; detail?: string;
  provenance?: HistProvenance; place?: string; metric?: string;
}
export interface HistObsPoint { date: string; value: number }
export interface HistObservations {
  served: true; source: string; place: string; metric: string;
  unit?: string; frequency?: string; native_field?: string; portname?: string; class_label?: string;
  series: HistObsPoint[];
  coverage?: { from: string; to: string; n: number; missing_dates: string[] };
  provenance: HistProvenance;
}

export interface HistTimelineObs {
  place: string; metric: string; unit?: string; on_date?: number | null;
  trailing?: HistObsPoint[]; last_observation?: HistObsPoint | null; provenance?: HistProvenance;
  served?: boolean; reason?: string;
}
export interface HistMarketCtx {
  metric: string; label?: string; unit?: string; last?: HistObsPoint | null; provenance?: HistProvenance;
}
export interface HistTimelineDoc {
  date: string; as_of: string; label?: string;
  events: HistEvent[]; event_sources?: HistEventSourceStatus[];
  observations: HistTimelineObs[]; market_context: HistMarketCtx[];
  live_equivalent?: { available?: boolean; mode?: string; count?: number; store_coverage?: Record<string, unknown> };
}

/* ── fork ──────────────────────────────────────────────────────────── */
export interface ForkDisruption {
  node_id: string; kind: string; capacity_loss?: number; duration_d: number; started_at?: string; shape?: 'step';
}
export interface ForkValidation { ok: boolean; code?: string; message?: string }
export interface ForkChainRequest {
  event_id: string;
  request: { source: string; scenario: { disruption: ForkDisruption }; params?: Record<string, unknown>; engine_rev?: number };
  validation: ForkValidation; notes?: string[]; expected_run_id?: string; run_id?: string;
}
export interface ForkSimBody {
  disruptions: ForkDisruption[]; tick: 'hour' | 'day' | 'week' | 'month'; ticks: number; seed: number;
  start_at: string; weather: boolean;
}
export interface ForkSimRequest {
  request: ForkSimBody; validation: ForkValidation;
  horizon?: { start_at: string; ticks: number; ends_at: string; lead_in_days: number };
  notes?: string[]; run_with?: string;
}
export interface ForkLineageEvent {
  event_id: string; source?: string; mode?: string; node_id: string; kind: string; started_at: string;
  replay_id?: string; provenance?: HistProvenance;
}
export interface ForkedFrom {
  kind: string; lineage_id: string; label?: string; events: ForkLineageEvent[];
  window?: { from: string; to: string }; built_at?: string; simulated_outputs_are?: string; matched_by?: string;
}
export interface ForkCreatedChain { event_id: string; created: boolean; run_id?: string; stream_hash?: string; code?: string; reason?: string }
export interface ForkResponse {
  forked_from: ForkedFrom; chain_requests: ForkChainRequest[]; sim_request: ForkSimRequest | null;
  notes?: string[]; created_chain_runs: ForkCreatedChain[]; sim_not_run?: string;
}
export interface ForkRequestBody {
  event_ids?: string[];
  window?: { from: string; to: string; places?: string[]; sources?: string[] };
  options?: {
    chain?: { params?: Record<string, unknown>; create?: boolean };
    sim?: { lead_in_days?: number; tail_days?: number; ticks?: number; seed?: number; weather?: boolean };
  };
}

/* ── realism ───────────────────────────────────────────────────────── */
export interface RateCell {
  hits?: number; graded?: number; rate?: number; ci95_clopper_pearson?: [number, number];
  mean_abs_error_d?: number | null; false_alarms?: number; n_with_both?: number; note?: string;
}
export interface RankCell { graded: boolean; n?: number; spearman?: number | null; note?: string }
export interface RealismMetrics {
  direction: RateCell; magnitude: RateCell; onset: RateCell; rank_correlation: RankCell; ungraded?: unknown[];
}
export interface RealismBaseline extends Partial<RealismMetrics> {
  description?: string; k?: number; budget_places?: number;
}
export interface RealismPlace {
  place: string; role: string;
  observed?: { baseline_per_day?: number | null; eval_mean?: number | null; change_pct?: number | null; direction?: string | null; onset_date?: string | null; onset_lag_d?: number | null };
  model?: { direction?: string | null; change_pct?: number | null; onset_date?: string | null; onset_lag_d?: number | null; severity?: number | null; why_no_direction?: string | null };
  persistence?: { direction?: string | null; change_pct?: number | null };
  k_hop?: { direction?: string | null; hops_from_seed?: number | null };
}
export interface RealismHeadline {
  direction?: { model?: string; persistence?: string; k_hop?: string };
  magnitude?: { model?: string; persistence?: string };
  onset?: { model?: string; model_mean_abs_error_d?: number | null; persistence?: string };
  rank_correlation?: { model?: number | null; k_hop?: number | null };
  verdicts?: Record<string, string>;
}
export interface RealismWindow {
  from: string; to: string; event_start?: string; evaluation_from?: string; evaluation_to?: string;
  baseline?: { from: string; to: string };
}
export type RealismSeries = Record<string, { observed?: Array<[string, number]>; simulated?: Array<[string, number]> }>;
export interface RealismCard {
  card_id?: string; source: 'COMPUTED' | 'COMPUTED+STORED' | 'STORED' | string;
  label: string; title?: string;
  rule?: { id?: string; frozen?: string; [k: string]: unknown };
  run: { kind: 'sim' | 'chain' | string; run_id: string; prediction_digest?: Record<string, unknown> };
  event?: HistEvent & { event_id?: string };
  forked_from?: ForkedFrom | null;
  window: RealismWindow; places: RealismPlace[]; not_compared?: Array<{ place: string; reason: string }>;
  metrics: RealismMetrics; baselines: { persistence?: RealismBaseline; k_hop?: RealismBaseline };
  verdicts?: Record<string, string>; headline?: RealismHeadline;
  series?: RealismSeries;
  observed_provenance?: Array<HistProvenance & { place: string; metric?: string; unit?: string; payload_sha256?: string }>;
  caveats?: string[]; grader?: string; stored_at?: string;
}
export interface RealismResultItem {
  card_id: string; title?: string; run_id: string; run_kind: 'sim' | 'chain' | string; event_id?: string;
  window: RealismWindow; label?: string; headline: RealismHeadline; stored_at?: string;
}
export interface RealismResultsDoc { label?: string; count: number; results: RealismResultItem[] }
export interface RealismRuleDoc { label?: string; rule: { id: string; frozen?: string; [k: string]: unknown }; caveat?: string }
