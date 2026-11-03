/**
 * Content scope guard: no war or political content is shown on screen.
 *
 * This is a DISPLAY layer. Stored and served artefacts are never edited: some
 * are hash-anchored (replay episodes, scorecards) and the server serves them
 * byte for byte. Here two things happen to text on its way to the screen:
 *
 *  1. NEUTRAL_TEXT: a short fixed list of known strings from pinned data is
 *     shown in a neutral wording (the Red Sea episode as a route diversion; one
 *     episode step without its political cause).
 *  2. holdsOutOfScope / safeExcerpt: a quoted passage that holds war or
 *     political wording is not shown (the filing link stays).
 *
 * OUT_OF_SCOPE_SOURCE is the same expression as server/ports/content_scope.py
 * (the two are kept identical).
 */

export const OUT_OF_SCOPE_SOURCE = String.raw`\b(houthis?|iran(?:ian)?|missiles?|military|militias?|armed conflict|war|warfare|wartime|attacks?|attacked|seiz(?:e|ed|ure|ures)|hostilit(?:y|ies)|ukrain(?:e|ian)|russia(?:n)?|israel(?:i)?|gaza|hamas|terror(?:ism|ist|ists)?|invasion|sanctions?|geopolitic(?:s|al)?|insurgen(?:t|ts|cy)|ceasefire|truce|clashes|blockades?|embargo(?:es)?|piracy|naval|yemen|syria|putin|drones?)\b`;

const OUT_OF_SCOPE = new RegExp(OUT_OF_SCOPE_SOURCE, 'i');

export function holdsOutOfScope(text: unknown): boolean {
  return typeof text === 'string' && OUT_OF_SCOPE.test(text);
}

/** A quoted passage, or null when it holds out-of-scope wording. */
export function safeExcerpt(text: string | null | undefined): string | null {
  if (!text) return null;
  return holdsOutOfScope(text) ? null : text;
}

/** Known strings of pinned data -> neutral wording. Substring replacement. */
export const NEUTRAL_TEXT: ReadonlyArray<readonly [string, string]> = [
  ['Red Sea / Bab el-Mandeb crisis, 2023-2025', 'Red Sea route diversion, 2023-2025'],
];

/** One string in its neutral wording (unchanged when it is not a known one). */
export function neutralText(s: string): string {
  let out = s;
  for (const [from, to] of NEUTRAL_TEXT) {
    if (out.includes(from)) out = out.split(from).join(to);
  }
  return out;
}

/** Neutral wording for every known string inside a freshly parsed JSON value.
 *  Mutates in place and returns the same value (a served body is ours to edit). */
export function neutraliseDoc<T>(v: T): T {
  walk(v);
  return v;
}

function walk(v: unknown): void {
  if (Array.isArray(v)) {
    for (let i = 0; i < v.length; i++) {
      const x = v[i];
      if (typeof x === 'string') { const n = neutralText(x); if (n !== x) v[i] = n; }
      else if (x && typeof x === 'object') walk(x);
    }
  } else if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>;
    for (const k of Object.keys(o)) {
      const x = o[k];
      if (typeof x === 'string') { const n = neutralText(x); if (n !== x) o[k] = n; }
      else if (x && typeof x === 'object') walk(x);
    }
  }
}
