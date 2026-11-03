import { CloseMark } from "./CloseMark";
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useId,
  useRef,
  useState,
} from "react";
import maplibregl from "maplibre-gl";
import { MapboxOverlay } from "@deck.gl/mapbox";
import "maplibre-gl/dist/maplibre-gl.css";
import "../theme/tokens.css";
import "../theme/hud.css";
import { railArrow } from "../theme/railNav";
import { pairedBasemap, placeKindColors, themeForBasemapMode, tokens } from "../theme/tokens";
import type {
  BasemapMode,
  BasemapStatus,
  ChainResponse,
  GraphData,
  GraphNode,
  NodeRunState,
  PortActivityRecord,
  PortWatchSnapshot,
  ThemeName,
} from "../types";
import {
  CARTO_DARK_STYLE,
  CARTO_LIGHT_STYLE,
  applyDarkPaint,
  applyLightPaint,
  attributionHtml,
  satelliteStyle,
} from "../theme/basemaps";
import {
  fetchProbe,
  imageryChip,
  resolveDailyDate,
  type SatSource,
} from "../theme/imagery";
import { FocusBox } from "./FocusBox";
import {
  IDLE_FOCUS,
  KIND_LEGEND,
  dropFocus,
  engageFocus,
  focusZoomForKind,
  cardCameraOffset,
  popupShiftY,
  releaseFocus,
  type FocusGoodsLine,
  type FocusState,
} from "./focus";
import {
  ensureUnderlay,
  setUnderlayVisibility,
  syncFallbackStyle,
} from "../theme/underlay";
import {
  cachedStyleJson,
  isFamilySwap,
  prefetchStyleJson,
  withUnderlayCarried,
  type StyleDoc,
} from "../theme/styleSwap";
import {
  buildAmbientLayers,
  buildHoverLayers,
  buildPulseLayer,
  buildStaticLayers,
  placePaintLegend,
  revealedArcData,
  withPulseLayer,
  type ClusterHit,
  type PlacePaint,
} from "./layers";
import { pulsingPlaces } from "../places/placeLayers";
import { CLUSTER_CELL_PX, MAP_MAX_ZOOM, MAP_MIN_ZOOM, TIER_LABEL, fitWorldZoom, quantizeZoom, zoomTier } from "../places/tiers";
import { zoomToExpand } from "../places/cluster";
import { prefersReducedMotion } from "./motion";
import { Inspector } from "./Inspector";
import { networkChipText } from "./networkChip";
import { chainPlaceOrder, mapSummary, stepKeyDir, stepPlace } from "./mapAlt";
import { laneChipModel } from "./laneChip";
import { approxSeaLegs, laneIndexAt, type LaneSet } from "../lanes/laneIndex";
import { aisChipModel, type AisFeed } from "./aisChip";
import { useCascade } from "./useCascade";
import type {
  SimLayers,
  SimLayerKey,
} from "../sim/layersGate";
import { resolveSimLayers } from "../sim/layersGate";
import type { SimRunDetail, SimState, SimWeatherResponse } from "../sim/types";
import { buildSimIndex, activeDisruptionNodeIds } from "../sim/helpers";
import { BAND_LABEL, bandView, maxZoomForBasemap, zoomBand, type BandView } from "../sim/bands";
import { liveKeyLabel } from "../twin/live";
import { capFirst } from "../theme/railWord";
import {
  weatherLayerData,
  WEATHER_ATTRIBUTION,
  WEATHER_CELLS_LABEL,
  WEATHER_SAMPLE_LABEL,
} from "../sim/weather";
import {
  buildSimFlowLayers,
  buildSimLaneLayers,
  buildSimStateLayers,
} from "../sim/simLayers";
import { FLOW_STEP_MS } from "../sim/playback";
import { TwinController, type TwinMapLike } from "../twin/controller";
import { TwinChips, TwinHud, TwinSurface } from "../twin/TwinUi";
import type { LiveFeed } from "../twin/live";

/**
 * SIM map input. When `sim` is provided the map renders
 * the simulation ON TOP of the base graph: lanes from the run's
 * rendered paths, entities verbatim from the state payload, weather
 * from the pinned snapshot. Playback + layer state are HOST-owned (the
 * app layout store); the map renders what it is given and reports
 * toggles/transport intents through the callbacks — the host builds the
 * full control surface on the same props.
 */
export interface SimMapData {
  run: SimRunDetail;
  state: SimState;
  weather: SimWeatherResponse | null;
  layers: SimLayers;
  playing: boolean;
  /** Entity id of the selected vehicle (host-owned selection);
   *  the map rings it wherever it is individually rendered. */
  selectedVehicleId?: string | null;
}

export interface SupermAPMapProps {
  /** Graph data (992 nodes / 4,550 edges). Supplied by the host app —
   *  the API has no GET /graph endpoint; see data/loaders.ts. */
  graph: GraphData;
  /** True while the host's graph source is still loading
   *  (the app's graphSource === 'loading'). The NETWORK footer chip
   *  then renders "—" instead of the empty boot graph's counts —
   *  never "0 places · 0 routes" mid-boot. Absent = not loading. */
  graphLoading?: boolean;
  /** Chain response for the selected run (events + materialised edges[]).
   *  When provided, run arcs + node highlights + cascade transport render. */
  chain?: ChainResponse | null;
  /** PortWatch snapshot for the activity-dot layer (SNAPSHOT-labelled). */
  portwatch?: PortWatchSnapshot | null;
  /** Controlled theme. If omitted, the map manages theme internally and
   *  reports changes through the `setTheme` callback prop. */
  theme?: ThemeName;
  initialTheme?: ThemeName;
  defaultBasemap?: BasemapMode;
  /** Controlled selection. If omitted, selection is internal and reported
   *  through the `selectNode` callback prop. */
  selectedNodeId?: string | null;
  /** selectNode(id) — fired on node click (id) and on deselect (null). */
  selectNode?: (id: string | null) => void;
  /** setTheme(dark|light) — fired when the switcher changes theme.
   *  SAT is a basemap mode, not a theme, and does not fire this. */
  setTheme?: (theme: ThemeName) => void;
  /** Basemap reachability: fired on every status CHANGE —
   *  "unreachable" when the MapLibre style fails to load / its geography
   *  tiles error, "ok" again when a (re)tried style + tiles load. The map
   *  also renders the same signal as an on-map chip, so hosts that ignore
   *  this prop still surface it; the prop exists for shells that want to
   *  mirror it (e.g. a header banner). Optional — API stays compatible. */
  onBasemapStatus?: (status: BasemapStatus) => void;
  /** Controlled basemap mode (Satellite toggle). When
   *  provided, the host owns the basemap; internal switches report through
   *  onBasemapModeChange instead of mutating internal state. */
  basemapMode?: BasemapMode;
  onBasemapModeChange?: (mode: BasemapMode) => void;
  /** SATELLITE imagery source: "static" (EOX 2016, CC BY 4.0) or "daily"
   *  (NASA GIBS true colour, an explicit acquisition day). Optional; the map
   *  owns it when absent (default "static"). Imagery is context, not detections. */
  satSource?: SatSource;
  onSatSourceChange?: (source: SatSource) => void;
  /** Controlled layer visibility (rail layer group + HUD toggles).
   *  When provided, lanes/activity read from here and the HUD checkboxes
   *  report through onLayerVisChange. ports/chokes/air filter node
   *  classes + air edges in the deck layers. */
  layerVis?: { lanes: boolean; activity: boolean; ports: boolean; chokes: boolean; air: boolean };
  onLayerVisChange?: (patch: Partial<{ lanes: boolean; activity: boolean; ports: boolean; chokes: boolean; air: boolean }>) => void;
  /** LANE GEOMETRY: lane polylines of the sea legs (GET /graph/lanes/geometry?scope=sea),
   *  simplified and full. With it the legs layer and the chain arcs between sea places follow the lanes
   *  (simplified below zoom 4); sea legs without one stay chords, dashed, with an Approx. chip.
   *  Omit/null = the chords of every leg, as before. */
  lanes?: LaneSet | null;
  /** The lane feed failed: the map stays on chords and the strip says Lane geometry NOT SERVED. */
  lanesNotServed?: boolean;
  /** SIM: simulation rendering input. Omit/null in
   *  LIVE/CANNED — the sim layer stack, transport bar and SIMULATED
   *  labelling render only while a sim is supplied, and removing it
   *  restores the exact prior LIVE/CANNED rendering. (the
   *  layer toggles themselves live in the host's SIM panel, not on
   *  the canvas — see the HUD note at the render site.) */
  sim?: SimMapData | null;
  /** Fired with the entity id when a vessel / aircraft is
   *  picked on the map (PORT band and deeper only). */
  onSimVehiclePick?: (entityId: string | null) => void;
  /** DIGITAL TWIN renderer (default true): the simulated fleet is drawn
   *  from typed-array buffers with per-frame interpolation along each
   *  leg, speed colour, fading trails, level of detail, hover chips,
   *  click-to-select with route preview, box / lasso counts and
   *  class / state filters. false = the legacy per-tick layers. */
  twin?: boolean;
  /** LIVE entities layer (observations, never merged with the
   *  simulated fleet): `{status, columns:{ids,lon,lat,heading,speed,
   *  kind,age_s}}` as served by GET /live/entities. status
   *  "not_served" renders the NOT SERVED chip, "down" hides the
   *  layer. Omit/null = no LIVE chip. */
  liveEntities?: LiveFeed | null;
  /** The AIS feed's real state, read by the host from its source-health
   *  board (so the map chip, the map bar and the status bar can never
   *  disagree). Omit = the standalone page: the historical
   *  "AIS - NO KEY / NEEDS-KEY" chip. */
  aisFeed?: AisFeed | null;
  /** Fired when a SIM layer toggle flips in the host's SIM panel
   *  layer stack (the host persists the record — layout store). */
  onSimLayerToggle?: (key: SimLayerKey, on: boolean) => void;
  /** Suppress the map's minimal sim transport bar — the
   *  host's timeline (same callbacks, grown-up control) is the one
   *  time control. Default true (the standalone page keeps its bar). */
  simTransport?: boolean;
  /** Minimal sim transport intents (the host owns the
   *  full control surface). */
  onSimPlay?: () => void;
  onSimPause?: () => void;
  onSimStep?: (delta: number) => void;
  onSimSeek?: (tick: number) => void;
  /** PLACE FOCUS: when false, focus never engages —
   *  the host sets this while the AUTO DEMO owns the camera. Default
   *  true. Marker clicks and the imperative focusNode engage focus;
   *  plain selection changes (chain rows) do not. */
  focusEnabled?: boolean;
  /** When false the place card (inspector / focus box) is not drawn although a place is selected; the host
   *  uses it so the boot selection does not open a card over the map. Default true. */
  placeCard?: boolean;
  /** Focus box PLACE DESK action — the host opens its full place
   *  inspector for the focused place. Omit to hide the action. */
  onOpenInspector?: (id: string) => void;
  /** Goods-summary lines for the SELECTED place, derived by the host
   *  from its served IMPACT payload (the map renders them verbatim in
   *  the focus box; null/absent = no goods block). */
  focusGoods?: FocusGoodsLine[] | null;
  /** PLACE PAINT: host-supplied per-place fill paint. Entries keyed
   *  by place id carry a colour + optional opacity that override the
   *  place's state colour for its disc / shape FILL only — state
   *  rings, severity markers, badges and halos keep rendering over
   *  the painted fill, and places not listed render normally. The
   *  legend entries render in the map's legend stack alongside the
   *  kind legend, verbatim. Null/absent = rendering identical to
   *  the map without the prop. */
  placePaint?: PlacePaint | null;
  /** Controlled cascade step (highest revealed seq). Omit for built-in transport. */
  cascadeStep?: number;
  onCascadeStepChange?: (seq: number) => void;
  autoPlayCascade?: boolean;
  /** Cascade stagger, ms per seq step. Default 300. */
  staggerMs?: number;
  /** Show the built-in cascade transport bar when a chain is loaded. Default true. */
  showTransport?: boolean;
  initialCenter?: [number, number];
  initialZoom?: number;
  className?: string;
  style?: React.CSSProperties;
}

