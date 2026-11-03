/* DISRUPTION BRIEF: the served document (server/brief, schema brief/1). Only
 * the fields the window reads. Every number a person sees is a fact's
 * `display`; the window never computes one. */

export type BriefTransport = 'LIVE' | 'RECORDED';

export interface BriefSource {
  type: string;
  label: string;
  url?: string;
  publisher?: string;
  title?: string;
  date?: string;
  retrieved_at?: string;
  [k: string]: unknown;
}

export interface BriefFact {
  id: string;
  key: string;
  label: string;
  kind: string;
  display: string;
  unit: string | null;
  chip: string;
  section: string;
  source: BriefSource;
}

export interface Seg { t: string; fact?: string }

export interface ParaBlock { id: string; kind: 'paragraph'; text: string; segments: Seg[]; fact_ids: string[]; source: string }
export interface BulletItem { text: string; segments: Seg[]; fact_ids: string[] }
export interface BulletsBlock { id: string; kind: 'bullets'; items: BulletItem[]; fact_ids: string[]; source: string }
export interface TableColumn { label: string; unit: string | null; num: boolean }
export interface TableRow { fact_id: string | null; cells: string[]; state?: string }
export interface TableBlock { id: string; kind: 'table'; title: string; columns: TableColumn[]; rows: TableRow[]; chip: string | null; note: string | null; fact_ids: string[] }
export interface FigureBlock { id: string; kind: 'figure'; figure_id: string; title: string; chip: string; fact_ids: string[] }
export type BriefBlock = ParaBlock | BulletsBlock | TableBlock | FigureBlock;

export interface BriefSection {
  id: string;
  title: string;
  state: 'SERVED' | 'NOT SERVED';
  reason: string | null;
  chips: string[];
  blocks: BriefBlock[];
}

export interface BriefFigure { id: string; title: string; chip: string; svg: string }
export interface BriefKeyFigure { label: string; value: string; fact_id: string; chip: string }
export interface BriefSourceEntry {
  id: string; kind: string; label: string; fact_ids: string[];
  publisher?: string; title?: string; url?: string; date?: string; state?: string; retrieved_at?: string;
}
export interface BriefVerifier { checked: number; passed: number; flagged: Array<{ block: string; reason: string }>; dropped: number }

export interface BriefProvenance {
  model: string | null; transport: string; transport_raw?: string; live?: boolean;
  prompt_id?: string | null; prompt_version?: string | null; cost_usd?: number; cached?: boolean; estimated_cost_usd?: number;
}

export interface BriefDoc {
  schema: string;
  brief_id: string;
  title: string;
  subject: string;
  generated_at: string;
  label: string;
  transport: BriefTransport;
  provenance: BriefProvenance;
  writer: { source: string; fallback_reason: string | null; prompt_id: string | null; model: string | null };
  cost: { usd: number; estimated_usd: number };
  run: { run_id: string; stream_hash: string | null; engine_label: string; map_version?: string | null };
  summary: { text: string; segments: Seg[]; fact_ids: string[]; source: string };
  key_figures: BriefKeyFigure[];
  sections: BriefSection[];
  figures: BriefFigure[];
  facts: BriefFact[];
  sources: BriefSourceEntry[];
  verifier: BriefVerifier;
  words: number;
  sha256: string;
  exports: { html_url: string; pdf_url: string; json_url: string };
  cached?: boolean;
}

export interface BriefRequest {
  run_id: string;
  mc_job_id?: string;
  lane_plan?: { origin: string; destination: string };
  research_episode_id?: string;
}

export interface BriefEstimate {
  brief_id: string;
  transport: BriefTransport;
  model: string | null;
  estimated_cost_usd: number;
  estimated_if_live_usd: number;
  ceiling_usd: number;
  will_call_model: boolean;
  cached: boolean;
}

export interface BriefStatus { enabled: boolean; transport: BriefTransport; model: string | null; pdf: { available: boolean } }

export type BriefResult<T> =
  | { kind: 'ok'; doc: T }
  | { kind: 'notserved' }
  | { kind: 'refused'; code: string; message: string }
  | { kind: 'down'; code: string };
