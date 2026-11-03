/**
 * Runs a parsed command intent against injected dependencies (the window
 * manager, the store, the appearance hook). Pure of React, so it can be driven
 * with a fake. Returns true when the intent acted.
 */
import type { DockSide, WindowId } from '../windows/registry';
import type { Intent, LayoutOp } from './command';

export interface CommandDeps {
  openWindow(id: WindowId, how: 'dock' | 'float' | 'tab', tabWith?: WindowId): void;
  closeWindow(id: WindowId): void;
  floatWindow(id: WindowId): void;
  dockWindow(id: WindowId, side?: DockSide): void;
  tabWindow(id: WindowId, withId: WindowId): void;
  maximizeWindow(id: WindowId, on: boolean): void;
  /** The window that has focus now (the target of Alt+Enter). */
  activeWindow(): WindowId | null;
  applyLayout(preset: string, user: boolean): void;
  layoutOp(op: LayoutOp, name?: string): void;
  setTheme(v: 'dark' | 'light'): void;
  setDensity(v: 'comfortable' | 'compact'): void;
  setMode(v: 'live' | 'sim'): void;
  exportDataset(id: string): void;
  openMetaAi(): void;
  /** `tour [quick|full]`: start the guided tour. */
  startTour(mode: 'quick' | 'full'): void;
  /** `CODE arg`: select a place (or open a run) and fly to it. */
  focusArg(window: WindowId, arg: string): void;
}
export interface Mods { shift?: boolean; alt?: boolean }

export function runIntent(i: Intent, d: CommandDeps, mods: Mods = {}): boolean {
  switch (i.t) {
    case 'open': {
      const active = d.activeWindow();
      const how = i.how ?? (mods.alt && active && active !== i.window ? 'tab' : mods.shift ? 'float' : 'dock');
      d.openWindow(i.window, how, how === 'tab' ? active ?? undefined : undefined);
      if (i.arg) d.focusArg(i.window, i.arg);
      return true;
    }
    case 'close': d.closeWindow(i.window); return true;
    case 'float': d.floatWindow(i.window); return true;
    case 'dock': d.dockWindow(i.window, i.side); return true;
    case 'tab': d.tabWindow(i.window, i.with); return true;
    case 'maximize': d.maximizeWindow(i.window, true); return true;
    case 'restore': d.maximizeWindow(i.window, false); return true;
    case 'layout-apply': d.applyLayout(i.preset, i.user); return true;
    case 'layout-op': d.layoutOp(i.op, i.name); return true;
    case 'action':
      if (i.action === 'theme' && (i.value === 'dark' || i.value === 'light')) d.setTheme(i.value);
      else if (i.action === 'density' && (i.value === 'comfortable' || i.value === 'compact')) d.setDensity(i.value);
      else if (i.action === 'mode' && (i.value === 'live' || i.value === 'sim')) d.setMode(i.value);
      else if (i.action === 'export' && i.value) d.exportDataset(i.value);
      else if (i.action === 'meta-ai') d.openMetaAi();
      else return false;
      return true;
    case 'tour': d.startTour(i.mode); return true;
    default: return false;
  }
}
