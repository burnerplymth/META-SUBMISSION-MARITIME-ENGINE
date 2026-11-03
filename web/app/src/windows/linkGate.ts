/**
 * Link-group gate for the map's fly-to bridge. A click inside a window
 * records which window it was (LinkScope); for a short moment after it, a
 * fly-to or focus request is honoured only when that window shares the map
 * window's link group. Requests with no recent click (search, the command bar,
 * the URL) are never gated.
 */
import { registerFlyGate } from '../mapFlyTo';
import { linkOf, linkStore } from './linkGroups';

const WINDOW_MS = 800;
let last: { wid: string; t: number } | null = null;

export function noteWindowInteraction(wid: string): void { last = { wid, t: Date.now() }; }
export const clearWindowInteraction = (): void => { last = null; };

/** True when a fly-to request may move the map right now. */
export function mayFlyMap(now = Date.now()): boolean {
  if (!last || now - last.t > WINDOW_MS) return true;
  const ls = linkStore.get();
  const caller = linkOf(ls, last.wid);
  const map = linkOf(ls, 'map');
  return caller !== null && caller === map;
}

export function installFlyGate(): () => void {
  registerFlyGate(() => mayFlyMap());
  return () => registerFlyGate(null);
}
