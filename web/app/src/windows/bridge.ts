/**
 * The seam between the store and the window manager (no React, no dockview).
 * The store's workspace actions (setLayout, closePanel, toggleMax, closeMap,
 * restoreMap, setFocus) call `wmBridge.handlers` when a manager is attached,
 * and the manager reports which windows are open back through `report`, so
 * `visible` and `mapHidden` stay truthful for search, the inspector tabs and
 * the rail. With no manager attached (the old grid) every call falls through
 * to the store's own state: nothing changes for the grid.
 */
import type { Preset } from './presets';
import type { WindowId } from './registry';

export interface WmHandlers {
  closeWindow(id: WindowId): void;
  toggleMax(id: WindowId): void;
  hideMap(): void;
  showMap(): void;
  /** Open (or raise) a window; used by search, the command bar and focus. */
  openWindow(id: WindowId, how?: 'dock' | 'float' | 'tab', tabWith?: WindowId): void;
  /** Apply a `preset=` link code, or return to the built-in layout when null. */
  applyCode(code: string | null): void;
  /** AUTO DEMO: capture the dock, force the canonical built-in, restore the presenter's layout. */
  captureLayout(): Preset;
  restoreLayout(p: Preset): void;
  forceCanonical(): void;
}
export interface WmReport { open: WindowId[]; mapHidden: boolean; /** `preset=` code of a modified layout, else null. */ code: string | null }

let handlers: WmHandlers | null = null;
let reporter: ((r: WmReport | null) => void) | null = null;
let last: WmReport | null = null;

export const wmBridge = {
  get handlers(): WmHandlers | null { return handlers; },
  attach(h: WmHandlers): void { handlers = h; },
  detach(): void { handlers = null; last = null; reporter?.(null); },
  /** The store registers its setter once. */
  setReporter(fn: ((r: WmReport | null) => void) | null): void { reporter = fn; if (fn && last) fn(last); },
  report(r: WmReport): void {
    if (last && last.mapHidden === r.mapHidden && last.code === r.code && last.open.length === r.open.length && last.open.every((v, i) => v === r.open[i])) return;
    last = r; reporter?.(r);
  },
  get attached(): boolean { return handlers !== null; },
};
