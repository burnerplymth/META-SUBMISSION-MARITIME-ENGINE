import type { Citation } from './citation';
// API contract shapes (events, chains, runs, forks, scorecard).
export interface CausalEvent {
  run_id: string; seq: number; node_id: string; parent_id: number | null;
  channel: 'queue' | 'congestion' | 'reroute' | 'mode-shift' | 'inventory' | 'cost' | string;
  mechanism: string; delay_d: number; magnitude: number; at: string; tick?: number;
}
export interface ChainEdge { from_seq: number | null; to_seq: number; from_node: string; to_node: string; channel: string }
export interface Chain { run_id: string; status: string; event_count: number; stream_hash: string; events: CausalEvent[]; edges: ChainEdge[] }
/** WHYNOT (Analysis Desks): one place adjacent to the chain that was
 *  NOT hit, with the engine-recorded reason and its deciding figures. */
export interface NotHitEntry { place_id: string; reason: string; figures: Record<string, unknown> }
/* ── Analysis Desks payloads (GET/POST /desks/*, served figures only) ── */
export interface CritRow { rank: number; place_id: string; name?: string; kind?: string; places_hit: number; total_delay_d: number; events: number }
export interface CritDoc { status: string; version?: string; graph_version?: string; graph_hash?: string; elapsed_s?: number; places_swept?: number; rows: CritRow[]; job?: { state: string; done: number; total: number; error?: string } }
export interface LaneLeg { from: string; to: string; mode: string; transit_d: number; capacity_share?: number; spare_capacity?: number | null; spare_capacity_unit?: string | null; bypass_of?: string | null }
export interface LanePath { places: string[]; legs: LaneLeg[]; total_transit_d: number; spare_capacity?: number | null; spare_capacity_unit?: string | null; added_transit_d?: number; from_place?: string }
export interface LaneDoc { origin: string; destination: string; method?: string; primary: LanePath | null; removals: Array<{ place_id: string; result: string; added_transit_d: number | null; alt_transit_d: number | null }>; single_points_of_failure: string[]; alternates: LanePath[] }
export interface RerouteDoc { place_id: string; name?: string; inbound_places: string[]; outbound_places: string[]; bypass_legs: LaneLeg[]; alternates: LanePath[] }
export interface ReplaySummary { event_id: string; title?: string; disruption?: Record<string, unknown>; observed_steps: number; per_day_run_state: boolean; stored: boolean }
export interface ReplayDoc { id: string; title?: string; disruption?: Record<string, unknown>; disruption_notes?: string; observed_steps: Array<Record<string, unknown>>; per_day_run_state: boolean; source_document?: string }
export interface VerifyDoc { run_id: string; match: boolean; stored_stream_hash: string; recomputed_stream_hash?: string; graph_version?: string; journal_graph_hash?: string; current_graph_hash?: string; graph_matches_journal?: boolean; error?: string; journal: Record<string, unknown> }
export interface ChannelsDoc { run_id: string; tick: number; ticks: number[]; channels: Array<{ channel: string; state: string; events_at_tick: number; events_total: number }> }
export interface CoverageDoc { graph_version?: string; legend: Array<{ class: string; places: number }>; unclassed: number; places: Array<{ place_id: string; name?: string; kind?: string; tier?: string | null; coverage_class: string | null }> }
export interface LensDoc { run_id: string; goods_class: string; goods_label: string; method?: string; places: Array<{ place_id: string; state: string; share: number | null }>; legs: Array<{ from: string; to: string; state: string }> }
export interface CostDoc { source: string; entries: number; per_action: Array<{ action: string; calls: number; estimated_usd: number; actual_usd: number; accrued_usd: number; transport?: string }>; totals: { estimated_usd: number; actual_usd: number; accrued_usd: number }; kill_switch: { cap_usd: number | null; spent_usd: number | null; accrued_usd?: number | null; cap_reached: boolean | null; transport?: string; model?: string } | null }
export interface WxDoc { event: FeedEvent & { nws?: FeedEvent['nws'] }; mapping: { method: string; primary_place: { place_id: string; name?: string; kind?: string } | null; connected_places: Array<{ place_id: string; name?: string; kind?: string }> }; seed: { source: string; method?: string; scenario: { disruption: Omit<DisruptionSpec, 'capacity_loss'> & { capacity_loss: number | null } } } | null }
export interface EvalDoc { status: string; transport?: string | null; labelled_set: { size: number; provenance?: string; label_fields: string[]; sources: string[] } | null; grades: null }
export interface RunReceipt { run_id: string; status: string; event_count: number; stream_hash: string; parent_run_id?: string; at_seq?: number }
export interface DisruptionSpec { node_id: string; kind: string; capacity_loss: number; duration_d: number; shape: 'step' | 'ramp' | 'pulse'; started_at?: string }
export interface FeedProvenance { source?: string; retrieved_at?: string; mode?: string }
export interface FeedEvent {
  id: string; node_id: string; kind: string; severity: number; status: string;
  started_at?: string; detected_at: string; retrieved_at?: string; source: string;
  // mode is OPTIONAL: a LIVE chip renders only when the served event itself
  // carries a mode (top-level or in its provenance) — never assumed.
  mode?: string;
  headline: string; evidence_url?: string; dedupe_key?: string;
  /** Human-readable source page derived by the server (absent when the source has none). */
  citation?: Citation;
  nws?: { headline?: string; event?: string; area?: string; severity?: string; certainty?: string };
  provenance?: FeedProvenance;
}
/** GET /events response: file order; invalid_lines only when > 0. */
/** GET /graph response: data/graph/graph.json verbatim (nodes / edges). */
export interface GraphData { version?: string; nodes: unknown[]; edges: unknown[] }
/** Where a runtime dataset actually came from — drives the offline labelling. */
export type RemoteSource = 'loading' | 'live' | 'fallback';
export interface HealthEntry { adapter: string; state: 'LIVE' | 'SNAPSHOT' | 'DOWN'; last_success_at: string | null; mode: string; note?: string }
export interface Health { adapters: HealthEntry[]; summary: Record<string, number> }
export interface Scorecard {
  artifact: string; version: number;
  aggregate: {
    replays: number;
    counts: { genuine_misses: number; input_steps: number; non_seed_steps: number; observed_steps: number; unquantified_steps: number };
    coverage: { covered_count: number; distinct_non_seed_nodes: number; fraction: number; uncovered_count: number; definition: string };
    delay_accuracy: { hits: number; misses: number; graded_steps: number; hit_fraction: number; mean_abs_error_d: number; abs_error_sum_d: number; definition: string };
    false_positives: { count: number; definition: string; by_replay: Record<string, string[]> };
  };
  genuine_misses: Array<{ replay_id: string; node_id: string; observed_delay_d: number; predicted_delay_d: number | null; delay_error_d?: number; reason: string; effect: string; evidence_date: string; evidence_source: string }>;
  methodology: Record<string, string>;
  honesty_caveats: string[];
  replays: Array<{ id: string; title: string; seed_node: string; event_count: number; stream_hash: string; run_id: string;
    coverage: { covered_count?: number; distinct_non_seed_nodes?: number; fraction?: number };
    delay_accuracy: { hits?: number; graded_steps?: number; mean_abs_error_d?: number } }>;
  graph: { nodes: number; edges: number; hash: string; version: string };
}
/** GET /llm/status (provider.status(), burn meter). cap_usd is null
 *  when no daily cap is configured in the API's env — never assumed. */
