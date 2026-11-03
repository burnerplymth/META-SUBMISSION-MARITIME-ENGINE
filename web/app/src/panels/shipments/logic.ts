/* Pure view logic of the shipments window: the scenario request, the joined
 * table rows, filters, sorting, number words and the CSV the table exports. */
import type { ScenarioBody } from './client';
import type { McBlock, ChainDisruption, EvalDoc, EvalShipment, EvalUnresolved, PlaceRow, Range3, ScenarioChoice, Shipment, ShipStatus } from './types';

export const NOT_SERVED = 'Not served';
export const LABEL_WORD = 'Modelled';
export const MIN_DURATION = 1;
export const MAX_DURATION = 3650;
export const KINDS = ['closure', 'capacity_cut', 'congestion', 'strike', 'weather', 'grounding', 'accident', 'policy', 'other'] as const;

/** The disruption the open causal chain stands on, as the exposure request needs it. */
export function chainDisruption(
  spec: { node_id: string; kind: string; capacity_loss: number; duration_d: number } | null | undefined,
  rootPlace: string | null,
): ChainDisruption | null {
  const place = spec?.node_id || rootPlace;
  if (!place) return null;
  const kind = (KINDS as readonly string[]).includes(spec?.kind ?? '') ? (spec!.kind) : spec?.kind === 'capacity' ? 'capacity_cut' : 'other';
  const dur = spec && Number.isFinite(spec.duration_d) && spec.duration_d > 0 ? spec.duration_d : null;
  const loss = spec && Number.isFinite(spec.capacity_loss) && spec.capacity_loss > 0 && spec.capacity_loss <= 1 ? spec.capacity_loss : null;
  return { place_id: place, kind, duration_d: dur, capacity_loss: loss };
}

export function clampDuration(n: number): number {
  return Math.max(MIN_DURATION, Math.min(MAX_DURATION, Math.round(Number.isFinite(n) ? n : 14)));
}

/** The `scenario` field of an evaluate request, or undefined for no disruption. */
export function scenarioBody(c: ScenarioChoice, chain: ChainDisruption | null, closureIds: ReadonlySet<string> = new Set()): ScenarioBody | undefined {
  if (c.kind === 'library') {
    const b: { scenario_id: string; duration_d: number; capacity_loss?: number } = { scenario_id: c.id, duration_d: clampDuration(c.duration) };
    if (c.loss != null && !closureIds.has(c.id)) b.capacity_loss = c.loss;
    return b;
  }
  if (c.kind === 'chain' && chain) {
    const d: { kind: string; place: string; duration_d: number; capacity_loss?: number } = { kind: chain.kind, place: chain.place_id, duration_d: clampDuration(c.duration) };
    if (chain.kind !== 'closure') { const l = c.loss ?? chain.capacity_loss; if (l != null) d.capacity_loss = l; }
    return { disruption: d };
  }
  if (c.kind === 'builder') {
    const d: { kind: string; place: string; duration_d: number; capacity_loss?: number } = { kind: c.shockKind, place: c.place, duration_d: clampDuration(c.duration) };
    if (c.shockKind !== 'closure' && c.loss != null) d.capacity_loss = c.loss;
    return { disruption: d };
  }
  return undefined;
}

/* ── number words ─────────────────────────────────────────────────── */
export const fmtD = (n: number | null | undefined): string => (n == null || !Number.isFinite(n) ? '—' : n.toFixed(1));
export function fmtRangeD(r: Range3 | null | undefined): string {
  if (!r) return '—';
  return r.low === r.high ? `${fmtD(r.median)}` : `${fmtD(r.low)} to ${fmtD(r.high)}`;
}
export function fmtUsd(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—';
  return Math.round(n).toLocaleString('en-US');
}
export function fmtUsdShort(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—';
  const a = Math.abs(n);
  if (a >= 1e9) return `${(n / 1e9).toFixed(2)} bn`;
  if (a >= 1e6) return `${(n / 1e6).toFixed(2)} m`;
  return Math.round(n).toLocaleString('en-US');
}
export const fmtPct = (x: number | null | undefined): string => (x == null || !Number.isFinite(x) ? '—' : `${Math.round(x * 100)}%`);
export function fmtQty(s: Shipment): string {
  if (s.quantity == null) return '—';
  return `${s.quantity.toLocaleString('en-US', { maximumFractionDigits: 1 })} ${s.unit ?? 'TEU'}`;
}

export const STATUS_WORD: Record<ShipStatus, string> = {
  unaffected: 'Unaffected', delayed: 'Delayed', rerouted: 'Rerouted', not_served: 'Not served', unresolved: 'Unresolved',
};
export const STATUS_CHIP: Record<ShipStatus, string> = {
  unaffected: 'INFO', delayed: 'FLAG', rerouted: 'FLAG', not_served: 'NOT SERVED', unresolved: 'NO DATA',
};
const STATUS_RANK: Record<ShipStatus, number> = { rerouted: 0, delayed: 1, unaffected: 2, not_served: 3, unresolved: 4 };

