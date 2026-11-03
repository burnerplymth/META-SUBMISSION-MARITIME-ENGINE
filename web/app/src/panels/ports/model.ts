/* PORTS board — pure model. Everything here is hook-free and
 * DOM-free: query building
 * (filters / sort / paging / overlay), cell formatting with the
 * provenance chip, status presentation from the served PORTS-STATUS
 * rule, the row view and the detail-drawer view.
 *
 * Doctrine carried by these functions:
 *  - a cell with no value is NOT SERVED, never a zero (formatCell
 *    never prints a number for a null value);
 *  - no sum is ever computed here — every figure is shown in its own
 *    unit, per place, per goods class;
 *  - the chip word is the served one (a SOURCED value from a LIVE feed
 *    wears LIVE, a stored copy wears SNAPSHOT as served). */

import { parseCitation, type Citation } from '../../citation';
import { labelWord } from '../../copy';
import { safeExcerpt } from '../../contentGuard';
import type { StateKind, StateMarkName } from '../../shell/stateChips';

export type ChipKind = 'LIVE' | 'SNAPSHOT' | 'SOURCED' | 'MODELLED' | 'SIMULATED' | 'NOT SERVED' | 'LEDGER' | 'DOWN';

/** A served datum: value + unit + provenance. */
export interface Cell {
  value: unknown;
  unit?: string | null;
  chip: string;
  class?: string | null;
  feed_state?: string | null;
  source?: string | null;
  as_of?: string | null;
  note?: string | null;
  reason?: string | null;
  basis?: string | null;
  max_severity?: number | null;
  [k: string]: unknown;
}

export interface StatusBasis { component: string; metric: string; value: unknown; level: number }
export interface PlaceStatus {
  level: number; label: string; rule_id: string; rule_version: string;
  basis: StatusBasis[]; not_evaluated: Array<{ component: string; reason: string }>; chip?: string;
}
export interface Place {
  place_id: string; name: string; kind: string; mode: string;
  country?: string | null; region?: string | null; lat?: number | null; lon?: number | null;
  tier: string; tier_label?: string | null;
  capacity: Cell; status: PlaceStatus; cells: Record<string, Cell>;
}
export interface PortsRuleRef { id: string; version: string }
export interface PortsDoc {
  schema: string; as_of: string; run_id: string | null; sim_run_id: string | null; tick: number | null;
  sort: string; order: string; total: number; limit: number; offset: number;
  rule: PortsRuleRef & Record<string, unknown>; places: Place[];
}
export interface FeedState {
  feed: string; label?: string; serves?: string; state: string; max_age_s?: number;
  retrieved_at?: string | null; age_s?: number | null; stale?: boolean | null; records?: number | null; reason?: string | null;
}
export interface PortsSummary {
  schema: string; places: number;
  by_status: Record<string, number>; by_kind: Record<string, number>; by_tier: Record<string, number>;
  by_status_and_kind?: Record<string, Record<string, number>>;
  rule?: PortsRuleRef & Record<string, unknown>;
  feeds: FeedState[];
  events_store?: { state: string; records: number; active: number; reason: string | null };
  satwatch?: { scans: number; with_counts: number };
  as_of?: string;
}
export interface PortDetail extends Omit<Place, 'cells'> {
  schema: string; as_of: string; run_id: string | null; sim_run_id: string | null; tick: number | null;
  cells: Record<string, Cell>; series: Record<string, unknown>; rule: PortsRuleRef & Record<string, unknown>; feeds: FeedState[];
}

/* ── chips ──────────────────────────────────────────────────────────── */

/** The chip kinds a cell may wear, mapped onto the one `Status` grammar (drawn mark + word; SOURCED and LEDGER carry no mark). */
export const CHIP_STATE: Record<ChipKind, StateKind> = {
  LIVE: 'live', SNAPSHOT: 'snapshot', SOURCED: 'meta', MODELLED: 'model', SIMULATED: 'sim', 'NOT SERVED': 'notserved', LEDGER: 'meta', DOWN: 'down',
};
const CHIP_SET: ReadonlySet<string> = new Set(Object.keys(CHIP_STATE));

export const isCell = (o: unknown): o is Cell =>
  !!o && typeof o === 'object' && !Array.isArray(o) && 'chip' in (o as object) && 'value' in (o as object);

