import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { errorLine } from './errorText';
import { api, ApiError, onDataMode, type RunOrigin, type SimVehicleDetail } from './api';
import { CANNED_LABEL, CANNED_SPEC, CHAIN_FALLBACK, CHAIN_FALLBACK_LABEL, FEED_FALLBACK, loadGraphFallback } from './seed';
import { nodeLabel, prettyKind, rootEvent } from './format';
import type { Chain, DisruptionSpec, EnsExplorationEntry, FeedEvent, GoodsClass, GraphData, Health, ImpactNode, ImpactRun, LedgerEntry, Lens, LiveTwinDoc, LlmStatus, MoneyExposuresDoc, MoneyNodePanel, PanelId, PortWatchSnapshot, RemoteSource, ReplaySummary, RunCatalogueEntry, SatScan, Scorecard, Theme, Watchlist } from './types';
import { bumpTicket, isCurrentTicket, issueTicket, newTicketBox, type TicketBox } from './requestTicket';
import { createSliceRecovery } from './search/sliceRecovery';
import { deskRestoreOnly } from './deskMode';
import { wmBridge, type WmReport } from './windows/bridge';
import { MODE_KEY, SIM_LAYOUT, SIM_LAYOUT_NAME, layoutAfterMode, modeAfterSimTransition, parseMode, readMode, type ShellMode } from './shell/mode';
import {
  CANONICAL_GEOMETRY, DEFAULT_COLW, PANEL_COLUMN,
  parseColWidths, sanitiseColWidths, serializeColWidths,
  type ColWidths, type LayoutGeometry,
} from './layoutGeometry';
import {
  captureLocalSnapshot, captureScrollSnapshot, collectScrollTargets, restoreLocalSnapshot,
  scheduleScrollRestore, type LocalSnapshot, type ScrollSnapshot, type StorageLike,
} from './demoSession';
import {
  applyUrlTarget, closeAppTab, openAppTab, readLocationUrlState, useSimInflight, useStorageEvents, useUrlSync,
  type SimInflightState,
} from './urlSync';
import {
  EMPTY_URL_STATE, classifyStorageEvent, newTabHash, resolveUrlLayout,
  type HistoryPos, type UrlState,
} from './urlState';
import { researchHash } from './panels/research/urlHash';
import type { SimMarker, SimRunDetail, SimRunRequest, SimState, SimWeatherResponse } from '../../map/src/sim/types';
import type { SimLayers, SimLayerKey } from '../../map/src/sim/layersGate';
import { LIVE_LAYER_DEFAULTS, sanitiseSimLayers, simLayerDefaults, withLayerToggled } from '../../map/src/sim/layersGate';
import { clampTick, isAtEnd } from '../../map/src/sim/playback';
import { liveRunMeta, type LiveRunRef } from './shell/truthLabel';
import {
  advancePlayhead, clampPlayhead, floorTickOf, fmtSimDayTime, interpolatePositions,
  freezeLink, relink, playheadMs,
  type PanelLinkState, type SimGranularity,
} from '../../map/src/sim/phaseD';

/** Linkable SIM panels: each carries its own LINK
 *  toggle on the shared time+selection bus. */
export type SimPanelKey = 'board' | 'queue' | 'chain';
const SIM_PANEL_KEYS: SimPanelKey[] = ['board', 'queue', 'chain'];
const allLinked = (): Record<SimPanelKey, PanelLinkState> =>
  ({ board: { ...relink() }, queue: { ...relink() }, chain: { ...relink() } });

/** Default SIM run = the DEMO configuration: the Suez
 *  closure (the gold-path shock) opening at start + 3 days for 10 days,
 *  day tick × 30, seed 42, weather off — so the path (traffic
 *  flowing → shock lands → queues + diversions → downstream places going
 *  late in sequence) is what plays by default and via the scrubber.
 *  The start is a FIXED anchor (not the clock), so the default run is a
 *  cache hit. The server
 *  builds and pins this exact run (server/sim/demo.py carries the same two
 *  constants and must agree). */
export const DEMO_START_AT = '2026-10-26T00:00:00Z';
export const DEMO_CLOSURE_OPENS_AT = '2026-10-29T00:00:00Z'; // start + 3 days
export function defaultSimRequest(): SimRunRequest {
  return {
    disruptions: [{
      node_id: CANNED_SPEC.node_id, kind: CANNED_SPEC.kind,
      capacity_loss: CANNED_SPEC.capacity_loss, duration_d: 10,
      shape: 'step', started_at: DEMO_CLOSURE_OPENS_AT,
    }],
    tick: 'day', ticks: 30, seed: 42, start_at: DEMO_START_AT, weather: false,
    /* new runs sail the lane polylines (journaled; the SIM panel shows the Lane geometry chip) */
    lane_geometry: true,
  };
}

/** Where a /llm surface stands: no bundled fallback exists for these, so a
 *  failed fetch is 'unreachable' and the UI says n/a rather than inventing
 *  a $0.00 burn or a zero-entry ledger. */
export type LlmSource = 'loading' | 'live' | 'unreachable';
/** Same tri-state for the surfaces (money / satwatch / lenses):
 *  'unreachable' asserts nothing rather than inventing data. */
export type DataSource = LlmSource;

/** The fetch-derived provenance word for a served dataset that is not a
 * feed (the network): SERVED by the real API, SNAPSHOT when the serving
 * layer marks responses as a baked capture (X-Supermap-Data-Mode:
 * SNAPSHOT — see api.ts). A successful fetch never earns LIVE (TRUTH):
 * that word belongs to feed items and goes through shell/truthLabel.ts. */
/** The two short notes the sim panel shows beside the volume-at-risk slot (state labels, not prose). */
export const SIM_NOTE_NO_DISRUPTIONS = 'No disruptions. Baseline is the run itself.';
export const SIM_NOTE_TWIN_FAILED = 'Baseline twin not created.';
export const srcWord = (snapshot: boolean) => (snapshot ? 'SNAPSHOT' : 'SERVED');

export interface CompareState { parent: Chain; child: Chain; divergence: number | null; atSeq: number }
/** The store slice the AUTO DEMO can touch, snapshotted before
 *  the demo starts and restored on any exit (abort or completion) so
 *  the live session is byte-identical afterwards. `local` carries the
 *  demo-touchable localStorage keys verbatim (demoSession.ts). */
export interface DemoSessionSnapshot {
  chain: Chain | null; runMeta: RunMeta | null; compare: CompareState | null;
  layout: string; focusPanel: PanelId | null; closed: PanelId[];
  maximized: PanelId | null; selectedNode: string | null; selectedSeq: number | null;
  moneyNode: string | null; local: LocalSnapshot;
  /** Workspace geometry (column widths + map closed state) —
   *  the demo can resize/close/maximize the map, so the snapshot
   *  carries it and restoreDemoSession puts the presenter's geometry
   *  back; the director forces CANONICAL_GEOMETRY for the run. */
  geometry: LayoutGeometry;
  /** The window manager's dock at capture time (windows/bridge.ts); absent with the old grid. */
  wmLayout?: import('./windows/presets').Preset;
  /** Item 4: DOM scroll offsets (columns + panel bodies, keyed by
   *  stable element identifier — demoSession.ts), captured with the
   *  slice and restored after the layout restore renders. */
  scrolls: ScrollSnapshot;
}
/** `liveRef` (TRUTH): what a desk run was built from, so its title can be
 *  re-derived from the feed's state now (shell/truthLabel.ts `runTitle`);
 *  the stored `label` itself is never LIVE-prefixed. */
export interface RunMeta { label: string; mode: 'LIVE' | 'CANNED' | 'SCENARIO'; spec?: DisruptionSpec; liveRef?: LiveRunRef }

/** The honest run meta for a chain opened by id (history fork,
 *  scenario loop, run catalogue, search) — decided by the chain's
 *  ORIGIN, never inherited from whatever was on screen before. The
 *  journal's live_disruption_id marks a live-seeded run (LIVE, same
 *  label shape as boot); everything else was created from a stated
 *  scenario or a fork and is SCENARIO, labelled with the root place,
 *  the engine revision when the journal gave one, and a short id.
 *  origin === null (journal unavailable) yields the SCENARIO default:
 *  never LIVE styling without evidence. */
export function describeChainRun(chain: Chain, origin: RunOrigin | null, feed: readonly FeedEvent[]): RunMeta {
  const place = chain.events.length ? nodeLabel(rootEvent(chain).node_id) : 'run';
  const liveId = origin?.settings?.live_disruption_id;
  if (liveId) {
    const ev = feed.find((f) => f.id === liveId);
    return ev ? liveRunMeta(ev, nodeLabel(ev.node_id)) : liveRunMeta(null, place);
  }
  const rev = origin?.engine_label ?? (typeof origin?.engine_rev === 'number' ? `ENGINE REV ${origin.engine_rev}` : null);
  const tail = chain.run_id.length > 10 ? chain.run_id.slice(-6) : chain.run_id;
  return { label: `SCENARIO — ${place}${rev ? ` · ${rev}` : ''} · ${tail}`, mode: 'SCENARIO' };
}
/** Basemap mode of the map module (dark/light follow the theme; sat is an
 *  independent basemap — reference imagery, not a theme). */
export type MapBasemap = 'dark' | 'light' | 'sat';
/** Rail layer-group state: wired through the map
 *  adapter into the deck layers — lanes/activity toggles plus node-class
 *  visibility filters over served graph kinds/modes. */
export interface LayerVis { lanes: boolean; activity: boolean; ports: boolean; chokes: boolean; air: boolean }
/** Host-supplied per-place map paint (Analysis Desks): structurally the
 *  map package's PlacePaint — desks set it, the map renders it; null =
 *  unpainted (identical rendering). */
export interface MapPaint { entries: Record<string, { color: string; opacity?: number }>; legend: Array<{ label: string; color: string }> }

/* Every mounted panel owns a distinct PanelId — 'mapcontent'
 * (Map Content rail) beside 'map' (the map section), 'chaintrace'
 * (TRC) beside 'chain' (CHN) — so close/max/focus act on one panel.
 * Each layout lists the new ids exactly where the shared id appeared,
 * so every panel shows in the same layouts as before. */
export const LAYOUTS: Record<string, PanelId[]> = {
  SupermAP: ['news', 'market', 'map', 'mapcontent', 'chain', 'chaintrace', 'whatif', 'ensemble', 'alerts', 'explain', 'health', 'sim'],
  'Domino Room': ['map', 'mapcontent', 'chain', 'chaintrace', 'whatif', 'ensemble'],
  'Place Desk': ['map', 'mapcontent', 'chain', 'chaintrace', 'explain', 'alerts', 'health', 'sim'],
  'Market Desk': ['news', 'market', 'money', 'map', 'mapcontent', 'satwatch', 'impact'],
  Replay: ['map', 'mapcontent', 'chain', 'chaintrace', 'replay', 'ledger'],
  History: ['map', 'mapcontent', 'chain', 'chaintrace', 'proof', 'ledger'],
  Proof: ['proof', 'ledger', 'chain', 'chaintrace'],
  'Analysis Desks': ['map', 'mapcontent', 'chain', 'chaintrace', 'desks'],
  /* SIMULATION family (shell/mode.ts): run control, chain, what-if,
   * ensemble and impact around the map. */
  [SIM_LAYOUT_NAME]: SIM_LAYOUT,
  'Report Desk': ['reports', 'exports', 'expt'],
  'Data Desk': ['dataviewer', 'exports', 'reports', 'expt'],
  'Lane Desk': ['laneplanner'],
  'Shipments Desk': ['shipments'],
  'Watch Desk': ['watchlists'],
  'Satellite Desk': ['satdesk'],
  'Ports Board': ['map', 'mapcontent', 'ports'],
  'Port Twin': ['porttwin'],
  Agent: ['map', 'mapcontent', 'chain', 'chaintrace', 'agent'],
  Research: ['map', 'mapcontent', 'chain', 'chaintrace', 'research'],
  Demo: ['map', 'mapcontent', 'chain', 'chaintrace', 'agent'],
  Builder: ['map', 'mapcontent', 'scenariobuilder', 'chain', 'chaintrace'],
};
export const LAYOUT_NAMES = Object.keys(LAYOUTS);

/* LIVE TWIN section: a flag-gated layout that exists only
 * while the server flag is on. It is deliberately NOT a member of
 * LAYOUTS — the static record drives readLayout validation and every
 * other layout consumer, and the section must be wholly absent (no
 * menu entry, no panel, no placeholder) when the flag is off. The
 * resolver below is the single place the name resolves: enabled →
 * its panels; unresolved or disabled → the default layout's panels,
 * so a pending 'Live Twin' value never flashes the section. */
export const LIVE_TWIN_LAYOUT_NAME = 'Live Twin';
export const LIVE_TWIN_LAYOUT: PanelId[] = ['map', 'mapcontent', 'livetwin', 'livefeed', 'livetape', 'livectx'];
export function layoutPanelsFor(name: string, liveTwinEnabled: boolean): PanelId[] {
  if (name === LIVE_TWIN_LAYOUT_NAME) return liveTwinEnabled ? LIVE_TWIN_LAYOUT : LAYOUTS.SupermAP;
  return LAYOUTS[name] ?? LAYOUTS.SupermAP;
}

/* ONE safe storage wrapper + ONE validated read choke point.
 * Every localStorage access in this module routes through safeStorage,
 * whose operations NEVER throw — a blocked / private-mode / quota-full
 * storage must not white-screen the first render. Stored values are
 * untrusted input: readTheme() returns a member of the Theme union or
 * the 'dark' default, and readLayout() an actual layout name or the
 * default layout; both the theme and the basemap initialisers read the
 * theme through readTheme() (there is no second independent reader). */
export const safeStorage = {
  get(key: string): string | null {
    try { return globalThis.localStorage?.getItem(key) ?? null; } catch { return null; }
  },
  set(key: string, value: string): void {
    try { globalThis.localStorage?.setItem(key, value); } catch { /* blocked storage: session state still works */ }
  },
  remove(key: string): void {
    try { globalThis.localStorage?.removeItem(key); } catch { /* blocked storage: nothing to remove */ }
  },
};
/** StorageLike adapter over safeStorage, for the demoSession
 *  snapshot/restore helpers — they now route through the same
 *  never-throwing wrapper instead of raw localStorage. */
export const safeLocalStorage: StorageLike = {
  getItem: (key) => safeStorage.get(key),
  setItem: (key, value) => safeStorage.set(key, value),
  removeItem: (key) => safeStorage.remove(key),
};
/** The validated theme read: the four Theme values pass through,
 *  ANYTHING else (garbage like 'neon', a missing key, a throwing
 *  store) boots as 'dark'. */
export function readTheme(storage: Pick<StorageLike, 'getItem'> = safeLocalStorage): Theme {
  let raw: string | null = null;
  try { raw = storage.getItem('supermap.theme'); } catch { raw = null; }
  return raw === 'light' || raw === 'dark' || raw === 'retro-dark' || raw === 'retro-light' ? raw : 'dark';
}
/** Theme → paired basemap family. The retro themes ride the
 *  institutional basemaps (retro-dark ↔ dark tiles, retro-light ↔
 *  light tiles); sat is never a pairing, only an explicit choice. */
export function basemapForTheme(t: Theme): 'dark' | 'light' {
  return t === 'light' || t === 'retro-light' ? 'light' : 'dark';
}
/** Basemap → theme, preserving the family the user is in: a
 *  basemap switch inside a retro theme lands on that family's retro
 *  variant, inside an institutional theme on the plain variant. */
export function themeForBasemap(current: Theme, m: 'dark' | 'light'): Theme {
  const retro = current === 'retro-dark' || current === 'retro-light';
  return retro ? (m === 'light' ? 'retro-light' : 'retro-dark') : m;
}
/** The basemap's initial mode derives from the SAME validated theme
 *  read (sat is only ever entered explicitly, never at boot). */
export function readBasemap(storage: Pick<StorageLike, 'getItem'> = safeLocalStorage): MapBasemap {
  return basemapForTheme(readTheme(storage));
}
/** The validated layout read: a known layout name passes through, the
 *  pre-rename 'Node Desk' migrates to 'Place Desk', and anything else
 *  (garbage, missing, throwing store) boots as the default layout. */
/* The sim state cache is an LRU bounded at 64 entries — it was
 * an unbounded Map cleared only on run load/exit, so a long scrub
 * session grew it without limit. Reads and writes both refresh
 * recency; eviction drops the least-recently-used tick payload. */
