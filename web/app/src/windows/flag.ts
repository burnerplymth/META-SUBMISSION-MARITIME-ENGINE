/**
 * Staged migration flag. The window manager is the
 * default; the old four-column grid stays reachable for one release with
 * `?wm=0` in the address, or `localStorage supermap.wm = 0`. `?wm=1`
 * forces the manager back. Never throws.
 *
 * The stored value is only ever written here, when `?wm=0` or `?wm=1` is in the
 * address, so a `0` in storage always comes from an explicit request for the
 * grid and `?wm=1` clears it. Nothing else sets it.
 */
export const WM_KEY = 'supermap.wm';
export type WmChoice = 'manager' | 'grid';

export function chooseWorkspace(search: string, stored: string | null): WmChoice {
  const m = /(?:^|[?&])wm=([01])(?:&|$)/.exec(search);
  if (m) return m[1] === '0' ? 'grid' : 'manager';
  return stored === '0' ? 'grid' : 'manager';
}

export function readWorkspaceChoice(): WmChoice {
  try {
    const search = typeof location !== 'undefined' ? location.search : '';
    let stored: string | null = null;
    try { stored = globalThis.localStorage?.getItem(WM_KEY) ?? null; } catch { stored = null; }
    const choice = chooseWorkspace(search, stored);
    if (/(?:^|[?&])wm=[01](?:&|$)/.test(search)) {
      try { if (choice === 'grid') globalThis.localStorage?.setItem(WM_KEY, '0'); else globalThis.localStorage?.removeItem(WM_KEY); } catch { /* storage blocked */ }
    }
    return choice;
  } catch { return 'manager'; }
}
