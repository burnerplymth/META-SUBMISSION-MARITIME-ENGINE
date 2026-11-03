// Deck.gl layers for the simulation.
// Everything here renders SERVED data only: lane polylines are the
// server's rendered paths (geometry.ts conversion + antimeridian
// split), entity positions come verbatim from the /sim/state payload,
// flow density is the state's per-lane EN_ROUTE count, weather values
// are the pinned snapshot's. Nothing is recomputed or invented.
//
// FLOWS animation language: pulses advance on the caller's `flowStep`
// counter, which the map advances on the same 300 ms step clock as the
// cascade transport (SupermapMap) — never a free-running rAF loop
// detached from state.
//
// JUMP-SAFETY: buildSimLayers is a pure function of its
// input — the same (run, state, layers, band, flowStep) always builds
// the same layers, with no memory of previously rendered ticks. Timeline
// scrubbing depends on this: arbitrary tick order, including
// backwards across disruption onsets, renders correctly.

import {
  IconLayer,
  PathLayer,
  PolygonLayer,
  ScatterplotLayer,
  TextLayer,
} from "@deck.gl/layers";
import type { Layer } from "@deck.gl/core";
import type { ThemeName } from "../types";
import { isDarkTheme, modeColors, placeKindColors, tokens } from "../theme/tokens";
import { hexToRgba } from "../components/layers";
import {
  laneGeometryOn,
  modeLayerOn,
  type SimLayers,
} from "./layersGate";
import {
  bandAtLeast,
  laneFlowDensity,
  queueRings,
  type BandView,
} from "./bands";
import { pointAtFraction, type LonLat } from "./geometry";
import {
  entityTierOf,
  laneTierOf,
  laneVisibleAtBand,
  nodeTierOf,
  TIER_WEIGHT,
} from "./phaseD";
import type { SimEntity, SimLane, SimState, SimWeatherCell } from "./types";
import {
  waveGradeRgb,
  weatherLayerData,
  windGradeRgb,
  type WeatherLayerData,
} from "./weather";

type RGBA = [number, number, number, number];
const rgb = (c: [number, number, number], a: number): RGBA => [c[0], c[1], c[2], a];

/* Rotation-neutral aircraft glyph (4-point star): the state payload
 * carries no leg heading, so aircraft are NEVER rotated to a fabricated
 * bearing — shape + colour carry the mode
 * Glyph colours come from the theme TOKEN set (fill = the
 * theme's text colour; stroke = the theme's ocean/panel ground) — no
 * hex literals of its own, so both themes stay single-sourced. */
