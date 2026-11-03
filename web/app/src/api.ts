import type { AlertRule, AlertRuleBody, Chain, ChannelsDoc, CostDoc, CoverageDoc, CritDoc, EnsBranchDetail, EnsExpandResult, EnsExplorationDetail, EnsExplorationEntry, EnsStatsBranches, EnsStatsJobSummary, EnsStatsResults, EnsTreeJobSummary, EnsTreeMemberDetail, EnsTreeQuoteResponse, EnsTreeView, EnsVariantData, EnsembleResult, EvalDoc, FeedEvent, FiredAlert, GoodsClass, GraphData, Health, ImpactAnaloguesDoc, ImpactExposure, ImpactNode, ImpactRates, ImpactRun, LaneDoc, LedgerEntry, LensDoc, LensesDoc, LiveTwinDoc, LlmExplain, LlmStatus, MoneyExposuresDoc, MoneyNodePanel, NotHitEntry, PortWatchSnapshot, ReplayDoc, ReplaySummary, RerouteDoc, RunCatalogueEntry, RunReceipt, SatScan, Scorecard, DisruptionSpec, VerifyDoc, Watchlist, WatchlistView, WxDoc } from './types';
import { parseCitation } from './citation';
import { neutraliseDoc } from './contentGuard';
import type { SimEntity, SimMarkersResponse, SimRunDetail, SimRunRequest, SimRunSummary, SimState, SimWeatherResponse } from '../../map/src/sim/types';
import type { LanesGeometryDoc } from '../../map/src/lanes/laneIndex';

/** GET /sim/run/{id}/vehicle/{entity_id}?tick=N: one
 *  vehicle's generated identity plus its state entry at the tick.
 *  Identity is MODELLED throughout (basis is served, never inferred);
 *  sea identities carry the vessel name/class + class size priors,
 *  air identities the tail/type/class/rotation/home hub. `state` is
 *  the state route's entry for the entity VERBATIM (same cached
 *  payload server-side); it is null — with `state_reason` — when the
 *  vehicle has not departed at the tick. `weather` is sampled from
 *  the run's pinned snapshot at the vehicle's position; null (with a
 *  null `weather_source`) when no snapshot is pinned. */
export interface SimVehiclePriors {
  loa_m: [number, number];
  beam_m: [number, number];
  gt: [number, number];
  /** Absent for passenger vessels (their GT range suffices). */
  capacity?: { unit: string; range: [number, number] };
}

export interface SimVehicleIdentity {
  basis: 'MODELLED';
  mode: 'sea' | 'air';
  lane: string;
  from: string;
  to: string;
  speed_kmh: number;
  speed_class: number;
  dwell_h: number;
  /* sea */
  vessel_name?: string;
  vessel_class?: string;
  vessel_priors?: SimVehiclePriors;
  /* air */
  tail?: string;
  aircraft_type?: string;
  cls?: 'cargo' | 'passenger';
  rotation?: string[];
  home_hub?: string;
  /** Cargo-at-hub band only; absent for passenger rotations. */
  dwell_hub_h?: number;
}

export interface SimVehicleWeather {
  wind_kn: number | null;
  wind_dir: number | null;
  wave_m: number | null;
}

export interface SimVehicleDetail {
  sim_run_id: string;
  entity_id: string;
  tick: number;
  sim_time: string;
  identity: SimVehicleIdentity;
  state: SimEntity | null;
  state_reason?: string;
  weather: SimVehicleWeather | null;
  weather_source: string | null;
}

/** What GET /run/{id}/journal says about where a chain run came from. */
export interface RunOrigin {
  engine_rev?: number; engine_label?: string;
  settings?: { live_disruption_id?: string; engine_rev?: number; engine_label?: string };
}

// API base via VITE_API_BASE. Default "" = same origin, which the
// Vite dev/preview proxy forwards to http://localhost:8000 (the API sends no
// CORS headers, so a direct cross-origin default would fail in the browser).
export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? '';

/**
 * Deployment data-mode: a baked-snapshot host (the
 * static artifact's shim, or any future static host serving captured API
 * responses) marks EVERY response with the header
 * `X-Supermap-Data-Mode: SNAPSHOT` (plus `X-Supermap-Captured-At: <ISO>`).
 * The real API never sets it. Fetch success alone therefore does not
 * earn the LIVE label: when the marker is present, the store flips its
 * snapshotMode flag and every fetch-derived LIVE label renders SNAPSHOT
 * instead (see store.tsx + the panel stamps). One-way and additive — no
 * contract change, and a live deployment is byte-for-byte unaffected.
 */
