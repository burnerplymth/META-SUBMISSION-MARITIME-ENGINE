/**
 * ENSEMBLE STATISTICS — static copy + pure display logic.
 *
 * Every fixed string the surface renders lives in ENS_COPY, in one
 * place, so the wording sits in exactly one file (the
 * search copy.ts pattern). These
 * strings — and every generated string below — follow the vocabulary
 * law (a place is a place, a connection is a leg or route, the whole
 * is the network; never the engine-room words) and against the
 * raw-identifier ban. Served strings (the payload's own labelling,
 * definitions, axis descriptions, count note) are NOT copy: they are
 * rendered from the payload, passed through the shared formatters.
 *
 * The honesty rule that shapes this module: a share is a COUNT of
 * variants. No helper here ever produces a percentage — the count
 * form ("reached in 6,214 of 10,000 variants") is the only form.
 */
import type { Chain, ChainEdge, DisruptionSpec, EnsCurveBin, EnsVariantData } from '../types';
import { labelWord } from '../copy';

export const ENS_COPY = {
  title: 'Ensemble statistics',
  /** The payload's labelling title, verbatim — also the pre-job text,
   *  so the panel states the same words before any job exists. */
  labellingTitle: 'Ensemble statistics, causal variants',
  labellingStatement: 'Every figure is computed over the variants actually run. Each variant is one named combination of the variation axes printed here, run through the same deterministic causal engine as a single run and nothing is sampled, so every variant replays exactly and its full causal chain can be opened. A share is a count of variants (for example, \'reached in 6,214 of 10,000 variants\'), never a real-world chance. Weighting is uniform over the variants run.',
  contextDerived: 'DERIVED FROM CHAIN ROOT',
  contextStored: 'STORED SCENARIO',
  staleNote: 'SCENARIO CHANGED · RUN AGAIN',
  runButton: 'Run ensemble statistics',
  rerunButton: 'Run again',
  runningWord: 'RUNNING',
  interruptedWord: 'INTERRUPTED',
  interruptedNote: 'No worker attached · not running · run again',
  failedWord: 'FAILED',
  completeWord: 'COMPLETE',
  noJobs: 'NO JOBS YET',
  storedJobs: 'Stored jobs',
  outcomesHead: 'Outcome statistics — over the variants run',
  curveHead: 'Distribution — variants per outcome band',
  axesHead: 'Variation axes · each variant picks one level',
  baseVariantHead: 'Variant 0 — the base combination',
  branchesButton: 'Open the causal chain results',
  branchesTitle: 'CAUSAL CHAIN RESULTS — BRANCHES',
  placesHead: 'Places — reached by the chain',
  legsHead: 'Legs — carrying an effect onward',
  backToStats: 'Ensemble statistics',
  backToBranches: 'All branches',
  firingHead: 'Variants where this branch fired',
  openVariant: 'Open chain',
  cappedNote: '† CAPPED · OUTCOME PAST TOP OF SERVED RANGE',
  resolutionNote: 'Percentile resolution',
  variantsShownNote: 'firing variants listed',
  emptyBranches: 'NO BRANCH FIRED',
  unreachable: 'Ensemble statistics unavailable',
  countInvalid: 'Enter a whole number of variants between 1 and 10,000.',
  jobFor: 'Job for',
  requested: 'requested',
  runs: 'runs',
  gridOf: 'distinct variants in the declared grid',
} as const;

/** Axis keys → the short screen label. The served axis descriptions
 *  carry the full wording; these are just the row names. */
export const AXIS_LABELS: Record<string, string> = {
  duration_mult: 'Duration',
  capacity_loss_mult: 'Capacity loss',
  queue_sensitivity: 'Queue sensitivity',
  hit_threshold: 'Hit threshold',
  shape: 'Shape',
};
export const AXIS_ORDER = ['duration_mult', 'capacity_loss_mult', 'queue_sensitivity', 'hit_threshold', 'shape'];

/** The four served outcomes, in display order, with their axis unit. */
export const OUTCOME_META: Array<{ key: string; label: string; unit: string; count?: boolean }> = [
  { key: 'worst_path_delay_d', label: 'Worst path delay', unit: 'days' },
  { key: 'queue_peak_delay_d', label: 'Queue peak delay', unit: 'days' },
  { key: 'places_reached', label: 'Places reached', unit: 'places', count: true },
  { key: 'chain_events', label: 'Chain events', unit: 'events', count: true },
];

export const fmtInt = (n: number): string => n.toLocaleString('en-US');

/** Days / counts alike: integers plain, fractions at one decimal. */
export const fmtVal = (v: number | null | undefined): string => {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
};

/** A percentile, minimum or maximum of an integer count (places, events): shown as a whole number.
 *  The served percentile can be fractional (an interpolated value such as 8.05); the display rounds. */
export const fmtCountStat = (v: number | null | undefined): string => {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  return String(Math.round(v));
};

/** The count form — the ONLY way a share is ever rendered. */
export const countOf = (fired: number, total: number): string =>
  `${fmtInt(fired)} of ${fmtInt(total)}`;

export const reachedText = (fired: number, total: number): string =>
  `reached in ${countOf(fired, total)} variants`;

export const carriedText = (fired: number, total: number): string =>
  `carried an effect in ${countOf(fired, total)} variants`;

/** Served status → the word the panel prints. `interrupted` is the
 *  API's own honest word for a persisted record with no live worker;
 *  it is rendered, never spun over. */
