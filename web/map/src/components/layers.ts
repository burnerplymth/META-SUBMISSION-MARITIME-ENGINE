import { GeoJsonLayer, LineLayer, PathLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import { PathStyleExtension } from "@deck.gl/extensions";
import type { Layer } from "@deck.gl/core";
import type {
  ChainEdge,
  GraphData,
  GraphNode,
  NodeRunState,
  PortActivityRecord,
  ThemeName,
} from "../types";
import { REROUTE_ARC, isDarkTheme, placeKindColors, tokens } from "../theme/tokens";
import { splitAntimeridianLonLat, type LonLat } from "../sim/geometry";
import { laneIndexAt, laneRoute, laneSegments, pairKey, type LaneIndex, type LaneSet } from "../lanes/laneIndex";
import { hexToRgba, rgba, type RGBA } from "../places/color";
import { appearAlpha, legAlpha, legWidthPx, quantizeZoom, smoothstep } from "../places/tiers";
import {
  LABEL_CHARSET,
  MONO,
  buildActivityLayer,
  buildChainArcLayers,
  buildHoverLayers as buildPlaceHoverLayers,
  buildPlaceLayers,
  buildSeqBadges,
  arcPathsFor,
  glowBlend,
  graphInfo,
  revealedArcData,
  type ArcDatum,
  type PlaceCtx,
  type SeqBadge,
} from "../places/placeLayers";

/* PLACE PAINT lives in places/paint.ts; re-exported here so the
 * package surface (index.ts) is unchanged. */
export {
  placePaintEntryFor,
  placePaintLegend,
  placePaintRgba,
  type PlacePaint,
  type PlacePaintEntry,
  type PlacePaintLegendEntry,
} from "../places/paint";
export { hexToRgba, LABEL_CHARSET };
export type { MarkerState, ClusterHit } from "../places/placeLayers";

const REROUTE_EXT = [new PathStyleExtension({ dash: true })];

export interface LayerContext extends PlaceCtx {
  /** currently pulsing seq (latest revealed) */
  pulseSeq: number | null;
  /** 0..1 pulse phase for the latest-step halo */
  pulsePhase: number;
  showGraphEdges: boolean;
  showActivity: boolean;
  /** Layer filters: edge modes hidden by the rail layer group.
   *  Chain members and the selection are never hidden — the run's
   *  truth stays on the map. */
  hiddenModes?: ReadonlySet<string>;
  /** LANE GEOMETRY: lane polylines of the sea legs, simplified and full. With it the legs and
   *  the chain arcs of sea places follow the lanes; sea legs without a lane stay chords, drawn dashed
   *  (Approx.). Absent = the chords, as before. */
  lanes?: LaneSet | null;
}

/* Base graph edges are drawn as LineLayer segments, and a
 * straight [lon, lat] segment between two nodes on opposite sides of
 * the antimeridian streaks a line across the whole map (measured: 49
 * of the real graph's 4,550 edges have |Δlon| > 180°). Base edges go
 * through the same antimeridian splitter the sim lanes use
 * (sim/geometry.ts): each edge expands to one segment per subpath
 * piece, so no drawn segment ever spans > 180° of longitude. The
 * expansion is a pure function of the graph, cached per graph object. */
export interface BaseEdgeSegment {
  source: LonLat;
  target: LonLat;
  mode: string;
  capacity_share: number;
  from: string;
  to: string;
  /** a sea leg drawn as its chord because it has no lane geometry (Approx.) */
  approx?: boolean;
}
const baseEdgeCache = new WeakMap<GraphData, BaseEdgeSegment[]>();
const laneEdgeCache = new WeakMap<GraphData, WeakMap<LaneIndex, BaseEdgeSegment[]>>();

/** Legs along the lanes: a sea leg that has a lane pair is drawn once per pair along its polyline; a sea
 *  leg without one stays a chord marked `approx`; air and ground legs are chords as ever. Pure. */
export function baseEdgeSegmentsOnLanes(graph: GraphData, lanes: LaneIndex): BaseEdgeSegment[] {
  let per = laneEdgeCache.get(graph);
  if (!per) laneEdgeCache.set(graph, (per = new WeakMap()));
  const hit = per.get(lanes);
  if (hit) return hit;
  const chords = baseEdgeSegments(graph);
  const share = new Map<string, { share: number; from: string; to: string }>();
  for (const e of graph.edges) {
    if (e.mode !== "sea") continue;
    const k = pairKey(e.from, e.to);
    if (!lanes.pairs.has(k)) continue;
    const cur = share.get(k);
    if (!cur || e.capacity_share > cur.share) share.set(k, { share: e.capacity_share, from: e.from, to: e.to });
  }
  const out: BaseEdgeSegment[] = [];
  for (const c of chords) {
    if (c.mode !== "sea") {
      out.push(c);
    } else if (!lanes.pairs.has(pairKey(c.from, c.to))) {
      out.push({ ...c, approx: true });
    }
  }
  for (const seg of laneSegments(lanes)) {
    const s = share.get(seg.pair);
    if (!s) continue;
    out.push({ source: seg.source, target: seg.target, mode: "sea", capacity_share: s.share, from: s.from, to: s.to });
  }
  per.set(lanes, out);
  return out;
}

/** The visible leg arrays the legs layers draw, memoised by (segment source, hidden modes).
 *  A new array identity makes deck.gl regenerate its attribute buffers, so repeated builds with
 *  the same graph, lanes and hidden modes must hand back the very same arrays. */
export interface LegSegments {
  /** lane / non-sea legs, drawn as solid lines */
  segs: BaseEdgeSegment[];
  /** sea legs with no lane geometry: the dashed chord (Approx.) */
  approxSegs: BaseEdgeSegment[];
}
const legSegCache = new WeakMap<BaseEdgeSegment[], Map<string, LegSegments>>();
export function legSegments(graph: GraphData, laneIdx: LaneIndex | null, hiddenModes?: ReadonlySet<string>): LegSegments {
  const all = laneIdx ? baseEdgeSegmentsOnLanes(graph, laneIdx) : baseEdgeSegments(graph);
  const key = hiddenModes && hiddenModes.size ? [...hiddenModes].sort().join(",") : "";
  let per = legSegCache.get(all);
  if (!per) legSegCache.set(all, (per = new Map()));
  const hit = per.get(key);
  if (hit) return hit;
  const visible = hiddenModes && hiddenModes.size ? all.filter((e) => !hiddenModes.has(e.mode)) : all;
  const out: LegSegments = { segs: visible.filter((e) => !e.approx), approxSegs: visible.filter((e) => e.approx) };
  per.set(key, out);
  return out;
}

export function baseEdgeSegments(graph: GraphData): BaseEdgeSegment[] {
  const hit = baseEdgeCache.get(graph);
  if (hit) return hit;
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const out: BaseEdgeSegment[] = [];
  for (const e of graph.edges) {
    const a = byId.get(e.from);
    const b = byId.get(e.to);
    const pa: LonLat = a ? [a.lon, a.lat] : [0, 0];
    const pb: LonLat = b ? [b.lon, b.lat] : [0, 0];
    for (const sub of splitAntimeridianLonLat([pa, pb])) {
      for (let i = 1; i < sub.length; i++) {
        out.push({
          source: sub[i - 1],
          target: sub[i],
          mode: e.mode,
          capacity_share: e.capacity_share,
          from: e.from,
          to: e.to,
        });
      }
    }
  }
  baseEdgeCache.set(graph, out);
  return out;
}

/* ---- Graticule: 30° meridians/parallels. Built once; inserted FIRST
 * so it sits under every leg, arc and place. Hairline, theme-toned. ---- */
interface GratFeature {
  type: "Feature";
  properties: Record<string, never>;
  geometry: { type: "LineString"; coordinates: Array<[number, number]> };
}
const GRATICULE: { type: "FeatureCollection"; features: GratFeature[] } = (() => {
  const features: GratFeature[] = [];
  for (let lon = -180; lon <= 180; lon += 30) {
    features.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: [[lon, -80], [lon, 80]] } });
  }
  for (let lat = -60; lat <= 60; lat += 30) {
    const pts: Array<[number, number]> = [];
    for (let lon = -180; lon <= 180; lon += 10) pts.push([lon, lat]);
    features.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: pts } });
  }
  return { type: "FeatureCollection", features };
})();

