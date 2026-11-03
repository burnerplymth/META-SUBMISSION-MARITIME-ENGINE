/* Replay window: pure logic. No DOM, no fetch, no React.
 *
 * The comparison is descriptive. A pair holds what was observed and what the engine produced, both as stored;
 * `classify` names the relation (matched, not flagged, not observed, ...), the timing offset in days and a magnitude
 * descriptor. Nothing here scores, ranks or reduces a result to one number: counts exist only per relation, and the
 * panel prints them beside a Descriptive chip. */
import { holdsOutOfScope } from '../../contentGuard';
import type { Chain } from '../../types';
import type { Basis, ChainDoc, EngineDoc, EpisodeDoc, ObsEvent, Pair, Row, Status } from './types';

export const DAY_MS = 86_400_000;

/* ── dates and numbers ─────────────────────────────────────────────────── */
export const toMs = (iso: string): number => Date.parse(iso.length <= 10 ? `${iso}T00:00:00Z` : iso);
export const isoDay = (ms: number): string => new Date(ms).toISOString().slice(0, 10);
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** 23 Mar 2021 */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const ms = toMs(iso);
  if (!Number.isFinite(ms)) return '';
  const d = new Date(ms);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}
/** 23 Mar to 3 Apr 2021, or 15 Oct 2020 to 22 Nov 2022 */
export function fmtRange(start: string, end: string): string {
  const a = new Date(toMs(start)); const b = new Date(toMs(end));
  if (![a, b].every((d) => Number.isFinite(d.getTime()))) return '';
  if (a.getUTCFullYear() === b.getUTCFullYear()) return `${a.getUTCDate()} ${MONTHS[a.getUTCMonth()]} to ${fmtDate(end)}`;
  return `${fmtDate(start)} to ${fmtDate(end)}`;
}
export function fmtNum(v: number, dp = 1): string {
  const s = Math.abs(v) >= 1000 ? Math.round(v).toLocaleString('en-US') : (Math.round(v * 10 ** dp) / 10 ** dp).toFixed(dp);
  return s;
}
export const signed = (v: number, dp = 1): string => {
  const r = Math.round(v * 10 ** dp) / 10 ** dp;
  return `${r > 0 ? '+' : r < 0 ? '-' : ''}${fmtNum(Math.abs(r), dp)}`;
};
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);
const fmtBase = (v: number): string => (Number.isInteger(v) ? String(v) : fmtNum(v));
export const h8 = (hex: string | null | undefined): string => (hex ?? '').slice(0, 8);

/** Text from the server shown only when it holds no out-of-scope wording. */
export const safeText = (s: unknown): string => (typeof s === 'string' && !holdsOutOfScope(s) ? s : '');

/* ── chain steps ───────────────────────────────────────────────────────── */
export interface Step { seq: number; placeId: string; channel: string; addedD: number; at: string; date: string; lagD: number; parent: number | null }

/** First event of each place in stream order, with the cost leaves of zero delay left out (the same rule as the
 *  published prediction digest). `lagD` is days after the stated start. */
export function chainSteps(chain: ChainDoc | null | undefined): Step[] {
  if (!chain) return [];
  const start = toMs(chain.disruption.started_at);
  const seen = new Set<string>();
  const out: Step[] = [];
  for (const e of [...chain.events].sort((a, b) => a.seq - b.seq)) {
    if (e.channel === 'cost' && e.delay_d === 0) continue;
    if (seen.has(e.node_id)) continue;
    seen.add(e.node_id);
    const at = toMs(e.at);
    out.push({ seq: e.seq, placeId: e.node_id, channel: e.channel, addedD: e.delay_d, at: e.at, date: isoDay(at), lagD: Math.round(((at - start) / DAY_MS) * 1e6) / 1e6, parent: e.parent_id });
  }
  return out;
}

/* ── shared time axis ──────────────────────────────────────────────────── */
export interface Axis { t0: number; t1: number }
export function axisOf(window: { start: string; end: string }): Axis {
  const t0 = toMs(window.start); const t1 = toMs(window.end);
  return { t0, t1: t1 > t0 ? t1 : t0 + DAY_MS };
}
/** Fraction of the axis (0 to 1) of a date; the same function places observed events and modelled steps. */
export const fracOf = (axis: Axis, iso: string): number => (toMs(iso) - axis.t0) / (axis.t1 - axis.t0);
export const inAxis = (axis: Axis, iso: string): boolean => { const f = fracOf(axis, iso); return f >= 0 && f <= 1; };
/** Snap a cursor (ms) to a whole UTC day inside the axis. */
export function snapDay(axis: Axis, ms: number): number {
  const c = Math.min(axis.t1, Math.max(axis.t0, ms));
  return Math.min(axis.t1, axis.t0 + Math.round((c - axis.t0) / DAY_MS) * DAY_MS);
}
export function stepCursor(axis: Axis, cur: number | null, dir: -1 | 1 | 'start' | 'end', days = 1): number {
  if (dir === 'start') return axis.t0;
  if (dir === 'end') return snapDay(axis, axis.t1);
  const base = cur ?? axis.t0;
  return snapDay(axis, base + dir * days * DAY_MS);
}

