/* LANE PLANNER logic: pure functions (no DOM, no React) so every number
 * and every drawing is testable and the on-screen chart is the exported one.
 * No prose is produced here: labels, chips, units and codes only. */
import { AXIS, GRID, INK, INK2, INK3, SURFACE, text } from '../../charts/draw';
import { CHART_FS, clipLabel, textWidth } from '../../charts/format';
import { linearScale, niceTicks } from '../../charts/scale';
import type { ExportTable } from '../../charts/types';
import { h, round, type VNode } from '../../charts/vnode';
import type { GeoLeg, PlanDoc, PlaceRow, Range3, RouteDoc, Scenario } from './types';
import { labelWord } from '../../copy';

/* ── formatting ─────────────────────────────────────────────────────── */
export const NOT_SERVED = 'Not served';
/** Days with one decimal. */
export const fmtD = (v: number | null | undefined): string => (v == null || !Number.isFinite(v) ? NOT_SERVED : v.toFixed(1));
/** Range ends: whole days from ten, one decimal below (a range is not a point). */
export const fmtEnd = (v: number | null | undefined): string => (v == null || !Number.isFinite(v) ? NOT_SERVED : Math.abs(v) >= 10 ? String(Math.round(v)) : v.toFixed(1));
export const fmtRange = (r: Range3 | null | undefined): string => (r ? `${fmtEnd(r.low)} to ${fmtEnd(r.high)}` : NOT_SERVED);
export const fmtSigned = (v: number | null | undefined): string => (v == null || !Number.isFinite(v) ? NOT_SERVED : Math.abs(v) < 0.05 ? '0.0' : `${v > 0 ? '+' : '−'}${Math.abs(v).toFixed(1)}`);
export const fmtKm = (v: number | null | undefined): string => (v == null || !Number.isFinite(v) ? NOT_SERVED : Math.round(v).toLocaleString('en-US'));
export const fmtPct = (v: number | null | undefined): string => (v == null || !Number.isFinite(v) ? NOT_SERVED : `${v.toFixed(1)}%`);
export const dPlus = (v: number): string => `D+${Math.round(v)}`;

/* ── route colours: one slot per corridor, stable ───────────────────── */
const CORRIDOR_SLOT: Record<string, number> = { suez: 2, cape: 1, panama: 3, other: 7, direct: 7 };
export function routeColors(routes: readonly RouteDoc[]): Record<string, string> {
  const used = new Set<number>();
  const out: Record<string, string> = {};
  for (const r of routes) {
    let slot = CORRIDOR_SLOT[r.corridor] ?? 7;
    for (let guard = 0; used.has(slot) && guard < 8; guard++) slot = (slot % 8) + 1;
    used.add(slot);
    out[r.id] = `var(--cs-${slot})`;
  }
  return out;
}

/* ── state words ────────────────────────────────────────────────────── */
export function chipFor(label: string | undefined | null): string {
  const l = (label ?? '').toUpperCase();
  return l === 'MODELLED' || l === 'SIMULATED' || l === 'SOURCED' ? l : l === 'MODELED' ? 'Modelled' : NOT_SERVED;
}
export const stateChip = (state: string): string => (state === 'WITHIN CITED RANGE' || state === 'WITHIN' ? 'Sourced' : state === 'NO CITED RANGE' ? 'Modelled' : 'Flag');

/* ── refusal wording: the named reason as a code, never a sentence ─── */
export const REFUSAL_WORDS: Record<string, string> = {
  unknown_place: 'Unknown place', same_place: 'Same place', not_a_sea_place: 'Not a sea place', shell_endpoint: 'Estimated place not included',
  no_sea_path: 'No sea path', unknown_scenario: 'Unknown scenario', disruption_place_not_modelled: 'Disruption place not modelled',
  invalid_disruption: 'Invalid disruption', invalid_request: 'Invalid request', bad_json: 'Invalid request',
  shell_same_anchor: 'Same access place', shell_anchor_is_endpoint: 'Access place is endpoint', shell_no_core_leg: 'No access leg',
};
export const refusalWord = (code: string): string => REFUSAL_WORDS[code] ?? labelWord(code.replace(/_/g, ' '));

