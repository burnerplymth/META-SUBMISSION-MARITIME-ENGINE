/* Dataset rows -> chart spec. Defaults per dataset, the unit split
 * (a y column whose unit varies per row is never drawn on one axis:
 * it becomes small multiples, one per unit), provenance of the plotted
 * column, and the doctrine guards (no goods-class stacking, no totals,
 * no aggregation: a row is a row). */
import type { BarCategory, BarSeries, ChartSpec, FacetSpec, LineSpec, Provenance, SeriesIn } from '../../charts/types';
import { columnLabel } from '../../charts/format';
import { unitOf, type Cell, type Column, type Dataset } from './columns';

export type ViewType = 'line' | 'bar' | 'scatter' | 'cascade';
export interface ChartView {
  type: ViewType;
  x: number | null; y: number | null; series: number | null;
  /** user split into small multiples by this column */
  split: number | null;
  stacked: boolean;
  /** category cap for bars */
  top: number;
}

export const TOP_CHOICES: readonly number[] = [10, 25, 40, 100];

const col = (ds: Dataset, name: string): number | null => ds.columns.find((c) => c.name === name)?.index ?? null;
const has = (ds: Dataset, ...names: string[]) => names.every((n) => col(ds, n) != null);

/** A column that names a goods class: never stacked, never summed. */
export const isGoodsClassColumn = (c: Column | undefined): boolean => !!c && /goods|commodity|hs_?code|cargo_class/i.test(c.name);

export const numericColumns = (ds: Dataset): Column[] => ds.columns.filter((c) => c.type === 'number');
export const labelColumns = (ds: Dataset): Column[] => ds.columns.filter((c) => c.type === 'text' || c.type === 'category');
export const axisColumns = (ds: Dataset): Column[] => ds.columns.filter((c) => c.type === 'number' || c.type === 'time');

/** Is anything plottable? */
export const chartable = (ds: Dataset): boolean => numericColumns(ds).some((c) => !/(^|_)(seq|step|tick)$/.test(c.name) || numericColumns(ds).length > 1);

export function isChainEvents(ds: Dataset): boolean {
  return has(ds, 'seq', 'parent_seq', 'at', 'place', 'channel', 'delay_d', 'magnitude');
}

const firstNumeric = (ds: Dataset, prefer: readonly string[]): number | null => {
  for (const p of prefer) { const i = col(ds, p); if (i != null && ds.columns[i].type === 'number') return i; }
  const c = numericColumns(ds).find((x) => !x.aux && !/(^|_)(seq|step|tick|events|count)$/.test(x.name)) ?? numericColumns(ds).find((x) => !x.aux);
  return c?.index ?? null;
};

/** Sensible first chart for a dataset. */
export function defaultView(ds: Dataset): ChartView {
  const base: ChartView = { type: 'bar', x: null, y: null, series: null, split: null, stacked: false, top: 25 };
  if (isChainEvents(ds)) return { ...base, type: 'cascade', x: col(ds, 'at'), y: col(ds, 'delay_d'), series: col(ds, 'channel') };
  switch (ds.id) {
    case 'legs': return { ...base, type: 'bar', x: col(ds, 'to_place'), y: col(ds, 'delay_d'), series: col(ds, 'channel') };
    case 'impact_per_place': return { ...base, type: 'bar', x: col(ds, 'place'), y: col(ds, 'capacity_at_risk') ?? col(ds, 'capacity') };
    case 'ports_board': return { ...base, type: 'bar', x: col(ds, 'place'), y: col(ds, 'activity_ratio') ?? col(ds, 'capacity'), top: 25 };
    case 'sim_series': return { ...base, type: 'line', x: col(ds, 'sim_time'), y: col(ds, 'queue'), series: col(ds, 'place') };
    case 'scorecards': return { ...base, type: 'bar', x: col(ds, 'title'), y: col(ds, 'matched_places') };
    case 'ledger': return { ...base, type: 'bar', x: col(ds, 'entry_id'), y: col(ds, 'locked_steps') };
    case 'health_board': return { ...base, type: 'bar', x: col(ds, 'label') ?? col(ds, 'feed'), y: col(ds, 'age_s') };
    default: break;
  }
  const time = ds.columns.find((c) => c.type === 'time');
  const y = firstNumeric(ds, []);
  if (time) return { ...base, type: 'line', x: time.index, y, series: labelColumns(ds).find((c) => c.type === 'category' && !c.aux)?.index ?? null };
  const label = labelColumns(ds).find((c) => !c.aux && !/(^|_)id$/.test(c.name)) ?? labelColumns(ds)[0];
  return { ...base, type: 'bar', x: label?.index ?? null, y };
}