export interface LlmStatus {
  spent_usd: number; accrued_usd?: number; cap_usd: number | null;
  transport: string; model?: string; calls_today?: number;
  cap_reached?: boolean; note?: string;
  /** Served by GET /llm/status for the status-bar META chip. */
  transport_label?: string; live?: boolean; disabled?: boolean;
}
/** POST /llm/explain: narrative for one stored run, cached
 *  server-side by stream_hash — a repeat call for the same stream returns
 *  cached:true at cost 0. The narrative TEXT carries its own transport
 *  labelling (in fixture mode it begins "FIXTURE EXPLAIN (transport=fixture
 *  — …)") and is rendered verbatim, never stripped or paraphrased.
 *  transport/cached are optional here only so the panel can chip exactly
 *  what the payload actually carries — absent fields render no chip. */
export interface LlmExplain {
  run_id: string; stream_hash?: string; narrative: string;
  model?: string; cost_usd?: number; cached?: boolean; transport?: string;
  /** The provenance block and
   *  the post-check outcome; `narrative_source: "template"` = no model text. */
  provenance?: unknown; narrative_source?: string; post_check?: string;
}
/** One grade.evidence[] provenance record (grading rule v1). `source` may
 *  or may not be a URL — it is linked only when it actually is one. */
export interface LedgerEvidence { source: string; retrieved_at: string; mode: string }
export interface LedgerGrade {
  hit_steps: number[]; missed_steps: number[]; ungradeable_steps?: number[];
  delay_error_d: number | null; graded_at?: string; evidence?: LedgerEvidence[];
}
/** One merged GET /llm/ledger entry (read view): predicted_chain is a
 *  verbatim chain array; a grade is attached at most once, absent = OPEN. */