/** The chip a cell wears. An absent value is NOT SERVED whatever the
 *  cell claims; a SOURCED value out of a LIVE feed wears LIVE; any
 *  other served chip word passes through; an unknown word is NOT SERVED
 *  rather than guessed. */
export function chipKind(c: Cell | null | undefined): ChipKind {
  if (!c || c.value === null || c.value === undefined) return 'NOT SERVED';
  const word = String(c.chip ?? '').toUpperCase();
  if (word === 'NOT SERVED') return 'NOT SERVED';
  if (word === 'SOURCED' && c.feed_state === 'LIVE') return 'LIVE';
  return CHIP_SET.has(word) ? (word as ChipKind) : 'NOT SERVED';
}

/** Short as-of for a chip: a date reads MM-DD; a LIVE timestamp reads
 *  HH:MMZ; other timestamps MM-DD. Null when nothing was served. */
export function shortAsOf(asOf: string | null | undefined, kind: ChipKind): string | null {
  if (!asOf) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(asOf);
  if (!m) return null;
  if (kind === 'LIVE' && m[4]) return `${m[4]}:${m[5]}Z`;
  return `${m[2]}-${m[3]}`;
}

/** Full as-of for tooltips: ISO date or `YYYY-MM-DD HH:MMZ`. */
export function longAsOf(asOf: string | null | undefined): string | null {
  if (!asOf) return null;
  const m = /^(\d{4}-\d{2}-\d{2})(?:T(\d{2}):(\d{2}))?/.exec(asOf);
  if (!m) return null;
  return m[2] ? `${m[1]} ${m[2]}:${m[3]}Z` : m[1];
}

/* ── value formatting ───────────────────────────────────────────────── */

const nfInt = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
export function fmtNumber(v: number): string {
  if (!Number.isFinite(v)) return '';
  const a = Math.abs(v);
  if (Number.isInteger(v) || a >= 1000) return nfInt.format(v);
  if (a >= 100) return v.toFixed(1);
  if (a >= 10) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  return v.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
}

/** Unit text for a cell column: parentheticals (window / estimate
 *  qualifiers) leave the cell and stay in the tooltip. */
export function unitShort(unit: string | null | undefined): string {
  if (!unit) return '';
  const u = unit.replace(/\s*\([^)]*\)/g, '').trim();
  const map: Record<string, string> = {
    'ratio to annual baseline': 'ratio', minutes: 'min', 'active events': 'events', 'port calls/day': 'calls/day',
    'vessel transits/day': 'transits/day', 'aircraft in bbox': 'aircraft',
  };
  const out = map[u] ?? u;
  return out.length > 26 ? `${out.slice(0, 25)}…` : out;
}

/** The text a cell prints. NOT SERVED cells print nothing here (the
 *  chip carries the state); object values (chain / simulated overlay)
 *  print their own labelled fields. */
export function formatValue(c: Cell | null | undefined): string {
  if (!c || c.value === null || c.value === undefined) return '';
  const v = c.value;
  const unit = (c.unit ?? '').toLowerCase();
  if (typeof v === 'number') {
    if (unit.startsWith('share')) return `${(v * 100).toFixed(1)}%`;
    if (unit.startsWith('ratio')) return v.toFixed(2);
    return fmtNumber(v);
  }
  if (typeof v === 'string') return v;
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v === 'object') {
    const o = v as Record<string, unknown>;
    if ('on_chain' in o) {
      if (!o.on_chain) return 'Off chain';
      const d = typeof o.max_delay_d === 'number' ? `+${o.max_delay_d.toFixed(1)} d` : '';
      const h = typeof o.hits === 'number' ? ` · ${o.hits} ${o.hits === 1 ? 'hit' : 'hits'}` : '';
      return `On chain ${d}${h}`.trim();
    }
    if ('queue' in o || 'berthed' in o) {
      const f = (k: string) => (typeof o[k] === 'number' ? fmtNumber(o[k] as number) : '—');
      return `Queue ${f('queue')} · Berthed ${f('berthed')}`;
    }
  }
  return '';
}

