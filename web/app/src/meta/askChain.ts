/**
 * ASK THE CHAIN: pure logic for the question box under the
 * EXPLAIN narrative. The model is allowed at the EDGE only: it may restate
 * the LOADED causal chain in words or refuse. It never sits in propagation
 * or grading, never invents a place or a number, never answers outside the
 * loaded chain.
 *
 *   request   POST /llm/ask {question, scope_node_ids, run_id}
 *             scope = the chain's OWN places (explicit); the run
 *             id grounds the server in the stored chain.
 *   preview   POST /llm/ask/preview (same body): the pre-call estimate, or
 *             the free refusal. Free on RECORDED, CONFIRM first when paid.
 *   reply     re-checked HERE against the chain record the page holds
 *             (second line after the server's own post-check): every number
 *             must be one a step record holds, every place id must be in the
 *             chain, no other catalogue place may be named. A miss discards
 *             the model text and shows the deterministic template (TEMPLATE
 *             chip, never the model chip).
 *   refusal   the server's machine code shown as a chip word (OUT_OF_GRAPH,
 *             NOT_IN_CHAIN, SCOPE_OUTSIDE_CHAIN, SPEND_CAP_REACHED, ...).
 *
 * `checkNarrative` is a port of server/llm/explain_check.py: the same
 * rules, so a sentence the server passes the page passes too.
 */
import type { Chain, CausalEvent } from '../types';
import { fmtUsd, parseProvenance, type LlmProvenance } from './provenance';
import { refusalWord } from './nlQuery';

export const ASK_MAX_CHARS = 280;

export interface AskRequest { question: string; scope_node_ids: string[]; run_id: string }

/** The chain's own places, in first-seen order (explicit scope). */
export function chainScope(chain: Pick<Chain, 'events'>): string[] {
  const seen: string[] = [];
  for (const ev of chain.events) if (!seen.includes(ev.node_id)) seen.push(ev.node_id);
  return seen;
}

/** null when there is nothing askable: no chain, no events or a blank question. */
export function buildAskRequest(chain: Chain | null | undefined, question: string): AskRequest | null {
  const q = question.replace(/\s+/g, ' ').trim().slice(0, ASK_MAX_CHARS);
  if (!chain || !chain.run_id || chain.events.length === 0 || !q) return null;
  return { question: q, scope_node_ids: chainScope(chain), run_id: chain.run_id };
}

/* ── post-check: port of server/llm/explain_check.py ─────────────────── */

const NUM_RE = /(?<![\w.])-?\d+(?:\.\d+)?/g;
const ID_RE = /\b(?:sea|air|gnd|chk|rail|road)_[a-z0-9_]+\b/g;

const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const decimals = (tok: string): number => (tok.includes('.') ? tok.split('.')[1].length : 0);
const roundTo = (v: number, d: number): number => Number(v.toFixed(d));

export function allowedValues(events: readonly CausalEvent[]): number[] {
  const vals: number[] = [events.length];
  for (const ev of events) {
    const e = ev as unknown as Record<string, unknown>;
    for (const k of ['seq', 'parent_id', 'tick']) if (num(e[k])) vals.push(e[k] as number);
    if (num(e.delay_d)) vals.push(e.delay_d);
    if (num(e.magnitude)) { vals.push(e.magnitude); vals.push(e.magnitude * 100); }
    if (typeof e.at === 'string') for (const g of e.at.match(/\d+/g) ?? []) vals.push(Number(g));
  }
  return vals;
}

export type CheckReason = 'number_not_in_chain' | 'node_not_in_chain';

