/* HISTORY / FORK / REALISM: pure logic. No React, no fetch, no clock
 * except where a `today`/`now` argument is passed in. Everything here is
 * shaping and arithmetic over SERVED values: no number is invented, and a
 * missing day stays a gap (never zero-filled). Vocabulary: places, legs,
 * causal chain. */
import type { StateKind } from '../../shell/stateChips';
import type {
  ForkChainRequest, ForkRequestBody, ForkResponse, ForkSimRequest, HistEvent, HistObsPoint, HistProvenance,
  HistSource, HistTimelineDoc, RankCell, RateCell, RealismCard, RealismResultItem,
} from './types';
import { labelWord } from '../../copy';
import { humaniseNote } from '../../format';

/* ── dates (ISO YYYY-MM-DD, UTC) ─────────────────────────────────────── */
export const DAY_MS = 86_400_000;
const ISO_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

export function isIsoDay(s: unknown): s is string {
  if (typeof s !== 'string') return false;
  const m = ISO_RE.exec(s);
  if (!m) return false;
  const t = Date.UTC(+m[1], +m[2] - 1, +m[3]);
  const d = new Date(t);
  return d.getUTCFullYear() === +m[1] && d.getUTCMonth() === +m[2] - 1 && d.getUTCDate() === +m[3];
}
export const toMs = (iso: string): number => { const m = ISO_RE.exec(iso)!; return Date.UTC(+m[1], +m[2] - 1, +m[3]); };
export const fromMs = (ms: number): string => new Date(ms).toISOString().slice(0, 10);
export const addDays = (iso: string, n: number): string => fromMs(toMs(iso) + n * DAY_MS);
export const daysBetween = (a: string, b: string): number => Math.round((toMs(b) - toMs(a)) / DAY_MS);
export const dayOf = (ts: string): string => ts.slice(0, 10);
export const todayIso = (now: Date): string => now.toISOString().slice(0, 10);
export const minIso = (a: string, b: string): string => (a <= b ? a : b);
export const maxIso = (a: string, b: string): string => (a >= b ? a : b);
export const clampIso = (d: string, lo: string, hi: string): string => maxIso(lo, minIso(hi, d));

/** Inclusive list of days; `cap` guards against a runaway range. */
export function dateRange(from: string, to: string, cap = 4000): string[] {
  if (!isIsoDay(from) || !isIsoDay(to) || from > to) return [];
  const n = Math.min(daysBetween(from, to) + 1, cap);
  return Array.from({ length: n }, (_, i) => addDays(from, i));
}

/* ── source board: what /history/sources says each feed can do ───────── */
export interface DateRange { from: string; to: string }

/** The date range a HISTORY-CAPABLE source serves, read from its served
 *  `history_window` text. null = no stated numeric range (the source is
 *  not HISTORY-CAPABLE, or it states cases / windows, not dates). */
export function parseSourceRange(src: Pick<HistSource, 'state' | 'history_window'>, today: string): DateRange | null {
  if (src.state !== 'HISTORY-CAPABLE') return null;
  const w = src.history_window ?? '';
  let from: string | null = null;
  const iso = /(\d{4}-\d{2}-\d{2})/.exec(w);
  if (iso) from = iso[1];
  else {
    const y = /\b(\d{4})\s*->/.exec(w) ?? /from the (\d{4})s/.exec(w);
    if (y) from = `${y[1]}-01-01`;
  }
  if (!from) return null;
  let to = today;
  if (/behind today/i.test(w)) to = addDays(today, -7);
  else if (/lags ~?1 year/i.test(w)) to = addDays(today, -365);
  return from <= to ? { from, to } : null;
}

export type Availability = 'in' | 'partial' | 'out' | 'unbounded' | 'none';
/** How a source's range relates to a chosen window. `none` = the source
 *  is not HISTORY-CAPABLE (its range is NOT SERVED, never backfilled). */
export function availability(src: HistSource, from: string, to: string, today: string): Availability {
  if (src.state !== 'HISTORY-CAPABLE') return 'none';
  const r = parseSourceRange(src, today);
  if (!r) return 'unbounded';
  if (to < r.from || from > r.to) return 'out';
  return from >= r.from && to <= r.to ? 'in' : 'partial';
}