/** The pulse halo as its own layer builder — the ONLY layer
 *  whose paint depends on pulsePhase. Everything else the map draws
 *  is static between cascade steps (buildStaticLayers below), so the
 *  pulse can animate on its own without rebuilding the full stack. */
export interface PulseContext {
  nodeById: Map<string, GraphNode>;
  runStateByNode: Map<string, NodeRunState>;
  pulseSeq: number | null;
  pulsePhase: number;
  theme: ThemeName;
}

export function buildPulseLayer(ctx: PulseContext): Layer | null {
  if (ctx.pulseSeq === null) return null;
  const t = tokens[ctx.theme];
  const accentRgb = hexToRgba(t.accent, 255).slice(0, 3) as [number, number, number];
  const evNode = [...ctx.runStateByNode.entries()].find(
    ([, s]) => s.seq === ctx.pulseSeq,
  );
  if (!evNode) return null;
  const n = ctx.nodeById.get(evNode[0]);
  if (!n) return null;
  const phase = ctx.pulsePhase;
  return new ScatterplotLayer({
    id: "cascade-pulse",
    data: [n],
    getPosition: (d: GraphNode) => [d.lon, d.lat],
    getRadius: 9 + phase * 26,
    radiusUnits: "pixels",
    getFillColor: [...accentRgb, Math.round(60 * (1 - phase))] as RGBA,
    getLineColor: [...accentRgb, Math.round(220 * (1 - phase))] as RGBA,
    lineWidthUnits: "pixels",
    getLineWidth: 1.5,
    stroked: true,
    filled: true,
    pickable: false,
    updateTriggers: { getRadius: phase, getFillColor: phase },
  });
}

