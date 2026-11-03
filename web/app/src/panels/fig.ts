import type { ImpactFigure } from '../types';

/* Figure-slot discipline for the IMPACT payloads.
 * The service legitimately serves SOME figure slots with no figure
 * object at all — not a null-valued figure, the key itself is absent:
 *  · a tier-1 row for a country the daily layer carries no national
 *    total for serves iso3 + places + an ABSENT share figure only —
 *    `affected_tonnage` / `national_tonnage` / `country` are not in
 *    the payload (server/impact/derive.py, the share-only branch);
 *  · a goods sub-profile in the ABSENT state serves no
 *    `total_impaired_volume`, and its `mix` slot is the single ABSENT
 *    figure carrying the reason instead of a per-class record.
 * Renderers must therefore treat a missing figure as NOT SERVED and
 * never read through it (reading `.value` of undefined would take down
 * the whole app). A figure
 * that IS served with a null value is ABSENT and carries its reason;
 * the two states are different facts and render differently. */
export type FigSlot = ImpactFigure | null | undefined;

export type FigState = 'value' | 'absent' | 'not-served';

export const figState = (f: FigSlot): FigState =>
  f == null ? 'not-served' : f.value == null ? 'absent' : 'value';

/** The provenance class a chip may claim for a slot: a missing figure
 *  has no class, so the chip renders nothing beside a NOT SERVED value
 *  rather than inventing a class for it. */
export const figChipClass = (f: FigSlot): string | null => (f == null ? null : f.class);

/** A derivation or reason as short display text, accepting both served shapes: the long sentence
 *  (a string) and the short labelled field (an object carrying `label`, `short`, `code` or `text`).
 *  Anything else yields an empty string, never "undefined" or "[object Object]". */
export function figText(x: unknown): string {
  if (typeof x === 'string') return x;
  if (x && typeof x === 'object') {
    const o = x as Record<string, unknown>;
    for (const k of ['label', 'short', 'text', 'code']) if (typeof o[k] === 'string' && o[k]) return o[k] as string;
  }
  return '';
}
