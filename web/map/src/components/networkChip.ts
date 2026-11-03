import type { GraphData } from "../types";

/**
 * The NETWORK footer chip's census text (SupermapMap's on-map
 * status strip). While the host's graph source is still
 * loading, the graph in hand is the empty boot graph — printing its
 * counts would state the false fact "0 places · 0 routes" mid-boot.
 * The loading state renders "—", never zeros: the same
 * law the app's mapbar and Map Content NETWORK row already follow,
 * threaded here through the host's graphSource signal.
 * Once loaded, the text is the plain census: counts plus
 * the core/shell split when the served network carries both tiers.
 */
export function networkChipText(graph: GraphData, loading?: boolean): string {
  if (loading) return "—";
  const base = `${graph.nodes.length.toLocaleString("en-US")} places · ${graph.edges.length.toLocaleString("en-US")} routes`;
  const tiers = graph.nodes.reduce(
    (a, n) => ({
      core: a.core + (n.tier === "core" ? 1 : 0),
      shell: a.shell + (n.tier === "shell" ? 1 : 0),
    }),
    { core: 0, shell: 0 },
  );
  return tiers.core > 0 && tiers.shell > 0
    ? `${base} · ${tiers.core.toLocaleString("en-US")} core · ${tiers.shell.toLocaleString("en-US")} shell`
    : base;
}