/** Splice the pulse layer into a static layer stack just under the
 *  place cores. A null pulse returns the stack unchanged. */
export function withPulseLayer(layers: Layer[], pulse: Layer | null): Layer[] {
  if (!pulse) return layers;
  const at = layers.findIndex((l) => l.id === "place-halo");
  if (at === -1) return [...layers, pulse];
  return [...layers.slice(0, at), pulse, ...layers.slice(at)];
}

const REROUTE_PATH_STEPS = 24;

function buildLayersInternal(
  graph: GraphData,
  chainEdges: ChainEdge[],
  activity: PortActivityRecord[],
  ctx: LayerContext,
  includePulse: boolean,
): Layer[] {
  const layers: Layer[] = [];
  const t = tokens[ctx.theme];
  const dark = isDarkTheme(ctx.theme);
  const { nodeById } = ctx;
  const sat = ctx.basemap === "sat";
  const zoom = ctx.zoom ?? 1.35;
  const kinds = placeKindColors(ctx.theme);
  const blend = glowBlend(ctx.theme, ctx.basemap);

  /* ---- Graticule: first layer ---- */
  layers.push(
    new GeoJsonLayer({
      id: "graticule",
      data: GRATICULE,
      filled: false,
      stroked: true,
      getLineColor: dark ? ([150, 170, 190, sat ? 40 : 26] as RGBA) : ([80, 110, 140, 38] as RGBA),
      getLineWidth: 0.6,
      lineWidthUnits: "pixels",
      pickable: false,
      updateTriggers: { getLineColor: [ctx.theme, ctx.basemap] },
    }),
  );

  /* ---- Legs (base graph edges): thin luminous lines. Opacity by
   * zoom and by flow share; a leg is as visible as the LESS visible
   * of its two places, so a leg never shows before its ends. Sea,
   * air and ground legs take their kind's colour. ---- */
  if (ctx.showGraphEdges) {
    const info = graphInfo(graph);
    const laneIdx = laneIndexAt(ctx.lanes, zoom);
    const { segs, approxSegs } = legSegments(graph, laneIdx, ctx.hiddenModes);
    const modeRgb = (mode: string): [number, number, number] =>
      mode === "air" ? kinds.air_hub : mode === "ground" ? kinds.ground_node : kinds.port;
    /* over the coastline underlay (tiles unreachable) the
     * legs gain presence (1.6x); over imagery as well. */
    const boost = ctx.fallbackMap ? 1.6 : 1;
    const satBoost = sat ? 1.6 : 1;
    const alphaOf = (e: BaseEdgeSegment): number => {
      const ends = Math.min(
        appearAlpha(zoom, info.appear.get(e.from) ?? 0),
        appearAlpha(zoom, info.appear.get(e.to) ?? 0),
      );
      /* a transoceanic chord is context, not signal: long legs fade */
      const span = Math.hypot(e.target[0] - e.source[0], e.target[1] - e.source[1]);
      const lengthFade = 1 - 0.5 * smoothstep(12, 80, span);
      return Math.min(255, Math.round(legAlpha(zoom, e.capacity_share) * ends * boost * satBoost * lengthFade));
    };
    layers.push(
      new LineLayer<BaseEdgeSegment>({
        id: "graph-edges-glow",
        data: segs,
        getSourcePosition: (e) => e.source,
        getTargetPosition: (e) => e.target,
        getColor: (e) => rgba(modeRgb(e.mode), Math.round(alphaOf(e) * (dark || sat ? 0.3 : 0.2))),
        getWidth: (e) => legWidthPx(zoom, e.capacity_share) * 3.6,
        widthUnits: "pixels",
        pickable: false,
        parameters: blend as never,
        updateTriggers: { getColor: [zoom, ctx.basemap, ctx.theme, ctx.fallbackMap], getWidth: zoom },
      }),
      new LineLayer<BaseEdgeSegment>({
        id: "graph-edges",
        data: segs,
        getSourcePosition: (e) => e.source,
        getTargetPosition: (e) => e.target,
        getColor: (e) => rgba(modeRgb(e.mode), alphaOf(e)),
        getWidth: (e) => legWidthPx(zoom, e.capacity_share) + (sat ? 0.3 : 0),
        widthUnits: "pixels",
        widthMinPixels: 0.4,
        widthMaxPixels: 3,
        pickable: false,
        updateTriggers: {
          getColor: [zoom, ctx.basemap, ctx.theme, ctx.fallbackMap],
          getWidth: [zoom, ctx.basemap],
        },
      }),
    );
    if (approxSegs.length) {
      /* sea legs with no lane geometry: the straight chord, dashed (Approx.) */
      layers.push(
        new PathLayer<BaseEdgeSegment>({
          id: "graph-edges-approx",
          data: approxSegs,
          getPath: (e: BaseEdgeSegment) => [e.source, e.target],
          getColor: (e: BaseEdgeSegment) => rgba(modeRgb(e.mode), alphaOf(e)),
          getWidth: (e: BaseEdgeSegment) => legWidthPx(zoom, e.capacity_share),
          widthUnits: "pixels",
          widthMinPixels: 0.4,
          widthMaxPixels: 3,
          getDashArray: [4, 4],
          dashJustified: true,
          extensions: REROUTE_EXT,
          pickable: false,
          updateTriggers: {
            getColor: [zoom, ctx.basemap, ctx.theme, ctx.fallbackMap],
            getWidth: [zoom, ctx.basemap],
          },
        } as never),
      );
    }
  }

  /* ---- Run arcs: materialised edges[], revealed in seq order.
   * Rendered verbatim from chain.edges — never re-derived.
   * Non-reroute arcs are glowing lines cause -> effect (the animated
   * comets are the ambient layer); reroute arcs are dashed green. ---- */
  const laneIdxArcs = laneIndexAt(ctx.lanes, zoom);
  const arcs = revealedArcData(chainEdges, { ...ctx, laneIndex: laneIdxArcs });
  layers.push(...buildChainArcLayers(arcs, ctx, ctx.pulseSeq));
  const rerouteArcs = chainEdges.filter(
    (e) => e.to_seq <= ctx.cascadeStep && e.from_node !== e.to_node && e.channel === "reroute",
  );
  if (rerouteArcs.length) {
    type RerouteDatum = { e: ChainEdge; path: LonLat[] };
    const paths: RerouteDatum[] = [];
    for (const e of rerouteArcs) {
      const a = nodeById.get(e.from_node);
      const b = nodeById.get(e.to_node);
      if (!a || !b) continue;
      for (const path of arcPathsFor(a, b, laneIdxArcs, REROUTE_PATH_STEPS).paths) paths.push({ e, path });
    }
    if (paths.length) {
      layers.push(
        new PathLayer<RerouteDatum>({
          id: "reroute-arcs",
          data: paths,
          getPath: (p: RerouteDatum) => p.path,
          getColor: rgba(REROUTE_ARC, 235),
          getWidth: 1.8,
          widthUnits: "pixels",
          dashJustified: true,
          getDashArray: [6, 5],
          extensions: REROUTE_EXT,
          pickable: false,
        } as never),
      );
      /* Largest reroute (by the target event's served delay), labelled
       * on the arc: `reroute via {bypass node} · +{delay}d`. */
      let best: { p: RerouteDatum; delay: number } | null = null;
      for (const p of paths) {
        const st = [...ctx.runStateByNode.values()].find((v) => v.seq === p.e.to_seq);
        const d = st?.delay_d ?? 0;
        if (!best || d > best.delay) best = { p, delay: d };
      }
      if (best) {
        const bp = best as { p: RerouteDatum; delay: number };
        const target = nodeById.get(bp.p.e.to_node);
        const mid = bp.p.path[Math.floor(bp.p.path.length / 2)];
        if (target && mid) {
          layers.push(
            new TextLayer({
              id: "reroute-annotation",
              data: [{ position: mid, text: `Reroute · ${target.name} · +${bp.delay.toFixed(1)} d` }],
              getPosition: (d: { position: LonLat }) => d.position,
              getText: (d: { text: string }) => d.text,
              getSize: 8.5,
              sizeUnits: "pixels",
              getColor: rgba(REROUTE_ARC, 255),
              /* off the arc's midpoint, where the two places' own labels sit */
              getTextAnchor: "start",
              getAlignmentBaseline: "top",
              getPixelOffset: [14, 10],
              background: true,
              getBackgroundColor: hexToRgba(t.badgeBg, 232),
              backgroundPadding: [4, 2, 4, 2],
              getBorderColor: rgba(REROUTE_ARC, 255),
              getBorderWidth: 1,
              fontFamily: MONO,
              fontWeight: 700,
              characterSet: LABEL_CHARSET,
              pickable: false,
              updateTriggers: { getColor: ctx.theme, getBackgroundColor: ctx.theme },
            } as never),
          );
        }
      }
    }
  }

  /* ---- PortWatch activity (SNAPSHOT): a soft glow per record ---- */
  if (ctx.showActivity && activity.length) {
    layers.push(buildActivityLayer(activity, ctx));
  }

  /* ---- Pulse halo on the latest revealed step ---- */
  if (includePulse) {
    const pulse = buildPulseLayer(ctx);
    if (pulse) layers.push(pulse);
  }

  /* ---- Places: glowing points, clusters, labels ---- */
  layers.push(...buildPlaceLayers(graph, ctx).layers);

  /* ---- Causal-chain order numbers at revealed nodes ---- */
  const badges: SeqBadge[] = [];
  ctx.runStateByNode.forEach((state, nodeId) => {
    if (state.seq <= ctx.cascadeStep) {
      const node = nodeById.get(nodeId);
      if (node) badges.push({ node, seq: state.seq, channel: state.channel });
    }
  });
  badges.sort((a, b) => a.seq - b.seq);
  layers.push(...buildSeqBadges(badges, ctx));

  return layers;
}

export function buildLayers(
  graph: GraphData,
  chainEdges: ChainEdge[],
  activity: PortActivityRecord[],
  ctx: LayerContext,
): Layer[] {
  return buildLayersInternal(graph, chainEdges, activity, ctx, true);
}

/** Every layer EXCEPT the pulse halo. These depend on the
 *  graph, chain, step and view state — never on pulsePhase — so the
 *  host can memoise them across pulse animation frames and rebuild
 *  only the pulse layer while the cascade plays. */
export function buildStaticLayers(
  graph: GraphData,
  chainEdges: ChainEdge[],
  activity: PortActivityRecord[],
  ctx: LayerContext,
): Layer[] {
  return buildLayersInternal(graph, chainEdges, activity, ctx, false);
}

/** Hover ring and name chip for one place, a separate layer set from the static stack. */
export function buildHoverLayers(graph: GraphData, ctx: PlaceCtx, hoverId: string | null): Layer[] {
  return buildPlaceHoverLayers(graph, ctx, hoverId);
}

export { buildAmbientLayers, revealedArcData } from "../places/placeLayers";
export type { ArcDatum };
export { quantizeZoom };