/* ── places ─────────────────────────────────────────────────────────── */
export interface PlaceChoice { id: string; name: string; kind: string; mode: string; tier: string; country: string | null; lat: number; lon: number }
export function placeChoices(nodes: readonly unknown[]): PlaceChoice[] {
  const out: PlaceChoice[] = [];
  for (const n of nodes) {
    const o = n as Record<string, unknown>;
    if (typeof o?.id !== 'string' || typeof o.lat !== 'number' || typeof o.lon !== 'number') continue;
    out.push({
      id: o.id, name: typeof o.name === 'string' ? o.name : o.id, kind: typeof o.kind === 'string' ? o.kind : 'place',
      mode: typeof o.mode === 'string' ? o.mode : 'sea', tier: typeof o.tier === 'string' ? o.tier : 'core',
      country: typeof o.country === 'string' ? o.country : null, lat: o.lat, lon: o.lon,
    });
  }
  return out.sort((a, b) => (a.tier === b.tier ? 0 : a.tier === 'core' ? -1 : 1) || (a.mode === b.mode ? 0 : a.mode === 'sea' ? -1 : 1) || a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
}
/** The tag a picker row carries: what the planner will say about this place.
 *  Place tier in plain words: Core (sourced data) or Estimated (shell data). */
export const placeTag = (p: Pick<PlaceChoice, 'mode' | 'tier' | 'kind'>): string => (p.mode !== 'sea' ? labelWord(p.mode) : p.tier !== 'core' ? 'Estimated' : p.kind === 'chokepoint' ? 'Chokepoint' : 'Core');
/** The stable key behind the tag, for the stylesheet class. */
export const placeTagKey = (p: Pick<PlaceChoice, 'mode' | 'tier' | 'kind'>): string => (p.mode !== 'sea' ? p.mode : p.tier !== 'core' ? 'shell' : p.kind === 'chokepoint' ? 'chokepoint' : 'core');

/* ── the plan request ───────────────────────────────────────────────── */
export interface PlanInput { origin: string; destination: string; extra: boolean; includeEstimated: boolean; choice: DisruptionChoice }
/** POST /lanes/plan body. `includeEstimated` is the Place tier switch: on, an
 *  estimate-grade endpoint is served through its nearest calibrated place; off,
 *  the server refuses it and names the calibrated neighbours. */
export function planBody(i: PlanInput, library: readonly Scenario[]) {
  return { origin: i.origin, destination: i.destination, extra: i.extra, shell_access: i.includeEstimated, disruption: disruptionBody(i.choice, library) };
}
/** Duration field text to a whole number of days in 1 to 3650 (applied on blur). */
export const clampDuration = (text: string, fallback = 1): number => {
  const n = Math.round(Number(text));
  return Number.isFinite(n) && n >= 1 ? Math.min(3650, n) : fallback;
};
/** A named variant as a short word: `SPEED-FAST` is `Fast`. */
export const variantWord = (id: string): string => labelWord(id.replace(/^(SPEED|QUEUE)[-_]/i, '').replace(/[-_]+/g, ' '));
export function searchPlaces(all: readonly PlaceChoice[], query: string, limit = 12): PlaceChoice[] {
  const q = query.trim().toLowerCase();
  if (!q) return all.slice(0, limit);
  const scored: Array<[number, PlaceChoice]> = [];
  for (const p of all) {
    const name = p.name.toLowerCase(); const id = p.id.toLowerCase(); const ctry = (p.country ?? '').toLowerCase();
    let s = -1;
    if (name === q || id === q) s = 0;
    else if (name.startsWith(q)) s = 1;
    else if (name.split(/[\s-]+/).some((w) => w.startsWith(q))) s = 2;
    else if (name.includes(q) || id.includes(q)) s = 3;
    else if (ctry.startsWith(q)) s = 4;
    if (s >= 0) scored.push([s * 10 + (p.tier === 'core' ? 0 : 5) + (p.mode === 'sea' ? 0 : 3), p]);
  }
  return scored.sort((a, b) => a[0] - b[0] || a[1].name.localeCompare(b[1].name)).slice(0, limit).map((x) => x[1]);
}

/* ── scenarios -> request ───────────────────────────────────────────── */
export interface DisruptionChoice { scenarioId: string | null; duration: number; loss: number | null }
export function disruptionBody(c: DisruptionChoice, library: readonly Scenario[]): { scenario_id: string; duration_d: number; capacity_loss?: number } | null {
  if (!c.scenarioId) return null;
  const s = library.find((x) => x.id === c.scenarioId);
  if (!s) return null;
  const body: { scenario_id: string; duration_d: number; capacity_loss?: number } = { scenario_id: s.id, duration_d: c.duration };
  if (s.kind !== 'closure' && c.loss != null) body.capacity_loss = c.loss;
  return body;
}

const lcFirst = (s: string): string => (s ? s[0].toLowerCase() + s.slice(1) : s);

/* ── lane view: the lane-based figures replace the chord-based ones in the panel ─────────────────────
 * A plan from a server that has the lane geometry sidecar carries, per route, `lane` (kilometres and days
 * computed from lane nautical miles, the queue block unchanged, the timeline) beside the old chord-based
 * fields. The panel shows the lane figures (still MODELLED, ranges low <= median <= high); a plan without
 * them is shown as it is. The old fields stay in the document (JSON export keeps both). */
export function hasLaneBasis(doc: PlanDoc): boolean {
  return doc.lane_basis?.state === 'SERVED' && doc.routes.length > 0 && doc.routes.every((r) => !!r.lane);
}
export function laneView(doc: PlanDoc): PlanDoc {
  if (!hasLaneBasis(doc)) return doc;
  const routes = doc.routes.map((r) => {
    const l = r.lane!;
    return {
      ...r, km: l.km, sail: l.sail, queue: l.queue ?? r.queue, total: l.total, vs_baseline: l.vs_baseline, timeline: l.timeline,
      queue_at_arrival: l.queue_at_arrival ?? r.queue_at_arrival, total_at_arrival: l.total_at_arrival ?? r.total_at_arrival,
      queue_at_arrival_by_place: l.queue_at_arrival_by_place ?? r.queue_at_arrival_by_place,
    };
  });
  const base = routes.find((r) => r.id === doc.baseline.route_id);
  const speeds = doc.routes[0].lane!.speed_kn;
  const sail = [
    { id: 'SPEED-FAST', speed_kn: speeds.fast, factor: 1, label: 'MODELLED' },
    { id: 'SPEED-BASE', speed_kn: speeds.base, factor: 1, label: 'MODELLED' },
    { id: 'SPEED-SLOW', speed_kn: speeds.slow, factor: 1, label: 'MODELLED' },
  ];
  const checks = doc.checks.map((c) => {
    if (c.id !== 'cape_vs_suez' || !c.lane) return c;
    const k = c.lane;
    return {
      ...c, flag: k.flag, state: k.state, sources: k.sources, reference: k.reference ?? c.reference,
      engine: { suez_km: k.suez_km, cape_km: k.cape_km, detour_km: k.detour_km, ratio: k.ratio, cut_pct: k.cut_pct, label: k.label },
    };
  });
  return {
    ...doc, routes, checks,
    baseline: base ? { ...doc.baseline, sail: base.sail } : doc.baseline,
    variants: { ...doc.variants, sail },
    time_basis: { ...doc.time_basis, kind: 'lane nautical miles', leg: null, leg_days: null, route_km: null, implied_speed_kn: null },
  };
}
/** Legs of a plan drawn as straight chords because they have no lane geometry (the Approx. count). */
export const approxLegCount = (doc: PlanDoc): number => doc.routes.reduce((n, r) => n + (r.geometry?.approx_legs ?? 0), 0);

/* ── the comparison table ───────────────────────────────────────────── */
export interface TableRow {
  key: string; label: string; basis: string; baseline: boolean; color: string;
  median: number; range: Range3; vs: number | null; queue: Range3 | null; arrival: Range3 | null; sail: Range3; km: number;
  /** `not_served`: the plan carries no queue block (older server, or none simulated); `zero`: a block exists and its high is 0. */
  queueState: 'served' | 'zero' | 'not_served' | 'none';
  chip: string; flagged: boolean;
}
export function tableRows(doc: PlanDoc): TableRow[] {
  const colors = routeColors(doc.routes);
  const rows: TableRow[] = [];
  if (doc.disruption) {
    rows.push({ key: 'baseline', label: `Baseline ${lcFirst(doc.baseline.label)}`, basis: 'baseline', baseline: true, color: 'var(--text-3)',
      median: doc.baseline.sail.median, range: doc.baseline.sail, vs: null, queue: null, arrival: null, sail: doc.baseline.sail, queueState: 'none',
      km: doc.routes.find((r) => r.id === doc.baseline.route_id)?.km.value ?? NaN, chip: chipFor(doc.baseline.sail.label), flagged: false });
  }
  for (const r of doc.routes) {
    const q = r.queue && r.queue.high > 0 ? r.queue : null;
    rows.push({ key: r.id, label: doc.disruption ? (r.disrupted_place_on_route && q ? `${r.label}, queue` : r.label) : (r.is_baseline ? `Baseline ${lcFirst(r.label)}` : r.label),
      basis: r.basis, baseline: !doc.disruption && r.is_baseline, color: colors[r.id], median: r.total.median, range: r.total,
      vs: doc.disruption || !r.is_baseline ? r.vs_baseline.median : null, queue: q, arrival: r.queue_at_arrival ?? null, sail: r.sail, km: r.km.value,
      queueState: r.queue == null ? 'not_served' : r.queue.high > 0 ? 'served' : 'zero',
      chip: chipFor(r.total.label), flagged: !r.floor_ok });
  }
  return rows;
}

/* ── the range chart ────────────────────────────────────────────────── */
export interface RangeChart { nodes: VNode[]; width: number; height: number; table: ExportTable; rowY: Record<string, number>; rowH: number }
const ROW_H = 40;
export function buildRangeChart(doc: PlanDoc, width: number, selected: string | null): RangeChart {
  const rows = tableRows(doc);
  const w = Math.max(320, Math.round(width));
  const lbl = Math.min(170, Math.round(w * 0.3));
  const top = 34; const bottom = 28; const right = 16;
  const height = top + rows.length * ROW_H + bottom;
  const plotX = lbl + 12; const plotW = w - plotX - right;
  const baseMed = doc.baseline.sail.median;
  const lows = rows.map((r) => r.range.low); const highs = rows.map((r) => r.range.high);
  const lo = Math.min(...lows, baseMed); const hi = Math.max(...highs, baseMed);
  const pad = Math.max(1, (hi - lo) * 0.08);
  const ticks = niceTicks(Math.max(0, lo - pad), hi + pad, Math.max(3, Math.floor(plotW / 70)));
  const d0 = Math.min(ticks[0] ?? lo, lo - pad); const d1 = Math.max(ticks[ticks.length - 1] ?? hi, hi + pad);
  const sx = linearScale([d0, d1], [plotX, plotX + plotW]);
  const nodes: VNode[] = [h('rect', { x: 0, y: 0, width: w, height, fill: SURFACE })];
  // grid + axis
  for (const t of ticks) {
    const x = round(sx(t));
    nodes.push(h('line', { x1: x, x2: x, y1: top - 6, y2: top + rows.length * ROW_H, stroke: GRID, strokeWidth: 1, strokeOpacity: 0.7 }));
    nodes.push(text(x, top + rows.length * ROW_H + 14, String(t), { anchor: 'middle', size: CHART_FS }));
  }
  nodes.push(text(plotX + plotW, top + rows.length * ROW_H + 25, 'days', { anchor: 'end', size: CHART_FS, fill: INK3 }));
  nodes.push(h('line', { x1: plotX, x2: plotX + plotW, y1: top + rows.length * ROW_H, y2: top + rows.length * ROW_H, stroke: AXIS, strokeWidth: 1 }));
  const rowY: Record<string, number> = {};
  rows.forEach((r, i) => {
    const y0 = top + i * ROW_H; const cy = y0 + ROW_H / 2 + 3;
    rowY[r.key] = y0;
    const sel = selected === r.key;
    if (sel) nodes.push(h('rect', { x: 0, y: y0, width: w, height: ROW_H, fill: 'var(--sel-fill)', fillOpacity: 0.55 }));
    nodes.push(text(8, cy, clipLabel(r.label, lbl - 8, CHART_FS), { size: CHART_FS, fill: INK, baseline: 'middle', weight: sel ? 600 : 400 }));
    const x0 = round(sx(r.range.low)); const x1 = round(sx(r.range.high)); const xm = round(sx(r.median));
    const color = r.color;
    const barH = 14;
    nodes.push(h('rect', { x: x0, y: cy - barH / 2, width: Math.max(2, x1 - x0), height: barH, rx: 2, fill: color, fillOpacity: r.baseline ? 0.28 : 0.38, stroke: color, strokeWidth: 1 }));
    // queue share of the median, drawn as a darker segment at the right of the median span
    nodes.push(h('line', { x1: xm, x2: xm, y1: cy - barH / 2 - 3, y2: cy + barH / 2 + 3, stroke: color, strokeWidth: 3 }));
    nodes.push(text(x0 - 4, cy, fmtEnd(r.range.low), { anchor: 'end', size: CHART_FS, baseline: 'middle' }));
    nodes.push(text(x1 + 4, cy, fmtEnd(r.range.high), { anchor: 'start', size: CHART_FS, baseline: 'middle' }));
    nodes.push(text(xm, cy - barH / 2 - 6, fmtD(r.median), { anchor: 'middle', size: CHART_FS, fill: INK, weight: 600 }));
  });
  // baseline reference line
  const bx = round(sx(baseMed));
  nodes.push(h('line', { x1: bx, x2: bx, y1: top - 8, y2: top + rows.length * ROW_H, stroke: INK, strokeWidth: 1, strokeDasharray: '3 3' }));
  nodes.push(text(bx, top - 14, `Baseline ${fmtD(baseMed)}`, { anchor: 'middle', size: CHART_FS, fill: INK }));
  const table: ExportTable = {
    columns: ['option', 'low [days]', 'median [days]', 'high [days]', 'vs baseline median [days]', 'label'],
    rows: rows.map((r) => [r.label, r.range.low, r.median, r.range.high, r.vs, r.chip]),
  };
  return { nodes, width: w, height, table, rowY, rowH: ROW_H };
}

/* ── the voyage timeline ────────────────────────────────────────────── */
export interface TimelineChart { nodes: VNode[]; width: number; height: number; table: ExportTable }
const T_ROW = 34;
export function buildTimeline(doc: PlanDoc, width: number, selected: string | null): TimelineChart {
  const w = Math.max(360, Math.round(width));
  const lbl = 156; const top = 24; const right = 14;
  const colors = routeColors(doc.routes);
  const rows: Array<{ key: string; label: string; color: string }> = [];
  if (doc.disruption) rows.push({ key: 'closure', label: 'Disruption', color: 'var(--range)' });
  for (const r of doc.routes) rows.push({ key: r.id, label: r.label, color: colors[r.id] });
  const height = top + rows.length * T_ROW + 8;
  const ends = doc.routes.map((r) => r.timeline[r.timeline.length - 1]?.end.high ?? r.total.high);
  const horizon = Math.max(1, ...ends, doc.disruption ? doc.disruption.end_d.high : 0);
  const ticks = niceTicks(0, horizon, Math.max(4, Math.floor((w - lbl - right) / 60)));
  const plotX = lbl + 8; const plotW = w - plotX - right;
  const sx = linearScale([0, Math.max(horizon, ticks[ticks.length - 1] ?? horizon)], [plotX, plotX + plotW]);
  const nodes: VNode[] = [h('rect', { x: 0, y: 0, width: w, height, fill: SURFACE })];
  nodes.push(h('rect', { x: plotX, y: 0, width: plotW, height: top - 4, fill: 'var(--bg-bar)' }));
  for (const t of ticks) {
    const x = round(sx(t));
    nodes.push(h('line', { x1: x, x2: x, y1: top - 4, y2: height - 6, stroke: GRID, strokeWidth: 1, strokeOpacity: 0.7 }));
    if (x + 3 + textWidth(dPlus(t), CHART_FS) <= w) nodes.push(text(x + 3, 14, dPlus(t), { size: CHART_FS, fill: INK2 })); /* a tick label that would run past the right edge is dropped */
  }
  const tableRows: ExportTable['rows'] = [];
  rows.forEach((row, i) => {
    const y = top + i * T_ROW; const cy = y + T_ROW / 2 - 2; const bh = 18;
    if (selected === row.key) nodes.push(h('rect', { x: 0, y, width: w, height: T_ROW, fill: 'var(--sel-fill)', fillOpacity: 0.45 }));
    nodes.push(text(8, cy, clipLabel(row.label, lbl - 10, CHART_FS), { size: CHART_FS, fill: INK, baseline: 'middle' }));
    if (row.key === 'closure' && doc.disruption) {
      const c = doc.disruption;
      const xe = round(sx(c.end_d.median)); const xl = round(sx(c.end_d.low)); const xh = round(sx(c.end_d.high));
      nodes.push(h('rect', { x: xl, y: cy - bh / 2, width: Math.max(2, xh - xl), height: bh, fill: 'var(--range)', fillOpacity: 0.12, stroke: 'var(--range)', strokeWidth: 1, strokeDasharray: '2 2' }));
      nodes.push(h('rect', { x: plotX, y: cy - bh / 2, width: Math.max(2, xe - plotX), height: bh, fill: 'var(--range-fill)', stroke: 'var(--range)', strokeWidth: 1.5 }));
      const cl = `${c.name} · ${dPlus(0)} to ${dPlus(c.end_d.median)}`;
      if (xe - plotX - 10 >= textWidth(cl, CHART_FS)) nodes.push(text(plotX + 6, cy, cl, { size: CHART_FS, fill: INK, baseline: 'middle' }));
      else nodes.push(text(Math.max(xe, xh) + 8, cy, clipLabel(cl, Math.max(60, plotX + plotW - Math.max(xe, xh) - 10), CHART_FS), { size: CHART_FS, fill: INK, baseline: 'middle' }));
      tableRows.push(['Disruption', 'closure', c.name, c.start_d, c.end_d.low, c.end_d.median, c.end_d.high, chipFor(c.label)]);
      return;
    }
    const r = doc.routes.find((x) => x.id === row.key);
    if (!r) return;
    const nameOf = (id?: string) => r.places.find((p) => p.id === id)?.name ?? id ?? '';
    for (const s of r.timeline) {
      const xs = round(sx(s.start.median)); const xe = round(sx(s.end.median));
      const isQ = s.kind === 'queue';
      const fill = isQ ? 'var(--bar-queue)' : row.color;
      nodes.push(h('rect', { x: xs, y: cy - bh / 2, width: Math.max(1.5, xe - xs - 1), height: bh, rx: 2, fill, fillOpacity: isQ ? 0.85 : 0.5, stroke: fill, strokeWidth: 1 }));
      const lab = isQ ? `Queue +${s.d.median.toFixed(1)} d` : `${nameOf(s.from)} to ${nameOf(s.to)} ${s.d.median.toFixed(1)} d`;
      if (xe - xs > textWidth(lab, CHART_FS) + 8) nodes.push(text(xs + 5, cy, lab, { size: CHART_FS, fill: INK, baseline: 'middle' }));
      tableRows.push([r.label, s.kind, isQ ? nameOf(s.place_id) : `${nameOf(s.from)} to ${nameOf(s.to)}`, s.start.median, s.d.low, s.d.median, s.d.high, isQ ? 'SIMULATED' : 'MODELLED']);
    }
    const last = r.timeline[r.timeline.length - 1];
    if (last) {
      const xl = round(sx(last.end.low)); const xm = round(sx(last.end.median)); const xh = round(sx(last.end.high));
      const wy = cy + bh / 2 + 3.5;
      nodes.push(h('line', { x1: xl, x2: xh, y1: wy, y2: wy, stroke: INK, strokeWidth: 1, strokeOpacity: 0.85 }));
      nodes.push(h('line', { x1: xl, x2: xl, y1: wy - 2.5, y2: wy + 2.5, stroke: INK, strokeWidth: 1 }));
      nodes.push(h('line', { x1: xh, x2: xh, y1: wy - 2.5, y2: wy + 2.5, stroke: INK, strokeWidth: 1 }));
      nodes.push(h('path', { d: `M${xm} ${cy - 6}L${xm + 6} ${cy}L${xm} ${cy + 6}L${xm - 6} ${cy}Z`, fill: INK, stroke: SURFACE, strokeWidth: 1 }));
    }
  });
  return { nodes, width: w, height, table: { columns: ['route', 'segment', 'detail', 'start median [days]', 'low [days]', 'median [days]', 'high [days]', 'label'], rows: tableRows } };
}

/* ── the route preview map ──────────────────────────────────────────── */
export interface LL { lon: number; lat: number }
export interface MapModel {
  w: number; h: number;
  x: (lon: number) => number; y: (lat: number) => number;
  bbox: { lon0: number; lon1: number; lat0: number; lat1: number };
  /** `d` is the route as drawn (lane polylines where the plan has them, the chord otherwise); `approx` is the
   *  part of it that is a chord because the leg has no lane geometry (drawn dashed, labelled Approx.). */
  lines: Array<{ routeId: string; label: string; color: string; dashed: boolean; d: string; approx: string; selected: boolean }>;
  points: Array<{ id: string; name: string; x: number; y: number; role: string; routes: string[]; disrupted: boolean }>;
}
/** Break a polyline where it crosses the antimeridian, so a Pacific leg is
 *  drawn as two pieces at the map edges, not as a line across the world. */
export function splitAntimeridian(pts: readonly LL[]): LL[][] {
  if (pts.length === 0) return [];
  const out: LL[][] = [[pts[0]]];
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1]; const b = pts[i];
    if (Math.abs(b.lon - a.lon) > 180) {
      const east = a.lon > 0; // from the eastern edge to the western edge, or back
      const bUn = b.lon + (east ? 360 : -360);
      const t = ((east ? 180 : -180) - a.lon) / (bUn - a.lon);
      const lat = a.lat + (b.lat - a.lat) * t;
      out[out.length - 1].push({ lon: east ? 180 : -180, lat });
      out.push([{ lon: east ? -180 : 180, lat }, b]);
    } else out[out.length - 1].push(b);
  }
  return out;
}
export function mapBox(places: readonly Pick<PlaceRow, 'lat' | 'lon'>[]): MapModel['bbox'] {
  let lon0 = 180; let lon1 = -180; let lat0 = 90; let lat1 = -90;
  for (const p of places) { lon0 = Math.min(lon0, p.lon); lon1 = Math.max(lon1, p.lon); lat0 = Math.min(lat0, p.lat); lat1 = Math.max(lat1, p.lat); }
  if (!(lon0 <= lon1)) return { lon0: -180, lon1: 180, lat0: -60, lat1: 80 };
  const padLon = Math.max(8, (lon1 - lon0) * 0.08); const padLat = Math.max(6, (lat1 - lat0) * 0.12);
  return { lon0: Math.max(-180, lon0 - padLon), lon1: Math.min(180, lon1 + padLon), lat0: Math.max(-85, lat0 - padLat), lat1: Math.min(85, lat1 + padLat) };
}
export function mapModel(doc: PlanDoc, w: number, hgt: number, selected: string | null): MapModel {
  const colors = routeColors(doc.routes);
  const all = doc.routes.flatMap((r) => r.places);
  /* a lane runs outside the box of its places (round a cape, through a strait): the box takes every vertex */
  const geoPts = doc.routes.flatMap((r) => (r.geometry?.legs ?? []).flatMap((l) => l.parts.flatMap((p) => p.map(([lon, lat]) => ({ lon, lat })))));
  const bbox = mapBox([...all, ...geoPts]);
  const pad = 14;
  const spanLon = bbox.lon1 - bbox.lon0; const spanLat = bbox.lat1 - bbox.lat0;
  const s = Math.min((w - pad * 2) / spanLon, (hgt - pad * 2) / spanLat);
  const ox = (w - spanLon * s) / 2; const oy = (hgt - spanLat * s) / 2;
  const x = (lon: number) => ox + (lon - bbox.lon0) * s;
  const y = (lat: number) => oy + (bbox.lat1 - lat) * s;
  const pathOf = (parts: ReadonlyArray<ReadonlyArray<LL>>): string =>
    parts.map((seg) => seg.map((p, i) => `${i ? 'L' : 'M'}${round(x(p.lon))} ${round(y(p.lat))}`).join('')).join('');
  const lines: MapModel['lines'] = doc.routes.map((r) => {
    const dashed = !!doc.disruption && r.disrupted_place_on_route;
    if (r.geometry && r.geometry.legs.length > 0) {
      const asLL = (parts: GeoLeg['parts']) => parts.map((seg) => seg.map(([lon, lat]) => ({ lon, lat })));
      const lane = r.geometry.legs.filter((l) => l.state === 'lane').flatMap((l) => asLL(l.parts));
      const approx = r.geometry.legs.filter((l) => l.state !== 'lane').flatMap((l) => asLL(l.parts));
      return { routeId: r.id, label: r.label, color: colors[r.id], dashed, d: pathOf(lane), approx: pathOf(approx), selected: selected === r.id };
    }
    return { routeId: r.id, label: r.label, color: colors[r.id], dashed, d: pathOf(splitAntimeridian(r.places.map((p) => ({ lon: p.lon, lat: p.lat })))), approx: '', selected: selected === r.id };
  });
  const seen = new Map<string, MapModel['points'][number]>();
  for (const r of doc.routes) {
    for (const p of r.places) {
      const cur = seen.get(p.id);
      const role = p.role ?? 'waypoint';
      if (cur) { cur.routes.push(r.id); if (cur.role === 'waypoint' && role !== 'waypoint') cur.role = role; continue; }
      seen.set(p.id, { id: p.id, name: p.name, x: round(x(p.lon)), y: round(y(p.lat)), role, routes: [r.id], disrupted: doc.disruption?.place_id === p.id });
    }
  }
  return { w, h: hgt, x, y, bbox, lines, points: [...seen.values()].sort((a, b) => a.id.localeCompare(b.id)) };
}

