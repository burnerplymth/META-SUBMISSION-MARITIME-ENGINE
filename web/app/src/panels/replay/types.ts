/* Replay window: the documents served by GET /replay/episodes and GET /replay/episodes/{id}. Plain data as stored;
 * the classification into matched / not flagged / not observed happens in logic.ts. */

export type Split = 'TRAIN' | 'TEST' | null;

export interface EpisodeCard {
  id: string;
  title: string;
  date_range: { start: string; end: string };
  one_liner: string;
  restricted: boolean;
  place_id: string;
  badges: string[];
  status: { split: Split; not_blind: boolean };
}

export interface EpisodeList { schema: string; mode: 'RECORDED'; include_restricted: boolean; episodes: EpisodeCard[] }

export interface ObsEvent {
  id: string; order: number; date: string; label: string; detail: string | null;
  publisher: string; badge: string; published: string; url: string | null; retrieved_on: string | null;
}
export interface ObsSeries {
  id: string; label: string; unit: string; place_id: string; metric: string;
  publisher: string; url: string | null; retrieved_on: string | null; points: Array<[string, number]>;
}
export interface Observed { events: ObsEvent[]; series: ObsSeries[]; reported: Array<Record<string, unknown>> }

export interface ChainEventDoc {
  seq: number; parent_id: number | null; node_id: string; channel: string; mechanism: string | null;
  delay_d: number; magnitude: number | null; at: string; tick: number;
}
export interface ChainDoc {
  schema: string; episode: string; config: string; run_id: string; status: string; event_count: number; stream_hash: string;
  events: ChainEventDoc[]; edges: Array<{ from_seq: number | null; to_seq: number; from_node: string; to_node: string; channel: string }>;
  disruption: { node_id: string; kind: string; capacity_loss: number; duration_d: number; started_at: string; shape: string };
  digest_checked_against_published: boolean;
}

export type Basis = 'portwatch' | 'dossier' | 'simulation' | 'reported';
export type PairKind = 'place' | 'step' | 'timing' | 'metric' | 'reported';

export interface Pair {
  key: string;
  basis: Basis;
  kind: PairKind;
  subject: { place_id?: string; label?: string };
  role?: string | null;
  hops?: number | null;
  unit?: string;
  timing?: boolean;
  metric_id?: string;
  band?: { abs_d: number } | null;
  band_text?: string | null;
  window?: [number, number] | null;
  /** observed side as stored; null = no observed record */
  observed: Record<string, unknown> | null;
  /** modelled side as stored; null = the pair was not compared */
  model: Record<string, unknown> | null;
  not_compared?: string;
  ungraded?: string;
  grade?: string | null;
  previous_grade?: string | null;
  obs_detail?: string | null;
  sim_detail?: string | null;
}

export interface FileRef { name: string; file: string; sha256: string }
export interface EngineDoc {
  id: string; label: string; available: boolean; reason: 'outside_core' | 'not_run' | null;
  prereg?: FileRef; scorecard?: FileRef; config_hash?: string;
  chain?: ChainDoc; pairs?: Pair[];
}
export interface SimSeries { id: string; label: string; unit: string; start: string; disrupted: number[]; twin: number[] }
export interface SimDoc {
  id: string; label: string; prereg: FileRef; scorecard: FileRef;
  pairs: Pair[]; series: SimSeries[];
}
export interface EpisodeDoc extends EpisodeCard {
  schema: string; mode: 'RECORDED';
  window: { start: string; end: string };
  disruption: { place_id: string; kind: string; capacity_loss: number; duration_d: number; started_at: string; basis: string | null } | null;
  observed: Observed; engines: EngineDoc[]; simulation: SimDoc | null;
  provenance: { files: Array<{ file: string; sha256: string; role: string }>; assembled_from: string; mode: 'RECORDED' };
}

/* ── classified rows ───────────────────────────────────────────────────── */
export type Status =
  | 'matched' | 'not_flagged' | 'not_observed' | 'opposite'
  | 'within' | 'outside' | 'not_produced'
  | 'ungraded' | 'not_compared' | 'quiet';

export interface Row {
  key: string;
  basis: Basis;
  subject: string;
  placeId: string | null;
  status: Status;
  observed: string;
  model: string;
  /** model minus observed, with its unit; null when either side is absent */
  difference: { value: number; unit: string } | null;
  /** timing offset in days, model minus observed; null when either date is absent */
  timingD: number | null;
  /** magnitude descriptor, e.g. "0.7x observed" */
  magnitude: string | null;
  observedDate: string | null;
  modelDate: string | null;
  publisher: string | null;
  url: string | null;
  note: string | null;
}
