// @supermap/map — Map UI public surface. The web app (web/app) imports from here
// via a relative path:  import { SupermapMap } from "../map/src";
export { SupermAPMap } from "./components/SupermapMap";
export { SupermAPMap as SupermapMap } from "./components/SupermapMap";
export type {
  SupermAPMapProps,
  SupermAPMapHandle,
} from "./components/SupermapMap";
export { Inspector } from "./components/Inspector";
export type { InspectorProps } from "./components/Inspector";
export { FocusBox } from "./components/FocusBox";
export type { FocusBoxProps } from "./components/FocusBox";
export {
  IDLE_FOCUS,
  KIND_LABEL,
  KIND_LEGEND,
  FOCUS_COPY,
  dropFocus,
  engageFocus,
  focusZoomForKind,
  releaseFocus,
} from "./components/focus";
export type {
  CameraView,
  FocusGoodsLine,
  FocusState,
} from "./components/focus";
export { useCascade } from "./components/useCascade";
export { prefersReducedMotion } from "./components/motion";
export type { CascadeState, CascadeOptions } from "./components/useCascade";
export {
  buildLayers,
  buildStaticLayers,
  baseEdgeSegments,
  baseEdgeSegmentsOnLanes,
  buildPulseLayer,
  withPulseLayer,
  buildAmbientLayers,
  revealedArcData,
  placePaintEntryFor,
  placePaintLegend,
  placePaintRgba,
} from "./components/layers";
export type {
  BaseEdgeSegment,
  LayerContext,
  PulseContext,
  MarkerState,
  ClusterHit,
  PlacePaint,
  PlacePaintEntry,
  PlacePaintLegendEntry,
} from "./components/layers";
export type { SimMapData } from "./components/SupermapMap";

/* LANE GEOMETRY: lane polylines of the sea legs and the chain-arc router over them. */
export {
  LANE_FULL_ZOOM,
  approxSeaLegs,
  buildLaneIndex,
  joinParts,
  laneIndexAt,
  laneRoute,
  laneSegments,
  pairKey,
  pairParts,
} from "./lanes/laneIndex";
export type { LaneIndex, LanePairRow, LaneRoute, LaneSegment, LaneSet, LanesGeometryDoc } from "./lanes/laneIndex";
export { arcPathsFor } from "./places/placeLayers";

/* The pure rules behind the glowing place layer (LOD tiers,
 * capacity bands, size interpolation, clustering, label placement). */
export {
  TIER_FROM,
  TIER_ORDER,
  TIER_LABEL,
  MAP_MIN_ZOOM,
  MAP_MAX_ZOOM,
  zoomTier,
  tierAtLeast,
  quantizeZoom,
  capacityBands,
  appearZoom,
  appearAlpha,
  markerRadiusPx,
} from "./places/tiers";
export type { ZoomTier, CapacityBand } from "./places/tiers";
export { clusterPlaces } from "./places/cluster";
export type { ClusterItem, PlaceCluster } from "./places/cluster";
export { selectLabels } from "./places/labels";
export type { LabelCandidate, PlacedLabel } from "./places/labels";
export { placeKindColors } from "./theme/tokens";

/* SIM — simulation rendering surface: layer builders,
 * band/gating/weather pure logic, playback driver. The sim API client
 * lives in the app (web/app/src/api.ts). */