export interface SourceBoardRow {
  source: string; label: string; state: string; stateKind: StateKind; stateWord: string;
  supports: string[]; range: DateRange | null; rangeLabel: string; served: boolean;
  avail: Availability; cacheEntries: number | null; verified: string | null;
}
const STATE_KIND: Record<string, StateKind> = { 'HISTORY-CAPABLE': 'snapshot', 'CURRENT-ONLY': 'meta', BLOCKED: 'down' };
export function sourceBoard(sources: readonly HistSource[], from: string, to: string, today: string): SourceBoardRow[] {
  return sources.map((s) => {
    const range = parseSourceRange(s, today);
    const avail = availability(s, from, to, today);
    return {
      source: s.source, label: s.label, state: s.state,
      stateKind: STATE_KIND[s.state] ?? 'down', stateWord: s.state,
      supports: s.supports ?? [], range,
      rangeLabel: range ? `${range.from} → ${range.to}` : '',
      served: s.state === 'HISTORY-CAPABLE',
      avail, cacheEntries: s.cache ? s.cache.entries : null,
      verified: s.verified?.result ?? null,
    };
  });
}

/** The pickable date limits: the intersection of the ranges of the
 *  HISTORY-CAPABLE sources that serve BOTH events and observations (the
 *  per-place daily series the charts and the tester need); when no such
 *  source states a range, the hull of every stated range. null when no
 *  source states one (nothing is pickable: honest empty). */
export function windowLimits(sources: readonly HistSource[], today: string): DateRange | null {
  const ranged = sources
    .map((s) => ({ s, r: parseSourceRange(s, today) }))
    .filter((x): x is { s: HistSource; r: DateRange } => x.r !== null);
  if (!ranged.length) return null;
  const both = ranged.filter((x) => (x.s.supports ?? []).includes('events') && (x.s.supports ?? []).includes('observations'));
  if (both.length) {
    const from = both.reduce((m, x) => maxIso(m, x.r.from), both[0].r.from);
    const to = both.reduce((m, x) => minIso(m, x.r.to), both[0].r.to);
    return from <= to ? { from, to } : null;
  }
  return {
    from: ranged.reduce((m, x) => minIso(m, x.r.from), ranged[0].r.from),
    to: ranged.reduce((m, x) => maxIso(m, x.r.to), ranged[0].r.to),
  };
}

export const MAX_WINDOW_D = 366;
/** Clamp a window into the limits and the span cap; from <= to always. */
export function clampWindow(from: string, to: string, lim: DateRange | null, maxDays = MAX_WINDOW_D): DateRange {
  let f = isIsoDay(from) ? from : (lim?.from ?? to);
  let t = isIsoDay(to) ? to : f;
  if (lim) { f = clampIso(f, lim.from, lim.to); t = clampIso(t, lim.from, lim.to); }
  if (f > t) [f, t] = [t, f];
  if (daysBetween(f, t) + 1 > maxDays) f = addDays(t, -(maxDays - 1));
  return { from: f, to: t };
}

/* ── events: window, markers, focus ──────────────────────────────────── */
export const eventStartDay = (e: Pick<HistEvent, 'started_at'>): string => dayOf(e.started_at);
/** Last day an event is in force (its stated duration), or null when no
 *  duration is stated (never assumed). */
export function eventEndDay(e: Pick<HistEvent, 'started_at' | 'expected_duration_d'>): string | null {
  const d = e.expected_duration_d;
  if (typeof d !== 'number' || !(d > 0)) return null;
  return addDays(eventStartDay(e), Math.max(0, Math.ceil(d) - 1));
}
/** Events whose stated span covers `date`; an event with no stated
 *  duration is active from its start only. */
export function activeEventsAt(events: readonly HistEvent[], date: string): HistEvent[] {
  return events.filter((e) => {
    const s = eventStartDay(e);
    if (date < s) return false;
    const end = eventEndDay(e);
    return end === null ? date === s : date <= end;
  });
}
/** The event the backplay is "at": the latest-starting event in force at
 *  the cursor (ties: the larger severity). null when none is in force. */
export function focusEventAt(events: readonly HistEvent[], date: string): HistEvent | null {
  const act = activeEventsAt(events, date);
  if (!act.length) return null;
  return [...act].sort((a, b) => (eventStartDay(b) < eventStartDay(a) ? -1 : eventStartDay(b) > eventStartDay(a) ? 1 : b.severity - a.severity))[0];
}
export interface EventTick { id: string; date: string; frac: number; mode: string; place: string }
/** Event ticks for the scrubber: only events that START inside the window. */
export function eventTicks(events: readonly HistEvent[], win: DateRange): EventTick[] {
  const span = Math.max(1, daysBetween(win.from, win.to));
  return events
    .filter((e) => eventStartDay(e) >= win.from && eventStartDay(e) <= win.to)
    .map((e) => ({ id: e.id, date: eventStartDay(e), frac: daysBetween(win.from, eventStartDay(e)) / span, mode: String(e.mode), place: e.node_id }))
    .sort((a, b) => a.frac - b.frac);
}
/** Places an event involves: its own place first, then watch, then
 *  control; no duplicates. */
