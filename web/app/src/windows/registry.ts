/**
 * window registry. Pure data: no React, no
 * store. Every window the workspace can show is one row here; the command
 * bar, the function-code bar, the presets and the dock adapter all read it,
 * so a window is declared exactly once.
 *
 * Window ids: today's PanelIds keep their ids (so urlState, search and the
 * store need no migration); the rail tools that open in the right
 * column slot are `tool-<id>` windows; `charts` is the chart dock.
 *
 * Vocabulary: places, legs, causal chain. No node / edge /
 * graph in any title.
 */
import type { PanelId } from '../types';
import type { IconName } from '../ui/Icon';

export type ToolWindowId = 'tool-history' | 'tool-realism' | 'tool-scenarios' | 'tool-exports';
export type WindowId = PanelId | ToolWindowId | 'charts' | 'aiactivity';
export type DockSide = 'left' | 'right' | 'centre' | 'bottom' | 'float';
export type WindowFlag = 'livetwin';
export type WindowMode = 'live' | 'sim';

export interface WindowDef {
  id: WindowId;
  /** Function code: unique, 3 letters, typed in the command bar. */
  code: string;
  /** Extra codes that open the same window (the old chip codes). */
  aliases?: readonly string[];
  /** Sentence-case title. */
  title: string;
  icon: IconName;
  minW: number;
  minH: number;
  /** Default float size. */
  w: number;
  h: number;
  defaultDock: DockSide;
  /** The map is a singleton and can only be hidden, never closed. */
  singleton?: boolean;
  /** Window exists only while this server flag is on (LIVE TWIN law). */
  flag?: WindowFlag;
  /** The window shows model output (carries the Meta AI badge). */
  ai?: boolean;
  /** Plain-language words the command bar matches for `open <words>`. */
  words: readonly string[];
  /** Position in the phone switcher (map first). */
  phoneOrder: number;
  /** Shell mode the window is meant for (omitted = both). */
  mode?: WindowMode;
}

const W = (d: WindowDef): WindowDef => d;

