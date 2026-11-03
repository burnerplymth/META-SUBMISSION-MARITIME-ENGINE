/**
 * Deep link and command grammar for the tour. Pure.
 *
 *   #tour=quick|full[&step=N]     N is 1-based, as the step counter shows it
 *   command bar: `tour`, `tour quick`, `tour full`
 *
 * The URL writer keeps only the keys it knows, so the tour reads its own link
 * once at load (captureLoadLink) before the first rewrite.
 */
import type { TourMode } from './engine';

export interface TourLink { mode: TourMode; /** 0-based step index, or null for a link without one. */ index: number | null }

export function parseTourLink(hash: string): TourLink | null {
  const body = hash.startsWith('#') ? hash.slice(1) : hash;
  let mode: TourMode | null = null;
  let step: number | null = null;
  for (const token of body.split('&')) {
    const eq = token.indexOf('=');
    if (eq < 0) continue;
    const key = token.slice(0, eq);
    let val: string;
    try { val = decodeURIComponent(token.slice(eq + 1)); } catch { continue; }
    if (key === 'tour') { if (val === 'quick' || val === 'full') mode = val; else if (val === '1' || val === 'true') mode = 'quick'; }
    else if (key === 'step' && /^\d{1,3}$/.test(val)) { const n = Number(val); if (n >= 1) step = n - 1; }
  }
  return mode ? { mode, index: step } : null;
}

export function tourLinkHash(mode: TourMode, step1?: number): string {
  return `#tour=${mode}${step1 ? `&step=${step1}` : ''}`;
}

/** `tour`, `tour quick`, `tour full` (also `tour start`, `guided tour`); null for any other text. */
export function parseTourCommand(text: string): { mode: TourMode } | null {
  const t = text.trim().toLowerCase().replace(/\s+/g, ' ');
  if (t === 'tour' || t === 'tour start' || t === 'guided tour' || t === 'tour quick') return { mode: 'quick' };
  if (t === 'tour full' || t === 'full tour') return { mode: 'full' };
  if (t === 'quick tour') return { mode: 'quick' };
  return null;
}

/* The link the page was loaded with, read once at import (before urlSync rewrites the hash). */
let loadLink: TourLink | null | undefined;
export function captureLoadLink(hash: string | undefined = typeof location !== 'undefined' ? location.hash : ''): TourLink | null {
  if (loadLink === undefined) loadLink = parseTourLink(hash ?? '');
  return loadLink;
}
/** Consume the load link (once): the provider starts the tour from it after boot. */
export function takeLoadLink(): TourLink | null { const l = captureLoadLink(); loadLink = null; return l; }
