/* Result chart_spec (contract) -> the charts kit's ChartSpec. Intervals and bands are drawn only when the result
 * carries them; observed, baseline and simulated series keep separate colours and are labelled. */
import type { BarSpec, ChartSpec, EventMarker, LineSpec, Provenance, SeriesIn } from '../../charts/types';
import { toMs } from './time';
import type { AnalysisResult, ChartSpecOut } from './types';
import { KIND_LABEL } from './types';

const SIM_COLOR = 'var(--c-sim, #b46ad8)';
const BASE_COLOR = 'var(--c-base, #8a94a6)';

function prov(r: AnalysisResult, recorded: boolean): Provenance {
  if (r.kind === 'model_overlay') return { chip: 'SIMULATED', source: 'Observed beside simulated', scope: recorded ? 'Recorded' : null };
  return { chip: 'SNAPSHOT', source: 'Historical archive', scope: recorded ? 'Recorded' : null };
}

const xNum = (x: number | string, time: boolean): number | null => (typeof x === 'number' ? x : time ? toMs(x) : null);

function lineFrom(r: AnalysisResult, c: ChartSpecOut, recorded: boolean): LineSpec | null {
  const time = c.xField === 'date';
  const series: SeriesIn[] = [];
  const add = (key: string, label: string, pts: Array<[number | string, number | null]>, color?: string) => {
    const points = pts.map(([x, y]) => ({ x: xNum(x, time), y })).filter((p): p is { x: number; y: number | null } => p.x !== null);
    if (points.length) series.push({ key, label, points, color });
  };
  for (const s of c.series) add(s.id, s.label, s.points, s.kind === 'simulated' ? SIM_COLOR : s.kind === 'baseline' ? BASE_COLOR : undefined);
  for (const b of c.bands) {
    const lo = b.points.map((p) => [p[0], p[1]] as [number | string, number | null]);
    const hi = b.points.map((p) => [p[0], p[2]] as [number | string, number | null]);
    const fin = (a: Array<[number | string, number | null]>) => a.map(([x, y]) => [x, typeof y === 'number' && Number.isFinite(y) ? y : null] as [number | string, number | null]);
    add(`${b.id}_low`, `${b.label}, low`, fin(lo), BASE_COLOR);
    add(`${b.id}_high`, `${b.label}, high`, fin(hi), BASE_COLOR);
  }
  if (!series.length) return null;
  const events: EventMarker[] = c.markers.map((m) => ({ x: xNum(m.x, time), label: m.label || 'Event' })).filter((m): m is EventMarker => m.x !== null);
  return {
    id: r.id, type: 'line', title: KIND_LABEL[r.kind], subtitle: c.yLabel, provenance: prov(r, recorded), xKind: time ? 'time' : 'linear', series, events,
    xLabel: c.xLabel, yLabel: c.yLabel, xUnit: time ? null : c.xUnit, yUnit: c.yUnit,
  };
}

function barFrom(r: AnalysisResult, c: ChartSpecOut, recorded: boolean): BarSpec | null {
  const first = c.series[0];
  if (!first || !first.points.length) return null;
  const cats = first.points.map(([x]) => String(x));
  const weekday = c.xField === 'weekday';
  const fmt = (x: string) => (c.type === 'histogram' ? String(Math.round(Number(x) * 10) / 10) : weekday ? weekdayWord(x) : x);
  const series = c.series.map((s) => ({ key: s.id, label: s.label, values: first.points.map(([x]) => s.points.find(([sx]) => String(sx) === String(x))?.[1] ?? null) }));
  for (const b of c.bands) {
    const at = (i: number) => cats.map((k) => bandValueAt(b.points, k, i));
    series.push({ key: `${b.id}_low`, label: `${b.label}, low`, values: at(1) }, { key: `${b.id}_high`, label: `${b.label}, high`, values: at(2) });
  }
  return {
    /* the y axis title is drawn inside the chart: a frame subtitle repeating it printed it twice */
    id: r.id, type: 'bar', title: KIND_LABEL[r.kind], provenance: prov(r, recorded), mode: 'grouped',
    categories: cats.map((k) => ({ key: k, label: fmt(k) })), series, xLabel: weekday ? 'Day of week' : c.xLabel, yLabel: c.yLabel, xUnit: null, yUnit: c.yUnit,
  };
}

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
/** A weekday index served as 0 (Monday) to 6 (Sunday) reads as its name; anything else is left as served. */
export function weekdayWord(x: string): string {
  const n = Number(x);
  return x.trim() !== '' && Number.isInteger(n) && n >= 0 && n <= 6 ? WEEKDAYS[n] : x;
}

export function chartSpecFor(r: AnalysisResult, recorded = false): ChartSpec | null {
  const c = r.chart;
  if (!c || r.error) return null;
  if (c.type === 'line' || c.type === 'segments') return lineFrom(r, c, recorded);
  return barFrom(r, c, recorded);
}

/** Aligned timeline: one line spec per series key (units differ, so one chart each). */
export function seriesSpec(key: string, label: string, unit: string | null, t: string[], values: Array<number | null>, events: Array<{ id: string; t: string; title: string }>, recorded: boolean): LineSpec {
  const points = t.map((d, i) => ({ x: toMs(d) as number, y: values[i] ?? null }));
  return {
    id: `series:${key}`, type: 'line', title: label, subtitle: null, provenance: { chip: 'SNAPSHOT', source: 'Historical archive', scope: recorded ? 'Recorded' : null },
    xKind: 'time', series: [{ key, label, points }], events: events.map((e) => ({ x: toMs(e.t) as number, label: e.title, id: e.id })).filter((e) => Number.isFinite(e.x)),
    xLabel: 'Date', yLabel: unit, xUnit: null, yUnit: unit,
  };
}

/** One value of a band at category `k`. A band is served as a few points (a constant band as its two end lags).
 *  A category with its own point reads that point (null when the point exists but is not a number: NOT SERVED);
 *  a category between two points of equal value reads that value (the band is constant there); any other
 *  category has no row (NaN: nothing is drawn, nothing is said). */
export function bandValueAt(points: ReadonlyArray<readonly unknown[]>, k: string, i: number): number | null {
  const own = points.find((q) => String(q[0]) === k);
  if (own) { const v = own[i]; return typeof v === 'number' && Number.isFinite(v) ? v : null; }
  const x = Number(k);
  if (!Number.isFinite(x)) return Number.NaN;
  const num = points.filter((q) => typeof q[0] === 'number' && typeof q[i] === 'number' && Number.isFinite(q[i] as number)).sort((a, b) => (a[0] as number) - (b[0] as number));
  for (let j = 0; j + 1 < num.length; j++) {
    const a = num[j]; const b = num[j + 1];
    if (x > (a[0] as number) && x < (b[0] as number) && a[1] === b[1] && a[2] === b[2]) return a[i] as number;
  }
  return Number.NaN;
}