/** Value of a daily series on the day of the cursor (null when the series has no row that day). */
export function seriesAt(points: ReadonlyArray<readonly [string, number]>, ms: number): { date: string; value: number } | null {
  const day = isoDay(ms);
  let lo = 0; let hi = points.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const d = points[mid][0];
    if (d === day) return { date: d, value: points[mid][1] };
    if (d < day) lo = mid + 1; else hi = mid - 1;
  }
  return null;
}
export const eventsOn = (events: readonly ObsEvent[], ms: number): ObsEvent[] => events.filter((e) => e.date === isoDay(ms));
export const stepsOn = (steps: readonly Step[], ms: number): Step[] => steps.filter((s) => s.date === isoDay(ms));
/** Value of a daily model series (start date + index) on the day of the cursor. */
export function simAt(s: { start: string; disrupted: number[]; twin: number[] }, ms: number): { disrupted: number; twin: number } | null {
  const i = Math.round((ms - toMs(s.start)) / DAY_MS);
  return i >= 0 && i < s.disrupted.length && i < s.twin.length ? { disrupted: s.disrupted[i], twin: s.twin[i] } : null;
}

/* ── relations ─────────────────────────────────────────────────────────── */
export const STATUS_LABEL: Record<Status, string> = {
  matched: 'Matched', not_flagged: 'Not flagged', not_observed: 'Not observed', opposite: 'Opposite direction',
  within: 'Within band', outside: 'Outside band', not_produced: 'Not produced',
  ungraded: 'Not graded', not_compared: 'Not compared', quiet: 'No change',
};
/** The published misses: the model and the record differ. */
export const MISS: ReadonlySet<Status> = new Set<Status>(['not_flagged', 'not_observed', 'opposite', 'outside', 'not_produced']);
const MISS_ORDER: Status[] = ['not_flagged', 'not_observed', 'opposite', 'not_produced', 'outside'];
export const BASIS_LABEL: Record<Basis, string> = { portwatch: 'PortWatch', dossier: 'Curated record', simulation: 'Simulation', reported: 'Reported' };

export interface Ctx { startMs: number | null; name: (placeId: string) => string }

const NOT_COMPARED: Record<string, string> = { low_count: 'Under 5 calls a day' };
const UNGRADED: Record<string, string> = { no_observed_onset: 'No onset observed', low_count: 'Under 5 calls a day', no_observed_drop: 'No drop observed' };

const dayOffset = (ctx: Ctx, iso: string | null): number | null => (iso && ctx.startMs != null ? Math.round(((toMs(iso) - ctx.startMs) / DAY_MS) * 10) / 10 : null);

function ratioWord(model: number, observed: number): string | null {
  if (!(observed > 0) || !(model >= 0)) return null;
  const r = model / observed;
  return `${r < 10 ? r.toFixed(1) : Math.round(r)}x observed`;
}
function fmtMetric(v: unknown, unit: string | undefined): string {
  if (Array.isArray(v) && v.length === 2 && v.every((x) => typeof x === 'number')) return `${fmtNum(v[0])} to ${fmtNum(v[1])} ${unit ?? ''}`.trim();
  const n = num(v);
  if (n == null) return '';
  return unit === '%' ? `${signed(n)}%` : `${fmtNum(n)} ${unit ?? ''}`.trim();
}

function base(p: Pair, ctx: Ctx): Pick<Row, 'key' | 'basis' | 'subject' | 'placeId' | 'publisher' | 'url'> {
  const id = p.subject.place_id ?? null;
  const label = safeText(p.subject.label);
  const name = id ? ctx.name(id) : label;
  const subject = p.kind === 'reported' ? (label || name) : p.kind === 'timing' ? `${name} ${label.toLowerCase()}`.trim() : (name || label);
  const o = p.observed ?? {};
  return { key: p.key, basis: p.basis, subject, placeId: id, publisher: str(o.publisher), url: str(o.url) };
}
const blank = { difference: null, timingD: null, magnitude: null, observedDate: null, modelDate: null, note: null } as const;

