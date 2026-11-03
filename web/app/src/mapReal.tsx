import React, { useCallback, useEffect, useMemo, useRef } from 'react';
// The map package, imported by relative path (../map/src from web/app).
import { SupermAPMap, useLiveEntities } from '../../map/src';
import type { ChainResponse, FocusGoodsLine, GraphData, PlacePaint, PortWatchSnapshot as MapPortWatchSnapshot, SupermAPMapHandle } from '../../map/src';
import type { AisFeedState } from '../../map/src/components/aisChip';
import { API_BASE } from './api';
import { prettyKind } from './format';
import { adapterFeedState, FEED_ADAPTERS } from './shell/feedState';
import { registerMapHandle } from './mapFlyTo';
import { themeForBasemap, useStore } from './store';
import { useLaneGeometry } from './laneGeometry';
import type { Chain, GraphData as AppGraphData } from './types';

/**
 * Goods-summary lines for the focus box, derived from the served
 * IMPACT payload for the selected place (per-class tonnes/day
 * figures, rendered as served — the map never computes them). The
 * payload serves either a per-class record or a single fallback
 * figure; a class ABSENT from the payload stays absent here.
 */
function goodsText(value: number, unit: string | null): string {
  const u = unit ?? '';
  /* The served unit often already carries the rate ("tonnes/day …");
   * append "/day" only when it does not. */
  const rated = /\/(day|d)\b|per day/i.test(u);
  return `${value.toLocaleString('en-US')} ${u}${rated ? '' : ' /day'}`.trim();
}

function focusGoodsFor(s: ReturnType<typeof useStore>, selectedNode: string | null): FocusGoodsLine[] | null {
  const g = s.impactNodeData;
  if (!selectedNode || !g || g.node_id !== selectedNode) return null;
  const tpd = g.goods.tonnes_per_day;
  if (!tpd) return null;
  if (typeof tpd === 'object' && !('value' in tpd)) {
    const rec = tpd as Record<string, { value: number | null; unit: string | null; class: string }>;
    const lines = Object.entries(rec)
      .filter(([, f]) => f.value !== null)
      .sort((a, b) => (b[1].value ?? 0) - (a[1].value ?? 0))
      .slice(0, 3)
      .map(([cls, f]) => ({
        label: prettyKind(cls),
        text: goodsText(f.value as number, f.unit),
        state: f.class,
      }));
    return lines.length ? lines : null;
  }
  const fig = tpd as { value: number | null; unit: string | null; class: string };
  if (fig.value === null) return null;
  return [{
    label: 'All goods',
    text: goodsText(fig.value, fig.unit),
    state: fig.class,
  }];
}

/**
 * Adapter: the app's panel props surface → the map package's SupermAPMapProps.
 * The graph arrives as a prop from the app layer (store): fetched live from
 * GET /graph at boot, with the bundled graph (src/seed.ts
 * loadGraphFallback, dynamically imported) as the labelled offline
 * fallback. This component does not import the graph.
 *
 * Simulation: when the store's sim slice is active, the run +
 * current-tick state + weather + persisted layers are passed through
 * as the map's `sim` input and the transport/layer callbacks wire back
 * to the store actions. The control surface is the mode switch in the
 * map bar, the SIM panel and the timeline at the map foot; the map's
 * own minimal sim transport is suppressed (simTransport={false}) so the timeline is
 * the one time control. With no sim active, `sim` is null and the map
 * renders exactly the pre-SIM LIVE/CANNED surface.
 */
