/**
 * AGENT CLIENT ACTIONS: UI control the server hands to the browser (open,
 * float, dock, tab windows, apply a layout, switch mode, focus a place, show
 * a run). They run through the window controller and the store, exactly like
 * the command bar's verbs. Pure over injected deps so it is testable.
 *
 * The arguments came from a model, so they are validated again here: a window
 * id must be a real, currently available window; a preset id a built-in; a
 * place id must exist in the loaded places. A failed action reports a code
 * and a short summary back to the server (which tells the model).
 */
import { isWindowId, windowAvailable, WINDOWS, type DockSide, type WindowGate, type WindowId } from '../windows/registry';
import { nodeName } from '../seed';
import type { OpenAsk } from '../panels/research/researchNav';
import type { ClientResult } from './types';

export interface ViewDeps {
  gate(): WindowGate;
  isOpen(id: WindowId): boolean;
  isMaximized(id: WindowId): boolean;
  /** Whether the window is a floating window now; absent in view deps that cannot tell. */
  isFloating?(id: WindowId): boolean;
  openWindow(id: WindowId, how: 'dock' | 'float' | 'tab', tabWith?: WindowId): void;
  closeWindow(id: WindowId): void;
  floatWindow(id: WindowId): void;
  dockWindow(id: WindowId, side?: DockSide): void;
  tabWindow(id: WindowId, withId: WindowId): void;
  toggleMaximize(id: WindowId): void;
  /** Apply a built-in layout preset by id; false when there is no such preset. */
  applyPreset(id: string): boolean;
  setMode(m: 'live' | 'sim'): void;
  hasPlace(id: string): boolean;
  /** Select the place and fly the map to it (restores a hidden map). */
  focusPlace(id: string): void;
  /** Open a stored run in the causal chain; false when it could not be loaded. */
  showRun(runId: string): Promise<boolean>;
  /** Scenario builder steps (scenario_open, scenario_organise); absent in view deps that have no builder. */
  scenarioOpen?(args: unknown): ClientResult;
  scenarioOrganise?(args: unknown): Promise<ClientResult>;
  /** Show a stored brief in the Brief window; false when it could not be loaded. */
  openBrief?(briefId: string): Promise<boolean>;
  /** Open the Research window on an episode and tab (open_research); absent in view deps that have no Research window. */
  openResearch?(ask: OpenAsk): void;
}

/** What the browser knows about the task that asked: the agent session, for the Research report tab. */
export interface ActionContext { sessionId?: string | null }

const ok = (summary: string): ClientResult => ({ ok: true, summary });
const fail = (code: string, summary: string): ClientResult => ({ ok: false, code, summary });
const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const PLACE_RE = /^[A-Za-z0-9_-]{1,64}$/;
const RUN_RE = /^[A-Za-z0-9_-]{1,80}$/;
const BRIEF_RE = /^brf_[0-9a-f]{16}$/;
const ID_RE = /^[a-z0-9_:.-]{1,64}$/;
const ASSEMBLY_RE = /^asm_[0-9a-f]{16}$/;
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const isDay = (v: unknown): v is string => typeof v === 'string' && DAY_RE.test(v) && new Date(`${v}T00:00:00Z`).toISOString().startsWith(v);

function win(v: unknown, d: ViewDeps): WindowId | ClientResult {
  if (typeof v !== 'string' || !isWindowId(v)) return fail('unknown_window', 'Window not found');
  if (!windowAvailable(v, d.gate())) return fail('window_unavailable', `${WINDOWS[v].title} is not available`);
  return v;
}
const bad = (r: WindowId | ClientResult): r is ClientResult => typeof r !== 'string';

