/* Viewer state <-> a URL query value (?dv=...). A chart or grid view
 * reduces to: dataset id, run, tab, chart type and the x / y / series /
 * split column NAMES, filters and sort. Everything parsed from a URL is
 * untrusted: unknown keys are dropped, ids and names are pattern
 * checked, sizes are capped. */
import type { Dataset } from './columns';
import { normaliseView, TOP_CHOICES, type ChartView, type ViewType } from './chartSpec';
import type { SortKey } from './grid';

export type ViewerTab = 'grid' | 'chart' | 'map';
export interface ViewerLink {
  d: string; run?: string | null; sim?: string | null; tab?: ViewerTab; type?: ViewType;
  x?: string; y?: string; s?: string; p?: string; k?: boolean; n?: number;
  f?: Record<string, string>; o?: Array<[string, 'asc' | 'desc']>;
}

const ID = /^[A-Za-z0-9_.:-]{1,128}$/;
const NAME = /^[A-Za-z0-9_.-]{1,64}$/;
const TYPES: readonly ViewType[] = ['line', 'bar', 'scatter', 'cascade'];
const TABS: readonly ViewerTab[] = ['grid', 'chart', 'map'];
export const MAX_LINK = 1800;

export function encodeViewerLink(l: ViewerLink): string {
  const o: Record<string, unknown> = { d: l.d };
  for (const k of ['run', 'sim', 'tab', 'type', 'x', 'y', 's', 'p', 'n'] as const) if (l[k] != null && l[k] !== '') o[k] = l[k];
  if (l.k) o.k = 1;
  if (l.f && Object.keys(l.f).length) o.f = l.f;
  if (l.o && l.o.length) o.o = l.o.map(([c, d]) => `${c}:${d}`);
  return JSON.stringify(o);
}

export function decodeViewerLink(raw: string | null | undefined): ViewerLink | null {
  if (!raw || raw.length > MAX_LINK) return null;
  let j: unknown;
  try { j = JSON.parse(raw); } catch { return null; }
  if (!j || typeof j !== 'object' || Array.isArray(j)) return null;
  const o = j as Record<string, unknown>;
  if (typeof o.d !== 'string' || !ID.test(o.d)) return null;
  const out: ViewerLink = { d: o.d };
  const id = (v: unknown) => (typeof v === 'string' && ID.test(v) ? v : null);
  const nm = (v: unknown) => (typeof v === 'string' && NAME.test(v) ? v : undefined);
  if (id(o.run)) out.run = id(o.run);
  if (id(o.sim)) out.sim = id(o.sim);
  if (typeof o.tab === 'string' && (TABS as readonly string[]).includes(o.tab)) out.tab = o.tab as ViewerTab;
  if (typeof o.type === 'string' && (TYPES as readonly string[]).includes(o.type)) out.type = o.type as ViewType;
  for (const k of ['x', 'y', 's', 'p'] as const) { const v = nm(o[k]); if (v) out[k] = v; }
  if (o.k === 1 || o.k === true) out.k = true;
  if (typeof o.n === 'number' && TOP_CHOICES.includes(o.n)) out.n = o.n;
  if (o.f && typeof o.f === 'object' && !Array.isArray(o.f)) {
    const f: Record<string, string> = {};
    for (const [k, v] of Object.entries(o.f as Record<string, unknown>).slice(0, 12)) if (NAME.test(k) && typeof v === 'string' && v.length <= 80) f[k] = v;
    if (Object.keys(f).length) out.f = f;
  }
  if (Array.isArray(o.o)) {
    const so: Array<[string, 'asc' | 'desc']> = [];
    for (const e of o.o.slice(0, 3)) {
      const m = typeof e === 'string' ? /^([A-Za-z0-9_.-]{1,64}):(asc|desc)$/.exec(e) : null;
      if (m) so.push([m[1], m[2] as 'asc' | 'desc']);
    }
    if (so.length) out.o = so;
  }
  return out;
}

/** A shareable address: the app's own path, ?dv=, and a hash that opens
 *  the Data Desk layout on the right run. */
export function viewerUrl(loc: { origin: string; pathname: string }, l: ViewerLink): string {
  const hash = ['layout=Data%20Desk', l.run ? `run=${encodeURIComponent(l.run)}` : null, l.sim ? `sim=${encodeURIComponent(l.sim)}` : null].filter(Boolean).join('&');
  return `${loc.origin}${loc.pathname}?dv=${encodeURIComponent(encodeViewerLink(l))}#${hash}`;
}

export function readViewerParam(search: string): ViewerLink | null {
  try { return decodeViewerLink(new URLSearchParams(search).get('dv')); } catch { return null; }
}

/* ── names <-> indexes ─────────────────────────────────────────────── */
const idx = (ds: Dataset, name: string | undefined): number | null => (name ? (ds.columns.find((c) => c.name === name)?.index ?? null) : null);
const nameOf = (ds: Dataset, i: number | null): string | undefined => (i != null ? ds.columns[i]?.name : undefined);

export function linkToView(ds: Dataset, l: ViewerLink, base: ChartView): ChartView {
  return normaliseView(ds, {
    ...base, type: l.type ?? base.type, x: l.x ? idx(ds, l.x) : base.x, y: l.y ? idx(ds, l.y) : base.y,
    series: l.s ? idx(ds, l.s) : base.series, split: l.p ? idx(ds, l.p) : base.split, stacked: l.k ?? base.stacked, top: l.n ?? base.top,
  });
}
export function linkToFilters(ds: Dataset, l: ViewerLink): Record<number, string> {
  const out: Record<number, string> = {};
  for (const [k, v] of Object.entries(l.f ?? {})) { const i = idx(ds, k); if (i != null) out[i] = v; }
  return out;
}
export function linkToSort(ds: Dataset, l: ViewerLink): SortKey[] {
  return (l.o ?? []).flatMap(([c, dir]) => { const i = idx(ds, c); return i == null ? [] : [{ col: i, dir }]; });
}

export function stateToLink(ds: Dataset, st: { runId: string | null; simRunId: string | null; tab: ViewerTab; view: ChartView; filters: Record<number, string>; sort: readonly SortKey[] }): ViewerLink {
  const l: ViewerLink = { d: ds.id, run: st.runId, sim: st.simRunId, tab: st.tab, type: st.view.type };
  const x = nameOf(ds, st.view.x); const y = nameOf(ds, st.view.y); const s = nameOf(ds, st.view.series); const p = nameOf(ds, st.view.split);
  if (x) l.x = x; if (y) l.y = y; if (s) l.s = s; if (p) l.p = p;
  if (st.view.stacked) l.k = true;
  l.n = st.view.top;
  const f: Record<string, string> = {};
  for (const [k, v] of Object.entries(st.filters)) { const n = nameOf(ds, Number(k)); if (n && v.trim()) f[n] = v; }
  if (Object.keys(f).length) l.f = f;
  const o = st.sort.flatMap((k) => { const n = nameOf(ds, k.col); return n ? [[n, k.dir] as [string, 'asc' | 'desc']] : []; });
  if (o.length) l.o = o;
  return l;
}
