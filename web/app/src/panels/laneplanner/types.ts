/* Shapes of POST /lanes/plan (schema lanes.plan/1), read defensively: a
 * missing field is NOT SERVED in the view, never a zero. */

export type Label = 'MODELLED' | 'SIMULATED' | 'SOURCED';
export const LABELS: readonly string[] = ['MODELLED', 'SIMULATED', 'SOURCED'];

export interface Range3 { low: number; median: number; high: number }
export interface RangeBlock extends Range3 { unit?: string; label?: string; sources?: string[]; rule?: string; measure?: string }
/** Wait at arrival, per place of a route: the day the vessel reaches it and the wait it meets there (ranges over the named variants). */
export interface ArrivalRow { place_id: string; name: string; arrival_d: Range3; wait_d: Range3 }
/** Access leg of a shell endpoint: served through the nearest connected core place, MODELLED; no wait is simulated at the shell place. */
export interface ShellAccessEnd { place_id: string; via: { id: string; name: string }; km: number; graph_leg_days: number; label: string; wait: string }
export interface ShellAccess { label: string; limit_km: number; origin: ShellAccessEnd | null; destination: ShellAccessEnd | null }

export interface PlaceRow {
  id: string; name: string; kind: string; mode: string; tier: string; country: string | null; lat: number; lon: number;
  role?: 'origin' | 'destination' | 'gate' | 'waypoint';
}
export interface LegRow { from: string; to: string; km: number; days: number }
export interface Segment {
  kind: 'sail' | 'queue'; from?: string; to?: string; place_id?: string; name?: string; km?: number;
  d: Range3; start: Range3; end: Range3;
}
/** Lane geometry of one leg of a route: `lane` = a land-checked lane polyline, `approx` = the
 *  straight chord (no lane geometry for the leg). Parts are [lon, lat], split at the antimeridian. */
export interface GeoLeg { from: string; to: string; state: 'lane' | 'approx'; parts: Array<Array<[number, number]>> }
export interface RouteGeometry { label: string; detail: string; legs: GeoLeg[]; lane_legs: number; approx_legs: number }
/** The lane-based figures of a route: days from lane nautical miles at the named speeds (MODELLED). */
export interface LaneBlock {
  label: string; km: { value: number; label: string; sources: string[] }; nm: number;
  legs: Array<{ from: string; to: string; state: string; nm: number; km: number }>; lane_legs: number; approx_legs: number;
  speed_kn: { fast: number; base: number; slow: number; class: string };
  sail: RangeBlock; queue: RangeBlock | null; total: RangeBlock; vs_baseline: RangeBlock; timeline: Segment[];
  queue_at_arrival?: RangeBlock | null; total_at_arrival?: RangeBlock | null; queue_at_arrival_by_place?: ArrivalRow[];
}
export interface RouteDoc {
  id: string; label: string; corridor: string; basis: string; services: string[]; n_services: number; note: string | null;
  is_baseline: boolean; floor_ok: boolean; disrupted_place_on_route: boolean;
  km: { value: number; label: string; sources: string[] };
  places: PlaceRow[]; legs: LegRow[]; gates: Array<{ id: string; name: string }>;
  sail: RangeBlock; queue: RangeBlock | null; total: RangeBlock; vs_baseline: RangeBlock;
  /** additive: the wait a vessel meets on reaching each place; `queue` is the run peak */
  queue_at_arrival?: RangeBlock | null; total_at_arrival?: RangeBlock | null; queue_at_arrival_by_place?: ArrivalRow[];
  queue_by_place: Array<{ place_id: string; name: string; wait_d: number; low: number; high: number }>;
  timeline: Segment[];
  /** additive: absent on plans from a server without the lane geometry sidecar */
  geometry?: RouteGeometry; length_km?: number; lane?: LaneBlock;
}
export interface ClosureDoc {
  place_id: string; name: string; kind: string; capacity_loss: number; scenario_id: string | null;
  duration_d: number; start_d: number; end_d: Range3; label: string;
}
export interface CheckRow {
  id: string; state: string; flag: boolean; sources: string[];
  suez_route_id?: string; cape_route_id?: string;
  engine?: { suez_km?: number; cape_km?: number; detour_km?: number; ratio?: number; cut_pct?: number; great_circle_km?: number; label: string };
  reference?: { id: string; scope: string; label: string; cut_pct: { lo: number; hi: number }; label_state: string; source: string } | null;
  /** additive: the same check on lane kilometres, and the sidecar's own pair lanes beside it */
  lane?: {
    suez_km: number; cape_km: number; detour_km: number; ratio: number; cut_pct: number; label: string; state: string; flag: boolean;
    reference: { id: string; scope: string; label: string; cut_pct: { lo: number; hi: number }; label_state: string; source: string } | null;
    sources: string[]; approx_legs?: { suez: number; cape: number };
    sea_routes_pair?: { suez_nm: number; cape_nm: number; cut_pct: number; label: string };
  };
  routes?: Array<{ route_id: string; ok: boolean }>;
}
export interface SourceRow { id: string; label: string; state: string; detail?: string; url?: string }
export interface QueueVariant { id: string; profile: string; duration_mult: number; duration_d: number; base: boolean }
export interface PlanDoc {
  schema: string; plan_id: string; engine_label: string;
  inputs: { origin: string; destination: string; extra: boolean; departs_at: string | null; disruption: unknown };
  journal: { inputs_sha256: string; graph_hash: string; engine2_registry_hash: string } & Record<string, unknown>;
  places: { origin: PlaceRow; destination: PlaceRow };
  states: Record<string, string>;
  variants: {
    sail: Array<{ id: string; speed_kn: number; factor: number; label: string }>;
    queue: QueueVariant[]; rule: string; base: { sail: string; queue: string };
  };
  lane_basis?: { state: string; label: string; rule?: string; speeds_kn?: Record<string, number> };
  time_basis: { kind: string; leg: string | null; leg_days: number | null; route_km: number | null; days_per_km: number; implied_speed_kn: number | null };
  baseline: { route_id: string; label: string; sail: RangeBlock };
  shell_access?: ShellAccess | null;
  queue_measures?: Record<string, string>;
  disruption: ClosureDoc | null;
  routes: RouteDoc[];
  checks: CheckRow[];
  sources: SourceRow[];
}

export interface Scenario {
  id: string; label: string; place_id: string; kind: string; default_duration_d: number;
  duration_options_d?: number[]; default_capacity_loss?: number; capacity_loss_options?: number[];
}

export interface PlanRequest {
  origin: string; destination: string; extra?: boolean; shell_access?: boolean;
  disruption?: { scenario_id: string; duration_d?: number; capacity_loss?: number } | null;
}

export type PlanResult =
  | { kind: 'ok'; doc: PlanDoc }
  | { kind: 'refused'; code: string; message: string; refusal: Record<string, unknown> }
  | { kind: 'notserved' }
  | { kind: 'down'; code: string };
