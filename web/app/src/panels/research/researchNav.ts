/* Research window navigation, shared with the rest of the app. A request from outside (the agent's open_research) can
 * arrive before the window is mounted, so it is held here until the panel takes it. Plain module state with a version
 * counter for useSyncExternalStore; no React. */
import type { WorkbenchSection } from './reportModel';
import type { AnalysisKind } from './types';

export type ResearchTab = 'workbench' | 'report';

/** open_research: which episode (or places and window) and, optionally, which tab; the agent session whose report to show. */
export interface OpenAsk {
  tab: ResearchTab | null;
  episodeId: string | null;
  places: string[];
  from: string | null;
  to: string | null;
  sessionId: string | null;
}
/** An evidence chip: show the analysis row or series it cites in the workbench. */
export interface FocusAsk { kind: AnalysisKind | null; series: string | null; section: WorkbenchSection }

let tab: ResearchTab = 'workbench';
let version = 0;
let open: OpenAsk | null = null;
let focus: FocusAsk | null = null;
const subs = new Set<() => void>();
const bump = (): void => { version += 1; subs.forEach((f) => f()); };

export const researchNav = {
  tab: (): ResearchTab => tab,
  version: (): number => version,
  setTab(t: ResearchTab): void { if (t !== tab) { tab = t; bump(); } },
  /** Ask the window to open on an episode and tab. */
  requestOpen(ask: OpenAsk): void { open = ask; if (ask.tab) tab = ask.tab; bump(); },
  takeOpen(): OpenAsk | null { const a = open; open = null; return a; },
  /** Ask the workbench to show what an evidence entry cites. */
  requestFocus(ask: FocusAsk): void { focus = ask; tab = 'workbench'; bump(); },
  takeFocus(): FocusAsk | null { const a = focus; focus = null; return a; },
  subscribe(f: () => void): () => void { subs.add(f); return () => { subs.delete(f); }; },
  /** Reset to the initial state. */
  reset(): void { tab = 'workbench'; open = null; focus = null; version = 0; subs.clear(); },
};