export {
  buildSimLayers,
  buildSimLaneLayers,
  buildSimFlowLayers,
  buildSimStateLayers,
  hullAngleDeg,
  selectVesselLabels,
  vesselLabelText,
  weatherCellGradeRgb,
  VESSEL_LABEL_CAP,
} from "./sim/simLayers";
export type {
  SimLayerInput,
  SimLaneLayerInput,
  SimFlowLayerInput,
  SimStateLayerInput,
} from "./sim/simLayers";
export { buildSimIndex, activeDisruptionNodeIds } from "./sim/helpers";
export type { SimIndex } from "./sim/helpers";
export {
  laneSubpaths,
  splitAntimeridianLonLat,
  pointAtFraction,
  hasWrapStreak,
} from "./sim/geometry";
export type { LonLat } from "./sim/geometry";
export {
  zoomBand,
  maxZoomForBasemap,
  bandView,
  laneFlowDensity,
  queueRings,
  REGION_LANE_ENTITY_LIMIT,
  BAND_ORDER,
  bandAtLeast,
} from "./sim/bands";
export type { BandView, LaneChip, LaneAggregate, QueueRing, ZoomBand } from "./sim/bands";
export {
  SIM_LAYER_KEYS,
  LIVE_LAYER_DEFAULTS,
  simLayerDefaults,
  resolveSimLayers,
  withLayerToggled,
  modeLayerOn,
  laneGeometryOn,
  sanitiseSimLayers,
} from "./sim/layersGate";
export type { SimLayerKey, SimLayers, ResolvedLayers } from "./sim/layersGate";
export {
  weatherLayerData,
  hourIndex,
  windGradeRgb,
  waveGradeRgb,
  WEATHER_ATTRIBUTION,
  WEATHER_SAMPLE_LABEL,
  WEATHER_CELLS_LABEL,
} from "./sim/weather";
export type { WeatherLayerData, WeatherPoint } from "./sim/weather";
export {
  useSimPlayback,
  clampTick,
  nextTick,
  prevTick,
  isAtEnd,
  FLOW_STEP_MS,
} from "./sim/playback";
export type { SimPlayback } from "./sim/playback";
export {
  TICK_SPECS,
  GRANULARITIES,
  GRANULARITY_SECONDS,
  tickSecondsOf,
  floorTickOf,
  isInterpolated,
  clampPlayhead,
  stepPlayhead,
  advancePlayhead,
  playheadMs,
  fmtSimDayTime,
  simClockReadout,
  interpolatePositions,
  MARKER_KIND_LABEL,
  markerFraction,
  LINKED,
  freezeLink,
  relink,
  onRunSwitch,
  effectivePanelView,
  placeLabel,
  parseLaneKey,
  entityLabel,
  berthsFor,
  laneDailyThroughput,
  deriveStatusBoard,
  queueChartRows,
  tierSplit,
  isCorePlace,
  tierOfValue,
  laneTierOf,
  entityTierOf,
  nodeTierOf,
  TIER_WEIGHT,
  laneVisibleAtBand,
  clsLabelOf,
  fmtLate,
  meanQueueWaitDays,
  computeVolumeAtRisk,
} from "./sim/phaseD";
export type {
  SimTickSpec,
  SimGranularity,
  SimClockReadout,
  PanelLinkState,
  MasterView,
  PanelView,
  SimPlaceInfo,
  GoodsLine,
  StatusBoard,
  TierSplit,
  VarLine,
  VolumeAtRisk,
  SimTier,
} from "./sim/phaseD";
export type {
  SimTickSize,
  SimEntityState,
  SimRunRequest,
  SimRunSummary,
  SimRunDetail,
  SimLane,
  SimJournal,
  SimJournalDisruption,
  SimEntity,
  SimNodeState,
  SimNodeWeather,
  SimState,
  SimWeatherCell,
  SimWeatherSnapshot,
  SimWeatherResponse,
  SimMarker,
  SimMarkerKind,
  SimMarkersResponse,
} from "./sim/types";

export { tokens, channelColors, modeColors, themeFamily, isDarkTheme, pairedBasemap, themeForBasemapMode } from "./theme/tokens";
export type { ThemeTokens } from "./theme/tokens";
export {
  CARTO_DARK_STYLE,
  CARTO_LIGHT_STYLE,
  SAT_ATTRIBUTION,
  attributionFor,
  attributionHtml,
  attributionIds,
  satelliteStyle,
} from "./theme/basemaps";
export {
  EOX_ATTRIBUTION,
  EOX_TILES,
  GIBS_ATTRIBUTION,
  defaultDailyDate,
  gibsTileUrl,
  imageryChip,
  resolveDailyDate,
} from "./theme/imagery";
export type { SatSource, ImageryChip } from "./theme/imagery";
export {
  UNDERLAY_SOURCE_ID,
  UNDERLAY_BG_LAYER_ID,
  UNDERLAY_FILL_LAYER_ID,
  UNDERLAY_OUTLINE_LAYER_ID,
  UNDERLAY_LAYER_IDS,
  buildFallbackStyle,
  ensureUnderlay,
  setUnderlayVisibility,
  syncFallbackStyle,
  underlayPaint,
} from "./theme/underlay";

export {
  API_BASE,
  GRAPH_URL,
  fetchGraph,
  fetchChain,
  fetchPortWatch,
} from "./data/loaders";

export type {
  Mode,
  NodeKind,
  Channel,
  DataMode,
  ThemeName,
  BasemapMode,
  BasemapStatus,
  Provenance,
  GraphNode,
  GraphEdge,
  GraphData,
  CausalEvent,
  ChainEdge,
  ChainResponse,
  PortActivityRecord,
  PortWatchSnapshot,
  NodeRunState,
  SupermAPMapCallbacks,
} from "./types";

/* DIGITAL TWIN (fleet renderer): pure data layer + LIVE entities API.
 * The renderer is built into <SupermapMap> (prop `twin`, default on);
 * these exports are the testable logic and the LIVE contract. */
export { FleetEngine, buildFleetContext, FILTER_ALL, DELAYED_MIN } from "./twin/fleet";
export type { FleetFilter, FleetContext, FleetCounts } from "./twin/fleet";
export { buildPathTable, arcOfPoint, pointAtArc } from "./twin/pathTable";
export type { PathTable } from "./twin/pathTable";
export { lodForZoom, LOD_GLYPH_FROM, LOD_LABEL_FROM, LOD_HEAT_BELOW } from "./twin/lod";
export type { TwinLod, TwinLodLevel } from "./twin/lod";
export { TrailBuffer, fillTrails, trailLengthKm } from "./twin/trails";
export { subSolarPoint, nightPolygon } from "./twin/terminator";
export { fetchLiveEntities, useLiveEntities } from "./twin/liveFetch";
export { normaliseLiveResponse, liveChip, validLiveColumns } from "./twin/live";
export type { LiveFeed, LiveEntitiesColumns, LiveFeedStatus, LiveChip } from "./twin/live";
