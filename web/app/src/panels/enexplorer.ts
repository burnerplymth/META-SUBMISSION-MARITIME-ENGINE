/**
 * ENSEMBLE EXPLORER — static copy + pure display logic for the two
 * modes over the flat ensemble layer: precomputed
 * combination TREES and saved EXPLORATIONS (interactive deepening).
 *
 * Every fixed string the two surfaces render lives in ENX_COPY, in
 * one place, so the wording sits in exactly one file
 * (the ENS_COPY pattern). These strings —
 * and every generated string below — follow the vocabulary law and
 * the raw-identifier ban. Served strings (the payloads' labelling,
 * the quote's arithmetic and per-level table, prune reasons, count
 * notes, axis descriptions) are NOT copy: they render from the
 * payload through the shared formatters.
 *
 * The honesty rules that shape this module, mirroring the server:
 *  - a quote is computed before anything runs, and its per-level
 *    table is rendered from the served levels, never recomputed;
 *  - a tree over the budget cannot launch from this surface: the
 *    budget is raised only by a deliberate control that names the
 *    raised figure, and an over-hard-maximum tree renders the
 *    refusal from the served quote — no job is created either way;
 *  - a pruned tree member renders as pruned, with its served reason;
 *  - a share is a count of variants — no percentage exists here.
 */
import type {
  EnsExplorationDetail, EnsExplorationMember, EnsTreeMember, EnsTreeQuote,
} from '../types';
import { AXIS_LABELS, fmtInt, fmtVal, combinationText } from './ensstats';