export interface LedgerEntry {
  entry_id: string; locked_at: string; disruption_id: string; run_id: string;
  predicted_chain: CausalEvent[]; grade?: LedgerGrade; supersedes?: string;
}
// Interaction actions: the ONLY interaction surface.
export type C8Action = 'selectNode' | 'openChain' | 'runWhatIf' | 'forkAt' | 'showProof' | 'showMoney' | 'setTheme';
export interface DirectorStep { action: C8Action | 'focusPanel' | 'caption'; payload?: Record<string, unknown>; caption: string; dwell_ms: number }
export type Theme = 'dark' | 'light' | 'retro-dark' | 'retro-light';
// ── money / satwatch / lenses ─────────────────────────────
// GET /money/node/{node_id} — per-node panel shape. Prices are null
// (unpriced) when Stooq is unreachable from the deployment — never faked.
export interface PriceProvenance { source?: string; retrieved_at?: string; mode?: string }
export interface MoneyExposure {
  ticker: string; company: string; exposure_kind: string; filing_url: string;
  last_price: number | null; price_date?: string | null; prev_close?: number | null;
  price_change_pct?: number | null; sparkline?: number[]; price_provenance?: PriceProvenance | null;
}
export interface SimulatedCostPressure {
  value: number; label: string; rule: string;
  provenance?: { source?: string; mode?: string };
}
export interface MoneyNodePanel {
  node_id: string; exposed: MoneyExposure[];
  simulated_cost_pressure?: SimulatedCostPressure;
}
/** GET /money/exposures — all 22 node→company mappings with filing detail. */
export interface MoneyMappingFiling { form: string; filed_date: string; url: string; excerpt?: string }
export interface MoneyMapping {
  node_id: string; ticker: string; company: string; exposure_kind: string;
  filing: MoneyMappingFiling;
}
export interface MoneyExposuresDoc { mappings?: MoneyMapping[]; [k: string]: unknown }
/** GET /satwatch/scans: scan records. counts stays null until the Meta
 *  key is set; the UI shows the AWAITING chip, never counts. */
export interface SatScene { date: string; cloud_pct_aoi?: number; cloud_pct_tile?: number; platform?: string; id?: string; datetime?: string }
export interface SatScan {
  node_id: string; name: string; source?: string; retrieved_at?: string;
  crops: { now: string; baseline: string };
  scene_now: SatScene; scene_baseline: SatScene;
  detection: { counts: unknown | null; status: string; provider?: string; note?: string; countable_class?: string };
}
export interface LensesDoc { lenses: Lens[]; note?: string; version?: string }
export interface Lens { lens_id: string; label: string; node_ids: string[]; basis: string }
/** GET /portwatch — the PortWatch snapshot document verbatim:
 *  {adapter, provenance, records[]}; each record carries node_id +
 *  vessel_count_total (+ per-class counts). Structurally the map
 *  package's PortWatchSnapshot — the app loads it, the map draws it. */
export interface PortWatchProvenance { source: string; retrieved_at: string; mode: string }
export interface PortWatchRecord {
  record_type: string; node_id: string; portname?: string;
  vessel_count_total: number; [k: string]: unknown;
}
export interface PortWatchSnapshot {
  adapter: string; provenance: PortWatchProvenance; records: PortWatchRecord[];
}
// ── end money / satwatch / lenses ──────────────────────────────────────────────────────────
// ── alerts & watchlists (server/alerts) ────────────────
// Rule records as served by GET /alerts/rules: params are the server's
// NORMALIZED shape — every key for the rule's type is present (nulls
// included), exactly as server/alerts/validate.py returns them.
export type AlertRuleType = 'adapter_state' | 'event' | 'run_outcome' | 'ledger';
export type AlertRuleParams =
  | { adapter: string | null; match: 'state_is' | 'state_is_not'; state: 'LIVE' | 'SNAPSHOT' | 'DOWN' }
  | { min_severity: number; node_id: string | null; node_class: 'sea' | 'air' | 'ground' | null; kind: string | null }
  | { metric: 'worst_delay_d' | 'node_delay_d'; node_id: string | null; threshold_d: number }
  | { event: 'lock' | 'grade' };
