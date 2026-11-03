// Pure logic for the SIM control
// surface: timeline/playhead arithmetic, the per-panel LINK
// state machine, the per-place status-board derivation,
// the volume-at-risk baseline diff and the provenance tier split.
// Everything here is a pure function of served payloads —
// the run detail, state(tick) payloads, markers and the served graph
// place records. No fetch, no clock reads, no invented fields: where a
// served field is absent the derivation reports it as absent (the UI
// renders the honest labelled state) rather than substituting a value.

import type {
  SimEntity,
  SimLane,
  SimMarker,
  SimMarkerKind,
  SimRunDetail,
  SimState,
  SimTickSize,
} from "./types";

/* ── tick model ───────────────────────────────────────────────── */

export interface SimTickSpec {
  seconds: number;
  defaultTicks: number;
  maxTicks: number;
  /** Readout label, e.g. "1H" — month is a fixed 30-day step. */
  label: string;
  /** Segmented-control letter. */
  letter: string;
}

export const TICK_SPECS: Record<SimTickSize, SimTickSpec> = {
  hour: { seconds: 3600, defaultTicks: 168, maxTicks: 384, label: "1H", letter: "H" },
  day: { seconds: 86400, defaultTicks: 60, maxTicks: 180, label: "1D", letter: "D" },
  week: { seconds: 604800, defaultTicks: 26, maxTicks: 52, label: "1W", letter: "W" },
  month: { seconds: 2592000, defaultTicks: 12, maxTicks: 24, label: "1M = 30D", letter: "M" },
};

export const tickSecondsOf = (run: SimRunDetail): number =>
  run.tick_seconds ?? TICK_SPECS[run.journal.tick].seconds;

export const runStartMs = (run: SimRunDetail): number =>
  Date.parse(run.start_at ?? run.journal.start_at);

export const runEndMs = (run: SimRunDetail): number =>
  runStartMs(run) + run.journal.ticks * tickSecondsOf(run) * 1000;

/* ── timeline / playhead ───────────────────────────────────────── */

/** Playback STEP granularity: how much sim-time one step (or
 *  one play-second at 1×) advances the playhead. It is NEVER a claim
 *  about the run's resolution — the run tick stays in the readout. */
export type SimGranularity = "MINUTE" | "HOUR" | "DAY";
export const GRANULARITIES: SimGranularity[] = ["MINUTE", "HOUR", "DAY"];
export const GRANULARITY_SECONDS: Record<SimGranularity, number> = {
  MINUTE: 60,
  HOUR: 3600,
  DAY: 86400,
};

const EPS = 1e-9;

/** The playhead is a fractional tick position in [0, ticks-1]. The
 *  exact state in force is the FLOOR tick's (discrete facts step
 *  at tick boundaries, never interpolated). */
export const floorTickOf = (playhead: number): number => Math.floor(playhead + EPS);

export const isInterpolated = (playhead: number): boolean =>
  playhead - Math.floor(playhead) > 1e-6;

export const clampPlayhead = (playhead: number, ticks: number): number =>
  ticks <= 0 ? 0 : Math.max(0, Math.min(ticks - 1, playhead));

/** One granularity STEP from `playhead`, in ticks (may be fractional
 *  when the step is smaller than the run tick — positions then render
 *  interpolated and the readout carries the INTERPOLATED tag). */
export function stepPlayhead(
  playhead: number,
  dir: 1 | -1,
  granularity: SimGranularity,
  tickSeconds: number,
  ticks: number,
): number {
  const delta = GRANULARITY_SECONDS[granularity] / tickSeconds;
  return clampPlayhead(playhead + dir * delta, ticks);
}

/** Playback advance over `elapsedMs` of wall time at `speed`×: one
 *  play-second advances the playhead by one granularity step. */
export function advancePlayhead(
  playhead: number,
  elapsedMs: number,
  speed: number,
  granularity: SimGranularity,
  tickSeconds: number,
  ticks: number,
): number {
  const delta =
    (GRANULARITY_SECONDS[granularity] / tickSeconds) * speed * (elapsedMs / 1000);
  return clampPlayhead(playhead + delta, ticks);
}