/** Only the types the dataset can draw. */
export function viewTypes(ds: Dataset): ViewType[] {
  const out: ViewType[] = [];
  if (isChainEvents(ds)) out.push('cascade');
  if (numericColumns(ds).length) {
    out.push('bar');
    if (axisColumns(ds).length >= 1) out.push('line', 'scatter');
  }
  return out;
}

/** Sanitise a view against the dataset: indexes in range, types allowed. */
export function normaliseView(ds: Dataset, v: ChartView): ChartView {
  const ok = (i: number | null) => (i != null && ds.columns[i] ? i : null);
  const types = viewTypes(ds);
  const type = types.includes(v.type) ? v.type : (types[0] ?? 'bar');
  const series = ok(v.series);
  const stacked = v.stacked && type === 'bar' && series != null && !isGoodsClassColumn(ds.columns[series]);
  return { type, x: ok(v.x), y: ok(v.y), series, split: ok(v.split), stacked, top: TOP_CHOICES.includes(v.top) ? v.top : 25 };
}

/* ── provenance of what is plotted ─────────────────────────────────── */
export interface ProvIn { datasetProvenance: string | null; runId: string | null; asOf: string | null; source?: string | null; scope?: string | null }
const ABSENT = new Set(['', 'NOT SERVED', 'NOT APPLICABLE']);

/** The chip of the plotted column: its sibling chip column when there
 *  is one (several chips join with '+'), else the row `provenance`
 *  column, else the dataset's declared provenance. */
export function chipForColumn(ds: Dataset, yCol: Column | null, idx: readonly number[], fallback: string | null): string {
  const chips = new Set<string>();
  const pc = yCol?.chipOf ?? col(ds, 'provenance');
  if (pc != null) for (const i of idx) { const v = ds.rows[i][pc]; if (typeof v === 'string' && !ABSENT.has(v)) chips.add(v); }
  if (chips.size) return [...chips].sort().join('+');
  if (yCol && idx.every((i) => ds.rows[i][yCol.index] == null)) return 'NOT SERVED';
  if (fallback && !/^mixed/i.test(fallback)) return fallback.toUpperCase();
  return pc != null ? 'NOT SERVED' : 'MIXED';
}

export function provenanceFor(ds: Dataset, yCol: Column | null, idx: readonly number[], p: ProvIn, extra?: string | null): Provenance {
  const scope = [p.scope, extra].filter(Boolean).join(' · ') || null;
  return { chip: chipForColumn(ds, yCol, idx, p.datasetProvenance), source: p.source ?? null, asOf: p.asOf, runId: p.runId, scope };
}

/* ── building ──────────────────────────────────────────────────────── */
const str = (c: Cell): string => (c == null ? '' : String(c));
const cap = <T,>(a: readonly T[], n: number): T[] => a.slice(0, n);

interface Built { spec: ChartSpec | null; reason: string | null }

function seriesKeys(ds: Dataset, idx: readonly number[], c: number | null): string[] {
  if (c == null) return [];
  const seen: string[] = []; const set = new Set<string>();
  for (const i of idx) { const k = str(ds.rows[i][c]); if (k && !set.has(k)) { set.add(k); seen.push(k); } }
  return seen;
}

/** Split row indexes into facets: by the user's split column and, when
 *  the y column's unit varies per row, by unit. */
export function facetRows(ds: Dataset, idx: readonly number[], y: Column | null, split: number | null): Array<{ key: string; label: string; unit: string | null; idx: number[] }> {
  const groups = new Map<string, { label: string; unit: string | null; idx: number[] }>();
  for (const i of idx) {
    const row = ds.rows[i];
    const unit = y ? unitOf(ds, y, row) : null;
    const sp = split != null ? str(row[split]) : '';
    const key = `${sp}\u0000${y && y.unitFrom != null ? (unit ?? '') : ''}`;
    let g = groups.get(key);
    if (!g) {
      const parts = [split != null ? sp || 'NOT SERVED' : null, y && y.unitFrom != null ? (unit ?? 'NO UNIT') : null].filter(Boolean);
      g = { label: parts.join(' · '), unit: y && y.unitFrom != null ? unit : null, idx: [] };
      groups.set(key, g);
    }
    g.idx.push(i);
  }
  return [...groups.entries()].map(([key, g]) => ({ key, ...g }));
}

