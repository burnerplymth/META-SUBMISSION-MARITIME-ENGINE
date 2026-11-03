// SupermAP contract types: graph, CausalEvent, chain response,
// provenance, actions.
// These mirror the served shapes exactly — do not improvise fields.

export type Mode = "sea" | "air" | "ground";
export type NodeKind = "port" | "chokepoint" | "air_hub" | "ground_node";
export type Channel =
  | "queue"
  | "congestion"
  | "reroute"
  | "mode-shift"
  | "inventory"
  | "cost";
export type DataMode = "LIVE" | "SNAPSHOT" | "MODELED";
export type ThemeName = "dark" | "light" | "retro-dark" | "retro-light";
export type BasemapMode = "dark" | "light" | "sat";
/** Basemap reachability signal: "ok" while the
 *  MapLibre style + geography tiles load; "unreachable" when the style
 *  fails to load or its tiles error — the deck.gl lanes/graph stay live,
 *  only the geography tiles are missing. This signal reports TILE status
 *  only: the bundled Natural Earth coastline underlay (theme/underlay.ts)
 *  that shows beneath failed tiles is derived FROM this signal and never
 *  alters it, and is never presented as the live basemap. */
export type BasemapStatus = "ok" | "unreachable";

/** Provenance envelope (shared envelope): no datum without a source renders. */
export interface Provenance {
  source: string;
  retrieved_at: string;
  mode: DataMode;
}

/** Node */
export interface GraphNode {
  id: string;
  name: string;
  mode: Mode;
  kind: NodeKind;
  /** Two-tier network (additive, served on the current graph):
   *  "core" = calibration-grade, "shell" = routing-grade. Absent on
   *  pre-expansion graphs, where every place reads as core. */
  tier?: string;
  /** Country as served verbatim on the current graph (980 of 992
   *  places; the 12 chokepoints serve none). Additive — absent on
   *  pre-expansion graphs. */
  country?: string;
  lat: number;
  lon: number;
  capacity: number;
  capacity_unit: string;
  queue_cap: number;
  inventory_days: number;
  sources: string[];
  aliases?: string[];
  hit_threshold?: number;
}

/** Edge. Key = `${from}->${to}:${mode}` (no separate id). */
export interface GraphEdge {
  from: string;
  to: string;
  mode: Mode;
  base_transit_d: number;
  capacity_share: number;
  bypass_of?: string;
  calibration: { volume_source: string; efficiency_source?: string };
}

export interface GraphData {
  version: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/** CausalEvent. The DAG is the unit of truth; seq is gapless 0..n-1. */
export interface CausalEvent {
  run_id: string;
  seq: number;
  node_id: string;
  parent_id: number | null;
  channel: Channel;
  mechanism: string;
  delay_d: number;
  magnitude: number;
  at: string;
  tick?: number;
}

/** ChainEdge — materialised by the API from parent_id links.
 *  The map renders arcs from these verbatim; it never re-derives them. */
export interface ChainEdge {
  from_seq: number | null;
  to_seq: number;
  from_node: string;
  to_node: string;
  channel: Channel;
}

/** GET /chain/:run_id response */
export interface ChainResponse {
  run_id: string;
  status: string;
  event_count: number;
  stream_hash: string;
  events: CausalEvent[];
  edges: ChainEdge[];
}

/** PortWatch snapshot record (port_activity subset used by the dot layer). */
export interface PortActivityRecord {
  record_type: "port_activity";
  node_id: string;
  portname: string;
  vessel_count_total: number;
  vessel_count_container?: number;
  vessel_count_dry_bulk?: number;
  vessel_count_general_cargo?: number;
  vessel_count_tanker?: number;
  provenance: Provenance;
}

export interface PortWatchSnapshot {
  adapter: string;
  provenance: Provenance;
  records: PortActivityRecord[];
}

/** Per-node run state derived from an event (for inspector + styling). */
export interface NodeRunState {
  seq: number;
  channel: Channel;
  mechanism: string;
  delay_d: number;
  magnitude: number;
  at: string;
}

/** Host callbacks owned by the map. */
export interface SupermAPMapCallbacks {
  /** selectNode(id) */
  selectNode?: (id: string | null) => void;
  /** setTheme(dark|light) — fired by the basemap switcher for dark/light.
   *  SAT is a basemap mode, not a theme; it does not fire setTheme. */
  setTheme?: (theme: ThemeName) => void;
}