export const REASON_WORD: Record<string, string> = {
  ambiguous: 'Ambiguous', no_match: 'No match', unclear: 'Unclear',
};
export const QUALITY_WORD: Record<string, string> = {
  id: 'Exact', exact: 'Exact', code: 'Code', alias: 'Alias', prefix: 'Prefix', fuzzy: 'Close spelling',
};
export const MODE_WORD: Record<string, string> = { sea: 'Sea', air: 'Air', ground: 'Ground' };

/** Words for the refusal and down codes a request can end in. No server text is shown. */
export function failWord(code: string): string {
  switch (code) {
    case 'too_many_lanes': return 'Too many lanes';
    case 'too_many_shipments': return 'Too many shipments';
    case 'unknown_scenario': return 'Scenario not found';
    case 'unknown_place': case 'disruption_place_not_modelled': return 'Place not modelled';
    case 'rate_limited': return 'Call limit reached';
    case 'network': return 'Offline';
    case 'aborted': return 'Interrupted';
    default: return 'Request refused';
  }
}

/* ── the joined table ─────────────────────────────────────────────── */
export interface Row {
  s: Shipment; evaluated: boolean; status: ShipStatus; origin: PlaceRow | null; destination: PlaceRow | null; originQ: string | null; destQ: string | null;
  lane: string; added: Range3 | null; baseline: Range3 | null; value: number | null; affectedValue: number | null; route: string | null;
  chips: string[]; unresolved: EvalUnresolved[];
}

export function joinRows(list: readonly Shipment[], doc: EvalDoc | null): Row[] {
  const ev = new Map<string, EvalShipment>(); const res = new Map<string, EvalDoc['resolved'][number]>();
  const unres = new Map<string, EvalUnresolved[]>();
  if (doc) {
    for (const e of doc.shipments) ev.set(e.id, e);
    for (const r of doc.resolved) res.set(r.id, r);
    for (const u of doc.unresolved) { const a = unres.get(u.id) ?? []; a.push(u); unres.set(u.id, a); }
  }
  return list.map((s) => {
    const e = ev.get(s.id); const r = res.get(s.id);
    const status: ShipStatus = e?.status ?? 'unresolved';
    const value = s.value_usd ?? null;
    return {
      s, evaluated: doc !== null, status, origin: r?.origin_place ?? null, destination: r?.destination_place ?? null,
      originQ: r?.match_quality.origin ?? null, destQ: r?.match_quality.destination ?? null,
      lane: r ? `${r.origin_place.name} to ${r.destination_place.name}` : `${s.origin} to ${s.destination}`,
      added: e?.added_days ?? null, baseline: e?.baseline_days ?? null, value,
      affectedValue: e && (status === 'delayed' || status === 'rerouted') ? e.value_in_affected_usd : null,
      route: e?.route_label ?? null, chips: e?.notes_chips ?? [], unresolved: unres.get(s.id) ?? [],
    };
  });
}

export type SortKey = 'exposure' | 'id' | 'lane' | 'status' | 'added' | 'value';
export interface SortState { key: SortKey; dir: 1 | -1 }
export const DEFAULT_SORT: SortState = { key: 'exposure', dir: 1 };

/** Stable sort. "exposure" puts rerouted and delayed first, largest median added days first, then the rest. */
export function sortRows(rows: readonly Row[], sort: SortState): Row[] {
  const cmpStr = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
  const base = (a: Row, b: Row): number => {
    switch (sort.key) {
      case 'id': return cmpStr(a.s.id, b.s.id);
      case 'lane': return cmpStr(a.lane.toLowerCase(), b.lane.toLowerCase());
      case 'status': return STATUS_RANK[a.status] - STATUS_RANK[b.status];
      case 'added': return (a.added?.median ?? -1) - (b.added?.median ?? -1);
      case 'value': return (a.value ?? -1) - (b.value ?? -1);
      default: {
        const r = STATUS_RANK[a.status] - STATUS_RANK[b.status];
        return r !== 0 ? r : (b.added?.median ?? 0) - (a.added?.median ?? 0);
      }
    }
  };
  return rows.map((r, i) => ({ r, i })).sort((x, y) => { const c = base(x.r, y.r) * (sort.key === 'exposure' ? 1 : sort.dir); return c !== 0 ? c : x.i - y.i; }).map((x) => x.r);
}

export type FilterKey = 'all' | 'affected' | 'unaffected' | 'not_served' | 'unresolved';
export const FILTERS: ReadonlyArray<{ key: FilterKey; label: string }> = [
  { key: 'all', label: 'All' }, { key: 'affected', label: 'Affected' }, { key: 'unaffected', label: 'Unaffected' },
  { key: 'not_served', label: 'Not served' }, { key: 'unresolved', label: 'Unresolved' },
];
const inFilter = (s: ShipStatus, f: FilterKey): boolean =>
  f === 'all' ? true : f === 'affected' ? s === 'delayed' || s === 'rerouted' : s === f;
