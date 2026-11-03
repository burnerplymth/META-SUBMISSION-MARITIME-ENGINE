/**
 * The real tour host: reads and writes the product through the same actions a
 * person uses (store actions, the window controller, the Agent controller and
 * the windows' own buttons). No network of its own: the data it asks for comes
 * from the local API and the recorded fixtures. Every wait is bounded by
 * STEP_WAIT_MS: when the local API does not serve in time the step carries on
 * and the caption shows a "Not served" chip (notes.ts).
 */
import { getAgent } from '../agent/singleton';
import { finalAnswer } from '../agent/reducer';
import { defaultSimRequest, type useStore } from '../store';
import { wm } from '../windows/controller';
import type { WindowId } from '../windows/registry';
import type { AgentNow, RunNow, ThemeName, TourHost } from './apply';
import { click, findByText, frames, setInputValue, waitFor, windowEl } from './dom';
import { HOOKS, scenarioTab } from './hooks';
import { markNotServed, noteEpoch } from './notes';
import type { ApplyCtx } from './engine';
import { tourPreset } from './layouts';
import { SUEZ_LABEL, SUEZ_SPEC } from './steps';
import type { TourAction, TourOpen, TourShellMode, TourSim } from './types';

type StoreT = ReturnType<typeof useStore>;

export interface HostDeps {
  /** The latest store value (refreshed every render). */
  store(): StoreT;
  /** Playback speed (waits shrink with it). */
  speed(): number;
  /** The viewer's own session, captured at start. */
  baseline(): { theme: ThemeName; basemap: 'dark' | 'light' | 'sat'; homeRunId: string | null };
}

const sleepMs = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** The longest any one wait for data may take, in ms. */
export const STEP_WAIT_MS = 12000;

/** Resolve to the promise's value, or to `undefined` (and mark the step Not served) after `ms`. */
async function bounded<T>(p: Promise<T>, ms = STEP_WAIT_MS): Promise<T | undefined> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const at = noteEpoch();   /* a wait left over from an earlier step never marks the step now on screen */
  const timeout = new Promise<undefined>((r) => { timer = setTimeout(() => { markNotServed(at); r(undefined); }, ms); });
  try { return await Promise.race([p, timeout]); } finally { if (timer) clearTimeout(timer); }
}
/** `waitFor` capped at STEP_WAIT_MS; a miss marks the step Not served. */
async function need<T>(pred: () => T | null | undefined | false, ctx?: ApplyCtx, ms = STEP_WAIT_MS): Promise<T | null> {
  const at = noteEpoch();
  const v = await waitFor(pred, Math.min(ms, STEP_WAIT_MS), ctx);
  if (!v && !ctx?.isStale()) markNotServed(at);
  return v;
}

/** The recorded Agent task is sent only when the served transport is not live (the tour never spends a live Meta call). */
export const mayRunAgentTask = (transport: string | null | undefined): boolean => String(transport ?? '').toUpperCase() !== 'LIVE';