/** Chain overlay detail: the capacity at risk in the place's OWN unit. */
export function chainDetailText(c: Cell | null | undefined): string | null {
  const v = c?.value as Record<string, unknown> | null | undefined;
  if (!v || typeof v !== 'object' || !v.on_chain) return null;
  const cap = v.capacity_at_risk;
  if (typeof cap !== 'number') return null;
  return `${fmtNumber(cap)} ${String(v.capacity_at_risk_unit ?? '')}`.trim();
}

export interface CellView {
  text: string;
  unit: string;
  chip: ChipKind;
  asOf: string | null;
  title: string;
  notServed: boolean;
  /** Raw feed state served with the cell (LIVE / SNAPSHOT / NOT SERVED). */
  feedState: string | null;
}

export function cellView(c: Cell | null | undefined, fallbackReason = 'Not served for this place'): CellView {
  const chip = chipKind(c);
  const notServed = chip === 'NOT SERVED';
  const as = notServed ? null : shortAsOf(c?.as_of, chip); // an absent value has no as-of to show
  const parts: string[] = [];
  if (c?.source) parts.push(String(c.source));
  const la = longAsOf(c?.as_of);
  if (la) parts.push(`as of ${la}`);
  if (c?.unit) parts.push(String(c.unit));
  if (notServed) parts.push(labelWord(c?.reason ?? fallbackReason));
  else if (c?.note) parts.push(String(c.note));
  return {
    text: notServed ? '' : formatValue(c), unit: notServed || typeof c?.value === 'object' ? '' : unitShort(c?.unit), chip, asOf: as,
    title: parts.join(' · '), notServed, feedState: c?.feed_state ?? null,
  };
}

/* ── status ─────────────────────────────────────────────────────────── */

/** PORTS-STATUS 1.1.0: the words describe a deviation from a baseline, never a closure or an alarm. */
export const STATUS_LABELS = ['IN RANGE', 'DEVIATION LOW', 'DEVIATION MID', 'DEVIATION HIGH', 'NO DATA'] as const;
export type StatusLabel = (typeof STATUS_LABELS)[number];
/** The drawn mark per place status (never a font glyph): solid dot, half, ring with a centre, cross, slashed ring. */
export const STATUS_MARK: Record<StatusLabel, StateMarkName> = { 'IN RANGE': 'dot', 'DEVIATION LOW': 'half', 'DEVIATION MID': 'target', 'DEVIATION HIGH': 'cross', 'NO DATA': 'slash' };

export function statusLabel(s: PlaceStatus | null | undefined): StatusLabel {
  const l = String(s?.label ?? '').toUpperCase();
  return (STATUS_LABELS as readonly string[]).includes(l) ? (l as StatusLabel) : 'NO DATA';
}
/** CSS hook per level (the stylesheets keep their existing .pt-status--normal/watch/elevated/critical selectors). */
const STATUS_KEY: Record<StatusLabel, string> = { 'IN RANGE': 'normal', 'DEVIATION LOW': 'watch', 'DEVIATION MID': 'elevated', 'DEVIATION HIGH': 'critical', 'NO DATA': 'no-data' };
export const statusKey = (l: StatusLabel): string => STATUS_KEY[l];

export function statusTitle(s: PlaceStatus): string {
  const b = s.basis.map((x) => `${x.component} ${x.metric} ${typeof x.value === 'number' ? fmtNumber(x.value) : String(x.value)} → level ${x.level}`);
  return [`${s.rule_id} v${s.rule_version}`, ...b].join(' · ');
}

/* ── query building ─────────────────────────────────────────────────── */

export const MODES = ['sea', 'air', 'ground'] as const;
export const KINDS = ['port', 'chokepoint', 'air_hub', 'ground_node'] as const;
export const TIERS = ['core', 'shell'] as const;
export const SORT_KEYS = ['name', 'id', 'status', 'capacity', 'activity', 'ratio', 'events', 'chain'] as const;
export type SortKey = (typeof SORT_KEYS)[number];
export type SortOrder = 'asc' | 'desc';
export const PAGE_SIZES = [25, 50, 100, 200] as const;
/** REGIONS-v1, the published filter table (server/ports/regions.py). */
export const REGIONS = [
  'North America', 'Latin America & Caribbean', 'Europe & Central Asia', 'Middle East & North Africa', 'Sub-Saharan Africa',
  'South Asia', 'East Asia', 'Southeast Asia', 'Oceania', 'Unclassified',
] as const;