export const ENX_COPY = {
  modeStats: 'STATISTICS',
  modeTree: 'COMBINATION TREE',
  modeExplorations: 'EXPLORATIONS',
  // ── Tree launcher ──
  treeHead: 'Combination tree — refine one axis per level',
  treeIntro: 'A tree job runs the base variant, then refines one axis at a time: each level takes every member the level above produced and varies the next axis across that level’s branching factor. Quote first — the quote runs nothing and persists nothing.',
  branchingLabel: 'Branching per level (comma-separated)',
  branchingHint: 'One factor per level · max 5 levels · default 10',
  branchingInvalid: 'ENTER 1–5 WHOLE NUMBERS · COMMA-SEPARATED',
  quoteButton: 'Quote this tree',
  quoting: 'Quoting…',
  launchButton: 'Launch tree job',
  launching: 'Launching…',
  quoteHead: 'Quote — nothing has run',
  quoteLevelsHead: 'Runs each level introduces',
  quotedTotal: 'Variants the levels introduce',
  withBase: 'with the base variant',
  estWall: 'Estimated wall time',
  estSize: 'Estimated record size',
  estimatorBasis: 'Estimate basis (served)',
  budgetLine: 'Budget',
  defaultBudgetWord: 'default',
  hardMaxWord: 'hard maximum',
  readyNote: 'WITHIN BUDGET · LAUNCH RUNS THE QUOTED VARIANTS',
  raiseHead: 'Over the default budget',
  raiseNote: 'OVER QUOTED BUDGET · RAISE TO LAUNCH',
  raiseLabel: 'Raise the budget to',
  raiseButton: 'Raise budget and re-quote',
  raiseInvalid: 'ENTER A WHOLE NUMBER ≥ QUOTED TOTAL',
  refusedHead: 'Refused — nothing ran',
  refusedOverMax: 'OVER HARD MAXIMUM · REDUCE THE TREE',
  refusedOverBudget: 'OVER BUDGET · RAISE IT OR REDUCE THE TREE',
  treeJobsHead: 'Stored tree jobs',
  noTreeJobs: 'NO TREE JOBS YET',
  treeStaleNote: 'SCENARIO CHANGED · QUOTE AGAIN',
  // ── Tree explorer ──
  levelWord: 'LEVEL',
  baseLevelName: 'Base',
  membersWord: 'members',
  memberWord: 'member',
  showMore: 'Show more members',
  showingOf: 'Showing',
  expandedWord: 'EXPANDED',
  prunedWord: 'PRUNED — NOT EXPANDED',
  finalLevelWord: 'FINAL LEVEL',
  baseMember: 'Base combination',
  subtreeWord: 'in its part of the tree',
  accountingLine: 'Accounting (served)',
  levelPlanHead: 'Level plan — the axis each level refines',
  filterActive: 'FILTERED TO THE SELECTED MEMBER',
  filterShowAll: 'Show all members',
  showChildren: 'Show the members refined from this one',
  memberHead: 'Member',
  basisStored: 'Aggregates basis: stored with the job',
  basisReplayed: 'Aggregates basis: re-derived by replay · verified',
  openChain: 'Open this variant’s full causal chain',
  chainLabelPrefix: 'ENSEMBLE TREE VARIANT',
  chainNotServed: 'CHAIN NOT SERVED FOR THIS MEMBER',
  chainOpening: 'Opening chain…',
  treeLoadFailed: 'The tree view could not be loaded',
  // ── Explorations ──
  expHead: 'Explorations — deepen one member at a time',
  expIntro: 'An exploration is a saved set of members. Each member is an ordinary ensemble job; deepening picks one variant of a member and runs a new member that holds every axis at that variant’s combination except the next axis not yet varied along the path, which is swept over values you supply. The record stores pointers only — a reopened exploration renders from the saved record itself.',
  expNameLabel: 'Name',
  expRootLabel: 'Root member — an existing ensemble job',
  expCreate: 'Create exploration',
  expCreating: 'Creating…',
  expNoRoot: 'Run an ensemble statistics job first',
  expListHead: 'Saved explorations',
  expNone: 'No explorations yet.',
  expOpen: 'Open',
  expRename: 'Rename',
  expRenaming: 'Saving…',
  expMembersHead: 'Members · siblings grouped by parent',
  expRootMember: 'Root member',
  expDeepenedOn: 'deepened on',
  expFromVariant: 'from variant',
  expVariedLine: 'Varied along the path so far',
  expNextLine: 'Next axis to deepen',
  expAllVaried: 'every axis varied · no next axis',
  expCrumbHead: 'Path from the root member',
  expCrumbRoot: 'ROOT MEMBER · NO DEEPENING STEPS',
  expCrumbStep: 'of its parent, held fixed; deepened on',
  expCrumbCombo: 'the held combination',
  deepenButton: 'Deepen from this variant',
  deepenRowHead: 'Deepen from a variant of this member',
  deepenIndexLabel: 'Variant number',
  deepenGo: 'Set up deepening',
  deepenIndexInvalid: 'Enter a variant number this member actually ran.',
  expandHead: 'Deepen',
  expandAxisLine: 'Axis this deepening sweeps',
  expandHeldLine: 'Other axes held at the chosen variant',
  expandValuesLabel: 'Values to sweep (comma-separated)',
  expandValuesHint: '1 to 10,000 values · shape axis: shape names · other axes: numbers',
  expandValuesInvalid: 'ENTER 1 TO 10,000 VALUES · COMMA-SEPARATED',
  expandButton: 'Run this deepening',
  expandRunning: 'Running…',
  expandQuoteHead: 'Deepening quote (the new member’s own)',
  expandQuoteValues: 'values swept',
  expandQuoteGrid: 'distinct variants in the new member’s declared grid',
  expandDone: 'MEMBER ADDED · SELECTED BELOW',
  expLoadFailed: 'The exploration could not be loaded',
  memberMissing: 'JOB NOT SERVED · NO RECORD',
  memberRunning: 'RUNNING · AGGREGATES WHEN COMPLETE',
  unreachable: 'Ensemble explorer unavailable',
} as const;

/** The per-level branching list's default text (the server's own
 *  default configuration: 10 at each of the five levels). */
export const DEFAULT_BRANCHING_TEXT = '10, 10, 10, 10, 10';

/** Branching input → the list, or the panel's honest error line.
 *  Client discipline mirrors the server's (whole numbers ≥ 1, never
 *  coerced); the depth cap is the five declared axes. */
export function parseBranching(text: string): { branching: number[] } | { error: string } {
  const parts = text.split(',').map((p) => p.trim()).filter((p) => p.length > 0);
  if (parts.length === 0 || parts.length > 5) return { error: ENX_COPY.branchingInvalid };
  const out: number[] = [];
  for (const p of parts) {
    if (!/^\d+$/.test(p)) return { error: ENX_COPY.branchingInvalid };
    const n = Number(p);
    if (!Number.isInteger(n) || n < 1) return { error: ENX_COPY.branchingInvalid };
    out.push(n);
  }
  return { branching: out };
}

export const branchingText = (list: number[]): string => list.join(', ');

/** An axis key → its screen label (the shared axis labels). */
export const axisLabel = (axis: string): string => AXIS_LABELS[axis] ?? axis;

/** One quote level as the explorer's table renders it: the axis by
 *  its screen label, the rest exactly the served figures. */