export function createHost(d: HostDeps): TourHost {
  const S = () => d.store();
  const agent = () => getAgent();

  const agentNow = (): AgentNow => {
    const st = agent().getSnapshot().state;
    if (st.status === 'none') return 'none';
    if (st.status === 'awaiting_confirmation' && st.pending.length > 0) return 'confirm';
    if (st.status === 'running' || st.status === 'awaiting_confirmation' || st.status === 'awaiting_client' || st.busy) return 'busy';
    return 'done';
  };
  const terminal = (s: string) => ['idle', 'stopped', 'failed', 'cancelled', 'interrupted'].includes(s);

  async function runAgent(to: 'none' | 'confirm' | 'done'): Promise<void> {
    const a = agent();
    if (to === 'none') { a.reset(); return; }
    if (to === 'confirm') {
      if (agentNow() === 'confirm') return;
      if (a.getSnapshot().state.status !== 'none') a.reset();
      if (!a.getSnapshot().meta.loaded) await bounded(a.loadMeta());
      /* the tour plays recorded content only: with a live transport it never sends the task */
      if (!mayRunAgentTask(a.getSnapshot().meta.status?.transport_label)) return;
      const task = a.getSnapshot().meta.tasks[0];
      if (!task) return;
      if (!(await bounded(a.send(task, 'typed')))) return;
      await need(() => { const s = a.getSnapshot().state; return (s.status === 'awaiting_confirmation' && s.pending.length > 0) || terminal(s.status); });
      return;
    }
    /* done: confirm every pending action on the viewer's behalf (the model has no way to), then wait for the answer */
    if (agentNow() === 'none') await runAgent('confirm');
    for (let guard = 0; guard < 4; guard += 1) {
      const s = a.getSnapshot().state;
      if (s.status === 'awaiting_confirmation' && s.pending.length > 0 && !s.busy) await bounded(a.confirm(s.pending[0].id));
      const ended = await need(() => { const x = a.getSnapshot().state; return (x.status === 'awaiting_confirmation' && x.pending.length > 0 && !x.busy) || terminal(x.status); });
      if (!ended) return;
      if (terminal(a.getSnapshot().state.status)) break;
    }
    await waitFor(() => finalAnswer(a.getSnapshot().state) !== null || a.getSnapshot().state.status !== 'idle', STEP_WAIT_MS);
  }

  const runNow = (): RunNow => {
    const s = S();
    const c = s.chain;
    if (!c) return 'other';
    if (d.baseline().homeRunId && c.run_id === d.baseline().homeRunId) return 'home';
    if (s.compare === null && c.events[0]?.node_id === SUEZ_SPEC.node_id) return 'suez';
    return 'other';
  };

  return {
    baseline: () => ({ theme: d.baseline().theme, basemap: d.baseline().basemap }),
    mode: () => S().mode,
    async setMode(m: TourShellMode) {
      S().setMode(m);
      await waitFor(() => S().mode === m, 4000);
      await frames(3);
      await sleepMs(120);
    },
    theme: () => S().theme as ThemeName,
    setTheme: (t) => S().setTheme(t),
    basemap: () => S().basemap,
    setBasemap: (b) => S().setBasemap(b),

    layoutId: () => wm.state.presetId,
    layoutModified: () => wm.state.modified,
    async applyLayout(id: string) {
      const p = tourPreset(id);
      if (!p) return;
      wm.apply(p);
      await frames(3);
    },
    openWindows: () => wm.state.open,
    openWindow: (o: TourOpen) => wm.openWindow(o.id, o.how ?? 'dock', o.with),
    focusWindow: (id: WindowId) => wm.focusWindow(id),

    selection: () => ({ place: S().selectedNode, seq: S().selectedSeq }),
    selectPlace: (id, seq) => S().selectNode(id, seq),

    run: runNow,
    async ensureRun(r) {
      if (r === 'home') {
        const id = d.baseline().homeRunId;
        if (id) { const ok = await bounded(S().openChain(id)); if (!ok) S().clearError(); }
        return;
      }
      const id = await bounded(S().runWhatIf(SUEZ_SPEC as never, SUEZ_LABEL));
      if (!id) S().clearError();
    },
    sim: (): TourSim => {
      const s = S();
      return s.simActive && s.simRun ? (s.simVehicleId ? 'vehicle' : 'fleet') : 'none';
    },
    async ensureSim(t: TourSim) {
      const s = S();
      if (t === 'none') {
        if (s.simRun) { s.exitSim(); await waitFor(() => !S().simActive, 4000); await frames(2); S().setMode('sim'); await waitFor(() => S().simActive, 4000); }
        return;
      }
      if (!S().simRun) {
        if (!S().simActive) { S().setMode('sim'); await waitFor(() => S().simActive, 4000); }
        await bounded(S().enterSim(defaultSimRequest()));
        await need(() => S().simRun && S().simState);
      }
      if (t === 'fleet') { if (S().simVehicleId) S().selectSimVehicle(null); return; }
      if (!S().simVehicleId) {
        /* a vessel under way, so its detailed track is not an empty dock */
        const pick = () => (S().simState?.entities ?? []).find((e) => e.mode === 'sea' && e.state === 'EN_ROUTE' && e.lon !== null);
        if (!pick()) { S().simSeek(Math.min(6, Math.max(0, (S().simRun?.ticks ?? 8) - 1))); await waitFor(pick, STEP_WAIT_MS); }
        const v = pick() ?? (S().simState?.entities ?? [])[0];
        if (v) S().selectSimVehicle(v.id);
      }
      await frames(2);
    },

    agent: agentNow,
    runAgent,

    async act(a: TourAction, ctx: ApplyCtx) {
      switch (a.t) {
        case 'wait': await sleepMs(a.ms / d.speed()); return;
        case 'lane-plan': {
          await need(() => windowEl('laneplanner')?.querySelector(HOOKS.lanePlanResult), ctx);
          return;
        }
        case 'satellite': {
          const root = await need(() => windowEl('satdesk')?.querySelector(HOOKS.satRoot) as HTMLElement | null, ctx);
          if (!root) return;
          const btn = await need(() => findByText(HOOKS.satPlace, a.place, root), ctx);
          if (btn && btn.getAttribute('aria-pressed') !== 'true') click(btn);
          if (a.source) { const src = await need(() => findByText(HOOKS.satSource, a.source as string, root), ctx); if (src && src.getAttribute('aria-pressed') !== 'true') click(src); }
          const ai = await need(() => root.querySelector(HOOKS.satAi) as HTMLElement | null, ctx);
          ai?.scrollIntoView({ block: 'center' });
          return;
        }
        case 'port-strike': {
          const win = await need(() => windowEl('porttwin'), ctx);
          if (!win) return;
          if (win.querySelector(HOOKS.portCompare)) return;
          const strike = await need(() => win.querySelector<HTMLElement>(HOOKS.portStrike), ctx);
          if (!strike) return;
          click(strike);
          await frames(2);
          const rate = await waitFor(() => win.querySelector<HTMLInputElement>(HOOKS.portFactor), 10000, ctx);
          if (rate) {
            setInputValue(rate, '0.5');
            rate.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
            await frames(2);
          }
          const run = await need(() => win.querySelector<HTMLElement>(HOOKS.portRun), ctx);
          if (!run) return;
          click(run);
          await need(() => win.querySelector(HOOKS.portCompare), ctx);
          win.querySelector(HOOKS.portKpis)?.scrollIntoView?.({ block: 'center' });
          return;
        }
        case 'fork': {
          if (S().compare) return;
          await S().forkAt(2, { capacity_loss: 0.5, duration_d: 3 });
          S().clearError();
          return;
        }
        case 'scenario-tab': {
          const root = await need(() => windowEl('tool-scenarios'), ctx);
          if (!root) return;
          const tab = await need(() => root.querySelector<HTMLElement>(scenarioTab(a.tab)), ctx);
          click(tab);
          await frames(2);
          return;
        }
      }
    },
    async wait(ms, ctx) {
      const end = Date.now() + ms / d.speed();
      while (Date.now() < end && !ctx.isStale()) await sleepMs(Math.min(120, Math.max(0, end - Date.now())));
    },
  };
}