export interface PortsFilters {
  mode: string[]; kind: string[]; tier: string[]; region: string[]; status: string[]; q: string; hasEvents: boolean;
}
export const EMPTY_FILTERS: PortsFilters = { mode: [], kind: [], tier: [], region: [], status: [], q: '', hasEvents: false };

export type Overlay =
  | { type: 'none' }
  | { type: 'chain'; runId: string }
  | { type: 'sim'; simRunId: string; tick: number };

export interface BoardState {
  filters: PortsFilters; sort: SortKey; order: SortOrder; limit: number; offset: number; overlay: Overlay;
}
export const INITIAL_BOARD: BoardState = { filters: EMPTY_FILTERS, sort: 'status', order: 'desc', limit: 50, offset: 0, overlay: { type: 'none' } };

/** GET /ports query string (no leading '?'). Defaults are sent in full
 *  so the served board is exactly the one the controls show. */
export function buildPortsQuery(b: BoardState): string {
  const p = new URLSearchParams();
  const f = b.filters;
  for (const k of ['mode', 'kind', 'tier', 'region', 'status'] as const) if (f[k].length) p.set(k, f[k].join(','));
  if (f.q.trim()) p.set('q', f.q.trim());
  if (f.hasEvents) p.set('has_events', 'true');
  p.set('sort', b.sort); p.set('order', b.order);
  p.set('limit', String(b.limit)); p.set('offset', String(b.offset));
  if (b.overlay.type === 'chain') p.set('run_id', b.overlay.runId);
  if (b.overlay.type === 'sim') { p.set('sim_run_id', b.overlay.simRunId); p.set('tick', String(b.overlay.tick)); }
  return p.toString();
}

/** Overlay query for the detail route. */
export function overlayQuery(o: Overlay): string {
  if (o.type === 'chain') return `run_id=${encodeURIComponent(o.runId)}`;
  if (o.type === 'sim') return `sim_run_id=${encodeURIComponent(o.simRunId)}&tick=${o.tick}`;
  return '';
}