/** Name the relation between the record and the model for one pair. */
export function classify(p: Pair, ctx: Ctx): Row {
  const b = base(p, ctx);
  const o = p.observed; const m = p.model;
  if (p.kind === 'place') {
    if (p.not_compared || !o || !m) {
      return { ...b, ...blank, status: 'not_compared', observed: NOT_COMPARED[p.not_compared ?? ''] ?? 'No series', model: '', note: null };
    }
    const dir = str(o.direction); const chg = num(o.change_pct);
    const effect = dir === 'down' || dir === 'up';
    const flagged = m.direction === 'down';
    const delay = num(m.delay_d); const lag = num(m.lag_d);
    const status: Status = flagged ? (dir === 'down' ? 'matched' : dir === 'up' ? 'opposite' : 'not_observed') : (effect ? 'not_flagged' : 'quiet');
    const observed = effect ? `${dir === 'down' ? 'Down' : 'Up'} ${fmtNum(Math.abs(chg ?? 0), 0)}%` : `Flat${chg != null ? ` (${signed(chg, 0)}%)` : ''}`;
    const model = flagged ? `Flagged${delay != null && delay > 0 ? `, +${fmtNum(delay)} d` : ''}` : 'Not flagged';
    const modelDate = flagged && lag != null && ctx.startMs != null ? isoDay(ctx.startMs + lag * DAY_MS) : null;
    return { ...b, ...blank, status, observed, model, modelDate, note: num(o.baseline_per_day) != null ? `Baseline ${fmtBase(num(o.baseline_per_day) as number)} a day` : null };
  }
  if (p.kind === 'step') {
    const flagged = !!m?.flagged;
    const od = num(o?.delay_d); const md = num(m?.delay_d);
    const odate = str(o?.date); const mdate = str(m?.date);
    const status: Status = !o ? 'not_observed' : flagged ? 'matched' : 'not_flagged';
    const observed = !o ? 'No observed record' : od != null ? `+${fmtNum(od)} d, ${fmtDate(odate)}` : `Effect recorded, ${fmtDate(odate)}`;
    const model = flagged ? (md != null ? `+${fmtNum(md)} d` : 'Flagged') : 'Not flagged';
    const timingD = odate && mdate ? Math.round(((toMs(mdate) - toMs(odate)) / DAY_MS) * 10) / 10 : null;
    const diff = od != null && md != null ? { value: Math.round((md - od) * 100) / 100, unit: 'd' } : null;
    return { ...b, status, observed, model, difference: diff, timingD, magnitude: od != null && md != null ? ratioWord(md, od) : null, observedDate: odate, modelDate: mdate, note: str(o?.text) };
  }
  if (p.kind === 'timing') {
    const ml = num(m?.lag_d);
    if (p.ungraded || !o) {
      return { ...b, ...blank, status: 'ungraded', observed: UNGRADED[p.ungraded ?? ''] ?? 'Not graded', model: ml != null ? `Day ${fmtNum(ml)}` : '', modelDate: str(m?.date) };
    }
    const ol = num(o.lag_d);
    if (ol == null) return { ...b, ...blank, status: 'ungraded', observed: 'Not graded', model: ml != null ? `Day ${fmtNum(ml)}` : '', modelDate: str(m?.date) };
    const band = p.band?.abs_d ?? null;
    if (ml == null) return { ...b, ...blank, status: 'not_produced', observed: `Day ${fmtNum(ol, 0)}`, model: 'No onset', observedDate: str(o.date) };
    const diff = Math.round((ml - ol) * 10) / 10;
    const within = band != null ? Math.abs(diff) <= band : false;
    return { ...b, status: within ? 'within' : 'outside', observed: `Day ${fmtNum(ol, 0)}`, model: `Day ${fmtNum(ml)}`, difference: { value: diff, unit: 'd' }, timingD: diff,
      magnitude: band != null ? (within ? `Within ${fmtNum(band, 0)} d` : `Outside ${fmtNum(band, 0)} d`) : null, observedDate: str(o.date), modelDate: str(m?.date), note: 'Days after the stated start' };
  }
  if (p.kind === 'metric') {
    const unit = p.unit ?? '';
    const ov = o ? o.value : null; const mv = num(m?.value);
    const grade = (p.grade ?? '').toUpperCase();
    const prev = num(m?.previous);
    const note = [p.band_text ? `Band ${p.band_text}` : null, prev != null ? `Earlier version ${fmtMetric(prev, unit)}` : null].filter(Boolean).join('. ') || null;
    if (ov == null || grade.startsWith('UNGRADED') || grade.startsWith('UNDEFINED')) {
      return { ...b, ...blank, status: 'ungraded', observed: ov == null ? 'No observed figure' : fmtMetric(ov, unit), model: mv != null ? fmtMetric(mv, unit) : '', note };
    }
    if (mv == null) return { ...b, ...blank, status: 'not_produced', observed: fmtMetric(ov, unit), model: 'Not reached', note };
    const on = num(ov);
    const range = Array.isArray(ov) ? (ov as number[]) : null;
    const diff = on != null ? Math.round((mv - on) * 100) / 100 : null;
    let magnitude: string | null = null;
    if (on != null && unit === '%') magnitude = `${signed(mv - on)} points`;
    else if (on != null) magnitude = ratioWord(mv, on);
    else if (range) magnitude = mv >= range[0] && mv <= range[1] ? 'Inside observed range' : mv < range[0] ? 'Below observed range' : 'Above observed range';
    return { ...b, status: grade === 'HIT' ? 'within' : 'outside', observed: fmtMetric(ov, unit), model: fmtMetric(mv, unit),
      difference: diff != null ? { value: diff, unit: unit === '%' ? 'points' : unit } : null, timingD: p.timing && diff != null ? diff : null, magnitude, observedDate: null, modelDate: null, note };
  }
  /* reported: a figure published for a place, against what the simulation did with it */
  const ov = num(o?.value); const mv = num(m?.value);
  const observed = ov != null ? `${fmtNum(ov, 0)}% ${safeText(o?.text)}`.trim() : 'No figure';
  const status: Status = mv != null && mv > 0 ? 'matched' : 'not_flagged';
  return { ...b, ...blank, status, observed, model: mv != null ? `${fmtNum(mv, 0)} ${safeText(m?.text)}`.trim() : 'Not produced', observedDate: str(o?.date) };
}