export interface QuoteRow {
  level: number; axisLabel: string; branching: number; fanout: number;
  runs: string; cumulative: string;
}
export function quoteRows(quote: EnsTreeQuote): QuoteRow[] {
  return quote.levels.map((l) => ({
    level: l.level,
    axisLabel: AXIS_LABELS[l.axis] ?? l.axis,
    branching: l.branching,
    fanout: l.fanout,
    runs: fmtInt(l.runs),
    cumulative: fmtInt(l.cumulative),
  }));
}

/** The launch gate over a served quote. `ready` — the tree launches
 *  under the budget it was quoted with. `raise` — over that budget
 *  but inside the hard maximum: launchable only after the budget is
 *  deliberately raised to at least the quoted total. `refused` —
 *  over the hard maximum: no budget launches it. */
export type QuoteGate =
  | { kind: 'ready' }
  | { kind: 'raise'; minimum: number }
  | { kind: 'refused' };
export function quoteGate(quote: EnsTreeQuote): QuoteGate {
  if (quote.launchable) return { kind: 'ready' };
  if (quote.within_hard_max) return { kind: 'raise', minimum: quote.quoted_runs };
  return { kind: 'refused' };
}

/** The refusal block's lines, composed from the served quote only —
 *  its arithmetic and per-level table verbatim, its totals and
 *  estimates as served, then the panel's own closing line. */
export function refusalLines(quote: EnsTreeQuote): string[] {
  const lines = [
    `This combination tree would introduce ${fmtInt(quote.quoted_runs)} variants (${quote.arithmetic}; levels: ${quote.levels_table}).`,
    `${ENX_COPY.estWall} ${fmtVal(quote.est_wall_seconds)} seconds (${fmtVal(quote.est_wall_minutes)} minutes) · ${ENX_COPY.estSize} ${fmtInt(quote.est_bytes)} bytes (${fmtVal(quote.est_mb)} MB).`,
  ];
  lines.push(quote.within_hard_max ? ENX_COPY.refusedOverBudget : ENX_COPY.refusedOverMax);
  return lines;
}

/** Estimates line for a launchable quote, from the served figures. */
export const estimateLine = (quote: EnsTreeQuote): string =>
  `${ENX_COPY.estWall} ${fmtVal(quote.est_wall_seconds)} s · ${ENX_COPY.estSize} ${fmtVal(quote.est_mb)} MB (${fmtInt(quote.est_bytes)} bytes)`;

/** Budget line for the quote card, from the served budget fields. */
export const budgetLine = (quote: EnsTreeQuote): string =>
  `${ENX_COPY.budgetLine} ${fmtInt(quote.budget)} · ${ENX_COPY.defaultBudgetWord} ${fmtInt(quote.default_budget)} · ${ENX_COPY.hardMaxWord} ${fmtInt(quote.hard_max)}`;

/** A member's refinement as one phrase: the axis it refined and the
 *  value it took, in the combination line's own convention. The root
 *  refined nothing — it is the base combination. */
export function memberRefinement(member: EnsTreeMember): string {
  if (member.level === 0 || !member.axis) return ENX_COPY.baseMember;
  const label = AXIS_LABELS[member.axis] ?? member.axis;
  const v = member.value;
  const val = typeof v === 'number' ? String(v) : String(v);
  return member.axis.endsWith('_mult') ? `${label} ×${val}` : `${label} ${val}`;
}

/** A member's state: pruned (its served reason renders with it),
 *  expanded, or a member of the final level — the server's build
 *  leaves every member in exactly one of the three. */
export function memberState(member: EnsTreeMember): 'pruned' | 'expanded' | 'final' {
  if (member.pruned) return 'pruned';
  if (member.expanded) return 'expanded';
  return 'final';
}
export const memberStateWord = (state: 'pruned' | 'expanded' | 'final'): string =>
  state === 'pruned' ? ENX_COPY.prunedWord : state === 'expanded' ? ENX_COPY.expandedWord : ENX_COPY.finalLevelWord;

/** A member's headline outcomes as one line, from its served
 *  outcome vector — the same four outcomes the aggregate blocks use. */
export function memberOutcomesLine(member: EnsTreeMember): string {
  const o = member.outcomes;
  return `worst path delay ${fmtVal(o.worst_path_delay_d)} d · queue peak ${fmtVal(o.queue_peak_delay_d)} d · ` +
    `${fmtVal(o.places_reached)} places reached · ${fmtVal(o.chain_events)} chain events · ` +
    `${fmtInt(member.subtree_run_count)} variants ${ENX_COPY.subtreeWord}`;
}