export interface DataModeInfo { snapshot: boolean; capturedAt?: string }
let dataMode: DataModeInfo = { snapshot: false };
const dataModeListeners = new Set<(m: DataModeInfo) => void>();
export function onDataMode(fn: (m: DataModeInfo) => void): () => void {
  dataModeListeners.add(fn); fn(dataMode);
  return () => { dataModeListeners.delete(fn); };
}
function noteDataMode(headers: Headers): void {
  if (dataMode.snapshot) return;
  const mode = headers.get('x-supermap-data-mode');
  if (mode && mode.trim().toUpperCase() === 'SNAPSHOT') {
    dataMode = { snapshot: true, capturedAt: headers.get('x-supermap-captured-at') ?? undefined };
    dataModeListeners.forEach((fn) => fn(dataMode));
  }
}

export class ApiError extends Error {
  code: string;
  /** Client-side qualifier of a code (for example a build that outlived the wait); never shown raw. */
  tag?: string;
  constructor(code: string, message: string, tag?: string) { super(message); this.code = code; this.tag = tag; }
}

/** Poll-loop backoff — the delay before the next poll after
 *  `failures` CONSECUTIVE failures: the base interval while healthy
 *  (failures = 0), doubling per consecutive failure, capped. A success
 *  resets the count to 0, so a recovered loop returns to base rate. */
export function pollBackoffMs(failures: number, baseMs = 1200, capMs = 30000): number {
  if (failures <= 0) return baseMs;
  return Math.min(capMs, baseMs * 2 ** failures);
}

/** Every request carries a timeout — req() was a bare fetch,
 *  so a hung connection hung the caller (and every poll loop built on
 *  it) forever. Default 15 s; callers may override per call with
 *  `timeoutMs` and/or pass their own AbortSignal, which is honoured
 *  alongside (not replaced by) the timeout signal. */
export const DEFAULT_REQ_TIMEOUT_MS = 15000;
export interface ReqInit extends RequestInit { timeoutMs?: number }

/** Status-carrying twin of req(): identical timeout / error
 *  behaviour, but the caller also sees the HTTP status. POST
 *  /sim/run needs it — 201 (stored summary) and 202 (building) are
 *  both success statuses, and req() alone would hide which one the
 *  server answered. */
async function reqWithStatus<T>(path: string, init?: ReqInit): Promise<{ status: number; body: T }> {
  return reqRaw<T>(path, init);
}

async function reqRaw<T>(path: string, init?: ReqInit): Promise<{ status: number; body: T }> {
  const { timeoutMs = DEFAULT_REQ_TIMEOUT_MS, signal: callerSignal, ...rest } = init ?? {};
  const ctrl = new AbortController();
  let timedOut = false;
  const timer = timeoutMs > 0
    ? setTimeout(() => { timedOut = true; ctrl.abort(); }, timeoutMs)
    : null;
  if (callerSignal) {
    if (callerSignal.aborted) ctrl.abort();
    else callerSignal.addEventListener('abort', () => ctrl.abort(), { once: true });
  }
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...rest, headers: rest.body != null ? { 'Content-Type': 'application/json', ...(rest.headers as Record<string, string> | undefined) } : rest.headers, signal: ctrl.signal,
    });
  } catch {
    if (timedOut) {
      throw new ApiError('timeout', 'Request timed out.');
    }
    if (callerSignal?.aborted) {
      throw new ApiError('aborted', 'Request cancelled.');
    }
    throw new ApiError('network', 'Service unreachable.');
  } finally {
    if (timer) clearTimeout(timer);
  }
  noteDataMode(res.headers);
  const body = (await res.json().catch(() => null)) as Record<string, unknown> | null;
  if (!res.ok) {
    const err = (body?.error ?? {}) as { code?: string; message?: string };
    throw new ApiError(err.code ?? `http_${res.status}`, err.message ?? `HTTP ${res.status}`);
  }
  // content scope: known war or political wording of pinned data is shown neutrally (the graph is large and has none)
  return { status: res.status, body: (path.startsWith('/graph') ? body : neutraliseDoc(body)) as T };
}