export interface AlertRule {
  id: string; name: string; type: AlertRuleType; enabled: boolean;
  params: AlertRuleParams; created_at: string; updated_at: string;
}
/** Create/replace body: {name, type, enabled, params} — params carries
 *  exactly the served key set for the type (unknown keys are a 422). */
export interface AlertRuleBody { name: string; type: AlertRuleType; enabled: boolean; params: Record<string, unknown> }
/** One fired-alert record (GET /alerts/feed, newest first). `message` is
 *  the evaluator's own summary of the triggering fact, rendered verbatim. */
export interface FiredAlert {
  id: string; rule_id: string; rule_name?: string; type?: string;
  fired_at: string; message: string; fact: Record<string, unknown>;
}
export interface Watchlist {
  id: string; name: string; node_ids: string[];
  created_at: string; updated_at: string;
}
/** GET /watchlists/{id}/view — per-node state read off served sources
 *  only (graph identity, latest events, chain involvement); the view
 *  computes no new fact. */
export interface WatchlistNodeView {
  node_id: string; name: string | null; mode: string | null; kind: string | null;
  active_events: number;
  latest_events: Array<{ id: string | null; kind: string | null; severity: number | null; status: string | null; detected_at: string | null }>;
  chain: { runs_involved: number; run_ids: string[]; max_delay_d: number | null; latest_run_id: string | null };
}
export interface WatchlistView { watchlist: Watchlist; nodes: WatchlistNodeView[] }
// ── end alerts & watchlists ─────────────────────────────────────────────────────────────
// ── IMPACT layer (server/impact) ──────────────────────
// Every figure the layer serves is one object — {value, unit, class,
// source, as_of, derivation} — so no surface can render a naked number:
// the provenance stamp travels in the payload. value is null exactly
// when class is ABSENT, and an ABSENT figure always carries `reason`.
// Goods classes are never converted or merged across unit classes: the
// run payload's per-class sub-profiles stay separate end to end.
export interface ImpactFigure {
  value: number | null; unit: string | null;
  class: 'SOURCED' | 'MODELLED' | 'HISTORICAL' | 'ABSENT' | string;
  source: string; as_of: string | null; derivation: string; reason?: string;
}
export interface ImpactWindow { start: string; end: string; days_present?: number; window_days: number }
export interface ImpactRunPlace {
  node_id: string; name: string; kind: string;
  capacity: ImpactFigure; capacity_at_risk: ImpactFigure;
  worst_magnitude: ImpactFigure; max_delay: ImpactFigure; hits: number;
}
export interface ImpactVolumeUnit {
  unit: string; places: ImpactRunPlace[];
  daily_volume_at_risk: ImpactFigure; capacity_days_impaired: ImpactFigure;
}
export interface ImpactGoodsProfilePlace { node_id: string; name: string; goods: string; window: ImpactWindow | null }
export interface ImpactGoodsSubProfile {
  state: string; places: ImpactGoodsProfilePlace[];
  /** Served only in the MODELLED state. In the ABSENT state there is
   *  no total: the `mix` slot itself carries the single ABSENT figure
   *  with its reason (see fig.ts — the slot is absent, not null). */
  total_impaired_volume?: ImpactFigure;
  mix: Record<string, ImpactFigure> | ImpactFigure;
}
export interface ImpactTier1Row {
  iso3: string;
  /** Absent on share-only rows: when the daily layer carries no
   *  national total for the country, the row serves iso3 + places +
   *  an ABSENT share figure only — no country name, no tonnage
   *  figures (the tonnage slots are absent, not null). */
  country?: string;
  places: string[];
  affected_tonnage?: ImpactFigure; national_tonnage?: ImpactFigure; share: ImpactFigure;
}
export interface ImpactTier2Row {
  reporter_iso3: string; reporter: string; reporter_code?: number;
  good: string; good_label: string; period: string; basis: string;
  share: ImpactFigure; via: string[];
}
export interface ImpactWatchlistEntry {
  reporter_iso3: string; reporter: string; good: string; good_label: string;
  share: ImpactFigure; via: string[];
}
export interface ImpactAnalogueObservation {
  as_of: string; note: string | null; series: string; source: string;
  /** Exactly one value key per observation, its unit in the key name. */
  value_usd_per_40ft?: number; value_usd_per_feu?: number; value_usd_per_day?: number;
}
export interface ImpactAnalogue {
  id: string; title: string; episode_dates: { start: string; end: string };
  channels: string[]; observations: ImpactAnalogueObservation[];
  shows: string; does_not_show: string; transit_effect?: string;
  /** Present (null) on the Suez 2021 entry: the deliberate absence of a
   *  spot-rate figure, with its reason in rate_observation_note. */
  rate_observation?: number | null; rate_observation_note?: string;
}
export interface ImpactCitation {
  index: string; route: string | null; value: ImpactFigure;
  publisher: string; url: string;
  corroborating_publisher?: string; corroborating_url?: string;
  week?: string; age_days?: number; stale?: boolean; stale_stamp?: string | null;
}
export interface ImpactRates {
  schema: string; state: string; statement: string;
  licence_states: Record<string, string>;
  licence_check: { index?: string; checked_at?: string; checked_by?: string; finding?: string; outcome?: string };
  citations: ImpactCitation[]; citations_note: string;
  /** Plain links to index publishers whose values are not quoted (licence). */
  index_links?: Array<{ index: string; publisher: string; url: string }>;
}
export interface ImpactRun {
  schema: string; run_id: string;
  seed: { node_id: string; name: string; kind: string; started_at: string | null };
  places_affected: number;
  volume_at_risk: { by_unit: ImpactVolumeUnit[]; clearance_horizon: ImpactFigure };
  goods_profile: { port: ImpactGoodsSubProfile; chokepoint: ImpactGoodsSubProfile };
  economy_exposure: {
    tier1_port_share: ImpactTier1Row[];
    tier2_trade_share: ImpactTier2Row[];
    tier2_coverage: {
      routing_sets_version: string; affected_with_sets: string[];
      affected_without_sets: string[];
      /** Present only when at least one affected strait/canal carries a
       *  seeded routing set and the extracts were actually consulted;
       *  in the short form these facts are absent, and render absent. */
      reporters_outside_region_table?: string[];
      extracts_retrieved_at?: string;
      extracts_empty?: Array<{ reporter_code: number; cmd_code: string }>;
    };
    watchlist: { header: string; threshold: number; entries: ImpactWatchlistEntry[] };
  };
  price_context: {
    statement: string; channels_in_run: string[];
    analogues: ImpactAnalogue[]; analogues_note: string; rates: ImpactRates;
  };
}
/** GET /impact/node/{id} goods block — exactly one of the three states:
 *  SOURCED (daily-layer mix), FALLBACK (pinned call mix, shares only) or
 *  ABSENT (the figure object under `goods` carries the reason). Ports
 *  carry import/export/combined mixes; chokepoints carry transit mixes;
 *  FALLBACK carries combined_mix only, with per-class ABSENT figures
 *  where the pinned source has no split (RoRo). */