/** Sim-clock instant for a (possibly fractional) playhead. */
export const playheadMs = (run: SimRunDetail, playhead: number): number =>
  runStartMs(run) + playhead * tickSecondsOf(run) * 1000;

export interface SimClockReadout {
  /** "21 Oct 26 · 14:00 UTC" */
  dateTime: string;
  /** e.g. "Run tick 1D" — the run's own resolution, always shown. */
  tickLabel: string;
  /** Floor tick index (the exact state in force). */
  tickIndex: number;
  interpolated: boolean;
}

const MONS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const fmtSimDayTime = (ms: number): string => {
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "—";
  const p2 = (n: number) => String(n).padStart(2, "0");
  return `${p2(d.getUTCDate())} ${MONS[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)} · ${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())} UTC`;
};

export function simClockReadout(run: SimRunDetail, playhead: number): SimClockReadout {
  return {
    dateTime: fmtSimDayTime(playheadMs(run, playhead)),
    tickLabel: `Run tick ${TICK_SPECS[run.journal.tick].label}`,
    tickIndex: floorTickOf(playhead),
    interpolated: isInterpolated(playhead),
  };
}

/** Position interpolation between two adjacent EXACT tick states:
 * only EN_ROUTE lon/lat move (linearly — movement along a
 *  segment is linear between events); every discrete fact (state,
 *  queue membership, waits, node series, disruptions, hash) stays the
 *  floor tick's verbatim. Entities absent from either state keep the
 *  floor position. The input states are never mutated. */
export function interpolatePositions(
  floorState: SimState,
  nextState: SimState,
  frac: number,
): SimState {
  if (frac <= 0) return floorState;
  const f = Math.min(1, frac);
  const nextById = new Map(nextState.entities.map((e) => [e.id, e]));
  const entities = floorState.entities.map((e) => {
    if (e.state !== "EN_ROUTE" || e.lon === null || e.lat === null) return e;
    const n = nextById.get(e.id);
    if (!n || n.lon === null || n.lat === null) return e;
    // Antimeridian-safe: take the short way around the globe.
    let dLon = n.lon - e.lon;
    if (dLon > 180) dLon -= 360;
    if (dLon < -180) dLon += 360;
    let lon = e.lon + dLon * f;
    if (lon > 180) lon -= 360;
    if (lon < -180) lon += 360;
    return { ...e, lon: round5(lon), lat: round5(e.lat + (n.lat - e.lat) * f) };
  });
  return { ...floorState, entities };
}
const round5 = (v: number) => Math.round(v * 1e5) / 1e5;

/* ── markers ─────────────────────────────────────────────────── */

export const MARKER_KIND_LABEL: Record<SimMarkerKind, string> = {
  disruption_onset: "Disruption begins",
  disruption_expiry: "Disruption ends",
  queue_peak: "Queue peak",
  congested_arrival: "Congested arrival",
  chain_event: "Chain event",
};

/** Marker x-position on the ruler, as a fraction of the run span. */
export const markerFraction = (marker: SimMarker, ticks: number): number =>
  ticks <= 1 ? 0 : Math.max(0, Math.min(1, marker.tick / (ticks - 1)));

/* ── LINK state machine ──────────────────────────────────────── */

/** Per-panel link state. LINKED panels follow the master tick +
 *  selection from the sim bus; UNLINKED panels freeze on the tick +
 *  selection + run captured at unlink time (comparison mode). Frozen
 *  state is bound to its run: switching runs re-links every panel. */
export interface PanelLinkState {
  linked: boolean;
  frozenTick: number | null;
  frozenNodeId: string | null;
  frozenRunId: string | null;
  /** ISO sim time of the frozen tick — shown prominently, never the
   *  wall-clock moment of freezing. */
  frozenAtLabel: string | null;
}

export const LINKED: PanelLinkState = {
  linked: true,
  frozenTick: null,
  frozenNodeId: null,
  frozenRunId: null,
  frozenAtLabel: null,
};

export interface MasterView {
  runId: string;
  playhead: number;
  nodeId: string | null;
}

