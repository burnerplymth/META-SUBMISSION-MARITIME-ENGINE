/**
 * SEARCH FEATURE REGISTRY — the panels, layouts, map layers and basemaps
 * the advanced search can act on, as static data (the search
 * catalogue). Titles and desk codes are the panels' own, copied
 * from their Panel headers in panels/core.tsx + panels/desk.tsx; layout
 * membership mirrors the store's LAYOUTS table; layout codes mirror
 * App.tsx's LAYOUT_CODE. Keywords are plain-language only — they exist so
 * a user can find a panel by what it does ("backtest", "watchlist"),
 * never by internal vocabulary.
 *
 * Display discipline: no user-facing
 * string in this file uses the banned internal words for the world model
 * or its connections. Internal field names are unaffected.
 *
 * Every entry's action is a descriptor the search box dispatches
 * through the store's existing actions (the interaction actions where one exists:
 * selectNode / openChain / showMoney / showProof; workspace plumbing —
 * setLayout / setFocus / setLens / setLayerVis / setBasemap — otherwise).
 * A panel result whose panel is not in the active layout means: switch
 * to the first layout that contains it, then focus it.
 */
import type { PanelId } from '../types';
import { WINDOWS } from '../windows/registry';

export interface PanelFeature {
  /** Store PanelId the focus action targets (one id per panel —
   *  'chain' = CHN, 'chaintrace' = TRC, 'mapcontent' = Map Content). */
  panelId: PanelId;
  /** Desk code chip, as rendered on the panel header (e.g. 'ALR'). */
  code: string;
  /** Human title, as rendered on the panel header. */
  title: string;
  /** Plain-language keywords (matching only, never displayed). */
  keywords: string[];
  /** Layouts whose panel set includes this panel (store LAYOUTS). */
  layouts: string[];
}