/** (ok, reason): `reason` is a machine code, never model text. */
export function checkNarrative(text: string, chain: Pick<Chain, 'run_id' | 'stream_hash' | 'events'>): { ok: boolean; reason: CheckReason | null } {
  let scrubbed = text;
  for (const token of [chain.run_id, chain.stream_hash]) if (token) scrubbed = scrubbed.split(token).join(' ');
  const allowed = allowedValues(chain.events);
  for (const tok of scrubbed.match(NUM_RE) ?? []) {
    const shown = Number(tok);
    const d = decimals(tok);
    if (!allowed.some((v) => roundTo(v, d) === roundTo(shown, d))) return { ok: false, reason: 'number_not_in_chain' };
  }
  const nodes = new Set(chain.events.map((e) => e.node_id));
  for (const ident of new Set(scrubbed.match(ID_RE) ?? [])) {
    if (!nodes.has(ident)) return { ok: false, reason: 'node_not_in_chain' };
  }
  return { ok: true, reason: null };
}

/** Catalogue places NAMED in `text` that the chain does not contain. */
export function foreignPlaceNames(text: string, chain: Pick<Chain, 'events'>, catalogue: ReadonlyArray<{ id: string; name?: string }>): string[] {
  const inChain = new Set(chain.events.map((e) => e.node_id));
  const names = new Map(catalogue.map((n) => [n.id, (n.name ?? '').toLowerCase()]));
  const chainNames = [...inChain].map((id) => names.get(id) ?? '').filter(Boolean);
  const hay = text.toLowerCase();
  const out: string[] = [];
  for (const [id, name] of names) {
    if (inChain.has(id) || name.length < 5 || !hay.includes(name)) continue;
    if (chainNames.some((cn) => cn.includes(name))) continue;
    out.push(id);
  }
  return out;
}

/** The deterministic words for a chain, from the step records only
 *  (same text the server's render_template serves). */
export function renderTemplate(chain: Pick<Chain, 'run_id' | 'events'>): string {
  if (chain.events.length === 0) return `Run ${chain.run_id}: no events in the chain.`;
  const steps = chain.events.map((ev) => `step ${ev.seq}: ${ev.node_id} via ${ev.channel} (${ev.mechanism}), delay ${ev.delay_d} d`);
  return `Rule-based answer. Run ${chain.run_id}: ${steps.join('; ')}.`;
}

/* ── preview ─────────────────────────────────────────────────────────── */

export type AskPreviewView =
  | { kind: 'refused'; code: string }
  | { kind: 'estimate'; estimate: string | null; paid: boolean };

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

/** POST /llm/ask/preview -> a free refusal, or the cost chip + paid flag.
 *  Only the recorded-fixture transport is known free; anything else (http,
 *  or a transport this build does not know) asks first. null = unusable. */
export function askPreviewView(raw: unknown): AskPreviewView | null {
  if (!isObj(raw)) return null;
  if (raw.refused === true) return { kind: 'refused', code: typeof raw.reason === 'string' && raw.reason ? raw.reason : 'refused' };
  const est = num(raw.estimated_cost_usd) ? raw.estimated_cost_usd : null;
  const transport = typeof raw.transport === 'string' ? raw.transport.toLowerCase() : '';
  if (est === null && !transport) return null;
  return { kind: 'estimate', estimate: est === null ? null : `Estimate ${fmtUsd(est)}`, paid: !transport.includes('fixture') };
}

/* ── reply ───────────────────────────────────────────────────────────── */

export type AskView =
  /** The model's sentence, passed every check. */
  | { kind: 'answer'; text: string; cited: string[]; provenance: LlmProvenance | null; postCheck: 'PASS'; checkedBy: 'server+page' | 'page'; cost: string | null }
  /** Model text discarded (post-check failed): the deterministic template. */
  | { kind: 'template'; text: string; cited: string[]; provenance: LlmProvenance | null; postCheck: 'TEMPLATE_FALLBACK'; reason: string; by: 'server' | 'page'; cost: string | null }
  /** The server declined. `code` is its machine code. */
  | { kind: 'refused'; code: string; provenance: LlmProvenance | null; cost: string | null }
  /** A reply the page cannot trust at all (wrong shape, other run, no text). */
  | { kind: 'invalid'; code: string };

