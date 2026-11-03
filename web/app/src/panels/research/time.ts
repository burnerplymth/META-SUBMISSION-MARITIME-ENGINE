/* Time helpers of the Research window. Pure. The server may send a day as an ISO date, an ISO instant, epoch
 * milliseconds, epoch seconds or a whole number of days since 1970; every one becomes epoch milliseconds (UTC). */
export const DAY_MS = 86_400_000;

export function toMs(v: unknown): number | null {
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) return null;
    if (v >= 1e11) return v;
    if (v >= 1e9) return v * 1000;
    return v * DAY_MS;
  }
  if (typeof v === 'string' && v.trim()) {
    const s = v.trim();
    const t = Date.parse(/^\d{4}-\d{2}-\d{2}$/.test(s) ? `${s}T00:00:00Z` : s);
    return Number.isFinite(t) ? t : null;
  }
  return null;
}

const pad = (n: number): string => String(n).padStart(2, '0');
export const isoDay = (ms: number): string => {
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
};
export const isoInstant = (ms: number): string => `${isoDay(ms)} ${pad(new Date(ms).getUTCHours())}:${pad(new Date(ms).getUTCMinutes())} UTC`;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'] as const;
export const MONTH_NAMES = MONTHS;
/** 29 Mar 2021 */
export const displayDay = (ms: number): string => {
  const d = new Date(ms);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
};
export const addDays = (ms: number, n: number): number => ms + n * DAY_MS;
export const daysBetween = (a: number, b: number): number => (b - a) / DAY_MS;

export type RangePreset = '30d' | '90d' | '1y' | '5y';
export const RANGE_PRESETS: ReadonlyArray<{ id: RangePreset; label: string; days: number }> = [
  { id: '30d', label: '30 d', days: 30 }, { id: '90d', label: '90 d', days: 90 }, { id: '1y', label: '1 y', days: 365 }, { id: '5y', label: '5 y', days: 1826 },
];
/** The window ending at `endMs` (a preset counts back from it). Dates are whole UTC days. */
export function presetWindow(id: RangePreset, endMs: number): { from: string; to: string } {
  const p = RANGE_PRESETS.find((x) => x.id === id) ?? RANGE_PRESETS[1];
  const end = Math.floor(endMs / DAY_MS) * DAY_MS;
  return { from: isoDay(end - p.days * DAY_MS), to: isoDay(end) };
}
/** Which preset (if any) the window equals, for the pressed state of the preset buttons. */
export function presetOf(from: string, to: string): RangePreset | null {
  const a = toMs(from), b = toMs(to);
  if (a === null || b === null) return null;
  const d = Math.round(daysBetween(a, b));
  return RANGE_PRESETS.find((p) => p.days === d)?.id ?? null;
}

export const MAX_WINDOW_DAYS = 366 * 10;
/** A date range is valid when both ends parse, from is before to, and the span is at most ten years. */
export function windowIssue(from: string, to: string): 'bad_date' | 'order' | 'too_long' | null {
  const a = toMs(from), b = toMs(to);
  if (a === null || b === null || !/^\d{4}-\d{2}-\d{2}$/.test(from) || !/^\d{4}-\d{2}-\d{2}$/.test(to)) return 'bad_date';
  if (b <= a) return 'order';
  if (daysBetween(a, b) > MAX_WINDOW_DAYS) return 'too_long';
  return null;
}