export function classifyAll(pairs: readonly Pair[], ctx: Ctx): Row[] {
  return pairs.map((p) => classify(p, ctx));
}

export type Counts = Record<Status, number> & { compared: number; misses: number };
export function summarize(rows: readonly Row[]): Counts {
  const c = Object.fromEntries((Object.keys(STATUS_LABEL) as Status[]).map((s) => [s, 0])) as Record<Status, number>;
  for (const r of rows) c[r.status] += 1;
  const compared = rows.length - c.not_compared - c.ungraded;
  const misses = rows.filter((r) => MISS.has(r.status)).length;
  return { ...c, compared, misses };
}
/** Misses, in a fixed order: relation first, then basis, then the order served. */
export function missesOf(rows: readonly Row[]): Row[] {
  const idx = rows.map((r, i) => [r, i] as const).filter(([r]) => MISS.has(r.status));
  idx.sort((a, b) => MISS_ORDER.indexOf(a[0].status) - MISS_ORDER.indexOf(b[0].status) || a[1] - b[1]);
  return idx.map(([r]) => r);
}

/** The rows of one engine's view: its chain pairs plus the engine-independent simulation pairs. */
export function allPairs(doc: EpisodeDoc, engine: EngineDoc | null): Pair[] {
  return [...(engine?.pairs ?? []), ...(doc.simulation?.pairs ?? [])];
}
export function startOf(doc: EpisodeDoc): number | null {
  return doc.disruption?.started_at ? toMs(doc.disruption.started_at) : null;
}

/* ── chips and the method disclosure ───────────────────────────────────── */
export interface HashChip { key: 'prereg' | 'config' | 'scorecard'; label: string; short: string; full: string }
export function hashChips(e: EngineDoc | null | undefined): HashChip[] {
  if (!e || !e.available) return [];
  const out: HashChip[] = [];
  if (e.prereg) out.push({ key: 'prereg', label: 'Preregistration', short: h8(e.prereg.sha256), full: e.prereg.sha256 });
  if (e.config_hash) out.push({ key: 'config', label: 'Config', short: h8(e.config_hash), full: e.config_hash });
  if (e.scorecard) out.push({ key: 'scorecard', label: 'Scorecard', short: h8(e.scorecard.sha256), full: e.scorecard.sha256 });
  return out;
}

/** The episode split as a word: held out or development. */
export const splitWord = (split: string | null | undefined): string | null => (split === 'TEST' ? 'Held out' : split === 'TRAIN' ? 'In-sample' : split ? split.charAt(0) + split.slice(1).toLowerCase() : null);

