/**
 * CLASSIFIER RESULT VIEW: POST /llm/classify -> chips. The
 * classifier is an EDGE-IN model: it reads one feed headline and returns
 * a structured candidate. The UI shows the candidate as chips only —
 * the free-text `reason` is deliberately NOT rendered (no explanatory
 * prose) — and re-validates it client-side:
 *
 *   - kind must be in the closed enum, severity an int 1..5, confidence
 *     a finite number in [0,1]; anything else -> Unclassified;
 *   - the place must be a place the served catalogue holds; a model
 *     place id the catalogue does not know is shown as an UNKNOWN PLACE
 *     refusal, never as a place name.
 */
import { parseProvenance, fmtUsd, type LlmProvenance } from './provenance';

export const CLASSIFY_KINDS = ['closure', 'capacity_cut', 'congestion', 'strike', 'weather', 'grounding', 'accident', 'policy', 'other'] as const;

export interface ClassifyView {
  state: 'classified' | 'unclassified';
  /** Tags in display order; each is a short sentence-case word. */
  kind: string | null;
  severity: number | null;
  confidence: number | null;
  /** Catalogue place id, only when the catalogue knows it. */
  placeId: string | null;
  /** The model named a place the catalogue does not hold. */
  unknownPlace: boolean;
  relevant: boolean | null;
  mintable: boolean | null;
  provenance: LlmProvenance | null;
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

export function classifyView(raw: unknown, knownPlaceIds: ReadonlySet<string>): ClassifyView | null {
  if (!isObj(raw)) return null;
  const provenance = parseProvenance(raw.provenance);
  const kind = typeof raw.kind === 'string' && (CLASSIFY_KINDS as readonly string[]).includes(raw.kind) ? raw.kind : null;
  const sev = typeof raw.severity === 'number' && Number.isInteger(raw.severity) && raw.severity >= 1 && raw.severity <= 5 ? raw.severity : null;
  const conf = typeof raw.confidence === 'number' && Number.isFinite(raw.confidence) && raw.confidence >= 0 && raw.confidence <= 1 ? raw.confidence : null;
  const nodeId = typeof raw.node_id === 'string' && raw.node_id ? raw.node_id : null;
  const placeId = nodeId && knownPlaceIds.has(nodeId) ? nodeId : null;
  const unknownPlace = !!nodeId && !placeId;
  const servedUnclassified = raw.unclassified === true;
  const valid = kind !== null && sev !== null && conf !== null && !servedUnclassified;
  return {
    state: valid ? 'classified' : 'unclassified',
    kind: valid ? kind : null,
    severity: valid ? sev : null,
    confidence: valid ? conf : null,
    placeId,
    unknownPlace,
    relevant: typeof raw.relevant === 'boolean' ? raw.relevant : null,
    mintable: typeof raw.mintable === 'boolean' ? raw.mintable : null,
    provenance,
  };
}

export interface ClassifyPreviewView {
  /** "Estimate $0.000462" */
  estimate: string | null;
  /** True unless the preview says the call is a free recorded one. */
  paid: boolean;
}

/** POST /llm/classify/preview -> the cost chip. `paid` only when the
 *  served transport is the http (LIVE) one; fixture = no charge. */
export function classifyPreviewView(raw: unknown): ClassifyPreviewView | null {
  if (!isObj(raw)) return null;
  const est = typeof raw.estimated_cost_usd === 'number' && Number.isFinite(raw.estimated_cost_usd) ? raw.estimated_cost_usd : null;
  const transport = typeof raw.transport === 'string' ? raw.transport.toLowerCase() : '';
  if (est === null && !transport) return null;
  // Only the recorded-fixture transport is known free; anything else
  // (http, or a transport this build does not know) asks first.
  return { estimate: est === null ? null : `Estimate ${fmtUsd(est)}`, paid: !transport.includes('fixture') };
}

/** Sentence case: `capacity_cut` -> `Capacity cut`. */
export const kindWord = (k: string): string => { const t = k.replace(/_/g, ' '); return t.charAt(0).toUpperCase() + t.slice(1); };
