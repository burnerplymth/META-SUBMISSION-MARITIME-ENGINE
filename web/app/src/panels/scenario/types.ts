/* Payload shapes of the scenario loop routes, as served. Keys that already exist in the
 * API (node_id, place_id ...) are kept verbatim; the UI words are places,
 * legs and causal chain. */

export type EngineRev = 1 | 2;
export type Loop = 'chain' | 'sim';

/* ── chain loop ─────────────────────────────────────────────────────── */
export type ShockShape = 'step' | 'ramp' | 'pulse';

export interface ScenarioEntry {
  id: string; label: string; place_id: string; place_name: string; place_kind: string; kind: string;
  duration_options_d: number[]; default_duration_d: number;
  capacity_loss_options?: number[]; default_capacity_loss?: number;
  related_place_ids?: string[]; shapes?: string[]; verified?: boolean; request?: unknown;
}
export interface ScenariosDoc { count: number; engine_revs: number[]; scenarios: ScenarioEntry[] }

export interface ShockRules {
  kinds: string[]; shapes: string[];
  capacity_loss: { min_exclusive: number; max: number; closure_forces: number };
  duration_d: { min_exclusive: number; max: number };
  params: Record<string, [number, number]>;
  fork_overrides: string[]; engine_revs: number[];
}

export interface RunReceipt {
  run_id: string; status: string; event_count: number; stream_hash: string;
  engine_rev?: number; engine_label?: string; parent_run_id?: string; at_seq?: number;
}

export interface CheckRefusal { code: string; reason: string }
export interface ShockCheck {
  accepted: boolean; refused: CheckRefusal | null;
  shock?: { node_id: string; kind: string; capacity_loss: number; duration_d: number; shape: string; started_at?: string } | null;
  kind?: 'SHOCK' | 'FORK SHOCK'; engine_rev?: number; at_seq?: number;
  overrides?: Record<string, unknown>; diverges_from_seq?: number | null;
}

export interface ChainJournalShock {
  n: number; kind: 'SHOCK' | 'FORK SHOCK' | string; place_id: string;
  shock_kind?: string; size?: number; duration_d?: number; shape?: string;
  starts_at?: string; at_seq: number | null; overrides?: Record<string, unknown>;
}
export interface ChainLineageRow { run_id: string; parent_run_id: string | null; at_seq: number | null; engine_rev: number }
export interface ChainJournal {
  run_id: string; kind: string; engine_rev: number; engine_label: string;
  settings: { params?: Record<string, number>; engine_rev?: number; engine_label?: string; stream_hash?: string; event_count?: number; map_version?: string; plugin_registry_hash?: string };
  shocks: ChainJournalShock[]; lineage: ChainLineageRow[];
}

export interface CompareSide { run_id: string; engine_rev: number; event_count: number; stream_hash: string }
export interface ComparePlaceRow {
  place_id: string; only_in: 'a' | 'b' | null;
  channel_a: string | null; channel_b: string | null; first_seq_a: number | null; first_seq_b: number | null;
  delay_d_a: number | null; delay_d_b: number | null; delay_delta_d: number;
  magnitude_a: number | null; magnitude_b: number | null; magnitude_delta: number;
  cost_a: number | null; cost_b: number | null; cost_delta: number;
}
export type ChannelCounts = Record<string, number>;
export interface ChainCompare {
  a: CompareSide; b: CompareSide; same_engine_rev: boolean; identical: boolean;
  diverges_from_seq: number | null; shared_prefix_len: number;
  places: ComparePlaceRow[]; channels: { a: ChannelCounts; b: ChannelCounts; delta: ChannelCounts };
}

export interface ChainVerify {
  run_id: string; engine_rev: number; engine_label: string; event_count: number;
  stored_stream_hash: string; recomputed_stream_hash: string; stored_hash_matches: boolean;
  replay_stream_hash: string; replay_event_count: number; replay_matches: boolean;
  byte_identical: boolean; canonical_bytes: number; canonical_sha256: string;
  first_difference_seq: number | null; verified: boolean;
}

/* ── sim loop ───────────────────────────────────────────────────────── */
export type SimInterventionKind = 'disruption' | 'airspace' | 'storm_cell';
export interface SimIntervention { kind: SimInterventionKind; at_tick: number; spec: Record<string, unknown> }

export interface SimJournalRow {
  n: number; tick: number; kind: 'SETTING' | 'SHOCK' | string;
  setting?: string; value?: unknown;
  shock?: string; place_ids?: string[]; sim_id?: string; source?: 'RUN' | 'INJECTED' | string; seq?: number;
  duration_d?: number; capacity_loss?: number | null; disruption_kind?: string; spec?: Record<string, unknown>;
}
export interface SimJournalIntervention {
  seq: number; kind: string; at_tick: number; sim_id?: string; spec: Record<string, unknown>;
}
export interface SimJournal {
  sim_run_id: string; provenance: string;
  lineage: { parent_id: string | null; forked_at_tick: number | null } | null;
  settings: Record<string, unknown>;
  rows: SimJournalRow[];
  interventions: SimJournalIntervention[];
  replay_body?: Record<string, unknown>;
}

export interface SimLineageRun {
  sim_run_id: string; parent_id: string | null; forked_at_tick: number | null;
  status: 'ready' | 'building' | 'failed' | 'missing' | string; created_utc: string | null; n_interventions: number | null;
}
export interface SimLineage {
  root: string; provenance: string; ancestors: SimLineageRun[]; runs: SimLineageRun[];
  edges: Array<{ parent_id: string; child_id: string; forked_at_tick: number }>;
}

export interface SimMeasure {
  id: string; label: string; unit: string; basis: 'SIMULATED' | 'MODELLED' | string;
  a: number | null; b: number | null; difference: number | null;
  first_differs_tick: number | null; per_tick: boolean; a_at: unknown; b_at: unknown;
}
export interface SimCompare {
  a: string; b: string; lineage: { relation: string; forked_at_tick: number } | null; provenance: string;
  diverges_from_tick: number | null;
  first_difference: null | {
    tick: number; t: string; entity_id: string; mode: string; place_id: string | null;
    a_event: Record<string, unknown> | null; b_event: Record<string, unknown> | null;
  };
  differing_entities: number;
  places_changed: { count: number; first: Array<{ place_id: string; first_differs_tick: number }> };
  measures: SimMeasure[];
  omitted: Array<{ id?: string; reason?: string } | string>;
}

export interface SimVerify {
  sim_run_id: string; provenance: string; stream_sha256: string; record_sha256: string;
  rule_version: string; stored_rule_version: string | null;
  replay_check: 'NOT_CHECKED' | 'MATCH' | 'MISMATCH' | string; checked_utc: string | null;
  replay_detail: null | { stream_match: boolean; record_match: boolean; replay_stream_sha256: string; replay_record_sha256: string };
  replay_job: null | { status: 'building' | 'complete' | 'failed' | string; started_at?: string; error?: { code: string; message: string } | null };
}

export interface SimForkReceipt {
  sim_run_id: string; status: 'building' | 'ready' | string; parent_id?: string; forked_at_tick?: number;
  provenance?: string; unchanged?: boolean;
}

/* ── catalogue ──────────────────────────────────────────────────────── */
export interface CatalogueRun {
  id: string; kind: string; title: string; created_at: string; status: string; facts?: Record<string, unknown>;
}
