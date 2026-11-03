import type { Func } from './SubBar';

/** Layouts that none of the sub-bar function tabs stands for. They are
 *  reached from LAYOUTS, the rail or the palette, and light no tab (a lit
 *  MAP there says the wrong place). */
export const TABLESS_LAYOUTS: readonly string[] = ['Live Twin', 'Report Desk', 'Data Desk', 'Satellite Desk', 'Analysis Desks', 'History', 'Lane Desk', 'Shipments Desk', 'Watch Desk', 'Port Twin', 'Agent', 'Demo', 'Research', 'Builder'];

/** The function tab a layout lights, given the tab that was lit before
 *  (HOME, MAP, LEDGER and WHAT-IF share layouts with another tab, so the
 *  previous choice stands there). */
export function funcForLayout(layout: string, prev: Func | null): Func | null {
  if (TABLESS_LAYOUTS.includes(layout)) return null;
  if (layout === 'Proof') return 'PROOF';
  if (layout === 'Replay') return prev === 'LEDGER' ? 'LEDGER' : 'REPLAY';
  if (layout === 'Market Desk') return 'MARKETS';
  if (layout === 'Domino Room') return prev === 'WHAT-IF' ? 'WHAT-IF' : 'CHAINS';
  if (layout === 'Place Desk') return 'CHAINS';
  return prev === 'HOME' || prev === 'MAP' ? prev : 'MAP';
}