export const WINDOW_LIST: readonly WindowDef[] = [
  W({ id: 'map', code: 'MAP', title: 'Map', icon: 'map', minW: 360, minH: 260, w: 640, h: 420, defaultDock: 'centre', singleton: true, words: ['map', 'world', 'places'], phoneOrder: 0 }),
  W({ id: 'news', code: 'NWS', title: 'News', icon: 'newspaper', minW: 280, minH: 160, w: 380, h: 420, defaultDock: 'left', ai: true, words: ['news', 'disruptions', 'headlines', 'events'], phoneOrder: 1 }),
  W({ id: 'market', code: 'MKT', title: 'Markets', icon: 'chart-line', minW: 280, minH: 160, w: 380, h: 320, defaultDock: 'left', words: ['markets', 'market', 'prices', 'watch'], phoneOrder: 8 }),
  W({ id: 'satwatch', code: 'SWT', title: 'Satellite watch', icon: 'satellite', minW: 280, minH: 160, w: 380, h: 320, defaultDock: 'left', words: ['satellite watch', 'scans', 'imagery'], phoneOrder: 12 }),
  W({ id: 'health', code: 'HLT', title: 'Source health', icon: 'activity', minW: 280, minH: 160, w: 380, h: 300, defaultDock: 'left', words: ['health', 'sources', 'feeds', 'adapters'], phoneOrder: 13 }),
  W({ id: 'mapcontent', code: 'LYR', aliases: ['MPC'], title: 'Map layers', icon: 'layers', minW: 260, minH: 160, w: 340, h: 360, defaultDock: 'right', words: ['layers', 'map content', 'selection'], phoneOrder: 3 }),
  W({ id: 'chain', code: 'CHN', title: 'Causal chain', icon: 'chain', minW: 300, minH: 200, w: 520, h: 360, defaultDock: 'right', words: ['chain', 'causal chain', 'cascade', 'domino'], phoneOrder: 2 }),
  W({ id: 'chaintrace', code: 'TRC', title: 'Chain diagram', icon: 'waypoints', minW: 300, minH: 200, w: 520, h: 360, defaultDock: 'right', words: ['chain diagram', 'trace', 'diagram'], phoneOrder: 4 }),
  W({ id: 'whatif', code: 'WIF', title: 'What-if', icon: 'flask-conical', minW: 320, minH: 220, w: 420, h: 420, defaultDock: 'right', words: ['what-if', 'what if', 'scenario', 'closure'], phoneOrder: 5 }),
  W({ id: 'ensemble', code: 'ENS', title: 'Ensembles', icon: 'chart-bar', minW: 320, minH: 220, w: 420, h: 380, defaultDock: 'right', words: ['ensembles', 'bottlenecks', 'variants'], phoneOrder: 14 }),
  W({ id: 'sim', code: 'SIM', title: 'Simulation', icon: 'play', minW: 320, minH: 220, w: 420, h: 420, defaultDock: 'right', words: ['simulation', 'run control', 'simulate'], phoneOrder: 6 }),
  W({ id: 'impact', code: 'IMP', title: 'Impact', icon: 'package', minW: 300, minH: 160, w: 460, h: 380, defaultDock: 'right', words: ['impact', 'goods', 'volume', 'exposure'], phoneOrder: 7 }),
  W({ id: 'desks', code: 'DSK', title: 'Analysis desks', icon: 'database', minW: 320, minH: 220, w: 520, h: 420, defaultDock: 'right', words: ['analysis desks', 'desks'], phoneOrder: 15 }),
  W({ id: 'proof', code: 'PRF', title: 'Proof', icon: 'shield-check', minW: 300, minH: 180, w: 460, h: 380, defaultDock: 'right', words: ['proof', 'scorecard'], phoneOrder: 16 }),
  W({ id: 'replay', code: 'RPL', title: 'Replay', icon: 'history', minW: 420, minH: 300, w: 820, h: 620, defaultDock: 'right', words: ['replay', 'backtest', 'past events', 'ever given', 'misses'], phoneOrder: 15.5 }),
  W({ id: 'ledger', code: 'LDG', title: 'Chain ledger', icon: 'ledger', minW: 300, minH: 180, w: 460, h: 380, defaultDock: 'right', words: ['ledger', 'chains'], phoneOrder: 17 }),
  W({ id: 'money', code: 'MNY', title: 'Exposures', icon: 'coins', minW: 300, minH: 180, w: 460, h: 380, defaultDock: 'right', words: ['money', 'exposures', 'companies'], phoneOrder: 18 }),
  W({ id: 'explain', code: 'XPL', title: 'Explain', icon: 'info', minW: 300, minH: 180, w: 420, h: 340, defaultDock: 'right', ai: true, words: ['explain', 'inspector', 'step'], phoneOrder: 19 }),
  W({ id: 'alerts', code: 'ALR', title: 'Alerts', icon: 'bell', minW: 300, minH: 180, w: 420, h: 340, defaultDock: 'right', words: ['alerts', 'watchlists'], phoneOrder: 20 }),
  W({ id: 'reports', code: 'RPT', title: 'Reports', icon: 'file-text', minW: 380, minH: 260, w: 560, h: 460, defaultDock: 'right', words: ['reports', 'report'], phoneOrder: 21 }),
  W({ id: 'dataviewer', code: 'DAT', title: 'Data viewer', icon: 'database', minW: 380, minH: 260, w: 640, h: 480, defaultDock: 'right', words: ['data viewer', 'data', 'grid', 'dataset'], phoneOrder: 21.5 }),
  W({ id: 'laneplanner', code: 'LNE', title: 'Lane planner', icon: 'route', minW: 420, minH: 300, w: 760, h: 560, defaultDock: 'right', words: ['lane planner', 'lanes', 'routes', 'transit time', 'voyage'], phoneOrder: 21.6 }),
  W({ id: 'montecarlo', code: 'MCS', title: 'Monte Carlo', icon: 'chart-line', minW: 480, minH: 420, w: 820, h: 660, defaultDock: 'right', words: ['monte carlo', 'simulations', 'bands', 'distribution', 'uncertainty', 'input ranges'], phoneOrder: 21.7 }),
  W({ id: 'shipments', code: 'SHP', title: 'My shipments', icon: 'package', minW: 420, minH: 300, w: 820, h: 580, defaultDock: 'right', words: ['my shipments', 'shipments', 'exposure', 'cargo', 'containers', 'import csv', 'added transit'], phoneOrder: 21.72 }),
  W({ id: 'watchlists', code: 'WCH', title: 'Watchlists', icon: 'bell', minW: 420, minH: 300, w: 820, h: 600, defaultDock: 'right', words: ['watchlists', 'watchlist', 'watch', 'rules', 'triggered', 'alerts', 'monitor'], phoneOrder: 21.8 }),
  W({ id: 'scenariobuilder', code: 'SCB', title: 'Scenario builder', icon: 'rows-2', minW: 420, minH: 300, w: 780, h: 600, defaultDock: 'right', words: ['scenario builder', 'builder', 'shocks', 'timeline', 'multiple ports', 'scenario timeline'], phoneOrder: 21.74 }),
  W({ id: 'exports', code: 'EXP', title: 'Exports', icon: 'file-down', minW: 380, minH: 260, w: 560, h: 460, defaultDock: 'right', words: ['exports', 'export', 'datasets', 'csv'], phoneOrder: 22 }),
  W({ id: 'expt', code: 'EXPT', title: 'Export', icon: 'file-down', minW: 560, minH: 460, w: 900, h: 660, defaultDock: 'right', words: ['export', 'download', 'csv', 'xlsx', 'pdf', 'pack', 'zip', 'save data'], phoneOrder: 22.5 }),
  W({ id: 'satdesk', code: 'SAT', title: 'Satellite desk', icon: 'satellite', minW: 380, minH: 260, w: 560, h: 460, defaultDock: 'right', words: ['satellite desk', 'satellite', 'anchorages'], phoneOrder: 23 }),
  W({ id: 'porttwin', code: 'PTW', title: 'Port twin', icon: 'ship', minW: 420, minH: 300, w: 760, h: 560, defaultDock: 'right', words: ['port twin', 'port map', 'berths', 'quays', 'port simulation'], phoneOrder: 23.5 }),
  W({ id: 'research', code: 'RSH', title: 'Research', icon: 'history', minW: 480, minH: 320, w: 980, h: 640, defaultDock: 'right', words: ['research', 'historical', 'episodes', 'event study', 'historical analysis'], phoneOrder: 23.7 }),
  W({ id: 'agent', code: 'AGT', title: 'Meta AI', icon: 'ai', minW: 360, minH: 300, w: 560, h: 640, defaultDock: 'right', ai: true, words: ['agent', 'meta ai agent', 'task', 'assistant', 'ask meta ai'], phoneOrder: 0.5 }),
  W({ id: 'brief', code: 'BRF', title: 'Disruption brief', icon: 'file-text', minW: 480, minH: 420, w: 780, h: 680, defaultDock: 'right', ai: true, words: ['brief', 'disruption brief', 'report', 'summary', 'one page', 'note'], phoneOrder: 21.2 }),
  W({ id: 'ports', code: 'PRT', title: 'Ports', icon: 'anchor', minW: 300, minH: 180, w: 560, h: 460, defaultDock: 'right', words: ['ports', 'board', 'places'], phoneOrder: 9 }),
  W({ id: 'livetwin', code: 'LTV', title: 'Live twin', icon: 'radio', minW: 300, minH: 180, w: 420, h: 340, defaultDock: 'right', flag: 'livetwin', words: ['live twin'], phoneOrder: 24 }),
  W({ id: 'livefeed', code: 'LVF', title: 'Live feeds', icon: 'signal', minW: 300, minH: 180, w: 420, h: 340, defaultDock: 'right', flag: 'livetwin', words: ['live feeds', 'entities'], phoneOrder: 25 }),
  W({ id: 'livetape', code: 'LVT', title: 'Live tape', icon: 'activity', minW: 300, minH: 180, w: 420, h: 340, defaultDock: 'right', flag: 'livetwin', words: ['live tape', 'tape'], phoneOrder: 26 }),
  W({ id: 'livectx', code: 'LVC', title: 'Live context', icon: 'gauge', minW: 300, minH: 180, w: 420, h: 340, defaultDock: 'right', flag: 'livetwin', words: ['live context'], phoneOrder: 27 }),
  W({ id: 'charts', code: 'CHT', title: 'Charts', icon: 'chart-bar', minW: 300, minH: 160, w: 560, h: 240, defaultDock: 'bottom', words: ['charts', 'chart', 'delays'], phoneOrder: 10 }),
  W({ id: 'aiactivity', code: 'AIA', aliases: ['MAI'], title: 'Meta AI activity', icon: 'ai', minW: 360, minH: 200, w: 1000, h: 400, defaultDock: 'float', words: ['meta ai', 'meta ai activity', 'ai activity', 'model calls', 'ai calls'], phoneOrder: 31 }),
  W({ id: 'tool-history', code: 'HIS', title: 'History', icon: 'history', minW: 380, minH: 260, w: 560, h: 460, defaultDock: 'right', mode: 'live', words: ['history', 'backplay', 'past disruptions'], phoneOrder: 11 }),
  W({ id: 'tool-realism', code: 'RLS', title: 'Realism', icon: 'scale', minW: 380, minH: 260, w: 560, h: 460, defaultDock: 'right', words: ['realism', 'backtest cards'], phoneOrder: 28 }),
  W({ id: 'tool-scenarios', code: 'SCN', title: 'Scenario loop', icon: 'git-fork', minW: 380, minH: 260, w: 560, h: 460, defaultDock: 'right', mode: 'sim', words: ['scenarios', 'scenario loop', 'shock', 'compare'], phoneOrder: 29 }),
  W({ id: 'tool-exports', code: 'DST', title: 'Datasets', icon: 'table', minW: 380, minH: 260, w: 560, h: 460, defaultDock: 'right', words: ['datasets', 'data'], phoneOrder: 30 }),
];