export const SIM_STATE_CACHE_MAX = 64;
export function cacheSimState<T>(cache: Map<string, T>, key: string, st: T): void {
  if (cache.has(key)) cache.delete(key);
  cache.set(key, st);
  while (cache.size > SIM_STATE_CACHE_MAX) {
    const oldest = cache.keys().next().value as string | undefined;
    if (oldest === undefined) break;
    cache.delete(oldest);
  }
}
export function readSimCache<T>(cache: Map<string, T>, key: string): T | undefined {
  const hit = cache.get(key);
  if (hit) { cache.delete(key); cache.set(key, hit); }
  return hit;
}

/* Boot hero selection. File order is OLDEST first, so liveFeed[0]
 * is not used. The selection uses ONLY served fields: FeedEvent.severity + feed order (from
 * GET /events), RunReceipt.event_count (the run summary served by
 * POST /run), and the fetched Chain's events (its size and its
 * distinct channels — the propagation modes the engine served).
 * A chain QUALIFIES at >= 8 events across >= 2 modes (the Suez-class
 * chain, canned or live, is the qualifying shape). */
export interface BootChainCandidate { item: FeedEvent; chain: Chain; receiptEvents: number }
export const BOOT_CHAIN_MIN_EVENTS = 8;
export const BOOT_CHAIN_MIN_MODES = 2;
export function bootChainModeCount(chain: Chain): number {
  return new Set(chain.events.map((e) => e.channel)).size;
}
export function bootChainSize(c: BootChainCandidate): number {
  return c.receiptEvents > 0 ? c.receiptEvents : (c.chain.event_count || c.chain.events.length);
}
export function bootChainQualifies(c: BootChainCandidate): boolean {
  return bootChainSize(c) >= BOOT_CHAIN_MIN_EVENTS && bootChainModeCount(c.chain) >= BOOT_CHAIN_MIN_MODES;
}
/** Highest-severity feed item; ties keep feed order (earliest wins). */
export function highestSeverityFeedItem(feed: FeedEvent[]): FeedEvent | null {
  let best: FeedEvent | null = null;
  for (const f of feed) if (!best || f.severity > best.severity) best = f;
  return best;
}
/** Pick the boot hero from the candidates actually run at boot:
 *  the largest QUALIFYING chain wins (ties: higher severity, then
 *  earlier feed order); when none qualifies, fall back to the
 *  highest-severity feed item's chain. */
export function selectBootChain(candidates: BootChainCandidate[], feed: FeedEvent[]): BootChainCandidate | null {
  if (!candidates.length) return null;
  const order = new Map(feed.map((f, i) => [f.id, i]));
  const byRank = (a: BootChainCandidate, b: BootChainCandidate) =>
    (bootChainSize(b) - bootChainSize(a))
    || (b.item.severity - a.item.severity)
    || ((order.get(a.item.id) ?? 0) - (order.get(b.item.id) ?? 0));
  const qualifying = candidates.filter(bootChainQualifies);
  if (qualifying.length) return [...qualifying].sort(byRank)[0];
  const fallbackItem = highestSeverityFeedItem(feed);
  return candidates.find((c) => c.item.id === fallbackItem?.id)
    ?? [...candidates].sort((a, b) => (b.item.severity - a.item.severity) || ((order.get(a.item.id) ?? 0) - (order.get(b.item.id) ?? 0)))[0];
}

/* The boot walk's candidate queue, from served facts only.
 * A feed item is eligible when (a) its place exists in the loaded
 * network and is not a SHELL-tier place (the chain engine runs the
 * CORE view: a shell place is refused unknown_node), and (b) its
 * disruption is not resolved — by its own status or by a later
 * lifecycle line of the same event (id / dedupe_key). Eligible
 * items are de-duplicated per place+kind, ordered by severity
 * (ties: feed order) and capped, so selectBootChain compares a
 * bounded handful and never a whole feed. nodes === null (network
 * unavailable) applies no place filter: the engine decides. */
export interface BootNetNode { id: string; tier?: string }
export const BOOT_MAX_CANDIDATES = 3;
export function bootCandidateQueue(feed: FeedEvent[], nodes: readonly BootNetNode[] | null, max = BOOT_MAX_CANDIDATES): FeedEvent[] {
  const tierOf = nodes ? new Map(nodes.map((n) => [n.id, n.tier])) : null;
  const lastStatus = new Map<string, string>();
  for (const f of feed) lastStatus.set(f.dedupe_key || f.id, String(f.status).toLowerCase());
  const seen = new Set<string>();
  return feed
    .map((f, i) => ({ f, i }))
    .sort((a, b) => (b.f.severity - a.f.severity) || (a.i - b.i))
    .map((x) => x.f)
    .filter((f) => {
      if (tierOf && (!tierOf.has(f.node_id) || tierOf.get(f.node_id) === 'shell')) return false;
      if (String(f.status).toLowerCase() === 'resolved' || lastStatus.get(f.dedupe_key || f.id) === 'resolved') return false;
      const k = `${f.node_id}|${f.kind}`;
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    })
    .slice(0, Math.max(1, max));
}

/** The validated column-widths read — the same choke-point
 *  discipline as readTheme/readLayout: a stored JSON widths record
 *  is sanitised field by field (garbage → canonical default, out of
 *  range → clamped); a missing key or a throwing store boots at the
 *  canonical widths. */
export function readColWidths(storage: Pick<StorageLike, 'getItem'> = safeLocalStorage): ColWidths {
  let raw: string | null = null;
  try { raw = storage.getItem('supermap.colw'); } catch { raw = null; }
  return parseColWidths(raw);
}

export function readLayout(storage: Pick<StorageLike, 'getItem'> = safeLocalStorage): string {
  let saved: string | null = null;
  try { saved = storage.getItem('supermap.layout'); } catch { saved = null; }
  if (saved === 'Node Desk') return 'Place Desk';
  /* A stored 'Live Twin' is accepted as a PENDING value only: the
   * livetwin boot fetch coerces it to 'SupermAP' if the flag resolves
   * off, and layoutPanelsFor renders it as 'SupermAP' until then. */
  if (saved === LIVE_TWIN_LAYOUT_NAME) return LIVE_TWIN_LAYOUT_NAME;
  return saved && Object.prototype.hasOwnProperty.call(LAYOUTS, saved) ? saved : 'SupermAP';
}

/** Version of the persisted layout keys (`supermap.layout`, `supermap.colw`, `supermap.sim.layers`). Keys written
 *  before the version existed are normalised once: a known value is kept in canonical form, anything else is dropped,
 *  so the next read boots from the defaults. */
export const LAYOUT_SCHEMA_KEY = 'supermap.layout.schema';
export const LAYOUT_SCHEMA = '1';
export function migrateLayoutStorage(st: { getItem(k: string): string | null; setItem(k: string, v: string): void; removeItem(k: string): void }): void {
  try {
    if (st.getItem(LAYOUT_SCHEMA_KEY) === LAYOUT_SCHEMA) return;
    const layout = st.getItem('supermap.layout');
    if (layout !== null) {
      const name = readLayout({ getItem: () => layout });
      if (name === layout) { /* already canonical */ } else if (layout === 'Node Desk' || layout === LIVE_TWIN_LAYOUT_NAME) st.setItem('supermap.layout', name); else st.removeItem('supermap.layout');
    }
    const colw = st.getItem('supermap.colw');
    if (colw !== null) {
      let ok = true; try { JSON.parse(colw); } catch { ok = false; }
      if (ok) st.setItem('supermap.colw', serializeColWidths(parseColWidths(colw))); else st.removeItem('supermap.colw');
    }
    const lay = st.getItem('supermap.sim.layers');
    if (lay !== null) {
      try { st.setItem('supermap.sim.layers', JSON.stringify(sanitiseSimLayers(JSON.parse(lay), LIVE_LAYER_DEFAULTS))); } catch { st.removeItem('supermap.sim.layers'); }
    }
    st.setItem(LAYOUT_SCHEMA_KEY, LAYOUT_SCHEMA);
  } catch { /* blocked storage: the readers sanitise on their own */ }
}
let layoutMigrated = false;

interface Store {
  /** Shell mode (LIVE | SIMULATION): persisted, URL-synced; the
   *  sim slice entering/leaving by any path moves it too. */
  mode: ShellMode; setMode: (m: ShellMode, opts?: { keepLayout?: boolean }) => void;
  theme: Theme; selectedNode: string | null; selectedSeq: number | null;
  /** The map's place card: closed at boot (the boot chain selects a place without opening it), opened by a user selection. */
  placeCardOpen: boolean;
  chain: Chain | null; runMeta: RunMeta | null; busy: string | null; error: string | null;
  health: Health | null; scorecard: Scorecard | null; latencyMs: number | null;
  // Runtime datasets (GET /events + GET /graph). Initial values are the bundled
  // fallbacks with source 'loading'; source flips to 'live' on fetch success or
  // 'fallback' on failure — the UI shows the OFFLINE COPY chip only in 'fallback'.
  feed: FeedEvent[]; feedSource: RemoteSource; feedInvalid: number;
  graph: GraphData; graphSource: RemoteSource;
  // LLM surfaces (GET /llm/status + GET /llm/ledger), fetched at boot.
  llmStatus: LlmStatus | null; llmStatusSource: LlmSource;
  ledger: LedgerEntry[] | null; ledgerSource: LlmSource;
  // Surfaces: money panel (per-node, fetched on demand), the full
  // exposures doc (boot, for filing enrichment), satwatch scans (boot) and
  // the lenses (boot). activeLens is client-only view state.
  /** Fired-alert count (GET /alerts/feed at boot) for the util bar's
   *  ALERTS (n); null = not served yet / unreachable (renders bare ALERTS). */
  alertsCount: number | null;
  // The simulation slice — fully separable from the
  // LIVE/CANNED chain state above. Entering/leaving SIM never touches
  // chain/runMeta/feed/graph; the tick is an index into the run's
  // sampled states and every position renders from simState payloads.
  // The mode switch + SIM panel build on these exact actions.
  simActive: boolean; simRun: SimRunDetail | null; simState: SimState | null;
  simWeather: SimWeatherResponse | null; simTick: number; simPlaying: boolean;
  /** Layer record, persisted in localStorage (layout store);
   *  SATELLITE is derived from the basemap at read time (see provider). */
  simLayers: SimLayers; setSimLayer: (key: SimLayerKey, on: boolean) => void;
  enterSim: (req: SimRunRequest) => Promise<void>; exitSim: () => void;
  simPlay: () => void; simPause: () => void; simStep: (delta: number) => void;
  simSeek: (tick: number) => void; setSimTick: (tick: number) => void;
  // The timeline + link bus + VaR twin.
  /** Fractional playhead in tick units; the exact state in force is
   *  its floor tick's. simState is the DISPLAY state (positions
   *  interpolated between adjacent exact ticks when fractional);
   *  simStateExact is the floor tick's payload verbatim — boards, VaR
   *  and chain sync derive from it, never from the interpolated one. */
  simPlayhead: number; simStateExact: SimState | null;
  setSimPlayhead: (playhead: number) => void;
  simStepGranularity: (dir: 1 | -1) => void;
  simSpeed: number; setSimSpeed: (speed: number) => void;
  simGranularity: SimGranularity; setSimGranularity: (g: SimGranularity) => void;
  /** Markers for the loaded run (null = not served / no run). */
  simMarkers: SimMarker[] | null; simMarkersTruncated: boolean;
  /** Chain sync (default OFF — markers only). */
  simChainSync: boolean; setSimChainSync: (on: boolean) => void;
  /** The chain synced from SIM lives HERE, in the sim slice —
   *  never in the desk `chain` slice. sim.tsx's chain sync
   *  goes through openSimSyncedChain() and reads this field; writing it
   *  into the desk slice (openChain) would replace the desk chain and
   *  exitSim — clearing only the sim slice — would leave the synced
   *  chain behind as the desk chain. */
  simSyncedChain: Chain | null;
  openSimSyncedChain: (runId: string) => Promise<boolean>;
  /** The desk chain's run_id recorded when SIM was entered
   *  (null = the desk held no chain). exitSim restores the recorded
   *  desk chain byte-identically (the same object), whatever interim
   *  writes touched the desk slice while SIM was open. */
  simEntryDeskChainId: string | null;
  /** Per-panel LINK states on the shared bus. */
  simLinks: Record<SimPanelKey, PanelLinkState>;
  toggleSimLink: (key: SimPanelKey) => void;
  /** Exact state at an arbitrary tick of the loaded run (the SAME
   *  /sim/state source + session cache the master uses — panels frozen
   *  at a tick derive from this, never a private fetch loop). */
  fetchSimStateAt: (tick: number) => Promise<SimState | null>;
  /** The map-picked vehicle's entity id (null = none
   *  picked). Selection is host-owned — the map only reports picks
   *  through onSimVehiclePick; the id clears on exitSim and on run
   *  change, like every other per-run field. */
  simVehicleId: string | null;
  selectSimVehicle: (id: string | null) => void;
  /** One vehicle's MODELLED identity + its state entry at a tick of
   *  the loaded run (GET /sim/run/{id}/vehicle/{entity}?tick=N),
   *  session-cached by (run, entity, tick) in the same bounded-LRU
   *  discipline as the state cache. Null when no run is loaded, no
   *  vehicle is selected, or the fetch fails — the inspector then
   *  renders nothing rather than guessing. */
  fetchSimVehicleAt: (tick: number) => Promise<SimVehicleDetail | null>;
  /** Enter the SIM shell with no run loaded (honest empty timeline). */
  enterSimShell: () => void;
  /** Run creation: like enterSim, plus an undisrupted
   *  twin when the request carries disruptions. Resolves to an error
   *  line in the standard shape ("[code] message") or null on success —
   *  the SIM panel surfaces it verbatim, honestly. */
  createSimRun: (req: SimRunRequest) => Promise<string | null>;
  /** Baseline twin for the loaded run + its exact state at the
   *  master floor tick. simVarNote explains a reserved VaR slot. */
  simBaselineRun: SimRunDetail | null; simBaselineState: SimState | null;
  simVarNote: string | null;
  /** Create-then-poll: a run creation the server answered
   *  202 (building) — its id + when the build was first reported.
   *  Set from the settled helper's onBuilding, cleared when the run
   *  loads, when creation fails, and on exitSim. Null = no build in
   *  flight. */
  simBuilding: { simRunId: string; since: number } | null;
  /** Mode switch: leave SIM (restoring the desk exactly)
   *  and show the LIVE chain or the CANNED replay on the desk. */
  showDeskMode: (mode: 'LIVE' | 'CANNED') => Promise<void>;
  layerVis: LayerVis; setLayerVis: (patch: Partial<LayerVis>) => void;
  mapPaint: MapPaint | null; setMapPaint: (p: MapPaint | null) => void;
  basemap: MapBasemap; setBasemap: (m: MapBasemap) => void;
  moneyPanel: MoneyNodePanel | null; moneySource: DataSource;
  moneyExposures: MoneyExposuresDoc | null; moneyExposuresSource: DataSource;
  // IMPACT layer (server/impact): the loaded run's impact payload and
  // the selected place's impact block, fetched on demand with the same
  // loading/live/unreachable honesty contract as money. impactRun is
  // only meaningful alongside impactRunFor (the run it was derived
  // from); consumers gate on payload.run_id === chain.run_id.
  impactRun: ImpactRun | null; impactRunFor: string | null; impactRunSource: DataSource;
  fetchImpactRun: (runId: string) => Promise<void>;
  impactNodeData: ImpactNode | null; impactNodeSource: DataSource;
  fetchImpactNode: (nodeId: string) => Promise<void>;
  satScans: SatScan[] | null; satSource: DataSource;
  /** PortWatch slice (GET /portwatch at boot): the snapshot the
   *  map's ACTIVITY layer draws. null (loading/unreachable) leaves the
   *  layer empty and the legend hidden — never a fake claim. */
  portwatch: PortWatchSnapshot | null; portwatchSource: DataSource;
  /** Snapshot deployment state: true when the serving
   * layer marks responses as a baked capture — fetch-derived LIVE labels
   * then render SNAPSHOT. Live-API deployments never flip this. */
  snapshotMode: boolean; capturedAt: string | null;
  lenses: Lens[] | null; lensesSource: DataSource;
  /** Search slices: null until loaded; an unloaded slice
   *  leaves the search index spanning less — nothing is invented.
   *  All five load through bounded recovery cycles:
   *  a boot failure does not remove any of them for the
   *  whole session — see ensureSearchSlices. */
  runsCatalogue: RunCatalogueEntry[] | null;
  explorations: EnsExplorationEntry[] | null;
  watchlists: Watchlist[] | null;
  goodsClasses: GoodsClass[] | null;
  replays: ReplaySummary[] | null;
  /** Re-ask for any recovered search slice that has not landed
   *  (bounded retry cycle per slice; a no-op for slices already
   *  loaded or loading). Called at boot and whenever search opens
   *  with a slice absent — the recovery path for a missed boot
   *  fetch, never a poll. */
  ensureSearchSlices: () => void;
  /** LIVE TWIN slice (boot-fetched, the ledger/lenses pattern):
   *  liveTwinEnabled is true ONLY when GET /livetwin/feeds answered
   *  with enabled:true — any failure (the flag-off 404 included)
   *  leaves it false and the section absent. liveTwin is that answer
   *  verbatim; the panel renders it or nothing. */
  liveTwin: LiveTwinDoc | null;
  liveTwinEnabled: boolean;
  /** Layout names the chrome may offer: the static list, plus
   *  'Live Twin' iff the section is enabled. */
  layoutNames: string[];
  /** The live-feed seed note for the SIM panel ("Seeded from the
   *  live feed · …"), set by the livetwin bridge after its run is
   *  created; cleared by exitSim and by the SIM panel's own creation
   *  paths. Null = no note. */
  simSeedNote: string | null; setSimSeedNote: (note: string | null) => void;
  /** Open a STORED sim run (search catalogue): the stored run's
   *  detail through the same call enterSim uses, its baseline
   *  attached, then loaded — the desk chain untouched. Weather is
   *  fetched concurrently and wired in when it lands; it never
   *  gates the load. */
  openSimRun: (id: string) => Promise<void>;
  activeLens: string | null; setLens: (lensId: string | null) => void;
  fetchMoney: (nodeId: string) => Promise<void>;
  compare: CompareState | null; layout: string; visible: PanelId[]; closed: Set<PanelId>;
  maximized: PanelId | null; focusPanel: PanelId | null; moneyNode: string | null;
  /** Window state: persisted column widths + the map's closed
   *  flag. The map is NEVER unmounted by any of this — App renders it
   *  unconditionally and geometry only re-templates the grid / toggles
   *  its cell's display (layoutGeometry.ts). */
  colW: ColWidths; mapHidden: boolean;
  setColW: (side: 'left' | 'right', w: number) => void;
  closeMap: () => void; restoreMap: () => void;
  /** / AUTO DEMO: force the canonical geometry (widths, map
   *  open, nothing maximized) for a deterministic demo run. */
  forceCanonicalGeometry: () => void;
  centreTab: 'Map' | 'Table' | 'Chart' | 'News';
  setCentreTab: (t: Store['centreTab']) => void;
  // ---- interaction actions (the only interaction surface) ----
  selectNode: (id: string | null, seq?: number | null) => void;
  clearSelectedSeq: () => void;
  openChain: (runId: string) => Promise<boolean>;
  /** Demo isolation: snapshot / restore the demo-touchable
   *  slice (see DemoSessionSnapshot). */
  captureDemoSession: () => DemoSessionSnapshot;
  restoreDemoSession: (snap: DemoSessionSnapshot) => void;
  /** Load an already-served chain (an ensemble-statistics variant's
   *  replayed chain) into the chain view — the same rendering every
   *  other chain gets. The previous run's meta spec is preserved, like
   *  a fork's, so panel scenario contexts do not silently change. */
  openVariantChain: (chain: Chain, label: string) => void;
  runWhatIf: (spec: DisruptionSpec, label?: string) => Promise<string | null>;
  forkAt: (seq: number, overrides: Record<string, unknown>) => Promise<void>;
  showProof: () => void; showMoney: (nodeId: string) => void; setTheme: (t: Theme) => void;
  // ---- workspace plumbing (not interaction actions: layout/chrome only) ----
  setLayout: (name: string) => void; closePanel: (p: PanelId) => void;
  toggleMax: (p: PanelId) => void; setFocus: (p: PanelId | null) => void;
  clearError: () => void;
  currentChain: () => Chain | null;
  boot: () => Promise<void>;
  /** Tab / history chrome (urlSync.ts): ‹ › availability + actions, '+'
   *  (a real new browser tab on the current state), ✕ (closes this tab,
   *  only when the app opened it). */
  historyPos: HistoryPos; goBack: () => void; goForward: () => void;
  openNewTab: () => void; canCloseTab: boolean; closeTab: () => void;
  /** A sim build another tab started (or this tab's own): the top-strip
   *  chip's state. openSimInflight opens a ready run. */
  simInflight: SimInflightState | null;
  openSimInflight: () => void; dismissSimInflight: () => void;
  /** Scenario loop: show a sim fork build in the in-flight chip (follows its status; never auto-opens). */
  followSimBuild: (simRunId: string) => void;
}

