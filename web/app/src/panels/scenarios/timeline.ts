/* Scenario builder timeline math. Pure: no React, no DOM, no clock. Times are
 * days from the scenario start; the same arithmetic backs the SVG editor, the
 * keyboard control. Nothing is clamped silently: a move or a
 * resize that would leave the bounds stops at the bound and says so by
 * returning the bounded value (the editor shows the bound on the chip). */
import type { UiShock } from './types';

export const OFFSET_MAX_D = 365;
export const DURATION_MAX_D = 3650;
export const SNAPS = [0.25, 1] as const;
export type Snap = typeof SNAPS[number];
export const DEFAULT_SNAP: Snap = 0.25;
export const MIN_PX_PER_DAY = 1;
export const MAX_PX_PER_DAY = 120;
export const DEFAULT_PX_PER_DAY = 14;

export const r4 = (x: number): number => Math.round(x * 10000) / 10000;
export const clamp = (x: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, x));

/** Snap to a step; step 0 only rounds to the wire precision. */
export const snapTo = (v: number, step: number): number => (step > 0 ? r4(Math.round(v / step) * step) : r4(v));

export const endOf = (s: Pick<UiShock, 'startD' | 'durationD'>): number => r4(s.startD + s.durationD);

/** The smallest bar the editor draws or accepts by dragging. */
export const minDuration = (snap: number): number => (snap > 0 ? snap : 0.01);

/** Move a bar: the new start for a pointer delta, snapped and held in [0, OFFSET_MAX_D]. */
export function moveStart(startD: number, deltaD: number, snap: number): number {
  return clamp(snapTo(startD + deltaD, snap), 0, OFFSET_MAX_D);
}

/** Drag the right end: the new duration, end snapped, never below the minimum. */
export function resizeEnd(startD: number, durationD: number, deltaD: number, snap: number): number {
  const end = snapTo(startD + durationD + deltaD, snap);
  return clamp(r4(end - startD), minDuration(snap), DURATION_MAX_D);
}

/** Drag the left end: the right end stays; start snapped, held in [0, end - minimum]. */
export function resizeStart(startD: number, durationD: number, deltaD: number, snap: number): { startD: number; durationD: number } {
  const end = r4(startD + durationD);
  const next = clamp(snapTo(startD + deltaD, snap), 0, Math.max(0, end - minDuration(snap)));
  return { startD: next, durationD: r4(end - next) };
}

export type KeyEdit = { kind: 'move' | 'resize-end' | 'resize-start'; delta: number } | { kind: 'home' } | null;
/** Keyboard control of a focused bar. Arrows move by one snap (Shift: one day);
 *  Alt+Arrows move the end; Alt+Ctrl+Arrows move the start edge. Home returns to day 0. */
export function keyEdit(e: { key: string; shiftKey: boolean; altKey: boolean; ctrlKey?: boolean; metaKey?: boolean }, snap: number): KeyEdit {
  if (e.key === 'Home') return { kind: 'home' };
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return null;
  const step = e.shiftKey ? 1 : (snap > 0 ? snap : 0.25);
  const delta = e.key === 'ArrowLeft' ? -step : step;
  if (e.altKey && (e.ctrlKey || e.metaKey)) return { kind: 'resize-start', delta };
  if (e.altKey) return { kind: 'resize-end', delta };
  return { kind: 'move', delta };
}

/** Apply a keyboard edit to a shock's (start, duration). */
export function applyKeyEdit(s: Pick<UiShock, 'startD' | 'durationD'>, k: Exclude<KeyEdit, null>, snap: number): { startD: number; durationD: number } {
  if (k.kind === 'home') return { startD: 0, durationD: s.durationD };
  if (k.kind === 'move') return { startD: moveStart(s.startD, k.delta, snap), durationD: s.durationD };
  if (k.kind === 'resize-end') return { startD: s.startD, durationD: resizeEnd(s.startD, s.durationD, k.delta, snap) };
  return resizeStart(s.startD, s.durationD, k.delta, snap);
}