/** At most three short lines: frozen before outcome, the split, the not-blind caveat. */
export function methodLines(doc: EpisodeDoc, e: EngineDoc | null | undefined): string[] {
  const l1 = e && e.available && e.prereg
    ? `Frozen before outcome: preregistration ${h8(e.prereg.sha256)}${e.config_hash ? `, config ${h8(e.config_hash)}` : ''}.`
    : 'Scenario preregistered; no recorded run here.';
  const split = doc.status.split;
  const l2 = split === 'TEST' ? 'Held-out episode, one run per engine.' : split === 'TRAIN' ? 'In-sample episode: used when the engines were configured.' : 'No episode split.';
  const l3 = doc.status.not_blind
    ? 'In-sample episode: its TEST result is not blind.'
    : doc.disruption?.basis === 'MODELED' ? 'The capacity loss is a modelled input.' : 'The comparison is descriptive.';
  return [l1, l2, l3];
}

/** Why an engine has no chain here, as a short chip word. */
export function reasonWord(reason: EngineDoc['reason']): string {
  return reason === 'outside_core' ? 'Outside core places' : 'Not run';
}

/* ── export table ──────────────────────────────────────────────────────── */
export interface Labels { episode: string; engine: string; prereg: string; config: string; scorecard: string }
export const COMPARISON_COLUMNS = ['Episode', 'Engine', 'Subject', 'Basis', 'Status', 'Observed', 'Model', 'Difference', 'Difference unit', 'Timing offset d',
  'Magnitude', 'Observed date', 'Model date', 'Source', 'Link', 'Preregistration', 'Config', 'Scorecard'];
export function comparisonRows(rows: readonly Row[], l: Labels): Array<Array<string | number | null>> {
  return rows.map((r) => [
    l.episode, l.engine, r.subject, BASIS_LABEL[r.basis], STATUS_LABEL[r.status], r.observed, r.model,
    r.difference ? r.difference.value : null, r.difference ? r.difference.unit : null, r.timingD, r.magnitude, r.observedDate, r.modelDate,
    r.publisher, r.url, l.prereg, l.config, l.scorecard,
  ]);
}
export const STEP_COLUMNS = ['Episode', 'Engine', 'Step', 'Place', 'Channel', 'Added days', 'Date', 'Days after start'];
export function stepRows(steps: readonly Step[], name: (id: string) => string, l: Labels): Array<Array<string | number | null>> {
  return steps.map((s, i) => [l.episode, l.engine, i + 1, name(s.placeId), s.channel, s.addedD, s.date, s.lagD]);
}
export const EVENT_COLUMNS = ['Episode', 'Date', 'Event', 'Detail', 'Publisher', 'Published', 'Link'];
export function eventRows(events: readonly ObsEvent[], episode: string): Array<Array<string | number | null>> {
  return events.map((e) => [episode, e.date, safeText(e.label), safeText(e.detail) || null, safeText(e.publisher), e.published, e.url]);
}

/* ── the chain as the map and the chain window read it ─────────────────── */
/** The recorded chain in the shape of GET /chain/{run_id}, so the map draws its arcs and the chain windows list it. */
export function toStoreChain(c: ChainDoc): Chain {
  return {
    run_id: c.run_id, status: c.status, event_count: c.event_count, stream_hash: c.stream_hash, edges: c.edges,
    events: c.events.map((e) => ({ run_id: c.run_id, seq: e.seq, node_id: e.node_id, parent_id: e.parent_id, channel: e.channel, mechanism: e.mechanism ?? '', delay_d: e.delay_d, magnitude: e.magnitude ?? 0, at: e.at, tick: e.tick })),
  };
}
/** Event the Replay window raises for the Research workbench. A listener sets `detail.handled = true`. */
export const OPEN_IN_RESEARCH = 'supermap:research-open';
export interface ResearchRequest { episode: string; placeId: string; start: string; end: string; engine: string | null; handled?: boolean }

/** Source badge as a plain label: acronyms the feed lower-cases ("Ntsb") are restored ("NTSB"). */
const ACRONYMS = new Set(['ntsb', 'usace', 'mxsoc', 'nws', 'noaa', 'eia', 'cbp', 'imf', 'ais', 'gdelt', 'usgs', 'nasa']);
export function sourceLabel(b: string): string {
  const t = safeText(b).trim();
  return ACRONYMS.has(t.toLowerCase()) ? t.toUpperCase() : t;
}
/** Train or test membership as a plain two-word label. */
export const splitLabel = (split: 'TRAIN' | 'TEST' | null | undefined): string => (split === 'TEST' ? 'Test split' : split === 'TRAIN' ? 'Train split' : '');