export interface SupermAPMapHandle {
  flyToNode: (id: string) => void;
  /** Engage place focus on a place (select + fly + focus box).
   *  Inert while focusEnabled is false (AUTO DEMO). */
  focusNode: (id: string) => void;
  /** Close the focus box and restore the pre-focus camera. */
  clearFocus: () => void;
  setBasemap: (mode: BasemapMode) => void;
  playCascade: () => void;
  pauseCascade: () => void;
  resetCascade: () => void;
  getMap: () => maplibregl.Map | null;
}

const BASEMAP_LABEL: Record<BasemapMode, string> = {
  dark: "Dark",
  light: "Light",
  sat: "Satellite",
};

/** Kind swatch colour (css rgb) for the key. */
const rgbCss = (c: ReadonlyArray<number>): string => `rgb(${c[0]},${c[1]},${c[2]})`;
const rgbOfHex = (h: string): number[] => [
  parseInt(h.slice(1, 3), 16),
  parseInt(h.slice(3, 5), 16),
  parseInt(h.slice(5, 7), 16),
];

/** How long a basemap style attempt may stay unloaded before the map
 *  declares the basemap unreachable (the MapLibre `error` event usually
 *  fires long before this; the timer is the backstop for silent hangs). */
const BASEMAP_LOAD_TIMEOUT_MS = 10_000;

/**
 * SupermAP map. MapLibre GL basemap (CARTO dark/light + imagery satellite)
 * with deck.gl layers for the graph, run arcs, cascade
 * animation and the PortWatch SNAPSHOT activity dots.
 */
