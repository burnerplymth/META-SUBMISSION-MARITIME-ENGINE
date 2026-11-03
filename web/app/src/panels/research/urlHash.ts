/* The Research window's slice of the URL hash (`rsh=`). A value is one of
 *   ep.<episode id>                      a curated episode
 *   ep.<episode id>.<from>.<to>          an episode with its own dates
 *   pl.<place>+<place>.<from>.<to>       up to five places and a date window
 * Dates are YYYY-MM-DD. Everything read from a URL is untrusted: anything that does not match is dropped. */
import { toMs } from './time';

export const MAX_PLACES = 5;
const ID = /^[A-Za-z0-9_-]{1,64}$/;
const DAY = /^\d{4}-\d{2}-\d{2}$/;

export interface ResearchSel { episode: string | null; places: string[]; from: string | null; to: string | null }
export const EMPTY_SEL: ResearchSel = { episode: null, places: [], from: null, to: null };

export function encodeSel(s: ResearchSel): string | null {
  const dates = s.from && s.to && DAY.test(s.from) && DAY.test(s.to) ? `.${s.from}.${s.to}` : '';
  if (s.episode && ID.test(s.episode)) return `ep.${s.episode}${dates}`;
  const places = s.places.filter((p) => ID.test(p)).slice(0, MAX_PLACES);
  if (places.length && dates) return `pl.${places.join('+')}${dates}`;
  return null;
}

export function decodeSel(v: string | null | undefined): ResearchSel | null {
  if (!v) return null;
  const parts = v.split('.');
  const kind = parts[0];
  const okDate = (s: string | undefined): s is string => !!s && DAY.test(s) && toMs(s) !== null;
  if (kind === 'ep' && parts.length >= 2 && ID.test(parts[1])) {
    const sel: ResearchSel = { episode: parts[1], places: [], from: null, to: null };
    if (parts.length === 4 && okDate(parts[2]) && okDate(parts[3])) { sel.from = parts[2]; sel.to = parts[3]; }
    else if (parts.length !== 2) return null;
    return sel;
  }
  if (kind === 'pl' && parts.length === 4 && okDate(parts[2]) && okDate(parts[3])) {
    const raw = parts[1].split('+');
    const places = raw.filter((p) => ID.test(p));
    if (!places.length || places.length > MAX_PLACES || places.length !== raw.length) return null;
    return { episode: null, places, from: parts[2], to: parts[3] };
  }
  return null;
}

/* The shared value: the store reads it into the URL writer; the panel writes it (replace, never push). */
let cur: string | null = null;
const subs = new Set<() => void>();
export const researchHash = {
  get: (): string | null => cur,
  set(v: string | null): void { if (v === cur) return; cur = v; subs.forEach((f) => f()); },
  /** Boot: take the value the URL carried before the writer first runs. */
  adopt(v: string | null): void { cur = v; subs.forEach((f) => f()); },
  subscribe(f: () => void): () => void { subs.add(f); return () => { subs.delete(f); }; },
};

/** A shareable link to the current state. */
export function researchLink(sel: ResearchSel, base: { origin: string; pathname: string; search: string }): string | null {
  const v = encodeSel(sel);
  return v ? `${base.origin}${base.pathname}${base.search}#rsh=${encodeURIComponent(v)}` : null;
}
