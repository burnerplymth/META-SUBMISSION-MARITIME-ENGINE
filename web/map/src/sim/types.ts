// Client types for the additive /sim API.
// Shapes mirror server/api/app.py /sim routes + server/sim/ records
// EXACTLY as served (verified against live payloads): do not improvise fields.
//
// COORDINATE ORDER WARNING: derivation lane `path` / `midpoint` are
// [lat, lon] pairs (server geometry convention). State entities carry
// separate `lon` / `lat` scalars. deck.gl wants [lon, lat] — the
// conversion lives in sim/geometry.ts and nowhere else.

import type { Mode } from "../types";

export type SimTickSize = "hour" | "day" | "week" | "month";
export type SimEntityState = "EN_ROUTE" | "QUEUED" | "BERTHED" | "IN_PORT";

/** POST /sim/run request body (strict server-side). */
export interface SimRunRequest {
  disruptions: Array<
    | { disruption_id: string }
    | {
        node_id: string;
        kind: string;
        capacity_loss?: number;
        duration_d: number;
        started_at?: string;
        shape?: "step";
      }
  >;
  tick: SimTickSize;
  ticks?: number;
  seed: number;
  start_at?: string;
  weather?: boolean;
  /** LANE GEOMETRY (opt-in, journaled): sea vessels sail the land-checked lane polylines instead
   *  of straight chords. The server folds the two sidecar hashes into the journal when this is true. */
  lane_geometry?: boolean;
}

/** POST /sim/run 201 summary (also the head of GET /sim/run/{id}).
 *  Adds the timeline-ruler fields to the summary:
 *  tick_seconds / start_at / end_at (= start_at + ticks × tick_seconds)
 *  / seed / state_hash (the FINAL tick's hash). */
export interface SimRunSummary {
  sim_run_id: string;
  status: string;
  tick_size: SimTickSize;
  ticks: number;
  /** Ruler fields (present on runs served with them). */
  tick_seconds?: number;
  start_at?: string;
  end_at?: string;
  seed?: number;
  state_hash?: string;
  entity_count: number;
  vessel_count: number;
  aircraft_count: number;
  provenance: {
    line: string;
    basis: string;
    split: { sourced_edges: number; modeled_edges: number; total_edges: number };
    headway_coarsening: { factor: number; reason: string };
    weather: { source: string; retrieved_at: string; hash: string } | null;
    weather_reason: string | null;
    [k: string]: unknown;
  };
}

/** One derivation lane (GET /sim/run/{id}.derivation.lanes[]).
 *  `path` is the RENDERED path (already routed via rendered_via when the
 *  land-crossing rule fired) as [lat, lon] samples; `rendered_via` lists
 *  intermediate node ids, [] for a direct great-circle lane. */
export interface SimLane {
  key: string;
  mode: Mode;
  from: string;
  to: string;
  /** Tiered graph: when served it is the route's tier and
   *  wins; the current server serves no per-route tier, so
   *  the client resolves the tier from the endpoint places' served tiers (the
   *  base map's tier source) — see phaseD.laneTierOf. Rendered
   *  distinctly: shell-touching routes subordinate. */
  tier?: string;
  capacity_share: number;
  share_basis: string;
  flow_only: boolean;
  headway_band?: number;
  headway_h?: number;
  speed?: number;
  speed_class?: number;
  speed_kmh?: number;
  speed_unit?: string;
  nominal_duration_h?: number;
  direct_distance_km: number;
  rendered_distance_km: number;
  land_crossing: number;
  rendered_via: string[];
  disposition: string;
  /** [lat, lon] samples of the rendered path (>= 8 points, <= 2° apart). */
  path: Array<[number, number]>;
  /** [lat, lon] midpoint of the rendered path (mobile lanes only). */
  midpoint?: [number, number];
  departure_count?: number;
}

export interface SimJournalDisruption {
  node_id: string;
  kind: string;
  capacity_loss: number | null;
  duration_d: number;
  shape: string;
  started_at: string;
  /** Server-assigned id ("d1", "d2", …) — state.disruptions_active
   *  refers to THESE ids; the node is resolved through this list. */
  sim_id: string;
}

export interface SimJournal {
  graph_hash: string;
  graph_version: string;
  disruptions: SimJournalDisruption[];
  tick: SimTickSize;
  ticks: number;
  seed: number;
  start_at: string;
  weather_snapshot_hash: string | null;
  /** Present only on runs that opted into lane geometry: the content hashes of the two sidecars. */
  lane_geometry?: { sea_routes_content_sha256: string; lane_geometry_content_sha256: string };
}

/** GET /sim/run/{sim_run_id} — summary + journal + derivation summary. */
export interface SimRunDetail extends SimRunSummary {
  journal: SimJournal;
  derivation: {
    lanes: SimLane[];
    split: { sourced_edges: number; modeled_edges: number; total_edges: number };
    basis: string;
    headway_coarsening: { factor: number; reason: string };
    longest_lane_duration_h: number;
    preroll_s: number;
  };
  weather: { source: string; retrieved_at: string; hash: string } | null;
  weather_reason: string | null;
}