const PANEL_ROWS: PanelFeature[] = [
  { panelId: 'news', code: 'NWS', title: 'DISRUPTION NEWS', layouts: ['SupermAP', 'Market Desk'],
    keywords: ['news', 'disruptions', 'feed', 'headlines', 'alerts feed', 'events', 'warnings', 'weather alerts'] },
  { panelId: 'market', code: 'MKT', title: 'MARKET WATCH', layouts: ['SupermAP', 'Market Desk'],
    keywords: ['market', 'watch', 'prices', 'session', 'indices'] },
  { panelId: 'map', code: 'MAP', title: 'WORLD — ALL MODES', layouts: ['SupermAP', 'Domino Room', 'Place Desk', 'Market Desk', 'Replay', 'Analysis Desks'],
    keywords: ['map', 'world', 'all modes', 'globe', 'chart', 'the map itself'] },
  { panelId: 'mapcontent', code: 'MAP', title: 'MAP CONTENT', layouts: ['SupermAP', 'Domino Room', 'Place Desk', 'Market Desk', 'Replay', 'Analysis Desks'],
    keywords: ['map', 'content', 'selection', 'layers', 'places on the map', 'routes'] },
  { panelId: 'chain', code: 'CHN', title: 'CHAIN TRACE — CAUSAL CHAIN', layouts: ['SupermAP', 'Domino Room', 'Place Desk', 'Replay', 'Proof', 'Analysis Desks'],
    keywords: ['chain', 'trace', 'cascade', 'domino', 'causal chain', 'sequence', 'delays'] },
  { panelId: 'chaintrace', code: 'TRC', title: 'CHAIN — DOMINO TRACE', layouts: ['SupermAP', 'Domino Room', 'Place Desk', 'Replay', 'Proof', 'Analysis Desks'],
    keywords: ['chain', 'domino', 'trace', 'links', 'steps', 'visual'] },
  { panelId: 'whatif', code: 'WIF', title: 'WHAT-IF DESK', layouts: ['SupermAP', 'Domino Room'],
    keywords: ['what-if', 'what if', 'scenario', 'closure', 'simulate', 'fork', 'disruption scenario'] },
  { panelId: 'ensemble', code: 'ENS', title: 'ENSEMBLES — RECURRING BOTTLENECKS', layouts: ['SupermAP', 'Domino Room'],
    keywords: ['ensembles', 'bottlenecks', 'variants', 'recurring', 'spread', 'scenarios compared'] },
  { panelId: 'replay', code: 'RPL', title: 'REPLAY — BACKTEST', layouts: ['Replay'],
    keywords: ['replay', 'backtest', 'past events', 'ever given', 'misses', 'published misses', 'observed', 'model chain', 'episodes', 'baltimore', 'los angeles'] },
  { panelId: 'proof', code: 'PRF', title: 'PROOF — BACKTEST SCORECARD', layouts: ['Proof'],
    keywords: ['proof', 'scorecard', 'replays'] },
  { panelId: 'ledger', code: 'LDG', title: 'CHAIN LEDGER', layouts: ['Replay', 'Proof'],
    keywords: ['ledger', 'chains', 'locked', 'public record', 'graded chains'] },
  { panelId: 'money', code: 'MNY', title: 'MONEY — EXPOSURES', layouts: ['Market Desk'],
    keywords: ['money', 'exposures', 'companies', 'tickers', 'stocks', 'filings', 'sec', 'edgar', 'cost pressure'] },
  { panelId: 'impact', code: 'IMP', title: 'IMPACT — VOLUME, GOODS & EXPOSURE', layouts: ['Market Desk'],
    keywords: ['impact', 'volume at risk', 'goods', 'tonnage', 'trade exposure', 'watchlist', 'freight rates', 'analogues', 'clearance'] },
  { panelId: 'satwatch', code: 'SAT', title: 'SATELLITE WATCH', layouts: ['Market Desk'],
    keywords: ['satellite', 'imagery', 'sentinel', 'scans', 'overhead', 'photos'] },
  { panelId: 'explain', code: 'EXP', title: 'EXPLAIN — STEP INSPECTOR', layouts: ['SupermAP', 'Place Desk'],
    keywords: ['explain', 'inspector', 'step', 'why', 'mechanism', 'detail'] },
  { panelId: 'alerts', code: 'ALR', title: 'ALERTS & WATCHLISTS', layouts: ['SupermAP', 'Place Desk'],
    keywords: ['alerts', 'watchlists', 'rules', 'notify', 'fired', 'watch'] },
  { panelId: 'health', code: 'HLT', title: 'SOURCE HEALTH', layouts: ['SupermAP', 'Place Desk'],
    keywords: ['health', 'sources', 'feeds', 'status', 'live', 'down', 'adapters'] },
  { panelId: 'sim', code: 'SIM', title: 'SIMULATION — LIVING NETWORK', layouts: ['SupermAP', 'Place Desk'],
    keywords: ['simulation', 'simulate', 'scenario player', 'living network', 'traffic', 'vehicles', 'timeline', 'play a disruption'] },
  { panelId: 'desks', code: 'DSK', title: 'ANALYSIS DESKS', layouts: ['Analysis Desks'],
    keywords: ['analysis desks', 'desks', 'why not hit', 'verify chain', 'channels', 'criticality', 'lane', 'reroute', 'replay', 'coverage', 'lens', 'report', 'cost', 'weather', 'eval'] },
  { panelId: 'livetwin', code: 'LTV', title: 'LIVE TWIN', layouts: ['Live Twin'],
    keywords: ['live twin', 'live feeds', 'feeds', 'aircraft near places', 'vessels', 'active disruptions'] },
  { panelId: 'reports', code: 'RPT', title: 'REPORT CENTRE', layouts: ['Report Desk'],
    keywords: ['report', 'reports', 'report centre', 'executive brief', 'full report', 'print', 'pdf', 'html', 'markdown', 'build report'] },
  { panelId: 'exports', code: 'EXP', title: 'EXPORT CENTRE', layouts: ['Report Desk'],
    keywords: ['export', 'exports', 'export centre', 'datasets', 'csv', 'data pack', 'zip', 'download', 'manifest'] },
  { panelId: 'expt', code: 'EXPT', title: 'EXPORT', layouts: ['Report Desk', 'Data Desk'],
    keywords: ['export', 'download', 'save', 'csv', 'xlsx', 'excel', 'pdf', 'pack', 'zip', 'manifest', 'sha256', 'svg', 'png', 'json', 'report', 'charts', 'tables'] },
  { panelId: 'dataviewer', code: 'DAT', title: 'DATA VIEWER', layouts: ['Data Desk'],
    keywords: ['data viewer', 'viewer', 'grid', 'table', 'dataset', 'chart', 'charts', 'visualise', 'visualize', 'explore data', 'rows', 'columns', 'filter', 'sort'] },
  { panelId: 'laneplanner', code: 'LNE', title: 'LANE PLANNER', layouts: ['Lane Desk'],
    keywords: ['lane planner', 'lanes', 'route planner', 'compare routes', 'origin and destination', 'transit time', 'transit range', 'voyage', 'shipping route', 'via suez', 'via the cape', 'cape of good hope', 'panama', 'detour', 'queue wait'] },
  { panelId: 'scenariobuilder', code: 'SCB', title: 'SCENARIO BUILDER', layouts: ['Builder'],
    keywords: ['scenario', 'scenario builder', 'shocks', 'add shock', 'timeline', 'multiple ports', 'several ports', 'many ports', 'delay', 'stagger', 'cluster', 'overlap', 'duration', 'capacity loss', 'combined shocks', 'gantt'] },
  { panelId: 'montecarlo', code: 'MCS', title: 'MONTE CARLO', layouts: [],
    keywords: ['monte carlo', 'simulations', 'bands', 'p10', 'p50', 'p90', 'share of simulations', 'distribution', 'histogram', 'input ranges', 'uncertainty', 'seed', 'compare runs'] },
  { panelId: 'shipments', code: 'SHP', title: 'MY SHIPMENTS', layouts: ['Shipments Desk'],
    keywords: ['my shipments', 'shipments', 'shipment exposure', 'exposure', 'import csv', 'cargo', 'containers', 'teu', 'added transit', 'which shipments', 'affected shipments', 'portfolio'] },
  { panelId: 'watchlists', code: 'WCH', title: 'WATCHLISTS', layouts: ['Watch Desk'],
    keywords: ['watchlists', 'watchlist', 'watch', 'rules', 'triggered', 'alert rules', 'monitor places', 'watched places', 'added transit rule', 'feed state change', 'deviation'] },
  { panelId: 'satdesk', code: 'SAT', title: 'SATELLITE DESK', layouts: ['Satellite Desk'],
    keywords: ['satellite desk', 'satellite', 'anchorages', 'baseline', 'imagery', 'detections', 'vessels at anchor', 'sentinel', 'scan'] },
  { panelId: 'ports', code: 'PRT', title: 'PORTS BOARD', layouts: ['Ports Board'],
    keywords: ['ports', 'places', 'board', 'status', 'tracker', 'capacity', 'chokepoints', 'air hubs', 'border crossings'] },
  { panelId: 'porttwin', code: 'PTW', title: 'PORT TWIN', layouts: ['Port Twin'],
    keywords: ['port twin', 'port map', 'flat map', 'berths', 'quays', 'terminals', 'cranes', 'yards', 'anchorage', 'fairway', 'port simulation', 'berth closure', 'strike', 'surge', 'channel closure', 'yard outage', 'crane', 'sentinel-2', 'openstreetmap', 'osm', 'compare baseline', 'queue', 'waiting', 'berth utilisation'] },
  { panelId: 'research', code: 'RSH', title: 'RESEARCH', layouts: ['Research'],
    keywords: ['research', 'history', 'historical', 'backtest', 'episodes', 'event study', 'before and after', 'lagged correlation', 'change points', 'seasonality', 'ever given', 'baltimore bridge', 'historical series', 'satellite scenes', 'vessel tracks'] },
  { panelId: 'agent', code: 'AGT', title: 'Meta AI', layouts: ['Agent'],
    keywords: ['agent', 'meta ai', 'ask meta ai', 'task', 'assistant', 'plan a task', 'run a task', 'tools', 'steps', 'automate', 'do it for me'] },
  { panelId: 'brief', code: 'BRF', title: 'DISRUPTION BRIEF', layouts: [],
    keywords: ['brief', 'disruption brief', 'report', 'summary', 'one page', 'one-page', 'note', 'write brief', 'institutional note', 'verified statements', 'meta ai brief'] },
  { panelId: 'livefeed', code: 'LVF', title: 'LIVE ENTITIES AND FEEDS', layouts: ['Live Twin'],
    keywords: ['live', 'entities', 'vessels', 'aircraft', 'feed health', 'terminal'] },
  { panelId: 'livetape', code: 'LVT', title: 'LIVE TAPE', layouts: ['Live Twin'],
    keywords: ['live', 'tape', 'alerts', 'stream', 'events'] },
  { panelId: 'livectx', code: 'LVC', title: 'LIVE CONTEXT', layouts: ['Live Twin'],
    keywords: ['live', 'context', 'eia', 'comtrade', 'energy prices', 'trade values'] },
];

