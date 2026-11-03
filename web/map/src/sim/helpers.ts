// Run-detail indexing helpers. Pure:
// turn a GET /sim/run/{id} detail + the graph into the lookup
// structures the layer builders consume, converting lane paths exactly
// once ([lat, lon] served -> [lon, lat] deck order + antimeridian
// split). No positions are computed here — entity positions only ever
// come from /sim/state.

import { laneSubpaths, type LonLat } from "./geometry";
import type { GraphData } from "../types";
import type { SimLane, SimRunDetail, SimState } from "./types";

export interface SimIndex {
  lanesByKey: Map<string, SimLane>;
  /** lane key -> full unsplit rendered path, [lon, lat] */
  lonLatPaths: Map<string, LonLat[]>;
  /** lane key -> antimeridian-split subpaths, [lon, lat] */
  subpathsByLane: Map<string, LonLat[][]>;
  /** node id -> [lon, lat] from the graph */
  nodePos: Map<string, LonLat>;
  /** place id -> served tier ("core" | "shell" | undefined) from the
   *  graph payload — the base map's own tier source. The sim payloads
   *  carry no tier of their own today, so tier weighting
   *  resolves element tiers from this map (phaseD.laneTierOf etc.);
   *  a tier served on an element itself still wins there. */
  nodeTier: Map<string, string | undefined>;
}

export function buildSimIndex(run: SimRunDetail, graph: GraphData): SimIndex {
  const lanesByKey = new Map<string, SimLane>();
  const lonLatPaths = new Map<string, LonLat[]>();
  const subpathsByLane = new Map<string, LonLat[][]>();
  for (const lane of run.derivation.lanes) {
    lanesByKey.set(lane.key, lane);
    lonLatPaths.set(
      lane.key,
      lane.path.map(([lat, lon]) => [lon, lat] as LonLat),
    );
    subpathsByLane.set(lane.key, laneSubpaths(lane.path));
  }
  const nodePos = new Map<string, LonLat>();
  const nodeTier = new Map<string, string | undefined>();
  for (const n of graph.nodes) {
    nodePos.set(n.id, [n.lon, n.lat]);
    nodeTier.set(n.id, (n as { tier?: string }).tier);
  }
  return { lanesByKey, lonLatPaths, subpathsByLane, nodePos, nodeTier };
}

/** state.disruptions_active carries journal sim_ids ("d1", …); the map
 *  needs node ids — resolved through the run's journal, the only place
 *  the sim_id -> node binding is served. */
export function activeDisruptionNodeIds(
  run: SimRunDetail,
  state: SimState,
): Set<string> {
  const bySimId = new Map(
    run.journal.disruptions.map((d) => [d.sim_id, d.node_id]),
  );
  const out = new Set<string>();
  for (const simId of state.disruptions_active) {
    const node = bySimId.get(simId);
    if (node) out.add(node);
  }
  return out;
}