export async function performClientAction(name: string, rawArgs: unknown, d: ViewDeps, ctx: ActionContext = {}): Promise<ClientResult> {
  const a = isObj(rawArgs) ? rawArgs : {};
  try {
    switch (name) {
      case 'open_window': {
        const w = win(a.window, d); if (bad(w)) return w;
        const how = a.how === 'float' || a.how === 'tab' ? a.how : 'dock';
        let tabWith: WindowId | undefined;
        if (how === 'tab') {
          const t = win(a.tab_with, d); if (bad(t)) return t;
          if (!d.isOpen(t)) d.openWindow(t, 'dock');
          tabWith = t;
        }
        d.openWindow(w, how, tabWith);
        if (!(d.isOpen(w) || w === 'map')) return fail('open_failed', `${WINDOWS[w].title} did not open`);
        /* A float that would cover the controls in use joins the dock instead (workspace manager); report what happened. */
        const placed = how === 'float' && d.isFloating && !d.isFloating(w) ? 'dock' : how;
        return ok(`Opened ${WINDOWS[w].title} (${placed})`);
      }
      case 'arrange_window': {
        const w = win(a.window, d); if (bad(w)) return w;
        const t = WINDOWS[w].title;
        switch (a.action) {
          case 'float': if (d.isOpen(w)) d.floatWindow(w); else d.openWindow(w, 'float'); return ok(`Floated ${t}`);
          case 'dock': if (d.isOpen(w)) d.dockWindow(w); else d.openWindow(w, 'dock'); return ok(`Docked ${t}`);
          case 'tab': {
            const o = win(a.tab_with, d); if (bad(o)) return o;
            if (!d.isOpen(o)) d.openWindow(o, 'dock');
            if (d.isOpen(w)) d.tabWindow(w, o); else d.openWindow(w, 'tab', o);
            return ok(`Tabbed ${t} with ${WINDOWS[o].title}`);
          }
          case 'maximize': if (!d.isOpen(w)) d.openWindow(w, 'dock'); if (!d.isMaximized(w)) d.toggleMaximize(w); return ok(`Maximized ${t}`);
          case 'restore': if (d.isMaximized(w)) d.toggleMaximize(w); return ok(`Restored ${t}`);
          case 'close': d.closeWindow(w); return ok(`Closed ${t}`);
          default: return fail('unknown_action', 'Arrangement not found');
        }
      }
      case 'apply_layout': {
        if (typeof a.preset !== 'string') return fail('unknown_preset', 'Layout not found');
        return d.applyPreset(a.preset) ? ok(`Layout ${a.preset} applied`) : fail('unknown_preset', 'Layout not found');
      }
      case 'set_mode': {
        if (a.mode !== 'live' && a.mode !== 'sim') return fail('unknown_mode', 'Mode not found');
        d.setMode(a.mode);
        return ok(`Mode ${a.mode === 'sim' ? 'simulation' : 'live'}`);
      }
      case 'focus_place': {
        if (typeof a.place_id !== 'string' || !PLACE_RE.test(a.place_id)) return fail('unknown_place', 'Place not found');
        if (!d.hasPlace(a.place_id)) return fail('unknown_place', 'Place not found');
        d.focusPlace(a.place_id);
        return ok(`Focused ${nodeName(a.place_id)} on the map`);
      }
      case 'show_run': {
        if (typeof a.run_id !== 'string' || !RUN_RE.test(a.run_id)) return fail('unknown_run', 'Run not found');
        const opened = await d.showRun(a.run_id);
        return opened ? ok('Opened run in the causal chain') : fail('run_not_loaded', 'Run could not be loaded');
      }
      case 'open_brief': {
        if (typeof a.brief_id !== 'string' || !BRIEF_RE.test(a.brief_id)) return fail('unknown_brief', 'Brief not found');
        if (!d.openBrief) return fail('window_unavailable', 'Disruption brief is not available');
        const shown = await d.openBrief(a.brief_id);
        return shown ? ok('Opened the disruption brief') : fail('brief_not_loaded', 'Brief not loaded');
      }
      case 'open_research': {
        if (!d.openResearch || !windowAvailable('research', d.gate())) return fail('window_unavailable', 'Research is not available');
        const places = a.places === undefined ? [] : a.places;
        if (!Array.isArray(places) || places.length > 5 || places.some((p) => typeof p !== 'string' || !PLACE_RE.test(p) || !d.hasPlace(p))) return fail('unknown_place', 'Place not found');
        if ((a.from === undefined) !== (a.to === undefined)) return fail('invalid_window', 'Dates not valid');
        if (a.from !== undefined && (!isDay(a.from) || !isDay(a.to) || a.to < a.from)) return fail('invalid_window', 'Dates not valid');
        if (a.episode_id !== undefined && (typeof a.episode_id !== 'string' || !ID_RE.test(a.episode_id))) return fail('unknown_episode', 'Episode not found');
        if (a.assembly_id !== undefined && (typeof a.assembly_id !== 'string' || !ASSEMBLY_RE.test(a.assembly_id))) return fail('unknown_assembly', 'Assembly not found');
        if (a.series !== undefined && (!Array.isArray(a.series) || a.series.length > 8 || a.series.some((x) => typeof x !== 'string' || !ID_RE.test(x)))) return fail('invalid_request', 'Series not valid');
        if (a.tab !== undefined && a.tab !== 'workbench' && a.tab !== 'report') return fail('invalid_request', 'Tab not found');
        /* An assembly the agent made in this task is shown with its report; otherwise the workbench. */
        const tab = a.tab === 'workbench' || a.tab === 'report' ? a.tab : a.assembly_id !== undefined && ctx.sessionId ? 'report' : null;
        d.openResearch({
          tab, episodeId: (a.episode_id as string | undefined) ?? null, places: places as string[], from: (a.from as string | undefined) ?? null,
          to: (a.to as string | undefined) ?? null, sessionId: ctx.sessionId ?? null,
        });
        return ok(`Opened Research${tab === 'report' ? ' on the report' : ''}`);
      }
      case 'scenario_open': return d.scenarioOpen ? d.scenarioOpen(a) : fail('unavailable', 'Scenario timeline not available.');
      case 'scenario_organise': return d.scenarioOrganise ? await d.scenarioOrganise(a) : fail('unavailable', 'Scenario timeline not available.');
      default: return fail('unknown_tool', 'Not a view action');
    }
  } catch {
    return fail('view_error', 'View action failed');
  }
}