/** Search rows: code and title come from the window registry (one declaration per window, so a code
 *  never names two windows); the row's own keywords stay, and its former header title is kept as a
 *  keyword so typing it still finds the window. */
export const PANEL_FEATURES: PanelFeature[] = PANEL_ROWS.map((row) => {
  const w = WINDOWS[row.panelId];
  const former = row.title.toLowerCase();
  return { ...row, code: w.code, title: w.title, keywords: row.keywords.includes(former) ? row.keywords : [...row.keywords, former] };
});

/**
 * The rail tools: HIS, RLS, SCN, PRT and EXP open in the
 * tool slot, not in a layout, so they are their own registry rows. `toolId`
 * is the shell's ToolId (shell/tools.ts); `modes` are the shell modes the
 * tool exists in, mirrored from PLANNED_TOOLS (kept in step with it).
 * Opening one from the palette switches to the first of its modes when the
 * shell is in another mode, then opens the slot. SAT and RPT are layouts
 * with their own panel rows above (Satellite Desk, Report Centre).
 */
export type ToolFeatureId = 'ports' | 'history' | 'realism' | 'scenarios' | 'exports';
export interface ToolFeature {
  toolId: ToolFeatureId;
  code: string;
  title: string;
  modes: Array<'live' | 'sim'>;
  keywords: string[];
}

