/**
 * Map fly-to bridge. The map
 * module exposes flyToNode only through its imperative handle
 * (SupermAPMapHandle); mapReal.tsx registers that handle here as the
 * map mounts and unmounts. Callers outside the map (the search
 * dispatcher) ask for a flight without holding a React ref.
 *
 * If no map is mounted yet — a layout switch is in flight — the
 * request is held briefly and honoured the moment a handle registers;
 * a stale hold (the user has moved on) is dropped, never replayed.
 */
import type { SupermAPMapHandle } from '../../map/src';

/* A closed map must not silently swallow a fly-to. The store
 * registers a restore callback (App shell effect); every focus /
 * fly-to request below invokes it first, so search's "view on map"
 * and the feed/rail place clicks reopen a closed map before the
 * camera moves. Registered by the shell, cleared on unmount. */
let restoreHook: (() => void) | null = null;
export function registerMapRestore(fn: (() => void) | null): void {
  restoreHook = fn;
}
export function requestMapRestore(): void {
  restoreHook?.();
}

/* Link groups: a click in a window that is not in the map's link group
 * must not move the map's camera or selection. The window manager registers a
 * gate (windows/linkGate.ts); with no manager there is none and every request
 * goes through, exactly as before. */
let flyGate: (() => boolean) | null = null;
export function registerFlyGate(fn: (() => boolean) | null): void {
  flyGate = fn;
}

let handle: SupermAPMapHandle | null = null;
let pending: { id: string; at: number } | null = null;
let pendingFocus: { id: string; at: number } | null = null;
const PENDING_TTL_MS = 2000;

export function registerMapHandle(h: SupermAPMapHandle | null): void {
  handle = h;
  if (h && pendingFocus) {
    const held = pendingFocus;
    pendingFocus = null;
    pending = null;
    if (Date.now() - held.at <= PENDING_TTL_MS) h.focusNode(held.id);
    return;
  }
  if (h && pending) {
    const held = pending;
    pending = null;
    if (Date.now() - held.at <= PENDING_TTL_MS) h.flyToNode(held.id);
  }
}

export function flyToNode(id: string): void {
  if (flyGate && !flyGate()) return;
  requestMapRestore();
  if (handle) handle.flyToNode(id);
  else pending = { id, at: Date.now() };
}

/** PLACE FOCUS: engage the map's focus on a place —
 *  search / feed / rail selections route here so the camera flies and
 *  the focus box opens through the one map-owned path. */
export function focusPlaceOnMap(id: string): void {
  if (flyGate && !flyGate()) return;
  requestMapRestore();
  if (handle) handle.focusNode(id);
  else pendingFocus = { id, at: Date.now() };
}
