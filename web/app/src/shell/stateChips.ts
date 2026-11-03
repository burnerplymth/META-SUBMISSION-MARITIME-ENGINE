/* State semantics. Every
 * state has a drawn mark AND a word; colour is never the only carrier.
 * Pure data so the stylesheets can be
 * checked against the table.
 *
 * Four semantic groups plus neutral:
 *   live      ok            solid dot            Live
 *   snapshot  stale         ring                 Snapshot
 *   model|sim derived       half dot | ring+dot  Modelled | Simulated
 *   down|notserved absent   cross | slashed ring Down | Not served
 *   meta      neutral       no mark              (caller's word)
 */

export type StateKind = 'live' | 'snapshot' | 'sim' | 'model' | 'down' | 'notserved' | 'meta';
export type StateGroup = 'ok' | 'stale' | 'derived' | 'absent' | 'neutral';

/** The mark drawn for each state (ui/StateMark.tsx). Never a font glyph. */
export type StateMarkName = 'dot' | 'ring' | 'half' | 'target' | 'cross' | 'slash';
export const STATE_MARK: Record<StateKind, StateMarkName | null> = {
  live: 'dot', snapshot: 'ring', model: 'half', sim: 'target', down: 'cross', notserved: 'slash', meta: null,
};

/** Display words, sentence case. */
export const STATE_LABEL: Record<StateKind, string> = {
  live: 'Live', snapshot: 'Snapshot', sim: 'Simulated', model: 'Modelled', down: 'Down', notserved: 'Not served', meta: '',
};
/** Legacy caps words. Kept so existing callers that compare
 *  against them keep working; Chip renders STATE_LABEL for any word that is
 *  just the legacy caps form of its state. */
export const STATE_WORD: Record<StateKind, string> = {
  live: 'LIVE', snapshot: 'SNAPSHOT', sim: 'SIMULATED', model: 'MODELLED', down: 'DOWN', notserved: 'NOT SERVED', meta: '',
};
export const STATE_GROUP: Record<StateKind, StateGroup> = {
  live: 'ok', snapshot: 'stale', model: 'derived', sim: 'derived', down: 'absent', notserved: 'absent', meta: 'neutral',
};
/** Mark form per state: solid, ring, half, ring with dot, cross, slashed ring. */
export const STATE_FORM: Record<StateKind, 'solid' | 'ring' | 'half' | 'target' | 'cross' | 'slash' | 'plain'> = {
  live: 'solid', snapshot: 'ring', model: 'half', sim: 'target', down: 'cross', notserved: 'slash', meta: 'plain',
};
export const STATE_KINDS = Object.keys(STATE_MARK) as StateKind[];

/** The word a chip shows: an explicit word wins, except that the legacy caps
 *  form of the state's own word is shown in the sentence-case form. Other
 *  caller words (`DATA SNAPSHOT`, `NOAA LIVE`) pass through unchanged. */
/** A word that says work is in flight (Loading, Building, Running...). Its chip is a status region, so a change
 *  from "Building" to the result is announced without moving focus. */
export const isProgressWord = (word: string | undefined | null): boolean =>
  !!word && /^(loading|building|running|evaluating|fetching|pending|working|computing|checking)\b/i.test(word.trim());

export function chipWord(kind: StateKind, word?: string): string {
  if (word === undefined) return STATE_LABEL[kind];
  return STATE_WORD[kind] && word === STATE_WORD[kind] ? STATE_LABEL[kind] : word;
}

/** A source-health adapter state as served ('LIVE' | 'SNAPSHOT' | other)
 *  mapped onto the chip kinds: anything that is not LIVE or SNAPSHOT is DOWN. */
export function adapterStateKind(state: string): StateKind {
  const s = state.toUpperCase();
  return s === 'LIVE' ? 'live' : s === 'SNAPSHOT' ? 'snapshot' : 'down';
}