export function placesInvolved(e: Pick<HistEvent, 'node_id' | 'watch_places' | 'control_places'>): { seed: string; watch: string[]; control: string[] } {
  const seen = new Set([e.node_id]);
  const take = (xs: string[] | undefined) => (xs ?? []).filter((p) => (seen.has(p) ? false : (seen.add(p), true)));
  return { seed: e.node_id, watch: take(e.watch_places), control: take(e.control_places) };
}
/** Events that mark a place's chart: its own, or one that lists it as a
 *  watch or control place. */
export function eventsForPlace(events: readonly HistEvent[], place: string): HistEvent[] {
  return events.filter((e) => e.node_id === place || (e.watch_places ?? []).includes(place) || (e.control_places ?? []).includes(place));
}
/** The chart places for a window: the focus event's, else every event's
 *  own place in the window. Capped so the panel stays dense. */
export function chartPlaces(events: readonly HistEvent[], focus: HistEvent | null, cap = 6): string[] {
  const out: string[] = [];
  const add = (p: string) => { if (!out.includes(p)) out.push(p); };
  if (focus) { const p = placesInvolved(focus); [p.seed, ...p.watch, ...p.control].forEach(add); }
  events.forEach((e) => add(e.node_id));
  return out.slice(0, cap);
}
/** Window around a curated example: the rule's 28-day baseline before the
 *  event, then the stated duration (7..120 d) plus a 14-day tail; when no
 *  duration is stated the window runs 90 days past the start. Clamped to
 *  the limits. */
export const EXAMPLE_UNSTATED_AFTER_D = 90;
export function exampleWindow(startedAt: string, durationD: number | null | undefined, lim: DateRange | null): DateRange {
  const s = dayOf(startedAt);
  const after = typeof durationD === 'number' && durationD > 0 ? Math.min(120, Math.max(7, Math.ceil(durationD))) + 14 : EXAMPLE_UNSTATED_AFTER_D;
  return clampWindow(addDays(s, -28), addDays(s, after), lim);
}

/* ── transport: step, speed, play ────────────────────────────────────── */
export const SPEEDS = [1, 2, 4, 8] as const;
export type Speed = (typeof SPEEDS)[number];
export const BASE_STEP_MS = 800;
export const speedMs = (speed: number): number => Math.round(BASE_STEP_MS / Math.max(1, speed));
export function stepCursor(cursor: string, delta: number, win: DateRange): string {
  return clampIso(addDays(cursor, delta), win.from, win.to);
}
/** One play tick: the next day, or null at the window end (stop). */
export function playTick(cursor: string, win: DateRange): string | null {
  return cursor >= win.to ? null : addDays(cursor, 1);
}
export function cursorFrac(cursor: string, win: DateRange): number {
  const span = daysBetween(win.from, win.to);
  return span <= 0 ? 0 : Math.max(0, Math.min(1, daysBetween(win.from, cursor) / span));
}
export function dateAtFrac(frac: number, win: DateRange): string {
  const span = daysBetween(win.from, win.to);
  return addDays(win.from, Math.round(Math.max(0, Math.min(1, frac)) * span));
}

/* ── series shaping ──────────────────────────────────────────────────── */
export interface DayPoint { date: string; v: number | null }
/** Put a served series on the window's day axis. A day the source did
 *  not serve is a null gap, never zero. */