export const TOOL_FEATURES: ToolFeature[] = [
  { toolId: 'history', code: 'HIS', title: 'History', modes: ['live'],
    keywords: ['history', 'historical', 'past disruptions', 'backplay', 'as of', 'archive', 'ever given', 'suez 2021', 'fork window', 'events in a window', 'time travel'] },
  { toolId: 'realism', code: 'RLS', title: 'Realism', modes: ['live', 'sim'],
    keywords: ['realism', 'backtest', 'stored cards', 'model vs persistence', 'grading rule', 'frozen rule'] },
  { toolId: 'scenarios', code: 'SCN', title: 'Scenario loop', modes: ['sim'],
    keywords: ['scenario', 'scenarios', 'scenario loop', 'shock', 'inject', 'fork', 'compare runs', 'journal', 'verify by replay', 'what if loop', 'closure'] },
  { toolId: 'ports', code: 'PRT', title: 'Ports', modes: ['live'],
    keywords: ['ports', 'port list', 'port directory', 'harbours', 'harbors', 'terminals', 'places directory'] },
  { toolId: 'exports', code: 'EXP', title: 'Exports', modes: ['live', 'sim'],
    keywords: ['exports', 'export', 'datasets', 'csv', 'data pack', 'download data', 'zip'] },
];

export interface LayoutFeature {
  name: string;
  /** What the row reads on screen when it differs from the stored layout key. */
  label?: string;
  /** Function code as shown in the navbar chip (App.tsx LAYOUT_CODE). */
  code: string;
  keywords: string[];
}