/** Toggle LINKED → UNLINKED: freeze exactly the current master view. */
export function freezeLink(master: MasterView, frozenAtLabel: string): PanelLinkState {
  return {
    linked: false,
    frozenTick: floorTickOf(master.playhead),
    frozenNodeId: master.nodeId,
    frozenRunId: master.runId,
    frozenAtLabel,
  };
}

/** Re-link: snap back to the master — no memory of the frozen view. */
export const relink = (): PanelLinkState => ({ ...LINKED });

/** Switching runs re-links all panels. */
export const onRunSwitch = (): PanelLinkState => ({ ...LINKED });

export interface PanelView {
  tick: number;
  nodeId: string | null;
  frozen: boolean;
  frozenAtLabel: string | null;
}

/** The view a panel renders: its frozen view while unlinked (and the
 *  frozen run still current), else the master view. */
export function effectivePanelView(link: PanelLinkState, master: MasterView): PanelView {
  if (!link.linked && link.frozenRunId === master.runId && link.frozenTick !== null) {
    return {
      tick: link.frozenTick,
      nodeId: link.frozenNodeId,
      frozen: true,
      frozenAtLabel: link.frozenAtLabel,
    };
  }
  return {
    tick: floorTickOf(master.playhead),
    nodeId: master.nodeId,
    frozen: false,
    frozenAtLabel: null,
  };
}

/* ── status board derivation ─────────────────────────────────── */

/** The served place record the board derivation reads (from GET /graph
 *  via the app store). Every field is optional: absence is reported,
 *  never filled in. */
export interface SimPlaceInfo {
  id: string;
  name?: string;
  mode?: string;
  kind?: string;
  capacity?: number;
  capacity_unit?: string;
  queue_cap?: number;
  tier?: string;
}

/** Human fallback for a node id when the served graph carries no name:
 *  strip the class prefix and title-case — never the raw id. */
export function placeLabel(id: string, places: Map<string, SimPlaceInfo>): string {
  const p = places.get(id);
  if (p?.name) return p.name;
  const bare = id.replace(/^(sea|air|gnd|chk)_/, "").replace(/_/g, " ");
  return bare.charAt(0).toUpperCase() + bare.slice(1);
}

/** Parse a lane key `${mode}:${from}->${to}`. */
export function parseLaneKey(key: string): { from: string; to: string } | null {
  const rest = key.split(":")[1];
  if (!rest) return null;
  const [from, to] = rest.split("->");
  return from && to ? { from, to } : null;
}

const MODE_WORD: Record<string, string> = { sea: "Vessel", air: "Aircraft" };

/** Air class in human words; null when not served. */
export const clsLabelOf = (e: SimEntity): string | null =>
  e.cls === "cargo" ? "CARGO" : e.cls === "passenger" ? "PASSENGER" : null;

/** Rotation lateness in human form: "on time", "20 min late",
 *  "1 h 20 min late". Null input (field not served) → null. */
export function fmtLate(lateMin: number | null | undefined): string | null {
  if (typeof lateMin !== "number" || Number.isNaN(lateMin)) return null;
  if (lateMin <= 0) return "on time";
  const m = Math.round(lateMin);
  if (m < 60) return `${m} min late`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  return rest === 0 ? `${h} h late` : `${h} h ${rest} min late`;
}

/** Entity display label (mode + route + class — NEVER the raw
 *  entity id). Class comes from the lane's served speed class. */
export function entityLabel(
  e: SimEntity,
  lanesByKey: Map<string, SimLane>,
  places: Map<string, SimPlaceInfo>,
): { label: string; route: string; classLabel: string } {
  const lane = lanesByKey.get(e.lane);
  const ends = lane ? { from: lane.from, to: lane.to } : parseLaneKey(e.lane);
  const route = ends
    ? `${placeLabel(ends.from, places)} → ${placeLabel(ends.to, places)}`
    : "route not served";
  const classLabel = lane?.speed
    ? `${lane.speed} ${lane.speed_unit ?? ""} class`.trim()
    : "class not served";
  return { label: `${MODE_WORD[e.mode] ?? "Vehicle"} · ${route}`, route, classLabel };
}

