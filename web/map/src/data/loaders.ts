import type {
  ChainResponse,
  GraphData,
  PortWatchSnapshot,
} from "../types";

/**
 * Data loading. API base is configurable via VITE_API_BASE
 * (default http://localhost:8000).
 *
 * IMPORTANT — graph loading: the API has NO GET /graph endpoint
 * (verified in server/api/app.py: only POST /run, POST /fork,
 * GET /chain/{run_id}, GET /scorecard, GET /health). So the graph is
 * supplied to <SupermapMap> as a prop by the host app, or fetched
 * from a configurable static URL via fetchGraph() — VITE_GRAPH_URL, e.g.
 * a /graph.json served next to the app, or the raw data/graph/graph.json
 * in a dev checkout.
 */

export const API_BASE: string =
  (import.meta.env?.VITE_API_BASE as string | undefined) ??
  "http://localhost:8000";

export const GRAPH_URL: string | undefined = import.meta.env
  ?.VITE_GRAPH_URL as string | undefined;

export async function fetchGraph(url: string = GRAPH_URL ?? ""): Promise<GraphData> {
  if (!url) {
    throw new Error(
      "Map data not configured. Set VITE_GRAPH_URL or pass the places and legs as a prop.",
    );
  }
  const res = await fetch(url);
  if (!res.ok) throw new Error(`fetchGraph: HTTP ${res.status} for ${url}`);
  return (await res.json()) as GraphData;
}

/** GET /chain/:run_id (events + materialised edges[]). */
export async function fetchChain(
  runId: string,
  apiBase: string = API_BASE,
): Promise<ChainResponse> {
  const res = await fetch(`${apiBase}/chain/${encodeURIComponent(runId)}`);
  if (!res.ok) throw new Error(`fetchChain: HTTP ${res.status} for run ${runId}`);
  return (await res.json()) as ChainResponse;
}

/** PortWatch snapshot — SNAPSHOT mode, never LIVE. Accepts either the
 *  raw snapshot file shape ({adapter, provenance, records[]}) and keeps
 *  only port_activity records for the dot layer. */
export async function fetchPortWatch(url: string): Promise<PortWatchSnapshot> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`fetchPortWatch: HTTP ${res.status} for ${url}`);
  const raw = (await res.json()) as PortWatchSnapshot & { records: unknown[] };
  return {
    adapter: raw.adapter ?? "portwatch",
    provenance: raw.provenance,
    records: (raw.records ?? []).filter(
      (r): r is PortWatchSnapshot["records"][number] =>
        typeof r === "object" &&
        r !== null &&
        (r as { record_type?: string }).record_type === "port_activity",
    ),
  };
}
