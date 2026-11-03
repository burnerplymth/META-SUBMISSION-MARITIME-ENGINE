/**
 * The one tour of the page: the engine bound to the real host, plus the
 * session it captures at start and restores on exit. React reads it through
 * useSyncExternalStore (TourRoot); the command bar, the top bar and the deep
 * link start it through `startTour`.
 *
 * Exit restores the viewer's own state: layout (the window manager's preset),
 * theme and basemap, shell mode, selection, the chain on screen and the
 * persisted keys the tour touches (demoSession.ts), and it clears the Agent
 * window (the tour's task is not the viewer's).
 */
import { getAgent } from '../agent/singleton';
import type { AgentCheckpoint } from '../agent/controller';
import { api } from '../api';
import { defaultSimRequest, type DemoSessionSnapshot, type useStore } from '../store';
import { applyStep, type ThemeName } from './apply';
import { waitFor } from './dom';
import { TourEngine, type TourMode } from './engine';
import { createHost } from './host';
import { STEPS_BY_MODE } from './steps';
import type { TourStep } from './types';

type StoreT = ReturnType<typeof useStore>;

interface Session {
  snap: DemoSessionSnapshot;
  theme: ThemeName;
  basemap: 'dark' | 'light' | 'sat';
  mode: 'live' | 'sim';
  homeRunId: string | null;
  agent: AgentCheckpoint;
  focus: Element | null;
}

let latest: StoreT | null = null;
let session: Session | null = null;
/** Called by TourRoot on every render so the host always sees the current store. */
export const bindStore = (s: StoreT): void => { latest = s; };
const store = (): StoreT => {
  if (!latest) throw new Error('tour: no store bound');
  return latest;
};

const host = createHost({
  store,
  speed: () => engine.getState().speed,
  baseline: () => ({
    theme: session?.theme ?? (store().theme as ThemeName),
    basemap: session?.basemap ?? store().basemap,
    homeRunId: session ? session.homeRunId : store().chain?.run_id ?? null,
  }),
});

export const engine = new TourEngine<TourStep>({
  steps: STEPS_BY_MODE,
  apply: (step, _i, ctx) => applyStep(step, host, ctx),
  onStart: () => {
    const s = store();
    session = {
      snap: s.captureDemoSession(),
      theme: s.theme as ThemeName, basemap: s.basemap, mode: s.mode,
      homeRunId: s.chain?.run_id ?? null,
      agent: getAgent().checkpoint(),
      focus: typeof document !== 'undefined' ? document.activeElement : null,
    };
  },
  onExit: async () => {
    const ses = session;
    session = null;
    if (!ses) return;
    getAgent().reset();
    getAgent().restore(ses.agent);
    let s = store();
    if (s.mode !== ses.mode) {
      s.setMode(ses.mode);
      await waitFor(() => store().mode === ses.mode, 4000);
      s = store();
    }
    s.restoreDemoSession(ses.snap);
    s.setTheme(ses.theme);
    if (s.basemap !== ses.basemap) s.setBasemap(ses.basemap);
    s.clearError();
    (ses.focus as HTMLElement | null)?.focus?.();
  },
  onError: () => { try { store().clearError(); } catch { /* no store */ } },
});

/**
 * The Full tour shows the simulation fleet on the fixed demo run, which takes
 * about a minute to build, and so does its undisrupted twin when the server has not stored it. Asking for it as
 * the tour opens (idempotent, same id) lets the build finish before step 7.
 */
function warmDemoSim(): void {
  try {
    const req = defaultSimRequest();
    /* the demo run, then its undisrupted twin (the Simulation window loads both) */
    void api.simRun(req).catch(() => { /* the fleet step reports it if it is not served */ });
    void api.simRun({ ...req, disruptions: [] }).catch(() => { /* same */ });
  } catch { /* no network layer */ }
}

/** Start the tour (Quick by default). `index` opens paused on that step (a deep link to one step). */
export function startTour(mode: TourMode = 'quick', index: number | null = null): void {
  if (engine.getState().status !== 'idle') {
    if (engine.getState().mode !== mode) engine.setMode(mode);
    if (index !== null) engine.goTo(index);
    return;
  }
  if (mode === 'full') warmDemoSim();
  engine.start(mode, index ?? 0, { paused: index !== null });
}