export interface ImpactNodeGoods {
  state: 'SOURCED' | 'FALLBACK' | 'ABSENT' | string;
  figure_class?: string; window?: ImpactWindow | null;
  /** SOURCED ports: per-class tonnes/day record. FALLBACK: a single
   *  ABSENT figure under the same key (shares only, no tonnage). */
  tonnes_per_day?: Record<string, ImpactFigure> | ImpactFigure;
  total_tonnes_per_day?: ImpactFigure; portcalls_per_day?: ImpactFigure;
  import_mix?: Record<string, ImpactFigure>; export_mix?: Record<string, ImpactFigure>;
  combined_mix?: Record<string, ImpactFigure>;
  transits_per_day_total?: ImpactFigure; transit_tonnes_per_day_total?: ImpactFigure;
  per_class?: Record<string, { transits_per_day: ImpactFigure; transit_tonnes_per_day: ImpactFigure }>;
  transit_mix?: Record<string, ImpactFigure>;
  goods?: ImpactFigure;
}
export interface ImpactNode {
  schema: string; node_id: string; name: string; kind: string; tier?: string;
  country: { iso3: string | null; name: string | null; source: string | null; reason: string | null };
  capacity: ImpactFigure; goods: ImpactNodeGoods;
  country_exposure: ImpactTier1Row[];
}
/** GET /impact/exposure?country={iso3} — the country's Tier-1 structure
 *  (every build port's share of national tonnage, one shared window).
 *  Tier-2 is computed per run, so here it is an ABSENT figure whose
 *  reason says exactly that. */