export const SupermAPMap = forwardRef<SupermAPMapHandle, SupermAPMapProps>(
  function SupermAPMap(props, ref) {
    const {
      graph,
      chain,
      portwatch,
      aisFeed,
      selectNode,
      setTheme,
      initialTheme = "dark",
      defaultBasemap,
      autoPlayCascade = false,
      staggerMs = 300,
      showTransport = true,
      /* Default framing: lon −124…140, lat −58…88 at 940px
       * → centre ≈ [8, 15], zoom ≈ 1.35. */
      initialCenter = [8, 15],
      initialZoom: initialZoomProp,
    } = props;
    const initialZoom = initialZoomProp ?? 1.35;

    const [themeState, setThemeState] = useState<ThemeName>(props.theme ?? initialTheme);
    const theme = props.theme ?? themeState;
    const [basemapState, setBasemapState] = useState<BasemapMode>(
      defaultBasemap ?? pairedBasemap(theme),
    );
    const basemap = props.basemapMode ?? basemapState;
    const [satSourceState, setSatSourceState] = useState<SatSource>("static");
    const satSource: SatSource = props.satSource ?? satSourceState;
    /* GIBS acquisition day: undefined = resolving, null = none served. */
    const [dailyDate, setDailyDate] = useState<string | null | undefined>(undefined);
    const satSourceRef = useRef(satSource);
    satSourceRef.current = satSource;
    const hasPortwatch = !!(portwatch && portwatch.records && portwatch.records.length);
    const portwatchRef = useRef(hasPortwatch);
    portwatchRef.current = hasPortwatch;
    const dailyDateRef = useRef(dailyDate);
    dailyDateRef.current = dailyDate;
    const satNotServedRef = useRef(false);
    satNotServedRef.current = basemap === "sat" && satSource === "daily" && dailyDate === null;
    /* The day is still resolving: no imagery yet, so the coastline underlay stays up. */
    const satPendingRef = useRef(false);
    satPendingRef.current = basemap === "sat" && satSource === "daily" && dailyDate === undefined;
    /* SATELLITE pairs with the dark chrome: while the imagery basemap
     *  is up, the map's own layers + furniture paint from the dark
     *  token set whatever the host theme is (legibility over tiles).
     *  The host's panels outside the map keep the host theme. */
    const effTheme: ThemeName = basemap === "sat" ? "dark" : theme;
    const [internalSelected, setInternalSelected] = useState<string | null>(null);
    const selectedId =
      props.selectedNodeId !== undefined ? props.selectedNodeId : internalSelected;
    const [hoverId, setHoverId] = useState<string | null>(null);
    const [showGraphEdgesState, setShowGraphEdgesState] = useState(true);
    const [showActivityState, setShowActivityState] = useState(true);
    const showGraphEdges = props.layerVis ? props.layerVis.lanes : showGraphEdgesState;
    const showActivity = props.layerVis ? props.layerVis.activity : showActivityState;
    const [zoom, setZoom] = useState(initialZoom);
    /* Quantised zoom (0.25 steps) drives the place / leg layer stack:
     * deck scales sizes per frame, the stack rebuilds only when this
     * steps — a bounded number of rebuilds per zoom level, no popping. */
    const [zoomQ, setZoomQ] = useState(() => quantizeZoom(initialZoom));
    const [keyOpen, setKeyOpen] = useState(false);
    const mapDescId = `sm-map-desc-${useId().replace(/:/g, "")}`;
    const stepPlaceKeyRef = useRef<(e: KeyboardEvent) => void>(() => {});
    const [scale, setScale] = useState<{ label: string; px: number }>({ label: "2000 km", px: 76 });
    const [pulsePhase, setPulsePhase] = useState(0);
    const [calloutPos, setCalloutPos] = useState<{ x: number; y: number; flip: boolean; shiftX: number; shiftY: number } | null>(null);

    /* ---- PLACE FOCUS state ----
     * The machine lives in components/focus.ts (pure);
     * this component owns the camera moves and the box's projected
     * anchor. focusEnabled=false (AUTO DEMO) drops any session with
     * no camera restore — the demo owns the camera. */
    const [focus, setFocus] = useState<FocusState>(IDLE_FOCUS);
    const focusRef = useRef(focus);
    focusRef.current = focus;
    const focusEnabledRef = useRef(props.focusEnabled !== false);
    focusEnabledRef.current = props.focusEnabled !== false;
    const [focusPos, setFocusPos] = useState<{
      x: number; y: number; flip: boolean; shiftX: number; shiftY: number; docked: boolean;
    } | null>(null);
    const attrRef = useRef<maplibregl.AttributionControl | null>(null);

    const containerRef = useRef<HTMLDivElement>(null);
    const mapRef = useRef<maplibregl.Map | null>(null);
    const overlayRef = useRef<MapboxOverlay | null>(null);
    const basemapRef = useRef(basemap);
    basemapRef.current = basemap;

    /* ---- basemap reachability signal ----
     * MapLibre is the source of truth: its `error` event is GL's primary
     * error surface — style-JSON load failures and (non-404) tile load
     * failures both bubble up to the map as ErrorEvents (tile failures
     * carry a `tile` payload). `styledata` for the current attempt +
     * successful per-tile `sourcedata` mark the basemap healthy again,
     * so a theme/basemap switch that retries and succeeds flips the
     * status back to "ok". A per-attempt timeout covers silent hangs. */
    const [basemapStatus, setBasemapStatus] = useState<BasemapStatus>("ok");
    const basemapStatusRef = useRef<BasemapStatus>("ok");
    const onBasemapStatusRef = useRef(props.onBasemapStatus);
    onBasemapStatusRef.current = props.onBasemapStatus;
    const basemapAttemptRef = useRef(0);
    const styleLoadedRef = useRef(false);
    const basemapTimerRef = useRef<number | null>(null);

    /* ---- coastline underlay visibility ----
     * The bundled Natural Earth underlay (theme/underlay.ts) is visible
     * unless the current style attempt has loaded AND the basemap status
     * signal says "ok" — i.e. it shows while tiles are loading or
     * unreachable (in SAT: only while imagery is unreachable), and hides
     * once real tiles are in so no double coastline can appear. This is
     * derived FROM the status signal; it never feeds back into it, so
     * the BASEMAP UNREACHABLE chip semantics are unchanged. */
    const [underlayVisible, setUnderlayVisible] = useState(true);
    const underlayVisibleRef = useRef(true);
    /* True while the LOCAL fallback style (ocean bg + underlay only) is
     * the map's style. Its load must never be read as a basemap load:
     * markBasemapLoaded callers check this flag, so the UNREACHABLE chip
     * stays up for as long as real tiles are unreachable. Reset at the
     * start of every real basemap attempt. */
    const fallbackStyleActiveRef = useRef(false);
    /** If the current attempt left the map with NO style at all (the
     *  remote style JSON failed — a style-less map cannot accept the
     *  underlay source/layers), swap in the local fallback style so the
     *  coastline still renders; if the fallback style is on screen but
     *  painted for the previous theme (the new remote style is hanging
     *  — a pending setStyle leaves the old style up), re-paint it for
     *  the current mode. No-op when a real style is loaded. */
    const ensureFallbackStyle = useCallback(() => {
      const map = mapRef.current;
      if (!map) return;
      if (syncFallbackStyle(map, basemapRef.current)) {
        fallbackStyleActiveRef.current = true;
      }
    }, []);
    const syncUnderlayVisibility = useCallback(() => {
      const visible = !(
        styleLoadedRef.current && basemapStatusRef.current === "ok" && !satPendingRef.current
      );
      if (underlayVisibleRef.current !== visible) {
        underlayVisibleRef.current = visible;
        setUnderlayVisible(visible);
      }
      const map = mapRef.current;
      if (map) setUnderlayVisibility(map, visible);
    }, []);

    const reportBasemapStatus = useCallback(
      (status: BasemapStatus) => {
        const changed = basemapStatusRef.current !== status;
        basemapStatusRef.current = status;
        if (status === "unreachable") ensureFallbackStyle();
        syncUnderlayVisibility();
        if (!changed) return;
        setBasemapStatus(status);
        onBasemapStatusRef.current?.(status);
      },
      [syncUnderlayVisibility, ensureFallbackStyle],
    );

    const clearBasemapTimer = useCallback(() => {
      if (basemapTimerRef.current !== null) {
        window.clearTimeout(basemapTimerRef.current);
        basemapTimerRef.current = null;
      }
    }, []);

    /** A (re)tried style loaded: basemap healthy, cancel the watchdog. */
    const markBasemapLoaded = useCallback(() => {
      styleLoadedRef.current = true;
      clearBasemapTimer();
      /* Daily imagery with no served day has no tiles: that is NOT SERVED,
       * never a healthy basemap. */
      reportBasemapStatus(satNotServedRef.current ? "unreachable" : "ok");
      syncUnderlayVisibility();
    }, [clearBasemapTimer, reportBasemapStatus, syncUnderlayVisibility]);

    /** Start watching a new style attempt (initial style or setStyle). */
    const beginBasemapAttempt = useCallback(() => {
      const attempt = ++basemapAttemptRef.current;
      styleLoadedRef.current = false;
      fallbackStyleActiveRef.current = false;
      syncUnderlayVisibility();
      clearBasemapTimer();
      basemapTimerRef.current = window.setTimeout(() => {
        if (
          basemapAttemptRef.current === attempt &&
          !styleLoadedRef.current
        ) {
          reportBasemapStatus("unreachable");
        }
      }, BASEMAP_LOAD_TIMEOUT_MS);
    }, [clearBasemapTimer, reportBasemapStatus, syncUnderlayVisibility]);

    const nodeById = useMemo(() => {
      const m = new Map<string, GraphNode>();
      for (const n of graph.nodes) m.set(n.id, n);
      return m;
    }, [graph]);

    const activityByNode = useMemo(() => {
      const m = new Map<string, PortActivityRecord>();
      for (const r of portwatch?.records ?? []) {
        const prev = m.get(r.node_id);
        if (!prev || r.vessel_count_total > prev.vessel_count_total) m.set(r.node_id, r);
      }
      return m;
    }, [portwatch]);

    /* events -> per-node run state (first hit per node for badges/arcs;
     * inspector shows that node's earliest-seq event, matching the DAG). */
    const cascade = useCascade(chain?.events, {
      staggerMs,
      autoPlay: autoPlayCascade,
      step: props.cascadeStep,
      onStepChange: props.onCascadeStepChange,
    });
    const runStateByNode = useMemo(() => {
      const m = new Map<string, NodeRunState>();
      for (const ev of cascade.ordered) {
        if (!m.has(ev.node_id)) {
          m.set(ev.node_id, {
            seq: ev.seq,
            channel: ev.channel,
            mechanism: ev.mechanism,
            delay_d: ev.delay_d,
            magnitude: ev.magnitude,
            at: ev.at,
          });
        }
      }
      return m;
    }, [cascade.ordered]);

    const pulseSeq = chain && cascade.step >= 0 ? cascade.step : null;

    /* pulse ticker: requestAnimationFrame, and ONLY while the
     * cascade is actually playing with a step revealed and not yet
     * at its end. Paused, finished, or no revealed step → no ticker
     * at all (the halo renders one still frame at phase 0). The tick
     * feeds pulsePhase only, which rebuilds only the pulse layer —
     * the static layer stack is memoised apart (see layers below).
     * under prefers-reduced-motion the ticker never runs. */
    const pulseActive = cascade.playing && pulseSeq !== null && !cascade.atEnd;
    useEffect(() => {
      if (!pulseActive) {
        setPulsePhase(0);
        return;
      }
      if (prefersReducedMotion()) return;
      const t0 = performance.now();
      let raf = 0;
      const tick = () => {
        setPulsePhase(((performance.now() - t0) % 1200) / 1200);
        raf = window.requestAnimationFrame(tick);
      };
      raf = window.requestAnimationFrame(tick);
      return () => window.cancelAnimationFrame(raf);
    }, [pulseActive]);

    /** The basemap mode the map's style was last set for (null before init). */
    const appliedModeRef = useRef<BasemapMode | null>(basemap);

    /** The dark <-> light shortcut: diffed setStyle with the cached JSON of
     *  the other family, underlay carried across, theme paint re-applied, all
     *  synchronously. Returns false (nothing changed or a rebuild is in
     *  flight and owns the outcome) when the caller must run the full path. */
    const swapFamilyInPlace = useCallback(
      (map: maplibregl.Map, mode: BasemapMode, json: StyleDoc): boolean => {
        const before = (map as unknown as { style?: unknown }).style;
        try {
          beginBasemapAttempt();
          map.setStyle(json as never, {
            diff: true,
            transformStyle: ((prev: StyleDoc, next: StyleDoc) =>
              withUnderlayCarried(prev, next)) as never,
          });
        } catch {
          return false;
        }
        if ((map as unknown as { style?: unknown }).style !== before) {
          /* The diff could not be applied and MapLibre rebuilt the style
           * from scratch (async). style.load re-asserts underlay + paint;
           * mark the attempt loaded when it lands. */
          map.once("style.load", () => {
            if (!fallbackStyleActiveRef.current) markBasemapLoaded();
          });
          return true;
        }
        ensureUnderlay(map, mode, underlayVisibleRef.current);
        if (mode === "dark") applyDarkPaint(map);
        else if (mode === "light") applyLightPaint(map);
        markBasemapLoaded();
        return true;
      },
      [beginBasemapAttempt, markBasemapLoaded],
    );

    const applyBasemap = useCallback(
      (map: maplibregl.Map, mode: BasemapMode) => {
        const style =
          mode === "dark"
            ? CARTO_DARK_STYLE
            : mode === "light"
              ? CARTO_LIGHT_STYLE
              : satelliteStyle(satSourceRef.current, dailyDateRef.current);
        const prevMode = appliedModeRef.current;
        appliedModeRef.current = mode;
        /* Dark <-> light is the same style skeleton (same source, same
         * layer ids): when a real style is loaded and the other family's
         * JSON is already cached, move there with a DIFFED setStyle (only
         * the differing paint values and the sprite) instead of rebuilding
         * the whole style. See theme/styleSwap.ts for the why and the
         * measurements. Anything unusual takes the full path below. */
        if (
          isFamilySwap(prevMode, mode) &&
          styleLoadedRef.current &&
          !fallbackStyleActiveRef.current
        ) {
          const cached = cachedStyleJson(style as string);
          if (cached && swapFamilyInPlace(map, mode, cached)) return;
        }
        beginBasemapAttempt();
        /* diff:false = always a full style rebuild, so `style.load`
         * fires and the theme paint is re-applied. A diffed update
         * (the default when the SAME style is set again, e.g. a double
         * effect run) silently reverts the re-paint to the style's
         * native colours and fires no `style.load`. */
        map.setStyle(style as never, { diff: false });
        map.once("styledata", () => {
          // The (re)tried style arrived — a theme/basemap switch that
          // succeeds flips the reachability signal back to "ok".
          // setStyle wiped custom layers: re-assert the underlay first.
          ensureUnderlay(map, basemapRef.current, underlayVisibleRef.current);
          // If this styledata is the LOCAL fallback style loading (the
          // real style failed), it is NOT a basemap load — the attempt
          // stays unreachable and the underlay stays visible.
          if (!fallbackStyleActiveRef.current) markBasemapLoaded();
          // Theme paint is NOT applied here: this one-shot listener can
          // be consumed by the outgoing style's traffic on an unsettled
          // switch. Paint rides style.load (see the lifecycle effect).
        });
      },
      [beginBasemapAttempt, markBasemapLoaded, swapFamilyInPlace],
    );

    /* ---- map lifecycle (once) ---- */
    useEffect(() => {
      if (!containerRef.current || mapRef.current) return;
      const initial =
        basemapRef.current === "dark"
          ? CARTO_DARK_STYLE
          : basemapRef.current === "light"
            ? CARTO_LIGHT_STYLE
            : satelliteStyle(satSourceRef.current, dailyDateRef.current);
      const z0 = initialZoomProp ?? fitWorldZoom(containerRef.current.clientWidth);
      setZoom(z0);
      setZoomQ(quantizeZoom(z0));
      const map = new maplibregl.Map({
        container: containerRef.current,
        style: initial as never,
        center: initialCenter,
        zoom: z0,
        /* The map would otherwise add its OWN default attribution
         * control alongside the explicit one below: two stacked
         * controls in the bottom-right corner, whose upper toggle
         * lands mid-corner — on phone-width maps it sat under the
         * cascade bar's slider and could not be reached. The explicit control is the one legal line. */
        attributionControl: false,
        minZoom: MAP_MIN_ZOOM,
        /* Zoom policy: maxZoom 16 on CARTO styles, 18
         * on SAT (imagery) — kept in sync on basemap switches below. */
        maxZoom: maxZoomForBasemap(basemapRef.current),
        renderWorldCopies: true,
        locale: { "Map.Title": "Map of places and legs" },
      });
      mapRef.current = map;
      /* text alternative + keyboard stepping: the canvas is described by the summary below */
      map.getCanvas().setAttribute("aria-describedby", mapDescId);
      const onCanvasKey = (e: KeyboardEvent) => stepPlaceKeyRef.current(e);
      map.getCanvas().addEventListener("keydown", onCanvasKey);
      /* Attribution: ONE terse legal line inside the attribution
       * control (bottom-right), per basemap style — the only place
       * basemap credit appears on any surface. The control is held so
       * its line can be swapped on basemap/underlay changes (the
       * control has no setter; remove + re-add is the mechanism). */
      const attr = new maplibregl.AttributionControl({
        compact: false,
        customAttribution: attributionHtml(
          basemapRef.current,
          underlayVisibleRef.current,
          satSourceRef.current,
          portwatchRef.current,
        ),
      });
      map.addControl(attr, "bottom-right");
      attrRef.current = attr;
      beginBasemapAttempt();
      /* Basemap reachability via MapLibre's own error surface:
       *  - an `error` before the current style has loaded = the style
       *    fetch/parse failed (CARTO style JSON unreachable);
       *  - an `error` carrying a `tile` = a geography tile failed —
       *    this is also how SAT (inline style, EOX or NASA GIBS raster tiles) fails.
       *  Other post-load errors (e.g. a missing sprite image) are NOT
       *  basemap reachability and must not raise the chip. */
      map.on("error", (e: { tile?: unknown }) => {
        if (e?.tile !== undefined || !styleLoadedRef.current) {
          reportBasemapStatus("unreachable");
        }
      });
      /* A geography tile that loads successfully after a failure means
       *  the basemap host is reachable again (pan/retry recovery). */
      map.on("sourcedata", (e) => {
        if (e.dataType === "source" && e.tile && styleLoadedRef.current) {
          reportBasemapStatus("ok");
        }
      });
      /* Coastline underlay: (re)asserted on EVERY style load — the
       * initial style and every setStyle from a theme/basemap switch —
       * because setStyle wipes custom sources/layers. */
      map.on("style.load", () => {
        ensureUnderlay(map, basemapRef.current, underlayVisibleRef.current);
        /* Theme paint lives HERE: paint is a property of
         * the style that actually loaded, applied on every style.load
         * for the CURRENT mode. Painting from a one-shot
         * `styledata` listener registered at switch time would fail — a toggle
         * during boot or a pending load let unrelated styledata traffic
         * consume it, stranding the incoming style in native CARTO
         * paint. The local fallback style is skipped: its background
         * is the ocean token by construction (buildFallbackStyle) and
         * CARTO paint rules would repaint it as land. */
        if (!fallbackStyleActiveRef.current) {
          if (basemapRef.current === "dark") applyDarkPaint(map);
          if (basemapRef.current === "light") applyLightPaint(map);
        }
      });
      const overlay = new MapboxOverlay({ interleaved: false, layers: [] });
      overlayRef.current = overlay;
      map.addControl(overlay as unknown as maplibregl.IControl);
      /* The deck.gl layer canvas is a picture layer over the map canvas, which is the one keyboard stop and the one
       * named region: the overlay canvas is kept out of the tab order and out of the accessibility tree. */
      const quietDeckCanvas = () => {
        containerRef.current?.querySelectorAll<HTMLCanvasElement>("canvas:not(.maplibregl-canvas)").forEach((c) => {
          if (c.tabIndex !== -1) c.tabIndex = -1;
          if (c.getAttribute("aria-hidden") !== "true") c.setAttribute("aria-hidden", "true");
        });
      };
      quietDeckCanvas();
      const deckWatch = new MutationObserver(quietDeckCanvas);
      if (containerRef.current) deckWatch.observe(containerRef.current, { childList: true, subtree: true });
      /* Zoom controls top-right, under the overlay bar. */
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      /* Zoom bucket for the base-edge treatment + the scale
       * bar's served measurement (metres-per-pixel from the live map
       * state; the bar picks the largest round distance ≤ ~120px). */
      let zoomRaf = 0;
      const onZoom = () => {
        if (zoomRaf) return;
        zoomRaf = window.requestAnimationFrame(() => {
          zoomRaf = 0;
          const q = quantizeZoom(map.getZoom());
          setZoomQ((prev) => (prev === q ? prev : q));
        });
      };
      map.on("zoom", onZoom);
      const updateScale = () => {
        setZoom(map.getZoom());
        setZoomQ(quantizeZoom(map.getZoom()));
        const c = map.getCenter();
        const mpp = (78271.51697 * Math.cos((c.lat * Math.PI) / 180)) / Math.pow(2, map.getZoom());
        const steps: Array<[number, string]> = [
          [2000000, "2000 km"], [1000000, "1000 km"], [500000, "500 km"], [200000, "200 km"],
          [100000, "100 km"], [50000, "50 km"], [20000, "20 km"], [10000, "10 km"],
          [5000, "5 km"], [2000, "2 km"], [1000, "1 km"],
        ];
        for (const [m, label] of steps) {
          const px = m / mpp;
          if (px <= 120) { setScale({ label, px: Math.round(px) }); return; }
        }
        setScale({ label: "500 m", px: Math.round(500 / mpp) });
      };
      map.on("moveend", updateScale);
      map.on("load", updateScale);
      map.on("load", () => {
        ensureUnderlay(map, basemapRef.current, underlayVisibleRef.current);
        // A first "load" can be the local fallback style's — not a
        // basemap load (see applyBasemap's styledata handler). Theme
        // paint rides style.load, which precedes this event.
        if (!fallbackStyleActiveRef.current) markBasemapLoaded();
      });
      // Node clicks are handled by deck.gl picking (layer onClick);
      // deselection happens via the inspector close control / host app.
      return () => {
        clearBasemapTimer();
        deckWatch.disconnect();
        if (zoomRaf) window.cancelAnimationFrame(zoomRaf);
        map.remove();
        mapRef.current = null;
        overlayRef.current = null;
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    /* maxZoom policy follows the basemap (16 CARTO / 18 SAT) */
    useEffect(() => {
      mapRef.current?.setMaxZoom(maxZoomForBasemap(basemap));
    }, [basemap]);

    /* attribution line follows the basemap + underlay visibility */
    useEffect(() => {
      const map = mapRef.current;
      const prev = attrRef.current;
      if (!map || !prev) return;
      map.removeControl(prev as unknown as maplibregl.IControl);
      /* The credit text must be legible on every basemap, so the control is
       * expanded (not the collapsed "i" button). */
      const next = new maplibregl.AttributionControl({
        compact: false,
        customAttribution: attributionHtml(basemap, underlayVisible, satSource, hasPortwatch),
      });
      map.addControl(next, "bottom-right");
      attrRef.current = next;
    }, [basemap, underlayVisible, satSource, hasPortwatch]);

    /* GIBS acquisition day: today (UTC) minus 1, else the day before; none = NOT SERVED.
     * Re-resolved whenever DAILY is (re)selected. */
    useEffect(() => {
      if (basemap !== "sat" || satSource !== "daily") return;
      let cancelled = false;
      setDailyDate(undefined);
      resolveDailyDate(new Date(), fetchProbe).then((r) => {
        if (!cancelled) setDailyDate(r.date);
      });
      return () => {
        cancelled = true;
      };
    }, [basemap, satSource]);

    const setSatSource = useCallback(
      (next: SatSource) => {
        if (props.satSource === undefined) setSatSourceState(next);
        props.onSatSourceChange?.(next);
      },
      [props.satSource, props.onSatSourceChange],
    );
    const imageryChipView = useMemo(
      () => imageryChip({ source: satSource, date: dailyDate, now: new Date() }),
      // utcDay: a tab left open across UTC midnight re-evaluates the age of the served day
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [satSource, dailyDate, new Date().toISOString().slice(0, 10)],
    );

    /* ---- SIM derived render data ----
     * All display-only projections of served payloads: the index
     * converts the run's rendered lane paths once; the band view
     * aggregates the CURRENT state payload for the CURRENT zoom band;
     * weather points project the pinned snapshot at the tick in view.
     * Entity positions are never computed here. */
    const sim = props.sim ?? null;
    /* Memo keys are the STABLE parts of the sim prop (run, layer
     * record, node/weather facts): `sim` itself is a fresh object on
     * every playback update (the host replaces the display state ~4x a
     * second), and keying on it rebuilt the index of ~4,550 lanes and
     * the lane geometry on every update. */
    const simRun = sim?.run ?? null;
    const simLayerRec = sim?.layers ?? null;
    const simResolved = useMemo(
      () => (simLayerRec ? resolveSimLayers(simLayerRec) : null),
      [simLayerRec],
    );
    const simIndex = useMemo(
      () => (simRun ? buildSimIndex(simRun, graph) : null),
      [simRun, graph],
    );
    const twinOn = props.twin !== false;
    /* The fleet key wears Live only while the live layer's own chip does (feed live now, observations fresh). */
    const fleetKey = props.liveEntities ? liveKeyLabel(props.liveEntities) : null;
    const bandName = zoomBand(zoom);
    const simBandView = useMemo((): BandView | null => {
      if (!sim || !simIndex) return null;
      /* twin: individuals are drawn by the fleet engine at every
       * band, so only the band name is needed (no per-update
       * grouping of ~4,000 entities by lane). */
      if (twinOn) return { band: bandName, individual: [], chips: [], aggregated: [] };
      return bandView(sim.state, simIndex.lanesByKey, simIndex.lonLatPaths, zoom);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [!!sim, simIndex, twinOn, twinOn ? bandName : sim?.state, twinOn ? 0 : zoom]);
    const simStateNodes = sim?.state.nodes;
    const simStateTime = sim?.state.sim_time;
    const simWeather = useMemo(
      () =>
        sim && simIndex
          ? weatherLayerData(sim.weather, sim.state, simIndex.lanesByKey, simIndex.nodePos)
          : null,
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [sim?.weather, simStateNodes, simStateTime, simIndex],
    );
    const simDisruptions = sim?.state.disruptions_active;
    const simDisruptionNodes = useMemo(
      () => (sim ? activeDisruptionNodeIds(sim.run, sim.state) : new Set<string>()),
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [simRun, simDisruptions],
    );
    /* FLOWS step clock: the cascade's 300 ms/step language (FLOW_STEP_MS)
     * — pulses advance one step per interval while a sim is on screen
     * with FLOWS effective; never a detached free-running loop (the
     * interval exists only in SIM mode and clears with it). */
    const [flowStep, setFlowStep] = useState(0);
    const flowsOn = !!simResolved?.effective.FLOWS;
    useEffect(() => {
      if (!flowsOn) return;
      const id = window.setInterval(() => setFlowStep((s) => s + 1), FLOW_STEP_MS);
      return () => window.clearInterval(id);
    }, [flowsOn]);
    /* the sim stack is memoised in THREE parts matching the
     * split builders in sim/simLayers.ts. The lane-geometry memo's
     * deps deliberately exclude flowStep (and state), so the 300 ms
     * flow clock rebuilds ONLY the small flow-pulse layer — never the
     * ~4,550 lane routes. The state memo likewise excludes flowStep. */
    const simLaneLayers = useMemo(
      () =>
        sim && simIndex && simBandView && simResolved
          ? buildSimLaneLayers({
              theme: effTheme,
              lanes: sim.run.derivation.lanes,
              subpathsByLane: simIndex.subpathsByLane,
              layers: simResolved.effective,
              bandView: simBandView,
              placeTiers: simIndex.nodeTier,
            })
          : [],
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [simRun, simIndex, simBandView, simResolved, effTheme],
    );
    const simFlowLayers = useMemo(
      () =>
        sim && simIndex && simBandView && simResolved
          ? buildSimFlowLayers({
              theme: effTheme,
              lanes: sim.run.derivation.lanes,
              lonLatPaths: simIndex.lonLatPaths,
              state: sim.state,
              layers: simResolved.effective,
              bandView: simBandView,
              flowStep,
              placeTiers: simIndex.nodeTier,
            })
          : [],
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [simRun, sim?.state.state_hash, sim?.state.tick, simIndex, simBandView, simResolved, effTheme, flowStep],
    );
    /* vehicle picking: the callback is read through a ref (the
     *  pattern) so a fresh inline host callback never rebuilds
     *  the state layers; the stable wrapper is what the layers hold. */
    const onSimVehiclePickRef = useRef(props.onSimVehiclePick);
    onSimVehiclePickRef.current = props.onSimVehiclePick;
    const handleSimVehiclePick = useCallback((entityId: string | null) => {
      onSimVehiclePickRef.current?.(entityId);
    }, []);
    const simStateLayers = useMemo(
      () =>
        sim && simIndex && simBandView && simResolved
          ? buildSimStateLayers({
              theme: effTheme,
              lanes: sim.run.derivation.lanes,
              lanesByKey: simIndex.lanesByKey,
              subpathsByLane: simIndex.subpathsByLane,
              lonLatPaths: simIndex.lonLatPaths,
              state: sim.state,
              layers: simResolved.effective,
              bandView: simBandView,
              nodePos: simIndex.nodePos,
              placeTiers: simIndex.nodeTier,
              weather: simWeather,
              disruptionNodeIds: simDisruptionNodes,
              onVehiclePick: handleSimVehiclePick,
              selectedVehicleId: sim.selectedVehicleId ?? null,
              twinEntities: twinOn,
            })
          : [],
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [
        simRun,
        twinOn ? simStateNodes : sim?.state,
        sim?.state.weather_cells,
        sim?.selectedVehicleId,
        simIndex,
        simBandView,
        simResolved,
        effTheme,
        simWeather,
        simDisruptionNodes,
        handleSimVehiclePick,
        twinOn,
      ],
    );
    const simLayersBuilt = useMemo(
      () => [...simLaneLayers, ...simFlowLayers, ...simStateLayers],
      [simLaneLayers, simFlowLayers, simStateLayers],
    );

    /* a change of imagery source or of the resolved day rebuilds the SATELLITE style */
    const satKey = basemap === "sat" ? `${satSource}:${String(dailyDate)}` : "";
    /* basemap switching after init */
    const firstRender = useRef(true);
    useEffect(() => {
      if (firstRender.current) {
        firstRender.current = false;
        return;
      }
      const map = mapRef.current;
      if (map) applyBasemap(map, basemap);
    }, [basemap, applyBasemap, satKey]);

    /* Warm the style-JSON cache for BOTH CARTO families shortly after a
     * dark/light basemap is up, so the next theme switch can take the
     * diffed shortcut (theme/styleSwap.ts). Best effort: an unreachable
     * host leaves the cache empty and the switch takes the full path. */
    useEffect(() => {
      if (basemap !== "dark" && basemap !== "light") return;
      const other = basemap === "dark" ? CARTO_LIGHT_STYLE : CARTO_DARK_STYLE;
      const own = basemap === "dark" ? CARTO_DARK_STYLE : CARTO_LIGHT_STYLE;
      const t = window.setTimeout(() => {
        void prefetchStyleJson(other).then(() => prefetchStyleJson(own));
      }, 800);
      return () => window.clearTimeout(t);
    }, [basemap]);

    const setBasemap = useCallback(
      (mode: BasemapMode) => {
        if (props.basemapMode !== undefined) {
          /* Controlled: the host owns the basemap (and, for dark/light,
           * the theme that follows it). Report and let the prop drive. */
          props.onBasemapModeChange?.(mode);
          return;
        }
        setBasemapState(mode);
        if (mode === "dark" || mode === "light") {
          /* land within the current family — a basemap switch
           * never kicks a retro user into an institutional theme. */
          const next = themeForBasemapMode(theme, mode);
          if (props.theme === undefined) setThemeState(next);
          setTheme?.(next);
        }
      },
      [props.theme, props.basemapMode, props.onBasemapModeChange, setTheme],
    );

    /* external theme control: follow it (unless SAT is being viewed).
     * Skipped when the basemap is host-controlled — the host drives it. */
    useEffect(() => {
      if (props.basemapMode !== undefined) return;
      if (props.theme !== undefined) {
        setThemeState(props.theme);
        setBasemapState((b) => (b === "sat" ? b : pairedBasemap(props.theme as ThemeName)));
      }
    }, [props.theme, props.basemapMode]);

    /* ---- PLACE FOCUS moves ----
     * engage: capture the camera (first engage only — the machine
     * keeps the original restore point across retargets) and fly to
     * the place at its kind's zoom. closeFocus (the box's ✕ and the
     * imperative clearFocus): restore that camera and deselect. Any
     * other end to the session (host deselects, host selects another
     * place, focus disabled) drops it silently — no camera fight. */
    const engage = useCallback(
      (id: string) => {
        const map = mapRef.current;
        const n = nodeById.get(id);
        if (!map || !n || !focusEnabledRef.current) return;
        const cur = focusRef.current;
        if (cur.phase === "focused" && cur.nodeId === id) return;
        const c = map.getCenter();
        setFocus(engageFocus(cur, id, { center: [c.lng, c.lat], zoom: map.getZoom() }));
        map.flyTo({ center: [n.lon, n.lat], zoom: focusZoomForKind(n.kind), offset: cardCameraOffset(map.getContainer().clientWidth) });
      },
      [nodeById],
    );

    /* the selection callback + controlled-ness are read
     * through refs so the click/deselect callbacks — and the
     * imperative handle built on them — stay stable across renders
     * even though hosts pass fresh inline callbacks each render. */
    const selectNodeRef = useRef(selectNode);
    selectNodeRef.current = selectNode;
    const controlledSelectionRef = useRef(props.selectedNodeId !== undefined);
    controlledSelectionRef.current = props.selectedNodeId !== undefined;
    const handleNodeClick = useCallback(
      (n: GraphNode) => {
        if (!controlledSelectionRef.current) setInternalSelected(n.id);
        selectNodeRef.current?.(n.id);
        engage(n.id);
      },
      [engage],
    );
    /* keyboard: ] and [ step the selection along the causal chain while the map has focus */
    stepPlaceKeyRef.current = (e: KeyboardEvent) => {
      const dir = stepKeyDir(e);
      if (!dir) return;
      const id = stepPlace(chainPlaceOrder(cascade.ordered), selectedId, dir);
      const n = id ? nodeById.get(id) : undefined;
      if (!n) return;
      e.preventDefault();
      handleNodeClick(n);
    };
    const handleDeselect = useCallback(() => {
      if (!controlledSelectionRef.current) setInternalSelected(null);
      selectNodeRef.current?.(null);
    }, []);

    const closeFocus = useCallback(() => {
      const { state, restore } = releaseFocus(focusRef.current);
      setFocus(state);
      if (restore) {
        mapRef.current?.flyTo({ center: restore.center, zoom: restore.zoom });
      }
      handleDeselect();
    }, [handleDeselect]);

    /* focus follows the host's selection only as an END signal: a
     * deselection, or a selection of a different place, closes the
     * session silently (the host is driving; no restore). Plain
     * selection changes never ENGAGE focus — engagement is marker
     * clicks + the imperative focusNode only. */
    useEffect(() => {
      const f = focusRef.current;
      if (f.phase === "focused" && selectedId !== f.nodeId) {
        setFocus(dropFocus(f));
      }
    }, [selectedId]);

    /* AUTO DEMO (or any host suppression) ends a session silently. */
    useEffect(() => {
      if (props.focusEnabled === false) {
        setFocus((f) => dropFocus(f));
      }
    }, [props.focusEnabled]);

    /* deck layers (SIM layers stack on top of the base graph layers;
     *  with no sim supplied the array is exactly the pre-SIM one).
     *  the STATIC stack is memoised without pulsePhase —
     *  it rebuilds only when graph/chain/step/view state changes —
     *  and the pulse halo is its own memo keyed on pulsePhase, so a
     *  playing cascade's animation frames rebuild one layer, never
     *  the whole stack. */
    /* A cluster badge zooms to its members (the badge expands as the
     * camera closes in); under reduced motion the move is instant. */
    const handleClusterClick = useCallback(
      (c: ClusterHit) => {
        const map = mapRef.current;
        if (!map) return;
        const items = c.members
          .map((id) => nodeById.get(id))
          .filter((n): n is GraphNode => !!n)
          .map((n) => ({ id: n.id, lon: n.lon, lat: n.lat, weight: n.capacity }));
        const cur = map.getZoom();
        /* the first zoom at which the badge dissolves into its places */
        const target = Math.min(MAP_MAX_ZOOM, zoomToExpand(items, cur, CLUSTER_CELL_PX));
        map.easeTo({
          center: [c.lon, c.lat],
          zoom: target,
          duration: prefersReducedMotion() ? 0 : 650,
        });
      },
      [nodeById],
    );

    const [hoverCluster, setHoverCluster] = useState(false);
    const handleClusterHover = useCallback((over: boolean) => setHoverCluster(over), []);

    const layerCtx = {
      theme: effTheme,
      basemap,
      /* the fallback-paint signal is the component's existing
       * tile-error state — basemapStatus 'unreachable' is exactly when
       * the coastline underlay is the geography on screen. */
      fallbackMap: basemapStatus === "unreachable",
      nodeById,
      runStateByNode,
      cascadeStep: cascade.step,
      pulseSeq,
      /* the static stack never reads the phase — pinned at 0 */
      pulsePhase: 0,
      selectedId,
      /* hover is its own small layer set (hoverLayers below): the static
       * stack never sees it, so a hover change rebuilds nothing here */
      hoverId: null,
      onNodeClick: handleNodeClick,
      onNodeHover: (n: GraphNode | null) => setHoverId(n?.id ?? null),
      showGraphEdges,
      showActivity,
      zoom: zoomQ,
      disruptedIds: simDisruptionNodes,
      onClusterClick: handleClusterClick,
      onClusterHover: handleClusterHover,
      hiddenKinds: props.layerVis
        ? new Set([
            ...(props.layerVis.ports ? [] : ["port"]),
            ...(props.layerVis.chokes ? [] : ["chokepoint"]),
            ...(props.layerVis.air ? [] : ["air_hub"]),
          ])
        : undefined,
      hiddenModes: props.layerVis && !props.layerVis.air ? new Set(["air"]) : undefined,
      placePaint: props.placePaint ?? null,
      lanes: props.lanes ?? null,
    };
    const staticLayers = useMemo(
      () => buildStaticLayers(graph, chain?.edges ?? [], portwatch?.records ?? [], layerCtx),
      [
        graph,
        chain,
        portwatch,
        effTheme,
        basemap,
        basemapStatus,
        nodeById,
        runStateByNode,
        cascade.step,
        pulseSeq,
        selectedId,
        handleNodeClick,
        showGraphEdges,
        showActivity,
        zoomQ,
        simDisruptionNodes,
        handleClusterClick,
        handleClusterHover,
        props.layerVis,
        props.placePaint,
        props.lanes,
      ],
    );
    const pulseLayer = useMemo(
      () =>
        buildPulseLayer({
          nodeById,
          runStateByNode,
          pulseSeq,
          pulsePhase,
          theme: effTheme,
        }),
      [nodeById, runStateByNode, pulseSeq, pulsePhase, effTheme],
    );
    /* AMBIENT layers (disrupted-place pulse, comets along the chain
     * arcs): their own ~10 fps ticker and their own memo, so the
     * static stack is never rebuilt by the animation. Still frame
     * under prefers-reduced-motion. */
    const reducedMotion = prefersReducedMotion();
    const chainEdges = chain?.edges;
    const laneIdx = useMemo(() => laneIndexAt(props.lanes, zoomQ), [props.lanes, zoomQ]);
    const arcData = useMemo(
      () =>
        revealedArcData(chainEdges ?? [], { nodeById, theme: effTheme, cascadeStep: cascade.step, laneIndex: laneIdx }),
      [chainEdges, nodeById, effTheme, cascade.step, laneIdx],
    );
    const approxLegs = useMemo(() => (laneIdx ? approxSeaLegs(graph, laneIdx) : 0), [graph, laneIdx]);
    const laneChip = laneChipModel({ notServed: !!props.lanesNotServed, hasLanes: !!laneIdx, approxLegs });
    const hasPulsing = useMemo(
      () =>
        pulsingPlaces({
          nodeById,
          runStateByNode,
          cascadeStep: cascade.step,
          disruptedIds: simDisruptionNodes,
        }).length > 0,
      [nodeById, runStateByNode, cascade.step, simDisruptionNodes],
    );
    const ambientNeeded = hasPulsing || arcData.length > 0;
    const [ambientPhase, setAmbientPhase] = useState(0);
    useEffect(() => {
      if (!ambientNeeded || reducedMotion) {
        setAmbientPhase(0);
        return;
      }
      const t0 = performance.now();
      let raf = 0;
      let last = 0;
      const tick = (now: number) => {
        if (now - last >= 90 && !document.hidden) {
          last = now;
          setAmbientPhase(((now - t0) % 2800) / 2800);
        }
        raf = window.requestAnimationFrame(tick);
      };
      raf = window.requestAnimationFrame(tick);
      return () => window.cancelAnimationFrame(raf);
    }, [ambientNeeded, reducedMotion]);
    const ambientLayers = useMemo(
      () =>
        ambientNeeded
          ? buildAmbientLayers(
              graph,
              arcData,
              {
                theme: effTheme,
                nodeById,
                runStateByNode,
                cascadeStep: cascade.step,
                disruptedIds: simDisruptionNodes,
                basemap,
                zoom: zoomQ,
              },
              ambientPhase,
              reducedMotion,
            )
          : [],
      [ambientNeeded, graph, arcData, effTheme, nodeById, runStateByNode, cascade.step, simDisruptionNodes, basemap, zoomQ, ambientPhase, reducedMotion],
    );
    const hoverLayers = useMemo(
      () => buildHoverLayers(graph, layerCtx, hoverId),
      // layerCtx is rebuilt each render; its inputs are listed here
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [graph, hoverId, effTheme, basemap, zoomQ, selectedId, runStateByNode, cascade.step, simDisruptionNodes, props.layerVis],
    );
    const layers = useMemo(
      () => [...withPulseLayer(staticLayers, pulseLayer), ...ambientLayers, ...simLayersBuilt, ...hoverLayers],
      [staticLayers, pulseLayer, ambientLayers, simLayersBuilt, hoverLayers],
    );
    /* DIGITAL TWIN: the controller owns the fleet layers and the rAF
     * loop; React hands it the (rarely changing) base stack and the
     * served inputs. With twin off the stack goes straight to deck. */
    const twinRef = useRef<TwinController | null>(null);
    if (!twinRef.current) twinRef.current = new TwinController();
    const twin = twinRef.current;
    const onSimVehiclePickLatest = useRef(props.onSimVehiclePick);
    onSimVehiclePickLatest.current = props.onSimVehiclePick;
    useEffect(() => {
      if (!twinOn) return;
      const map = mapRef.current;
      const overlay = overlayRef.current;
      if (!map || !overlay) return;
      twin.attach(map as unknown as TwinMapLike, overlay);
      twin.setOnPick((id) => onSimVehiclePickLatest.current?.(id));
      return () => twin.detach();
    }, [twinOn, twin]);
    useEffect(() => {
      twin.setTheme(effTheme);
    }, [twin, effTheme]);
    const simSeaOn = !!simResolved?.effective.SEA;
    const simAirOn = !!simResolved?.effective.AIR;
    useEffect(() => {
      if (!twinOn) return;
      twin.setSim(
        sim
          ? {
              runId: sim.run.sim_run_id,
              lanes: sim.run.derivation.lanes,
              state: sim.state,
              seaOn: simSeaOn,
              airOn: simAirOn,
              hostSelectedId: sim.selectedVehicleId,
            }
          : null,
      );
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [twinOn, twin, sim, simSeaOn, simAirOn]);
    useEffect(() => {
      if (twinOn) twin.setLive(props.liveEntities ?? null);
    }, [twinOn, twin, props.liveEntities]);
    useEffect(() => {
      if (twinOn) twin.setBase(layers);
      else overlayRef.current?.setProps({ layers });
    }, [twinOn, twin, layers]);
    const twinMapGetter = useCallback(() => mapRef.current as unknown as TwinMapLike | null, []);

    /* cursor affordance */
    useEffect(() => {
      const canvas = mapRef.current?.getCanvas();
      if (canvas) canvas.style.cursor = hoverId || hoverCluster ? "pointer" : "";
    }, [hoverId, hoverCluster]);

    /* inspector anchor projection */
    const selectedNode = selectedId ? nodeById.get(selectedId) : undefined;
    useEffect(() => {
      const map = mapRef.current;
      if (!map || !selectedNode) {
        setCalloutPos(null);
        return;
      }
      const update = () => {
        const p = map.project([selectedNode.lon, selectedNode.lat]);
        const w = map.getContainer().clientWidth;
        const flip = p.x > w - 300;
        /* Callout clamp: the callout is 248px wide with
         * an 18px leader offset, so a node projecting near (or beyond) the
         * container's left/right edge rendered the panel half off-map with
         * its title cut. The panel is now shifted back inside with an 8px
         * margin (shiftX, applied by Inspector). Content and the
         * selectNode behaviour are unchanged — only the offset moves. */
        const CALLOUT_W = 264, OFF = 18, MARGIN = 8, CALLOUT_EST_H = 280;
        const left = flip ? p.x - OFF - CALLOUT_W : p.x + OFF;
        const clamped = Math.min(Math.max(left, MARGIN), Math.max(MARGIN, w - CALLOUT_W - MARGIN));
        /* the same clamp on the vertical axis (a ~240 px callout
         * centred on a point near the top of a short map lost its title). */
        const h = map.getContainer().clientHeight;
        const shiftY = popupShiftY(p.y, h, Math.min(CALLOUT_EST_H, h - 2 * MARGIN));
        setCalloutPos({ x: p.x, y: p.y, flip, shiftX: clamped - left, shiftY });
      };
      update();
      map.on("move", update);
      return () => {
        map.off("move", update);
      };
    }, [selectedNode]);

    /* focus box anchor projection — the inspector callout's clamp,
     * extended to both axes (the box is taller) and to a docked
     * full-width form on narrow containers. */
    const focusedNode = focus.phase === "focused" ? nodeById.get(focus.nodeId) : undefined;
    useEffect(() => {
      const map = mapRef.current;
      if (!map || !focusedNode) {
        setFocusPos(null);
        return;
      }
      const update = () => {
        const p = map.project([focusedNode.lon, focusedNode.lat]);
        const w = map.getContainer().clientWidth;
        const h = map.getContainer().clientHeight;
        if (w < 520) {
          setFocusPos({ x: p.x, y: p.y, flip: false, shiftX: 0, shiftY: 0, docked: true });
          return;
        }
        const BOX_W = 300, OFF = 18, MARGIN = 8, EST_H = 340;
        const flip = p.x > w - (BOX_W + OFF + MARGIN);
        const left = flip ? p.x - OFF - BOX_W : p.x + OFF;
        const clampedLeft = Math.min(Math.max(left, MARGIN), Math.max(MARGIN, w - BOX_W - MARGIN));
        const desiredTop = p.y - EST_H / 2;
        const clampedTop = Math.min(Math.max(desiredTop, MARGIN), Math.max(MARGIN, h - EST_H - MARGIN));
        setFocusPos({
          x: p.x, y: p.y, flip,
          shiftX: clampedLeft - left, shiftY: clampedTop - desiredTop, docked: false,
        });
      };
      update();
      map.on("move", update);
      return () => {
        map.off("move", update);
      };
    }, [focusedNode]);

    /* cascade transport fns are read through a ref — useCascade
     * returns fresh function identities every render, so depending on
     * them (or omitting the deps array entirely, as before) recreated
     * the imperative handle on every render. */
    const cascadeRef = useRef(cascade);
    cascadeRef.current = cascade;
    useImperativeHandle(
      ref,
      () => ({
        flyToNode: (id: string) => {
          const n = nodeById.get(id);
          /* an unknown id is a no-op — before this it flew the
           * camera to [0, 0] at a default zoom via a null-coalescing
           * fallback on the node's coordinates. */
          if (!n) return;
          const m = mapRef.current;
          m?.flyTo({
            center: [n.lon, n.lat],
            zoom: focusZoomForKind(n.kind),
            offset: cardCameraOffset(m.getContainer().clientWidth),
          });
        },
        focusNode: (id: string) => {
          if (!focusEnabledRef.current) return;
          if (!controlledSelectionRef.current) setInternalSelected(id);
          selectNodeRef.current?.(id);
          engage(id);
        },
        clearFocus: closeFocus,
        setBasemap,
        playCascade: () => cascadeRef.current.play(),
        pauseCascade: () => cascadeRef.current.pause(),
        resetCascade: () => cascadeRef.current.reset(),
        getMap: () => mapRef.current,
      }),
      [nodeById, engage, closeFocus, setBasemap],
    );

    const currentEvent =
      cascade.step >= 0 ? cascade.ordered[cascade.step] : undefined;
    const pwProv = portwatch?.provenance;
    const aisChip = aisChipModel(aisFeed);
    const paintLegend = placePaintLegend(props.placePaint);
    const kindRgb = placeKindColors(effTheme);
    const tk = tokens[effTheme];

    /* Arrow-key travel along the toolbar's Rail segmented controls (focus moves, Enter or Space presses). */
    const railKeys = (e: React.KeyboardEvent<HTMLDivElement>) => {
      const btns = Array.from(e.currentTarget.querySelectorAll("button"));
      const to = railArrow(e.key, btns.indexOf(e.target as HTMLButtonElement), btns.length);
      if (to === null) return;
      e.preventDefault();
      btns[to]?.focus();
    };

    return (
      <div
        className={`sm-map-root ${chain && showTransport ? "sm-has-bar " : ""}${props.className ?? ""}`}
        style={props.style}
        data-theme={effTheme}
        data-basemap={basemap}
        data-basemap-status={basemapStatus}
        data-underlay-visible={underlayVisible}
      >
        <div className="sm-map-canvas" ref={containerRef} />
        <div id={mapDescId} className="sm-sr" role="status" aria-live="polite" aria-atomic="true">
          {mapSummary({ graph, loading: props.graphLoading, basemapLabel: BASEMAP_LABEL[basemap], selectedName: selectedNode?.name, chainEvents: cascade.total, chainStep: cascade.step })}
        </div>

        {/* TOOLBAR (top-left): one quiet strip. Basemap segmented
            control, the KEY toggle, and the single basemap state chip.
            State labels only; no prose on the map. */}
        <div className="sm-map-hud">
          <div className="sm-toolbar" role="toolbar" aria-label="Map">
            <div className="sm-seg" role="group" aria-label="Basemap" onKeyDown={railKeys}>
              {(Object.keys(BASEMAP_LABEL) as BasemapMode[]).map((m) => (
                <button
                  key={m}
                  aria-pressed={basemap === m}
                  onClick={() => setBasemap(m)}
                >
                  {BASEMAP_LABEL[m]}
                </button>
              ))}
            </div>
            {basemap === "sat" && (
              <div className="sm-seg" role="group" aria-label="Imagery source" onKeyDown={railKeys}>
                <button aria-pressed={satSource === "static"} onClick={() => setSatSource("static")}>
                  Static
                </button>
                <button aria-pressed={satSource === "daily"} onClick={() => setSatSource("daily")}>
                  Latest daily
                </button>
              </div>
            )}
            {basemap === "sat" && (
              <span
                className={`sm-chip${imageryChipView.state === "not-served" ? " sm-chip-alert" : ""}`}
                role="status"
                data-imagery-source={satSource}
                data-imagery-state={imageryChipView.state}
              >
                {imageryChipView.text}
              </span>
            )}
            <button
              className="sm-tool"
              aria-expanded={keyOpen}
              aria-controls="sm-key"
              onClick={() => setKeyOpen((o) => !o)}
            >
              Layers
            </button>
            {basemapStatus === "unreachable" && !(basemap === "sat" && satSource === "daily" && dailyDate === null) && (
              <span className="sm-chip sm-chip-alert" role="status">
                Basemap offline
              </span>
            )}
          </div>

          {/* KEY: kind colours, capacity bands, tier, state, layers. One
              collapsible control (SIM layer toggles live in the host's
              SIM panel; SATELLITE is the basemap control). */}
          {keyOpen && (
            <div className="sm-key sm-layers" id="sm-key" role="group" aria-label="Key">
              <div className="sm-layers-head">
                <span>Layers</span>
                <button className="sm-callout-close" onClick={() => setKeyOpen(false)} aria-label="Close layers">
                  <CloseMark />
                </button>
              </div>
              <div className="sm-layers-toggles">
                <label>
                  <input
                    type="checkbox"
                    checked={showGraphEdges}
                    onChange={(e) => {
                      if (props.layerVis) props.onLayerVisChange?.({ lanes: e.target.checked });
                      else setShowGraphEdgesState(e.target.checked);
                    }}
                  />
                  Legs
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={showActivity}
                    onChange={(e) => {
                      if (props.layerVis) props.onLayerVisChange?.({ activity: e.target.checked });
                      else setShowActivityState(e.target.checked);
                    }}
                  />
                  Activity
                </label>
              </div>
              <div className="sm-key-h">Sources</div>
              <div className="sm-layers-sources">
                {portwatch && pwProv && (
                  <span className="sm-chip">
                    <b>PortWatch</b> {pwProv.retrieved_at.slice(0, 10)}
                    <span className={`sm-mode sm-mode-${pwProv.mode.toLowerCase()}`}>
                      {pwProv.mode}
                    </span>
                  </span>
                )}
                <span className="sm-chip" data-ais-state={aisFeed ? aisFeed.state : "NOT SERVED"}>
                  {aisChip.lead}{" "}
                  <span className={`sm-mode sm-mode-${aisChip.cls}`}>{aisChip.badge}</span>
                </span>
                {props.graphLoading === undefined && (
                  <span className="sm-chip">
                    <b>Network</b> {networkChipText(graph, props.graphLoading)}
                  </span>
                )}
                {showGraphEdges && laneChip && (
                  <span className="sm-chip" data-chip={laneChip.kind === "approx" ? "approx-legs" : "lanes-not-served"}>
                    <b>{laneChip.label}</b> {laneChip.value}
                  </span>
                )}
              </div>
              <div className="sm-key-h">Key</div>
              <div className="sm-key-h">Places</div>
              {KIND_LEGEND.map((k) => (
                <div className="sm-key-row" key={k.kind}>
                  <span
                    className={`sm-key-dot${k.kind === "chokepoint" ? " ring" : ""}`}
                    style={{ "--k": rgbCss(kindRgb[k.kind]) } as React.CSSProperties}
                    aria-hidden
                  />
                  {k.label}
                </div>
              ))}
              <div className="sm-key-row">
                <span className="sm-key-bands" aria-hidden>
                  <i />
                  <i />
                  <i />
                  <i />
                </span>
                Capacity
              </div>
              <div className="sm-key-row">
                <span className="sm-key-dot" style={{ "--k": rgbCss(kindRgb.port) } as React.CSSProperties} aria-hidden />
                Core
                <span className="sm-key-dot dim" style={{ "--k": rgbCss(kindRgb.port) } as React.CSSProperties} aria-hidden />
                Shell
              </div>
              <div className="sm-key-h">State</div>
              <div className="sm-key-row">
                <span className="sm-key-dot" style={{ "--k": rgbCss(rgbOfHex(tk.danger)) } as React.CSSProperties} aria-hidden />
                Disrupted
                <span className="sm-key-dot" style={{ "--k": rgbCss(rgbOfHex(tk.accent)) } as React.CSSProperties} aria-hidden />
                Affected
              </div>
              {twinOn && (sim || props.liveEntities) && (
                <>
                  <div className="sm-key-h">Fleet</div>
                  <div className="sm-key-row">
                    <span className="sm-key-dot" style={{ "--k": rgbCss(rgbOfHex(tk.vessel)) } as React.CSSProperties} aria-hidden />
                    Vessel
                    <span className="sm-key-dot" style={{ "--k": rgbCss(kindRgb.air_hub) } as React.CSSProperties} aria-hidden />
                    Aircraft
                    {fleetKey && (
                      <>
                        <span className={`sm-key-dot${fleetKey.live ? " live" : ""}`} aria-hidden />
                        {fleetKey.word}
                      </>
                    )}
                  </div>
                </>
              )}
            </div>
          )}
          {twinOn && <TwinHud ctrl={twin} hasSim={!!sim} />}
        </div>

        {twinOn && <TwinSurface ctrl={twin} map={twinMapGetter} />}

        {/* Bottom-left: the host's place-paint legend (when painting),
            one row of state chips, then scale + zoom level. Every chip
            is a data-mode or state label (LIVE / SNAPSHOT / SIMULATED). */}
        <div className="sm-map-legend">
          {/* PLACE PAINT legend: the host's entries, verbatim —
              swatch colour + label only. Absent when no paint is set. */}
          {paintLegend.length > 0 && (
            <div className="sm-placepaint-legend" role="group" aria-label="Place paint">
              {placePaintLegend(props.placePaint).map((e, i) => (
                <span key={`${e.label}:${i}`}>
                  <span
                    className="sm-placepaint-swatch"
                    aria-hidden
                    style={{ backgroundColor: e.color }}
                  />
                  {e.label}
                </span>
              ))}
            </div>
          )}
          <div className="sm-statusrow">
            {portwatch && pwProv && (
              <span className="sm-chip">
                <b>PortWatch</b> {pwProv.retrieved_at.slice(0, 10)}
                <span className={`sm-mode sm-mode-${pwProv.mode.toLowerCase()}`}>
                  {pwProv.mode}
                </span>
              </span>
            )}
            {/* AIS, network and lane-geometry chips live in the Layers popover (Sources) */}
            {/* Simulated-layer labelling: every simulated surface
                carries SIMULATED; weather carries its CC BY 4.0 attribution
                wherever it shows, or its honest UNAVAILABLE state. */}
            {sim && !twinOn && (
              <span className="sm-chip sm-chip-sim">
                <b>SIMULATED</b> {sim.run.entity_count} entities
              </span>
            )}
            {twinOn && (
              <TwinChips
                ctrl={twin}
                theme={effTheme}
                runEntityCount={sim?.run.entity_count ?? null}
                groundOn={!!simResolved?.effective.GROUND}
                hasSim={!!sim}
              />
            )}
            {sim && simResolved?.effective.WEATHER && (
              simWeather?.status === "ok" ? (
                <span className="sm-chip">
                  <b>Weather</b> {WEATHER_SAMPLE_LABEL} — {WEATHER_ATTRIBUTION}{" "}
                  (CC BY 4.0) · snapshot {simWeather.retrieved_at.slice(0, 10)}
                </span>
              ) : (
                <span className="sm-chip sm-chip-alert">
                  <b>Weather</b> not served
                </span>
              )
            )}
            {/* weather cells legend: shown while the modelled
                cells are actually rendered (WEATHER on + cells in the
                state payload), with the weather source and the step's
                validity window when the payload carries one. */}
            {sim && simResolved?.effective.WEATHER && (sim.state.weather_cells?.length ?? 0) > 0 && (
              <span className="sm-chip">
                {WEATHER_CELLS_LABEL} ·{" "}
                {simWeather?.status === "ok" ? simWeather.source : "Open-Meteo"}
                {sim.state.weather_step
                  ? ` · valid ${sim.state.weather_step.from}–${sim.state.weather_step.to}`
                  : ""}
              </span>
            )}
            {sim && simResolved?.flowsAutoEnabledLanes && (
              <span className="sm-chip">
                <b>Flows</b> lanes on automatically
              </span>
            )}
          </div>
        </div>

        {/* bottom-right: scale and zoom level, clear of the cascade bar */}
      <div className="sm-scalerow" aria-hidden>
        <div className="sm-scalebar">
          <div>{scale.label}</div>
          <div className="sm-scale-line" style={{ width: scale.px }} />
        </div>
        <span className="sm-tier">
          {TIER_LABEL[zoomTier(zoomQ)]} · Z{(Math.round(zoomQ * 10) / 10).toFixed(1)}
        </span>
      </div>

        {/* cascade transport */}
        {chain && showTransport && (
          <div className="sm-cascade-bar" role="group" aria-label="Cascade playback">
            <button onClick={cascade.reset} aria-label="Back to first step">First</button>
            <button onClick={cascade.stepBack} aria-label="Previous step">Back</button>
            <button onClick={cascade.toggle}>
              {cascade.playing ? "Pause" : "Play"}
            </button>
            <button onClick={cascade.stepForward} aria-label="Next step">Next</button>
            <span className="sm-step-readout">
              {cascade.step < 0
                ? `Step – of ${cascade.total}`
                : `Step ${cascade.step + 1} of ${cascade.total}`}
              {currentEvent
                ? ` · #${currentEvent.seq + 1} ${currentEvent.channel}`
                : ""}
            </span>
            <input
              type="range"
              min={-1}
              max={cascade.total - 1}
              value={cascade.step}
              onChange={(e) => cascade.seek(Number(e.target.value))}
              aria-label="Cascade step"
            />
          </div>
        )}

        {/* SIM minimal transport (play/pause/step/
            seek over ticks; the host store owns the tick and fetches
            state(tick); the host's full control surface can replace
            it on the same callbacks.) */}
        {sim && props.simTransport !== false && (
          <div className="sm-cascade-bar sm-sim-bar" role="group" aria-label="Simulation playback">
            <span className="sm-sim-badge">SIMULATED</span>
            <button onClick={() => props.onSimSeek?.(0)} aria-label="Back to first tick">First</button>
            <button onClick={() => props.onSimStep?.(-1)} aria-label="Previous tick">Back</button>
            <button onClick={sim.playing ? props.onSimPause : props.onSimPlay}>
              {sim.playing ? "Pause" : "Play"}
            </button>
            <button onClick={() => props.onSimStep?.(1)} aria-label="Next tick">Next</button>
            <span className="sm-step-readout">
              Tick {sim.state.tick + 1} of {sim.run.ticks} ·{" "}
              {sim.state.sim_time.replace("T", " ").slice(0, 16)} UTC
            </span>
            <input
              type="range"
              min={0}
              max={sim.run.ticks - 1}
              value={sim.state.tick}
              onChange={(e) => props.onSimSeek?.(Number(e.target.value))}
              aria-label="Simulation tick"
            />
            <span>
              {capFirst(sim.run.tick_size)}
              {sim.run.tick_size === "month" ? " = 30 d" : ""} · Zoom{" "}
              {simBandView ? BAND_LABEL[simBandView.band] : "—"}
            </span>
          </div>
        )}

        {/* Inspector callout — superseded by the focus box while
            the selected place is the focused one. */}
        {props.placeCard !== false && selectedNode && calloutPos &&
          !(focus.phase === "focused" && focus.nodeId === selectedNode.id) && (
            <Inspector
              node={selectedNode}
              runState={runStateByNode.get(selectedNode.id)}
              activity={activityByNode.get(selectedNode.id)}
              x={calloutPos.x}
              y={calloutPos.y}
              flip={calloutPos.flip}
              shiftX={calloutPos.shiftX}
              shiftY={calloutPos.shiftY}
              onClose={handleDeselect}
            />
          )}

        {/* PLACE FOCUS box */}
        {props.placeCard !== false && focusedNode && focusPos && (
          <FocusBox
            node={focusedNode}
            runState={runStateByNode.get(focusedNode.id)}
            activity={activityByNode.get(focusedNode.id)}
            simNode={
              sim
                ? (sim.state.nodes.find((n) => n.id === focusedNode.id) ?? null)
                : null
            }
            goods={props.focusGoods ?? null}
            x={focusPos.x}
            y={focusPos.y}
            flip={focusPos.flip}
            shiftX={focusPos.shiftX}
            shiftY={focusPos.shiftY}
            docked={focusPos.docked}
            onClose={closeFocus}
            onOpenInspector={props.onOpenInspector}
          />
        )}
      </div>
    );
  },
);