async function req<T>(path: string, init?: ReqInit): Promise<T> {
  return (await reqRaw<T>(path, init)).body;
}

/** One in-flight POST /llm/explain per run id (see `api.explain`). */
const explainInflight = new Map<string, Promise<LlmExplain>>();

/**
 * Normalise one event exactly as served by GET /events. Provenance fields
 * (source / mode / retrieved_at / provenance) are carried through verbatim —
 * mode stays undefined when the served record carries none, so the UI never
 * invents a LIVE chip. Headline: the served top-level headline when present,
 * else the NWS headline inside the record, else the NWS event + area.
 */
function normalizeEvent(raw: unknown): FeedEvent | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.id !== 'string' || typeof r.node_id !== 'string') return null;
  const nws = (r.nws ?? undefined) as FeedEvent['nws'];
  const provenance = (r.provenance ?? undefined) as FeedEvent['provenance'];
  const headline =
    typeof r.headline === 'string' && r.headline ? r.headline
    : nws?.headline ? nws.headline
    : nws?.event ? `${nws.event}${nws.area ? ` — ${nws.area}` : ''}`
    : `${String(r.kind ?? 'event')} — ${String(r.node_id)}`;
  return {
    id: r.id, node_id: r.node_id,
    kind: String(r.kind ?? 'other'),
    severity: typeof r.severity === 'number' ? r.severity : 0,
    status: String(r.status ?? 'unknown'),
    started_at: typeof r.started_at === 'string' ? r.started_at : undefined,
    detected_at: String(r.detected_at ?? ''),
    retrieved_at: typeof r.retrieved_at === 'string' ? r.retrieved_at : provenance?.retrieved_at,
    source: String(r.source ?? provenance?.source ?? 'unknown'),
    mode: typeof r.mode === 'string' ? r.mode : provenance?.mode,
    headline,
    evidence_url: typeof r.evidence_url === 'string' ? r.evidence_url : undefined,
    citation: parseCitation(r.citation),
    dedupe_key: typeof r.dedupe_key === 'string' ? r.dedupe_key : undefined,
    nws, provenance,
  };
}

/** Contract check for GET /portwatch: the document is
 *  {adapter, provenance, records[]} — records are carried verbatim;
 *  only the envelope is validated. */
export function parsePortWatch(body: unknown): PortWatchSnapshot {
  const b = body as PortWatchSnapshot | null;
  if (!b || typeof b !== 'object' || typeof b.adapter !== 'string' || !Array.isArray(b.records)) {
    throw new ApiError('bad_portwatch', 'The vessel activity service returned no activity records');
  }
  return b;
}

/** Create-then-poll sim creation: POST /sim/run answers
 *  201 + the stored summary when the run already exists, or 202
 *  {sim_run_id, status:'building'} when a new build starts;
 *  GET /sim/run/{id}/status then reports building / ready (with the
 *  summary) / failed (with the served error) until the build settles.
 *  Unknown ids are a 404 unknown_sim_run, surfaced via ApiError. */
export type SimRunStatusResponse =
  | { sim_run_id: string; status: 'ready'; summary: SimRunSummary }
  | { sim_run_id: string; status: 'building'; started_at: string }
  | { sim_run_id: string; status: 'failed'; error: { code: string; message: string } };

export interface SimRunBuildingResponse { sim_run_id: string; status: 'building' }

export interface SimRunSettledOptions {
  /** Called once with the building run's id when POST answers 202. */
  onBuilding?: (simRunId: string) => void;
  intervalMs?: number;
  deadlineMs?: number;
}
export const SIM_RUN_POLL_INTERVAL_MS = 1500;
export const SIM_RUN_SETTLE_DEADLINE_MS = 600_000;

const settleDelay = (ms: number): Promise<void> => new Promise((r) => { setTimeout(r, ms); });