export function alignDays(series: readonly HistObsPoint[], from: string, to: string): DayPoint[] {
  const by = new Map(series.map((p) => [p.date, p.value]));
  return dateRange(from, to).map((date) => ({ date, v: by.has(date) ? (by.get(date) as number) : null }));
}
export function alignPairs(pairs: ReadonlyArray<readonly [string, number]> | undefined, from: string, to: string): DayPoint[] {
  return alignDays((pairs ?? []).map(([date, value]) => ({ date, value })), from, to);
}
export interface Extent { min: number; max: number }
/** Count series start at 0 when none is negative, so a fall reads as a fall. */
export function extentOf(points: readonly DayPoint[]): Extent | null {
  const vs = points.map((p) => p.v).filter((v): v is number => v !== null);
  if (!vs.length) return null;
  const lo = Math.min(...vs); const hi = Math.max(...vs);
  const min = lo >= 0 ? 0 : lo;
  return { min, max: hi > min ? hi : min + 1 };
}
export interface Box { w: number; h: number; l: number; r: number; t: number; b: number }
export const r1 = (n: number): number => Math.round(n * 10) / 10;
export const xAt = (i: number, n: number, box: Box): number => r1(box.l + (n <= 1 ? 0 : (i / (n - 1)) * (box.w - box.l - box.r)));
export const yAt = (v: number, ext: Extent, box: Box): number => r1(box.h - box.b - ((v - ext.min) / (ext.max - ext.min)) * (box.h - box.t - box.b));
/** SVG path strings for each contiguous run of served days in [i0, i1]. */
export function linePaths(points: readonly DayPoint[], ext: Extent | null, box: Box, i0 = 0, i1 = points.length - 1): string[] {
  if (!ext) return [];
  const out: string[] = [];
  let cur: string[] = [];
  const flush = () => { if (cur.length) out.push(cur.length === 1 ? `${cur[0]} h0` : cur.join(' ')); cur = []; };
  for (let i = Math.max(0, i0); i <= Math.min(points.length - 1, i1); i++) {
    const v = points[i].v;
    if (v === null) { flush(); continue; }
    cur.push(`${cur.length ? 'L' : 'M'}${xAt(i, points.length, box)},${yAt(v, ext, box)}`);
  }
  flush();
  return out;
}
/** Last served value at or before index `i` (the axis tag at the cursor). */
export function lastAtOrBefore(points: readonly DayPoint[], i: number): { date: string; v: number; i: number } | null {
  for (let k = Math.min(i, points.length - 1); k >= 0; k--) { const v = points[k].v; if (v !== null) return { date: points[k].date, v, i: k }; }
  return null;
}
export const dayIndex = (points: readonly DayPoint[], date: string): number => points.findIndex((p) => p.date === date);
export function missingDays(points: readonly DayPoint[]): number { return points.filter((p) => p.v === null).length; }

/* ── provenance → chip ───────────────────────────────────────────────── */
/** Chip kind for a served historical record. SNAPSHOT is a recorded
 *  fetch, MODELED an inference; nothing historical is ever LIVE. A null
 *  mode is a refusal (NOT SERVED). */
export function provKind(p: HistProvenance | null | undefined): StateKind {
  const m = (p?.mode ?? '').toString().toUpperCase();
  if (m === 'SNAPSHOT') return 'snapshot';
  if (m === 'MODELED' || m === 'MODELLED') return 'model';
  return 'notserved';
}
export const provWord = (p: HistProvenance | null | undefined): string | undefined => (provKind(p) === 'model' ? 'Modelled' : undefined);
/** `3 events`, `1 event`: a count and its noun, plural in lower case. */
export const countNoun = (n: number, noun: string): string => `${n} ${noun}${n === 1 ? '' : 's'}`;
export const retrievedDay = (p: HistProvenance | null | undefined): string => (p?.retrieved_at ? p.retrieved_at.slice(0, 10) : '');

/** A refusal reason code as a label in sentence case. */
export const reasonLabel = (reason: string): string => labelWord(reason.replace(/_/g, ' '));

/* ── timeline rows (GET /history/timeline) ───────────────────────────── */
export interface TimelineObsRow { place: string; metric: string; unit: string; onDate: number | null; trailing: HistObsPoint[]; served: boolean; reason: string; prov: HistProvenance | undefined }
export interface TimelineEventRow { id: string; place: string; kind: string; severity: number; statusAsOf: string; day: number | null; mode: string; source: string }
export interface TimelineMarketRow { metric: string; label: string; unit: string; date: string; value: number | null; prov: HistProvenance | undefined }
export function timelineRows(doc: HistTimelineDoc): { events: TimelineEventRow[]; obs: TimelineObsRow[]; market: TimelineMarketRow[] } {
  return {
    events: doc.events.map((e) => ({ id: e.id, place: e.node_id, kind: e.kind, severity: e.severity, statusAsOf: e.status_as_of ?? e.status, day: e.day_of_event ?? null, mode: String(e.mode), source: e.source })),
    obs: doc.observations.map((o) => ({
      place: o.place, metric: o.metric, unit: o.unit ?? '', onDate: o.on_date ?? null, trailing: o.trailing ?? [],
      served: o.served !== false && ((o.trailing?.length ?? 0) > 0 || o.on_date != null), reason: o.reason ?? '', prov: o.provenance,
    })),
    market: doc.market_context.map((m) => ({ metric: m.metric, label: m.label ?? m.metric, unit: m.unit ?? '', date: m.last?.date ?? '', value: m.last ? m.last.value : null, prov: m.provenance })),
  };
}