export interface GoodsLine {
  /** Unit class — TEU and tonnes are NEVER merged. */
  unitClass: "TEU" | "tonnes";
  /** Group within the class: the entity derivation class (mode). */
  group: string;
  amount: number;
  unit: string;
}

export interface StatusBoard {
  placeId: string;
  present: Array<{ label: string; classLabel: string; state: string; queueWaitH: number | null; diverted: boolean; clsLabel: string | null; lateMin: number | null }>;
  inbound: Array<{ label: string; classLabel: string; etaAt: string | null; diverted: boolean; clsLabel: string | null; lateMin: number | null }>;
  /** Aircraft on the ground at this place awaiting departure
   *  (dispatched, in port) — the departure queue at the tick. */
  departureQueue: number;
  /** True when at least one EN_ROUTE entry carried dest/eta fields;
   *  false means inbound ETAs are not served in this payload. */
  inboundServed: boolean;
  queue: number | null;
  berthed: number | null;
  berths: number | null;
  congestion: string;
  goods: GoodsLine[];
  goodsNotEstimated: number;
  disruptions: Array<{ label: string; kindLabel: string; endsAt: string }>;
}

/** Berth count from the served queue_cap via the MODELED
 *  divisors (sea /40, air /400). Null when queue_cap is not served. */
export function berthsFor(place: SimPlaceInfo | undefined): number | null {
  if (!place || typeof place.queue_cap !== "number") return null;
  if (place.mode === "air") return Math.max(1, Math.round(place.queue_cap / 400));
  if (place.mode === "sea") return Math.max(1, Math.round(place.queue_cap / 40));
  return null;
}

/** Lane daily throughput as served: the origin place's daily capacity
 *  (GET /graph) × the lane's capacity_share (run derivation). Null when
 *  either input is not served — goods for that leg are not estimated. */
export function laneDailyThroughput(
  lane: SimLane,
  places: Map<string, SimPlaceInfo>,
): { amount: number; unit: string } | null {
  const origin = places.get(lane.from);
  if (!origin || typeof origin.capacity !== "number" || !origin.capacity_unit) return null;
  if (typeof lane.capacity_share !== "number") return null;
  return { amount: origin.capacity * lane.capacity_share, unit: origin.capacity_unit };
}

const unitClassOf = (unit: string): "TEU" | "tonnes" | null =>
  unit.startsWith("TEU") ? "TEU" : unit.startsWith("tonnes") ? "tonnes" : null;

/** Board for one place at one tick, from the SAME state payload +
 *  run detail the map renders (no second code path). */