export const Ctx = createContext<Store | null>(null);
export const useStore = () => { const s = useContext(Ctx); if (!s) throw new Error('store'); return s; };

/* The sim-volatile slice (playback / playhead / sim state) is
 * ALSO provided through its own context, and the desk slice through
 * another, so consumers of the desk slice do not re-render on
 * playback ticks (the full Store value necessarily changes identity
 * on a tick — it carries the sim fields for legacy consumers). New
 * code should prefer the narrowest context: useDeskStore() for
 * desk/chain panels, useSimStore() for sim-only consumers. The full
 * useStore() remains for mixed consumers until they migrate. */
export type SimSliceKey =
  | 'simActive' | 'simRun' | 'simState' | 'simWeather' | 'simTick' | 'simPlaying'
  | 'simLayers' | 'setSimLayer' | 'enterSim' | 'exitSim'
  | 'simPlay' | 'simPause' | 'simStep' | 'simSeek' | 'setSimTick'
  | 'simPlayhead' | 'simStateExact' | 'setSimPlayhead' | 'simStepGranularity'
  | 'simSpeed' | 'setSimSpeed' | 'simGranularity' | 'setSimGranularity'
  | 'simMarkers' | 'simMarkersTruncated' | 'simChainSync' | 'setSimChainSync'
  | 'simSyncedChain' | 'openSimSyncedChain' | 'simEntryDeskChainId'
  | 'simLinks' | 'toggleSimLink' | 'fetchSimStateAt' | 'enterSimShell' | 'createSimRun'
  | 'simVehicleId' | 'selectSimVehicle' | 'fetchSimVehicleAt'
  | 'simBaselineRun' | 'simBaselineState' | 'simVarNote' | 'openSimRun' | 'simBuilding';
export const SIM_SLICE_KEYS: SimSliceKey[] = [
  'simActive', 'simRun', 'simState', 'simWeather', 'simTick', 'simPlaying',
  'simLayers', 'setSimLayer', 'enterSim', 'exitSim',
  'simPlay', 'simPause', 'simStep', 'simSeek', 'setSimTick',
  'simPlayhead', 'simStateExact', 'setSimPlayhead', 'simStepGranularity',
  'simSpeed', 'setSimSpeed', 'simGranularity', 'setSimGranularity',
  'simMarkers', 'simMarkersTruncated', 'simChainSync', 'setSimChainSync',
  'simSyncedChain', 'openSimSyncedChain', 'simEntryDeskChainId',
  'simLinks', 'toggleSimLink', 'fetchSimStateAt', 'enterSimShell', 'createSimRun',
  'simVehicleId', 'selectSimVehicle', 'fetchSimVehicleAt',
  'simBaselineRun', 'simBaselineState', 'simVarNote', 'openSimRun', 'simBuilding',
];
const SIM_SLICE_SET: ReadonlySet<string> = new Set(SIM_SLICE_KEYS);
export type SimStore = Pick<Store, SimSliceKey | 'showDeskMode'>;
export type DeskStore = Omit<Store, SimSliceKey>;
const SimCtx = createContext<SimStore | null>(null);
export const DeskCtx = createContext<DeskStore | null>(null);
export const useSimStore = () => { const s = useContext(SimCtx); if (!s) throw new Error('sim store'); return s; };
export const useDeskStore = () => { const s = useContext(DeskCtx); if (!s) throw new Error('desk store'); return s; };

/** First seq whose event differs between two chain streams (run_id excluded), as a fork compares them. */
export function divergence(a: Chain, b: Chain): number | null {
  const strip = (e: Chain['events'][number]) => JSON.stringify(Object.keys(e).sort().map((k) => [k, k === 'run_id' ? '' : (e as unknown as Record<string, unknown>)[k]]));
  const n = Math.min(a.events.length, b.events.length);
  for (let i = 0; i < n; i++) if (strip(a.events[i]) !== strip(b.events[i])) return i;
  return a.events.length === b.events.length ? null : n;
}