const stripFixturePrefix = (t: string): string => t.replace(/^\s*FIXTURE ANSWER\s*\([^)]*\)\s*\.?\s*/i, '');

/** What the settled charge of a paid call reads, only for a LIVE block. */
function costOf(p: LlmProvenance | null): string | null {
  return p && p.transport === 'LIVE' && p.live !== false && num(p.cost_usd) ? fmtUsd(p.cost_usd) : null;
}

/**
 * POST /llm/ask reply -> what the page may show. `chain` is the chain the
 * question was asked of (the page's own record); `catalogue` the served
 * place list (for the foreign-place check).
 */
export function askView(raw: unknown, chain: Chain, catalogue: ReadonlyArray<{ id: string; name?: string }>): AskView {
  if (!isObj(raw)) return { kind: 'invalid', code: 'reply_not_an_object' };
  const parsed = parseProvenance(raw.provenance);
  const provenance = parsed && parsed.transport !== 'NONE' ? parsed : null;
  if (raw.refused === true) {
    const code = typeof raw.reason === 'string' && raw.reason ? raw.reason : 'refused';
    return { kind: 'refused', code, provenance, cost: costOf(provenance) };
  }
  if (typeof raw.answer !== 'string' || !raw.answer.trim()) return { kind: 'invalid', code: 'no_answer' };
  if (typeof raw.run_id === 'string' && raw.run_id !== chain.run_id) return { kind: 'invalid', code: 'other_run' };
  if (!provenance) return { kind: 'invalid', code: 'no_provenance' };
  const inChain = new Set(chain.events.map((e) => e.node_id));
  const cited = (Array.isArray(raw.cited_node_ids) ? raw.cited_node_ids : [])
    .filter((c): c is string => typeof c === 'string' && inChain.has(c));
  const cost = costOf(provenance);
  const serverFell = raw.post_check === 'TEMPLATE_FALLBACK' || raw.answer_source === 'template';
  if (serverFell) {
    const reason = typeof raw.post_check_reason === 'string' && raw.post_check_reason ? raw.post_check_reason : 'server_post_check';
    // The server's template is built from the step records, so it passes the
    // same check; if it somehow does not, the page's own template is shown.
    const text = checkNarrative(raw.answer, chain).ok ? raw.answer.replace(/^TEMPLATE\.\s*/, 'Rule-based answer. ') : renderTemplate(chain);
    return { kind: 'template', text, cited, provenance, postCheck: 'TEMPLATE_FALLBACK', reason, by: 'server', cost };
  }
  // The page's own check, on the RAW text (before any cosmetic cleaning).
  const own = checkNarrative(raw.answer, chain);
  const foreign = own.ok ? foreignPlaceNames(raw.answer, chain, catalogue) : [];
  if (!own.ok || foreign.length > 0) {
    return {
      kind: 'template', text: renderTemplate(chain), cited, provenance, postCheck: 'TEMPLATE_FALLBACK',
      reason: own.reason ?? 'node_not_in_chain', by: 'page', cost,
    };
  }
  return {
    kind: 'answer', text: stripFixturePrefix(raw.answer), cited, provenance, postCheck: 'PASS',
    checkedBy: raw.post_check === 'PASS' ? 'server+page' : 'page', cost,
  };
}

/** Chip word for a refusal / fallback / error code. */
export const askCodeWord = (code: string): string => refusalWord(code);

/** Suggested questions (chips). Only shapes the answerer can ground in a
 *  chain; the selected step's place makes the "why" question concrete. */
export function suggestQuestions(chain: Chain | null | undefined, selectedName: string | null): string[] {
  if (!chain || chain.events.length === 0) return [];
  const out = ['Which step adds the most delay?'];
  if (selectedName) out.push(`Why did ${selectedName} get hit?`);
  out.push('How many steps are in the chain?');
  return out;
}