export function statusWord(status: string): string {
  switch (status) {
    case 'running': return ENS_COPY.runningWord;
    case 'interrupted': return ENS_COPY.interruptedWord;
    case 'complete': return ENS_COPY.completeWord;
    case 'failed': return ENS_COPY.failedWord;
    default: return labelWord(status);
  }
}

/** Custom-count input → a validated count or an error worded for the
 *  panel's honest line. The API enforces the same bounds; the client
 *  check exists so a bad count never becomes a request. */
export function parseCount(text: string): { count: number } | { error: string } {
  const t = text.trim();
  if (!/^\d+$/.test(t)) return { error: ENS_COPY.countInvalid };
  const n = Number(t);
  if (!Number.isInteger(n) || n < 1 || n > 10000) return { error: ENS_COPY.countInvalid };
  return { count: n };
}

/**
 * P3 chart grammar for the distribution curve: the served bins arrive
 * at fixed fine resolution (0.1-day bins to 200 days, 1-day above,
 * plus an explicit overflow bin whose `hi` is null). Sparse sets pass
 * through untouched — the component centres them on the axis, so a
 * handful of outcomes never becomes a full-width slab. Dense sets are
 * aggregated into at most `maxCols` consecutive columns with counts
 * summed — openly, with `aggregated` set so the panel states it, and
 * the overflow bin always lands in the final column with its count
 * intact, so nothing ever clips silently.
 */
export interface CurveCol { lo: number; hi: number | null; count: number }
export function aggCurve(bins: EnsCurveBin[], maxCols: number): { cols: CurveCol[]; aggregated: boolean; total: number } {
  const total = bins.reduce((a, b) => a + b.count, 0);
  if (bins.length <= maxCols) {
    return { cols: bins.map((b) => ({ ...b })), aggregated: false, total };
  }
  const per = Math.ceil(bins.length / maxCols);
  const cols: CurveCol[] = [];
  for (let i = 0; i < bins.length; i += per) {
    const group = bins.slice(i, i + per);
    cols.push({
      lo: group[0].lo,
      hi: group[group.length - 1].hi,
      count: group.reduce((a, b) => a + b.count, 0),
    });
  }
  return { cols, aggregated: true, total };
}

/** Column axis label: "0.0–0.1" for a bounded band, "1,200+" for the
 *  overflow column (hi null), a bare value for a zero-width band. */
export function colLabel(col: CurveCol): string {
  if (col.hi === null) return `${fmtInt(Math.round(col.lo))}+`;
  if (col.hi === col.lo) return fmtVal(col.lo);
  return `${fmtVal(col.lo)}–${fmtVal(col.hi)}`;
}

/** One variant's axis combination as a single line of named levels —
 *  the identity of the variant wherever it is listed or opened. */
export function combinationText(combo: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const axis of AXIS_ORDER) {
    if (!(axis in combo)) continue;
    const v = combo[axis];
    const label = AXIS_LABELS[axis] ?? axis;
    const val = typeof v === 'number' ? String(v) : String(v);
    parts.push(axis.endsWith('_mult') ? `${label} ×${val}` : `${label} ${val}`);
  }
  for (const [k, v] of Object.entries(combo)) {
    if (!AXIS_ORDER.includes(k)) parts.push(`${k} ${String(v)}`);
  }
  return parts.join(' · ');
}

/** Served axis levels as one line ("0.5, 0.7, 0.85, 1, 1.18, 1.4, 1.65"). */
export function levelsText(levels: unknown[]): string {
  return levels.map((v) => (typeof v === 'number' ? String(v) : String(v))).join(', ');
}

/** A variant's replayed chain, shaped for the store's chain view: the
 *  events verbatim, the routes between steps derived from the served
 *  parent links — exactly the routes the chain rendering draws. */
export function variantChain(data: EnsVariantData): Chain {
  const bySeq = new Map(data.events.map((e) => [e.seq, e]));
  const edges: ChainEdge[] = [];
  for (const e of data.events) {
    if (e.parent_id === null) continue;
    const parent = bySeq.get(e.parent_id);
    if (!parent) continue;
    edges.push({ from_seq: e.parent_id, to_seq: e.seq, from_node: parent.node_id, to_node: e.node_id, channel: e.channel });
  }
  return {
    run_id: data.variant.run_id,
    status: 'complete',
    event_count: data.events.length,
    stream_hash: data.variant.stream_hash,
    events: data.events,
    edges,
  };
}

/** Job progress, as printed while a job runs and in its stored row. */
export const progressText = (done: number, planned: number): string =>
  `${fmtInt(done)} of ${fmtInt(planned)} variants computed`;

/** The firing-variant list is capped for display; the cap is stated,
 *  never silent (the P3 rule applied to a list instead of a chart). */
export const shownNote = (shown: number, total: number): string =>
  `Showing the first ${fmtInt(shown)} of ${fmtInt(total)} firing variants`;

/** Dense-curve aggregation, stated beside the curve it describes. */
export const groupedNote = (bins: number, cols: number): string =>
  `Bands grouped ${fmtInt(bins)} → ${fmtInt(cols)} for display — counts summed, nothing dropped.`;

/** The open final band of a distribution, stated beside the curve. */
export const overflowNote = (col: CurveCol, unit: string): string =>
  `The final band is open: ${fmtInt(col.count)} variants beyond ${fmtVal(col.lo)} ${unit}.`;

/** Staleness key for the scenario context: the five fields that define
 *  the disruption, so a served spec and the panel's spec compare equal
 *  regardless of key order or extra served fields. */
export function specKey(spec: DisruptionSpec | null | undefined): string {
  if (!spec) return '';
  return [spec.node_id, spec.kind, spec.capacity_loss, spec.duration_d, spec.shape].join('|');
}
