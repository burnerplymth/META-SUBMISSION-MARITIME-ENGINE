/**
 * The SIM → desk restore decision in showDeskMode.
 *
 * Leaving SIM restores the desk exactly as it was ONLY when the chain
 * already on the desk is the mode being asked for (a LIVE chain +
 * click LIVE = restore, no reload). Every other combination must
 * LOAD the requested mode — in particular a SCENARIO chain (any
 * what-if / fork) + click LIVE must run a live chain. A handler
 * that also returned for `mode === 'LIVE' && runMeta.mode !==
 * 'CANNED'` would swallow exactly the SCENARIO case.
 */
export function deskRestoreOnly(
  simActive: boolean,
  currentMode: 'LIVE' | 'CANNED' | 'SCENARIO' | null | undefined,
  target: 'LIVE' | 'CANNED',
): boolean {
  return simActive && currentMode === target;
}

/** Which of the Map header's desk tabs is selected, and what the first tab
 *  is called (truth-label law: the word LIVE needs a feed that is live now).
 *
 *  - SIM active: the SIM tab.
 *  - a CANNED chain: the Canned tab.
 *  - a SCENARIO chain (agent, what-if, fork): neither desk tab, the header
 *    shows a Scenario chip instead; the old default wrongly lit Live.
 *  - the feed desk (or nothing loaded yet): the first tab, reading "Live"
 *    only while the feed is live now, otherwise "Recorded". */
export interface DeskTabView { selected: 'LIVE' | 'CANNED' | 'SIM' | null; liveWord: 'Live' | 'Recorded'; scenario: boolean }
export function deskTabView(
  simActive: boolean,
  runMode: 'LIVE' | 'CANNED' | 'SCENARIO' | null | undefined,
  liveNow: boolean,
): DeskTabView {
  const liveWord = liveNow ? 'Live' : 'Recorded';
  if (simActive) return { selected: 'SIM', liveWord, scenario: false };
  if (runMode === 'CANNED') return { selected: 'CANNED', liveWord, scenario: false };
  if (runMode === 'SCENARIO') return { selected: null, liveWord, scenario: true };
  return { selected: 'LIVE', liveWord, scenario: false };
}
