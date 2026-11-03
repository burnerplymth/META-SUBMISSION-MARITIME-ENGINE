import { clampAtWord } from '../../../map/src/components/focus';
import { labelWord } from '../copy';
import { humaniseIds, nodeLabel, reasonCode } from '../format';
import type { GraphData } from '../types';

/* Display helpers shared by the desk panels: ids, enums and evidence
 * strings are mapped to labels at render time. Pure. */

/** Clamp at a word boundary with an ellipsis; `truncated` only when cut. */
export const clampWords = clampAtWord;

const URL_RE = /https?:\/\/[^\s)]+/i;

/** An evidence string ("Publisher compilation: https://...") as a publisher
 *  name plus a link. The raw URL is never meant to be printed. */
export function parseSource(raw: string | null | undefined): { publisher: string; url: string | null } {
  const text = typeof raw === 'string' ? raw.trim() : '';
  const m = URL_RE.exec(text);
  const url = m ? m[0].replace(/[.,;]+$/, '') : null;
  let publisher = (m ? text.slice(0, m.index) : text)
    .replace(/[\s:;,–—-]+$/, '')
    .replace(/\s+compilation$/i, '')
    .trim();
  if (!publisher && url) {
    try { publisher = new URL(url).hostname.replace(/^www\./, ''); } catch { /* keep empty */ }
  }
  return { publisher: publisher || 'Source', url };
}

const PLACE_ID = /^(sea|air|gnd|chk|ground)_[a-z0-9_]+$/;
const ENUM = /^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$/;

/** A served value read as a label: a place id becomes the place name, an
 *  ENUM_CONSTANT becomes sentence case, embedded place ids inside text are
 *  replaced, numbers print as they are, empty values print a dash. */
export function displayValue(v: unknown, graph?: GraphData | null): string {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : '—';
  if (typeof v !== 'string') return '—';
  const t = v.trim();
  if (PLACE_ID.test(t)) return nodeLabel(t, graph);
  if (ENUM.test(t) && /[A-Z]{2}/.test(t)) return labelWord(t);
  return humaniseIds(t, graph);
}

/** An error as a short factual line: a title plus at most one clamped sentence, never a raw
 *  exception, stack, path or id. `ApiError`-shaped values (with a `code`) keep their message. */
export function friendlyError(e: unknown, graph?: GraphData | null): string {
  const msg = e && typeof e === 'object' && 'code' in e && typeof (e as { message?: unknown }).message === 'string'
    ? ((e as unknown) as { message: string }).message : '';
  const first = humaniseIds(msg, graph).split(/(?<=\.)\s|\n/)[0].replace(/\s*\/[\w./-]+/g, '').trim();
  if (!first || /Traceback|Exception|Error:|undefined|null|NaN/.test(first)) return 'Request refused.';
  const c = clampAtWord(first, 100);
  return `Request refused. ${c.text.endsWith('.') || c.truncated ? c.text : `${c.text}.`}`;
}

/** A served feed note as short state words ("Needs key", "Timeout"), never the note's own prose,
 *  environment-variable names or exception text. Empty string when the note carries no known reason. */
export function noteWords(note: string | null | undefined): string {
  const code = reasonCode(note);
  if (!code) return '';
  return code.split(' · ').map((c) => labelWord(c.replace(/-/g, ' '))).join(' · ');
}