export const LAYOUT_FEATURES: LayoutFeature[] = [
  { name: 'SupermAP', code: 'SM001', keywords: ['home', 'main', 'default', 'overview', 'layout'] },
  { name: 'Domino Room', code: 'CHN', keywords: ['domino', 'chains', 'layout', 'scenarios'] },
  { name: 'Place Desk', code: 'PLD', keywords: ['place desk', 'detail', 'layout'] },
  { name: 'Market Desk', code: 'MKT', keywords: ['market', 'money', 'satellite', 'layout'] },
  { name: 'Replay', code: 'RPL', keywords: ['replay', 'backtest', 'past events', 'ledger', 'layout'] },
  { name: 'History', code: 'HIS', keywords: ['history', 'backplay', 'past disruptions', 'layout'] },
  { name: 'Proof', code: 'PRF', keywords: ['proof', 'backtest', 'ledger', 'layout'] },
  { name: 'Analysis Desks', code: 'DSK', keywords: ['analysis desks', 'desks', 'layout'] },
  { name: 'Report Desk', code: 'RPT', keywords: ['report desk', 'reports', 'exports', 'data pack', 'layout'] },
  { name: 'Data Desk', code: 'DAT', keywords: ['data desk', 'data viewer', 'datasets', 'charts', 'grid', 'layout'] },
  { name: 'Satellite Desk', code: 'SAT', keywords: ['satellite desk', 'satellite', 'anchorages', 'imagery', 'layout'] },
  { name: 'Ports Board', code: 'PRT', keywords: ['ports', 'board', 'places', 'tracker', 'layout'] },
  { name: 'Lane Desk', code: 'LNE', keywords: ['lane desk', 'lane planner', 'lanes', 'routes', 'transit time', 'layout'] },
  { name: 'Shipments Desk', code: 'SHP', keywords: ['shipments desk', 'my shipments', 'shipments', 'exposure', 'cargo', 'layout'] },
  { name: 'Watch Desk', code: 'WCH', keywords: ['watch desk', 'watchlists', 'rules', 'triggered', 'layout'] },
  { name: 'Port Twin', code: 'PTW', keywords: ['port twin', 'port map', 'berths', 'quays', 'port simulation', 'layout'] },
  { name: 'Research', code: 'RSH', keywords: ['research', 'history', 'historical', 'backtest', 'episodes', 'event study', 'layout'] },
  { name: 'Agent', label: 'Meta AI', code: 'AGT', keywords: ['agent', 'meta ai', 'task', 'assistant', 'layout'] },
  { name: 'Builder', code: 'SCB', keywords: ['builder', 'scenario builder', 'shocks', 'timeline', 'multiple ports', 'layout'] },
  { name: 'Demo', code: 'DMO', keywords: ['demo', 'showcase', 'map', 'agent', 'chain', 'layout'] },
];

/**
 * The Analysis Desks panel's sections, one row per desk. LANE and
 * REROUTE are ONE desk here (twelve desks in all) — its row's anchor
 * is the LANE section and its keywords carry the reroute words.
 * `code` is the section's own desk code
 * (WHYNOT / VERIFY / CHAN / CRIT / LANE / REPLAY / COVER / LENS /
 * REPORT / COST / WX / EVAL); `title` is the section's <h5> text in
 * panels/desks.tsx verbatim; `anchor` is the stable id on that
 * section's <h5> in panels/desks.tsx, which the desks panel scrolls
 * into view when search hands it the desk key (see navBridge.ts).
 */
export interface DeskFeature {
  /** Bridge key handed to the desks panel (navBridge requestDesk). */
  desk: string;
  /** Desk code chip, as named for the section (e.g. 'CRIT'). */
  code: string;
  /** Human title, as rendered in the section's <h5>. */
  title: string;
  /** Plain-language keywords (matching only, never displayed). */
  keywords: string[];
  /** Stable id of the section's <h5> anchor in panels/desks.tsx. */
  anchor: string;
}