export function deriveStatusBoard(args: {
  state: SimState;
  run: SimRunDetail;
  placeId: string;
  places: Map<string, SimPlaceInfo>;
}): StatusBoard {
  const { state, run, placeId, places } = args;
  const lanesByKey = new Map(run.derivation.lanes.map((l) => [l.key, l]));
  const place = places.get(placeId);

  const present = state.entities
    .filter((e) => e.node === placeId && e.state !== "EN_ROUTE")
    .map((e) => {
      const { label, classLabel } = entityLabel(e, lanesByKey, places);
      return {
        label, classLabel, state: e.state, queueWaitH: e.queue_wait_h ?? null,
        diverted: e.diverted === true, clsLabel: clsLabelOf(e),
        lateMin: typeof e.late_min === "number" ? e.late_min : null,
      };
    });
  const departureQueue = state.entities.filter(
    (e) => e.node === placeId && e.state === "IN_PORT",
  ).length;

  let inboundServed = false;
  const inbound = state.entities
    .filter((e) => e.state === "EN_ROUTE" && e.dest === placeId)
    .map((e) => {
      inboundServed = true;
      const { label, classLabel } = entityLabel(e, lanesByKey, places);
      return {
        label, classLabel, etaAt: e.eta_at ?? null, diverted: e.diverted === true,
        clsLabel: clsLabelOf(e), lateMin: typeof e.late_min === "number" ? e.late_min : null,
      };
    });
  if (!inboundServed) {
    inboundServed = state.entities.some(
      (e) => e.state === "EN_ROUTE" && typeof e.dest === "string",
    );
  }

  const nodeState = state.nodes.find((n) => n.id === placeId) ?? null;
  const berths = berthsFor(place);
  const queue = nodeState?.queue ?? null;
  const berthed = nodeState?.berthed ?? null;
  const congestion =
    queue === null
      ? "queue state not served for this place"
      : queue > 0 && berths !== null && berthed !== null && berthed >= berths
        ? "Congested — berths full, queue waiting"
        : queue > 0
          ? "Queue forming"
          : "Flowing — no queue";

  // Goods: per-entity load = lane daily throughput × headway in
  // days (MODELLED — the UI labels it). Present + inbound entities.
  const goodsByKey = new Map<string, GoodsLine>();
  let goodsNotEstimated = 0;
  const goodsEntities = state.entities.filter(
    (e) =>
      (e.node === placeId && e.state !== "EN_ROUTE") ||
      (e.state === "EN_ROUTE" && e.dest === placeId),
  );
  for (const e of goodsEntities) {
    const lane = lanesByKey.get(e.lane);
    if (!lane || typeof lane.headway_h !== "number") { goodsNotEstimated += 1; continue; }
    const tp = laneDailyThroughput(lane, places);
    if (!tp) { goodsNotEstimated += 1; continue; }
    const cls = unitClassOf(tp.unit);
    if (!cls) { goodsNotEstimated += 1; continue; }
    const group = MODE_WORD[e.mode] ?? "Vehicle";
    const key = `${cls}|${group}`;
    const amount = tp.amount * (lane.headway_h / 24);
    const cur = goodsByKey.get(key);
    if (cur) cur.amount += amount;
    else goodsByKey.set(key, { unitClass: cls, group, amount, unit: cls === "TEU" ? "TEU" : "tonnes" });
  }
  const goods = [...goodsByKey.values()].sort(
    (a, b) => a.unitClass.localeCompare(b.unitClass) || a.group.localeCompare(b.group),
  );

  const activeIds = new Set(state.disruptions_active);
  const disruptions = run.journal.disruptions
    .filter((d) => d.node_id === placeId && activeIds.has(d.sim_id))
    .map((d) => {
      const endsMs = Date.parse(d.started_at) + d.duration_d * 86400000;
      return {
        label: `${placeLabel(placeId, places)} disruption in force`,
        kindLabel: d.kind.replace(/_/g, " "),
        endsAt: fmtSimDayTime(endsMs),
      };
    });

  return {
    placeId, present, inbound, inboundServed, queue, berthed, berths,
    congestion, goods, goodsNotEstimated, disruptions, departureQueue,
  };
}

/** Queue chart rows at one tick: places ranked by queue length in the
 *  same state payload (top `limit`). Display-only projection.
 *  The chart is a congestion RANKING, so on a tiered network
 *  it is computed over calibration-grade (CORE) places only — shell
 *  queues are still simulated (and shown on their own place's board)
 *  but never ranked. Untiered networks rank every place, as before. */
export function queueChartRows(
  state: SimState,
  places: Map<string, SimPlaceInfo>,
  limit = 8,
): Array<{ placeId: string; label: string; queue: number; berthed: number }> {
  const tiered = [...places.values()].some(
    (p) => typeof p.tier === "string" && p.tier !== "",
  );
  return state.nodes
    .filter((n) => n.queue > 0)
    .filter((n) => isCorePlace(places.get(n.id), tiered))
    .sort((a, b) => b.queue - a.queue || a.id.localeCompare(b.id))
    .slice(0, limit)
    .map((n) => ({ placeId: n.id, label: placeLabel(n.id, places), queue: n.queue, berthed: n.berthed }));
}

/* ── tier split + provenance ────────────────────────────────── */

export interface TierSplit {
  /** True when ANY served place carries a tier value. */
  tiered: boolean;
  coreNodes: number;
  shellNodes: number;
  coreLanes: number;
  shellTouchingLanes: number;
}

/** Tier split computed ONLY from data actually served: place records
 *  (GET /graph) + the run's derivation lanes. When no place carries a
 *  tier value the graph is all-CORE (pre-expansion case). */
