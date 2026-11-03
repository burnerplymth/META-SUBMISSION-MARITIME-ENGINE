/* Shapes of the shipment list (kept in the browser only) and of the server
 * documents it reads (POST /exposure/evaluate, /exposure/resolve, GET
 * /exposure/sample). Read defensively: a missing field
 * is NOT SERVED in the view, never a zero. */

export type ShipMode = 'sea' | 'air' | 'ground';
export const SHIP_MODES: readonly ShipMode[] = ['sea', 'air', 'ground'];

export interface Shipment {
  id: string; origin: string; destination: string; mode: ShipMode;
  etd?: string; eta?: string; value_usd?: number; quantity?: number; unit?: string; via?: string;
}

export interface Range3 { low: number; median: number; high: number }
export type ShipStatus = 'unaffected' | 'delayed' | 'rerouted' | 'not_served' | 'unresolved';

export interface PlaceRow { id: string; name: string; country?: string | null; mode?: string; kind?: string; tier?: string }
export interface Suggestion { id: string; name: string; country?: string | null; mode?: string }

export interface EvalShipment {
  id: string; lane_key: string | null; status: ShipStatus; added_days: Range3 | null; baseline_days: Range3 | null; disrupted_days: Range3 | null;
  value_usd: number | null; value_in_affected_usd: number; route_label: string | null; via: Array<{ id: string; name: string }> | null; notes_chips: string[];
}
export interface EvalLane {
  lane_key: string; origin: string; destination: string; mode: string; origin_name: string; destination_name: string; n_shipments: number;
  status: ShipStatus; added_days: Range3 | null; baseline_days: Range3 | null; route_label: string | null; notes_chips: string[];
}
export interface EvalResolved { id: string; origin_place: PlaceRow; destination_place: PlaceRow; match_quality: { origin: string; destination: string } }
export interface EvalUnresolved { id: string; field: 'origin' | 'destination' | 'via'; text: string; reason: string; suggestions: Suggestion[] }
export interface ExposedPlace { place: string; name: string; n: number; lat?: number | null; lon?: number | null }
export interface EvalSummary {
  n: number; n_served: number; n_affected: number; n_not_served: number; n_unresolved: number; share_affected: number | null;
  added_days_median_range: { low: number; high: number } | null; value_in_affected_usd: number; value_total_usd: number;
  most_exposed_places: ExposedPlace[];
}
export interface McLane { lane_key: string; added_days: Range3; places: number; places_missing: number; places_total: number }
/** The Monte Carlo block of an evaluate answer. Every added field is optional: an older server omits it. */
export interface McBlock {
  state: 'SERVED' | 'NOT SERVED'; job_id?: string; n?: number; label?: string; by_lane?: McLane[];
  statistic?: string; scenario_match?: boolean | null; mismatches?: string[]; missing_places?: number; job_places_listed?: number;
  engine?: { engine_rev: number | null; profile: string | null; lane_plan_engine_rev: number; same: boolean | null };
}
export interface EvalDoc {
  schema: string; label: string; scenario: { mode: string; label: string; place?: { id: string; name: string }; duration_d?: number };
  resolved: EvalResolved[]; unresolved: EvalUnresolved[]; by_lane: EvalLane[]; shipments: EvalShipment[]; summary: EvalSummary;
  counts: { shipments: number; unique_lanes: number };
  mc?: McBlock | null;
}

export type EvalResult =
  | { kind: 'ok'; doc: EvalDoc }
  | { kind: 'notserved' }
  | { kind: 'down'; code: string }
  | { kind: 'refused'; code: string };

export interface ResolveItem { text: string; mode: ShipMode | 'via' }
export interface ResolveRow {
  text: string; status: 'resolved' | 'unresolved'; quality: string | null; place: PlaceRow | null; reason?: string; suggestions: Suggestion[];
}

/** What the exposure is read against: nothing, a library scenario, or the causal chain open in the shell. */
export type ScenarioChoice =
  | { kind: 'none' }
  | { kind: 'library'; id: string; duration: number; loss: number | null }
  | { kind: 'chain'; duration: number; loss: number | null }
  /** the earliest shock of a Scenario builder scenario, handed over for this browser session only */
  | { kind: 'builder'; place: string; shockKind: string; duration: number; loss: number | null; name: string; of: number; rounded: boolean; via?: 'montecarlo' };

export interface ChainDisruption { place_id: string; kind: string; duration_d: number | null; capacity_loss: number | null }