export function MapReal({ chain, selectedNode, onSelect, graph, demoActive }:
  { chain: Chain | null; selectedNode: string | null; onSelect: (id: string, seq?: number) => void; graph: AppGraphData | null; demoActive?: boolean }) {
  const s = useStore();
  /* LIVE entities layer: polled only while the LIVE TWIN flag is on. Flag off =>
   * null => no request and no layer (the section must be absent, chip included). */
  const liveFeed = useLiveEntities(s.liveTwinEnabled ? API_BASE : null);
  /* LANE GEOMETRY: the legs and the chain arcs of sea places follow the lane polylines once they have loaded. */
  const laneGeo = useLaneGeometry(graph !== null);
  /* The AIS chip on the map reads the same health-board state as the map bar pill and the status bar. */
  const aisFs = adapterFeedState(s.health, FEED_ADAPTERS.ais, s.snapshotMode);
  const aisFeed = useMemo(() => ({ state: aisFs.word as AisFeedState, needsKey: aisFs.needsKey, calm: aisFs.calm }), [aisFs.word, aisFs.needsKey, aisFs.calm]);
  /* PLACE FOCUS goods: the focus box's goods block reads the IMPACT
   * payload for the selected place. Fetch it on selection (the IMPACT
   * panel fetches the same payload when it mounts; the store caches
   * by node id, so this is one request per place). */
  const impactFor = useRef<string | null>(null);
  useEffect(() => {
    if (!selectedNode) { impactFor.current = null; return; }
    if (s.impactNodeData?.node_id === selectedNode) { impactFor.current = selectedNode; return; }
    if (impactFor.current === selectedNode) return;
    impactFor.current = selectedNode;
    void s.fetchImpactNode(selectedNode);
    // fetchImpactNode is a stable store action; re-run on selection only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedNode]);
  const focusGoods = focusGoodsFor(s, selectedNode);
  /* Advanced search: publish the map's imperative handle
   * (flyToNode) to the fly-to bridge as the map mounts/unmounts, so a
   * search result's "View on map" can fly the camera through the one
   * existing code path. A callback ref, not an effect: the handle must
   * register as soon as the map appears. */
  const setMapHandle = useCallback((h: SupermAPMapHandle | null) => { registerMapHandle(h); }, []);
  /* The sim object is memoised over its real inputs. Built
   * inline it would be a NEW object on every render,
   * which invalidated every sim memo downstream in the map (index,
   * band view, resolved layers, and the lane-geometry memo) on every
   * app render — including renders the sim had nothing to do with. */
  const sim = useMemo(
    () =>
      s.simActive && s.simRun && s.simState
        ? {
            run: s.simRun,
            state: s.simState,
            weather: s.simWeather,
            layers: s.simLayers,
            playing: s.simPlaying,
            /* The host-owned vehicle selection — the map rings
             * the selected vehicle wherever it is rendered. */
            selectedVehicleId: s.simVehicleId,
          }
        : null,
    [s.simActive, s.simRun, s.simState, s.simWeather, s.simLayers, s.simPlaying, s.simVehicleId],
  );
  if (!graph) return <div className="map-loading mono" role="status">Loading places</div>;
  return (
    <div className="map-real">
      {/* The three `as unknown as` casts below are
        * LOAD-BEARING — the app and map contract types genuinely
        * drift, so typed conversions would lie:
        *  - graph: app GraphData has version OPTIONAL and nodes/edges
        *    as unknown[] (GET /graph serves them verbatim); the map's
        *    GraphData requires version: string + GraphNode[]/GraphEdge[].
        *  - chain: app CausalEvent/ChainEdge type channel as string;
        *    the map's types narrow it to the Channel union.
        *  - portwatch: app records type record_type as string, portname
        *    OPTIONAL, no per-record provenance, provenance.mode as
        *    string; the map's PortActivityRecord requires the
        *    "port_activity" literal, a portname, per-record Provenance
        *    and mode as the DataMode union. */}
      <SupermAPMap
        ref={setMapHandle}
        liveEntities={liveFeed}
        lanes={laneGeo.lanes}
        lanesNotServed={laneGeo.state === 'notserved'}
        aisFeed={aisFeed}
        graph={graph as unknown as GraphData}
        graphLoading={s.graphSource === 'loading'}
        chain={chain as unknown as ChainResponse}
        portwatch={s.portwatch as unknown as MapPortWatchSnapshot | null}
        theme={s.theme}
        selectedNodeId={selectedNode}
        selectNode={(id) => { if (id) onSelect(id); else s.selectNode(null); }}
        setTheme={(t) => {
          // The map's switcher reports basemap families ('dark'/'light');
          // land within the user's current family.
          if (t === 'dark' || t === 'light') s.setTheme(themeForBasemap(s.theme, t));
          else if (t === 'retro-dark' || t === 'retro-light') s.setTheme(t);
        }}
        basemapMode={s.basemap}
        onBasemapModeChange={(m) => s.setBasemap(m)}
        layerVis={s.layerVis}
        onLayerVisChange={(patch) => s.setLayerVis(patch)}
        placePaint={s.mapPaint as PlacePaint | null}
        /* The controlled cascade step is the app's
         * selected seq — selecting a chain step in any linked panel
         * reveals the cascade on the map up to exactly that step, and
         * the map's own transport reports its steps back through the
         * same selection bus (seq → its event's node), so the two
         * never drift. No selection = undefined = the map's built-in
         * transport owns the step, as before. */
        cascadeStep={s.selectedSeq ?? undefined}
        onCascadeStepChange={(seq) => {
          if (seq < 0) { s.clearSelectedSeq(); return; }
          const ev = chain?.events.find((e) => e.seq === seq);
          if (ev) onSelect(ev.node_id, seq);
        }}
        sim={sim}
        focusEnabled={!demoActive}
        placeCard={s.placeCardOpen}
        onOpenInspector={(id) => { s.selectNode(id); s.setLayout('Place Desk'); }}
        focusGoods={focusGoods}
        simTransport={false}
        onSimLayerToggle={(key, on) => s.setSimLayer(key, on)}
        onSimVehiclePick={(id) => s.selectSimVehicle(id)}
        onSimPlay={s.simPlay}
        onSimPause={s.simPause}
        onSimStep={(d) => s.simStep(d)}
        onSimSeek={(t) => s.simSeek(t)}
      />
    </div>
  );
}