function starIcon(fill: string, stroke: string): { url: string; width: number; height: number; anchorY: number } {
  const svg =
    `<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 64 64'>` +
    `<polygon points='32,2 40,24 62,32 40,40 32,62 24,40 2,32 24,24' fill='${fill}' fill-opacity='0.95' stroke='${stroke}' stroke-width='2'/>` +
    `</svg>`;
  return {
    url: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`,
    width: 64,
    height: 64,
    anchorY: 32,
  };
}
/* The star glyph depends only on the theme, so it is built +
 * encoded ONCE per theme and cached — mirroring markerIconCache in
 * components/layers.ts. Before this, starFor rebuilt + URI-encoded
 * the SVG on every getIcon call for every aircraft on every rebuild. */
const starIconCache = new Map<string, ReturnType<typeof starIcon>>();
const starFor = (theme: ThemeName) => {
  const hit = starIconCache.get(theme);
  if (hit) return hit;
  const t = tokens[theme];
  const icon = starIcon(t.text, isDarkTheme(theme) ? t.ocean : t.panel);
  starIconCache.set(theme, icon);
  return icon;
};

/* oriented hull glyph for HARBOUR/SHIP bands: an elongated hull
 * whose point is at the TOP of the SVG (pointing UP = north at angle
 * 0). Fill/stroke come from the theme tokens (vessel family), cached
 * per theme like the star glyph. */
function hullIcon(fill: string, stroke: string): { url: string; width: number; height: number; anchorY: number } {
  const svg =
    `<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 64 64'>` +
    `<polygon points='32,2 50,58 32,48 14,58' fill='${fill}' fill-opacity='0.95' stroke='${stroke}' stroke-width='2'/>` +
    `</svg>`;
  return {
    url: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`,
    width: 64,
    height: 64,
    anchorY: 32,
  };
}
const hullIconCache = new Map<string, ReturnType<typeof hullIcon>>();
const hullFor = (theme: ThemeName) => {
  const hit = hullIconCache.get(theme);
  if (hit) return hit;
  const t = tokens[theme];
  const icon = hullIcon(t.vessel, isDarkTheme(theme) ? t.ocean : t.panel);
  hullIconCache.set(theme, icon);
  return icon;
};

/**
 * deck.gl IconLayer angle for a compass bearing (degrees, clockwise
 * from north) applied to a glyph drawn pointing UP (north).
 *
 * Convention verified against the INSTALLED deck.gl (layers package
 * 9.4.0, icon-layer-vertex.glsl): instanceAngles go through
 * `rotate_by_angle` — mat2(cos, -sin, sin, cos) in GLSL column-major
 * order, i.e. (x, y) -> (cos*x + sin*y, -sin*x + cos*y) in the icon's
 * pixel space (x right, y DOWN, since positions (0,-1) maps to the
 * image top) — followed by `pixelOffset.y *= -1`. Tracing the north
 * tip vector (0, -1) through both steps gives the on-screen vector
 * (-sin A, -cos A) in (right, down) screen space, while a bearing B
 * points along (sin B, -cos B): equality needs A = -B. Positive
 * deck.gl angles therefore rotate the glyph COUNTER-clockwise on
 * screen, and the bearing must be negated. Result normalised to
 * (-180, 180].
 */
export function hullAngleDeg(bearingDeg: number): number {
  let a = -bearingDeg % 360;
  if (a <= -180) a += 360;
  if (a > 180) a -= 360;
  return a === 0 ? 0 : a;
}

/** SHIP-band label text for one entity: served name first (vessel
 *  name for sea, tail for air), falling back to the entity id. */
export function vesselLabelText(e: SimEntity): string {
  return e.vessel_name ?? e.tail ?? e.id;
}

/** Deterministic SHIP-band label selection: core-tier entities first
 *  (entityTierOf), then by id, capped at `cap` (default 200). Pure in
 *  its inputs — the same state always selects the same labels. */
export const VESSEL_LABEL_CAP = 200;
export function selectVesselLabels(
  entities: SimEntity[],
  lanesByKey: Map<string, SimLane>,
  placeTiers: ReadonlyMap<string, string | undefined>,
  cap: number = VESSEL_LABEL_CAP,
): SimEntity[] {
  const tierRank = (e: SimEntity): number =>
    entityTierOf(e, lanesByKey, placeTiers) === "core" ? 0 : 1;
  return [...entities]
    .sort((a, b) => tierRank(a) - tierRank(b) || a.id.localeCompare(b.id))
    .slice(0, cap);
}

/** weather-cell grade: the stronger of the cell's wind / wave
 *  grades (reusing the weather.ts grade families), or the storm grade
 *  for a severe cell. Grade strength is ranked by the same Beaufort /
 *  wave breaks the grade functions encode; equal ranks share the same
 *  palette colour, so the choice between them is immaterial. A cell
 *  with neither value renders at the calm grade. */
export function weatherCellGradeRgb(cell: SimWeatherCell): [number, number, number] {
  if (cell.severe) return windGradeRgb(Number.POSITIVE_INFINITY);
  const windRank = (kn: number): number => (kn < 11 ? 0 : kn < 22 ? 1 : kn < 34 ? 2 : 3);
  const waveRank = (m: number): number => (m < 1 ? 0 : m < 2.5 ? 1 : m < 4 ? 2 : 3);
  let best: { rank: number; rgb: [number, number, number] } | null = null;
  if (cell.wind_kn !== null) {
    best = { rank: windRank(cell.wind_kn), rgb: windGradeRgb(cell.wind_kn) };
  }
  if (cell.wave_m !== null) {
    const cand = { rank: waveRank(cell.wave_m), rgb: waveGradeRgb(cell.wave_m) };
    if (!best || cand.rank > best.rank) best = cand;
  }
  return best ? best.rgb : windGradeRgb(0);
}

/** Deterministic per-lane phase (stable across renders/ticks — a hash
 *  of the lane key, never Math.random: flow pulses must not jitter
 *  between identical states). */
function lanePhase(key: string): number {
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
  return (h % 1000) / 1000;
}

export interface SimLayerInput {
  theme: ThemeName;
  lanes: SimLane[];
  lanesByKey: Map<string, SimLane>;
  /** lane key -> antimeridian-split subpaths ([lon, lat]) */
  subpathsByLane: Map<string, LonLat[][]>;
  /** lane key -> full unsplit rendered path ([lon, lat]) */
  lonLatPaths: Map<string, LonLat[]>;
  state: SimState;
  /** effective (dependency-resolved) layer toggles */
  layers: SimLayers;
  bandView: BandView;
  /** 300 ms step counter driving FLOWS (cascade clock language) */
  flowStep: number;
  /** node id -> [lon, lat] (graph node positions) */
  nodePos: Map<string, LonLat>;
  /** place id -> served tier from the graph payload (the base map's
   *  tier source; SimIndex.nodeTier). Tier weighting resolves
   *  every lane/entity/place tier against this map (phaseD helpers). */
  placeTiers: ReadonlyMap<string, string | undefined>;
  /** projected weather layer data for the tick in view (or null) */
  weather: WeatherLayerData | null;
  /** node ids with an ACTIVE disruption at this tick (resolved from
   *  state.disruptions_active via the journal) */
  disruptionNodeIds: Set<string>;
  /** Fired with the entity id when a vessel / aircraft is
   *  picked (PORT band and deeper only — picking is off below PORT). */
  onVehiclePick?: (entityId: string) => void;
  /** Entity id to ring as the selected vehicle, when it is
   *  individually rendered at the current band. */
  selectedVehicleId?: string | null;
  /** DIGITAL TWIN: the fleet (entities, selection ring, labels, lane
   *  chips) is drawn by the twin renderer; this builder then emits
   *  only queues / weather / disruptions. Default false = the legacy
   *  per-tick entity layers, unchanged. */
  twinEntities?: boolean;
}

interface LaneDatum {
  lane: SimLane;
  subpath: LonLat[];
  /** resolved tier (phaseD.laneTierOf) — routing-grade routes render
   *  subordinate and are hidden at WORLD zoom (TIER_WEIGHT). */
  shell: boolean;
}

interface FlowPulse {
  lane: string;
  position: LonLat;
  mode: string;
  shell: boolean;
}

/* buildSimLayers is split into builders by what they read, because
 * SupermapMap memoises on deps that include flowStep (advanced by
 * a 300 ms interval) and a single builder would rebuild all ~4,550
 * lane routes every 300 ms:
 *   - buildSimLaneLayers: lane GEOMETRY + theme/band/toggles only.
 *     Never reads flowStep or state — safe to memoise across the
 *     flow clock.
 *   - buildSimFlowLayers: the FLOWS pulses — the ONLY builder that
 *     reads flowStep.
 *   - buildSimStateLayers: entities / chips / queues / weather /
 *     disruptions — reads state, never flowStep.
 * buildSimLayers composes the three in the original draw order and
 * stays a pure function of the full input (jump-safety above). */
export interface SimLaneLayerInput {
  theme: ThemeName;
  lanes: SimLane[];
  subpathsByLane: Map<string, LonLat[][]>;
  layers: SimLayers;
  bandView: BandView;
  placeTiers: ReadonlyMap<string, string | undefined>;
}

export interface SimFlowLayerInput {
  theme: ThemeName;
  lanes: SimLane[];
  lonLatPaths: Map<string, LonLat[]>;
  state: SimState;
  layers: SimLayers;
  bandView: BandView;
  flowStep: number;
  placeTiers: ReadonlyMap<string, string | undefined>;
}

export type SimStateLayerInput = Omit<SimLayerInput, "flowStep">;

/** Lane alpha by zoom band (dark grounds; light grounds scale up). */
const LANE_ALPHA_BY_BAND: Record<string, number> = {
  WORLD: 40,
  REGION: 50,
  PORT: 58,
  HARBOUR: 62,
  SHIP: 66,
};

export function buildSimLaneLayers(input: SimLaneLayerInput): Layer[] {
  const layers: Layer[] = [];
  const L = input.layers;
  const band = input.bandView.band;
  const dark = isDarkTheme(input.theme);
  const kinds = placeKindColors(input.theme);
  const laneRgb = (mode: string): [number, number, number] =>
    mode === "air" ? kinds.air_hub : mode === "ground" ? kinds.ground_node : kinds.port;

  /* ---- Lanes: the server's rendered paths, antimeridian-split.
   * Tier weighting: routing-grade (shell-touching) routes
   * render thinner + fainter than calibration-grade routes
   * (TIER_WEIGHT) and are hidden at WORLD zoom (laneVisibleAtBand), so the 992-place / ~4,550-route network
   * reads core-first instead of as a flat hairball. ---- */
  const laneData: LaneDatum[] = [];
  for (const lane of input.lanes) {
    if (!laneGeometryOn(L, lane.mode)) continue;
    const tier = laneTierOf(lane, input.placeTiers);
    if (!laneVisibleAtBand(tier, band)) continue;
    for (const subpath of input.subpathsByLane.get(lane.key) ?? []) {
      if (subpath.length >= 2) laneData.push({ lane, subpath, shell: tier === "shell" });
    }
  }
  if (laneData.length) {
    layers.push(
      new PathLayer<LaneDatum>({
        id: "sim-lanes",
        data: laneData,
        getPath: (d) => d.subpath,
        /* Thin luminous lines in the place-kind colours, a
         * little firmer with depth, never louder than the fleet */
        getColor: (d) =>
          rgb(
            laneRgb(d.lane.mode),
            (dark ? LANE_ALPHA_BY_BAND[band] : LANE_ALPHA_BY_BAND[band] * 1.5) *
              (d.shell ? TIER_WEIGHT.shellLaneAlpha : 1),
          ),
        getWidth: (d) =>
          (0.45 + d.lane.capacity_share * (band === "WORLD" ? 0.9 : 1.4)) *
          (d.shell ? TIER_WEIGHT.shellLaneWidth : 1),
        widthUnits: "pixels",
        widthMinPixels: 0.4,
        widthMaxPixels: 2.4,
        capRounded: false,
        jointRounded: false,
        pickable: false,
        updateTriggers: { getColor: [input.theme, band], getWidth: band },
      }),
    );
  }

  return layers;
}

export function buildSimFlowLayers(input: SimFlowLayerInput): Layer[] {
  const layers: Layer[] = [];
  const t = tokens[input.theme];
  const L = input.layers;
  const band = input.bandView.band;

  /* ---- FLOWS: animated direction/density pulses along lanes.
   * Density: sea/air = EN_ROUTE entities on the lane in THIS state
   * (the run's flow data); ground = corridor flow scaled by the lane's
   * served capacity_share (ground carries no discrete entities).
   * Direction follows the rendered path from->to.
   * Pulses carry their MODE's colour (sea = the vessel/sea
   * family, air/ground = their mode greys) — the amber accent is
   * reserved for state that means something (QUEUED vehicles, queue
   * peaks), never decoration for all moving traffic.
   * Shell-route pulses subordinate with their route and follow its
   * WORLD-zoom visibility. ---- */
  if (L.FLOWS && L.LANES) {
    const density = laneFlowDensity(input.state);
    const pulses: FlowPulse[] = [];
    for (const lane of input.lanes) {
      if (!laneGeometryOn(L, lane.mode)) continue;
      const tier = laneTierOf(lane, input.placeTiers);
      if (!laneVisibleAtBand(tier, band)) continue;
      const path = input.lonLatPaths.get(lane.key);
      if (!path || path.length < 2) continue;
      let count: number;
      if (lane.mode === "ground") {
        count = 1 + Math.round(lane.capacity_share * 4);
      } else {
        const d = density.get(lane.key) ?? 0;
        if (d === 0) continue;
        count = Math.min(8, 1 + Math.floor(d / 4));
      }
      const phase = lanePhase(lane.key);
      for (let i = 0; i < count; i++) {
        const frac = (phase + i / count + input.flowStep * 0.045) % 1;
        pulses.push({
          lane: lane.key,
          position: pointAtFraction(path, frac),
          mode: lane.mode,
          shell: tier === "shell",
        });
      }
    }
    if (pulses.length) {
      layers.push(
        new ScatterplotLayer<FlowPulse>({
          id: "sim-flows",
          data: pulses,
          getPosition: (d) => d.position,
          getRadius: (d) => (d.shell ? 3.2 * TIER_WEIGHT.shellEntitySize : 3.2),
          radiusUnits: "pixels",
          getFillColor: (d) =>
            d.mode === "sea"
              ? hexToRgba(t.sea, d.shell ? 130 : 215)
              : rgb(
                  modeColors[d.mode] ?? [140, 140, 140],
                  d.shell ? 110 : 190,
                ),
          pickable: false,
          updateTriggers: { getPosition: input.flowStep, getFillColor: input.theme },
        }),
      );
    }
  }

  return layers;
}

export function buildSimStateLayers(input: SimStateLayerInput): Layer[] {
  const layers: Layer[] = [];
  const t = tokens[input.theme];
  const L = input.layers;
  const band = input.bandView.band;

  /* ---- Entities: individuals per the band view. Sea = vessel disc
   * (cyan family), air = star glyph. State tints: EN_ROUTE full,
   * QUEUED amber, BERTHED dim, IN_PORT faint. Routing-grade
   * vehicles (on shell-touching routes) render smaller + fainter
   * (TIER_WEIGHT) — calibration-grade traffic carries the map. ---- */
  const individuals = input.twinEntities
    ? []
    : input.bandView.individual.filter((e) => modeLayerOn(L, e.mode));
  const sea = individuals.filter((e) => e.mode === "sea");
  const air = individuals.filter((e) => e.mode === "air");
  const shellOf = (e: (typeof individuals)[number]): boolean =>
    entityTierOf(e, input.lanesByKey, input.placeTiers) === "shell";
  const stateAlpha = (s: string, shell: boolean): number => {
    const base =
      s === "EN_ROUTE" ? 235 : s === "QUEUED" ? 235 : s === "BERTHED" ? 170 : 120;
    return Math.round(base * (shell ? TIER_WEIGHT.shellEntityAlpha : 1));
  };
  const stateColor = (mode: string, s: string, shell: boolean): RGBA => {
    if (s === "QUEUED") return hexToRgba(t.accent, stateAlpha(s, shell));
    if (mode === "sea") return hexToRgba(t.vessel, stateAlpha(s, shell));
    return rgb(modeColors.air ?? [154, 160, 166], stateAlpha(s, shell));
  };
  /* Picking: vessels + aircraft are pickable at PORT band and
   *  deeper only; below PORT they stay pickable:false exactly as
   *  before. The click handler guards a null picked object. */
  const pickableVehicles = bandAtLeast(band, "PORT");
  const vehicleClick = (info: { object?: unknown }) => {
    const obj = info?.object as SimEntity | null | undefined;
    if (obj) input.onVehiclePick?.(obj.id);
  };
  if (sea.length) {
    if (bandAtLeast(band, "HARBOUR")) {
      /* HARBOUR/SHIP: oriented hull glyph (pointing UP = north at
       *  angle 0), rotated by hullAngleDeg from the served bearing;
       *  entities without a bearing render unrotated. */
      layers.push(
        new IconLayer({
          id: "sim-vessels",
          data: sea,
          getPosition: (e) => [e.lon as number, e.lat as number],
          getIcon: () => hullFor(input.theme),
          getSize: (e) =>
            17 * (shellOf(e) ? TIER_WEIGHT.shellEntitySize : 1),
          sizeUnits: "pixels",
          getAngle: (e) =>
            typeof e.bearing_deg === "number" ? hullAngleDeg(e.bearing_deg) : 0,
          pickable: pickableVehicles,
          onClick: vehicleClick,
          updateTriggers: { getIcon: input.theme },
        }),
      );
    } else {
      layers.push(
        new ScatterplotLayer({
          id: "sim-vessels",
          data: sea,
          getPosition: (e) => [e.lon as number, e.lat as number],
          getRadius: (e) =>
            (band === "PORT" ? 4 : 3) * (shellOf(e) ? TIER_WEIGHT.shellEntitySize : 1),
          radiusUnits: "pixels",
          getFillColor: (e) => stateColor("sea", e.state, shellOf(e)),
          getLineColor: hexToRgba(isDarkTheme(input.theme) ? t.ocean : t.panel, 200),
          lineWidthUnits: "pixels",
          getLineWidth: 0.7,
          stroked: true,
          filled: true,
          pickable: pickableVehicles,
          onClick: vehicleClick,
          updateTriggers: { getFillColor: input.theme },
        }),
      );
    }
  }
  if (air.length) {
    layers.push(
      new IconLayer({
        id: "sim-aircraft",
        data: air,
        getPosition: (e) => [e.lon as number, e.lat as number],
        getIcon: () => starFor(input.theme),
        getSize: (e) =>
          (bandAtLeast(band, "PORT") ? 13 : 10) * (shellOf(e) ? TIER_WEIGHT.shellEntitySize : 1),
        sizeUnits: "pixels",
        pickable: pickableVehicles,
        onClick: vehicleClick,
        updateTriggers: { getIcon: input.theme },
      }),
    );
  }

  /* ---- selected-vehicle highlight: an accent ring around the
   *  selected entity, at any band where that entity is individually
   *  rendered (it is drawn from the band's individual set, so a
   *  non-rendered selection rings nothing). ---- */
  if (input.selectedVehicleId) {
    const selected = individuals.find((e) => e.id === input.selectedVehicleId);
    if (selected) {
      layers.push(
        new ScatterplotLayer({
          id: "sim-vehicle-selected",
          data: [selected],
          getPosition: (e) => [e.lon as number, e.lat as number],
          getRadius: 13,
          radiusUnits: "pixels",
          getFillColor: hexToRgba(t.accent, 40),
          getLineColor: hexToRgba(t.accent, 255),
          lineWidthUnits: "pixels",
          getLineWidth: 2,
          stroked: true,
          filled: true,
          pickable: false,
          updateTriggers: { getLineColor: input.theme, getFillColor: input.theme },
        }),
      );
    }
  }

  /* ---- SHIP band: per-entity labels (served name / tail, else
   *  id), deterministically capped — core tier first, then id. Colour
   *  is the theme's text token, so every theme (incl. retro) drives
   *  it; no colour literals here. ---- */
  if (band === "SHIP" && individuals.length) {
    const labelled = selectVesselLabels(individuals, input.lanesByKey, input.placeTiers);
    if (labelled.length) {
      layers.push(
        new TextLayer({
          id: "sim-vessel-labels",
          data: labelled,
          getPosition: (e) => [e.lon as number, e.lat as number],
          getText: (e) => vesselLabelText(e),
          getSize: 9,
          sizeUnits: "pixels",
          getColor: hexToRgba(t.text, 235),
          getPixelOffset: [0, -13],
          fontFamily: "'IBM Plex Mono', ui-monospace, monospace",
          pickable: false,
          updateTriggers: { getColor: input.theme },
        }),
      );
    }
  }

  /* ---- WORLD chips + REGION aggregate markers (count at midpoint).
   * A chip on a shell-touching route subordinates with its
   * route (smaller disc, fainter fill + count) — the chip is an
   * aggregation of state, so it is NOT hidden at WORLD with the route
   * geometry; it just stops shouting over core traffic. ---- */
  const chipShell = (laneKey: string): boolean => {
    const lane = input.lanesByKey.get(laneKey);
    return lane ? laneTierOf(lane, input.placeTiers) === "shell" : false;
  };
  const chipData = input.twinEntities ? [] : [
    ...input.bandView.chips
      .filter((c) => modeLayerOn(L, c.mode))
      .map((c) => ({ position: c.position, count: c.count, lane: c.lane, shell: chipShell(c.lane) })),
    ...input.bandView.aggregated
      .filter((a) => modeLayerOn(L, a.mode))
      .map((a) => ({ position: a.position, count: a.count, lane: a.lane, shell: chipShell(a.lane) })),
  ];
  if (chipData.length) {
    layers.push(
      new ScatterplotLayer({
        id: "sim-lane-chips-disc",
        data: chipData,
        getPosition: (d) => d.position,
        getRadius: (d) => (d.shell ? 9 * TIER_WEIGHT.shellChipSize : 9),
        radiusUnits: "pixels",
        getFillColor: (d) =>
          hexToRgba(t.accent, d.shell ? Math.round(235 * TIER_WEIGHT.shellChipAlpha) : 235),
        pickable: false,
      }),
      new TextLayer({
        id: "sim-lane-chips",
        data: chipData,
        getPosition: (d) => d.position,
        getText: (d) => String(d.count),
        getSize: (d) => (d.shell ? 8.5 * TIER_WEIGHT.shellChipSize : 8.5),
        sizeUnits: "pixels",
        getColor: (d) =>
          hexToRgba(t.accentText, d.shell ? Math.round(255 * TIER_WEIGHT.shellChipAlpha) : 255),
        fontFamily: "'IBM Plex Mono', ui-monospace, monospace",
        fontWeight: 700,
        characterSet: "0123456789",
        pickable: false,
        updateTriggers: { getColor: input.theme },
      }),
    );
  }

  /* ---- PORT band and deeper: queue rings at places (count from state).
   * Rings at routing-grade places subordinate (lighter
   * stroke + count) — the queue is still simulated and still shown on
   * the place's own board; it just carries less visual weight than a
   * calibration-grade queue. Disruption rings below deliberately do
   * NOT subordinate: danger means the same at either tier. ---- */
  if (bandAtLeast(band, "PORT")) {
    const nodeStateById = new Map(input.state.nodes.map((n) => [n.id, n]));
    const rings = queueRings(input.state)
      .map((r) => {
        const pos = input.nodePos.get(r.nodeId);
        if (!pos) return null;
        const shell =
          nodeTierOf(r.nodeId, nodeStateById.get(r.nodeId)?.tier, input.placeTiers) ===
          "shell";
        return { ...r, position: pos, shell };
      })
      .filter((r): r is NonNullable<typeof r> => r !== null);
    if (rings.length) {
      layers.push(
        new ScatterplotLayer({
          id: "sim-queue-rings",
          data: rings,
          getPosition: (d) => d.position,
          getRadius: (d) => 10 + Math.sqrt(d.queue) * 4,
          radiusUnits: "pixels",
          getFillColor: [0, 0, 0, 0],
          getLineColor: (d) =>
            hexToRgba(t.accent, d.shell ? Math.round(235 * TIER_WEIGHT.shellRingAlpha) : 235),
          lineWidthUnits: "pixels",
          getLineWidth: (d) => (d.shell ? 1.6 * TIER_WEIGHT.shellRingWidth : 1.6),
          stroked: true,
          filled: false,
          pickable: false,
          updateTriggers: { getLineColor: input.theme },
        }),
        new TextLayer({
          id: "sim-queue-counts",
          data: rings,
          getPosition: (d) => d.position,
          getText: (d) => `Q${d.queue}`,
          getSize: 8.5,
          sizeUnits: "pixels",
          getColor: (d) =>
            hexToRgba(t.accent, d.shell ? Math.round(255 * TIER_WEIGHT.shellRingAlpha) : 255),
          fontFamily: "'IBM Plex Mono', ui-monospace, monospace",
          fontWeight: 700,
          characterSet: "Q0123456789",
          pickable: false,
          updateTriggers: { getColor: input.theme },
        }),
      );
    }
  }

  /* ---- weather cells: modelled field cells from the STATE
   * payload (weather_cells), graded by the stronger of wind / wave
   * (severe takes the storm grade). Low-alpha fill, same-family
   * stroke. Present only while the WEATHER layer is on and the state
   * carries cells. ---- */
  if (L.WEATHER && input.state.weather_cells?.length) {
    const cells = input.state.weather_cells;
    layers.push(
      new PolygonLayer({
        id: "sim-weather-cells",
        data: cells,
        getPolygon: (c) => {
          const [w, s, e, n] = c.bbox;
          return [
            [w, s],
            [e, s],
            [e, n],
            [w, n],
            [w, s],
          ];
        },
        getFillColor: (c) => rgb(weatherCellGradeRgb(c), 62),
        getLineColor: (c) => rgb(weatherCellGradeRgb(c), 130),
        lineWidthUnits: "pixels",
        getLineWidth: 0.7,
        stroked: true,
        filled: true,
        pickable: false,
      }),
    );
  }

  /* ---- Weather: graded dots at network sample points (places +
   * lane midpoints). Wind sets place colour/size; wave sets midpoint
   * colour. Values are the pinned snapshot's (weather.ts). Weather is
   * a sampled field, not routing-grade traffic — no tier weighting. -- */
  if (L.WEATHER && input.weather?.status === "ok" && input.weather.points.length) {
    const pts = input.weather.points;
    layers.push(
      new ScatterplotLayer({
        id: "sim-weather",
        data: pts,
        getPosition: (d) => d.position,
        getRadius: (d) =>
          d.kind === "node"
            ? 4 + Math.min(10, (d.wind_kn ?? 0) / 4)
            : 3 + Math.min(8, (d.wave_m ?? 0) * 2),
        radiusUnits: "pixels",
        getFillColor: (d) => {
          const c = d.kind === "node"
            ? windGradeRgb(d.wind_kn ?? 0)
            : waveGradeRgb(d.wave_m ?? 0);
          return rgb(c, 165);
        },
        getLineColor: (d) => {
          const c = d.kind === "node"
            ? windGradeRgb(d.wind_kn ?? 0)
            : waveGradeRgb(d.wave_m ?? 0);
          return rgb(c, 235);
        },
        lineWidthUnits: "pixels",
        getLineWidth: 0.8,
        stroked: true,
        filled: true,
        pickable: false,
      }),
    );
    if (bandAtLeast(band, "PORT")) {
      const labelled = pts.filter(
        (d) => d.kind === "node" && d.wind_kn !== null,
      );
      if (labelled.length) {
        layers.push(
          new TextLayer({
            id: "sim-weather-labels",
            data: labelled,
            getPosition: (d) => d.position,
            getText: (d) => `${Math.round(d.wind_kn as number)}kn`,
            getSize: 8,
            sizeUnits: "pixels",
            getColor: hexToRgba(t.textDim, 230),
            fontFamily: "'IBM Plex Mono', ui-monospace, monospace",
            characterSet: "0123456789kn",
            pickable: false,
            updateTriggers: { getColor: input.theme },
          }),
        );
      }
    }
  }

  /* ---- Disruptions: danger rings at nodes with an ACTIVE disruption
   * at this tick (journal-resolved node ids). ---- */
  if (L.DISRUPTIONS && input.disruptionNodeIds.size) {
    const data = [...input.disruptionNodeIds]
      .map((id) => {
        const pos = input.nodePos.get(id);
        return pos ? { id, position: pos } : null;
      })
      .filter((d): d is NonNullable<typeof d> => d !== null);
    if (data.length) {
      layers.push(
        new ScatterplotLayer({
          id: "sim-disruptions",
          data,
          getPosition: (d) => d.position,
          getRadius: 15,
          radiusUnits: "pixels",
          getFillColor: hexToRgba(t.danger, 46),
          getLineColor: hexToRgba(t.danger, 245),
          lineWidthUnits: "pixels",
          getLineWidth: 1.8,
          stroked: true,
          filled: true,
          pickable: false,
          updateTriggers: { getLineColor: input.theme, getFillColor: input.theme },
        }),
      );
    }
  }

  return layers;
}

/** The full sim stack in the original draw order (lanes → flows →
 *  state layers), composed from the builders. Pure in
 *  the full input, exactly as before the split. */
export function buildSimLayers(input: SimLayerInput): Layer[] {
  return [
    ...buildSimLaneLayers(input),
    ...buildSimFlowLayers(input),
    ...buildSimStateLayers(input),
  ];
}
