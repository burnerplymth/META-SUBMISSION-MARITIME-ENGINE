/**
 * Puts the product in a step's complete target state, from wherever it is.
 * Pure of React and the store: every read and write goes through `TourHost`,
 * so the order of operations and the idempotence law hold for any host.
 *
 * The order matters and is fixed: shell mode, data (the chain on screen),
 * layout and windows, the simulation slice, selection, theme, the Agent task,
 * then the step's UI actions. Each part checks the current state first and
 * skips when it already matches, so applying the same step twice, or a step
 * after any other, ends in the same state.
 */
import type { ApplyCtx } from './engine';
import { clearNotes } from './notes';
import type { TourAction, TourAgent, TourOpen, TourShellMode, TourSim, TourStep } from './types';
import type { WindowId } from '../windows/registry';

export type AgentNow = 'none' | 'confirm' | 'done' | 'busy';
export type RunNow = 'home' | 'suez' | 'other';
export type ThemeName = 'dark' | 'light' | 'retro-dark' | 'retro-light';

/** What the tour may read and do. The real host (host.ts) wraps the store, the window manager and the Agent. */
export interface TourHost {
  /** The viewer's own theme and basemap, captured when the tour started. */
  baseline(): { theme: ThemeName; basemap: 'dark' | 'light' | 'sat' };
  mode(): TourShellMode;
  setMode(m: TourShellMode): Promise<void>;
  theme(): ThemeName;
  setTheme(t: ThemeName): void;
  basemap(): 'dark' | 'light' | 'sat';
  setBasemap(b: 'dark' | 'light' | 'sat'): void;

  layoutId(): string;
  layoutModified(): boolean;
  applyLayout(id: string): Promise<void>;
  openWindows(): readonly WindowId[];
  openWindow(o: TourOpen): void;
  focusWindow(id: WindowId): void;

  selection(): { place: string | null; seq: number | null };
  selectPlace(id: string | null, seq: number | null): void;

  run(): RunNow;
  ensureRun(r: 'home' | 'suez'): Promise<void>;
  sim(): TourSim;
  ensureSim(t: TourSim): Promise<void>;

  agent(): AgentNow;
  runAgent(to: 'none' | 'confirm' | 'done'): Promise<void>;

  act(a: TourAction, ctx: ApplyCtx): Promise<void>;
  /** Wait; resolves early when the tour moved on. Scaled by the playback speed. */
  wait(ms: number, ctx: ApplyCtx): Promise<void>;
}

/** How long the Agent confirmation card stays on screen before the tour confirms it, in ms at 1x. */
export const AGENT_HOLD_MS = 4500;

/** The theme a step wants, given the viewer's own. */
export function wantedTheme(target: TourStep['target'], base: ThemeName): ThemeName {
  if (target.theme !== 'opposite') return base;
  const retro = base.startsWith('retro');
  const light = base.endsWith('light');
  return (retro ? (light ? 'retro-dark' : 'retro-light') : light ? 'dark' : 'light') as ThemeName;
}

export async function applyStep(step: TourStep, host: TourHost, ctx: ApplyCtx): Promise<void> {
  const t = step.target;
  const live = (): boolean => !ctx.isStale();
  clearNotes();

  /* 1 shell mode */
  const mode = t.mode ?? 'live';
  if (host.mode() !== mode) { await host.setMode(mode); if (!live()) return; }

  /* 2 the chain on screen */
  if (t.run !== 'keep' && t.run !== host.run()) { await host.ensureRun(t.run); if (!live()) return; }

  /* 3 layout and windows */
  if (host.layoutId() !== t.layout || host.layoutModified()) { await host.applyLayout(t.layout); if (!live()) return; }
  const open = host.openWindows();
  for (const o of t.open ?? []) if (!open.includes(o.id)) host.openWindow(o);
  if (t.focus) host.focusWindow(t.focus);

  /* 4 the simulation slice (only a simulation step carries one) */
  if (mode === 'sim' && host.sim() !== t.sim) { await host.ensureSim(t.sim); if (!live()) return; }

  /* 5 selection */
  if (t.place !== undefined) {
    const cur = host.selection();
    const seq = t.seq ?? null;
    if (cur.place !== t.place || (t.place !== null && cur.seq !== seq)) host.selectPlace(t.place, seq);
  }

  /* 6 theme and basemap */
  const base = host.baseline();
  const theme = wantedTheme(t, base.theme);
  if (host.theme() !== theme) host.setTheme(theme);
  const basemap = t.basemap && t.basemap !== 'user' ? t.basemap : null;
  if (basemap && host.basemap() !== basemap) host.setBasemap(basemap);
  else if (!basemap && host.theme() === base.theme && host.basemap() !== base.basemap) host.setBasemap(base.basemap);

  /* 7 the recorded Agent task */
  await applyAgent(t.agent, host, ctx);
  if (!live()) return;

  /* 8 UI actions */
  for (const a of t.actions ?? []) { await host.act(a, ctx); if (!live()) return; }
}

async function applyAgent(to: TourAgent, host: TourHost, ctx: ApplyCtx): Promise<void> {
  const now = host.agent();
  if (to === 'none') { if (now !== 'none') await host.runAgent('none'); return; }
  if (to === 'keep') return;
  if (to === 'done') { if (now !== 'done') await host.runAgent('done'); return; }
  /* run: the confirmation card is shown, held, then confirmed by the tour (never by the model) */
  if (now === 'done') return;
  await host.runAgent('confirm');
  if (ctx.isStale() || host.agent() !== 'confirm') return;
  await host.wait(AGENT_HOLD_MS, ctx);
  if (ctx.isStale()) return;
  await host.runAgent('done');
}
