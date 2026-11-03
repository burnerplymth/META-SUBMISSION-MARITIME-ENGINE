/* Number and label formatting for chart axes, tooltips and the grid.
 * Locale-independent on purpose (en-US grouping, '.' decimal) so a
 * screenshot, and an export all agree. */

/** Chart label size in user units (CSS px at 1:1): the type floor, --fs-xs. Exports share it. */
export const CHART_FS = 11;

const GROUP = /\B(?=(\d{3})+(?!\d))/g;

/** Group the integer part of a plain decimal string. */
export function group(s: string): string {
  const neg = s.startsWith('-');
  const body = neg ? s.slice(1) : s;
  const [i, f] = body.split('.');
  return `${neg ? '-' : ''}${i.replace(GROUP, ',')}${f !== undefined ? `.${f}` : ''}`;
}

/** Significant-digit rendering that never falls back to exponent form
 *  for chart-range values and trims trailing zeros. */
export function trimNumber(v: number, maxFrac: number): string {
  const s = v.toFixed(Math.max(0, Math.min(10, maxFrac)));
  return s.includes('.') ? s.replace(/\.?0+$/, '') : s;
}

/** Decimals a tick step needs to print exactly: 2.5 needs one, 0.25 two, 25 none. */
export function stepDecimals(step: number): number {
  for (let d = 0; d <= 6; d++) if (Math.abs(Math.round(step * 10 ** d) - step * 10 ** d) < 1e-7 * Math.max(1, step * 10 ** d)) return d;
  return 6;
}

/** Axis tick: compact (k / M / B) above 10,000, grouped below. */
export function formatTick(v: number, step?: number): string {
  if (!Number.isFinite(v)) return '';
  const a = Math.abs(v);
  if (a >= 1e9) return `${trimNumber(v / 1e9, 2)}B`;
  if (a >= 1e6) return `${trimNumber(v / 1e6, 2)}M`;
  if (a >= 1e4) return `${trimNumber(v / 1e3, 1)}k`;
  const dp = step !== undefined && step > 0 ? stepDecimals(step) : a >= 100 ? 0 : a >= 1 ? 2 : 3;
  return group(trimNumber(v, Math.min(dp, 6)));
}

/** A value in a tooltip / table: grouped, trailing zeros trimmed, enough
 *  decimals that the figure is the one in the file. */
export function formatValue(v: number | null | undefined, unit?: string | null): string {
  if (v == null || !Number.isFinite(v)) return 'NOT SERVED';
  const a = Math.abs(v);
  const frac = a >= 1e6 ? 0 : a >= 100 ? 2 : a >= 10 ? 3 : a >= 1 ? 4 : a === 0 ? 0 : 5;
  const s = group(trimNumber(v, frac));
  return unit ? `${s} ${unit}` : s;
}

/** Percent from a 0..1 share. */
export const formatShare = (v: number | null | undefined): string =>
  v == null || !Number.isFinite(v) ? 'NOT SERVED' : `${trimNumber(v * 100, Math.abs(v) < 0.1 ? 1 : 0)}%`;

/** Pull "[unit]" out of a server header such as "delay_d [days]". */
export function parseHeader(h: string): { name: string; unit: string | null } {
  const m = /^(.*?)\s*\[([^\]]*)\]\s*$/.exec(h);
  return m ? { name: m[1], unit: m[2] || null } : { name: h, unit: null };
}

/** Human label for a snake_case column name: DELAY D, AT. */
export const columnLabel = (name: string): string => name.replace(/_/g, ' ').toUpperCase();

/** Rough monospace text width in px (9 to 11 px mono: 0.6 em per glyph). */
export const textWidth = (s: string, size = CHART_FS): number => s.length * size * 0.6;

/** Clip a label to a pixel budget with an ellipsis. */
export function clipLabel(s: string, maxPx: number, size = CHART_FS): string {
  const max = Math.max(1, Math.floor(maxPx / (size * 0.6)));
  return s.length <= max ? s : `${s.slice(0, Math.max(1, max - 1))}…`;
}

/** Byte size, binary-free: 1.2 KB. */
export function formatBytes(n: number | null | undefined): string | null {
  if (n == null || !Number.isFinite(n)) return null;
  if (n < 1000) return `${Math.round(n)} B`;
  if (n < 1e6) return `${trimNumber(n / 1e3, n < 1e4 ? 1 : 0)} KB`;
  return `${trimNumber(n / 1e6, 1)} MB`;
}

/** An axis title: the label, then the unit in brackets unless the label already says it (case aside). */
export function axisTitle(label: string | null | undefined, unit: string | null | undefined): string {
  const l = label ?? '';
  const u = unit ?? '';
  const said = !u || u.toLowerCase() === l.toLowerCase() || l.toLowerCase().includes(u.toLowerCase());
  return [l, said ? null : `[${u}]`].filter(Boolean).join(' ');
}
