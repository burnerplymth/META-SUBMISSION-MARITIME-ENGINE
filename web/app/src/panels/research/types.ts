/* Research window: the shapes of /research/* and the validators for what the
 * server, or a recorded copy of it, sends. Every document is untrusted: a parser returns a clean typed value or
 * null, never throws, and never lets a non-finite number through (a missing value is null, drawn as a gap). */
import { toMs } from './time';

/** Written into every export and shown as a chip row; the sentences stay apart so each is short. */
export const LABEL_PARTS = ['Descriptive statistics on historical data.', 'Associations, not causes.', 'Conditional on stated data.'] as const;
export const DESCRIPTIVE_LABEL: string = LABEL_PARTS.join(' ');

export const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
export const str = (v: unknown, max = 200): string | null => (typeof v === 'string' && v.length > 0 ? v.slice(0, max) : null);
export const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const strs = (v: unknown, max = 40): string[] => arr(v).filter((x): x is string => typeof x === 'string').slice(0, max);

export type DatasetState = 'cached' | 'live' | 'not_served';
const stateOf = (v: unknown): DatasetState => (v === 'live' ? 'live' : v === 'cached' ? 'cached' : 'not_served');

/* ── episodes ───────────────────────────────────────────────────────── */

export interface SourceRef { publisher: string | null; url: string | null; date: string | null }
export function parseSource(v: unknown): SourceRef {
  if (!isObj(v)) return { publisher: null, url: null, date: null };
  const url = str(v.url, 500);
  return { publisher: str(v.publisher, 200), url: url && /^https:\/\//i.test(url) ? url : null, date: str(v.date, 10) };
}

export interface ResearchEvent {
  id: string; t: string; tEnd: string | null; category: string; title: string; place: string | null; source: SourceRef;
}
export function parseEvents(v: unknown): ResearchEvent[] {
  const out: ResearchEvent[] = [];
  const seen = new Set<string>();
  for (const e of arr(v)) {
    if (!isObj(e)) continue;
    const id = str(e.id, 80), t = str(e.t, 30), title = str(e.title, 200);
    if (!id || !t || !title || toMs(t) === null || seen.has(id)) continue;
    seen.add(id);
    out.push({ id, t, tEnd: str(e.t_end, 30), category: str(e.category, 40) ?? 'other', title, place: str(e.place, 64), source: parseSource(e.source) });
  }
  return out.sort((a, b) => (toMs(a.t) as number) - (toMs(b.t) as number) || a.id.localeCompare(b.id));
}

export interface Episode {
  id: string; title: string; places: string[]; from: string; to: string; categories: string[]; events: ResearchEvent[];
  series: string[]; scenes: Record<string, number>; hasTracks: boolean; simRuns: string[]; notesChip: string | null;
}
export function parseEpisodes(body: unknown): Episode[] | null {
  if (!isObj(body) || !Array.isArray(body.episodes)) return null;
  const out: Episode[] = [];
  for (const e of body.episodes) {
    if (!isObj(e)) continue;
    const id = str(e.id, 64), title = str(e.title, 160);
    const w = isObj(e.window) ? e.window : {};
    const from = str(w.from, 10), to = str(w.to, 10);
    if (!id || !/^[a-z0-9_:.-]+$/.test(id) || !title || !from || !to || toMs(from) === null || toMs(to) === null) continue;
    const scenes: Record<string, number> = {};
    if (isObj(e.scenes)) for (const [k, n] of Object.entries(e.scenes)) { const c = num(n); if (c !== null && /^sentinel-[12]$/.test(k)) scenes[k] = c; }
    out.push({
      id, title, places: strs(e.places, 5), from, to, categories: strs(e.categories, 12), events: parseEvents(e.events),
      series: strs(e.series, 24), scenes, hasTracks: e.has_tracks === true, simRuns: strs(e.sim_runs, 8), notesChip: str(e.notes_chip, 120),
    });
  }
  return out;
}

/* ── assembly ───────────────────────────────────────────────────────── */

export interface SeriesBlock {
  key: string; dataset: string; place: string; unit: string | null; source: string | null; state: DatasetState; values: Array<number | null>;
}
export interface SamResult { count: number; text: string; note: string | null; recorded: boolean; chips: string[] }
export interface SceneRow {
  sceneId: string; t: string; place: string | null; source: string | null; cloudPct: number | null; nodataPct: number | null; usable: boolean;
}
export interface TrackPoint { t: string; lon: number; lat: number }
export interface TrackRow { id: string; cls: string | null; points: TrackPoint[]; durationH: number | null }
export interface CoverageNote { chip: string; dataset: string | null; place: string | null; text: string | null }
export interface PlaceRef { id: string; name: string; kind: string | null; lat: number | null; lon: number | null }

export interface Assembly {
  id: string; label: string; from: string; to: string; days: number; places: PlaceRef[]; t: string[]; series: SeriesBlock[];
  events: ResearchEvent[]; scenes: SceneRow[]; tracks: TrackRow[]; coverage: CoverageNote[];
}

export function parseAssembly(body: unknown): Assembly | null {
  if (!isObj(body)) return null;
  const id = str(body.assembly_id, 40);
  const tl = body.timeline;
  if (!id || !/^asm_[0-9a-f]{16}$/.test(id) || !isObj(tl) || !Array.isArray(tl.t) || !isObj(tl.series)) return null;
  const t = tl.t.filter((x): x is string => typeof x === 'string' && toMs(x) !== null);
  if (t.length !== tl.t.length) return null;
  const series: SeriesBlock[] = [];
  for (const [key, raw] of Object.entries(tl.series)) {
    if (!isObj(raw) || !/^[\w.:-]{1,100}$/.test(key)) continue;
    const vals = arr(raw.values);
    series.push({
      key, dataset: str(raw.dataset, 80) ?? key.split(':')[0], place: str(raw.place, 64) ?? key.split(':')[1] ?? 'global', unit: str(raw.unit, 30),
      source: str(raw.source, 200), state: stateOf(raw.state), values: t.map((_, i) => num(vals[i])),
    });
  }
  const win = isObj(body.window) ? body.window : {};
  const places: PlaceRef[] = arr(body.places).filter(isObj).map((p) => ({
    id: str(p.id, 64) ?? '', name: str(p.name, 80) ?? str(p.id, 64) ?? '', kind: str(p.kind, 30), lat: num(p.lat), lon: num(p.lon),
  })).filter((p) => p.id);
  const scenes: SceneRow[] = [];
  for (const s of arr(body.scenes)) {
    if (!isObj(s)) continue;
    const sceneId = str(s.scene_id, 100), st = str(s.t, 10);
    if (!sceneId || !st) continue;
    scenes.push({ sceneId, t: st, place: str(s.place, 64), source: str(s.source, 20), cloudPct: num(s.cloud_pct), nodataPct: num(s.nodata_pct), usable: s.usable === true });
  }
  const tracks: TrackRow[] = [];
  for (const tr of arr(body.tracks)) {
    if (!isObj(tr)) continue;
    const tid = str(tr.vessel_id_hash, 64);
    const points: TrackPoint[] = [];
    for (const p of arr(tr.points)) {
      if (!isObj(p)) continue;
      const lon = num(p.lon), lat = num(p.lat), pt = str(p.t, 30);
      if (lon === null || lat === null || !pt || Math.abs(lat) > 90 || Math.abs(lon) > 180) continue;
      points.push({ t: pt, lon, lat });
    }
    if (tid && points.length >= 2) tracks.push({ id: tid, cls: str(tr.class, 30), points, durationH: num(tr.duration_h) });
  }
  const coverage: CoverageNote[] = arr(body.coverage_notes).filter(isObj).map((c) => ({
    chip: str(c.chip, 60) ?? 'note', dataset: str(c.dataset, 80), place: str(c.place, 64), text: str(c.text, 200),
  }));
  return {
    id, label: str(body.label, 200) ?? '', from: str(win.from, 10) ?? t[0] ?? '', to: str(win.to, 10) ?? t[t.length - 1] ?? '',
    days: num(win.days) ?? t.length, places, t, series, events: parseEvents(body.events), scenes, tracks, coverage,
  };
}

/* ── scene scan (recorded SAM) ──────────────────────────────────────── */

export interface ScanDoc { results: Record<string, SamResult>; notCounted: Record<string, string> }
export function parseScan(body: unknown): ScanDoc | null {
  if (!isObj(body) || !Array.isArray(body.results)) return null;
  const results: Record<string, SamResult> = {};
  for (const r of body.results) {
    if (!isObj(r)) continue;
    const id = str(r.scene_id, 100), c = num(r.found);
    if (!id || c === null) continue;
    results[id] = { count: c, text: str(r.text, 60) ?? `SAM found ${c}`, note: str(r.note, 240), recorded: r.recorded === true, chips: strs(r.chips, 6) };
  }
  const notCounted: Record<string, string> = {};
  for (const n of arr(body.not_counted)) if (isObj(n)) { const id = str(n.scene_id, 100); if (id) notCounted[id] = str(n.reason, 60) ?? 'not_counted'; }
  return { results, notCounted };
}

/* ── analyses ───────────────────────────────────────────────────────── */

export type AnalysisKind = 'event_study' | 'before_after' | 'lagged_correlation' | 'change_points' | 'distribution' | 'seasonality' | 'model_overlay';
export const ANALYSIS_KINDS: readonly AnalysisKind[] = ['event_study', 'before_after', 'lagged_correlation', 'change_points', 'distribution', 'seasonality', 'model_overlay'];
export const KIND_LABEL: Record<AnalysisKind, string> = {
  event_study: 'Event study', before_after: 'Before and after', lagged_correlation: 'Lagged correlation', change_points: 'Change points',
  distribution: 'Distribution', seasonality: 'Seasonality', model_overlay: 'Model overlay',
};

export type ChartPoint = [number | string, number | null];
export interface ChartBand { id: string; label: string; points: Array<Array<number | string | null>> }
export interface ChartSeriesOut { id: string; label: string; kind: 'observed' | 'simulated' | 'baseline'; points: ChartPoint[] }
export interface ChartMarker { x: number | string; label: string; kind: string }
export interface ChartSpecOut {
  type: 'line' | 'bar' | 'histogram' | 'heatmap_lag' | 'segments';
  xField: string; xLabel: string | null; xUnit: string | null; yLabel: string | null; yUnit: string | null;
  series: ChartSeriesOut[]; bands: ChartBand[]; markers: ChartMarker[];
}

export function parseChartSpec(v: unknown): ChartSpecOut | null {
  if (!isObj(v)) return null;
  const t = v.type;
  if (t !== 'line' && t !== 'bar' && t !== 'histogram' && t !== 'heatmap_lag' && t !== 'segments') return null;
  const x = isObj(v.x) ? v.x : {}, y = isObj(v.y) ? v.y : {};
  const series: ChartSeriesOut[] = [];
  for (const s of arr(v.series)) {
    if (!isObj(s)) continue;
    const id = str(s.id, 60);
    if (!id) continue;
    const kind = s.kind === 'simulated' ? 'simulated' : s.kind === 'baseline' ? 'baseline' : 'observed';
    const points: ChartPoint[] = [];
    for (const p of arr(s.points)) {
      if (!Array.isArray(p) || p.length < 2) continue;
      const px = typeof p[0] === 'string' || (typeof p[0] === 'number' && Number.isFinite(p[0])) ? (p[0] as string | number) : null;
      if (px !== null) points.push([px, num(p[1])]);
    }
    series.push({ id, label: str(s.label, 80) ?? id, kind, points });
  }
  const bands: ChartBand[] = arr(v.bands).filter(isObj).map((b) => ({
    id: str(b.id, 60) ?? 'band', label: str(b.label, 80) ?? 'Band', points: arr(b.points).filter(Array.isArray) as Array<Array<number | string | null>>,
  }));
  const markers: ChartMarker[] = arr(v.markers).filter(isObj)
    .map((m) => ({ x: (typeof m.x === 'string' || typeof m.x === 'number' ? m.x : null) as number | string | null, label: str(m.label, 60) ?? '', kind: str(m.kind, 30) ?? 'event' }))
    .filter((m): m is ChartMarker => m.x !== null);
  return { type: t, xField: str(x.field, 40) ?? 'x', xLabel: str(x.label, 80), xUnit: str(x.unit, 30), yLabel: str(y.label, 80), yUnit: str(y.unit, 30), series, bands, markers };
}

export interface CiOut { level: number | null; method: string | null; low: number | null; high: number | null }
export interface AnalysisResult {
  id: string; kind: AnalysisKind; label: string; inputs: Record<string, unknown>; n: number | null; nUnit: string | null;
  estimate: Record<string, unknown>; ci: CiOut | null; hasPointwiseCi: boolean; chart: ChartSpecOut | null; caveats: string[];
  error: { code: string; message: string } | null;
}

const kindOf = (v: unknown): AnalysisKind | null => (typeof v === 'string' && (ANALYSIS_KINDS as readonly string[]).includes(v) ? (v as AnalysisKind) : null);

export function parseResult(v: unknown): AnalysisResult | null {
  if (!isObj(v)) return null;
  const kind = kindOf(v.kind), id = str(v.id, 80);
  if (!kind || !id) return null;
  const err = isObj(v.error) ? { code: str(v.error.code, 40) ?? 'error', message: str(v.error.message, 200) ?? '' } : null;
  const ci = isObj(v.ci) ? { level: num(v.ci.level), method: str(v.ci.method, 160), low: num(v.ci.low), high: num(v.ci.high) } : null;
  return {
    id, kind, label: str(v.label, 400) ?? DESCRIPTIVE_LABEL, inputs: isObj(v.inputs) ? v.inputs : {}, n: num(v.n), nUnit: str(v.n_unit, 20),
    estimate: isObj(v.estimate) ? v.estimate : {}, ci, hasPointwiseCi: isObj(v.ci) && Array.isArray(v.ci.pointwise), chart: parseChartSpec(v.chart_spec),
    caveats: strs(v.caveat_chips, 12), error: err,
  };
}

export function parseAnalyze(body: unknown): AnalysisResult[] | null {
  if (!isObj(body) || !Array.isArray(body.results)) return null;
  return body.results.map(parseResult).filter((r): r is AnalysisResult => r !== null);
}