/* ── overlap and span ───────────────────────────────────────────────── */
export interface OverlapSeg { a: string; b: string; fromD: number; toD: number; samePlace: boolean }
/** Pairs of shocks whose windows overlap in time (strictly), in list order. */
export function overlapSegments(shocks: readonly UiShock[]): OverlapSeg[] {
  const out: OverlapSeg[] = [];
  for (let i = 0; i < shocks.length; i++) {
    for (let j = i + 1; j < shocks.length; j++) {
      const a = shocks[i], b = shocks[j];
      const lo = Math.max(a.startD, b.startD), hi = Math.min(endOf(a), endOf(b));
      if (lo < hi) out.push({ a: a.id, b: b.id, fromD: r4(lo), toD: r4(hi), samePlace: a.placeId === b.placeId });
    }
  }
  return out;
}

export interface Span { firstStartD: number; lastEndD: number; totalSpanD: number }
/** First start to last end. */
export function spanOf(shocks: readonly UiShock[]): Span {
  if (shocks.length === 0) return { firstStartD: 0, lastEndD: 0, totalSpanD: 0 };
  const lo = Math.min(...shocks.map((s) => s.startD));
  const hi = Math.max(...shocks.map(endOf));
  return { firstStartD: r4(lo), lastEndD: r4(hi), totalSpanD: r4(hi - lo) };
}

/* ── axis ───────────────────────────────────────────────────────────── */
const TICK_STEPS = [1, 2, 5, 7, 10, 14, 30, 60, 90, 180, 365];
/** Days between axis ticks so that labels stay at least `minPx` apart. */
export function tickStep(pxPerDay: number, minPx = 64): number {
  return TICK_STEPS.find((s) => s * pxPerDay >= minPx) ?? TICK_STEPS[TICK_STEPS.length - 1];
}
/** Days the axis covers: the last end plus room, never less than `minDays`. */
export function viewDays(shocks: readonly UiShock[], minDays = 30): number {
  const last = shocks.length ? Math.max(...shocks.map(endOf)) : 0;
  return Math.max(minDays, Math.ceil(last * 1.15 + 3));
}
export function ticks(days: number, step: number): number[] {
  const out: number[] = [];
  for (let d = 0; d <= days; d += step) out.push(d);
  return out;
}
/** The zoom that fits `days` into `widthPx`. */
export const fitPxPerDay = (days: number, widthPx: number): number => clamp(widthPx / Math.max(1, days), MIN_PX_PER_DAY, MAX_PX_PER_DAY);
export const zoomIn = (px: number): number => clamp(Math.round(px * 1.5 * 100) / 100, MIN_PX_PER_DAY, MAX_PX_PER_DAY);
export const zoomOut = (px: number): number => clamp(Math.round((px / 1.5) * 100) / 100, MIN_PX_PER_DAY, MAX_PX_PER_DAY);
export const dayToX = (d: number, px: number): number => d * px;
export const xToDay = (x: number, px: number): number => x / px;

/* ── dates ──────────────────────────────────────────────────────────── */
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export const isDate = (s: unknown): s is string => typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(Date.parse(`${s}T00:00:00Z`));

/** The UTC date `d` days after `startDate`, as epoch milliseconds. */
export const dateMs = (startDate: string, d: number): number => Date.parse(`${startDate}T00:00:00Z`) + Math.round(d * 86400000);
/** "2 Nov" (UTC); with a time of day when the offset is not a whole day. */
export function dateLabel(startDate: string, d: number): string {
  const t = new Date(dateMs(startDate, d));
  const base = `${t.getUTCDate()} ${MONTHS[t.getUTCMonth()]}`;
  const hh = t.getUTCHours();
  return Number.isInteger(d) || hh === 0 && t.getUTCMinutes() === 0 ? base : `${base} ${String(hh).padStart(2, '0')}:${String(t.getUTCMinutes()).padStart(2, '0')}`;
}
export const dayLabel = (d: number): string => (Number.isInteger(d) ? `${d} d` : `${r4(d)} d`);
export const isoOf = (startDate: string, d = 0): string => new Date(dateMs(startDate, d)).toISOString().replace(/\.\d{3}Z$/, 'Z');
export function dateOnly(iso: string): string { return iso.slice(0, 10); }