export const DESK_FEATURES: DeskFeature[] = [
  { desk: 'whynot', code: 'WHYNOT', title: 'Why not hit', anchor: 'desk-whynot',
    keywords: ['why not hit', 'why not', 'not hit', 'not reached', 'missed place', 'place the chain passed by'] },
  { desk: 'verify', code: 'VERIFY', title: 'Verify chain', anchor: 'desk-verify',
    keywords: ['verify', 'verify chain', 'check the chain', 'hash match', 'recompute', 'integrity'] },
  { desk: 'chan', code: 'CHAN', title: 'Channels', anchor: 'desk-chan',
    keywords: ['channels', 'how a delay travels', 'queue', 'congestion', 'tick'] },
  { desk: 'crit', code: 'CRIT', title: 'Criticality', anchor: 'desk-crit',
    keywords: ['criticality', 'critical', 'most places hit', 'sweep', 'important places', 'bottleneck places'] },
  { desk: 'lane', code: 'LANE', title: 'Lane', anchor: 'desk-lane',
    keywords: ['lane', 'reroute', 'bypass', 'alternative route', 'another way round', 'single point of failure', 'stress test a route', 'route between places'] },
  { desk: 'replay', code: 'REPLAY', title: 'Replay', anchor: 'desk-replay',
    keywords: ['replay', 'past disruption', 'history', 'step through', 'red sea', 'panama', 'los angeles'] },
  { desk: 'cover', code: 'COVER', title: 'Coverage', anchor: 'desk-cover',
    keywords: ['coverage', 'cover', 'calibration coverage', 'core places', 'shell places', 'paint the map'] },
  { desk: 'lens', code: 'LENS', title: 'Lens', anchor: 'desk-lens',
    keywords: ['lens', 'goods carried', 'which places carry', 'apply to chain'] },
  { desk: 'report', code: 'REPORT', title: 'Report', anchor: 'desk-report',
    keywords: ['report', 'download', 'data pack', 'export the run', 'document'] },
  { desk: 'cost', code: 'COST', title: 'Cost', anchor: 'desk-cost',
    keywords: ['cost', 'spend', 'burn', 'kill switch', 'calls'] },
  { desk: 'wx', code: 'WX', title: 'Weather', anchor: 'desk-wx',
    keywords: ['weather', 'wx', 'storm alert', 'seed a what-if from weather', 'alerts'] },
  { desk: 'eval', code: 'EVAL', title: 'Evaluation', anchor: 'desk-eval',
    keywords: ['eval', 'evaluation', 'labelled set', 'grades', 'model quality'] },
];

/** Map layers (store LayerVis keys) and basemaps (store MapBasemap). */
export interface LayerFeature {
  /** LayerVis key, or `basemap:<key>` for a basemap switch. */
  key: string;
  title: string;
  keywords: string[];
}

export const LAYER_FEATURES: LayerFeature[] = [
  { key: 'lanes', title: 'Shipping lanes', keywords: ['lanes', 'routes', 'sea routes', 'lines', 'layer'] },
  { key: 'activity', title: 'Port activity', keywords: ['activity', 'vessel calls', 'portwatch', 'halos', 'layer'] },
  { key: 'ports', title: 'Ports', keywords: ['ports', 'places', 'markers', 'layer'] },
  { key: 'chokes', title: 'Chokepoints', keywords: ['chokepoints', 'straits', 'canals', 'layer'] },
  { key: 'air', title: 'Air routes', keywords: ['air', 'flights', 'cargo routes', 'layer'] },
  { key: 'basemap:dark', title: 'Dark chart', keywords: ['dark', 'basemap', 'chart', 'map style', 'layer'] },
  { key: 'basemap:light', title: 'Light chart', keywords: ['light', 'basemap', 'chart', 'map style', 'layer'] },
  { key: 'basemap:sat', title: 'Satellite imagery', keywords: ['satellite', 'imagery', 'basemap', 'aerial', 'map style', 'layer'] },
];