function barParts(ds: Dataset, idx: readonly number[], v: ChartView, xCol: Column, yCol: Column): { categories: BarCategory[]; series: BarSeries[]; colorBy: boolean } {
  const placeCol = ds.columns.find((c) => c.name === 'place_id' || c.name === 'to_place_id');
  const sCol = v.series;
  const rowsPerX = new Map<string, number>();
  for (const i of idx) rowsPerX.set(str(ds.rows[i][xCol.index]), (rowsPerX.get(str(ds.rows[i][xCol.index])) ?? 0) + 1);
  const keyCount = new Map<string, number>();
  if (sCol != null) for (const i of idx) { const k = `${str(ds.rows[i][xCol.index])}\u0000${str(ds.rows[i][sCol])}`; keyCount.set(k, (keyCount.get(k) ?? 0) + 1); }
  // pivot only when x repeats AND no (x, series) pair repeats: a pivot cell then holds exactly one row
  const repeated = [...rowsPerX.values()].some((n) => n > 1) && [...keyCount.values()].every((n) => n === 1);
  const placeOf = (i: number) => { const p = placeCol ? ds.rows[i][placeCol.index] : null; return typeof p === 'string' ? p : undefined; };
  if (sCol != null && repeated) {
    // pivot: distinct x down the side, distinct series across; no aggregation
    const cats: BarCategory[] = []; const catIdx = new Map<string, number>();
    const keys = seriesKeys(ds, idx, sCol);
    const vals = new Map<string, Array<number | null>>(keys.map((k) => [k, []] as const));
    for (const i of idx) {
      const x = str(ds.rows[i][xCol.index]);
      let ci = catIdx.get(x);
      if (ci === undefined) { ci = cats.length; catIdx.set(x, ci); cats.push({ key: x, label: x, placeId: placeOf(i) }); vals.forEach((arr) => arr.push(Number.NaN)); }
      const sk = str(ds.rows[i][sCol]);
      const arr = vals.get(sk);
      if (!arr) continue;
      arr[ci] = ds.rows[i][yCol.index] as number | null;
    }
    return { categories: cats, series: keys.map((k) => ({ key: k, label: k, values: vals.get(k) ?? [] })), colorBy: true };
  }
  // one bar per row; the series column only colours it
  const cats: BarCategory[] = idx.map((i, n) => ({ key: `${n}`, label: str(ds.rows[i][xCol.index]) || 'NOT SERVED', placeId: placeOf(i) }));
  if (sCol != null) {
    const keys = seriesKeys(ds, idx, sCol);
    return {
      categories: cats, colorBy: true,
      series: keys.map((k) => ({ key: k, label: k, values: idx.map((i) => (str(ds.rows[i][sCol]) === k ? (ds.rows[i][yCol.index] as number | null) : Number.NaN)) })),
    };
  }
  return { categories: cats, series: [{ key: 'v', label: yCol.name, values: idx.map((i) => ds.rows[i][yCol.index] as number | null) }], colorBy: false };
}

function singleSpec(ds: Dataset, idx: readonly number[], v: ChartView, id: string, title: string, unit: string | null, prov: Provenance): ChartSpec | null {
  const yCol = v.y != null ? ds.columns[v.y] : null;
  const xCol = v.x != null ? ds.columns[v.x] : null;
  if (!yCol || !xCol) return null;
  const common = { id, title, provenance: prov, xLabel: columnLabel(xCol.name), xUnit: xCol.unit && xCol.unit !== 'UTC' ? xCol.unit : null, yLabel: columnLabel(yCol.name), yUnit: unit ?? yCol.unit };
  if (v.type === 'bar') {
    // a cap keeps the largest served values; unserved rows are counted in the caption, never drawn as zero
    const served = idx.filter((i) => ds.rows[i][yCol.index] != null);
    const chosen = v.series == null && served.length > v.top
      ? [...served].sort((x, y) => (ds.rows[y][yCol.index] as number) - (ds.rows[x][yCol.index] as number)).slice(0, v.top)
      : cap(idx, v.top);
    const p = barParts(ds, chosen, v, xCol, yCol);
    const stacked = v.stacked && p.series.length > 1;
    return { type: 'bar', ...common, categories: p.categories, series: p.series, mode: stacked ? 'stacked' : 'grouped', colorBy: p.colorBy, horizontal: true, scope: stacked ? (unit ?? yCol.unit ?? columnLabel(yCol.name)) : null };
  }
  const sk = seriesKeys(ds, idx, v.series);
  const keys = sk.length ? sk : ['v'];
  const mk = (key: string): SeriesIn => {
    const rows = idx.filter((i) => key === 'v' || str(ds.rows[i][v.series as number]) === key);
    const pts = rows.filter((i) => ds.rows[i][xCol.index] != null).map((i) => ({
      x: ds.rows[i][xCol.index] as number, y: ds.rows[i][yCol.index] as number | null,
      label: str(ds.rows[i][ds.columns.find((c) => c.name === 'place')?.index ?? -1]) || undefined,
      placeId: (() => { const pc = ds.columns.find((c) => c.name === 'place_id'); const pv = pc ? ds.rows[i][pc.index] : null; return typeof pv === 'string' ? pv : undefined; })(),
    })).sort((a, b) => a.x - b.x);
    return { key, label: key === 'v' ? yCol.name : key, points: pts };
  };
  if (xCol.type !== 'time' && xCol.type !== 'number') return null;
  const xKind = xCol.type === 'time' ? 'time' : 'linear';
  if (v.type === 'line') return { type: 'line', ...common, xKind, series: keys.map(mk), step: ds.id === 'sim_series' } as LineSpec;
  return { type: 'scatter', ...common, xKind, series: keys.map(mk) };
}