/* ── fork: request and response mapping ──────────────────────────────── */
export const FORK_MAX_EVENTS = 8;
export const SIM_MAX_DAY_TICKS = 180;
/** Whether FORK WINDOW can run: the server refuses a window holding more
 *  than FORK_MAX_EVENTS events (422 too_many_events), so the button is
 *  disabled up front with a count chip instead of ending in a refusal.
 *  The cap is the documented one: the server serves
 *  no cap field to read. */
export interface ForkWindowGate { enabled: boolean; count: number; cap: number; over: boolean; reason: 'no_window' | 'no_events' | 'too_many_events' | null }
export function forkWindowGate(count: number | null, hasWindow: boolean, cap = FORK_MAX_EVENTS): ForkWindowGate {
  const n = count ?? 0;
  if (!hasWindow) return { enabled: false, count: n, cap, over: false, reason: 'no_window' };
  if (count === null || n === 0) return { enabled: false, count: n, cap, over: false, reason: 'no_events' };
  if (n > cap) return { enabled: false, count: n, cap, over: true, reason: 'too_many_events' };
  return { enabled: true, count: n, cap, over: false, reason: null };
}
/** The ids of the `n` most severe events (severity descending, then the
 *  earliest start, then id) for FORK TOP N BY SEVERITY on an over-cap window. */
export function topEventIds(events: ReadonlyArray<{ id: string; severity: number; started_at: string }>, n = FORK_MAX_EVENTS): string[] {
  return [...events]
    .sort((a, b) => (b.severity - a.severity) || (a.started_at < b.started_at ? -1 : a.started_at > b.started_at ? 1 : 0) || (a.id < b.id ? -1 : 1))
    .slice(0, n).map((e) => e.id);
}

export interface ForkOptionsInput { chainCreate?: boolean; leadIn?: number; tail?: number; ticks?: number; seed?: number; weather?: boolean; params?: Record<string, unknown> }
export type ForkBodyResult = { ok: true; body: ForkRequestBody } | { ok: false; code: 'no_events' | 'too_many_events' | 'bad_window' | 'bad_option'; };
/** The POST /history/fork body for events and/or a window. Only options
 *  the caller set are sent (the server's defaults stay the server's). */
export function buildForkBody(input: { eventIds?: readonly string[]; window?: { from: string; to: string; places?: string[] }; options?: ForkOptionsInput }): ForkBodyResult {
  const ids = [...new Set(input.eventIds ?? [])];
  if (ids.length > FORK_MAX_EVENTS) return { ok: false, code: 'too_many_events' };
  const w = input.window;
  if (w && (!isIsoDay(w.from) || !isIsoDay(w.to) || w.from > w.to)) return { ok: false, code: 'bad_window' };
  if (!ids.length && !w) return { ok: false, code: 'no_events' };
  const o = input.options ?? {};
  const sim: NonNullable<NonNullable<ForkRequestBody['options']>['sim']> = {};
  if (o.leadIn !== undefined) { if (!Number.isInteger(o.leadIn) || o.leadIn < 0) return { ok: false, code: 'bad_option' }; sim.lead_in_days = o.leadIn; }
  if (o.tail !== undefined) { if (!Number.isInteger(o.tail) || o.tail < 0) return { ok: false, code: 'bad_option' }; sim.tail_days = o.tail; }
  if (o.ticks !== undefined) { if (!Number.isInteger(o.ticks) || o.ticks < 1 || o.ticks > SIM_MAX_DAY_TICKS) return { ok: false, code: 'bad_option' }; sim.ticks = o.ticks; }
  if (o.seed !== undefined) sim.seed = o.seed;
  if (o.weather !== undefined) sim.weather = o.weather;
  const options: NonNullable<ForkRequestBody['options']> = {};
  if (o.chainCreate || o.params) options.chain = { ...(o.chainCreate ? { create: true } : {}), ...(o.params ? { params: o.params } : {}) };
  if (Object.keys(sim).length) options.sim = sim;
  const body: ForkRequestBody = {};
  if (ids.length) body.event_ids = ids;
  if (w) body.window = { from: w.from, to: w.to, ...(w.places?.length ? { places: w.places } : {}) };
  if (Object.keys(options).length) body.options = options;
  return { ok: true, body };
}

/** The body for forking specific events. The window rides along with the ids:
 *  the server resolves curated events offline, but a GDACS or PortWatch event
 *  id only resolves inside the window that contains it (404 unknown_event
 *  otherwise, which is what a per-row FORK on any non-curated row returned).
 *  Only the listed ids are forked, never the whole window. */