export const api = {
  /** Research workbench: historical archive queries, descriptive only. */
  researchEpisodes: () => req<unknown>('/research/episodes'),
  researchAssemble: (body: unknown) => req<unknown>('/research/assemble', { method: 'POST', body: JSON.stringify(body), timeoutMs: 45000 }),
  researchAnalyze: (body: unknown) => req<unknown>('/research/analyze', { method: 'POST', body: JSON.stringify(body), timeoutMs: 45000 }),
  researchScan: (body: unknown) => req<unknown>('/research/scenes/scan', { method: 'POST', body: JSON.stringify(body), timeoutMs: 45000 }),
  health: () => req<Health>('/health'),
  scorecard: () => req<Scorecard>('/scorecard'),
  /** GET /events: {events: [...]} in file order + optional invalid_lines. */
  events: async (): Promise<{ events: FeedEvent[]; invalidLines: number }> => {
    const body = await req<{ events?: unknown[]; invalid_lines?: number }>('/events');
    const events = (body.events ?? []).map(normalizeEvent).filter((e): e is FeedEvent => e !== null);
    return { events, invalidLines: body.invalid_lines ?? 0 };
  },
  /** GET /graph/lanes/geometry?scope=sea: one lane polyline per pair of places a sea leg
   *  joins, MODELLED, at world-zoom (coarse) or full detail. ETag-revalidated by the browser. */
  laneGeometry: (detail: 'coarse' | 'full') =>
    req<LanesGeometryDoc>(`/graph/lanes/geometry?scope=sea&detail=${detail}`, { timeoutMs: 60000 }),
  /** GET /graph: data/graph/graph.json verbatim. */
  graph: async (): Promise<GraphData> => {
    const g = await req<GraphData>('/graph');
    if (!g || !Array.isArray(g.nodes) || !Array.isArray(g.edges)) {
      throw new ApiError('bad_graph', 'The network service returned no places or lanes');
    }
    return g;
  },
  /** GET /llm/status: {spent_usd, cap_usd, transport, …} verbatim. */
  llmStatus: () => req<LlmStatus>('/llm/status'),
  /** GET /satwatch/sam/usage: the SAM call meter (separate from the LLM meter). */
  samUsage: () => req<{ calls_today?: number }>('/satwatch/sam/usage'),
  /** POST /llm/explain: {run_id} -> {run_id, stream_hash, narrative,
   *  model, cost_usd, cached, transport} verbatim. Cached server-side by
   *  stream_hash, so a repeat call for the same run costs $0. */
  explain: (runId: string): Promise<LlmExplain> => {
    /* Two windows (the Explain step inspector and the Map layers narrative)
     * ask for the same run when it loads. The server caches only after the
     * first answer lands, so on a LIVE model both would be paid for. One
     * in-flight request per run id is shared; the next call is a cache hit. */
    const hit = explainInflight.get(runId);
    if (hit) return hit;
    // a recorded Replay chain was never stored as a run: there is nothing to narrate
    if (runId.startsWith('replay_')) return Promise.reject(new ApiError('not_stored', 'A recorded Replay chain has no stored run'));
    const p = req<LlmExplain>('/llm/explain', { method: 'POST', body: JSON.stringify({ run_id: runId }) })
      .finally(() => { explainInflight.delete(runId); });
    explainInflight.set(runId, p);
    return p;
  },
  /** GET /llm/ledger: {entries: [...]} with grade{} merged on. */
  llmLedger: async (): Promise<LedgerEntry[]> => {
    const body = await req<{ entries?: unknown }>('/llm/ledger');
    if (!Array.isArray(body.entries)) {
      throw new ApiError('bad_ledger', 'The ledger service returned no entries');
    }
    return body.entries as LedgerEntry[];
  },
  /** GET /money/node/{node_id}: per-node money panel —
   *  exposed[] companies with filing_url + null prices when unpriced. */
  moneyNode: (nodeId: string) => req<MoneyNodePanel>(`/money/node/${encodeURIComponent(nodeId)}`),
  /** GET /money/exposures: the exposures doc verbatim (22 mappings). */
  moneyExposures: () => req<MoneyExposuresDoc>('/money/exposures'),
  /** GET /satwatch/scans: {scans: [...]} — 5 scan records. */
  satwatchScans: async (): Promise<SatScan[]> => {
    const body = await req<{ scans?: unknown }>('/satwatch/scans');
    if (!Array.isArray(body.scans)) throw new ApiError('bad_satwatch', 'The satellite scan service returned no scans');
    return body.scans as SatScan[];
  },
  /** Crop PNG URL for one /satwatch/scans record (served via the Vite proxy). */
  satCropUrl: (filename: string) => `${API_BASE}/satwatch/crops/${encodeURIComponent(filename)}`,
  /** GET /lenses: the lenses doc verbatim (6 curated lenses). */
  lenses: async (): Promise<LensesDoc> => {
    const body = await req<LensesDoc>('/lenses');
    if (!body || !Array.isArray(body.lenses)) throw new ApiError('bad_lenses', 'The lens service returned no lenses');
    return body;
  },
  /** GET /portwatch: the PortWatchSnapshot document verbatim.
   *  A body without a records array is a contract failure, not an
   *  empty snapshot — the store then leaves the layer empty and the
   *  legend hidden rather than claiming undrawn dots. */
  portwatch: async (): Promise<PortWatchSnapshot> => parsePortWatch(await req<unknown>('/portwatch')),
  chain: (runId: string) => req<Chain>(`/chain/${encodeURIComponent(runId)}`),
  /** GET /runs: the stored-run catalogue, metadata only —
   *  {runs: [{id, kind, title, created_at, status, facts}]} verbatim.
   *  The catalogue build measured ~14 s server-side —
   *  at the default 15 s timeout a healthy-but-slow build is
   *  indistinguishable from a hang and the attempt aborts, which is
   *  why this call carries a 60 s budget; the
   *  store's bounded recovery cycle governs how often it is spent. */
  runsCatalogue: () => req<{ runs: RunCatalogueEntry[] }>('/runs', { timeoutMs: 60_000 }),
  /** GET /impact/goods: the IMPACT goods taxonomy —
   *  {goods: [{code, label}]}, 8 classes, verbatim. */
  impactGoods: () => req<{ goods: GoodsClass[] }>('/impact/goods'),
  /** GET /livetwin/feeds: the live-twin feed board — feed
   *  states, per-place air aggregates and the vessel state, as served.
   *  Flag off on the server is a 404 (live_twin_off): the caller's
   *  failure path is the section's absence, never an empty board. */
  liveTwinFeeds: () => req<LiveTwinDoc>('/livetwin/feeds'),
  /** GET /livetwin/status: {enabled}, always 200. The boot reads the
   *  flag here BEFORE asking for the feed board, so a flag-off boot makes
   *  no request that answers 404 (nothing for the console to log). An
   *  older server without the route is probed the old way. */
  liveTwinStatus: () => req<{ enabled?: boolean }>('/livetwin/status'),
  /* ── Analysis Desks (/desks/*) ─────────────── */
  desksWhynot: (runId: string) => req<{ run_id: string; not_hit: NotHitEntry[] }>(`/desks/whynot/${encodeURIComponent(runId)}`),
  desksCrit: () => req<CritDoc>('/desks/crit'),
  desksCritRun: () => req<{ job: CritDoc['job'] }>('/desks/crit/run', { method: 'POST' }),
  desksLane: (origin: string, destination: string) =>
    req<LaneDoc>('/desks/lane', { method: 'POST', body: JSON.stringify({ origin, destination }) }),
  desksReroute: (placeId: string, destination?: string) =>
    req<RerouteDoc>('/desks/reroute', { method: 'POST', body: JSON.stringify({ place_id: placeId, destination }) }),
  desksReplays: () => req<{ replays: ReplaySummary[] }>('/desks/replays'),
  desksReplay: (eventId: string) => req<ReplayDoc>(`/desks/replay/${encodeURIComponent(eventId)}`),
  desksVerify: (runId: string) => req<VerifyDoc>(`/desks/verify/${encodeURIComponent(runId)}`, { method: 'POST' }),
  desksChannels: (runId: string, tick: number) => req<ChannelsDoc>(`/desks/channels/${encodeURIComponent(runId)}?tick=${tick}`),
  desksCoverage: () => req<CoverageDoc>('/desks/coverage'),
  desksLens: (runId: string, goodsClass: string) => req<LensDoc>(`/desks/lens/${encodeURIComponent(runId)}?class=${encodeURIComponent(goodsClass)}`),
  desksCost: () => req<CostDoc>('/desks/cost'),
  desksWx: (eventId: string) => req<WxDoc>(`/desks/wx/${encodeURIComponent(eventId)}`),
  desksEval: () => req<EvalDoc>('/desks/eval'),
  reportUrl: (runId: string, format: 'md' | 'html') => `${API_BASE}/desks/report/${encodeURIComponent(runId)}?format=${format}`,
  packUrl: (runId: string) => `${API_BASE}/desks/pack/${encodeURIComponent(runId)}`,
  runScenario: (disruption: DisruptionSpec) =>
    req<RunReceipt>('/run', { method: 'POST', body: JSON.stringify({ source: 'scenario', scenario: { disruption } }) }),
  runLive: (disruptionId: string) =>
    req<RunReceipt>('/run', { method: 'POST', body: JSON.stringify({ source: 'live', live: { disruption_id: disruptionId } }) }),
  /** GET /run/{id}/journal, read only for a run's ORIGIN: the engine
   *  revision and (live runs only) the disruption id it was seeded from. */
  runOrigin: (runId: string) => req<RunOrigin>(`/run/${encodeURIComponent(runId)}/journal`),
  fork: (runId: string, atSeq: number, overrides: Record<string, unknown>) =>
    req<RunReceipt>('/fork', { method: 'POST', body: JSON.stringify({ run_id: runId, at_seq: atSeq, overrides }) }),
  /** POST /ensemble: the fixed 27-variant causal ensemble
   *  for one disruption spec — ranked bottlenecks with hit COUNTS. */
  ensemble: (disruption: DisruptionSpec) =>
    req<EnsembleResult>('/ensemble', { method: 'POST', body: JSON.stringify({ source: 'scenario', scenario: { disruption } }) }),
  /** Ensemble statistics: variant jobs over the declared
   *  axes — create/poll a job, then read its aggregates, its per-place /
   *  per-leg branches, one branch's firing variants, and one variant's
   *  replayed chain (hash-verified server-side before it is served). */
  ensStatsCreate: (disruption: DisruptionSpec, count: number) =>
    req<EnsStatsJobSummary>('/ensemble-stats', { method: 'POST', body: JSON.stringify({ disruption, count }) }),
  ensStatsList: () => req<{ jobs: EnsStatsJobSummary[] }>('/ensemble-stats'),
  ensStatsJob: (id: string) => req<EnsStatsJobSummary>(`/ensemble-stats/${encodeURIComponent(id)}`),
  ensStatsResults: (id: string) => req<EnsStatsResults>(`/ensemble-stats/${encodeURIComponent(id)}/results`),
  ensStatsBranches: (id: string) => req<EnsStatsBranches>(`/ensemble-stats/${encodeURIComponent(id)}/branches`),
  ensStatsBranch: (id: string, branch: string) =>
    req<EnsBranchDetail>(`/ensemble-stats/${encodeURIComponent(id)}/branch?branch=${encodeURIComponent(branch)}`),
  ensStatsVariant: (id: string, index: number) =>
    req<EnsVariantData>(`/ensemble-stats/${encodeURIComponent(id)}/variants/${index}`),
  /** Ensemble TREES (precomputed mode): quote BEFORE anything runs —
   *  a quote persists nothing; create refuses an over-budget tree
   *  422 tree_too_large with the per-level table in the message and
   *  persists nothing. Members publish whole at completion: the
   *  light tree view, one member's subtree aggregates, and one
   *  member's replayed chain (hash-verified server-side). */
  ensTreeQuote: (body: { disruption: DisruptionSpec; branching: number[]; budget?: number }) =>
    req<EnsTreeQuoteResponse>('/ensemble-stats/tree/quote', { method: 'POST', body: JSON.stringify(body) }),
  ensTreeCreate: (body: { disruption: DisruptionSpec; branching: number[]; budget?: number }) =>
    req<EnsTreeJobSummary>('/ensemble-stats/tree', { method: 'POST', body: JSON.stringify(body) }),
  ensTreeList: () => req<{ jobs: EnsTreeJobSummary[] }>('/ensemble-stats/tree'),
  ensTreeJob: (id: string) => req<EnsTreeJobSummary>(`/ensemble-stats/tree/${encodeURIComponent(id)}`),
  ensTreeView: (id: string) => req<EnsTreeView>(`/ensemble-stats/tree/${encodeURIComponent(id)}/tree`),
  ensTreeMember: (id: string, index: number) =>
    req<EnsTreeMemberDetail>(`/ensemble-stats/tree/${encodeURIComponent(id)}/nodes/${index}`),
  ensTreeVariant: (id: string, index: number) =>
    req<EnsVariantData>(`/ensemble-stats/tree/${encodeURIComponent(id)}/variants/${index}`),
  /** Ensemble EXPLORATIONS (interactive deepening): a saved record of
   *  pointers + display metadata only; its members are ordinary flat
   *  jobs whose aggregates stay at the flat results route. Expand
   *  returns the child summary plus the child's own quote (grid size
   *  + count note, verbatim). */
  ensExplorations: () => req<{ explorations: EnsExplorationEntry[] }>('/ensemble-stats/explorations'),
  ensExplorationCreate: (name: string | null, rootJobId: string) =>
    req<EnsExplorationDetail>('/ensemble-stats/explorations', { method: 'POST', body: JSON.stringify({ name, root_job_id: rootJobId }) }),
  ensExploration: (id: string) => req<EnsExplorationDetail>(`/ensemble-stats/explorations/${encodeURIComponent(id)}`),
  ensExplorationRename: (id: string, name: string) =>
    req<EnsExplorationDetail>(`/ensemble-stats/explorations/${encodeURIComponent(id)}/rename`, { method: 'POST', body: JSON.stringify({ name }) }),
  ensExplorationExpand: (id: string, parentJobId: string, variantIndex: number, values: unknown[]) =>
    req<EnsExpandResult>(`/ensemble-stats/explorations/${encodeURIComponent(id)}/expand`, {
      method: 'POST', body: JSON.stringify({ parent_job_id: parentJobId, variant_index: variantIndex, values }),
    }),
  /** Alerts (server/alerts): rules CRUD. Validation is strict
   *  server-side — a bad body is a 422 in the standard
   *  {error:{code,message}} shape, surfaced via ApiError verbatim. */
  alertRules: () => req<{ rules: AlertRule[] }>('/alerts/rules'),
  createRule: (body: AlertRuleBody) =>
    req<AlertRule>('/alerts/rules', { method: 'POST', body: JSON.stringify(body) }),
  replaceRule: (id: string, body: AlertRuleBody) =>
    req<AlertRule>(`/alerts/rules/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteRule: (id: string) =>
    req<{ deleted: boolean; rule_id: string }>(`/alerts/rules/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  /** GET /alerts/feed: {alerts: [...]} newest first; limit 1..500. */
  alertsFeed: (limit = 50) => req<{ alerts: FiredAlert[] }>(`/alerts/feed?limit=${limit}`),
  /** Watchlists: CRUD + node add/remove + the per-node state view. */
  watchlists: () => req<{ watchlists: Watchlist[] }>('/watchlists'),
  createWatchlist: (name: string) =>
    req<Watchlist>('/watchlists', { method: 'POST', body: JSON.stringify({ name }) }),
  renameWatchlist: (id: string, name: string) =>
    req<Watchlist>(`/watchlists/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify({ name }) }),
  deleteWatchlist: (id: string) =>
    req<{ deleted: boolean; watchlist_id: string }>(`/watchlists/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  addWatchlistNode: (id: string, nodeId: string) =>
    req<Watchlist>(`/watchlists/${encodeURIComponent(id)}/nodes`, { method: 'POST', body: JSON.stringify({ node_id: nodeId }) }),
  removeWatchlistNode: (id: string, nodeId: string) =>
    req<Watchlist>(`/watchlists/${encodeURIComponent(id)}/nodes/${encodeURIComponent(nodeId)}`, { method: 'DELETE' }),
  watchlistView: (id: string) => req<WatchlistView>(`/watchlists/${encodeURIComponent(id)}/view`),
  /** Simulation: run creation is
   *  idempotent (identical body ⇒ identical sim_ id ⇒ stored summary);
   *  state(tick) is a pure function of the stored run — entity
   *  positions come ONLY from these payloads. Weather absence is a
   *  200 {weather: null, reason} state, not an error. */
  simRun: (body: SimRunRequest) =>
    req<SimRunSummary>('/sim/run', { method: 'POST', body: JSON.stringify(body) }),
  /** GET /sim/run/{id}/status — the create-then-poll status route
   *  (see SimRunStatusResponse). Each call rides req()'s standard
   *  request timeout. */
  simRunStatus: (simRunId: string) =>
    req<SimRunStatusResponse>(`/sim/run/${encodeURIComponent(simRunId)}/status`),
  /** Create a sim run and wait for it to settle, in-session: POSTs
   *  via the same route as simRun; a 201 (or any non-202 success —
   *  older stubs answer 200) resolves the summary immediately; a 202
   *  calls onBuilding(sim_run_id) and polls simRunStatus every
   *  intervalMs until ready → resolve the summary, or failed →
   *  throw an ApiError carrying the served code + message. Past
   *  deadlineMs the helper throws ApiError('timeout', …) — honestly:
   *  creation is still running server-side and the run can be
   *  reopened from search when it is ready. Raw simRun above is
   *  unchanged for existing consumers; this helper sees the
   *  202 body through reqWithStatus, which req() alone would hide. */
  simRunSettled: async (body: SimRunRequest, opts: SimRunSettledOptions = {}): Promise<SimRunSummary> => {
    const intervalMs = opts.intervalMs ?? SIM_RUN_POLL_INTERVAL_MS;
    const deadlineMs = opts.deadlineMs ?? SIM_RUN_SETTLE_DEADLINE_MS;
    const { status, body: posted } = await reqWithStatus<SimRunSummary | SimRunBuildingResponse>(
      '/sim/run', { method: 'POST', body: JSON.stringify(body) });
    if (status !== 202) return posted as SimRunSummary;
    const simRunId = (posted as SimRunBuildingResponse).sim_run_id;
    if (!simRunId) {
      throw new ApiError('bad_sim_run', 'Run not followed.');
    }
    opts.onBuilding?.(simRunId);
    const startedAt = Date.now();
    const timeoutError = () => new ApiError('timeout',
      'Run still building. Reopen from search when ready.', 'build');
    for (;;) {
      if (Date.now() - startedAt >= deadlineMs) throw timeoutError();
      await settleDelay(intervalMs);
      if (Date.now() - startedAt >= deadlineMs) throw timeoutError();
      const st = await api.simRunStatus(simRunId);
      if (st.status === 'ready') return st.summary;
      if (st.status === 'failed') throw new ApiError(st.error.code, st.error.message);
      /* status 'building': keep polling until the deadline. */
    }
  },
  simRunDetail: (simRunId: string) => req<SimRunDetail>(`/sim/run/${encodeURIComponent(simRunId)}`),
  simState: (simRunId: string, tick: number) =>
    req<SimState>(`/sim/state/${encodeURIComponent(simRunId)}?tick=${tick}`),
  /** GET /sim/run/{id}/vehicle/{entity_id}?tick=N: one
   *  vehicle's MODELLED identity + its state entry at the tick +
   *  snapshot weather at its position. Unknown run / vehicle are
   *  404s (unknown_sim_run / unknown_sim_vehicle); tick validation
   *  mirrors the state route (422 invalid_request). */
  fetchSimVehicle: (simRunId: string, entityId: string, tick: number) =>
    req<SimVehicleDetail>(`/sim/run/${encodeURIComponent(simRunId)}/vehicle/${encodeURIComponent(entityId)}?tick=${tick}`),
  /** GET /sim/run/{id}/markers: timeline markers
   *  derived only from stored facts — the timeline renders exactly these. */
  simMarkers: (simRunId: string) =>
    req<SimMarkersResponse>(`/sim/run/${encodeURIComponent(simRunId)}/markers?labels=place`),
  simWeather: (simRunId: string) => req<SimWeatherResponse>(`/sim/weather/${encodeURIComponent(simRunId)}`),
  /** IMPACT layer (server/impact): every figure in these payloads is a
   *  {value, unit, class, source, as_of, derivation} object — absent data
   *  is served as an ABSENT figure (value null + reason), never a zero.
   *  No payload carries a cross-class aggregate or a forward price
   *  figure; the panel renders exactly what is served. */
  impactRun: (runId: string) => req<ImpactRun>(`/impact/run/${encodeURIComponent(runId)}`),
  impactNode: (nodeId: string) => req<ImpactNode>(`/impact/node/${encodeURIComponent(nodeId)}`),
  impactExposure: (iso3: string) => req<ImpactExposure>(`/impact/exposure?country=${encodeURIComponent(iso3)}`),
  impactAnalogues: () => req<ImpactAnaloguesDoc>('/impact/analogues'),
  impactRates: () => req<ImpactRates>('/impact/rates'),
};