export interface ImpactExposure {
  schema: string; iso3: string; country: string; window: ImpactWindow;
  country_tonnage: { imports: ImpactFigure; exports: ImpactFigure; ports_in_layer: number; ports_in_build: number };
  ports: Array<{ node_id: string; name: string; tonnage: ImpactFigure; share: ImpactFigure }>;
  tier2: ImpactFigure;
}
/** GET /impact/analogues — the static, versioned analogue file served
 *  verbatim (optionally filtered to one channel). */
export interface ImpactAnaloguesDoc {
  schema: string; version: string; class: string; matching_rule: string;
  channel: string | null; entries: ImpactAnalogue[];
}
// ── end IMPACT ────────────────────────────────────────────────────────
/** GET /runs: metadata only for every stored run —
 *  chain runs, ensemble jobs, ensemble-tree jobs and SIM runs — with a
 *  server-built human title. `facts` is an open served fact bag
 *  (disruption kind, seed place name, event/variant counts, tick
 *  info); consumers display primitive values as served and tolerate
 *  any key's absence. */
export interface RunCatalogueEntry {
  id: string; kind: 'chain' | 'ensemble' | 'ensemble-tree' | 'sim';
  title: string; created_at: string; status: string;
  facts?: Record<string, unknown>;
}
/** GET /impact/goods: the IMPACT goods taxonomy, 8 classes. */
export interface GoodsClass { code: string; label: string }
export type PanelId = 'news' | 'market' | 'map' | 'mapcontent' | 'chain' | 'chaintrace' | 'whatif' | 'proof' | 'ledger' | 'money' | 'satwatch' | 'explain' | 'health' | 'ensemble' | 'alerts' | 'sim' | 'impact' | 'desks' | 'livetwin' | 'reports' | 'exports' | 'satdesk' | 'ports' | 'livefeed' | 'livetape' | 'livectx' | 'dataviewer' | 'laneplanner' | 'porttwin' | 'agent' | 'brief' | 'montecarlo' | 'shipments' | 'watchlists' | 'research' | 'expt' | 'scenariobuilder' | 'replay';
// ── LIVE TWIN ───────────────────────────
// Served only when the server flag is on; the route 404s otherwise and
// the section does not exist in the client at all. Every field is
// rendered as served — feed states, record counts and notes are the
// service's own words, and the air records are per-place aggregates
// with their provenance intact (no per-aircraft positions are served).
export interface LiveTwinFeed {
  id: string; label: string; state: string;
  retrieved_at: string | null; fresh: boolean;
  records: number | null; note?: string | null;
}
export interface LiveTwinAirRecord {
  record_type: string; node_id: string; aircraft_in_bbox: number;
  observed_at_epoch: number;
  provenance: { source: string; retrieved_at: string; mode: string };
}
export interface LiveTwinDoc {
  enabled: true; generated_at: string;
  feeds: LiveTwinFeed[]; air: LiveTwinAirRecord[];
  vessels: { state: string; records: unknown[]; note?: string | null };
}
/** POST /ensemble: run_ensemble's result as served by the
 *  API. `hits` is a COUNT of variant runs out of `variants` (the fixed
 *  27-variant grid) — a recurrence count, NEVER a probability.
 *  `worst_delay_d` is the maximum delay_d (days) recorded for the node
 *  across those variant runs. */
