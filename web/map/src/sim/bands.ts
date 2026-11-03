// Zoom policy + display
// aggregation. Pure functions over the WHOLE-RUN state payload:
// aggregation is display-only and never changes which entities exist —
// the same payload renders at every band.

import { pointAtFraction, type LonLat } from "./geometry";
import { MAP_MAX_ZOOM, MAP_MAX_ZOOM_SAT } from "../places/tiers";
import type { SimEntity, SimLane, SimNodeState, SimState } from "./types";

export type ZoomBand = "WORLD" | "REGION" | "PORT" | "HARBOUR" | "SHIP";

/** The band as a person reads it (the ids above stay as the stable keys). */
export const BAND_LABEL: Readonly<Record<ZoomBand, string>> = {
  WORLD: "World",
  REGION: "Region",
  PORT: "Port",
  HARBOUR: "Harbour",
  SHIP: "Ship",
};

/** REGION rule: lanes whose concurrent entity count exceeds
 *  this stay aggregated; the threshold is a rendering rule. */
export const REGION_LANE_ENTITY_LIMIT = 40;

/** Bands in shallow-to-deep order — the single source for depth
 *  comparisons (bandAtLeast) so gates never re-derive the ordering. */
export const BAND_ORDER: readonly ZoomBand[] = [
  "WORLD",
  "REGION",
  "PORT",
  "HARBOUR",
  "SHIP",
];

/** True when `band` is at least as deep as `min` in BAND_ORDER. */
export function bandAtLeast(band: ZoomBand, min: ZoomBand): boolean {
  return BAND_ORDER.indexOf(band) >= BAND_ORDER.indexOf(min);
}

/** Band boundaries (extended): WORLD z<3, REGION
 *  3<=z<6, PORT 6<=z<10, HARBOUR 10<=z<12, SHIP z>=12. */
export function zoomBand(zoom: number): ZoomBand {
  if (zoom < 3) return "WORLD";
  if (zoom < 6) return "REGION";
  if (zoom < 10) return "PORT";
  if (zoom < 12) return "HARBOUR";
  return "SHIP";
}

/** maxZoom policy: 18 on every basemap. CARTO vector
 *  tiles over-zoom cleanly past their native z16; imagery's
 *  native depth is z14 (EOX) or z9 (NASA GIBS); MapLibre over-zooms beyond. */
export function maxZoomForBasemap(basemap: "dark" | "light" | "sat"): number {
  return basemap === "sat" ? MAP_MAX_ZOOM_SAT : MAP_MAX_ZOOM;
}

export interface LaneChip {
  lane: string;
  mode: "sea" | "air";
  /** entities on this lane in the current state (all states) */
  count: number;
  /** EN_ROUTE subset — the moving density the chip summarises */
  enRoute: number;
  position: LonLat;
}

export interface LaneAggregate {
  lane: string;
  mode: "sea" | "air";
  count: number;
  position: LonLat;
  entities: SimEntity[];
}

export interface BandView {
  band: ZoomBand;
  /** entities rendered individually at this band */
  individual: SimEntity[];
  /** WORLD: per-lane moving count chips at lane midpoints */
  chips: LaneChip[];
  /** REGION: lanes over the limit, collapsed to one marker each */
  aggregated: LaneAggregate[];
}

const enRouteByLane = (state: SimState): Map<string, SimEntity[]> => {
  const m = new Map<string, SimEntity[]>();
  for (const e of state.entities) {
    const arr = m.get(e.lane);
    if (arr) arr.push(e);
    else m.set(e.lane, [e]);
  }
  return m;
};

const laneMidpoint = (lane: SimLane, lonLatPath: LonLat[]): LonLat =>
  lane.midpoint ? [lane.midpoint[1], lane.midpoint[0]] : pointAtFraction(lonLatPath, 0.5);

/**
 * Project a state payload into a band's display model.
 * `lonLatPaths` maps lane key -> rendered path in [lon, lat] (converted
 * once by the caller via geometry.laneSubpaths' unsplit source path —
 * pass the full unsplit polyline; chips sit at its midpoint).
 *
 * - WORLD: no individuals; one chip per lane that has entities.
 * - REGION: lanes with <= REGION_LANE_ENTITY_LIMIT entities render
 *   individually; denser lanes collapse to one aggregate marker.
 * - PORT / HARBOUR / SHIP: every entity individual; no chips, no
 *   aggregates.
 * Entities without a served position (lon/lat null — never happens for
 * EN_ROUTE in the current server, but the type allows it) are excluded
 * from individuals and still counted in chips/aggregates.
 */
export function bandView(
  state: SimState,
  lanesByKey: Map<string, SimLane>,
  lonLatPaths: Map<string, LonLat[]>,
  zoom: number,
): BandView {
  const band = zoomBand(zoom);
  const byLane = enRouteByLane(state);
  if (bandAtLeast(band, "PORT")) {
    return {
      band,
      individual: state.entities.filter((e) => e.lon !== null && e.lat !== null),
      chips: [],
      aggregated: [],
    };
  }
  if (band === "WORLD") {
    const chips: LaneChip[] = [];
    for (const [key, ents] of byLane) {
      const lane = lanesByKey.get(key);
      const path = lonLatPaths.get(key);
      if (!lane || !path || !path.length) continue;
      chips.push({
        lane: key,
        mode: lane.mode === "air" ? "air" : "sea",
        count: ents.length,
        enRoute: ents.filter((e) => e.state === "EN_ROUTE").length,
        position: laneMidpoint(lane, path),
      });
    }
    chips.sort((a, b) => a.lane.localeCompare(b.lane));
    return { band, individual: [], chips, aggregated: [] };
  }
  // REGION
  const individual: SimEntity[] = [];
  const aggregated: LaneAggregate[] = [];
  for (const [key, ents] of byLane) {
    if (ents.length <= REGION_LANE_ENTITY_LIMIT) {
      for (const e of ents) {
        if (e.lon !== null && e.lat !== null) individual.push(e);
      }
    } else {
      const lane = lanesByKey.get(key);
      const path = lonLatPaths.get(key);
      if (!lane || !path || !path.length) continue;
      aggregated.push({
        lane: key,
        mode: lane.mode === "air" ? "air" : "sea",
        count: ents.length,
        position: laneMidpoint(lane, path),
        entities: ents,
      });
    }
  }
  aggregated.sort((a, b) => a.lane.localeCompare(b.lane));
  return { band, individual, chips: [], aggregated };
}

/** Per-lane EN_ROUTE counts from the state payload — the FLOWS density
 *  source (density from the run's flow data, never invented). */
export function laneFlowDensity(state: SimState): Map<string, number> {
  const m = new Map<string, number>();
  for (const e of state.entities) {
    if (e.state === "EN_ROUTE") m.set(e.lane, (m.get(e.lane) ?? 0) + 1);
  }
  return m;
}

/** Queue rings for the PORT band and deeper: nodes with queue > 0. */
export interface QueueRing {
  nodeId: string;
  queue: number;
  berthed: number;
}

export function queueRings(state: SimState): QueueRing[] {
  return state.nodes
    .filter((n: SimNodeState) => n.queue > 0)
    .map((n) => ({ nodeId: n.id, queue: n.queue, berthed: n.berthed }))
    .sort((a, b) => a.nodeId.localeCompare(b.nodeId));
}
