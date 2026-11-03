import type { PanelId } from '../types';

/* LIVE | SIMULATION shell mode.
 * The mode selects the layout FAMILY and the tool panels the
 * shell offers; it never removes the LAYOUTS menu: the other family is
 * listed beneath the current one. Pure module, no store import (the
 * store imports this to register the Simulation layout). */

export type ShellMode = 'live' | 'sim';
export const MODE_KEY = 'supermap.mode';

/** The SIMULATION family's own layout: run control, chain, what-if,
 *  ensemble and impact around the map, source health at left. A static record entry in the
 *  store's LAYOUTS (so URL, storage and window geometry treat it like
 *  every other layout). */
export const SIM_LAYOUT_NAME = 'Simulation';
export const SIM_LAYOUT: PanelId[] = ['health', 'map', 'mapcontent', 'sim', 'chain', 'chaintrace', 'whatif', 'ensemble', 'impact'];

export const LIVE_FAMILY: readonly string[] = ['SupermAP', 'Market Desk', 'Replay', 'History', 'Proof', 'Analysis Desks', 'Domino Room'];
export const SIM_FAMILY: readonly string[] = [SIM_LAYOUT_NAME, 'Place Desk', 'Domino Room'];
export const LIVE_TWIN_NAME = 'Live Twin';

export const MODE_LABEL: Record<ShellMode, string> = { live: 'Now', sim: 'Simulation' };

export function parseMode(raw: unknown): ShellMode | null {
  return raw === 'live' || raw === 'sim' ? raw : null;
}
/** Validated read: anything but a known mode boots as LIVE. */
export function readMode(storage: { get(k: string): string | null }): ShellMode {
  return parseMode(storage.get(MODE_KEY)) ?? 'live';
}

/** The family for a mode. The LIVE TWIN section is part of LIVE only
 *  while its flag is on: flag off means the name is not offered at all. */
export function familyFor(mode: ShellMode, liveTwinEnabled: boolean): string[] {
  if (mode === 'sim') return [...SIM_FAMILY];
  return liveTwinEnabled ? [...LIVE_FAMILY, LIVE_TWIN_NAME] : [...LIVE_FAMILY];
}

/** The LAYOUTS menu content: the current family first, every other
 *  layout beneath it. Nothing is hidden. A family name that is not in
 *  `all` is dropped. */
export function menuLayouts(mode: ShellMode, all: readonly string[], liveTwinEnabled: boolean): { primary: string[]; other: string[] } {
  const fam = familyFor(mode, liveTwinEnabled).filter((n) => all.includes(n));
  return { primary: fam, other: all.filter((n) => !fam.includes(n)) };
}

export const defaultLayoutFor = (mode: ShellMode): string => (mode === 'sim' ? SIM_LAYOUT_NAME : 'SupermAP');

/** The layout to show after the mode changes: the current one if it is
 *  in the new family (Domino Room is in both), else the family default. */
export function layoutAfterMode(mode: ShellMode, current: string, liveTwinEnabled: boolean): string {
  return familyFor(mode, liveTwinEnabled).includes(current) ? current : defaultLayoutFor(mode);
}

/** The mode a sim-slice transition implies. The sim slice entering
 *  (by any path: the old switch, search, a sim= link) makes the shell
 *  SIMULATION; leaving it while in SIMULATION makes it LIVE. Pure on
 *  the previous and next simActive; null = no change. */
export function modeAfterSimTransition(prev: boolean, next: boolean, mode: ShellMode): ShellMode | null {
  if (prev === next) return null;
  if (next && mode !== 'sim') return 'sim';
  if (!next && mode === 'sim') return 'live';
  return null;
}
