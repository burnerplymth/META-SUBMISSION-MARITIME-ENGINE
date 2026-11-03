/**
 * META PROVENANCE — the one pure place a served LLM
 * provenance block becomes a chip view-model. The chip is built from the block ALONE:
 *
 *   LIVE      the http transport answered (a real model call): show the
 *             model id, LIVE, the settled cost, the cached flag.
 *   RECORDED  the recorded-fixture transport answered. NOT a live model:
 *             the chip says RECORDED and never a vendor/model name; the
 *             model string the fixture reports is only listed in the
 *             expanded form, as served.
 *   NONE      no model was called -> no chip at all.
 *
 * Honesty rules enforced here:
 *   - absent / malformed / NONE provenance -> null (nothing renders,
 *     never a fake chip);
 *   - LIVE is claimed only when every signal agrees. Any contradictory
 *     signal (transport_raw fixture, live === false) downgrades to
 *     RECORDED, the safe direction;
 *   - cost is shown for LIVE only, and only when it is a finite number.
 */

export type MetaTransport = 'LIVE' | 'RECORDED';

/** The served provenance block, only the fields the chip reads. */
export interface LlmProvenance {
  model: string | null;
  transport: string;
  transport_raw?: string;
  live?: boolean;
  prompt_id?: string | null;
  prompt_version?: string | null;
  cost_usd?: number;
  cached?: boolean;
  estimated_cost_usd?: number;
  narrative_source?: string;
  post_check?: string;
  note?: string;
}

export interface MetaRow { k: string; v: string }

export interface MetaChipView {
  transport: MetaTransport;
  /** 'live' | 'recorded': drives the mark (solid dot or ring), never the wording. */
  tone: 'live' | 'recorded';
  /** The state word in sentence case: Live | Recorded. */
  word: 'Live' | 'Recorded';
  /** The model id, only for Live (a recorded answer carries none on the badge). */
  modelWord: string | null;
  cached: boolean;
  /** "$0.0012", only for Live with a finite served cost. */
  costWord: string | null;
  /** One-line summary: "Meta AI · Recorded". */
  compact: string;
  /** Popover rows in order (Model, Mode, Prompt id, Cost, Cached); every
   *  value is as served, a missing one is an en dash. */
  rows: MetaRow[];
  /** Full accessible name of the badge: "Meta AI, recorded". */
  aria: string;
}

/** A missing value is an en dash. */
export const NA = '\u2013';

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown): string | undefined => (typeof v === 'string' && v.trim() ? v : undefined);
const fin = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : undefined);

/** Validate an unknown payload field into a provenance block, or null. */
export function parseProvenance(raw: unknown): LlmProvenance | null {
  if (!isObj(raw)) return null;
  const transport = str(raw.transport)?.toUpperCase();
  if (transport !== 'LIVE' && transport !== 'RECORDED' && transport !== 'NONE') return null;
  const p: LlmProvenance = {
    model: typeof raw.model === 'string' ? raw.model : null,
    transport,
  };
  const tr = str(raw.transport_raw); if (tr) p.transport_raw = tr;
  if (typeof raw.live === 'boolean') p.live = raw.live;
  const pid = str(raw.prompt_id); if (pid) p.prompt_id = pid;
  const pv = str(raw.prompt_version); if (pv) p.prompt_version = pv;
  const cost = fin(raw.cost_usd); if (cost !== undefined) p.cost_usd = cost;
  const est = fin(raw.estimated_cost_usd); if (est !== undefined) p.estimated_cost_usd = est;
  if (typeof raw.cached === 'boolean') p.cached = raw.cached;
  const ns = str(raw.narrative_source); if (ns) p.narrative_source = ns;
  const pc = str(raw.post_check); if (pc) p.post_check = pc;
  const note = str(raw.note); if (note) p.note = note;
  return p;
}

/** Dollar amount as the chip prints it: more places for sub-cent calls. */
export function fmtUsd(v: number): string {
  if (v === 0) return '$0.00';
  const a = Math.abs(v);
  if (a >= 1) return `$${v.toFixed(2)}`;
  if (a >= 0.0001) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(6)}`;
}

/** Decide LIVE vs RECORDED from every served signal. null = no model ran. */
export function effectiveTransport(p: LlmProvenance | null | undefined): MetaTransport | null {
  if (!p || p.transport === 'NONE') return null;
  if (p.transport === 'RECORDED') return 'RECORDED';
  // transport === 'LIVE': believe it only when nothing contradicts it.
  const raw = p.transport_raw?.toLowerCase();
  if (p.live === false || raw === 'fixture' || raw === 'none') return 'RECORDED';
  return 'LIVE';
}

export function metaChipView(p: LlmProvenance | null | undefined, label = 'Meta AI'): MetaChipView | null {
  const transport = effectiveTransport(p);
  if (!p || !transport) return null;
  const live = transport === 'LIVE';
  const word = live ? 'Live' : 'Recorded';
  const modelWord = live ? (p.model && p.model.trim() ? p.model : null) : null;
  const cached = p.cached === true;
  const costWord = live && p.cost_usd !== undefined ? fmtUsd(p.cost_usd) : null;
  const rows: MetaRow[] = [
    { k: 'Model', v: p.model && p.model.trim() ? p.model : NA },
    { k: 'Mode', v: word },
    { k: 'Prompt id', v: p.prompt_id ?? NA },
    { k: 'Cost', v: p.cost_usd !== undefined ? fmtUsd(p.cost_usd) : NA },
    { k: 'Cached', v: p.cached === undefined ? NA : p.cached ? 'Yes' : 'No' },
  ];
  if (p.estimated_cost_usd !== undefined) rows.push({ k: 'Estimate', v: fmtUsd(p.estimated_cost_usd) });
  return {
    transport, tone: live ? 'live' : 'recorded', word, modelWord, cached, costWord, compact: `${label} \u00b7 ${word}`, rows,
    aria: `${label}, ${word.toLowerCase()}`,
  };
}

/** EXPLAIN / narrate: which chip shows. A deterministic-template fallback
 *  (post-check failed) shows a TEMPLATE chip and NOT the model chip. */
export interface ExplainMeta {
  provenance: LlmProvenance | null;
  template: boolean;
  /** Post-check word as served (PASS / TEMPLATE_FALLBACK), else null. */
  postCheck: string | null;
}
export function explainMeta(data: { provenance?: unknown; narrative_source?: unknown; post_check?: unknown }): ExplainMeta {
  const provenance = parseProvenance(data.provenance);
  const source = str(data.narrative_source) ?? provenance?.narrative_source;
  const postCheck = str(data.post_check) ?? provenance?.post_check ?? null;
  const template = source?.toLowerCase() === 'template' || postCheck === 'TEMPLATE_FALLBACK';
  return { provenance: template ? null : provenance, template, postCheck };
}
