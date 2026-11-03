/* PORT TWIN (PTW) wire types. Only the fields this panel reads; everything
 * else the API sends is ignored. Shapes come from the port geometry and
 * port simulation APIs. */

/** Geometry layers in drawing order. */
export type GeoLayer =
  | 'water' | 'coastline' | 'terminal' | 'yard' | 'storage' | 'building' | 'rail' | 'road'
  | 'breakwater' | 'pier' | 'quay' | 'berth' | 'lock' | 'fairway' | 'anchorage' | 'crane';

export type CargoKind = 'container' | 'bulk' | 'tanker' | 'ro-ro' | 'general' | 'unknown';

export interface GeoFeature {
  type: 'Feature';
  properties: { layer: string; osm?: string; name?: string; kind?: string; operator?: string; area_m2?: number; length_m?: number };
  geometry: { type: string; coordinates: unknown };
}
export interface GeoDoc {
  place_id: string;
  basis: string;
  geometry_hash: string;
  attribution: string;
  license?: string;
  geojson: {
    bbox: [number, number, number, number];
    features: GeoFeature[];
    properties: { name?: string; counts?: Record<string, number>; provenance?: { osm_retrieved?: string; generalisation_level?: number } };
  };
}
export interface GeoIndexPort {
  place_id: string;
  name: string;
  geometry: { basis: string; bbox: [number, number, number, number]; counts: Record<string, number>; osm_retrieved: string; sha256: string } | null;
  imagery: { basis: string; resolution_m: number; scene_date: string; scene_id: string } | null;
  profile: { basis: string } | null;
}
export interface GeoIndex { attribution: string; count: number; ports: GeoIndexPort[] }

export interface Imagery {
  attribution: string;
  basis: string;
  bounds: [number, number, number, number];
  image_url: string;
  resolution_m: number;
  scene_date: string;
  platform?: string;
  cloud_pct_aoi?: number;
}

export interface ProfileGroup {
  id: string; name: string; kind: string; quay_length_m: number; berth_count_est: number; flag?: string; length_source?: string; origin?: string;
}
export interface PortProfile {
  geometry_hash: string;
  berth_groups: ProfileGroup[];
  anchorage: { area_km2: number | null; capacity_vessels_est: number | null; flag?: string; polygons_osm?: number };
  channel: { present: boolean };
  yard: { blocks: Array<{ id: string; area_m2: number; capacity_teu_est: number; flag?: string }>; total_area_m2?: number };
  cranes: { count_osm: number };
  params: { basis?: string; anchorage_slot_radius_m?: number };
}

export interface SimGroup { id: string; name: string; kind: string; berths: number; berth_length_m: number }
export interface SimProfileDoc {
  label: string;
  profile_hash?: string;
  tier: string;
  grade?: string;
  profile: {
    place_id: string; name: string; tier: string; profile_kind: string; geometry_hash: string | null;
    groups: SimGroup[];
    anchorage: { slots: number; basis?: string };
    channel: { present: boolean };
    yard: { capacity_teu: number | null; basis?: string };
    loa_scale?: number;
  };
}

export interface ScenarioItem { type: string; [k: string]: unknown }

export interface SimRequest {
  days: number; variant: 'low' | 'base' | 'high'; arrival_shape: 'even' | 'diurnal'; warmup_days?: number; start_at?: string;
  scenario: ScenarioItem[];
}

export interface SimSummary {
  calls: { arrived: number; departed: number; in_system_at_end: number; arrived_by_kind: Record<string, number> };
  queue: { peak: number; mean: number; outside_peak: number; at_end: number };
  wait_h: { mean: number; p95: number; max: number; calls: number; censored_still_waiting: number };
  port_time_h: { mean: number; p95: number; calls: number };
  throughput: { container_teu_per_day?: number; tonnes_per_day_by_class: Record<string, number> };
  berth_utilisation: Record<string, number>;
  yard: { mean_fill: number; peak_fill: number; min_throttle: number };
  result_digest?: string;
}
export interface SimRun {
  sim_id: string;
  place_id: string;
  name: string;
  status: string;
  journal: { days: number; warmup_days: number; variant: string; scenario: ScenarioItem[]; profile_kind: string; start_at?: string | null };
  summary: SimSummary;
  provenance: {
    label: string; basis: string; tier: string; grade: string;
    profile: { kind: string; geometry_hash: string | null; notes?: string[] };
    plugins_used?: Array<{ name: string; value: unknown; status: string }>;
    scorecard?: { minted: boolean };
  };
  links: { self: string; frames: string; kpis: string; compare: string; verify: string };
}
export interface SimBuilding { status: 'building'; links?: { self: string } }

/** A vessel row: [id, class index, state index, location, progress] and, when a frame serves one, a sixth
 *  element: the heading in degrees (compass, clockwise from north). The port simulation serves none today, so the panel derives a heading from the berth; a served one wins. */
export type VesselRow = [number, number, number, number | null, number | null] | [number, number, number, number | null, number | null, number | null];
export interface Frame {
  t: number; queue: number; anchored: number; outside: number; util: number[]; yard_fill: number | null; vessels: VesselRow[];
}
export interface FramesDoc {
  label: string; from: number; to: number; step_s: number; n_frames: number;
  legend: { states: string[]; classes: string[] };
  berths: Array<{ index: number; group: string; slot: number; length_m: number }>;
  groups: Array<{ index: number; id: string; name: string; kind: string; berths: number }>;
  anchorage_slots: number;
  frames: Frame[];
}

export interface KpiSample {
  t: number; queue: number; anchored: number; outside: number; at_berth: number; working: number;
  util: Record<string, number>; yard_fill: number | null; yard_throttle: number | null; wait_mean_h: number | null; arrivals: number;
}
export interface KpisDoc { label: string; basis: string; summary: SimSummary; timeline: { step_s: number; samples: KpiSample[] } }

export interface CompareRow {
  measure: string; unit?: string; baseline?: number | null; scenario?: number | null; basis?: string;
  delta?: number | null; delta_pct?: number | null; direction?: 'worse' | 'better' | 'same' | string; note?: string; omitted?: string;
}
/** `baseline_state` (portsim 1.1.0): is the undisturbed run itself congested. */
export interface BaselineState {
  structurally_saturated: boolean;
  kinds_over_capacity?: string[];
  kinds_at_service_time_bound?: string[];
  max_implied_utilisation?: number | null;
  reading?: string;
  baseline_wait_mean_h?: number | null;
  baseline_in_system_at_end?: number | null;
}
export interface CompareDoc {
  sim_id: string; baseline_id: string; identical: boolean; diverges_at_h: number | null; diverges_at_s: number | null;
  table: CompareRow[];
  /** Absent on a server older than model 1.1.0: the panel then says so, it does not guess. */
  baseline_state?: BaselineState | null;
  berth_utilisation: Record<string, { baseline: number; scenario: number; delta: number }>;
  scenario: ScenarioItem[];
}
export interface VerifyDoc { identical: boolean; result_digest: string; replayed_digest: string }

export interface PlaceHit { id: string; name: string }