/** GeoJSON land (Polygon / MultiPolygon) to one SVG path for the view. */
export function landPath(geo: unknown, m: Pick<MapModel, 'x' | 'y' | 'bbox'>): string {
  const feats = ((geo as { features?: Array<{ geometry?: { type?: string; coordinates?: unknown } }> })?.features) ?? [];
  const { lon0, lon1, lat0, lat1 } = m.bbox;
  const parts: string[] = [];
  const ring = (r: number[][]) => {
    let inside = false;
    for (const [lo, la] of r) if (lo >= lon0 - 20 && lo <= lon1 + 20 && la >= lat0 - 20 && la <= lat1 + 20) { inside = true; break; }
    if (!inside || r.length < 3) return;
    parts.push(`${r.map(([lo, la], i) => `${i ? 'L' : 'M'}${round(m.x(lo))} ${round(m.y(la))}`).join('')}Z`);
  };
  for (const f of feats) {
    const g = f.geometry;
    if (!g) continue;
    if (g.type === 'Polygon') (g.coordinates as number[][][]).forEach(ring);
    else if (g.type === 'MultiPolygon') (g.coordinates as number[][][][]).forEach((poly) => poly.forEach(ring));
  }
  return parts.join('');
}

/* ── CSV rows (Panel export) ────────────────────────────────────────── */
export function csvRows(doc: PlanDoc): Array<Record<string, unknown>> {
  const closure = doc.disruption;
  const rows: Array<Record<string, unknown>> = doc.routes.map((r) => ({
    row_type: 'route', plan_id: doc.plan_id, schema: doc.schema, engine: doc.engine_label,
    origin: doc.places.origin.name, destination: doc.places.destination.name,
    disruption: closure ? `${closure.name} ${closure.kind} ${closure.duration_d} d` : 'none',
    route: r.label, baseline: r.is_baseline, basis: r.basis, services: r.services.length, gates: r.gates.map((g) => g.name).join(' | '),
    length_km: r.km.value, length_label: r.km.label,
    sail_low_d: r.sail.low, sail_median_d: r.sail.median, sail_high_d: r.sail.high, sail_label: chipFor(r.sail.label),
    queue_peak_low_d: r.queue?.low ?? '', queue_peak_median_d: r.queue?.median ?? '', queue_peak_high_d: r.queue?.high ?? '', queue_peak_label: r.queue ? chipFor(r.queue.label) : '',
    wait_at_arrival_low_d: r.queue_at_arrival?.low ?? '', wait_at_arrival_median_d: r.queue_at_arrival?.median ?? '', wait_at_arrival_high_d: r.queue_at_arrival?.high ?? '',
    wait_at_arrival_label: r.queue_at_arrival ? chipFor(r.queue_at_arrival.label) : '',
    total_low_d: r.total.low, total_median_d: r.total.median, total_high_d: r.total.high, total_label: chipFor(r.total.label),
    vs_baseline_median_d: r.vs_baseline.median,
    sources: (r.total.sources ?? []).join(' | '),
  }));
  for (const c of doc.checks) {
    rows.push({
      row_type: 'check', plan_id: doc.plan_id, check: c.id, state: c.state, flag: c.flag,
      engine_cut_pct: c.engine?.cut_pct ?? '', cited_low_pct: c.reference?.cut_pct.lo ?? '', cited_high_pct: c.reference?.cut_pct.hi ?? '',
      suez_km: c.engine?.suez_km ?? '', cape_km: c.engine?.cape_km ?? '', sources: c.sources.join(' | '),
    });
  }
  return rows;
}

/* ── the server figure (POST /exports/chart, bar) ───────────────────── */
export function serverBarSpec(doc: PlanDoc): Record<string, unknown> {
  const chip = doc.routes.some((r) => r.queue && r.queue.high > 0) ? 'SIMULATED' : 'MODELLED';
  const source = `Lane planner ${doc.plan_id}, ${doc.engine_label}, named variants`;
  const mk = (name: string, pick: (r: RouteDoc) => number) => ({
    name, source, chip, unit: 'days', points: doc.routes.map((r) => ({ x: r.label, y: pick(r) })),
  });
  return {
    type: 'bar', chip,
    title: `${doc.places.origin.name} to ${doc.places.destination.name}, transit time range`,
    subtitle: doc.disruption ? `${doc.disruption.name} ${doc.disruption.kind}, ${doc.disruption.duration_d} d` : 'No disruption',
    series: [mk('Low', (r) => r.total.low), mk('Median', (r) => r.total.median), mk('High', (r) => r.total.high)],
    axes: { y: { label: 'Transit time', unit: 'days' } },
  };
}

/** A plain-text one-liner of the plan for a window title or a status line. */
export const planTitle = (doc: PlanDoc): string => `${doc.places.origin.name} to ${doc.places.destination.name}`;