export interface BuildIn { ds: Dataset; idx: readonly number[]; view: ChartView; prov: ProvIn; titleBase?: string }

/** The spec for the current rows and view, or a reason there is none. */
export function buildChartSpec(b: BuildIn): Built {
  const { ds, idx } = b;
  const v = normaliseView(ds, b.view);
  const title = b.titleBase ?? ds.id.replace(/_/g, ' ');
  if (idx.length === 0) return { spec: null, reason: 'NO ROWS' };
  if (v.type === 'cascade') {
    const g = (n: string) => col(ds, n) as number;
    if (!isChainEvents(ds)) return { spec: null, reason: 'NOT A CHAIN' };
    const events = idx.map((i) => {
      const r = ds.rows[i];
      return {
        seq: r[g('seq')] as number, parentSeq: r[g('parent_seq')] as number | null, at: r[g('at')] as number,
        place: str(r[g('place')]), placeId: str(r[col(ds, 'place_id') ?? -1]) || undefined, channel: str(r[g('channel')]),
        mechanism: col(ds, 'mechanism') != null ? str(r[col(ds, 'mechanism') as number]) || null : null,
        delayD: r[g('delay_d')] as number | null, magnitude: r[g('magnitude')] as number | null,
      };
    }).filter((e) => e.at != null && e.seq != null);
    return { spec: { type: 'cascade', id: `${ds.id}-cascade`, title: `${title} · causal chain`, yLabel: 'CAUSAL CHAIN', provenance: provenanceFor(ds, null, idx, b.prov), events }, reason: null };
  }
  const yCol = v.y != null ? ds.columns[v.y] : null;
  const xCol = v.x != null ? ds.columns[v.x] : null;
  if (!yCol || yCol.type !== 'number') return { spec: null, reason: 'PICK A NUMERIC Y' };
  if (!xCol) return { spec: null, reason: 'PICK X' };
  if ((v.type === 'line' || v.type === 'scatter') && xCol.type !== 'time' && xCol.type !== 'number') return { spec: null, reason: 'X NEEDS NUMBER OR TIME' };
  const facets = facetRows(ds, idx, yCol, v.split);
  const notServed = idx.filter((i) => ds.rows[i][yCol.index] == null).length;
  const extra = [v.type === 'bar' && idx.length > v.top ? `${v.top} OF ${idx.length} ROWS` : null, notServed ? `${notServed} NOT SERVED` : null].filter(Boolean).join(' · ') || null;
  const idBase = `${ds.id}-${v.type}-${yCol.name}`;
  if (facets.length <= 1) {
    const f = facets[0];
    const unit = f?.unit ?? null;
    const prov = provenanceFor(ds, yCol, idx, b.prov, extra);
    const spec = singleSpec(ds, idx, v, idBase, `${title} · ${columnLabel(yCol.name).toLowerCase()}`, unit, prov);
    return spec ? { spec, reason: null } : { spec: null, reason: 'NOTHING TO PLOT' };
  }
  const fs: FacetSpec[] = [];
  for (const f of facets) {
    const sp = singleSpec(ds, f.idx, v, `${idBase}-${fs.length}`, f.label, f.unit, provenanceFor(ds, yCol, f.idx, b.prov, extra));
    if (sp && sp.type !== 'cascade' && sp.type !== 'spark' && sp.type !== 'multiples') fs.push({ key: f.key, label: f.label, spec: sp });
  }
  if (!fs.length) return { spec: null, reason: 'NOTHING TO PLOT' };
  const unitSplit = yCol.unitFrom != null;
  return {
    spec: {
      type: 'multiples', id: idBase, title: `${title} · ${columnLabel(yCol.name).toLowerCase()}`,
      subtitle: unitSplit ? `SPLIT BY ${columnLabel(ds.columns[yCol.unitFrom as number].name)}${v.split != null ? ` + ${columnLabel(ds.columns[v.split].name)}` : ''}` : `SPLIT BY ${columnLabel(ds.columns[v.split as number].name)}`,
      provenance: provenanceFor(ds, yCol, idx, b.prov, extra), facets: fs,
      minCellWidth: v.type === 'bar' ? 300 : 260, cellHeight: v.type === 'bar' ? undefined : 150,
    },
    reason: null,
  };
}