export const WINDOWS: Readonly<Record<WindowId, WindowDef>> =
  Object.freeze(Object.fromEntries(WINDOW_LIST.map((d) => [d.id, d])) as Record<WindowId, WindowDef>);

export const WINDOW_IDS: readonly WindowId[] = WINDOW_LIST.map((d) => d.id);
export const isWindowId = (v: unknown): v is WindowId => typeof v === 'string' && Object.prototype.hasOwnProperty.call(WINDOWS, v);

/** Tool ids (shell/tools.ts ToolId) -> the window that replaces the slot. */
export const TOOL_WINDOW: Readonly<Record<string, WindowId>> = {
  ports: 'ports', history: 'tool-history', realism: 'tool-realism', scenarios: 'tool-scenarios', exports: 'tool-exports',
};
export const TOOL_OF_WINDOW: Readonly<Partial<Record<WindowId, string>>> = {
  'tool-history': 'history', 'tool-realism': 'realism', 'tool-scenarios': 'scenarios', 'tool-exports': 'exports',
};

/** Which windows exist for this server state. LIVE TWIN law: a flagged
 *  window is absent while its flag is off (menus, command bar, search,
 *  presets). Pure, so the law is testable without the store. */
export interface WindowGate { liveTwin: boolean }
export const windowAvailable = (id: WindowId, gate: WindowGate): boolean => {
  const d = WINDOWS[id];
  if (!d) return false;
  return d.flag === 'livetwin' ? gate.liveTwin : true;
};
export const availableWindows = (gate: WindowGate): WindowDef[] => WINDOW_LIST.filter((d) => windowAvailable(d.id, gate));

/** Case-insensitive code lookup (primary code or alias). */
export function windowByCode(code: string, gate: WindowGate): WindowDef | null {
  const c = code.trim().toUpperCase();
  if (!c) return null;
  for (const d of availableWindows(gate)) if (d.code === c || d.aliases?.includes(c)) return d;
  return null;
}

/** Windows whose title or words match a plain-language phrase: exact
 *  title / word first, then unique prefix. Null when none or ambiguous. */
export function windowByWords(text: string, gate: WindowGate): WindowDef | null {
  const t = text.trim().toLowerCase();
  if (!t) return null;
  const pool = availableWindows(gate);
  const exact = pool.filter((d) => d.title.toLowerCase() === t || d.words.includes(t) || d.code.toLowerCase() === t);
  if (exact.length === 1) return exact[0];
  if (exact.length > 1) return exact.find((d) => d.title.toLowerCase() === t) ?? exact[0];
  const pre = pool.filter((d) => d.title.toLowerCase().startsWith(t) || d.words.some((w) => w.startsWith(t)));
  return pre.length === 1 ? pre[0] : null;
}
