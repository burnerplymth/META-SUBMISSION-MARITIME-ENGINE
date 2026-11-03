/* Chart scales, ticks and domains. Pure: no DOM, no React. Everything
 * the SVG builders need to turn data into pixels and back. */

export type Domain = readonly [number, number];

export interface LinearScale {
  (v: number): number;
  invert: (px: number) => number;
  domain: Domain;
  range: Domain;
}

/** Linear map domain -> range. A zero-width domain maps to the range
 *  midpoint (a single point sits in the middle, never at NaN). */
export function linearScale(domain: Domain, range: Domain): LinearScale {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0;
  const f = ((v: number) => (span === 0 ? (r0 + r1) / 2 : r0 + ((v - d0) / span) * (r1 - r0))) as LinearScale;
  f.invert = (px: number) => (r1 === r0 ? d0 : d0 + ((px - r0) / (r1 - r0)) * span);
  f.domain = domain; f.range = range;
  return f;
}

export function extent(values: ReadonlyArray<number | null | undefined>): Domain | null {
  let lo = Infinity; let hi = -Infinity;
  for (const v of values) {
    if (v == null || !Number.isFinite(v)) continue;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  return lo <= hi ? [lo, hi] : null;
}

/** Step 1, 2, 2.5 or 5 times a power of ten, whichever lands nearest
 *  the wanted tick count. */
export function niceStep(span: number, count: number): number {
  if (!(span > 0) || count < 1) return 1;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const pick = norm < 1.5 ? 1 : norm < 2.25 ? 2 : norm < 3.5 ? 2.5 : norm < 7.5 ? 5 : 10;
  return pick * mag;
}

/** Round decimals produced by float steps (0.1 + 0.2). */
const clean = (v: number, step: number): number => {
  const dp = Math.max(0, -Math.floor(Math.log10(step)) + 2);
  return Number(v.toFixed(Math.min(12, dp)));
};

/** "Nice" ticks inside [lo, hi]. */
export function niceTicks(lo: number, hi: number, count = 5): number[] {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [];
  if (lo === hi) return [lo];
  if (lo > hi) [lo, hi] = [hi, lo];
  const step = niceStep(hi - lo, count);
  const first = Math.ceil(lo / step - 1e-9) * step;
  const out: number[] = [];
  for (let v = first, i = 0; v <= hi + step * 1e-9 && i < 200; v += step, i++) out.push(clean(v, step));
  return out;
}

/** Widen [lo, hi] to nice bounds (for a value axis). */
export function niceDomain(lo: number, hi: number, count = 5): Domain {
  if (lo === hi) {
    const pad = lo === 0 ? 1 : Math.abs(lo) * 0.1;
    return [lo - pad, hi + pad];
  }
  const step = niceStep(hi - lo, count);
  return [clean(Math.floor(lo / step + 1e-9) * step, step), clean(Math.ceil(hi / step - 1e-9) * step, step)];
}

/** A value-axis domain: nice bounds, optionally pinned to include zero
 *  (bars always do; lines only when asked). */
export function valueDomain(ext: Domain | null, opts: { zero?: boolean; count?: number } = {}): Domain {
  if (!ext) return [0, 1];
  let [lo, hi] = ext;
  if (opts.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  return niceDomain(lo, hi, opts.count ?? 5);
}

/* ── time ───────────────────────────────────────────────────────────── */
const MIN = 60_000; const HOUR = 3_600_000; const DAY = 86_400_000;
export const TIME_STEPS: readonly number[] = [
  MIN, 5 * MIN, 15 * MIN, 30 * MIN, HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR, DAY, 2 * DAY, 7 * DAY, 14 * DAY,
];

export interface TimeTicks { ticks: number[]; step: number; unit: 'minute' | 'hour' | 'day' | 'month' | 'year' }

/** Calendar ticks in UTC: day-or-finer steps are epoch-aligned, longer
 *  spans tick on the first of each month (or quarter / year). */
export function timeTicks(t0: number, t1: number, count = 5): TimeTicks {
  if (!(t1 > t0)) return { ticks: Number.isFinite(t0) ? [t0] : [], step: HOUR, unit: 'hour' };
  const span = t1 - t0;
  const want = span / Math.max(1, count);
  if (want <= 14 * DAY * 1.2) {
    const step = TIME_STEPS.find((s) => s >= want * 0.8) ?? TIME_STEPS[TIME_STEPS.length - 1];
    const first = Math.ceil(t0 / step) * step;
    const ticks: number[] = [];
    for (let t = first; t <= t1 && ticks.length < 200; t += step) ticks.push(t);
    return { ticks, step, unit: step < HOUR ? 'minute' : step < DAY ? 'hour' : 'day' };
  }
  const d0 = new Date(t0);
  const months = want < 45 * DAY ? 1 : want < 120 * DAY ? 3 : want < 240 * DAY ? 6 : 12;
  const ticks: number[] = [];
  let y = d0.getUTCFullYear(); let m = d0.getUTCMonth();
  m = Math.ceil(m / months) * months;
  if (months === 12) { m = 0; if (Date.UTC(y, 0, 1) < t0) y += 1; }
  for (let i = 0; i < 400; i++) {
    const t = Date.UTC(y + Math.floor(m / 12), m % 12, 1);
    if (t > t1) break;
    if (t >= t0) ticks.push(t);
    m += months;
  }
  return { ticks, step: months * 30 * DAY, unit: months >= 12 ? 'year' : 'month' };
}

const p2 = (n: number) => String(n).padStart(2, '0');
/** Tick label for a time axis; UTC, compact. */
export function formatTimeTick(t: number, unit: TimeTicks['unit'], _step?: number): string {
  const d = new Date(t);
  const ymd = `${d.getUTCFullYear()}-${p2(d.getUTCMonth() + 1)}-${p2(d.getUTCDate())}`;
  if (unit === 'year') return String(d.getUTCFullYear());
  if (unit === 'month') return `${d.getUTCFullYear()}-${p2(d.getUTCMonth() + 1)}`;
  if (unit === 'day') return ymd.slice(5);
  return `${ymd.slice(5)} ${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}`;
}

/** Full UTC timestamp for tooltips and CSV: 2026-10-20T06:00Z. */
export function formatTimeFull(t: number): string {
  const d = new Date(t);
  return `${d.getUTCFullYear()}-${p2(d.getUTCMonth() + 1)}-${p2(d.getUTCDate())}T${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}Z`;
}

/* ── band (categories) ──────────────────────────────────────────────── */
export interface BandScale {
  (key: string): number;
  bandwidth: number;
  step: number;
  keys: readonly string[];
  /** index of the band containing px, or -1 */
  indexAt: (px: number) => number;
}
export function bandScale(keys: readonly string[], range: Domain, padding = 0.25): BandScale {
  const [r0, r1] = range;
  const n = Math.max(1, keys.length);
  const step = (r1 - r0) / n;
  const bandwidth = Math.abs(step) * (1 - padding);
  const offset = (Math.abs(step) - bandwidth) / 2;
  const idx = new Map(keys.map((k, i) => [k, i] as const));
  const f = ((k: string) => {
    const i = idx.get(k) ?? 0;
    return step >= 0 ? r0 + i * step + offset : r0 + i * step - offset - bandwidth;
  }) as BandScale;
  f.bandwidth = bandwidth; f.step = step; f.keys = keys;
  f.indexAt = (px: number) => {
    const i = Math.floor((px - r0) / step);
    return i >= 0 && i < keys.length ? i : -1;
  };
  return f;
}

/** Zoom a domain by `factor` (<1 in, >1 out) about `centre`, clamped to
 *  `full`. */
export function zoomDomain(cur: Domain, full: Domain, factor: number, centre?: number): Domain {
  const c = centre ?? (cur[0] + cur[1]) / 2;
  let lo = c - (c - cur[0]) * factor;
  let hi = c + (cur[1] - c) * factor;
  const fullSpan = full[1] - full[0];
  if (hi - lo >= fullSpan) return full;
  if (lo < full[0]) { hi += full[0] - lo; lo = full[0]; }
  if (hi > full[1]) { lo -= hi - full[1]; hi = full[1]; }
  return [Math.max(full[0], lo), Math.min(full[1], hi)];
}

/** A brushed pixel interval -> a domain (null when too small to mean it). */
export function brushDomain(scale: LinearScale, px0: number, px1: number, minPx = 6): Domain | null {
  const a = Math.min(px0, px1); const b = Math.max(px0, px1);
  if (b - a < minPx) return null;
  const lo = scale.invert(a); const hi = scale.invert(b);
  return hi > lo ? [lo, hi] : null;
}

export const sameDomain = (a: Domain | null, b: Domain | null): boolean =>
  a === b || (!!a && !!b && a[0] === b[0] && a[1] === b[1]);