export function toggleIn(list: readonly string[], v: string): string[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

/** Any filter change returns to the first page. */
export function withFilters(b: BoardState, patch: Partial<PortsFilters>): BoardState {
  return { ...b, filters: { ...b.filters, ...patch }, offset: 0 };
}

export const activeFilterCount = (f: PortsFilters): number =>
  f.mode.length + f.kind.length + f.tier.length + f.region.length + f.status.length + (f.q.trim() ? 1 : 0) + (f.hasEvents ? 1 : 0);

/** First click on a key picks its natural direction (names and ids
 *  ascending, ratio ascending so the most depressed place leads, every
 *  other figure descending); a second click flips it. */
export function nextSort(b: BoardState, key: SortKey): BoardState {
  if (b.sort === key) return { ...b, order: b.order === 'asc' ? 'desc' : 'asc', offset: 0 };
  const asc = key === 'name' || key === 'id' || key === 'ratio';
  return { ...b, sort: key, order: asc ? 'asc' : 'desc', offset: 0 };
}

/** `chain` ordering exists only with a chain overlay; without it the
 *  sort falls back to the default (the server would refuse it). */
export function sanitiseSort(b: BoardState): BoardState {
  return b.sort === 'chain' && b.overlay.type !== 'chain' ? { ...b, sort: 'status', order: 'desc' } : b;
}

export function ariaSort(b: BoardState, key: SortKey): 'ascending' | 'descending' | 'none' {
  return b.sort !== key ? 'none' : b.order === 'asc' ? 'ascending' : 'descending';
}

export interface PageInfo { from: number; to: number; total: number; hasPrev: boolean; hasNext: boolean; prevOffset: number; nextOffset: number }
export function pageInfo(total: number, limit: number, offset: number): PageInfo {
  const to = Math.min(total, offset + limit);
  return {
    from: total === 0 ? 0 : offset + 1, to, total,
    hasPrev: offset > 0, hasNext: offset + limit < total,
    prevOffset: Math.max(0, offset - limit), nextOffset: offset + limit,
  };
}

/** The overlay choices the panel can offer for the store state: a loaded
 *  chain run and/or an active simulation run. Simulated and chain
 *  overlays stay separate choices — never one merged column. */
export function overlayChoices(chainRunId: string | null, simRunId: string | null, tick: number): Array<{ id: string; label: string; overlay: Overlay }> {
  const out: Array<{ id: string; label: string; overlay: Overlay }> = [{ id: 'none', label: 'None', overlay: { type: 'none' } }];
  if (chainRunId) out.push({ id: 'chain', label: 'Chain run', overlay: { type: 'chain', runId: chainRunId } });
  if (simRunId) out.push({ id: 'sim', label: 'Simulation run', overlay: { type: 'sim', simRunId, tick } });
  return out;
}
/** The tick the overlay queries use. While a simulation plays, the tick moves
 *  every frame; refetching `/ports` on each one floods the server, so the
 *  overlay holds the last paused tick until playback stops. */
export function overlayTickFor(playing: boolean, tick: number, held: number): number {
  return playing ? held : tick;
}
/** Keep a chosen overlay only while its run is still loaded. */
export function reconcileOverlay(o: Overlay, chainRunId: string | null, simRunId: string | null, tick: number): Overlay {
  if (o.type === 'chain') return chainRunId ? { type: 'chain', runId: chainRunId } : { type: 'none' };
  if (o.type === 'sim') return simRunId ? { type: 'sim', simRunId, tick } : { type: 'none' };
  return o;
}

/* ── row view ───────────────────────────────────────────────────────── */

/** Display words. The keys (`port`, `core`, ...) are the served values; the words are what a person reads. */
export const KIND_WORD: Record<string, string> = { port: 'Port', chokepoint: 'Chokepoint', air_hub: 'Air hub', ground_node: 'Border crossing' };
export const TIER_WORD: Record<string, string> = { core: 'Core', shell: 'Estimated' };
export const TIER_TITLE: Record<string, string> = { core: 'Core place, sourced capacity', shell: 'Shell place, estimated capacity' };

/** The kind's primary live signal cell (cells that cannot apply to a
 *  kind are not served for it). */
export function signalKey(kind: string): string {
  return kind === 'air_hub' ? 'hub_aircraft' : kind === 'ground_node' ? 'border_wait' : 'activity';
}
export const SIGNAL_LABEL: Record<string, string> = {
  port: 'Port calls', chokepoint: 'Transits', air_hub: 'Aircraft', ground_node: 'Wait',
};

export interface RowView {
  id: string; name: string; country: string; kind: string; mode: string; kindWord: string;
  tier: string; tierWord: string; tierTitle: string; region: string;
  status: { label: StatusLabel; mark: StateMarkName; key: string; title: string; ruleId: string; ruleVersion: string };
  capacity: CellView; signal: CellView; signalLabel: string; ratio: CellView; events: CellView;
  overlay: CellView | null; overlayDetail: string | null;
}

export function rowView(p: Place, overlay: Overlay['type'] = 'none'): RowView {
  const label = statusLabel(p.status);
  const cells = p.cells ?? {};
  const ov = overlay === 'chain' ? cells.chain_exposure : overlay === 'sim' ? cells.simulated : undefined;
  return {
    id: p.place_id, name: p.name, country: p.country ?? '', kind: p.kind, mode: p.mode,
    kindWord: KIND_WORD[p.kind] ?? labelWord(p.kind), tier: p.tier, tierWord: TIER_WORD[p.tier] ?? labelWord(p.tier),
    tierTitle: TIER_TITLE[p.tier] ?? labelWord(p.tier_label ?? ''), region: p.region ?? '',
    status: { label, mark: STATUS_MARK[label], key: statusKey(label), title: statusTitle(p.status), ruleId: p.status.rule_id, ruleVersion: p.status.rule_version },
    capacity: cellView(p.capacity),
    signal: cellView(cells[signalKey(p.kind)], 'Not applicable to this kind of place'),
    signalLabel: SIGNAL_LABEL[p.kind] ?? 'Signal',
    ratio: cellView(cells.activity_ratio, 'Evaluated for ports only'),
    events: cellView(cells.events),
    overlay: overlay === 'none' ? null : cellView(ov, 'Not in the loaded run'),
    overlayDetail: overlay === 'chain' ? chainDetailText(ov) : null,
  };
}

/** Summary strip: status counts in rule order (served counts only — a
 *  status the summary does not carry is NOT SERVED, not zero). */
export function summaryCounts(s: PortsSummary | null): Array<{ label: StatusLabel; count: number | null }> {
  return STATUS_LABELS.map((label) => ({ label, count: s && typeof s.by_status?.[label] === 'number' ? s.by_status[label] : null }));
}

/** Feed chip state for the strip: only a LIVE/SNAPSHOT/NOT SERVED word
 *  from the served feed object; anything else reads DOWN. */
export function feedChip(f: FeedState): { kind: ChipKind; label: string; title: string } {
  const st = String(f.state ?? '').toUpperCase();
  const kind: ChipKind = st === 'LIVE' ? 'LIVE' : st === 'SNAPSHOT' ? 'SNAPSHOT' : st === 'NOT SERVED' ? 'NOT SERVED' : 'DOWN';
  const parts = [f.serves, f.retrieved_at ? `retrieved ${longAsOf(f.retrieved_at) ?? f.retrieved_at}` : null,
    typeof f.records === 'number' ? `${f.records} records` : null, f.reason ? labelWord(f.reason) : null].filter(Boolean);
  return { kind, label: feedName(String(f.feed ?? f.label ?? '')), title: parts.join(' · ') };
}

/** Feed names as a person reads them. */
const FEED_NAME: Record<string, string> = { portwatch: 'PortWatch', opensky: 'OpenSky', aisstream: 'AISStream', gdacs: 'GDACS', noaa: 'NOAA', cbp: 'CBP', eia: 'EIA', comtrade: 'Comtrade' };
export const feedName = (raw: string): string => FEED_NAME[raw.toLowerCase()] ?? labelWord(raw.replace(/_/g, ' '));

/* ── detail view ────────────────────────────────────────────────────── */

export interface DetailRow { label: string; cell: CellView; extra?: string }
export interface DetailGroup { title: string; rows: DetailRow[] }
export interface DetailSection { id: string; title: string; groups: DetailGroup[]; meta?: string }
export interface HiddenSection { id: string; title: string; reason: string }

const humanise = (k: string): string => labelWord(k.replace(/_/g, ' '));

/** Every cell nested anywhere under `o`, in document order, labelled
 *  by its key path (array indices become 1-based ordinals). */
export function collectCells(o: unknown, path: string[] = []): Array<{ path: string[]; cell: Cell }> {
  if (isCell(o)) return [{ path, cell: o }];
  const out: Array<{ path: string[]; cell: Cell }> = [];
  if (Array.isArray(o)) o.forEach((x, i) => out.push(...collectCells(x, [...path, String(i + 1)])));
  else if (o && typeof o === 'object') for (const [k, v] of Object.entries(o as Record<string, unknown>)) out.push(...collectCells(v, [...path, k]));
  return out;
}

/** A section is hidden when it holds cells and every one is NOT SERVED
 *  with a feed that is not up (NOT SERVED / DOWN / unstated). A single
 *  NOT SERVED cell beside served ones stays visible as such. */
export function feedDown(cells: Cell[]): boolean {
  return cells.length > 0 && cells.every((c) => chipKind(c) === 'NOT SERVED' && (!c.feed_state || c.feed_state === 'NOT SERVED' || c.feed_state === 'DOWN'));
}

function groupsFromCells(cells: Array<{ path: string[]; cell: Cell }>, fallbackLabel: string): DetailGroup[] {
  const by = new Map<string, DetailRow[]>();
  for (const { path, cell } of cells) {
    const title = path.length > 1 ? humanise(path.slice(0, -1).join(' · ')) : '';
    const rows = by.get(title) ?? [];
    rows.push({ label: path.length ? humanise(path[path.length - 1]) : fallbackLabel, cell: cellView(cell) });
    by.set(title, rows);
  }
  return [...by.entries()].map(([title, rows]) => ({ title, rows }));
}

const SECTION_TITLES: Array<[string, string]> = [
  ['portwatch', 'PortWatch'], ['ais_samples', 'AIS samples'], ['satwatch', 'Satellite desk'], ['border_crossings', 'Border crossings'],
  ['events', 'Active events'], ['portwatch_disruption_history', 'Disruption history'], ['goods_composition', 'Goods composition'],
  ['legs', 'Nearby legs'], ['money_exposure', 'Money exposure'], ['chain_events', 'Chain events'], ['simulated_series', 'Simulated series'],
];

export interface DetailEvent {
  id: string; headline: string; severity: number | null; kind: string; source: string; citation: Citation | null; startedAt: string | null; expiresAt: string | null; cell: CellView;
}
export interface DetailLeg {
  otherId: string; otherName: string; direction: string; tier: string; mode: string; transitD: number | null; basis: string; share: CellView;
}
export interface DetailMoney {
  ticker: string; company: string; kind: string; excerpt: string; filing: CellView; price: CellView;
}
export interface DetailView {
  id: string; name: string; kind: string; mode: string; tierWord: string; tierTitle: string; country: string; region: string;
  status: RowView['status']; basis: StatusBasis[]; notEvaluated: Array<{ component: string; reason: string }>;
  capacity: CellView; cells: DetailRow[];
  window: string | null;
  sections: DetailSection[];
  events: DetailEvent[]; legs: DetailLeg[]; money: DetailMoney[];
  hidden: HiddenSection[];
  overlayLabel: string | null;
  /** The served run id, tooltip only. */
  overlayId: string | null;
}

export function windowText(w: unknown): string | null {
  const o = w as Record<string, unknown> | null;
  if (!o || typeof o !== 'object' || !o.start || !o.end) return null;
  const n = typeof o.days_present === 'number' && typeof o.window_days === 'number' ? ` · ${o.days_present}/${o.window_days} d` : '';
  return `${o.start} → ${o.end}${n}`;
}

export function buildDetail(d: PortDetail): DetailView {
  const base = rowView({ ...d, cells: d.cells } as Place, d.run_id ? 'chain' : d.sim_run_id ? 'sim' : 'none');
  const series = (d.series ?? {}) as Record<string, unknown>;
  const sections: DetailSection[] = [];
  const hidden: HiddenSection[] = [];
  let win: string | null = null;
  const titleOf = (id: string) => SECTION_TITLES.find(([k]) => k === id)?.[1] ?? humanise(id);

  const events: DetailEvent[] = [];
  const legs: DetailLeg[] = [];
  const money: DetailMoney[] = [];

  for (const [id, raw] of Object.entries(series)) {
    const title = titleOf(id);
    if (id === 'events') {
      const act = ((raw as { active?: unknown[] })?.active ?? []) as Array<Record<string, unknown>>;
      for (const e of act) {
        events.push({
          id: String(e.id ?? ''), headline: String(e.headline ?? e.title ?? ''), severity: typeof e.severity === 'number' ? e.severity : null,
          kind: String(e.kind ?? ''), source: String(e.source ?? ''), citation: parseCitation(e.citation) ?? null,
          startedAt: (e.started_at as string) ?? null, expiresAt: (e.expires_at as string) ?? null, cell: cellView(e.provenance as Cell),
        });
      }
      continue;
    }
    if (id === 'legs') {
      for (const l of (((raw as { nearby?: unknown[] })?.nearby ?? []) as Array<Record<string, unknown>>)) {
        legs.push({
          otherId: String(l.other_place_id ?? ''), otherName: String(l.other_name ?? l.other_place_id ?? ''), direction: String(l.direction ?? ''),
          tier: String(l.other_tier ?? ''), mode: String(l.mode ?? ''), transitD: typeof l.base_transit_d === 'number' ? l.base_transit_d : null,
          basis: String(l.basis ?? ''), share: cellView(l.capacity_share as Cell),
        });
      }
      continue;
    }
    if (id === 'money_exposure') {
      for (const m of (Array.isArray(raw) ? raw : []) as Array<Record<string, unknown>>) {
        money.push({
          ticker: String(m.ticker ?? ''), company: String(m.company ?? ''), kind: String(m.exposure_kind ?? ''),
          excerpt: safeExcerpt(String(m.filing_excerpt_verbatim ?? '')) ?? '', filing: cellView(m.filing as Cell), price: cellView(m.price as Cell, 'Unpriced'),
        });
      }
      continue;
    }
    if (id === 'border_crossings') {
      const groups: DetailGroup[] = [];
      for (const c of (Array.isArray(raw) ? raw : []) as Array<Record<string, unknown>>) {
        const rows: DetailRow[] = [];
        for (const [k, v] of Object.entries(c)) if (isCell(v)) rows.push({ label: humanise(k), cell: cellView(v) });
        groups.push({ title: `${String(c.crossing ?? '')} · ${labelWord(String(c.lanes ?? ''))}`.trim(), rows });
      }
      const cells = collectCells(raw).map((x) => x.cell);
      if (feedDown(cells)) hidden.push({ id, title, reason: String(cells[0]?.reason ?? 'feed not served') });
      else if (groups.length) sections.push({ id, title, groups });
      continue;
    }
    if (id === 'chain_events') {
      const o = raw as { run_id?: string; events?: unknown[]; provenance?: Cell };
      if (o?.provenance) sections.push({ id, title, meta: undefined, groups: [{ title: '', rows: [{ label: 'Chain events at this place', cell: cellView(o.provenance) }] }] });
      continue;
    }
    if (id === 'simulated_series') {
      const o = raw as { provenance?: Cell; sim_run_id?: string };
      if (isCell(raw) && chipKind(raw) === 'NOT SERVED') hidden.push({ id, title, reason: String(raw.reason ?? 'not in the run') });
      else if (o?.provenance) sections.push({ id, title, meta: undefined, groups: [{ title: '', rows: [{ label: 'Ticks in stored series', cell: cellView(o.provenance) }] }] });
      continue;
    }
    /* generic: portwatch, ais, satwatch, history, goods composition */
    const rec = raw && typeof raw === 'object' && !Array.isArray(raw) ? (raw as Record<string, unknown>) : null;
    if (rec && 'window' in rec) win = win ?? windowText(rec.window);
    const found = collectCells(raw);
    if (found.length === 0) continue;
    if (feedDown(found.map((x) => x.cell))) { hidden.push({ id, title, reason: String(found[0].cell.reason ?? 'feed not served') }); continue; }
    sections.push({ id, title, groups: groupsFromCells(found, title) });
  }

  const cellRows: DetailRow[] = Object.entries(d.cells ?? {}).filter(([k]) => k !== 'chain_exposure' && k !== 'simulated')
    .map(([k, c]) => ({ label: humanise(k), cell: cellView(c) }));
  for (const k of ['chain_exposure', 'simulated'] as const) {
    const c = d.cells?.[k];
    if (c) cellRows.push({ label: humanise(k), cell: cellView(c, 'Not in the loaded run'), extra: k === 'chain_exposure' ? chainDetailText(c) ?? undefined : undefined });
  }

  return {
    id: d.place_id, name: d.name, kind: base.kindWord, mode: d.mode, tierWord: base.tierWord, tierTitle: base.tierTitle,
    country: d.country ?? '', region: d.region ?? '', status: base.status, basis: d.status.basis ?? [], notEvaluated: d.status.not_evaluated ?? [],
    capacity: base.capacity, cells: cellRows, window: win, sections, events, legs, money, hidden,
    overlayLabel: d.run_id ? 'Chain run' : d.sim_run_id ? `Simulation run · Tick ${d.tick ?? '—'}` : null,
    overlayId: d.run_id ?? d.sim_run_id ?? null,
  };
}

/* ── export helper (panel menu) ─────────────────────────────────────── */

/** Flat CSV rows for the visible page: value and chip travel together;
 *  a NOT SERVED cell exports an empty value and the NOT SERVED word. */
export function exportRows(places: Place[]): Array<Record<string, unknown>> {
  return places.map((p) => {
    const r = rowView(p);
    return {
      place_id: r.id, name: r.name, kind: p.kind, mode: p.mode, tier: p.tier, region: r.region, status: r.status.label,
      status_rule: `${r.status.ruleId} v${r.status.ruleVersion}`,
      capacity: p.capacity.value ?? '', capacity_unit: p.capacity.unit ?? '', capacity_chip: chipKind(p.capacity),
      signal: r.signal.notServed ? '' : r.signal.text, signal_unit: r.signal.unit, signal_chip: r.signal.chip, signal_as_of: r.signal.asOf ?? '',
    };
  });
}