export function buildEventForkBody(eventIds: readonly string[], win: { from: string; to: string; places?: string[] } | null | undefined, options?: ForkOptionsInput): ForkBodyResult {
  return buildForkBody({ eventIds, ...(win ? { window: { from: win.from, to: win.to, places: win.places } } : {}), options });
}

export interface LabelRow { key: string; value: string; kind?: StateKind }
/** Place id -> the label shown (the app passes the served place name). */
export type PlaceName = (id: string) => string;
const pct = (x: number | undefined): string => (typeof x === 'number' ? `${Math.round(x * 1000) / 10}%` : '—');
const dur = (d: number | undefined): string => (typeof d === 'number' ? `${Math.round(d * 100) / 100} d` : '—');
/** The chain body as state labels: engine rev, place, kind, loss,
 *  duration, start, validity, expected run id. */
export function chainLabels(cr: ForkChainRequest, engineRev: 1 | 2, name: PlaceName = (id) => id): LabelRow[] {
  const d = cr.request.scenario.disruption;
  return [
    { key: 'Engine', value: `Rev ${engineRev}` },
    { key: 'Place', value: name(d.node_id) },
    { key: 'Kind', value: labelWord(d.kind) },
    { key: 'Loss', value: pct(d.capacity_loss) },
    { key: 'Duration', value: dur(d.duration_d) },
    { key: 'Start', value: d.started_at ? d.started_at.slice(0, 10) : '—' },
    cr.validation.ok ? { key: 'Validation', value: 'OK', kind: 'meta' as StateKind } : { key: 'Validation', value: labelWord((cr.validation.code ?? 'INVALID')), kind: 'down' as StateKind },
    { key: 'Run', value: cr.run_id ?? cr.expected_run_id ?? '—' },
  ];
}
/** The sim body as state labels. One row per disruption, then horizon. */
export function simLabels(sr: ForkSimRequest, name: PlaceName = (id) => id): LabelRow[] {
  const b = sr.request;
  const rows: LabelRow[] = [];
  b.disruptions.forEach((d, i) => {
    rows.push({ key: b.disruptions.length > 1 ? `Place ${i + 1}` : 'Place', value: `${name(d.node_id)} · ${labelWord(d.kind)} · ${pct(d.capacity_loss)} · ${dur(d.duration_d)}` });
  });
  rows.push(
    { key: 'Start', value: b.start_at.slice(0, 10) },
    { key: 'Ticks', value: `${b.ticks} × ${b.tick}` },
    { key: 'Ends', value: sr.horizon?.ends_at ? sr.horizon.ends_at.slice(0, 10) : '—' },
    { key: 'Lead-in', value: sr.horizon ? `${sr.horizon.lead_in_days} d` : '—' },
    { key: 'Seed', value: String(b.seed) },
    { key: 'Weather', value: b.weather ? 'On' : 'Off' },
    sr.validation.ok ? { key: 'Validation', value: 'OK', kind: 'meta' as StateKind } : { key: 'Validation', value: labelWord((sr.validation.code ?? 'INVALID')), kind: 'down' as StateKind },
  );
  return rows;
}
/** A served fork note as a short phrase: the server's notes name request fields (capacity_loss,
 *  duration_d, day ticks); the screen says what happened. Unknown notes pass through humaniseNote. */
export function forkNoteWord(note: string): string {
  if (/capacity_cut event without a capacity_loss/i.test(note)) return 'Capacity loss missing; the engine refuses this event.';
  if (/no capacity_loss on this/i.test(note)) return 'Capacity loss defaulted; not a sourced figure.';
  if (/states no duration/i.test(note)) return 'Duration defaulted.';
  if (/event is MODELED/i.test(note)) return 'Modelled event; capacity loss is not a sourced figure.';
  const cap = /day-tick maximum is (\d+)/i.exec(note);
  if (cap) return `Run covers the first ${cap[1]} days only.`;
  if (/weather is OFF/i.test(note)) return 'Weather off.';
  if (/weather=true/i.test(note)) return 'Weather on; current conditions are pinned to a past date.';
  return humaniseNote(note);
}