export function tierSplit(
  run: SimRunDetail,
  places: Map<string, SimPlaceInfo>,
): TierSplit {
  const all = [...places.values()];
  const tiered = all.some((p) => typeof p.tier === "string" && p.tier !== "");
  if (!tiered) {
    return {
      tiered: false,
      coreNodes: all.length,
      shellNodes: 0,
      coreLanes: run.derivation.lanes.length,
      shellTouchingLanes: 0,
    };
  }
  const isCore = (id: string) => places.get(id)?.tier === "core";
  let coreLanes = 0;
  let shellTouching = 0;
  for (const lane of run.derivation.lanes) {
    if (lane.flow_only) continue;
    if (isCore(lane.from) && isCore(lane.to)) coreLanes += 1;
    else shellTouching += 1;
  }
  return {
    tiered: true,
    coreNodes: all.filter((p) => p.tier === "core").length,
    shellNodes: all.filter((p) => p.tier === "shell").length,
    coreLanes,
    shellTouchingLanes: shellTouching,
  };
}

/** CORE-scope test for statistics: a place counts as CORE
 *  when the graph is untiered, or when its served tier is "core". */
export function isCorePlace(
  place: SimPlaceInfo | undefined,
  tiered: boolean,
): boolean {
  if (!tiered) return true;
  return place?.tier === "core";
}

/* ── tier weighting ──────────────────────────────────────
 * The two-tier network must not render flat: routing-grade (SHELL) places/routes at the same
 * weight as calibration-grade (CORE) ones. These pure helpers resolve
 * every SIM element's tier and the weight it renders at.
 *
 * Tier SOURCE discipline (never invented): a tier field served on the
 * element itself wins; otherwise the tier resolves from the served
 * network's place records — the same source the base map reads its
 * place tiers from (the graph payload's per-place `tier`). A place
 * with no tier value on an untiered network is CORE (contract:
 * no tier anywhere ⇒ all-CORE), so pre-expansion runs render exactly
 * as before. */

export type SimTier = "core" | "shell";

export const tierOfValue = (tier: string | undefined | null): SimTier =>
  tier === "shell" ? "shell" : "core";

/** A route's tier (lane classes): CORE only when the route itself
 *  is served as core, or — when it carries no tier — when BOTH its
 *  endpoint places are calibration-grade. Any shell endpoint makes
 *  the route shell-touching. */
export function laneTierOf(
  lane: SimLane,
  placeTiers: ReadonlyMap<string, string | undefined>,
): SimTier {
  if (typeof lane.tier === "string" && lane.tier !== "") return tierOfValue(lane.tier);
  return tierOfValue(placeTiers.get(lane.from)) === "shell" ||
    tierOfValue(placeTiers.get(lane.to)) === "shell"
    ? "shell"
    : "core";
}

/** A vehicle's tier: its own served tier when present, else its
 *  route's tier (a vehicle on a shell-touching route is routing-grade
 *  traffic, wherever it currently floats). */
export function entityTierOf(
  e: SimEntity,
  lanesByKey: Map<string, SimLane>,
  placeTiers: ReadonlyMap<string, string | undefined>,
): SimTier {
  if (typeof e.tier === "string" && e.tier !== "") return tierOfValue(e.tier);
  const lane = lanesByKey.get(e.lane);
  if (lane) return laneTierOf(lane, placeTiers);
  const ends = parseLaneKey(e.lane);
  if (ends) {
    return tierOfValue(placeTiers.get(ends.from)) === "shell" ||
      tierOfValue(placeTiers.get(ends.to)) === "shell"
      ? "shell"
      : "core";
  }
  return "core";
}

/** A place's tier for SIM overlays (queue rings, board scope): the
 *  state record's own tier when served, else the network's place
 *  record — the base map's tier source. */
export function nodeTierOf(
  nodeId: string,
  servedTier: string | undefined,
  placeTiers: ReadonlyMap<string, string | undefined>,
): SimTier {
  if (typeof servedTier === "string" && servedTier !== "") return tierOfValue(servedTier);
  return tierOfValue(placeTiers.get(nodeId));
}

