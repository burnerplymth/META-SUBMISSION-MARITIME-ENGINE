import type { CausalEvent, GraphData } from "../types";

/**
 * The map's text alternative and its keyboard stepping. Pure: no browser needed.
 *
 * The canvas is a picture of places and legs; a screen reader gets the same facts as short text: how many places
 * and legs, which place is selected, and where the playhead is on the causal chain. The text names states and
 * counts only (no prose), in the house vocabulary: places, legs, causal chain.
 */

export interface MapSummaryInput {
  graph: GraphData;
  /** The host's graph is still loading: counts are not stated (an empty boot graph is not a fact). */
  loading?: boolean;
  basemapLabel: string;
  selectedName?: string | null;
  /** Events in the loaded causal chain (0 or absent = none). */
  chainEvents?: number;
  /** Highest revealed seq, -1 before the first step. */
  chainStep?: number;
}

const n = (v: number): string => v.toLocaleString("en-US");

export function mapSummary(i: MapSummaryInput): string {
  const parts: string[] = [`${i.basemapLabel} basemap`];
  parts.push(i.loading ? "Places loading" : `${n(i.graph.nodes.length)} places, ${n(i.graph.edges.length)} legs`);
  if (i.selectedName) parts.push(`Selected place ${i.selectedName}`);
  const total = i.chainEvents ?? 0;
  if (total > 0) {
    const shown = Math.max(0, Math.min(total, (i.chainStep ?? -1) + 1));
    parts.push(`Causal chain ${n(total)} steps, ${n(shown)} shown`);
  }
  return `${parts.join(". ")}.`;
}

/** The places of a causal chain in step order, each once. */
export function chainPlaceOrder(events: readonly CausalEvent[] | undefined): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const e of [...(events ?? [])].sort((a, b) => a.seq - b.seq)) {
    if (!seen.has(e.node_id)) { seen.add(e.node_id); out.push(e.node_id); }
  }
  return out;
}

/** The place a step key moves to. From no selection (or a place off the chain) forward lands on the first place
 *  and back on the last; the ends do not wrap, so a held key stops at the end of the causal chain. */
export function stepPlace(order: readonly string[], current: string | null | undefined, dir: 1 | -1): string | null {
  if (!order.length) return null;
  const at = current ? order.indexOf(current) : -1;
  if (at < 0) return dir === 1 ? order[0] : order[order.length - 1];
  return order[Math.max(0, Math.min(order.length - 1, at + dir))];
}

/** Which key moves the selection: `]` next place on the causal chain, `[` previous. */
export function stepKeyDir(e: { key: string; altKey?: boolean; ctrlKey?: boolean; metaKey?: boolean }): 1 | -1 | 0 {
  if (e.altKey || e.ctrlKey || e.metaKey) return 0;
  return e.key === "]" ? 1 : e.key === "[" ? -1 : 0;
}
