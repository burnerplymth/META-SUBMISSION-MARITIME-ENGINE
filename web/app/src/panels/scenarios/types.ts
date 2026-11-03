/* Scenario builder (SCB): wire types for /scenarios/* (server/scenario) and the
 * builder's own document. Everything the server sends is data: rendered as
 * text, never evaluated. */

export const KINDS = ['closure', 'capacity_cut', 'congestion', 'strike', 'weather', 'grounding', 'accident', 'policy', 'other'] as const;
export type ShockKind = typeof KINDS[number];
export const SHAPES = ['step', 'ramp', 'pulse'] as const;
export type ShockShape = typeof SHAPES[number];

/** One shock row of the builder. Times are days from the scenario start. */
export interface UiShock {
  id: string;
  placeId: string;
  kind: ShockKind;
  startD: number;
  durationD: number;
  /** Fraction 0 to 1; a closure always runs at 1. */
  loss: number;
  shape: ShockShape;
  note: string;
}

export interface BuilderDoc {
  name: string;
  /** Scenario start, UTC date (YYYY-MM-DD). */
  startDate: string;
  shocks: UiShock[];
}

/* ── wire ───────────────────────────────────────────────────────────── */
export interface Verdict { ok: boolean; code: string | null; reason: string | null }
export interface ShockWindow { start_d: number; end_d: number; start_at: string; end_at: string }
export interface ShockVerdictRow {
  id: string;
  place_id: string;
  place_name: string | null;
  accepted: boolean;
  verdicts: { chain: Verdict; sim: Verdict };
  window?: ShockWindow;
}
export interface Overlap { a: string; b: string; overlap_d: number; same_place: boolean }
export interface SpanDoc { first_start_d: number; last_end_d: number; total_span_d: number }
export interface ValidateDoc {
  name: string;
  start_at: string;
  shocks: ShockVerdictRow[];
  accepted: { chain: boolean; sim: boolean };
  runnable: boolean;
  overlaps: Overlap[];
  span: SpanDoc | null;
  engine_rev?: 1 | 2;
}

export interface WireShock {
  id: string; place_id: string; kind: string; start_offset_d: number; duration_d: number;
  capacity_loss: number; shape: string; note: string;
}
export interface WireScenario { name: string; start_at: string; notes: string; shocks: WireShock[] }

export interface SavedRow { id: string; name: string; start_at: string; shock_count: number; places: string[]; total_span_d: number }
export interface SavedList { scenarios: SavedRow[]; count: number; max_saved: number }

export interface RulesDoc {
  max_shocks: number;
  kinds: string[];
  shapes: string[];
  start_offset_d: { min: number; max: number };
  duration_d: { min_exclusive: number; max: number };
  capacity_loss: { min_exclusive: number; max: number; closure_forces: number };
  defaults: { start_at: string; capacity_loss: number; duration_d: number };
  sim: { tick: string; horizon_d: number; shapes: string[]; seed: number };
  semantics: Record<'chain' | 'sim', { unit: string; exact: string[]; approximated: string[]; not_supported: string[] }>;
  organise: { kinds: string[] };
  engine_revs: number[];
}

export interface ChainRunRow {
  order: number; shock_id: string; place_id: string; place_name: string | null; kind: string;
  run_id: string; event_count: number | null; stream_hash: string; starts_at: string; start_offset_d: number; duration_d: number;
}
export interface CombinedRow { place_id: string; name: string | null; max_delay_d: number; sum_delay_d: number; hit_by: string[]; overlapping_hit_by?: string[]; by_shock: Record<string, number> }
export interface ChainRunDoc {
  scenario_id: string | null; target: 'chain'; engine_rev: number; label: string; start_at: string; shock_count: number;
  overlaps: Overlap[]; span: SpanDoc | null;
  runs: ChainRunRow[];
  combined: { method: string; exact: boolean; label: string; sum_rule?: string; places_reached: number; places: CombinedRow[] };
}
export interface SimRunDoc {
  scenario_id: string | null; target: 'sim'; engine_rev: number; label: string; start_at: string; shock_count: number;
  overlaps: Overlap[]; span: SpanDoc | null;
  sim_run_id: string; status: string; ticks: number; seed: number; weather: boolean;
  windows: Array<{ shock_id: string; sim_id: string; place_id: string; starts_at: string; duration_d: number; capacity_loss: number }>;
}
export type RunDoc = ChainRunDoc | SimRunDoc;

export interface OrganiseRule {
  kind: 'stagger' | 'sequence' | 'cluster' | 'align_start' | 'spread_evenly' | 'shift';
  gap_d?: number; reference?: 'end' | 'start'; order?: string[]; from_d?: number; within_d?: number;
  day?: number; window_d?: number; delta_d?: number; ids?: string[]; snap_d?: number;
}
export interface OrganiseDoc {
  rule: OrganiseRule & { snap_d: number };
  shocks: Array<{ id: string; start_offset_d: number }>;
  diff: Array<{ id: string; from_d: number; to_d: number; moved: boolean }>;
  moved: number;
  overlaps_before: Overlap[];
  overlaps_after: Overlap[];
  span_before: SpanDoc;
  span_after: SpanDoc;
  snap_d: number;
}

/** A proposed arrangement waiting for Accept or Discard. */
export interface PendingArrangement {
  rule: OrganiseRule;
  source: 'meta-ai' | 'rule';
  /** Instruction text as typed, when it came from the box. */
  text: string | null;
  starts: Record<string, number>;
  diff: OrganiseDoc['diff'];
  moved: number;
  overlapsBefore: number;
  overlapsAfter: number;
  spanBefore: number;
  spanAfter: number;
}