export interface ForkView {
  lineageId: string; eventIds: string[]; window: DateRange | null;
  chains: Array<{ eventId: string; place: string; labels: LabelRow[]; ok: boolean; runId: string | null }>;
  sim: { labels: LabelRow[]; ok: boolean; notes: string[]; body: ForkSimRequest['request'] } | null;
  notes: string[];
}
export function forkView(resp: ForkResponse, engineRev: 1 | 2 = 1, name: PlaceName = (id) => id): ForkView {
  const created = new Map(resp.created_chain_runs.map((c) => [c.event_id, c]));
  return {
    lineageId: resp.forked_from.lineage_id,
    eventIds: resp.forked_from.events.map((e) => e.event_id),
    window: resp.forked_from.window ? { from: resp.forked_from.window.from, to: resp.forked_from.window.to } : null,
    chains: resp.chain_requests.map((cr) => {
      const c = created.get(cr.event_id);
      return { eventId: cr.event_id, place: cr.request.scenario.disruption.node_id, labels: chainLabels(cr, engineRev, name), ok: cr.validation.ok, runId: c?.created ? (c.run_id ?? null) : null };
    }),
    sim: resp.sim_request ? { labels: simLabels(resp.sim_request, name), ok: resp.sim_request.validation.ok, notes: (resp.sim_request.notes ?? []).map(forkNoteWord), body: resp.sim_request.request } : null,
    notes: (resp.notes ?? []).map(forkNoteWord),
  };
}

/* lineage registry (what was forked from where) */
export interface LineageEntry {
  lineage_id: string; events: Array<{ event_id: string; node_id: string; kind: string; started_at: string }>;
  window: DateRange | null; chain_run_ids: string[];
  sim: { start_at: string; places: string[]; ticks: number } | null; at: number;
}
export function lineageEntry(resp: ForkResponse, chainRunIds: readonly string[], at: number): LineageEntry {
  const sim = resp.sim_request?.request;
  return {
    lineage_id: resp.forked_from.lineage_id,
    events: resp.forked_from.events.map((e) => ({ event_id: e.event_id, node_id: e.node_id, kind: e.kind, started_at: e.started_at })),
    window: resp.forked_from.window ? { from: resp.forked_from.window.from, to: resp.forked_from.window.to } : null,
    chain_run_ids: [...chainRunIds],
    sim: sim ? { start_at: sim.start_at, places: sim.disruptions.map((d) => d.node_id).sort(), ticks: sim.ticks } : null,
    at,
  };
}
/** Merge a new entry into the registry (same lineage id replaces, newest
 *  first, capped). */
export function registryAdd(reg: readonly LineageEntry[], e: LineageEntry, cap = 24): LineageEntry[] {
  return [e, ...reg.filter((x) => x.lineage_id !== e.lineage_id)].slice(0, cap);
}
export function lineageForChain(reg: readonly LineageEntry[], runId: string): LineageEntry | null {
  return reg.find((e) => e.chain_run_ids.includes(runId)) ?? null;
}
/** A sim run matches a fork when its start and its set of disruption
 *  places equal the fork's sim body. */
export function lineageForSim(reg: readonly LineageEntry[], journal: { start_at: string; disruptions: Array<{ node_id?: string }> } | null | undefined): LineageEntry | null {
  if (!journal) return null;
  const places = journal.disruptions.map((d) => d.node_id ?? '').sort();
  return reg.find((e) => e.sim && e.sim.start_at === journal.start_at && e.sim.places.length === places.length && e.sim.places.every((p, i) => p === places[i])) ?? null;
}
/** `place day [+ place day]`: where the run was forked from. */
export const lineageChipMeta = (e: LineageEntry, name: PlaceName = (id) => id): string =>
  e.events.map((x) => `${name(x.node_id)} ${x.started_at.slice(0, 10)}`).join(' + ');

/* ── realism: tester tables ──────────────────────────────────────────── */
export const REALISM_MAX_WINDOW_D = 150;
export function realismBody(input: { run: string; from?: string; to?: string; places?: string[]; store?: boolean }): { ok: true; body: Record<string, unknown> } | { ok: false; code: 'no_run' | 'bad_window' | 'window_too_large' } {
  const run = input.run.trim();
  if (!run) return { ok: false, code: 'no_run' };
  const body: Record<string, unknown> = { run };
  if (input.from || input.to) {
    if (!input.from || !input.to || !isIsoDay(input.from) || !isIsoDay(input.to) || input.from > input.to) return { ok: false, code: 'bad_window' };
    if (daysBetween(input.from, input.to) + 1 > REALISM_MAX_WINDOW_D) return { ok: false, code: 'window_too_large' };
    body.from = input.from; body.to = input.to;
  }
  if (input.places?.length) body.places = input.places;
  if (input.store) body.store = true;
  return { ok: true, body };
}

/* ── realism: descriptive counts only ───────────────────────────────── */
/* The card compares a recorded outcome with a simulated one, place by place.
 * The screen describes what was compared (counts, directions, percentages) and
 * never scores it: no hit rates, no rank correlation, no baseline verdicts. */