export function StoreProvider({ children }: { children: React.ReactNode }) {
  if (!layoutMigrated) { layoutMigrated = true; migrateLayoutStorage(safeLocalStorage); }
  const [theme, setThemeState] = useState<Theme>(() => readTheme());
  const [mode, setModeState] = useState<ShellMode>(() => readMode(safeStorage));
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [selectedSeq, setSelectedSeq] = useState<number | null>(null);
  const [placeCardOpen, setPlaceCardOpen] = useState(false);
  const [chain, setChain] = useState<Chain | null>(null);
  const [runMeta, setRunMeta] = useState<RunMeta | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [scorecard, setScorecard] = useState<Scorecard | null>(null);
  const [latencyMs, setLatencyMs] = useState<number | null>(null);
  const [feed, setFeed] = useState<FeedEvent[]>(FEED_FALLBACK);
  const [feedSource, setFeedSource] = useState<RemoteSource>('loading');
  const [feedInvalid, setFeedInvalid] = useState(0);
  /* The bundled fallback graph is now a
   * dynamic import (seed.ts loadGraphFallback) so it leaves the main
   * chunk; the boot state is an EMPTY graph labelled 'loading' until
   * the live GET /graph lands or the fallback resolves into place. */
  const [graph, setGraph] = useState<GraphData>({ nodes: [], edges: [] });
  const [graphSource, setGraphSource] = useState<RemoteSource>('loading');
  const [llmStatus, setLlmStatus] = useState<LlmStatus | null>(null);
  const [llmStatusSource, setLlmStatusSource] = useState<LlmSource>('loading');
  const [ledger, setLedger] = useState<LedgerEntry[] | null>(null);
  const [ledgerSource, setLedgerSource] = useState<LlmSource>('loading');
  const [compare, setCompare] = useState<CompareState | null>(null);
  // Layout read is validated by readLayout(): the pre-rename
  // 'Node Desk' migrates to 'Place Desk' there; unknown names boot as
  // the default layout.
  const [layout, setLayoutState] = useState<string>(() => readLayout());
  /* Column widths boot from the validated persisted read; the
   * map's closed flag is session state (a reload reopens the map —
   * only widths persist). */
  const [colW, setColWState] = useState<ColWidths>(() => readColWidths());
  const [mapHiddenState, setMapHidden] = useState(false);
  /* Window manager report (windows/bridge.ts): the open windows and whether the
   * map group is hidden. null = the old grid is in use and the fields below fall
   * through to the store's own state. */
  const [wmReport, setWmReport] = useState<WmReport | null>(null);
  /* Research window state in the URL hash (panels/research/urlHash.ts): a link restores episode and dates. */
  const researchHashValue = useSyncExternalStore(researchHash.subscribe, researchHash.get, () => null);
  useEffect(() => { wmBridge.setReporter(setWmReport); return () => wmBridge.setReporter(null); }, []);
  const mapHidden = wmReport ? wmReport.mapHidden : mapHiddenState;
  const [closed, setClosed] = useState<Set<PanelId>>(new Set());
  const [maximized, setMaximized] = useState<PanelId | null>(null);
  const [focusPanel, setFocusPanel] = useState<PanelId | null>(null);
  const [alertsCount, setAlertsCount] = useState<number | null>(null);
  const [layerVis, setLayerVisState] = useState<LayerVis>({ lanes: true, activity: true, ports: true, chokes: true, air: true });
  const [mapPaint, setMapPaint] = useState<MapPaint | null>(null);
  const [basemap, setBasemapState] = useState<MapBasemap>(() => readBasemap());
  // Sim slice state. simLayers restores from the layout
  // store's localStorage record (sanitised against the known keys);
  // everything else is session state for the loaded run only.
  const [simActive, setSimActive] = useState(false);
  const [simRun, setSimRun] = useState<SimRunDetail | null>(null);
  const [simStateData, setSimStateData] = useState<SimState | null>(null);
  const [simWeatherData, setSimWeatherData] = useState<SimWeatherResponse | null>(null);
  const [simTick, setSimTickState] = useState(0);
  const [simPlaying, setSimPlaying] = useState(false);
  const [simLayers, setSimLayersState] = useState<SimLayers>(() => {
    try {
      return sanitiseSimLayers(JSON.parse(safeStorage.get('supermap.sim.layers') ?? 'null'), LIVE_LAYER_DEFAULTS);
    } catch { return { ...LIVE_LAYER_DEFAULTS }; }
  });
  const simStateReq = useRef(0);
  /** True while an applyPlayhead fetch chain is in flight —
   *  the playback driver skips its tick instead of piling another
   *  serial floor→tick→twin fetch onto the same wall. */
  const simFetchInFlight = useRef(false);
  // Timeline / link bus slice state.
  const [simPlayhead, setSimPlayheadState] = useState(0);
  const [simStateExact, setSimStateExact] = useState<SimState | null>(null);
  const [simSpeed, setSimSpeedState] = useState(1);
  const [simGranularity, setSimGranularityState] = useState<SimGranularity>('HOUR');
  const [simMarkers, setSimMarkers] = useState<SimMarker[] | null>(null);
  const [simMarkersTruncated, setSimMarkersTruncated] = useState(false);
  const [simChainSync, setSimChainSyncState] = useState(false);
  const [simSyncedChain, setSimSyncedChain] = useState<Chain | null>(null);
  const [simEntryDeskChainId, setSimEntryDeskChainId] = useState<string | null>(null);
  /** The desk slice (chain + runMeta) recorded at SIM entry,
   *  restored by exitSim so the desk chain is byte-identical after
   *  SIM, even if an interim write touched the desk slice. */
  const simEntrySnapshot = useRef<{ chain: Chain | null; runMeta: RunMeta | null } | null>(null);
  const simChainReq = useRef<TicketBox>(newTicketBox());
  const [simLinks, setSimLinks] = useState<Record<SimPanelKey, PanelLinkState>>(allLinked);
  const [simBaselineRun, setSimBaselineRun] = useState<SimRunDetail | null>(null);
  const [simBaselineState, setSimBaselineState] = useState<SimState | null>(null);
  const [simVarNote, setSimVarNote] = useState<string | null>(null);
  /* The in-flight server-side build (202) for the creation
   * in progress, if any — see the Store interface note. */
  const [simBuilding, setSimBuilding] = useState<{ simRunId: string; since: number } | null>(null);
  /** / discipline: creation gets its own ticket box —
   *  starting a creation issues a ticket, exitSim bumps it, and a
   *  settle that resolves after exit (or after a newer creation)
   *  finds a dead ticket and never loads its run. */
  const simCreateReq = useRef<TicketBox>(newTicketBox());
  /** Synchronous mirror of the LOADED run's id — set by
   *  loadSimRun, cleared by exitSim. The decoupled weather fetch
   *  (started concurrently with the detail fetch, never awaited
   *  before the load) consults it when its payload finally lands:
   *  weather populates the slice ONLY for the run still loaded. */
  const simLoadedRunIdRef = useRef<string | null>(null);
  /** The loaded run's detail, mirrored synchronously (URL seek / restore). */
  const simLoadedRunRef = useRef<SimRunDetail | null>(null);
  /** Session caches of EXACT tick states, per run — scrubbing revisits
   *  are free and every consumer (master, frozen panels, baseline)
   *  reads the same payload for the same (run, tick). */
  const simStateCache = useRef(new Map<string, SimState>());
  /* The map-picked vehicle + its per-(run, entity,
   *  tick) detail cache — the same bounded-LRU discipline as the
   *  state cache (cacheSimState / readSimCache, 64 entries). */
  const [simVehicleId, setSimVehicleId] = useState<string | null>(null);
  const simVehicleCache = useRef(new Map<string, SimVehicleDetail>());
  const simBaselineRunRef = useRef<SimRunDetail | null>(null);
  const simLastRequest = useRef<SimRunRequest | null>(null);
  // Whether a persisted layer record existed at boot: with none, the
  // first SIM entry applies the SIM defaults (FLOWS on, WEATHER
  // on when the run has weather); with one, the record governs as-is.
  const simLayersStored = useRef(safeStorage.get('supermap.sim.layers') !== null);
  const [moneyNode, setMoneyNode] = useState<string | null>(null);
  const [moneyPanel, setMoneyPanel] = useState<MoneyNodePanel | null>(null);
  const [moneySource, setMoneySource] = useState<DataSource>('loading');
  const [impactRun, setImpactRun] = useState<ImpactRun | null>(null);
  const [impactRunFor, setImpactRunFor] = useState<string | null>(null);
  const [impactRunSource, setImpactRunSource] = useState<DataSource>('loading');
  const [impactNodeData, setImpactNodeData] = useState<ImpactNode | null>(null);
  const [impactNodeSource, setImpactNodeSource] = useState<DataSource>('loading');
  const impactRunReq = useRef(0);
  const impactNodeReq = useRef(0);
  /** The run id the impact fetch was last aimed at + its outcome, so
   *  repeat triggers for the same run (KPI tiles + IMP panel) share one
   *  request instead of refetching on every render. A failed fetch is
   *  NOT latched: the next trigger retries. */
  const impactRunTarget = useRef<{ id: string; live: boolean; inflight: boolean }>({ id: '', live: false, inflight: false });
  const [moneyExposures, setMoneyExposures] = useState<MoneyExposuresDoc | null>(null);
  const [moneyExposuresSource, setMoneyExposuresSource] = useState<DataSource>('loading');
  const [satScans, setSatScans] = useState<SatScan[] | null>(null);
  const [satSource, setSatSource] = useState<DataSource>('loading');
  const [portwatch, setPortwatch] = useState<PortWatchSnapshot | null>(null);
  const [portwatchSource, setPortwatchSource] = useState<DataSource>('loading');
  const [snapshotMode, setSnapshotMode] = useState(false);
  const [capturedAt, setCapturedAt] = useState<string | null>(null);
  useEffect(() => onDataMode((m) => {
    setSnapshotMode(m.snapshot); setCapturedAt(m.capturedAt ?? null);
  }), []);
  const [lenses, setLenses] = useState<Lens[] | null>(null);
  const [lensesSource, setLensesSource] = useState<DataSource>('loading');
  const [runsCatalogue, setRunsCatalogue] = useState<RunCatalogueEntry[] | null>(null);
  const [explorations, setExplorations] = useState<EnsExplorationEntry[] | null>(null);
  const [watchlists, setWatchlists] = useState<Watchlist[] | null>(null);
  const [goodsClasses, setGoodsClasses] = useState<GoodsClass[] | null>(null);
  const [replays, setReplays] = useState<ReplaySummary[] | null>(null);
  /* Search-slice recovery (search/sliceRecovery.ts):
   * all five slices load through bounded retry cycles, not
   * the one-shot boot fetches whose swallowed failures removed whole
   * categories from search for the session (goods) or left stored
   * runs unreachable (the catalogue's ~14 s server build against the
   * client's 15 s default timeout). The closing extension brought
   * the saved explorations, the watchlists and the stored replays
   * through the same machine. ensure() is called at boot and again
   * whenever search opens with a slice absent. */
  const goodsSliceRecovery = useMemo(() => createSliceRecovery(
    () => api.impactGoods(),
    (d) => { setGoodsClasses(d.goods); },
  ), []);
  const runsSliceRecovery = useMemo(() => createSliceRecovery(
    () => api.runsCatalogue(),
    (d) => { setRunsCatalogue(d.runs); },
  ), []);
  const explorationsSliceRecovery = useMemo(() => createSliceRecovery(
    () => api.ensExplorations(),
    (d) => { setExplorations(d.explorations); },
  ), []);
  const watchlistsSliceRecovery = useMemo(() => createSliceRecovery(
    () => api.watchlists(),
    (d) => { setWatchlists(d.watchlists); },
  ), []);
  const replaysSliceRecovery = useMemo(() => createSliceRecovery(
    () => api.desksReplays(),
    (d) => { setReplays(d.replays); },
  ), []);
  const ensureSearchSlices = useCallback(() => {
    goodsSliceRecovery.ensure();
    runsSliceRecovery.ensure();
    explorationsSliceRecovery.ensure();
    watchlistsSliceRecovery.ensure();
    replaysSliceRecovery.ensure();
  }, [goodsSliceRecovery, runsSliceRecovery, explorationsSliceRecovery, watchlistsSliceRecovery, replaysSliceRecovery]);
  const [liveTwin, setLiveTwin] = useState<LiveTwinDoc | null>(null);
  const [liveTwinEnabled, setLiveTwinEnabled] = useState(false);
  /* False until the livetwin boot fetch settles either way — the
   * coercion effect below may only fire on a SETTLED answer, never
   * on the unresolved state (which already renders as 'SupermAP'). */
  const [liveTwinSettled, setLiveTwinSettled] = useState(false);
  const [simSeedNote, setSimSeedNoteState] = useState<string | null>(null);
  const [activeLens, setActiveLens] = useState<string | null>(null);
  const moneyReq = useRef(0);
  const [centreTab, setCentreTab] = useState<Store['centreTab']>('Map');
  const booted = useRef(false);
  // The CURRENT chain, mirrored synchronously. React state alone is
  // not enough for sequenced action scripts: the AUTO DEMO director (demo.tsx)
  // runs runWhatIf → forkAt inside one effect closure whose captured store
  // snapshot still points at the pre-demo chain, so a state-closure forkAt
  // forked the stale 1-event home chain and the API rightly rejected it
  // ([at_seq_out_of_range]). Every chain write goes through applyChain(),
  // which updates this ref BEFORE the action returns, and forkAt reads the
  // ref — so a scripted fork acts on the chain runWhatIf just loaded,
  // exactly as a human clicking Fork after the what-if does (one code
  // path, same result). forkAt/clearError are therefore stable ([] deps)
  // and safe to call from a captured snapshot.
  const chainRef = useRef<Chain | null>(null);
  const applyChain = useCallback((c: Chain | null) => { chainRef.current = c; setChain(c); }, []);
  /** The chain slice's ticket box — openChain, runWhatIf,
   *  forkAt, loadCannedChain and the boot live run all issue from it;
   *  last-issued wins and a late earlier response is dropped. */
  const chainReq = useRef<TicketBox>(newTicketBox());
  /** The meta each run had when it was last on screen, so ‹ › restores its label. */
  const metaByRun = useRef(new Map<string, RunMeta>());
  useEffect(() => { if (chain && runMeta) metaByRun.current.set(chain.run_id, runMeta); }, [chain, runMeta]);
  /** The current feed and run meta for origin labels (stable callbacks read them). */
  const feedRef = useRef<FeedEvent[]>(feed);
  feedRef.current = feed;
  const runMetaRef = useRef<RunMeta | null>(null);
  runMetaRef.current = runMeta;
  /** The sim markers fetch gets its own box —
   *  loadSimRun issues, exitSim bumps, so a late markers response
   *  cannot repopulate a run that has been exited or replaced. */
  const simMarkerReq = useRef<TicketBox>(newTicketBox());
  /** Synchronous mirror of the demo-touchable slice, refreshed
   *  every render (the simPlayRef pattern) so captureDemoSession can
   *  read the CURRENT session from a stable callback. */
  const sessionRef = useRef<Omit<DemoSessionSnapshot, 'local' | 'scrolls'> | null>(null);
  sessionRef.current = {
    chain, runMeta, compare, layout, focusPanel,
    closed: [...closed], maximized, selectedNode, selectedSeq, moneyNode,
    geometry: { colW, mapHidden },
  };
  const clearError = useCallback(() => setError(null), []);
  /** The chain slice RIGHT NOW (chainRef), for the demo's fork-step
   *  choice — the render-time `chain` is one frame stale inside a beat. */
  const currentChain = useCallback((): Chain | null => chainRef.current, []);

  /** Snapshot the demo-touchable slice + localStorage keys.
   *  Item 4: the DOM scroll offsets ride along — they are not store
   *  state, so they are read from the live document at capture time. */
  const captureDemoSession = useCallback((): DemoSessionSnapshot => {
    demoUrlSuspended.current = true; // the demo does not write history (resynced by restore)
    return {
      ...(sessionRef.current as Omit<DemoSessionSnapshot, 'local' | 'scrolls'>),
      closed: [...(sessionRef.current?.closed ?? [])],
      local: captureLocalSnapshot(safeLocalStorage),
      ...(wmBridge.handlers ? { wmLayout: wmBridge.handlers.captureLayout() } : {}),
      scrolls: typeof document === 'undefined' ? {} : captureScrollSnapshot(collectScrollTargets(document)),
    };
  }, []);

  /** Restore a demo snapshot byte-identically. Bumping the
   *  chain ticket first means any demo-dispatched chain request
   *  still in flight lands on a dead ticket and is dropped,
   *  so nothing the demo started can write after the restore. */
  const restoreDemoSession = useCallback((snap: DemoSessionSnapshot) => {
    bumpTicket(chainReq.current);
    applyChain(snap.chain);
    setRunMeta(snap.runMeta); setCompare(snap.compare);
    setLayoutState(snap.layout); setFocusPanel(snap.focusPanel);
    setClosed(new Set(snap.closed)); setMaximized(snap.maximized);
    setSelectedNode(snap.selectedNode); setSelectedSeq(snap.selectedSeq);
    setMoneyNode(snap.moneyNode); setBusy(null);
    /* Geometry restores with the session — a snapshot taken
     * before the demo's canonical forcing (or an older snapshot with
     * no geometry field) lands sanitised, never raw. */
    if (snap.geometry) {
      setColWState(sanitiseColWidths(snap.geometry.colW));
      setMapHidden(snap.geometry.mapHidden === true);
    }
    restoreLocalSnapshot(safeLocalStorage, snap.local);
    if (snap.wmLayout && wmBridge.handlers) wmBridge.handlers.restoreLayout(snap.wmLayout);
    /* The layout persistence effect must not re-write the restored
     * layout over the exact stored value the line above just put back
     * (a demo that ended on another layout would otherwise store the
     * session layout, e.g. 'Simulation', in place of the user's last
     * chosen one). Same suppression the URL-driven layouts use. */
    if (sessionRef.current && sessionRef.current.layout !== snap.layout) layoutFromUrl.current = snap.layout;
    demoUrlSuspended.current = false; urlResync.current(); // re-sync the URL to the restored state (replace)
    /* Item 4: scroll offsets are DOM state, not store state — they
     * are restored AFTER the layout state above, scheduled over the
     * next animation frames so the restored layout has rendered and
     * the offsets do not clamp against the demo layout's content
     * height. Both demo exit paths (abort via kill(), natural
     * completion) funnel through this restore, so both get them. */
    scheduleScrollRestore(snap.scrolls ?? {}, () => (typeof document === 'undefined' ? [] : collectScrollTargets(document)));
  }, [applyChain]);

  const visible = useMemo(
    () => (wmReport
      ? wmReport.open.filter((w): w is PanelId => w in PANEL_COLUMN)
      : layoutPanelsFor(layout, liveTwinEnabled).filter((p) => !closed.has(p))),
    [layout, closed, liveTwinEnabled, wmReport]);
  const layoutNames = useMemo(
    () => (liveTwinEnabled ? [...LAYOUT_NAMES, LIVE_TWIN_LAYOUT_NAME] : LAYOUT_NAMES),
    [liveTwinEnabled]);
  /* The pending-'Live Twin' coercion: once the livetwin fetch has
   * settled OFF, a stored or session 'Live Twin' layout value falls
   * back to 'SupermAP' for good (the persistence effect then stores
   * the coerced value). While unsettled nothing fires — the resolver
   * above already renders the pending value as 'SupermAP'. */
  useEffect(() => {
    if (liveTwinSettled && !liveTwinEnabled && layout === LIVE_TWIN_LAYOUT_NAME) setLayoutState('SupermAP');
  }, [liveTwinSettled, liveTwinEnabled, layout]);
  const setSimSeedNote = useCallback((note: string | null) => setSimSeedNoteState(note), []);

  /* URL ↔ history ↔ cross-tab wiring (urlSync.ts; grammar in urlState.ts).
   * urlReady flips when boot has applied the URL it loaded with — the
   * history writer stays silent until then. demoUrlSuspended is true for
   * the AUTO DEMO's whole run (capture → restore): no history writes and
   * no layout/width persistence, so the demo neither fills the back stack
   * nor drives other tabs' storage sync. */
  const [urlReady, setUrlReady] = useState(false);
  const demoUrlSuspended = useRef(false);
  const urlResync = useRef<() => void>(() => {});
  /** A layout applied from the URL is this tab's own — not the user's
   *  saved choice — so its persistence write is skipped (it would drive
   *  every other tab's storage sync). */
  const layoutFromUrl = useRef<string | null>(null);
  /** A sim run the URL names that is still building: kept in the URL
   *  (and opened when ready) instead of being dropped by the writer. */
  const pendingUrlSim = useRef<{ id: string; tick: number | null } | null>(null);
  /** Boot's chain ticket, graph and live-twin answers, kept so the URL
   *  restore that runs after the chain loads can wait on them. */
  const bootTicketRef = useRef(0);
  const bootGraph = useRef<Promise<GraphData> | null>(null);
  const bootLiveTwin = useRef<Promise<boolean> | null>(null);
  const simUi = useRef({ active: false, building: false });
  simUi.current = { active: simActive, building: simBuilding !== null };
  const onSimReadyRef = useRef<(id: string, autoOpen: boolean) => void>(() => {});
  const simFollow = useSimInflight({
    storage: safeStorage,
    canFollow: () => !simUi.current.active && !simUi.current.building,
    onReady: (id, autoOpen) => onSimReadyRef.current(id, autoOpen),
  });

  useEffect(() => { document.documentElement.dataset.theme = theme; safeStorage.set('supermap.theme', theme); }, [theme]);
  useEffect(() => { if (!demoUrlSuspended.current) safeStorage.set(MODE_KEY, mode); }, [mode]);
  useEffect(() => {
    if (demoUrlSuspended.current) return;
    if (layoutFromUrl.current === layout) { layoutFromUrl.current = null; return; }
    safeStorage.set('supermap.layout', layout);
  }, [layout]);
  useEffect(() => { if (!demoUrlSuspended.current) safeStorage.set('supermap.colw', serializeColWidths(colW)); }, [colW]);
  useEffect(() => { safeStorage.set('supermap.sim.layers', JSON.stringify(simLayers)); }, [simLayers]);

  const fail = (e: unknown) => setError(errorLine(e));

  // selectNode — link-group bus: every panel reading selectedNode/selectedSeq
  // updates together (map highlight, chain row, inspector, Map Content rail).
  const selectNode = useCallback((id: string | null, seq?: number | null) => {
    setSelectedNode(id); setSelectedSeq(seq ?? null); setPlaceCardOpen(id !== null);
  }, []);
  /** The map's cascade reset (step < 0) clears the step only; it does not
   *  re-select the node captured in the map prop's closure, which is one
   *  render stale. */
  const clearSelectedSeq = useCallback(() => setSelectedSeq(null), []);

  /** Set the run meta of a chain just applied by id. The same run
   *  already on screen keeps its meta; a run seen before gets the meta it
   *  had; any other run is SCENARIO until its journal says it was seeded
   *  from a live disruption (then LIVE) — the engine revision joins the
   *  label when the journal gives it. Ticket-guarded like the chain write
   *  itself: a superseded open never touches the meta. */
  const settleOpenedMeta = useCallback(async (c: Chain, prevRunId: string | null, ticket: number) => {
    if (c.run_id === prevRunId && runMetaRef.current) return; // re-open of the chain on screen: meta unchanged
    const known = metaByRun.current.get(c.run_id);
    if (known) { setRunMeta(known); return; }
    setRunMeta(describeChainRun(c, null, feedRef.current));
    let origin: RunOrigin | null = null;
    try { origin = await api.runOrigin(c.run_id); } catch { /* journal unavailable: the SCENARIO default stands */ }
    if (!origin || !isCurrentTicket(chainReq.current, ticket) || chainRef.current?.run_id !== c.run_id) return;
    setRunMeta(describeChainRun(c, origin, feedRef.current));
  }, []);

  const openChain = useCallback(async (runId: string) => {
    const ticket = issueTicket(chainReq.current);
    setBusy('chain'); setError(null);
    try {
      const c = await api.chain(runId);
      if (!isCurrentTicket(chainReq.current, ticket)) return false;
      const prevRunId = chainRef.current?.run_id ?? null;
      applyChain(c);
      await settleOpenedMeta(c, prevRunId, ticket);
      return true;
    } catch (e) {
      if (isCurrentTicket(chainReq.current, ticket)) fail(e);
      return false;
    } finally { if (isCurrentTicket(chainReq.current, ticket)) setBusy(null); }
  }, [applyChain, settleOpenedMeta]);

  /** A variant / scenario-loop chain: SCENARIO unless it is a run seen
   *  before as CANNED (the bundled copy stays CANNED). The caller's label
   *  is kept; the engine revision is appended when the journal gives one. */
  const openVariantChain = useCallback((c: Chain, label: string) => {
    applyChain(c); setCompare(null); setSelectedSeq(null);
    setSelectedNode(c.events[0]?.node_id ?? null);
    const known = metaByRun.current.get(c.run_id);
    const mode = known?.mode === 'CANNED' ? 'CANNED' : 'SCENARIO';
    setRunMeta({ label, mode, spec: runMetaRef.current?.spec });
    if (c.run_id.startsWith('replay_')) return; // a recorded Replay chain has no stored journal to ask for
    void api.runOrigin(c.run_id).then((o) => {
      const rev = o.engine_label ?? (typeof o.engine_rev === 'number' ? `ENGINE REV ${o.engine_rev}` : null);
      if (!rev || chainRef.current?.run_id !== c.run_id) return;
      setRunMeta((m) => (m && m.label === label && !label.includes('ENGINE REV') ? { ...m, label: `${label} · ${rev}` } : m));
    }).catch(() => { /* no journal: the label stands as given */ });
  }, [applyChain]);

  const runWhatIf = useCallback(async (spec: DisruptionSpec, label?: string) => {
    const ticket = issueTicket(chainReq.current);
    setBusy('run'); setError(null);
    try {
      const r = await api.runScenario(spec);
      if (!isCurrentTicket(chainReq.current, ticket)) return null;
      const c = await api.chain(r.run_id);
      if (!isCurrentTicket(chainReq.current, ticket)) return null;
      applyChain(c); setCompare(null); setSelectedSeq(null); setSelectedNode(spec.node_id);
      setRunMeta({ label: label ?? `What-if — ${nodeLabel(spec.node_id)} · ${prettyKind(spec.kind).toLowerCase()}`, mode: 'SCENARIO', spec });
      return r.run_id;
    } catch (e) {
      if (isCurrentTicket(chainReq.current, ticket)) fail(e);
      return null;
    } finally { if (isCurrentTicket(chainReq.current, ticket)) setBusy(null); }
  }, [applyChain]);

  const forkAt = useCallback(async (seq: number, overrides: Record<string, unknown>) => {
    const parent = chainRef.current; // CURRENT chain — see chainRef note
    if (!parent) return;
    const ticket = issueTicket(chainReq.current);
    setBusy('fork'); setError(null);
    try {
      const r = await api.fork(parent.run_id, seq, overrides);
      if (!isCurrentTicket(chainReq.current, ticket)) return;
      const child = await api.chain(r.run_id);
      if (!isCurrentTicket(chainReq.current, ticket)) return;
      setCompare({ parent, child, divergence: divergence(parent, child), atSeq: seq });
      applyChain(child);
      setRunMeta((m) => ({ label: `Fork — ${nodeLabel(rootEvent(parent).node_id)}, from step ${seq}`, mode: 'SCENARIO', spec: m?.spec }));
    } catch (e) {
      if (isCurrentTicket(chainReq.current, ticket)) fail(e);
    } finally { if (isCurrentTicket(chainReq.current, ticket)) setBusy(null); }
  }, [applyChain]);

  // GET /money/node/{id} on demand. Last-write-wins via a request
  // counter so a slow earlier node can never overwrite a newer selection.
  const fetchMoney = useCallback(async (nodeId: string) => {
    const ticket = ++moneyReq.current;
    setMoneySource('loading');
    try {
      const p = await api.moneyNode(nodeId);
      if (moneyReq.current === ticket) { setMoneyPanel(p); setMoneySource('live'); }
    } catch {
      if (moneyReq.current === ticket) { setMoneyPanel(null); setMoneySource('unreachable'); }
    }
  }, []);

  // GET /impact/run/{id} on demand (IMPACT layer). Last-write-wins via
  // a request counter, like fetchMoney; the target ref dedupes repeat
  // triggers for the same run. A 404 (a run the API never stored — e.g.
  // the bundled offline chain) lands as 'unreachable': the surfaces
  // then assert no figures rather than inventing any.
  const fetchImpactRun = useCallback(async (runId: string) => {
    const tgt = impactRunTarget.current;
    if (tgt.id === runId && (tgt.live || tgt.inflight)) return;
    const ticket = ++impactRunReq.current;
    if (runId.startsWith('replay_')) { // a recorded Replay chain was never stored as a run
      setImpactRun(null); setImpactRunSource('unreachable'); setImpactRunFor(runId);
      impactRunTarget.current = { id: runId, live: false, inflight: false };
      return;
    }
    impactRunTarget.current = { id: runId, live: false, inflight: true };
    setImpactRunSource('loading'); setImpactRunFor(runId);
    try {
      const p = await api.impactRun(runId);
      if (impactRunReq.current === ticket) {
        setImpactRun(p); setImpactRunSource('live');
        impactRunTarget.current = { id: runId, live: true, inflight: false };
      }
    } catch {
      if (impactRunReq.current === ticket) {
        setImpactRun(null); setImpactRunSource('unreachable');
        impactRunTarget.current = { id: runId, live: false, inflight: false };
      }
    }
  }, []);

  // GET /impact/node/{id} on demand (IMPACT layer) — the selected
  // place's goods block + country exposure. Same counter discipline.
  const fetchImpactNode = useCallback(async (nodeId: string) => {
    const ticket = ++impactNodeReq.current;
    setImpactNodeSource('loading');
    try {
      const p = await api.impactNode(nodeId);
      if (impactNodeReq.current === ticket) { setImpactNodeData(p); setImpactNodeSource('live'); }
    } catch {
      if (impactNodeReq.current === ticket) { setImpactNodeData(null); setImpactNodeSource('unreachable'); }
    }
  }, []);

  const showProof = useCallback(() => { setFocusPanel('proof'); setClosed((c) => { const n = new Set(c); n.delete('proof'); return n; }); }, []);
  const showMoney = useCallback((nodeId: string) => {
    setMoneyNode(nodeId); setFocusPanel('money');
    setClosed((c) => { const n = new Set(c); n.delete('money'); return n; });
    setLayoutState((l) => (LAYOUTS[l]?.includes('money') ? l : 'Market Desk'));
  }, []);
  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    // SAT is an independent basemap: a theme change does not yank the user
    // out of satellite view; dark/light basemaps follow the theme FAMILY.
    setBasemapState((b) => (b === 'sat' ? b : basemapForTheme(t)));
  }, []);
  const setBasemap = useCallback((m: MapBasemap) => {
    setBasemapState(m);
    // The theme follows the basemap within the current family —
    // a basemap switch never kicks a retro user into institutional.
    if (m === 'dark' || m === 'light') setThemeState((t) => themeForBasemap(t, m));
  }, []);
  const setLayerVis = useCallback((patch: Partial<LayerVis>) => {
    setLayerVisState((v) => ({ ...v, ...patch }));
  }, []);

  // ---- Simulation actions --------
  // JUMP-SAFE: applying a state is a pure, idempotent
  // wholesale replacement — no incremental/sequential assumptions — so
  // scrubbing may fetch arbitrary ticks in arbitrary order (backwards
  // across disruption onsets included); the request counter ensures
  // the LATEST requested playhead is the one on screen, and exact tick
  // payloads are cached per run for the session.
  const getSimStateCached = useCallback(async (runId: string, tick: number): Promise<SimState> => {
    const key = `${runId}:${tick}`;
    const hit = readSimCache(simStateCache.current, key);
    if (hit) return hit;
    const st = await api.simState(runId, tick);
    cacheSimState(simStateCache.current, key, st);
    return st;
  }, []);

  /** Apply a (possibly fractional) playhead: the floor tick's EXACT
   *  state becomes simStateExact (boards/VaR/chain sync); simState is
   *  the display state — floor verbatim on tick boundaries, positions
   *  interpolated between the two adjacent exact states between ticks
   *  (discrete facts never interpolated). The baseline twin's
   *  exact state at the same floor tick rides along. */
  /* Supersession is checked after EVERY fetch, not only after
   * all of them — a superseded playhead stops fetching instead of
   * serially finishing floor→next→twin for a state nobody wants. A
   * fetch error HALTS playback (one fail, driver stopped): the old
   * code called fail() and playback continued into the same wall
   * every 250 ms. After a successful apply the next tick is prefetched
   * into the LRU cache while idle, so the driver's next step is warm. */
  const applyPlayhead = useCallback(async (run: SimRunDetail, playhead: number) => {
    const ticket = ++simStateReq.current;
    simFetchInFlight.current = true;
    const floor = floorTickOf(playhead);
    const frac = playhead - floor;
    try {
      const exact = await getSimStateCached(run.sim_run_id, floor);
      if (simStateReq.current !== ticket) return;
      let display = exact;
      if (frac > 1e-6 && floor + 1 < run.ticks) {
        const next = await getSimStateCached(run.sim_run_id, floor + 1);
        if (simStateReq.current !== ticket) return;
        display = interpolatePositions(exact, next, frac);
      }
      const baseline = simBaselineRunRef.current;
      const baselineState = baseline
        ? await getSimStateCached(baseline.sim_run_id, floor).catch(() => null)
        : null;
      if (simStateReq.current !== ticket) return;
      setSimStateExact(exact); setSimStateData(display);
      setSimTickState(exact.tick); setSimPlayheadState(playhead);
      if (baseline) setSimBaselineState(baselineState);
      if (floor + 1 < run.ticks) {
        void getSimStateCached(run.sim_run_id, floor + 1).catch(() => { /* prefetch is best-effort */ });
      }
    } catch (e) {
      if (simStateReq.current === ticket) { fail(e); setSimPlaying(false); }
    } finally {
      if (simStateReq.current === ticket) simFetchInFlight.current = false;
    }
  }, [getSimStateCached]);

  const setSimPlayhead = useCallback((playhead: number) => {
    if (!simRun) return;
    void applyPlayhead(simRun, clampPlayhead(playhead, simRun.ticks));
  }, [simRun, applyPlayhead]);

  const fetchSimStateAt = useCallback(async (tick: number): Promise<SimState | null> => {
    if (!simRun) return null;
    try { return await getSimStateCached(simRun.sim_run_id, clampTick(tick, simRun.ticks)); }
    catch { return null; }
  }, [simRun, getSimStateCached]);

  const selectSimVehicle = useCallback((id: string | null) => setSimVehicleId(id), []);

  /** The selected vehicle's detail at a tick — the
   *  fetchSimStateAt pattern over the vehicle route: reads the
   *  current run + selection, clamps the tick to the run's span,
   *  session-caches by (run, entity, tick). A failed fetch resolves
   *  null (never latched — the next call retries), like
   *  fetchSimStateAt. */
  const fetchSimVehicleAt = useCallback(async (tick: number): Promise<SimVehicleDetail | null> => {
    if (!simRun || !simVehicleId) return null;
    const t = clampTick(tick, simRun.ticks);
    const key = `${simRun.sim_run_id}:${simVehicleId}:${t}`;
    const hit = readSimCache(simVehicleCache.current, key);
    if (hit) return hit;
    try {
      const det = await api.fetchSimVehicle(simRun.sim_run_id, simVehicleId, t);
      cacheSimState(simVehicleCache.current, key, det);
      return det;
    } catch { return null; }
  }, [simRun, simVehicleId]);

  /** Record the desk chain's identity at SIM entry (first
   *  entry only — re-entering a run while SIM is already open must
   *  not overwrite the original desk snapshot with a sim-era chain). */
  const noteSimEntry = useCallback(() => {
    if (simEntrySnapshot.current) return;
    const chainNow = chainRef.current;
    simEntrySnapshot.current = { chain: chainNow, runMeta: sessionRef.current?.runMeta ?? null };
    setSimEntryDeskChainId(chainNow?.run_id ?? null);
  }, []);

  /** Load a chain INTO THE SIM SLICE (chain-sync
   *  target). Same ticket discipline as openChain, but the payload
   *  lands in simSyncedChain and the desk chain is never touched. */
  const openSimSyncedChain = useCallback(async (runId: string) => {
    const ticket = issueTicket(simChainReq.current);
    try {
      const c = await api.chain(runId);
      if (!isCurrentTicket(simChainReq.current, ticket)) return false;
      setSimSyncedChain(c);
      return true;
    } catch (e) {
      if (isCurrentTicket(simChainReq.current, ticket)) fail(e);
      return false;
    }
  }, []);

  /** Load a run detail as the current run: resets the playhead, the
   *  markers and every panel link (switching runs re-links all
   *  panels), then applies tick 0. `weather` may be null —
   *  the weather payload does not gate the load; it is wired in
   *  afterwards by wireLateSimWeather when its fetch resolves. */
  const loadSimRun = useCallback(async (detail: SimRunDetail, weather: SimWeatherResponse | null) => {
    simLoadedRunIdRef.current = detail.sim_run_id; simLoadedRunRef.current = detail;
    noteSimEntry();
    simStateCache.current.clear();
    /* A run change clears the vehicle selection — the
     * picked entity belongs to the previous run's roster. */
    setSimVehicleId(null); simVehicleCache.current.clear();
    setSimRun(detail); setSimWeatherData(weather);
    setSimMarkers(null); setSimMarkersTruncated(false);
    setSimLinks(allLinked()); setSimChainSyncState(false);
    setSimStateData(null); setSimStateExact(null); setSimTickState(0);
    setSimPlayheadState(0); setSimPlaying(false);
    setSimActive(true);
    const markerTicket = issueTicket(simMarkerReq.current);
    api.simMarkers(detail.sim_run_id)
      .then((r) => {
        if (!isCurrentTicket(simMarkerReq.current, markerTicket)) return;
        setSimMarkers(r.markers); setSimMarkersTruncated(r.markers_truncated);
      })
      .catch(() => {
        if (!isCurrentTicket(simMarkerReq.current, markerTicket)) return;
        setSimMarkers(null); setSimMarkersTruncated(false);
      });
    await applyPlayhead(detail, 0);
  }, [applyPlayhead, noteSimEntry]);

  /** Wire a CONCURRENTLY-started weather fetch into the
   *  sim slice. The fetch is started alongside the detail fetch and
   *  is never awaited before loadSimRun — a PINNED run's weather
   *  document is ~5.4 MB per tick (163 MB at 30 ticks), and
   *  awaiting it made stored weather runs unopenable. The run loads
   *  with weather=null; when the payload lands it populates the
   *  slice ONLY if the caller's ticket is still current (the ticket
   *  discipline — exitSim / a newer creation or open supersedes it)
   *  AND the loaded run is still this run (simLoadedRunIdRef). A
   *  rejected fetch arrives here as null (callers fold the rejection)
   *  and leaves simWeatherData null — the honest empty layer state;
   *  the run record's own weather_window still shows PINNED. */
  const wireLateSimWeather = useCallback((
    id: string,
    weatherPromise: Promise<SimWeatherResponse | null>,
    isCurrent: () => boolean,
  ) => {
    void weatherPromise.then((payload) => {
      if (!payload) return;
      if (!isCurrent()) return;
      if (simLoadedRunIdRef.current !== id) return;
      setSimWeatherData(payload);
    });
  }, []);

  /** Baseline attachment, shared by BOTH run-creation paths
   *  (enterSim and createSimRun): a run created from a request that
   *  carries disruptions gets its undisrupted twin — identical seed,
   *  tick, start and weather setting, empty disruption set — as the
   *  volume-at-risk baseline; a run without disruptions is its own
   *  baseline. Twin failure never blocks the run: the ref stays null
   *  and simVarNote carries the reason, so the VaR slot stays honestly
   *  reserved with no substituted figure. MUST run before loadSimRun —
   *  the playhead applier reads simBaselineRunRef — and it clears any
   *  previous baseline state up front, so a re-created run can never
   *  diff against a stale baseline.
   *
   *  Both paths must attach it: the SIM panel's "Load the demo
   *  configuration" entry (enterSim's caller) would otherwise load
   *  the disrupted demo run with simBaselineState permanently null
   *  and VOLUME AT RISK would render its empty state at every tick. */
  const attachSimBaseline = useCallback(async (request: SimRunRequest, detail: SimRunDetail) => {
    setSimBaselineState(null);
    if (request.disruptions.length === 0) {
      simBaselineRunRef.current = detail; setSimBaselineRun(detail);
      setSimVarNote(SIM_NOTE_NO_DISRUPTIONS);
      return;
    }
    try {
      const twinSummary = await api.simRunSettled({ ...request, disruptions: [] });
      const twin = await api.simRunDetail(twinSummary.sim_run_id);
      simBaselineRunRef.current = twin; setSimBaselineRun(twin);
      setSimVarNote(null);
    } catch (te) {
      simBaselineRunRef.current = null; setSimBaselineRun(null);
      setSimVarNote(SIM_NOTE_TWIN_FAILED);
    }
  }, []);

  /** THE shared creation path for enterSim + createSimRun —
   *  one helper so enterSim and createSimRun cannot drift
   *  apart (both attach the baseline). It owns the whole settle, in order:
   *  (a) the baseline state clears UP FRONT (attachSimBaseline's
   *      discipline) so a re-created run can never diff against a
   *      stale baseline;
   *  (b) the main run AND — when the request carries disruptions —
   *      its undisrupted twin start settling CONCURRENTLY via
   *      api.simRunSettled (create-then-poll: a 202 build settles
   *      in-session instead of failing the panel); the twin's outcome
   *      is captured as value-or-error and NEVER throws — twin failure
   *      reserves the VaR slot with the reason, it does not block the
   *      run. An undisrupted request takes the self-baseline path,
   *      unchanged;
   *  (c) the main settle is awaited → detail fetched → the
   *      simLayers default applied exactly as before. The weather
   *      fetch STARTS with the detail fetch but is NOT awaited:
   *      the run loads with weather=null and the
   *      payload is wired in by wireLateSimWeather when it lands;
   *  (d) the twin outcome is awaited → simBaselineRun /
   *      simBaselineRunRef set, or simVarNote carries the failure in
   *      the established wording;
   *  (e) loadSimRun runs LAST — the playhead applier reads
   *      simBaselineRunRef, so the baseline must be in place before
   *      the first apply (the ordering law — it is about the
   *      baseline ref, not weather).
   *  Building state (simBuilding) is set from either settle's
   *  onBuilding (first reporter wins — the main run reports first in
   *  the common case) and cleared as the run loads or when creation
   *  fails. The whole settle rides a creation ticket: a
   *  settle that resolves after exitSim bumped the ticket returns
   *  false and loads NOTHING. Resolves true when the run loaded,
   *  false when superseded/exited; throws the creation error (when
   *  still current) for the wrappers to surface in their own shape. */
  const settleSimCreation = useCallback(async (request: SimRunRequest): Promise<boolean> => {
    const ticket = issueTicket(simCreateReq.current);
    const current = () => isCurrentTicket(simCreateReq.current, ticket);
    let done = false;
    // (a) clear the baseline up front.
    setSimBaselineState(null);
    simBaselineRunRef.current = null;
    setSimBaselineRun(null);
    setSimVarNote(null);
    setSimBuilding(null);
    const noteBuilding = (id: string) => {
      if (done || !current()) return;
      setSimBuilding((b) => b ?? { simRunId: id, since: Date.now() });
    };
    /* Cross-tab discovery: the MAIN run's id (never the twin's) is
     * published to storage when its build is accepted and retracted when
     * it settles either way — even after an exit, the server job runs on. */
    let mainBuildId: string | null = null;
    const noteMainBuilding = (id: string) => {
      noteBuilding(id);
      if (!done && current()) { mainBuildId = id; simFollow.begin(id, Date.now()); }
    };
    // (b) start BOTH settles before awaiting either.
    const mainPromise = api.simRunSettled(request, { onBuilding: noteMainBuilding });
    const retract = () => { if (mainBuildId) simFollow.end(mainBuildId); };
    void mainPromise.then(retract, retract);
    type TwinOutcome = { self: true } | { twin: SimRunDetail } | { error: unknown };
    const twinPromise: Promise<TwinOutcome> = request.disruptions.length === 0
      ? Promise.resolve({ self: true } as TwinOutcome)
      : (async (): Promise<TwinOutcome> => {
          try {
            const twinSummary = await api.simRunSettled(
              { ...request, disruptions: [] }, { onBuilding: noteBuilding });
            const twin = await api.simRunDetail(twinSummary.sim_run_id);
            return { twin };
          } catch (e) { return { error: e }; }
        })();
    try {
      // (c) await the main settle, then detail + layers. The weather
      // fetch starts concurrently with the detail fetch and is never
      // awaited before the load; a rejection folds to null.
      const summary = await mainPromise;
      if (!current()) { done = true; return false; }
      const weatherPromise: Promise<SimWeatherResponse | null> =
        api.simWeather(summary.sim_run_id).catch(() => null);
      const detail = await api.simRunDetail(summary.sim_run_id);
      if (!current()) { done = true; return false; }
      simLastRequest.current = request;
      if (!simLayersStored.current) {
        simLayersStored.current = true;
        setSimLayersState(simLayerDefaults(detail.weather !== null));
      }
      // (d) await the twin outcome and attach the baseline.
      const outcome = await twinPromise;
      if (!current()) { done = true; return false; }
      if ('self' in outcome) {
        simBaselineRunRef.current = detail; setSimBaselineRun(detail);
        setSimVarNote(SIM_NOTE_NO_DISRUPTIONS);
      } else if ('twin' in outcome) {
        simBaselineRunRef.current = outcome.twin; setSimBaselineRun(outcome.twin);
        setSimVarNote(null);
      } else {
        const te = outcome.error;
        simBaselineRunRef.current = null; setSimBaselineRun(null);
        setSimVarNote(SIM_NOTE_TWIN_FAILED);
      }
      // (e) load LAST (with weather=null); the building state
      // clears as the run loads. The weather payload wires in when
      // its concurrent fetch lands, guarded by this settle's ticket
      // and the loaded run's id.
      done = true;
      setSimBuilding(null);
      await loadSimRun(detail, null);
      wireLateSimWeather(summary.sim_run_id, weatherPromise, current);
      return true;
    } catch (e) {
      done = true;
      if (!current()) return false;
      setSimBuilding(null);
      throw e;
    }
  }, [loadSimRun, wireLateSimWeather, simFollow.begin, simFollow.end]);

  const enterSim = useCallback(async (request: SimRunRequest) => {
    setBusy('sim'); setError(null);
    try {
      await settleSimCreation(request);
    } catch (e) { fail(e); } finally { setBusy(null); }
  }, [settleSimCreation]);

  /** Run creation: the shared settle above, with
   *  the settle result — an error line ("[code] message")
   *  or null (loaded, or superseded by an exit/newer creation) — the
   *  SIM panel form and the LIVE TWIN bridge consume. */
  const createSimRun = useCallback(async (request: SimRunRequest): Promise<string | null> => {
    setBusy('sim'); setError(null);
    try {
      await settleSimCreation(request);
      return null;
    } catch (e) {
      return errorLine(e);
    } finally { setBusy(null); }
  }, [settleSimCreation]);

  /** Open a STORED sim run: mirrors
   *  enterSim's body over the run's own served record — detail
   *  through the same call, baseline attached from a request
   *  rebuilt out of the run's journal (the baseline helper reads
   *  only its disruption count and the twin body), then loadSimRun.
   *  Focus lands on the SIM panel, where the run plays.
   *  The weather fetch starts with the detail fetch but is NOT
   *  awaited — the run loads with weather=null and the payload wires
   *  in when it lands. The open rides the creation ticket box: it
   *  supersedes an in-flight creation settle, exitSim supersedes it,
   *  and its late weather can only land on this run. */
  const openSimRun = useCallback(async (id: string) => {
    setBusy('sim'); setError(null);
    const ticket = issueTicket(simCreateReq.current);
    const current = () => isCurrentTicket(simCreateReq.current, ticket);
    try {
      const weatherPromise: Promise<SimWeatherResponse | null> =
        api.simWeather(id).catch(() => null);
      const detail = await api.simRunDetail(id);
      const j = detail.journal;
      const request: SimRunRequest = {
        disruptions: j.disruptions.map((d) => ({
          node_id: d.node_id, kind: d.kind,
          ...(d.capacity_loss != null ? { capacity_loss: d.capacity_loss } : {}),
          duration_d: d.duration_d, started_at: d.started_at, shape: 'step' as const,
        })),
        tick: j.tick, ticks: j.ticks, seed: j.seed, start_at: j.start_at,
        weather: detail.weather !== null,
        ...(j.lane_geometry ? { lane_geometry: true } : {}),
      };
      simLastRequest.current = request;
      if (!simLayersStored.current) {
        simLayersStored.current = true;
        setSimLayersState(simLayerDefaults(detail.weather !== null));
      }
      await attachSimBaseline(request, detail);
      if (!current()) return; // superseded (exit / newer creation or open)
      await loadSimRun(detail, null);
      wireLateSimWeather(id, weatherPromise, current);
      setFocusPanel('sim');
    } catch (e) { fail(e); } finally { setBusy(null); }
  }, [loadSimRun, attachSimBaseline, wireLateSimWeather]);

  const enterSimShell = useCallback(() => { noteSimEntry(); setSimActive(true); }, [noteSimEntry]);

  // Leaving SIM clears ONLY the sim slice — chain/runMeta/feed/graph
  // (LIVE/CANNED) are byte-identical before and after (separability).
  const exitSim = useCallback(() => {
    /* Bump both sim tickets BEFORE clearing — an in-flight
     * applyPlayhead / markers fetch that resumes after exit finds a
     * dead ticket and cannot repopulate the cleared slice. */
    bumpTicket(simStateReq);
    bumpTicket(simMarkerReq.current);
    bumpTicket(simChainReq.current);
    /* Bump the creation ticket too — a create-then-poll
     * settle still in flight at exit finds a dead ticket and never
     * loads its run — and clear any building state with the slice. */
    bumpTicket(simCreateReq.current);
    setSimBuilding(null);
    /* The loaded-run mirror clears with the slice — a weather
     * payload landing after exit has no run to populate. */
    simLoadedRunIdRef.current = null; simLoadedRunRef.current = null;
    setSimPlaying(false); setSimActive(false);
    setSimRun(null); setSimStateData(null); setSimWeatherData(null); setSimTickState(0);
    setSimPlayheadState(0); setSimStateExact(null); setSimMarkers(null);
    setSimMarkersTruncated(false); setSimChainSyncState(false); setSimLinks(allLinked());
    simBaselineRunRef.current = null; setSimBaselineRun(null);
    setSimBaselineState(null); setSimVarNote(null);
    /* The live-feed seed note belongs to the sim session that the
     * bridge created — leaving SIM clears it with the slice. */
    setSimSeedNoteState(null);
    simStateCache.current.clear();
    /* The vehicle selection dies with the sim slice. */
    setSimVehicleId(null); simVehicleCache.current.clear();
    /* The sim-synced chain dies with the sim slice, and the
     * desk chain recorded at entry is restored byte-identically
     * (the same object) if anything wrote the desk slice meanwhile —
     * leaving SIM can never strand a sim-synced chain on the desk,
     * including via showDeskMode's restore-without-reload path. */
    setSimSyncedChain(null);
    const entrySnap = simEntrySnapshot.current;
    simEntrySnapshot.current = null;
    setSimEntryDeskChainId(null);
    /* A snapshot taken before any desk chain existed (the shell opened at boot in
     * SIMULATION mode) restores nothing: it must never wipe a chain that loaded since. */
    if (entrySnap && entrySnap.chain && chainRef.current !== entrySnap.chain) {
      applyChain(entrySnap.chain);
      setRunMeta(entrySnap.runMeta);
    }
  }, [applyChain]);

  const setSimTick = useCallback((tick: number) => {
    if (!simRun) return;
    setSimPlayhead(clampTick(tick, simRun.ticks));
  }, [simRun, setSimPlayhead]);
  const simSeek = setSimTick;
  const simStep = useCallback((delta: number) => {
    setSimPlaying(false);
    setSimPlayhead(simPlayhead + delta);
  }, [simPlayhead, setSimPlayhead]);
  const simStepGranularity = useCallback((dir: 1 | -1) => {
    if (!simRun) return;
    setSimPlaying(false);
    const tickSeconds = simRun.tick_seconds ?? 86400;
    const stepTicks = ({ MINUTE: 60, HOUR: 3600, DAY: 86400 } as const)[simGranularity] / tickSeconds;
    setSimPlayhead(simPlayhead + dir * stepTicks);
  }, [simRun, simGranularity, simPlayhead, setSimPlayhead]);
  const simPlay = useCallback(() => {
    if (!simRun) return;
    if (isAtEnd(floorTickOf(simPlayhead), simRun.ticks) && simPlayhead >= simRun.ticks - 1) setSimPlayhead(0);
    setSimPlaying(true);
  }, [simRun, simPlayhead, setSimPlayhead]);
  const simPause = useCallback(() => setSimPlaying(false), []);
  const setSimLayer = useCallback((key: SimLayerKey, on: boolean) => {
    setSimLayersState((l) => withLayerToggled(l, key, on));
    // SATELLITE's source of truth is the basemap switcher.
    if (key === 'SATELLITE') setBasemap(on ? 'sat' : basemapForTheme(theme));
  }, [theme]); // eslint-disable-line react-hooks/exhaustive-deps
  const setSimSpeed = useCallback((speed: number) => setSimSpeedState(speed), []);
  const setSimGranularity = useCallback((g: SimGranularity) => setSimGranularityState(g), []);
  const setSimChainSync = useCallback((on: boolean) => setSimChainSyncState(on), []);
  const toggleSimLink = useCallback((key: SimPanelKey) => {
    setSimLinks((links) => {
      const cur = links[key];
      if (!simRun) return links;
      if (cur.linked) {
        const master = { runId: simRun.sim_run_id, playhead: simPlayhead, nodeId: selectedNode };
        return { ...links, [key]: freezeLink(master, fmtSimDayTime(playheadMs(simRun, floorTickOf(simPlayhead)))) };
      }
      return { ...links, [key]: relink() };
    });
  }, [simRun, simPlayhead, selectedNode]);

  // Playback driver: a 250 ms cadence advancing the playhead
  // by granularity × speed (one play-second = one granularity
  // step at 1×). Stops at the final tick. State stays a pure fetch —
  // the driver only chooses the playhead in view.
  const simPlayRef = useRef({ playhead: 0, granularity: 'HOUR' as SimGranularity, speed: 1 });
  simPlayRef.current = { playhead: simPlayhead, granularity: simGranularity, speed: simSpeed };
  useEffect(() => {
    if (!simPlaying || !simActive || !simRun) return;
    const tickSeconds = simRun.tick_seconds ?? 86400;
    let last = performance.now();
    const id = window.setInterval(() => {
      const now = performance.now();
      /* Skip this tick while a fetch is in flight, so serial fetches
       * do not stack against a slow service. The playhead does not advance on a
       * skipped tick; the next interval retries from the same place. */
      if (simFetchInFlight.current) { last = now; return; }
      const cur = simPlayRef.current;
      const next = advancePlayhead(cur.playhead, now - last, cur.speed, cur.granularity, tickSeconds, simRun.ticks);
      last = now;
      if (next >= simRun.ticks - 1) { setSimPlaying(false); }
      void applyPlayhead(simRun, next);
    }, 250);
    return () => window.clearInterval(id);
  }, [simPlaying, simActive, simRun, applyPlayhead]);

  /** Desk mode switch target: the CANNED replay through the API, with
   *  the bundled fixture as the offline fallback — the same contract
   *  as boot's CANNED path (duplicated deliberately so boot's gold
   *  path stays byte-identical). */
  const loadCannedChain = useCallback(async () => {
    const ticket = issueTicket(chainReq.current);
    try {
      const r = await api.runScenario(CANNED_SPEC);
      if (!isCurrentTicket(chainReq.current, ticket)) return;
      const c = await api.chain(r.run_id);
      if (!isCurrentTicket(chainReq.current, ticket)) return;
      applyChain(c); setSelectedNode(CANNED_SPEC.node_id);
      setRunMeta({ label: CANNED_LABEL, mode: 'CANNED', spec: CANNED_SPEC });
    } catch {
      if (!isCurrentTicket(chainReq.current, ticket)) return;
      applyChain(CHAIN_FALLBACK); setSelectedNode(CANNED_SPEC.node_id);
      setRunMeta({ label: CHAIN_FALLBACK_LABEL, mode: 'CANNED', spec: CANNED_SPEC });
      setError(null);
    }
  }, [applyChain]);

  /** Mode switch: SIM → desk restores the LIVE/CANNED
   *  state exactly as it was (the sim slice is separable); desk →
   *  desk re-runs the chosen desk mode through the existing paths. */
  const showDeskMode = useCallback(async (mode: 'LIVE' | 'CANNED') => {
    /* Restore-without-reload ONLY when the desk already holds
     * the requested mode (deskRestoreOnly). A SCENARIO chain + LIVE
     * falls through and loads a live chain below. */
    if (simActive) { exitSim(); if (deskRestoreOnly(simActive, runMeta?.mode, mode)) return; }
    if (mode === 'CANNED') { await loadCannedChain(); return; }
    /* (Same rule as boot): the LIVE desk target is the
     * highest-severity feed item, not file-order feed[0]. */
    const first = highestSeverityFeedItem(feed);
    if (!first) { await loadCannedChain(); return; }
    const ticket = issueTicket(chainReq.current);
    setBusy('run');
    try {
      const r = await api.runLive(first.id);
      if (!isCurrentTicket(chainReq.current, ticket)) return;
      const c = await api.chain(r.run_id);
      if (!isCurrentTicket(chainReq.current, ticket)) return;
      applyChain(c); setSelectedNode(first.node_id);
      setRunMeta(liveRunMeta(first, nodeLabel(first.node_id)));
    } catch { if (isCurrentTicket(chainReq.current, ticket)) await loadCannedChain(); }
    finally { if (isCurrentTicket(chainReq.current, ticket)) setBusy(null); }
  }, [simActive, exitSim, runMeta, feed, loadCannedChain, applyChain]);

  const boot = useCallback(async (url: UrlState) => {
    const t0 = performance.now();
    /* The two reads the first chain waits for go FIRST. A browser serves a handful of requests per host at a time and
     * starts them in the order they were issued; with the twenty status and catalogue reads below issued ahead of them,
     * the graph and the event feed queued behind those and the boot chain started seconds later on a slow link. */
    // Graph (GET /graph): replaces the initial bundled fallback in place when
    // it lands; on failure the fallback stays and is labelled OFFLINE COPY.
    bootGraph.current = api.graph().then((g) => { setGraph(g); setGraphSource('live'); return g; })
      .catch(() => {
        /* Live graph unreachable — resolve the bundled
         * fallback chunk and apply it, labelled OFFLINE COPY. */
        return loadGraphFallback().then((g) => { setGraph(g); setGraphSource('fallback'); return g; });
      });
    // Feed (GET /events): same contract. The promise resolves to whichever
    // list is in force (live or fallback) so the default live run below
    // seeds from the same events the feed panel renders.
    const feedReady: Promise<FeedEvent[]> = api.events()
      .then(({ events, invalidLines }) => { setFeed(events); setFeedSource('live'); setFeedInvalid(invalidLines); return events; })
      .catch(() => { setFeed(FEED_FALLBACK); setFeedSource('fallback'); return FEED_FALLBACK; });
    api.health().then((h) => { setHealth(h); setLatencyMs(Math.round(performance.now() - t0)); }).catch(() => setHealth(null));
    api.scorecard().then(setScorecard).catch(() => setScorecard(null));
    // LLM surfaces: burn meter (GET /llm/status) + chain ledger
    // (GET /llm/ledger). On failure the source stays 'unreachable' and the
    // chrome/panel render n/a — never a fabricated $0.00 or 0 entries.
    api.llmStatus().then((st) => { setLlmStatus(st); setLlmStatusSource('live'); })
      .catch(() => { setLlmStatus(null); setLlmStatusSource('unreachable'); });
    api.llmLedger().then((entries) => { setLedger(entries); setLedgerSource('live'); })
      .catch(() => { setLedger(null); setLedgerSource('unreachable'); });
    // Surfaces: exposures doc (filing enrichment + Market Watch
    // counts), satwatch scans, lenses — all read-only boot fetches with
    // the same loading/live/unreachable honesty contract as /llm.
    api.moneyExposures().then((d) => { setMoneyExposures(d); setMoneyExposuresSource('live'); })
      .catch(() => { setMoneyExposures(null); setMoneyExposuresSource('unreachable'); });
    api.satwatchScans().then((scans) => { setSatScans(scans); setSatSource('live'); })
      .catch(() => { setSatScans(null); setSatSource('unreachable'); });
    api.lenses().then((doc) => { setLenses(doc.lenses); setLensesSource('live'); })
      .catch(() => { setLenses(null); setLensesSource('unreachable'); });
    // Search slices: the run catalogue, saved explorations,
    // watchlists, the IMPACT goods taxonomy and the stored replays —
    // all five load through their bounded recovery cycles, so a boot-window failure is retried instead of
    // removing a whole category from search for the whole page life.
    runsSliceRecovery.ensure();
    explorationsSliceRecovery.ensure();
    watchlistsSliceRecovery.ensure();
    goodsSliceRecovery.ensure();
    replaysSliceRecovery.ensure();
    // LIVE TWIN: enabled ONLY on an enabled:true answer. The
    // flag-off 404 (and any other failure) settles the slice disabled
    // — the section then does not exist anywhere in the chrome.
    // /livetwin/status answers the flag with a 200, so a flag-off boot
    // never requests /livetwin/feeds (its 404 was a console error on every
    // load). A server without the status route is probed the old way.
    const liveTwinFlag: Promise<boolean | null> = api.liveTwinStatus()
      .then((r) => (typeof r?.enabled === 'boolean' ? r.enabled : null)).catch(() => null);
    bootLiveTwin.current = liveTwinFlag.then((flag) => (flag === false ? null : api.liveTwinFeeds())).then((d) => {
      const on = !!d && d.enabled === true;
      if (on) { setLiveTwin(d); setLiveTwinEnabled(true); }
      else { setLiveTwin(null); setLiveTwinEnabled(false); }
      setLiveTwinSettled(true);
      return on;
    }).catch(() => { setLiveTwin(null); setLiveTwinEnabled(false); setLiveTwinSettled(true); return false; });
    // PortWatch snapshot for the map's ACTIVITY layer. Failure
    // leaves the slice null/'unreachable' — layer empty, legend hidden.
    api.portwatch().then((doc) => { setPortwatch(doc); setPortwatchSource('live'); })
      .catch(() => { setPortwatch(null); setPortwatchSource('unreachable'); });
    // Fired feed count for the util bar (read-only; the ALR panel keeps
    // its own full fetch). Unreachable leaves the count null — the chrome
    // then renders a bare ALERTS label rather than an invented zero.
    api.alertsFeed(500).then((r) => setAlertsCount(r.alerts.length))
      .catch(() => setAlertsCount(null));
    // Deep links for demos: #light starts in light theme, #replay starts on
    // the CANNED chain (urlState.ts parses the hash; run=/sim=/place=… are
    // restored here and in restoreUrlTail below).
    if (url.flags.includes('light')) setThemeState('light');
    // CANNED path: execute the canned spec through the API first;
    // when the API itself is unreachable, load the bundled real-engine
    // fixture chain (seed.ts CHAIN_FALLBACK) instead of ending with "No
    // chain loaded." The fixture boot is a SUCCESS path — labelled CANNED ·
    // OFFLINE COPY via runMeta — so it must not leave an error bar behind;
    // the feed/graph OFFLINE COPY chips stay exactly as they are.
    /* The boot chain writes ride the chain ticket box like
     * every other chain action — a user action issued after boot
     * started supersedes the boot live run, whose late response is
     * then dropped instead of overwriting the user's chain. */
    const bootTicket = issueTicket(chainReq.current);
    bootTicketRef.current = bootTicket;
    const loadCanned = async () => {
      try {
        const r = await api.runScenario(CANNED_SPEC);
        if (!isCurrentTicket(chainReq.current, bootTicket)) return;
        const c = await api.chain(r.run_id);
        if (!isCurrentTicket(chainReq.current, bootTicket)) return;
        applyChain(c); setSelectedNode(CANNED_SPEC.node_id);
        setRunMeta({ label: CANNED_LABEL, mode: 'CANNED', spec: CANNED_SPEC });
      } catch {
        if (!isCurrentTicket(chainReq.current, bootTicket)) return;
        applyChain(CHAIN_FALLBACK); setSelectedNode(CANNED_SPEC.node_id);
        setRunMeta({ label: CHAIN_FALLBACK_LABEL, mode: 'CANNED', spec: CANNED_SPEC });
        setError(null);
      }
    };
    // Default state: a chain is already computed on load — live disruption
    // first, CANNED-labelled replay when the live run is unavailable.
    /* A run named in the URL replaces the boot hero; one the API cannot
     * open is dropped and boot proceeds normally. */
    if (url.run) {
      try {
        const c = await api.chain(url.run);
        if (!isCurrentTicket(chainReq.current, bootTicket)) return;
        applyChain(c);
        setRunMeta({ label: `SCENARIO — run ${c.run_id.length > 10 ? c.run_id.slice(-6) : c.run_id}`, mode: 'SCENARIO' });
        return;
      } catch { /* fall through to the normal boot */ }
    }
    if (url.flags.includes('replay')) { await loadCanned(); return; }
    const liveFeed = await feedReady;
    if (!liveFeed.length) { // live feed served zero events — nothing to run live; CANNED path.
      await loadCanned();
      return;
    }
    /* The boot hero is SELECTED from served data, never
     * liveFeed[0] (file order = oldest). Each distinct node+kind is
     * run once (POST /run is idempotent; its receipt's event_count is
     * the served run summary), its chain is fetched, and
     * selectBootChain picks the largest qualifying chain (>= 8
     * events, >= 2 modes), tie-broken by severity — falling back to
     * the highest-severity feed item when nothing qualifies. A
     * candidate whose run fails is skipped, not fatal; only when
     * EVERY candidate fails does boot take the CANNED path. */
    /* The queue is built from served facts BEFORE any POST /run —
     * places the loaded network does not know (or knows only as SHELL
     * places, which the chain engine refuses as unknown_node) and
     * resolved disruptions never reach the loop, the walk is bounded
     * (BOOT_MAX_CANDIDATES) and stops at the first qualifying chain,
     * so a cold boot posts a handful of runs at most and zero 422s. */
    const bootNet = await (bootGraph.current ?? Promise.resolve(null)).catch(() => null);
    const queue = bootCandidateQueue(liveFeed, bootNet ? (bootNet.nodes as unknown as BootNetNode[]) : null);
    const candidates: BootChainCandidate[] = [];
    for (const item of queue) {
      if (!isCurrentTicket(chainReq.current, bootTicket)) return;
      try {
        const r = await api.runLive(item.id);
        if (!isCurrentTicket(chainReq.current, bootTicket)) return;
        const c = await api.chain(r.run_id);
        if (!isCurrentTicket(chainReq.current, bootTicket)) return;
        const cand: BootChainCandidate = { item, chain: c, receiptEvents: r.event_count };
        candidates.push(cand);
        if (bootChainQualifies(cand)) break; // first qualifying chain wins; no further runs
      } catch { /* this candidate's run is unavailable — others may still qualify */ }
    }
    const chosen = selectBootChain(candidates, liveFeed);
    /* In a recorded or offline deployment (most feeds are not live) whose served disruptions only make
     * thin chains, the first screen boots the recorded Suez replay (labelled CANNED) so it shows a real cascade. */
    let mostlyOffline = false;
    if (chosen && !bootChainQualifies(chosen)) {
      const board = await Promise.resolve().then(() => api.health()).catch(() => null);
      if (!isCurrentTicket(chainReq.current, bootTicket)) return;
      mostlyOffline = !!board && board.adapters.length > 0 && board.adapters.filter((a) => a.state === 'LIVE').length * 2 < board.adapters.length;
    }
    if (!chosen || mostlyOffline) { await loadCanned(); return; }
    applyChain(chosen.chain); setSelectedNode(chosen.item.node_id);
    setRunMeta(liveRunMeta(chosen.item, nodeLabel(chosen.item.node_id)));
  }, [applyChain, goodsSliceRecovery, runsSliceRecovery, explorationsSliceRecovery, watchlistsSliceRecovery, replaysSliceRecovery]);

  /* The workspace-plumbing actions are stable callbacks (they
   *  were inline lambdas recreated on every render, which alone would
   *  defeat context memoisation), and the sim layer record's derived
   *  SATELLITE entry is memoised with them. */
  const setLens = useCallback((id: string | null) => setActiveLens(id), []);
  const setLayout = useCallback((n: string) => {
    /* Belt-and-braces for the absence law: the menu never offers
     * 'Live Twin' while disabled, and a stray call is a no-op too. */
    if (n === LIVE_TWIN_LAYOUT_NAME && !liveTwinEnabled) return;
    setLayoutState(n); setClosed(new Set()); setMaximized(null);
  }, [liveTwinEnabled]);
  /* Window actions — functional updates only, so batched
   * actions (close + maximize in one event) compose instead of
   * reading a stale mirror; the transition LAW they implement is
   * the pure helpers in layoutGeometry.ts (geometryAfterCloseMap /
   * geometryAfterToggleMax), pinned there and here. The map is a
   * window, not a member of the `closed` set: closePanel('map')
   * delegates here, and its restore path is restoreMap(). */
  const closeMap = useCallback(() => {
    if (wmBridge.handlers) { wmBridge.handlers.hideMap(); return; }
    setMapHidden(true);
    setMaximized((m) => (m === 'map' ? null : m)); // a closed window is not maximized
  }, []);
  const restoreMap = useCallback(() => { if (wmBridge.handlers) { wmBridge.handlers.showMap(); return; } setMapHidden(false); }, []);
  const closePanel = useCallback((p: PanelId) => {
    if (p === 'map') { closeMap(); return; }
    if (wmBridge.handlers) { wmBridge.handlers.closeWindow(p); return; }
    setClosed((c) => new Set(c).add(p));
  }, [closeMap]);
  const toggleMax = useCallback((p: PanelId) => {
    if (wmBridge.handlers) { wmBridge.handlers.toggleMax(p); return; }
    // ⤢ on the map restores a closed map first (geometryAfterToggleMax).
    if (p === 'map') setMapHidden(false);
    setMaximized((m) => (m === p ? null : p));
  }, []);
  const setColW = useCallback((side: 'left' | 'right', w: number) => {
    setColWState((prev) => sanitiseColWidths({ ...prev, [side]: w }));
  }, []);
  const forceCanonicalGeometry = useCallback(() => {
    wmBridge.handlers?.forceCanonical();
    setColWState({ ...CANONICAL_GEOMETRY.colW });
    setMapHidden(CANONICAL_GEOMETRY.mapHidden);
    setMaximized(null);
  }, []);
  const simLayersDerived = useMemo<SimLayers>(
    () => ({ ...simLayers, SATELLITE: basemap === 'sat' }),
    [simLayers, basemap],
  );


  /* ── URL restore, history and cross-tab sync (urlSync.ts) ───────────
   * boot() reads the hash once: layout + focus apply at once, the run
   * replaces the boot hero (bootBody), place + sim apply after the chain
   * loads (restoreUrlTail), and only then does the history writer start.
   * After that the hash follows the state (push for navigation, replace
   * for a scrub); popstate/hashchange apply a parsed hash back. */
  const urlLatest = useRef<{
    mode: ShellMode; layout: string; focus: PanelId | null; place: string | null; run: string | null; sim: string | null;
    simTick: number; simActive: boolean; graph: GraphData; liveTwinEnabled: boolean; colW: ColWidths; preset: string | null;
  } | null>(null);
  urlLatest.current = {
    mode, layout, focus: focusPanel, place: selectedNode, run: chain?.run_id ?? null,
    sim: simActive && simRun ? simRun.sim_run_id : null, simTick, simActive, graph, liveTwinEnabled, colW, preset: wmReport?.code ?? null,
  };

  const setLayoutFromUrl = useCallback((n: string) => {
    if (n !== urlLatest.current?.layout) layoutFromUrl.current = n;
    setLayoutState(n); setClosed(new Set()); setMaximized(null);
  }, []);
  const seekLoadedSim = useCallback(async (tick: number) => {
    const run = simLoadedRunRef.current;
    if (run) await applyPlayhead(run, clampPlayhead(tick, run.ticks));
  }, [applyPlayhead]);
  /** Open the sim run a URL names: ready → open + seek; still building →
   *  follow its status and open it when it lands; unknown/failed → drop. */
  const restoreSimFromUrl = useCallback(async (id: string, tick: number | null) => {
    let st: Awaited<ReturnType<typeof api.simRunStatus>>;
    try { st = await api.simRunStatus(id); } catch { return; }
    if (st.status === 'ready') {
      await openSimRun(id);
      if (tick !== null && simLoadedRunIdRef.current === id) await seekLoadedSim(tick);
    } else if (st.status === 'building') {
      pendingUrlSim.current = { id, tick };
      simFollow.follow(id, Date.now(), true);
    }
  }, [openSimRun, seekLoadedSim, simFollow.follow]);
  onSimReadyRef.current = (id, autoOpen) => {
    // The stored-runs catalogue behind search just gained this run.
    void api.runsCatalogue().then((d) => setRunsCatalogue(d.runs)).catch(() => { /* search keeps its last catalogue */ });
    const pend = pendingUrlSim.current;
    /* An EMPTY sim shell (SIMULATION mode, no run loaded, none being created) is exactly where a
     * deep-linked build must land; only a run the user already has, or a creation in flight, is kept. */
    if (!autoOpen || !pend || pend.id !== id || simLoadedRunIdRef.current !== null || simUi.current.building) return;
    void (async () => {
      await openSimRun(id);
      if (pend.tick !== null && simLoadedRunIdRef.current === id) await seekLoadedSim(pend.tick);
      pendingUrlSim.current = null; simFollow.dismiss();
    })();
  };

  const restoreUrlTail = useCallback(async (url: UrlState) => {
    if (!isCurrentTicket(chainReq.current, bootTicketRef.current)) return; // a user action superseded boot
    if (url.layout === LIVE_TWIN_LAYOUT_NAME) {
      const on = await (bootLiveTwin.current ?? Promise.resolve(false));
      const l = resolveUrlLayout(url.layout, on ? [...LAYOUT_NAMES, LIVE_TWIN_LAYOUT_NAME] : LAYOUT_NAMES, LIVE_TWIN_LAYOUT_NAME, on);
      if (l !== null) setLayoutFromUrl(l);
    }
    const place = url.place;
    if (place) {
      const g = await (bootGraph.current ?? Promise.resolve(null)).catch(() => null);
      if (g && (g.nodes as unknown as Array<{ id: string }>).some((n) => n.id === place)) setSelectedNode(place);
    }
    if (url.sim) await restoreSimFromUrl(url.sim, url.tick);
  }, [setLayoutFromUrl, restoreSimFromUrl]);

  const bootFromUrl = useCallback(async () => {
    if (booted.current) return; booted.current = true;
    const url = readLocationUrlState();
    /* Mode: the URL wins over the last session's stored mode. */
    const startMode: ShellMode = url.mode ?? readMode(safeStorage);
    if (url.mode) setModeState(url.mode);
    /* SIMULATION: the (empty) sim shell opens at once (a sim= run, if any, loads into it),
     * never a run of its own, and never half in SIMULATION when the run cannot open. */
    if (startMode === 'sim') enterSimShell();
    const l = resolveUrlLayout(url.layout, LAYOUT_NAMES, LIVE_TWIN_LAYOUT_NAME, false);
    if (l !== null && url.layout !== LIVE_TWIN_LAYOUT_NAME) setLayoutFromUrl(l); // 'Live Twin' waits for its flag (restoreUrlTail)
    if (url.focus) setFocusPanel(url.focus);
    if (url.research) { researchHash.adopt(url.research); if (!url.layout && !url.preset) setLayoutFromUrl('Research'); }
    simFollow.check(); // a build another tab started shows at once, not after the boot chain
    try { await boot(url); }
    finally {
      try { await restoreUrlTail(url); } finally {
        setUrlReady(true); simFollow.check();
      }
    }
  }, [boot, restoreUrlTail, setLayoutFromUrl, simFollow.check, enterSimShell]);

  /* Mode switch. Entering SIMULATION opens the sim shell (the same
   * no-network path as the old SIM button: it never creates a run) and
   * lands on the family's layout unless the current one belongs to it;
   * entering LIVE leaves the sim slice (the desk chain is restored by
   * exitSim) and does the same. A URL-driven change keeps the URL's own
   * layout (keepLayout). */
  const setMode = useCallback((m: ShellMode, opts?: { keepLayout?: boolean }) => {
    setModeState(m);
    if (m === 'sim') { if (!simUi.current.active) enterSimShell(); }
    else if (simUi.current.active) exitSim();
    if (!opts?.keepLayout) {
      setLayoutState((l) => {
        const next = layoutAfterMode(m, l, liveTwinEnabled);
        if (next !== l) { setClosed(new Set()); setMaximized(null); }
        return next;
      });
    }
  }, [enterSimShell, exitSim, liveTwinEnabled]);
  /* The sim slice entering or leaving by ANY path (the map-bar SIM
   * button, search, a sim= link) moves the shell mode with it. */
  const prevSimActive = useRef(simActive);
  const modeRef = useRef(mode); modeRef.current = mode;
  useEffect(() => {
    const next = modeAfterSimTransition(prevSimActive.current, simActive, modeRef.current);
    prevSimActive.current = simActive;
    if (next) setModeState(next);
  }, [simActive]);

  const urlApply = useCallback((t: UrlState, isCurrent: () => boolean) => {
    const L = () => urlLatest.current as NonNullable<typeof urlLatest.current>;
    return applyUrlTarget(t, {
      layoutNames: L().liveTwinEnabled ? [...LAYOUT_NAMES, LIVE_TWIN_LAYOUT_NAME] : LAYOUT_NAMES,
      liveTwinName: LIVE_TWIN_LAYOUT_NAME, liveTwinEnabled: L().liveTwinEnabled,
      setMode: (m) => setMode(m, { keepLayout: true }),
      setPreset: (code) => wmBridge.handlers?.applyCode(code),
      cur: () => ({ mode: L().mode, preset: L().preset, layout: L().layout, focus: L().focus, place: L().place, run: L().run, sim: L().sim, simTick: L().simTick, simActive: L().simActive }),
      hasPlace: (id) => (L().graph.nodes as unknown as Array<{ id: string }>).some((n) => n.id === id),
      setLayout: setLayoutFromUrl, setFocus: setFocusPanel, selectNode, exitSim,
      openChain: async (id) => {
        const known = metaByRun.current.get(id); // read before the load lands (its render records the stale meta)
        const ok = await openChain(id);
        if (ok && known) setRunMeta(known); // an unseen run keeps the origin-derived meta openChain set
        return ok;
      },
      restoreSim: restoreSimFromUrl, seekSim: seekLoadedSim,
    }, isCurrent);
  }, [setLayoutFromUrl, selectNode, exitSim, openChain, restoreSimFromUrl, seekLoadedSim, setMode]);

  const pendSim = pendingUrlSim.current && simFollow.inflight && simFollow.inflight.simRunId === pendingUrlSim.current.id
    && simFollow.inflight.state !== 'failed' ? pendingUrlSim.current : null;
  /** The scrub position is not rewritten into the URL while playing (4 writes a second). */
  const urlTick = useRef(0);
  if (!simPlaying) urlTick.current = simTick;
  const urlCurrent: UrlState = {
    ...EMPTY_URL_STATE,
    layout: layout === LIVE_TWIN_LAYOUT_NAME && !liveTwinEnabled ? 'SupermAP' : layout,
    mode: mode === 'sim' ? 'sim' : null,
    place: selectedNode, run: chain?.run_id ?? null,
    sim: simActive && simRun ? simRun.sim_run_id : pendSim?.id ?? null,
    tick: simActive && simRun ? urlTick.current : pendSim?.tick ?? null,
    focus: focusPanel, flags: [],
    preset: wmReport?.code ?? null,
    research: researchHashValue,
  };
  const urlSync = useUrlSync({ ready: urlReady, current: urlCurrent, light: theme === 'light', suspended: demoUrlSuspended, apply: urlApply });
  urlResync.current = urlSync.resync;

  /* Cross-tab: layout and column widths follow the other tabs' storage
   * writes. Applied as a REPLACE (never a push), validated like a boot
   * read, and compared with the live value first — an identical value is
   * not re-applied, so the persistence effects' echo cannot ping-pong. */
  useStorageEvents((e) => {
    const a = classifyStorageEvent(e, (k) => safeStorage.get(k));
    if (!a) return;
    if (a.kind === 'inflight') { simFollow.check(); return; }
    if (!urlReady || demoUrlSuspended.current) return;
    if (a.kind === 'layout') {
      const name = readLayout({ getItem: () => a.raw });
      if (name === urlLatest.current?.layout) return;
      urlSync.replaceNext();
      setLayoutState(name); setClosed(new Set()); setMaximized(null);
    } else {
      const w = parseColWidths(a.raw);
      if (serializeColWidths(w) !== serializeColWidths(urlLatest.current?.colW ?? DEFAULT_COLW)) setColWState(w);
    }
  });

  /* A ready chip retires itself once that run is the loaded one. */
  useEffect(() => {
    const i = simFollow.inflight;
    if (i && i.state === 'ready' && simRun?.sim_run_id === i.simRunId) simFollow.dismiss();
  }, [simFollow.inflight, simRun, simFollow.dismiss]); // eslint-disable-line react-hooks/exhaustive-deps
  const simInflight = simFollow.inflight;
  const openSimInflight = useCallback(() => {
    if (simInflight?.state === 'ready') void openSimRun(simInflight.simRunId);
    else if (simInflight?.state === 'failed') simFollow.dismiss();
  }, [simInflight, openSimRun, simFollow.dismiss]); // eslint-disable-line react-hooks/exhaustive-deps
  /* Read through a ref: a callback depending on the current state would
   * change identity on every playback tick and re-render the desk consumers. */
  const urlNow = useRef({ state: urlCurrent, light: false });
  urlNow.current = { state: urlCurrent, light: theme === 'light' };
  const openNewTab = useCallback(() => {
    openAppTab(newTabHash({ ...urlNow.current.state, flags: urlNow.current.light ? ['light'] : [] }));
  }, []);
  const historyPos = urlSync.pos; const goBack = urlSync.back; const goForward = urlSync.forward;
  const canCloseTab = urlSync.isChild; const closeTab = closeAppTab;
  const dismissSimInflight = simFollow.dismiss;
  const followSimBuild = useCallback((simRunId: string) => { simFollow.follow(simRunId, Date.now(), false); }, [simFollow.follow]); // eslint-disable-line react-hooks/exhaustive-deps

  /* The context value is memoised — it was a plain object
   *  literal recreated on every provider render, so every one of the
   *  ~54 useStore() consumers re-rendered on every state change,
   *  including each 250 ms playback tick. */
  const value = useMemo<Store>(() => ({
    mode, setMode, theme, selectedNode, selectedSeq, placeCardOpen, chain, runMeta, busy, error, health, scorecard,
    latencyMs, feed, feedSource, feedInvalid, graph, graphSource,
    llmStatus, llmStatusSource, ledger, ledgerSource,
    alertsCount, layerVis, setLayerVis, mapPaint, setMapPaint, basemap, setBasemap,
    simActive, simRun, simState: simStateData, simWeather: simWeatherData, simTick, simPlaying,
    simLayers: simLayersDerived, setSimLayer,
    enterSim, exitSim, simPlay, simPause, simStep, simSeek, setSimTick,
    simPlayhead, simStateExact, setSimPlayhead, simStepGranularity,
    simSpeed, setSimSpeed, simGranularity, setSimGranularity,
    simMarkers, simMarkersTruncated, simChainSync, setSimChainSync,
    simSyncedChain, openSimSyncedChain, simEntryDeskChainId,
    simLinks, toggleSimLink, fetchSimStateAt, enterSimShell, createSimRun,
    simVehicleId, selectSimVehicle, fetchSimVehicleAt,
    simBaselineRun, simBaselineState, simVarNote, simBuilding, showDeskMode, openSimRun,
    liveTwin, liveTwinEnabled, layoutNames, simSeedNote, setSimSeedNote,
    moneyPanel, moneySource, moneyExposures, moneyExposuresSource,
    impactRun, impactRunFor, impactRunSource, fetchImpactRun,
    impactNodeData, impactNodeSource, fetchImpactNode,
    satScans, satSource, portwatch, portwatchSource, snapshotMode, capturedAt, lenses, lensesSource,
    runsCatalogue, explorations, watchlists, goodsClasses, replays, ensureSearchSlices,
    activeLens, setLens, fetchMoney,
    compare, layout, visible, closed, maximized, focusPanel, moneyNode, centreTab, setCentreTab,
    colW, mapHidden, setColW, closeMap, restoreMap, forceCanonicalGeometry,
    selectNode, clearSelectedSeq, openChain, captureDemoSession, restoreDemoSession, openVariantChain, runWhatIf, forkAt, showProof, showMoney, setTheme, clearError, currentChain,
    setLayout, closePanel, toggleMax,
    setFocus: setFocusPanel, boot: bootFromUrl,
    historyPos, goBack, goForward, openNewTab, canCloseTab, closeTab, simInflight, openSimInflight, dismissSimInflight, followSimBuild,
  }), [
    mode, setMode, theme, selectedNode, selectedSeq, placeCardOpen, chain, runMeta, busy, error, health, scorecard,
    latencyMs, feed, feedSource, feedInvalid, graph, graphSource,
    llmStatus, llmStatusSource, ledger, ledgerSource,
    alertsCount, layerVis, setLayerVis, mapPaint, setMapPaint, basemap, setBasemap,
    simActive, simRun, simStateData, simWeatherData, simTick, simPlaying,
    simLayersDerived, setSimLayer,
    enterSim, exitSim, simPlay, simPause, simStep, simSeek, setSimTick,
    simPlayhead, simStateExact, setSimPlayhead, simStepGranularity,
    simSpeed, setSimSpeed, simGranularity, setSimGranularity,
    simMarkers, simMarkersTruncated, simChainSync, setSimChainSync,
    simSyncedChain, openSimSyncedChain, simEntryDeskChainId,
    simLinks, toggleSimLink, fetchSimStateAt, enterSimShell, createSimRun,
    simVehicleId, selectSimVehicle, fetchSimVehicleAt,
    simBaselineRun, simBaselineState, simVarNote, simBuilding, showDeskMode, openSimRun,
    liveTwin, liveTwinEnabled, layoutNames, simSeedNote, setSimSeedNote,
    moneyPanel, moneySource, moneyExposures, moneyExposuresSource,
    impactRun, impactRunFor, impactRunSource, fetchImpactRun,
    impactNodeData, impactNodeSource, fetchImpactNode,
    satScans, satSource, portwatch, portwatchSource, snapshotMode, capturedAt, lenses, lensesSource,
    runsCatalogue, explorations, watchlists, goodsClasses, replays, ensureSearchSlices,
    activeLens, setLens, fetchMoney,
    compare, layout, visible, closed, maximized, focusPanel, moneyNode, centreTab, setCentreTab,
    colW, mapHidden, setColW, closeMap, restoreMap, forceCanonicalGeometry,
    selectNode, clearSelectedSeq, openChain, captureDemoSession, restoreDemoSession, openVariantChain, runWhatIf, forkAt, showProof, showMoney, setTheme, clearError, currentChain,
    setLayout, closePanel, toggleMax, bootFromUrl,
    historyPos, goBack, goForward, openNewTab, canCloseTab, closeTab, simInflight, openSimInflight, dismissSimInflight, followSimBuild,
  ]);

  /* Split contexts: the desk value depends ONLY on desk fields
   *  (its identity is stable across playback ticks — a tick changes
   *  no desk dep), and the sim value only on the sim slice (+ the
   *  desk-mode switch the SIM chrome drives). Both are picked from the
   *  memoised full value, so the three views can never disagree. */
  const deskDeps = Object.keys(value).filter((k) => !SIM_SLICE_SET.has(k)).map((k) => (value as unknown as Record<string, unknown>)[k]);
  const deskValue = useMemo<DeskStore>(() => {
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(value)) if (!SIM_SLICE_SET.has(k)) out[k] = (value as unknown as Record<string, unknown>)[k];
    return out as unknown as DeskStore;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deskDeps);
  const simDeps = [...SIM_SLICE_KEYS.map((k) => (value as unknown as Record<string, unknown>)[k]), value.showDeskMode];
  const simValue = useMemo<SimStore>(() => {
    const out: Record<string, unknown> = { showDeskMode: value.showDeskMode };
    for (const k of SIM_SLICE_KEYS) out[k] = (value as unknown as Record<string, unknown>)[k];
    return out as unknown as SimStore;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, simDeps);

  return (
    <Ctx.Provider value={value}>
      <DeskCtx.Provider value={deskValue}>
        <SimCtx.Provider value={simValue}>{children}</SimCtx.Provider>
      </DeskCtx.Provider>
    </Ctx.Provider>
  );
}