/** The visual subordination routing-grade elements render at, as
 *  scales on the calibration-grade weight (1 = full weight). Stated
 *  once here; the layer builder applies them:
 *  shell routes thinner + fainter, shell vehicles smaller + fainter,
 *  shell count chips smaller + fainter, shell queue rings lighter. */
export const TIER_WEIGHT = {
  shellLaneAlpha: 0.45,
  shellLaneWidth: 0.55,
  shellEntityAlpha: 0.6,
  shellEntitySize: 0.7,
  shellChipAlpha: 0.6,
  shellChipSize: 0.75,
  shellRingAlpha: 0.6,
  shellRingWidth: 0.6,
} as const;

/** Zoom discipline for routing-grade routes: shell routes
 *  are HIDDEN at world zoom — ~4,500 routes at world zoom is a
 *  hairball — and revealed from regional zoom in. Calibration-grade
 *  routes render at every band. Vehicles/chips are aggregations of
 *  state, not route geometry, and are not hidden by this rule (they
 *  subordinate via TIER_WEIGHT instead). */
export function laneVisibleAtBand(tier: SimTier, band: "WORLD" | "REGION" | "PORT" | "HARBOUR" | "SHIP"): boolean {
  return tier === "core" || band !== "WORLD";
}

/* ── volume at risk (baseline diff) ────────────────────────────── */

export interface VarLine { unit: string; value: number }
export interface VolumeAtRisk {
  available: boolean;
  /** Why the slot stays reserved when unavailable — the missing input. */
  missing: string | null;
  lines: VarLine[];
  /** CORE places skipped because their served record has no daily
   *  throughput (capacity) — counted, never silently zero-filled. */
  skippedNoThroughput: number;
}

/** Mean entity queue-wait (days) at one node in one exact state: the
 *  mean of queue_wait_h over entities AT the node carrying a wait. */
export function meanQueueWaitDays(state: SimState, nodeId: string): number {
  let sum = 0;
  let n = 0;
  for (const e of state.entities) {
    if (e.node === nodeId && typeof e.queue_wait_h === "number") {
      sum += e.queue_wait_h / 24;
      n += 1;
    }
  }
  return n === 0 ? 0 : sum / n;
}

/**
 * VaR(t) = Σ over CORE nodes of node throughput/day × (mean entity
 * queue-wait days at t − baseline mean at t), per unit class.
 * The baseline is the run's undisrupted twin (identical
 * seed/tick/start/weather, empty disruption set) at the SAME tick.
 * Units never mix: one line per served capacity_unit. Negative diffs
 * are kept (a disruption can only be compared honestly, not floored).
 */
export function computeVolumeAtRisk(args: {
  state: SimState | null;
  baselineState: SimState | null;
  places: Map<string, SimPlaceInfo>;
}): VolumeAtRisk {
  const { state, baselineState, places } = args;
  if (!state) return { available: false, missing: "run state at this tick", lines: [], skippedNoThroughput: 0 };
  if (!baselineState) {
    return {
      available: false,
      missing: "baseline (undisrupted twin) state at this tick",
      lines: [],
      skippedNoThroughput: 0,
    };
  }
  const tiered = [...places.values()].some(
    (p) => typeof p.tier === "string" && p.tier !== "",
  );
  const byUnit = new Map<string, number>();
  let skipped = 0;
  const nodeIds = new Set([
    ...state.nodes.map((n) => n.id),
    ...baselineState.nodes.map((n) => n.id),
  ]);
  for (const id of nodeIds) {
    const place = places.get(id);
    if (!isCorePlace(place, tiered)) continue;
    if (!place || typeof place.capacity !== "number" || !place.capacity_unit) {
      skipped += 1;
      continue;
    }
    const diff = meanQueueWaitDays(state, id) - meanQueueWaitDays(baselineState, id);
    if (diff === 0) continue;
    byUnit.set(place.capacity_unit, (byUnit.get(place.capacity_unit) ?? 0) + place.capacity * diff);
  }
  const lines = [...byUnit.entries()]
    .map(([unit, value]) => ({ unit, value }))
    .sort((a, b) => a.unit.localeCompare(b.unit));
  return { available: true, missing: null, lines, skippedNoThroughput: skipped };
}