export function filterRows(rows: readonly Row[], f: FilterKey, q: string): Row[] {
  const t = q.trim().toLowerCase();
  return rows.filter((r) => inFilter(r.status, f) && (!t || `${r.s.id} ${r.s.origin} ${r.s.destination} ${r.lane}`.toLowerCase().includes(t)));
}
export function filterCounts(rows: readonly Row[]): Record<FilterKey, number> {
  const c: Record<FilterKey, number> = { all: rows.length, affected: 0, unaffected: 0, not_served: 0, unresolved: 0 };
  for (const r of rows) { if (r.status === 'delayed' || r.status === 'rerouted') c.affected += 1; else c[r.status] += 1; }
  return c;
}

/** The rows of the CSV the window exports: what the table shows, in its order. */
export function csvRows(rows: readonly Row[], scenarioLabel: string): Array<Record<string, unknown>> {
  return rows.map((r) => ({
    Shipment: r.s.id, 'Origin as given': r.s.origin, 'Destination as given': r.s.destination, Mode: MODE_WORD[r.s.mode] ?? r.s.mode,
    'Origin place': r.origin?.name ?? '', 'Destination place': r.destination?.name ?? '', Status: STATUS_WORD[r.status],
    'Added low [d]': r.added?.low ?? '', 'Added median [d]': r.added?.median ?? '', 'Added high [d]': r.added?.high ?? '',
    'Baseline median [d]': r.baseline?.median ?? '', 'Value [USD]': r.value ?? '', 'Value in affected shipments [USD]': r.affectedValue ?? '',
    Quantity: r.s.quantity ?? '', Unit: r.s.quantity != null ? (r.s.unit ?? 'TEU') : '', Departure: r.s.etd ?? '', Arrival: r.s.eta ?? '',
    Route: r.route ?? '', Scenario: scenarioLabel, Label: 'Modelled from named variants. Conditional on stated inputs.',
  }));
}

export function importWords(rej: { reason: string }[]): string {
  const n = rej.length;
  return n === 0 ? '' : `${n} ${n === 1 ? 'row' : 'rows'} skipped`;
}

/* ── Monte Carlo overlay ──────────────────────────────────────────── */
export const MC_ID_RE = /^mc_[A-Za-z0-9_-]{1,64}$/;

/** The disruption choice a handed-over Monte Carlo run stands for. */
export function choiceFromMcRun(h: { placeId: string; kind: string; durationD: number; capacityLoss: number }): Extract<ScenarioChoice, { kind: 'builder' }> {
  const duration = clampDuration(h.durationD);
  return { kind: 'builder', place: h.placeId, shockKind: h.kind, duration, loss: h.kind === 'closure' ? null : h.capacityLoss, name: 'Monte Carlo run', of: 1, rounded: duration !== h.durationD, via: 'montecarlo' };
}
export const STATISTIC_WORD = 'Largest of per-place P10/P50/P90';
export interface McView {
  state: 'none' | 'notserved' | 'served';
  /** false when the job is known to describe another scenario: the lane overlay is withheld */
  showLanes: boolean;
  chips: Array<{ chip: string; word: string }>;
  rows: Array<{ lane_key: string; name: string; range: string; places: string }>;
}
const whole = (n: number): string => String(Math.round(n));

/** What the window shows of the `mc` block. A mismatch hides the lane rows; null (cannot tell) shows them without a claim. */
export function mcView(mc: McBlock | null | undefined, laneName: (key: string) => string = (k) => k): McView {
  if (!mc) return { state: 'none', showLanes: false, chips: [], rows: [] };
  if (mc.state !== 'SERVED') return { state: 'notserved', showLanes: false, chips: [{ chip: 'NOT SERVED', word: 'Monte Carlo job' }], rows: [] };
  const chips: McView['chips'] = [{ chip: 'MODELLED', word: 'Monte Carlo' }];
  const differs = mc.scenario_match === false;
  if (differs) {
    const f = (mc.mismatches ?? []).filter((m) => typeof m === 'string' && m);
    chips.push({ chip: 'INFO', word: f.length ? `Job differs from scenario: ${f.join(', ')}` : 'Job differs from scenario' });
  }
  if (mc.statistic === 'max_of_place_percentiles') chips.push({ chip: 'INFO', word: STATISTIC_WORD });
  const missing = typeof mc.missing_places === 'number' && Number.isFinite(mc.missing_places) ? Math.round(mc.missing_places) : 0;
  if (missing > 0) chips.push({ chip: 'INFO', word: `${missing} ${missing === 1 ? 'place' : 'places'} not in the job's list` });
  if (mc.engine && mc.engine.same === false && mc.engine.engine_rev != null) chips.push({ chip: 'INFO', word: `Job engine rev ${mc.engine.engine_rev}` });
  const rows = differs ? [] : (mc.by_lane ?? []).map((l) => ({
    lane_key: l.lane_key, name: laneName(l.lane_key),
    range: `${fmtD(l.added_days.low)} to ${fmtD(l.added_days.high)}`,
    places: l.places_missing > 0 ? `${whole(l.places)} of ${whole(l.places_total)} places` : `${whole(l.places_total)} places`,
  }));
  return { state: 'served', showLanes: !differs, chips, rows };
}