export interface CountChip { key: 'places' | 'direction' | 'magnitude' | 'onset'; label: string; n: number; of: number | null }
/** Counts of graded entries on a card: how many places it holds and how many
 *  of them were graded for direction, magnitude and onset. */
export function describeCard(card: Pick<RealismCard, 'places' | 'metrics'>): CountChip[] {
  const total = card.places.length;
  const n = (c: RateCell | undefined): number => (typeof c?.graded === 'number' ? c.graded : 0);
  return [
    { key: 'places', label: 'Places', n: total, of: null },
    { key: 'direction', label: 'Direction', n: n(card.metrics.direction), of: total },
    { key: 'magnitude', label: 'Magnitude', n: n(card.metrics.magnitude), of: total },
    { key: 'onset', label: 'Onset', n: n(card.metrics.onset), of: total },
  ];
}
/** `3 of 4`, or `3` when there is no total. */
export const countText = (c: Pick<CountChip, 'n' | 'of'>): string => (c.of === null ? `${c.n}` : `${c.n} of ${c.of}`);

export interface PlaceRow {
  place: string; role: string; observedDir: string; observedPct: string;
  modelDir: string; modelPct: string; onset: string;
}
const dirWord = (d: string | null | undefined): string => (d ? labelWord(d) : 'No direction');
const sgn = (x: number | null | undefined): string => (typeof x === 'number' ? `${x > 0 ? '+' : ''}${x.toFixed(1)}%` : '—');
export function placeRows(card: Pick<RealismCard, 'places'>): PlaceRow[] {
  return card.places.map((p) => ({
    place: p.place, role: labelWord(p.role),
    observedDir: dirWord(p.observed?.direction), observedPct: sgn(p.observed?.change_pct),
    modelDir: dirWord(p.model?.direction), modelPct: sgn(p.model?.change_pct),
    onset: p.observed?.onset_date ? `${p.observed.onset_date}${typeof p.observed.onset_lag_d === 'number' ? ` +${p.observed.onset_lag_d} d` : ''}` : '—',
  }));
}
export interface ResultRow { id: string; title: string; runKind: string; runId: string; graded: string; window: string }
/** One row per stored card: the window and how many places were graded for direction. */
export function resultRows(items: readonly RealismResultItem[]): ResultRow[] {
  return items.map((i) => {
    const den = /\/(\d+)$/.exec(i.headline.direction?.model ?? '');
    return {
      id: i.card_id, title: i.title ?? i.run_id, runKind: labelWord(i.run_kind), runId: i.run_id,
      graded: den ? den[1] : '—', window: `${i.window.from} → ${i.window.to}`,
    };
  });
}

/* observed vs simulated overlay for one place */
export interface OverlayModel {
  place: string; days: string[];
  observed: DayPoint[]; simulated: DayPoint[];
  obsExt: Extent | null; simExt: Extent | null;
  obsPaths: string[]; simPaths: string[];
  eventX: number | null;
  band: { baseline: [number, number] | null; evaluation: [number, number] | null };
}
export function overlayModel(card: Pick<RealismCard, 'series' | 'window'>, place: string, box: Box): OverlayModel | null {
  const s = card.series?.[place];
  if (!s) return null;
  const { from, to } = card.window;
  const days = dateRange(from, to);
  const observed = alignPairs(s.observed, from, to);
  const simulated = alignPairs(s.simulated, from, to);
  const obsExt = extentOf(observed); const simExt = extentOf(simulated);
  const n = days.length;
  const x = (d?: string) => (d && d >= from && d <= to ? xAt(daysBetween(from, d), n, box) : null);
  const span = (a?: string, b?: string): [number, number] | null => {
    if (!a || !b) return null;
    const lo = maxIso(a, from); const hi = minIso(b, to);
    return lo > hi ? null : [xAt(daysBetween(from, lo), n, box), xAt(daysBetween(from, hi), n, box)];
  };
  return {
    place, days, observed, simulated, obsExt, simExt,
    obsPaths: linePaths(observed, obsExt, box), simPaths: linePaths(simulated, simExt, box),
    eventX: x(card.window.event_start),
    band: { baseline: span(card.window.baseline?.from, card.window.baseline?.to), evaluation: span(card.window.evaluation_from, card.window.evaluation_to) },
  };
}
/** Places that have a series in the card, in the card's own order. */
export const seriesPlaces = (card: Pick<RealismCard, 'places' | 'series'>): string[] => card.places.map((p) => p.place).filter((p) => !!card.series?.[p]);