export interface EnsembleBottleneck {
  node_id: string; hits: number; variants: number;
  first_seq_min: number; first_seq_mean: number; worst_delay_d: number;
}
export interface EnsembleResult {
  variants: number; ranked_bottlenecks: EnsembleBottleneck[];
  base_run: { run_id: string; stream_hash: string; event_count: number };
  disruption: DisruptionSpec;
  grid: { queue_sensitivity: number[]; hit_threshold: number[]; duration_mult: number[] };
}
// ── Ensemble statistics ─────────────────────
// POST /ensemble-stats + GET /ensemble-stats/{id}[/results|/branches|
// /branch|/variants/{index}] as served by server/ensstats. Every figure
// is a statistic over the variants actually run; `fire_share` is a COUNT
// of variants expressed as a fraction by the API — the UI renders the
// count form ("reached in X of N variants"), never a bare percentage.
export interface EnsStatsLabelling { title: string; statement: string; weighting: string }
export interface EnsStatsAxis { axis: string; levels: unknown[]; description: string; varies: boolean }
export interface EnsStatsJobSummary {
  id: string; status: string; created_at: string; completed_at: string | null;
  error: { code: string; message: string } | null;
  graph_hash: string; graph_version: string;
  disruption: DisruptionSpec; axes: EnsStatsAxis[]; axes_notes: string[];
  grid_size: number; variants_requested: number; variants_planned: number;
  count_note: string | null; progress: { done: number; planned: number };
  labelling: EnsStatsLabelling;
}
export interface EnsStatBlock {
  count: number; mean: number | null; min: number | null; max: number | null;
  p10: number | null; p50: number | null; p90: number | null;
  percentile_resolution_d: number | null; percentile_capped: boolean;
}
export interface EnsCurveBin { lo: number; hi: number | null; count: number }
export interface EnsOutcomeStats extends EnsStatBlock { curve: EnsCurveBin[] }
export interface EnsStatsResults {
  id: string; status: string; completed_at: string | null;
  disruption: DisruptionSpec; axes: EnsStatsAxis[]; axes_notes: string[];
  grid_size: number; variants_requested: number; count_note: string | null;
  labelling: EnsStatsLabelling; definitions: Record<string, string>;
  variants_run: number; outcomes: Record<string, EnsOutcomeStats>;
  places_reached_any: number; legs_fired_any: number;
  base_variant: { index: number; combination: Record<string, unknown>; outcomes: Record<string, number>; stream_hash: string } | null;
}
export interface EnsPlaceBranch {
  branch: string; node_id: string; name: string | null; kind: string | null; mode: string | null;
  fired: number; variants: number; fire_share: number;
  delay_contribution_d: EnsStatBlock; total_delay_d: EnsStatBlock;
  queue_peak_delay_d: number; first_hit_channels: Record<string, number>;
  first_seq_min: number | null; first_seq_mean: number | null;
}
export interface EnsLegBranch {
  branch: string; from: string; to: string; from_name: string | null; to_name: string | null;
  channel: string; fired: number; variants: number; fire_share: number;
  delay_contribution_d: EnsStatBlock;
}
export type EnsBranch = EnsPlaceBranch | EnsLegBranch;
export interface EnsStatsBranches {
  id: string; status: string; variants_run: number;
  labelling: EnsStatsLabelling; definitions: Record<string, string>;
  places: EnsPlaceBranch[]; legs: EnsLegBranch[];
}
export interface EnsBranchDetail {
  id: string; branch: EnsBranch; variants_fired: number[];
  variants_run: number; labelling: EnsStatsLabelling;
}
export interface EnsVariantRecord {
  index: number; seed: number; combination: Record<string, unknown>;
  run_id: string; stream_hash: string; outcomes: Record<string, number>;
  places_fired: string[]; legs_fired: string[];
}
export interface EnsVariantData { variant: EnsVariantRecord; combination: Record<string, unknown>; events: CausalEvent[] }