/** GET /sim/state/{id}?tick=N — entities[]. Positions come ONLY from
 *  this payload; the client never recomputes them. */
export interface SimEntity {
  id: string;
  /** When served it wins; otherwise resolves the
   *  vehicle's tier from its route (phaseD.entityTierOf) and renders
   *  routing-grade vehicles subordinate to calibration-grade ones. */
  tier?: string;
  mode: "sea" | "air";
  /** lane key, `${mode}:${from}->${to}` */
  lane: string;
  state: SimEntityState;
  lon: number | null;
  lat: number | null;
  node?: string;
  queue_wait_h?: number;
  /** Additive (EN_ROUTE entries only): destination node id of
   *  the leg in progress — a stored segment fact, served verbatim. */
  dest?: string;
  /** ISO arrival time of the leg in progress (stored fact). */
  eta_at?: string;
  /** Direction of travel in degrees along the rendered path. */
  bearing_deg?: number;
  /** Additive: true while the entity is sailing
   *  a DIVERSION leg — a MODELLED vessel-level reaction to a closure
   *  on its route. Absent on runs/legs with no diversion; the UI
   *  surfaces it as DIVERTED (MODELLED) and tolerates its absence. */
  diverted?: boolean;
  /** Additive: air entity class — surfaced in
   *  listings as CARGO / PASSENGER. Absent until served. */
  cls?: "cargo" | "passenger";
  /** Additive: rotation lateness in minutes —
   *  projected while en route, realised on the ground; both modes.
   *  Absent until served; the board renders nothing then. */
  late_min?: number;
  /** Additive (sea): vessel name as served — the SHIP-band
   *  label text when present. Absent until the server serves it. */
  vessel_name?: string;
  /** Additive (sea): vessel class as served. */
  vessel_class?: string;
  /** Additive (air): aircraft tail / registration as served —
   *  the SHIP-band label text when present. */
  tail?: string;
  /** Additive (air): aircraft type as served. */
  aircraft_type?: string;
}

/** GET /sim/run/{id}/markers — timeline markers
 *  derived ONLY from stored sim facts (+ read-only chain events for
 *  chain_event). Nothing is synthesised client-side either: the host
 *  renders exactly these. */
export type SimMarkerKind =
  | "disruption_onset"
  | "disruption_expiry"
  | "queue_peak"
  | "congested_arrival"
  | "chain_event";

export interface SimMarker {
  tick: number;
  at: string;
  kind: SimMarkerKind;
  label: string;
  node_id: string | null;
  ref: string | null;
}

export interface SimMarkersResponse {
  sim_run_id: string;
  markers: SimMarker[];
  markers_truncated: boolean;
}

export interface SimNodeWeather {
  wind_kn: number | null;
  wave_m: number | null;
  valid_at: string | null;
  source: string;
}

export interface SimNodeState {
  id: string;
  /** When served it wins; otherwise resolves the
   *  place's tier from the served network record (phaseD.nodeTierOf)
   *  and subordinates routing-grade queue overlays. */
  tier?: string;
  queue: number;
  berthed: number;
  arrived: number;
  departed: number;
  weather?: SimNodeWeather;
}

/** Additive: one modelled weather cell served inside the
 *  state payload — a bbox graded by its wind / wave values. */
export interface SimWeatherCell {
  bbox: [number, number, number, number];
  wind_kn: number | null;
  wave_m: number | null;
  severe: boolean;
}

export interface SimState {
  sim_run_id: string;
  tick: number;
  sim_time: string;
  state_hash: string;
  entities: SimEntity[];
  nodes: SimNodeState[];
  /** sim_id values (journal disruptions), NOT node ids. */
  disruptions_active: string[];
  /** Additive: modelled weather cells for this tick. Absent
   *  on runs / ticks without weather cells. */
  weather_cells?: SimWeatherCell[];
  /** Additive: validity window of the weather step in view,
   *  or null when no step applies. */
  weather_step?: { from: string; to: string } | null;
}

/** GET /sim/weather/{id} — absence is a 200 state, not an error. */
export interface SimWeatherSeries {
  times: string[];
  wind_kn?: number[];
  wind_dir?: number[];
  wave_m?: (number | null)[];
  wave_times?: string[];
  lat?: number;
  lon?: number;
}

export interface SimWeatherSnapshot {
  source: string;
  attribution: string;
  retrieved_at: string;
  hash: string;
  nodes: Record<string, SimWeatherSeries>;
  lane_midpoints: Record<string, SimWeatherSeries>;
}

export type SimWeatherResponse =
  | { weather: SimWeatherSnapshot }
  | { weather: null; reason: string };