/** Members grouped by level, levels ascending, served order kept. */
export function membersByLevel(members: EnsTreeMember[]): Array<{ level: number; members: EnsTreeMember[] }> {
  const by = new Map<number, EnsTreeMember[]>();
  for (const m of members) {
    const arr = by.get(m.level) ?? [];
    arr.push(m);
    by.set(m.level, arr);
  }
  return [...by.entries()].sort((a, b) => a[0] - b[0]).map(([level, ms]) => ({ level, members: ms }));
}

/** One breadcrumb step as a line: the parent variant that was held
 *  fixed (its full served combination, when the payload carries
 *  one) and the axis the step deepened on. */
export function crumbLine(step: EnsExplorationMember['breadcrumb'][number]): string {
  const axis = AXIS_LABELS[step.axis] ?? step.axis;
  const combo = step.combination ? ` · ${ENX_COPY.expCrumbCombo}: ${combinationText(step.combination)}` : '';
  return `Variant ${step.variant_index} ${ENX_COPY.expCrumbStep} ${axis}${combo}`;
}

/** The exploration's members as a hierarchy: the root first, each
 *  member's deepened children grouped directly beneath it in served
 *  (expansion) order — siblings from the same parent together. */
export interface MemberTreeRow { member: EnsExplorationMember; depth: number }
export function memberRows(detail: EnsExplorationDetail): MemberTreeRow[] {
  const byJob = new Map(detail.nodes.map((n) => [n.job_id, n]));
  const children = new Map<string, EnsExplorationMember[]>();
  for (const n of detail.nodes) {
    if (n.parent_job_id && byJob.has(n.parent_job_id)) {
      const arr = children.get(n.parent_job_id) ?? [];
      arr.push(n);
      children.set(n.parent_job_id, arr);
    }
  }
  const root = detail.nodes.find((n) => n.job_id === detail.root_job_id) ?? detail.nodes[0];
  const rows: MemberTreeRow[] = [];
  const walk = (m: EnsExplorationMember, depth: number) => {
    rows.push({ member: m, depth });
    for (const c of children.get(m.job_id) ?? []) walk(c, depth + 1);
  };
  if (root) walk(root, 0);
  // Any member the walk could not reach (its parent is absent from
  // the served record) still renders, after the reachable ones.
  const seen = new Set(rows.map((r) => r.member.job_id));
  for (const n of detail.nodes) if (!seen.has(n.job_id)) rows.push({ member: n, depth: 0 });
  return rows;
}

/** A member's row title in the exploration: the root by its place in
 *  the record, a deepened member by the axis it swept and the
 *  parent variant it came from. */
export function memberTitle(member: EnsExplorationMember): string {
  if (!member.parent_job_id) return ENX_COPY.expRootMember;
  const axis = memberSummaryAxis(member);
  const label = AXIS_LABELS[axis] ?? axis;
  return `${ENX_COPY.expDeepenedOn} ${label} · ${ENX_COPY.expFromVariant} ${member.parent_variant_index}`;
}
/** The axis a deepened member swept is the one its breadcrumb's last
 *  step names; the root swept none. */
export function memberSummaryAxis(member: EnsExplorationMember): string {
  const last = member.breadcrumb[member.breadcrumb.length - 1];
  return last ? last.axis : '';
}

/** The next-axis line for a member: the served next axis by label,
 *  or the all-varied statement when the payload serves none. */
export function nextAxisLine(member: EnsExplorationMember): string {
  if (!member.next_axis) return `${ENX_COPY.expNextLine}: ${ENX_COPY.expAllVaried}.`;
  return `${ENX_COPY.expNextLine}: ${AXIS_LABELS[member.next_axis] ?? member.next_axis}`;
}

/** Expansion values input → the values array, or the honest error.
 *  Numbers for the numeric axes, names for the shape axis; the count
 *  bound mirrors the server's (1 to 10,000 values). */
export function parseValues(text: string, axis: string): { values: unknown[] } | { error: string } {
  const parts = text.split(',').map((p) => p.trim()).filter((p) => p.length > 0);
  if (parts.length === 0 || parts.length > 10000) return { error: ENX_COPY.expandValuesInvalid };
  if (axis === 'shape') return { values: parts };
  const out: number[] = [];
  for (const p of parts) {
    const n = Number(p);
    if (!Number.isFinite(n)) return { error: ENX_COPY.expandValuesInvalid };
    out.push(n);
  }
  return { values: out };
}

/** Live count of the values in an expansion input, for the form's
 *  own line (0 when nothing parseable has been entered). */
export const valuesCount = (text: string): number =>
  text.split(',').map((p) => p.trim()).filter((p) => p.length > 0).length;