// ── Ensemble explorer: the two server modes over the flat
// layer — precomputed combination TREES (server/ensstats/tree_jobs.py)
// and saved EXPLORATIONS (interactive deepening over flat jobs,
// server/ensstats/explorations.py). Both serve the aggregate
// blocks (outcomes / places / legs) under the same labelling; the tree
// member's aggregates arrive in exactly that shape.
export interface EnsAggregates {
  variants: number;
  outcomes: Record<string, EnsOutcomeStats>;
  places: EnsPlaceBranch[];
  legs: EnsLegBranch[];
}
export interface EnsTreeQuoteLevel {
  level: number; axis: string; branching: number; fanout: number;
  runs: number; cumulative: number;
}
export interface EnsTreeQuote {
  branching: number[]; depth: number; levels: EnsTreeQuoteLevel[];
  quoted_runs: number; total_runs_including_base: number;
  budget: number; default_budget: number; hard_max: number;
  within_budget: boolean; within_hard_max: boolean; launchable: boolean;
  requires_raised_budget: boolean;
  est_wall_seconds: number; est_wall_minutes: number;
  est_bytes: number; est_mb: number;
  estimator: { seconds_per_variant: number; bytes_per_variant: number; basis: string };
  arithmetic: string; levels_table: string;
}
export interface EnsTreeAxisStep {
  level: number; axis: string; branching: number; fanout: number;
  rule: string; values: unknown[] | null; span: [number, number] | null;
  description: string;
}
export interface EnsTreeAccounting {
  quoted: number; quoted_variants: number; run: number; expanded: number;
  pruned: number; depth_leaves: number; unexpanded_runs: number;
}
export interface EnsTreeJobSummary {
  id: string; kind: string; status: string;
  created_at: string; completed_at: string | null;
  error: { code: string; message: string } | null;
  graph_hash: string; graph_version: string;
  disruption: DisruptionSpec; axes: EnsStatsAxis[]; axes_notes: string[];
  branching: number[]; depth: number; budget: number | null;
  axis_sequence: EnsTreeAxisStep[] | null;
  quote: EnsTreeQuote | null;
  progress: { done: number; quoted: number };
  accounting: EnsTreeAccounting | null;
  labelling: EnsStatsLabelling;
}
export interface EnsTreeQuoteResponse extends EnsTreeQuote {
  disruption: DisruptionSpec; axes: EnsStatsAxis[]; axes_notes: string[];
  axis_sequence: EnsTreeAxisStep[]; labelling: EnsStatsLabelling;
}
/** One tree member, light (GET .../tree): identity, the refinement
 *  that created it, its combination + outcome vector, expansion state
 *  and fired COUNTS. Aggregates are a separate fetch per member. */
export interface EnsTreeMember {
  index: number; parent: number | null; children: number[];
  level: number; ordinal: number; path: number[];
  axis: string | null; value: unknown;
  combination: Record<string, unknown>;
  outcomes: Record<string, number>;
  expanded: boolean; pruned: boolean;
  prune_reason: { code: string; message: string } | null;
  subtree_run_count: number;
  places_fired_count: number; legs_fired_count: number;
}
export interface EnsTreeView {
  id: string; status: string;
  disruption: DisruptionSpec; axes: EnsStatsAxis[]; axes_notes: string[];
  branching: number[]; depth: number;
  axis_sequence: EnsTreeAxisStep[];
  quote: EnsTreeQuote; accounting: EnsTreeAccounting;
  labelling: EnsStatsLabelling;
  nodes: EnsTreeMember[];
}
export interface EnsTreeMemberDetail {
  id: string; status: string;
  aggregates: EnsAggregates;
  aggregates_basis: string;
  variants_in_subtree: number;
  subtree_note: string;
  labelling: EnsStatsLabelling;
  definitions: Record<string, string>;
}
export interface EnsExplorationEntry {
  id: string; name: string; root_job_id: string;
  created_at: string; updated_at: string;
  expansions: number; members: number;
}
export interface EnsExplorationExpansion {
  parent_job_id: string; parent_variant_index: number;
  child_job_id: string; axis: string; created_at: string;
}
export interface EnsExplorationCrumbStep {
  job_id: string; parent_job_id: string; variant_index: number;
  combination: Record<string, unknown> | null;
  axis: string; axis_description: string | null;
}
export interface EnsExplorationMember {
  job_id: string;
  parent_job_id: string | null; parent_variant_index: number | null;
  status: string | null;
  summary: EnsStatsJobSummary | null;
  varied_axes: string[];
  next_axis: string | null;
  aggregates_ref: string;
  breadcrumb: EnsExplorationCrumbStep[];
}
export interface EnsExplorationDetail {
  id: string; name: string; created_at: string; updated_at: string;
  root_job_id: string;
  expansions: EnsExplorationExpansion[];
  nodes: EnsExplorationMember[];
  labelling: EnsStatsLabelling;
}
export interface EnsExpandQuote {
  axis: string; axis_description: string; values_count: number;
  grid_size: number; count_note: string | null;
}
export interface EnsExpandResult {
  exploration_id: string;
  expansion: EnsExplorationExpansion;
  child: EnsStatsJobSummary;
  quote: EnsExpandQuote;
}
