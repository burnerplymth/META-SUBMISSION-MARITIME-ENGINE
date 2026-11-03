// PLACE LAYERS: places are small GLOWING POINTS, not shapes.
//   halo      additive soft radial sprite (kind colour)
//   nodes     core dot, radius from the capacity band, pickable
//   rings     thin rings: chokepoints, disrupted, selected, hovered
//   glyphs    a tiny kind letter inside the core, from a high zoom
//   clusters  count badges where places overlap at low zoom
//   labels    collision-managed, priority by capacity
// Kind is carried by COLOUR (+ the chokepoint ring); CORE reads
// brighter than SHELL. Every size and opacity is an interpolation of
// zoom (places/tiers.ts) so nothing pops; the layer stack rebuilds on
// the host's quantised zoom only.

import { IconLayer, PathLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import { PathStyleExtension } from "@deck.gl/extensions";
import type { Layer } from "@deck.gl/core";
import type {
  BasemapMode,
  ChainEdge,
  GraphData,
  GraphNode,
  NodeRunState,
  PortActivityRecord,
  ThemeName,
} from "../types";
import { isDarkTheme, placeKindColors, tokens, channelColors, type PlaceKindKey } from "../theme/tokens";
import { splitAntimeridianLonLat, type LonLat } from "../sim/geometry";
import { laneRoute, type LaneIndex } from "../lanes/laneIndex";
import { prefersReducedMotion } from "../components/motion";
import {
  ADDITIVE_BLEND,
  GLOW_ATLAS_URL,
  GLOW_MAPPING,
  NORMAL_BLEND,
  clampByte,
  hexToRgba,
  rgba,
  type RGBA,
} from "./color";
import { clusterPlaces, type PlaceCluster } from "./cluster";
import { selectLabels, type LabelCandidate, type PlacedLabel } from "./labels";
import {
  CLUSTER_BELOW,
  CLUSTER_CELL_PX,
  KIND_GLYPH_FROM,
  appearAlpha,
  appearZoom,
  capacityBands,
  clamp01,
  haloRadiusPx,
  markerRadiusPx,
  quantizeZoom,
  smoothstep,
  tierBrightness,
  zoomTier,
  tierAtLeast,
  type CapacityBand,
  type ZoomTier,
} from "./tiers";
import { placePaintEntryFor, placePaintRgba, type PlacePaint } from "./paint";

export const MONO = "'IBM Plex Mono', ui-monospace, monospace";

/** Run-state class of a place marker. `sel` is NOT a core state any
 *  more: selection is a ring over whatever state the place is in. */
export type MarkerState = "base" | "hot" | "warm" | "faint";

export interface ClusterHit {
  id: string;
  lon: number;
  lat: number;
  count: number;
  members: string[];
}

export interface PlaceCtx {
  theme: ThemeName;
  nodeById: Map<string, GraphNode>;
  runStateByNode: Map<string, NodeRunState>;
  /** highest revealed seq (-1 = no run reveal) */
  cascadeStep: number;
  selectedId: string | null;
  hoverId: string | null;
  onNodeClick: (n: GraphNode) => void;
  onNodeHover: (n: GraphNode | null) => void;
  basemap?: BasemapMode;
  /** true while the basemap is unreachable and the coastline underlay
   *  is the geography on screen */
  fallbackMap?: boolean;
  /** quantised MapLibre zoom */
  zoom?: number;
  hiddenKinds?: ReadonlySet<string>;
  placePaint?: PlacePaint | null;
  /** places the loaded sim run currently has disrupted */
  disruptedIds?: ReadonlySet<string>;
  /** a cluster badge was clicked: the host zooms to its members */
  onClusterClick?: (c: ClusterHit) => void;
  /** the pointer is over (true) / left (false) a cluster badge */
  onClusterHover?: (over: boolean) => void;
}

/* ---- per-graph static info (bands, appear zooms) ---- */
interface GraphInfo {
  bands: Map<string, CapacityBand>;
  appear: Map<string, number>;
}
const infoCache = new WeakMap<GraphData, GraphInfo>();
export function graphInfo(graph: GraphData): GraphInfo {
  const hit = infoCache.get(graph);
  if (hit) return hit;
  const bands = capacityBands(graph.nodes);
  const appear = new Map<string, number>();
  for (const n of graph.nodes) appear.set(n.id, appearZoom(n, bands.get(n.id) ?? 0));
  const info = { bands, appear };
  infoCache.set(graph, info);
  return info;
}

/** The glow family blends additively on dark grounds; on light
 *  grounds additive would wash to white, so normal blending is used. */
export function glowBlend(theme: ThemeName, basemap?: BasemapMode) {
  return basemap === "sat" || isDarkTheme(theme) ? ADDITIVE_BLEND : NORMAL_BLEND;
}

export function placeStateOf(n: GraphNode, ctx: PlaceCtx): MarkerState {
  const run = ctx.runStateByNode.get(n.id);
  if (run && run.seq <= ctx.cascadeStep) return run.magnitude >= 0.75 ? "hot" : "warm";
  if (ctx.disruptedIds?.has(n.id)) return "hot";
  if (run) return "faint";
  return "base";
}

const kindKey = (n: GraphNode): PlaceKindKey => n.kind as PlaceKindKey;

export interface PlaceMark {
  id: string;
  node: GraphNode;
  kind: PlaceKindKey;
  band: CapacityBand;
  /** 0..1 appearance (0 = not yet on screen) */
  alpha: number;
  /** core radius px; 0 when hidden */
  radius: number;
  haloRadius: number;
  state: MarkerState;
  selected: boolean;
  hovered: boolean;
  /** never absorbed into a cluster, always labelled */
  pinned: boolean;
  /** core fill */
  fill: RGBA;
  /** halo tint (alpha already scaled) */
  halo: RGBA;
  /** state-tinted rgb (danger / accent / faint, else the kind colour) */
  rgb: [number, number, number];
  /** the kind's own colour */
  kindRgb: [number, number, number];
}

/** One mark per drawable place at the context's zoom. Deterministic:
 *  graph order, pure function of (graph, ctx). */
export function buildPlaceMarks(graph: GraphData, ctx: PlaceCtx, onlyId?: string): PlaceMark[] {
  const t = tokens[ctx.theme];
  const kinds = placeKindColors(ctx.theme);
  const info = graphInfo(graph);
  const zoom = ctx.zoom ?? 1.35;
  const dark = isDarkTheme(ctx.theme);
  const sat = ctx.basemap === "sat";
  const stateRgb: Record<MarkerState, [number, number, number] | null> = {
    base: null,
    hot: hexToRgba(t.danger, 255).slice(0, 3) as [number, number, number],
    warm: hexToRgba(t.accent, 255).slice(0, 3) as [number, number, number],
    faint: hexToRgba(t.textFaint, 255).slice(0, 3) as [number, number, number],
  };
  const out: PlaceMark[] = [];
  for (const n of graph.nodes) {
    if (onlyId !== undefined && n.id !== onlyId) continue;
    const run = ctx.runStateByNode.get(n.id);
    const selected = ctx.selectedId === n.id;
    if (ctx.hiddenKinds?.has(n.kind) && !run && !selected) continue;
    const band = info.bands.get(n.id) ?? 0;
    const state = placeStateOf(n, ctx);
    const pinned = selected || !!run || state === "hot";
    /* the run's truth and the selection are never faded out */
    const alpha = pinned ? 1 : appearAlpha(zoom, info.appear.get(n.id) ?? 0);
    const kind = kindKey(n);
    const baseRgb = kinds[kind] ?? kinds.port;
    const rgb = stateRgb[state] ?? baseRgb;
    const bright = tierBrightness(n.tier);
    const radius = alpha > 0.02 ? markerRadiusPx(band, n.tier, zoom) * (0.55 + 0.45 * alpha) : 0;
    const paint = placePaintEntryFor(ctx.placePaint, n.id);
    const painted = paint ? placePaintRgba(paint) : null;
    let fill: RGBA;
    let haloRgb = rgb;
    if (painted) {
      fill = [painted[0], painted[1], painted[2], clampByte(painted[3] * alpha)];
      haloRgb = [painted[0], painted[1], painted[2]];
    } else {
      fill = rgba(rgb, clampByte(235 * alpha * (state === "faint" ? 0.7 : bright)));
    }
    const haloBase = dark || sat ? 110 : 72;
    const halo = rgba(
      haloRgb,
      clampByte(haloBase * alpha * bright * (state === "hot" ? 1.35 : state === "faint" ? 0.5 : 1)),
    );
    out.push({
      id: n.id,
      node: n,
      kind,
      band,
      alpha,
      radius,
      haloRadius: radius > 0 ? haloRadiusPx(radius) : 0,
      state,
      selected,
      hovered: ctx.hoverId === n.id,
      pinned,
      fill,
      halo,
      rgb,
      kindRgb: baseRgb,
    });
  }
  return out;
}

/* ---- label plan ---------------------------------------------------- */

/** Smallest capacity band that earns a label per tier. The world tier
 *  is label-free (the points read on their own); chain members, the
 *  selection and the hovered place are always labelled. */
export function labelMinBand(tier: ZoomTier): number {
  switch (tier) {
    case "world":
      return 9;
    case "ocean":
      return 3;
    case "region":
      return 2;
    default:
      return 0;
  }
}
const LABEL_CAP: Record<ZoomTier, number> = { world: 0, ocean: 16, region: 70, port: 170, berth: 260 };

/** Priority of a place label: band first, then log capacity. */
export function labelPriority(n: Pick<GraphNode, "capacity" | "tier">, band: CapacityBand): number {
  return band * 100 + Math.log10(Math.max(1, n.capacity)) * 10 + (n.tier === "shell" ? 0 : 5);
}

export function planLabels(marks: PlaceMark[], singles: ReadonlySet<string>, clusters: PlaceCluster[], zoom: number): PlacedLabel[] {
  const tier = zoomTier(zoom);
  const minBand = labelMinBand(tier);
  const cands: LabelCandidate[] = [];
  for (const m of marks) {
    if (!singles.has(m.id) || m.alpha < 0.55) continue;
    const forced = m.pinned || m.hovered;
    if (!forced && m.band < minBand) continue;
    cands.push({
      id: m.id,
      text: m.node.name.toUpperCase(),
      lon: m.node.lon,
      lat: m.node.lat,
      priority: labelPriority(m.node, m.band),
      radiusPx: m.radius,
      forced,
      preferEnd: m.selected,
    });
  }
  const obstacles = clusters.map((c) => [c.lon, c.lat, 9 + Math.min(5, Math.log2(c.count))] as const);
  return selectLabels(cands, zoom, { max: LABEL_CAP[tier], obstacles });
}

/* ---- the layer stack ------------------------------------------------ */

interface RingSpec {
  position: [number, number];
  radius: number;
  color: RGBA;
  width: number;
}

export function placeRings(marks: PlaceMark[], ctx: PlaceCtx, hiddenIds: ReadonlySet<string>): RingSpec[] {
  const t = tokens[ctx.theme];
  const rings: RingSpec[] = [];
  for (const m of marks) {
    if (m.radius <= 0 || hiddenIds.has(m.id)) continue;
    const pos: [number, number] = [m.node.lon, m.node.lat];
    if (m.kind === "chokepoint") {
      rings.push({ position: pos, radius: m.radius + 3.2, color: rgba(m.kindRgb, clampByte(200 * m.alpha)), width: 1 });
    }
    if (m.state === "hot") {
      rings.push({ position: pos, radius: m.radius + 3.6 + (m.kind === "chokepoint" ? 3.4 : 0), color: hexToRgba(t.danger, 235), width: 1.2 });
    }
    if (m.hovered && !m.selected) {
      rings.push({ position: pos, radius: m.radius + 4.2, color: hexToRgba(t.accent, 230), width: 1.2 });
    }
    if (m.selected) {
      rings.push({ position: pos, radius: m.radius + 5.4, color: hexToRgba(t.selection, 255), width: 1.8 });
      rings.push({ position: pos, radius: m.radius + 8.2, color: hexToRgba(t.selection, 110), width: 1 });
    }
  }
  return rings;
}

/** The hover affordance as its own small layer set (ring + name chip above the dot), so a hover
 *  change never rebuilds the static stack. The static stack is built with `hoverId: null`. A place
 *  that is selected has its selection ring already; it gets the name chip only. */
export function buildHoverLayers(graph: GraphData, ctx: PlaceCtx, hoverId: string | null): Layer[] {
  if (!hoverId) return [];
  const mark = buildPlaceMarks(graph, { ...ctx, hoverId }, hoverId)[0];
  if (!mark || mark.radius <= 0) return [];
  const t = tokens[ctx.theme];
  const layers: Layer[] = [];
  const pos: [number, number] = [mark.node.lon, mark.node.lat];
  if (!mark.selected) {
    layers.push(
      new ScatterplotLayer<RingSpec>({
        id: "hover-ring",
        data: [{ position: pos, radius: mark.radius + 4.2, color: hexToRgba(t.accent, 230), width: 1.2 }],
        getPosition: (r) => r.position,
        getRadius: (r) => r.radius,
        radiusUnits: "pixels",
        filled: false,
        stroked: true,
        getLineColor: (r) => r.color,
        getLineWidth: (r) => r.width,
        lineWidthUnits: "pixels",
        pickable: false,
      }),
    );
  }
  layers.push(
    new TextLayer<{ position: [number, number]; text: string }>({
      id: "hover-label",
      data: [{ position: pos, text: mark.node.name.toUpperCase() }],
      getPosition: (d: { position: [number, number] }) => d.position,
      getText: (d: { text: string }) => d.text,
      getSize: 9.5,
      sizeUnits: "pixels",
      getColor: hexToRgba(t.text, 255),
      getTextAnchor: "middle",
      getAlignmentBaseline: "bottom",
      getPixelOffset: [0, -(mark.radius + 9)],
      background: true,
      getBackgroundColor: hexToRgba(t.badgeBg, 232),
      backgroundPadding: [4, 2, 4, 2],
      getBorderColor: hexToRgba(t.accent, 230),
      getBorderWidth: 1,
      fontFamily: MONO,
      fontWeight: 600,
      characterSet: LABEL_CHARSET,
      pickable: false,
      updateTriggers: { getColor: ctx.theme, getBackgroundColor: ctx.theme },
    } as never),
  );
  return layers;
}

const GLYPH: Record<PlaceKindKey, string> = { port: "P", air_hub: "A", ground_node: "G", chokepoint: "C" };

export interface PlaceLayerResult {
  layers: Layer[];
  clusters: PlaceCluster[];
  labels: PlacedLabel[];
  marks: PlaceMark[];
}

export function buildPlaceLayers(graph: GraphData, ctx: PlaceCtx): PlaceLayerResult {
  const t = tokens[ctx.theme];
  const zoom = ctx.zoom ?? 1.35;
  const dark = isDarkTheme(ctx.theme);
  const sat = ctx.basemap === "sat";
  const blend = glowBlend(ctx.theme, ctx.basemap);
  const marks = buildPlaceMarks(graph, ctx);
  /* sizes and colours ease over 240 ms as the zoom steps, so places
   * neither pop nor jump; motion-averse users get the end state at once */
  const transitions = prefersReducedMotion() ? undefined : { getSize: 240, getRadius: 240, getColor: 240, getFillColor: 240 };

  /* ---- clusters: only visible, unpinned places collapse ---- */
  let clusters: PlaceCluster[] = [];
  const absorbed = new Set<string>();
  if (zoom < CLUSTER_BELOW) {
    const res = clusterPlaces(
      marks
        .filter((m) => m.alpha >= 0.55)
        .map((m) => ({
          id: m.id,
          lon: m.node.lon,
          lat: m.node.lat,
          weight: m.node.capacity,
          pinned: m.pinned,
        })),
      quantizeZoom(zoom, 0.5),
      CLUSTER_CELL_PX,
    );
    clusters = res.clusters;
    for (const c of clusters) for (const id of c.members) absorbed.add(id);
  }
  const markById = new Map(marks.map((m) => [m.id, m]));
  const drawn = marks.map((m) =>
    absorbed.has(m.id) ? { ...m, radius: 0, haloRadius: 0 } : m,
  );
  const singles = new Set(marks.filter((m) => !absorbed.has(m.id)).map((m) => m.id));

  const layers: Layer[] = [];

  /* halo: additive soft sprite */
  layers.push(
    new IconLayer<PlaceMark>({
      id: "place-halo",
      data: drawn,
      iconAtlas: GLOW_ATLAS_URL,
      iconMapping: GLOW_MAPPING,
      getIcon: () => "glow",
      getPosition: (m) => [m.node.lon, m.node.lat],
      getSize: (m) => m.haloRadius * 2,
      sizeUnits: "pixels",
      getColor: (m) => m.halo,
      pickable: false,
      parameters: blend as never,
      transitions: transitions as never,
      updateTriggers: { getColor: [ctx.theme, ctx.basemap, ctx.runStateByNode, ctx.cascadeStep, ctx.placePaint, zoom], getSize: zoom },
    }),
  );

  /* core dots (pickable) */
  layers.push(
    new ScatterplotLayer<PlaceMark>({
      id: "nodes",
      data: drawn,
      getPosition: (m) => [m.node.lon, m.node.lat],
      getRadius: (m) => m.radius,
      radiusUnits: "pixels",
      getFillColor: (m) => m.fill,
      getLineColor: (m) =>
        sat
          ? ([0, 0, 0, clampByte(210 * m.alpha)] as RGBA)
          : hexToRgba(dark ? t.ocean : "#ffffff", clampByte(200 * m.alpha)),
      lineWidthUnits: "pixels",
      getLineWidth: sat ? 1 : 0.7,
      stroked: true,
      filled: true,
      pickable: true,
      autoHighlight: false,
      onClick: (info) => {
        const m = info.object as PlaceMark | undefined;
        if (m) ctx.onNodeClick(m.node);
      },
      onHover: (info) => ctx.onNodeHover((info.object as PlaceMark | undefined)?.node ?? null),
      transitions: transitions as never,
      updateTriggers: {
        getFillColor: [ctx.cascadeStep, ctx.selectedId, ctx.placePaint, ctx.theme, ctx.runStateByNode, ctx.disruptedIds, zoom],
        getRadius: [zoom, ctx.selectedId],
        getLineColor: [ctx.hoverId, ctx.selectedId, ctx.basemap, ctx.theme, zoom],
        getLineWidth: [ctx.hoverId, ctx.selectedId, ctx.basemap],
      },
    }),
  );

  /* rings */
  const rings = placeRings(drawn, ctx, absorbed);
  if (rings.length) {
    layers.push(
      new ScatterplotLayer<RingSpec>({
        id: "place-rings",
        data: rings,
        getPosition: (r) => r.position,
        getRadius: (r) => r.radius,
        radiusUnits: "pixels",
        filled: false,
        stroked: true,
        getLineColor: (r) => r.color,
        getLineWidth: (r) => r.width,
        lineWidthUnits: "pixels",
        pickable: false,
        updateTriggers: { getLineColor: [ctx.theme, ctx.selectedId, ctx.hoverId, ctx.runStateByNode, ctx.cascadeStep, zoom] },
      }),
    );
  }

  /* kind glyph, from a high zoom, only where the core can hold it */
  if (zoom >= KIND_GLYPH_FROM) {
    const glyphMarks = drawn.filter((m) => m.radius >= 3.6 && m.alpha > 0.7);
    if (glyphMarks.length) {
      layers.push(
        new TextLayer<PlaceMark>({
          id: "place-glyphs",
          data: glyphMarks,
          getPosition: (m) => [m.node.lon, m.node.lat],
          getText: (m) => GLYPH[m.kind],
          getSize: (m) => Math.round(m.radius * 1.15 * 10) / 10,
          sizeUnits: "pixels",
          getColor: hexToRgba(dark ? t.ocean : "#ffffff", 235),
          fontFamily: MONO,
          fontWeight: 700,
          characterSet: "PAGC",
          getTextAnchor: "middle",
          getAlignmentBaseline: "center",
          pickable: false,
          updateTriggers: { getColor: ctx.theme, getSize: zoom },
        }),
      );
    }
  }

  /* cluster badges */
  if (clusters.length) {
    const hotCluster = (c: PlaceCluster): boolean => c.members.some((id) => markById.get(id)?.state === "hot");
    const dominantRgb = (c: PlaceCluster): [number, number, number] => {
      const counts = new Map<PlaceKindKey, number>();
      for (const id of c.members) {
        const k = markById.get(id)?.kind;
        if (k) counts.set(k, (counts.get(k) ?? 0) + 1);
      }
      let best: PlaceKindKey = "port";
      let n = -1;
      for (const k of ["port", "air_hub", "ground_node", "chokepoint"] as PlaceKindKey[]) {
        if ((counts.get(k) ?? 0) > n) {
          best = k;
          n = counts.get(k) ?? 0;
        }
      }
      return placeKindColors(ctx.theme)[best];
    };
    const radiusOf = (c: PlaceCluster): number => 7 + Math.min(5, Math.log2(c.count)) * 1.3;
    layers.push(
      new IconLayer<PlaceCluster>({
        id: "place-cluster-halo",
        data: clusters,
        iconAtlas: GLOW_ATLAS_URL,
        iconMapping: GLOW_MAPPING,
        getIcon: () => "glow",
        getPosition: (c) => [c.lon, c.lat],
        getSize: (c) => radiusOf(c) * 4.4,
        sizeUnits: "pixels",
        getColor: (c) => rgba(hotCluster(c) ? hexToRgba(t.danger, 255) : dominantRgb(c), dark || sat ? 120 : 80),
        pickable: false,
        parameters: blend as never,
        updateTriggers: { getColor: [ctx.theme, ctx.basemap, ctx.runStateByNode, ctx.cascadeStep] },
      }),
      new ScatterplotLayer<PlaceCluster>({
        id: "place-clusters",
        data: clusters,
        getPosition: (c) => [c.lon, c.lat],
        getRadius: radiusOf,
        radiusUnits: "pixels",
        filled: true,
        stroked: true,
        getFillColor: hexToRgba(dark ? t.panel : "#ffffff", 232),
        getLineColor: (c) => rgba(hotCluster(c) ? hexToRgba(t.danger, 255) : dominantRgb(c), 255),
        lineWidthUnits: "pixels",
        getLineWidth: 1.2,
        pickable: true,
        onClick: (info) => {
          const c = info.object as PlaceCluster | undefined;
          if (c) ctx.onClusterClick?.({ id: c.id, lon: c.lon, lat: c.lat, count: c.count, members: c.members });
        },
        onHover: (info) => ctx.onClusterHover?.(!!info.object),
        updateTriggers: { getFillColor: ctx.theme, getLineColor: [ctx.theme, ctx.runStateByNode, ctx.cascadeStep] },
      }),
      new TextLayer<PlaceCluster>({
        id: "place-cluster-labels",
        data: clusters,
        getPosition: (c) => [c.lon, c.lat],
        getText: (c) => String(c.count),
        getSize: 9.5,
        sizeUnits: "pixels",
        getColor: hexToRgba(t.text, 255),
        fontFamily: MONO,
        fontWeight: 700,
        characterSet: "0123456789",
        getTextAnchor: "middle",
        getAlignmentBaseline: "center",
        pickable: false,
        updateTriggers: { getColor: ctx.theme },
      }),
    );
  }

  /* labels */
  const labels = planLabels(drawn, singles, clusters, zoom);
  if (labels.length) {
    layers.push(
      new TextLayer<PlacedLabel>({
        id: "place-labels",
        data: labels,
        getPosition: (l) => [l.lon, l.lat],
        getText: (l) => l.text,
        getSize: 9.5,
        sizeUnits: "pixels",
        getColor: hexToRgba(t.text, sat ? 255 : 232),
        getTextAnchor: (l) => l.anchor,
        getAlignmentBaseline: "center",
        getPixelOffset: (l) => [l.dx, 0],
        fontFamily: MONO,
        fontWeight: 600,
        characterSet: LABEL_CHARSET,
        fontSettings: { sdf: true },
        outlineWidth: sat ? 0.34 : 0.22,
        outlineColor: sat
          ? ([0, 0, 0, 255] as RGBA)
          : dark
            ? ([11, 11, 12, 235] as RGBA)
            : ([255, 255, 255, 235] as RGBA),
        pickable: false,
        updateTriggers: { getColor: [ctx.theme, ctx.basemap], outlineColor: [ctx.basemap, ctx.theme] },
      }),
    );
  }

  return { layers, clusters, labels, marks: drawn };
}

/** Character set for place labels: printable ASCII + the Latin
 *  diacritics the served place names use (Gdańsk, Constanța, …). */
export const LABEL_CHARSET: string = (() => {
  let s = "";
  for (let c = 32; c < 127; c++) s += String.fromCharCode(c);
  return (
    s +
    "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ" +
    "ĀāĂăĄąĆćĈĉĊċČčĎďĐđĒēĖėĘęĚěĜĝĞğĠġĢģĤĥĦħĨĩĪīĮįİıĴĵĶķĹĺĻļĽľŁł" +
    "ŃńŅņŇňŊŋŌōŎŏŐőŒœŔŕŖŗŘřŚśŜŝŞşŠšŢţŤťŦŧŨũŪūŬŭŮůŰűŲųŴŵŶŷŸŹźŻżŽžȘșȚț"
  );
})();

/* ---- PortWatch activity: a soft glow sized by vessel count ---- */

export function buildActivityLayer(
  records: PortActivityRecord[],
  ctx: Pick<PlaceCtx, "theme" | "basemap" | "fallbackMap" | "nodeById" | "zoom">,
): Layer {
  const t = tokens[ctx.theme];
  const sat = ctx.basemap === "sat";
  const blend = glowBlend(ctx.theme, ctx.basemap);
  const zoom = ctx.zoom ?? 1.35;
  /* A faint wash, never a blob: the served snapshot carries hundreds of
   * records, and additive glows stack. Alpha: normal < fallback (the
   * underlay is the geography) < satellite; size grows gently with zoom. */
  const alpha = sat ? 70 : ctx.fallbackMap ? 44 : 30;
  const grow = 1 + 0.7 * smoothstep(3, 10, zoom);
  /* a record whose place is not in the network has no position: it is
   * not drawn (never parked at [0, 0], where a dozen would stack into a
   * white blob in the Gulf of Guinea) */
  const known = records.filter((r) => ctx.nodeById.has(r.node_id));
  return new IconLayer<PortActivityRecord>({
    id: "portwatch-activity",
    data: known,
    iconAtlas: GLOW_ATLAS_URL,
    iconMapping: GLOW_MAPPING,
    getIcon: () => "glow",
    getPosition: (r) => {
      const n = ctx.nodeById.get(r.node_id) as GraphNode;
      return [n.lon, n.lat];
    },
    getSize: (r) => (10 + Math.log10(Math.max(1, r.vessel_count_total)) * 6) * grow,
    sizeUnits: "pixels",
    getColor: hexToRgba(t.vessel, alpha),
    pickable: false,
    parameters: blend as never,
    updateTriggers: { getColor: [ctx.basemap, ctx.fallbackMap, ctx.theme], getSize: zoom },
  });
}

/* ---- chain arcs: luminous lines from cause to effect ---- */

export interface ArcDatum {
  edge: ChainEdge;
  path: LonLat[];
  rgb: [number, number, number];
  /** a chain arc between two sea places that has no lane path: the lifted chord, drawn dashed (Approx.) */
  approx?: boolean;
}

const wrapLon = (lon: number): number => ((((lon + 180) % 360) + 360) % 360) - 180;

/** A lifted quadratic arc between two places on the SHORT way round
 *  the globe, split at the antimeridian. Pure. */
export function chainArcPaths(a: GraphNode, b: GraphNode, samples = 28): LonLat[][] {
  let dlon = b.lon - a.lon;
  if (dlon > 180) dlon -= 360;
  if (dlon < -180) dlon += 360;
  const blon = a.lon + dlon;
  const dx = blon - a.lon;
  const dy = b.lat - a.lat;
  const dist = Math.hypot(dx, dy);
  if (dist < 1e-9) return [];
  const mx = (a.lon + blon) / 2;
  const my = (a.lat + b.lat) / 2;
  /* lift perpendicular to the chord, towards the north */
  let px = -dy / dist;
  let py = dx / dist;
  if (py < 0) {
    px = -px;
    py = -py;
  }
  const lift = Math.min(28, dist * 0.2);
  const cx = mx + px * lift;
  const cy = Math.max(-80, Math.min(84, my + py * lift));
  const pts: LonLat[] = [];
  for (let i = 0; i <= samples; i++) {
    const s = i / samples;
    const lon = (1 - s) * (1 - s) * a.lon + 2 * (1 - s) * s * cx + s * s * blon;
    const lat = (1 - s) * (1 - s) * a.lat + 2 * (1 - s) * s * cy + s * s * b.lat;
    pts.push([wrapLon(lon), lat]);
  }
  return splitAntimeridianLonLat(pts);
}

/** The path(s) a chain arc between two places is drawn on. Two sea places with a path of lane legs
 *  between them: the lane polyline (LANE GEOMETRY). Two sea places without one: the lifted
 *  chord, marked approx (dashed, Approx.). Any other pair (air, ground): the lifted chord as ever. */
export function arcPathsFor(
  a: GraphNode,
  b: GraphNode,
  lanes: LaneIndex | null | undefined,
  samples = 28,
): { paths: LonLat[][]; approx: boolean } {
  if (lanes && a.mode === "sea" && b.mode === "sea") {
    const r = laneRoute(lanes, a.id, b.id);
    if (r && r.parts.length) return { paths: r.parts, approx: false };
    return { paths: chainArcPaths(a, b, samples), approx: true };
  }
  return { paths: chainArcPaths(a, b, samples), approx: false };
}

/** Point at a 0..1 fraction along a polyline (by vertex index; the
 *  samples are near-uniform so this reads as constant speed). */
export function pointAlong(path: ReadonlyArray<LonLat>, f: number): LonLat {
  if (path.length === 0) return [0, 0];
  const x = clamp01(f) * (path.length - 1);
  const i = Math.min(path.length - 2, Math.floor(x));
  if (i < 0) return path[0];
  const r = x - i;
  return [path[i][0] + (path[i + 1][0] - path[i][0]) * r, path[i][1] + (path[i + 1][1] - path[i][1]) * r];
}

export function revealedArcData(
  chainEdges: ChainEdge[],
  ctx: Pick<PlaceCtx, "nodeById" | "theme" | "cascadeStep"> & { laneIndex?: LaneIndex | null },
): ArcDatum[] {
  const t = tokens[ctx.theme];
  const accent = hexToRgba(t.accent, 255).slice(0, 3) as [number, number, number];
  const out: ArcDatum[] = [];
  for (const e of chainEdges) {
    if (e.to_seq > ctx.cascadeStep || e.from_node === e.to_node || e.channel === "reroute") continue;
    const a = ctx.nodeById.get(e.from_node);
    const b = ctx.nodeById.get(e.to_node);
    if (!a || !b) continue;
    const rgb = (channelColors[e.channel] ?? accent) as [number, number, number];
    const { paths, approx } = arcPathsFor(a, b, ctx.laneIndex);
    for (const path of paths) out.push(approx ? { edge: e, path, rgb, approx } : { edge: e, path, rgb });
  }
  return out;
}

export function buildChainArcLayers(
  arcs: ArcDatum[],
  ctx: Pick<PlaceCtx, "theme" | "basemap">,
  pulseSeq: number | null,
): Layer[] {
  if (!arcs.length) return [];
  const sat = ctx.basemap === "sat";
  const blend = glowBlend(ctx.theme, ctx.basemap);
  const hot = (d: ArcDatum): boolean => d.edge.to_seq === pulseSeq;
  return [
    new PathLayer<ArcDatum>({
      id: "run-arcs-glow",
      data: arcs,
      getPath: (d) => d.path,
      getColor: (d) => rgba(d.rgb, hot(d) ? 70 : 44),
      getWidth: (d) => (hot(d) ? 8 : 6),
      widthUnits: "pixels",
      capRounded: true,
      jointRounded: true,
      pickable: false,
      parameters: blend as never,
      updateTriggers: { getColor: [ctx.theme, pulseSeq], getWidth: pulseSeq },
    }),
    new PathLayer<ArcDatum>({
      id: "run-arcs",
      data: arcs.filter((d) => !d.approx),
      getPath: (d) => d.path,
      getColor: (d) => rgba(d.rgb, sat ? 255 : 235),
      getWidth: (d) => (hot(d) ? 2.6 : 1.6),
      widthUnits: "pixels",
      capRounded: true,
      jointRounded: true,
      pickable: false,
      updateTriggers: { getColor: [ctx.basemap, ctx.theme], getWidth: pulseSeq },
    }),
    ...(arcs.some((d) => d.approx)
      ? [
          new PathLayer<ArcDatum>({
            id: "run-arcs-approx",
            data: arcs.filter((d) => d.approx),
            getPath: (d: ArcDatum) => d.path,
            getColor: (d: ArcDatum) => rgba(d.rgb, sat ? 255 : 235),
            getWidth: (d: ArcDatum) => (hot(d) ? 2.6 : 1.6),
            widthUnits: "pixels",
            getDashArray: [5, 4],
            dashJustified: true,
            extensions: [new PathStyleExtension({ dash: true })],
            pickable: false,
            updateTriggers: { getColor: [ctx.basemap, ctx.theme], getWidth: pulseSeq },
          } as never),
        ]
      : []),
    /* the effect end: a bright terminal dot reads the direction even
     * when motion is off */
    new ScatterplotLayer<ArcDatum>({
      id: "run-arc-heads",
      data: arcs,
      getPosition: (d) => d.path[d.path.length - 1],
      getRadius: 2.6,
      radiusUnits: "pixels",
      getFillColor: (d) => rgba(d.rgb, 255),
      pickable: false,
      updateTriggers: { getFillColor: ctx.theme },
    }),
  ];
}

/* ---- ambient (animated) layers: rebuilt per animation frame only ---- */

export interface AmbientCtx extends Pick<PlaceCtx, "theme" | "nodeById" | "runStateByNode" | "cascadeStep" | "disruptedIds" | "basemap"> {
  zoom: number;
}

/** Ids of places that currently pulse (disrupted / hot in the chain). */
export function pulsingPlaces(
  ctx: Pick<PlaceCtx, "nodeById" | "runStateByNode" | "cascadeStep" | "disruptedIds">,
): GraphNode[] {
  const out: GraphNode[] = [];
  const seen = new Set<string>();
  ctx.runStateByNode.forEach((s, id) => {
    if (s.seq <= ctx.cascadeStep && s.magnitude >= 0.75) {
      const n = ctx.nodeById.get(id);
      if (n) {
        seen.add(id);
        out.push(n);
      }
    }
  });
  ctx.disruptedIds?.forEach((id) => {
    if (seen.has(id)) return;
    const n = ctx.nodeById.get(id);
    if (n) out.push(n);
  });
  return out;
}

/** Disrupted places emit a slow, faint expanding ring; comets travel
 *  each revealed arc from cause to effect. Under reduced motion the
 *  ring is a still halo and the comets are absent. */
export function buildAmbientLayers(
  graph: GraphData,
  arcs: ArcDatum[],
  ctx: AmbientCtx,
  phase: number,
  reducedMotion: boolean,
): Layer[] {
  const t = tokens[ctx.theme];
  const info = graphInfo(graph);
  const out: Layer[] = [];
  const hotNodes = pulsingPlaces(ctx);
  const rOf = (n: GraphNode): number => markerRadiusPx(info.bands.get(n.id) ?? 0, n.tier, ctx.zoom);
  if (hotNodes.length) {
    type Pulse = { n: GraphNode; p: number };
    const data: Pulse[] = [];
    if (reducedMotion) {
      for (const n of hotNodes) data.push({ n, p: 0.35 });
    } else {
      for (const n of hotNodes) {
        data.push({ n, p: phase });
        data.push({ n, p: (phase + 0.5) % 1 });
      }
    }
    out.push(
      new ScatterplotLayer<Pulse>({
        id: "place-pulse",
        data,
        getPosition: (d) => [d.n.lon, d.n.lat],
        getRadius: (d) => rOf(d.n) + 3 + d.p * 15,
        radiusUnits: "pixels",
        filled: false,
        stroked: true,
        getLineColor: (d) => hexToRgba(t.danger, Math.round(reducedMotion ? 120 : 170 * Math.pow(1 - d.p, 1.6))),
        getLineWidth: 1.1,
        lineWidthUnits: "pixels",
        pickable: false,
        updateTriggers: { getRadius: [phase, ctx.zoom], getLineColor: phase },
      }),
    );
  }
  if (arcs.length && !reducedMotion) {
    type Comet = { pos: LonLat; a: number; r: number; rgb: [number, number, number] };
    const comets: Comet[] = [];
    arcs.forEach((d, i) => {
      const off = (i * 0.2371) % 1;
      const f = (phase + off) % 1;
      /* a head and two fading trail points */
      comets.push({ pos: pointAlong(d.path, f), a: 255, r: 2.6, rgb: d.rgb });
      comets.push({ pos: pointAlong(d.path, Math.max(0, f - 0.035)), a: 150, r: 2.1, rgb: d.rgb });
      comets.push({ pos: pointAlong(d.path, Math.max(0, f - 0.07)), a: 70, r: 1.6, rgb: d.rgb });
    });
    out.push(
      new ScatterplotLayer<Comet>({
        id: "run-arc-comets",
        data: comets,
        getPosition: (c) => c.pos,
        getRadius: (c) => c.r,
        radiusUnits: "pixels",
        getFillColor: (c) => rgba(c.rgb, c.a),
        pickable: false,
        parameters: glowBlend(ctx.theme, ctx.basemap) as never,
        updateTriggers: { getPosition: phase },
      }),
    );
  }
  return out;
}

/* ---- sequence numbers (causal chain order) ---- */

export interface SeqBadge {
  node: GraphNode;
  seq: number;
  channel: string;
}

export function buildSeqBadges(
  badges: SeqBadge[],
  ctx: Pick<PlaceCtx, "theme" | "zoom">,
): Layer[] {
  const zoom = ctx.zoom ?? 1.35;
  if (!badges.length || !tierAtLeast(zoomTier(zoom), "ocean")) return [];
  const t = tokens[ctx.theme];
  const accent = hexToRgba(t.accent, 255).slice(0, 3) as [number, number, number];
  return [
    new TextLayer<SeqBadge>({
      id: "badge-labels",
      data: badges,
      getPosition: (d: SeqBadge) => [d.node.lon, d.node.lat],
      getText: (d: SeqBadge) => String(d.seq + 1),
      getSize: 8.5,
      sizeUnits: "pixels",
      getColor: hexToRgba(t.badgeText, 255),
      getPixelOffset: [10, -10],
      background: true,
      getBackgroundColor: hexToRgba(t.badgeBg, 238),
      backgroundPadding: [3, 1.5, 3, 1.5],
      getBorderColor: (d: SeqBadge) => rgba((channelColors[d.channel] ?? accent) as number[], 255),
      getBorderWidth: 1,
      fontFamily: MONO,
      fontWeight: 700,
      characterSet: "0123456789",
      getTextAnchor: "middle",
      getAlignmentBaseline: "center",
      pickable: false,
      updateTriggers: { getColor: ctx.theme, getBackgroundColor: ctx.theme, getBorderColor: ctx.theme },
    } as never),
  ];
}

