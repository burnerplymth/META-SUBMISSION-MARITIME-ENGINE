import { useMemo, useRef, useState } from "react";
import { SupermAPMap, type SupermAPMapHandle } from "./components/SupermapMap";
import type {
  BasemapStatus,
  ChainResponse,
  GraphData,
  PortWatchSnapshot,
  ThemeName,
} from "./types";
// Real graph (read-only build-time import; the API has no GET /graph).
// eslint-disable-next-line @typescript-eslint/no-restricted-imports
import graphJson from "../../../data/graph/graph.json";
// Real engine output: Suez closure (chk_suez, 7d) run against the real graph
// via the API — events + materialised edges.
import demoChain from "./fixtures/demo-chain.json";
// Trimmed copy of the PortWatch snapshot (port_activity records only).
import portwatchJson from "./fixtures/portwatch-activity.json";
import "./harness.css";

const graph = graphJson as unknown as GraphData;
const chain = demoChain as unknown as ChainResponse;
const portwatch = portwatchJson as unknown as PortWatchSnapshot;

/**
 * Standalone page for the map module: mounts the map with the real
 * 992-node / 4,550-edge graph, the PortWatch SNAPSHOT activity layer and a
 * real Suez chain so it can be verified without the web app.
 */
export function Harness() {
  const q = new URLSearchParams(typeof location === "undefined" ? "" : location.search);
  const [theme, setTheme] = useState<ThemeName>((q.get("theme") as ThemeName) || "dark");
  const [withChain, setWithChain] = useState(q.get("chain") !== "0");
  const mapRef = useRef<SupermAPMapHandle>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [lastAction, setLastAction] = useState("—");
  const [basemapStatus, setBasemapStatus] = useState<BasemapStatus>("ok");

  const counts = useMemo(
    () => ({
      chokepoints: graph.nodes.filter((n) => n.kind === "chokepoint").length,
      ports: graph.nodes.filter((n) => n.kind === "port").length,
    }),
    [],
  );

  return (
    <div className="harness" data-theme={theme}>
      <header className="harness-bar">
        <span className="harness-logo">SUPERMAP</span>
        <span className="harness-sub">MAP MODULE · STANDALONE · SM 001</span>
        <span className="harness-stats">
          {graph.nodes.length} NODES ({counts.ports} PORTS · {counts.chokepoints}{" "}
          CHOKEPOINTS) · {graph.edges.length} EDGES · CHAIN{" "}
          {chain.event_count} EVENTS
        </span>
        <label className="harness-toggle">
          <input
            type="checkbox"
            checked={withChain}
            onChange={(e) => setWithChain(e.target.checked)}
          />
          SUEZ CHAIN
        </label>
        <span className="harness-action">LAST ACTION: {lastAction}</span>
        <span className="harness-action">
          BASEMAP STATUS (onBasemapStatus): {basemapStatus}
        </span>
        <span className="harness-selected">
          SELECTED: {selected ?? "—"}
        </span>
      </header>
      <main className="harness-map">
        <SupermAPMap
          ref={mapRef}
          graph={graph}
          chain={withChain ? chain : null}
          portwatch={portwatch}
          theme={theme}
          autoPlayCascade={false}
          selectNode={(id) => {
            setSelected(id);
            setLastAction(`selectNode(${id ?? "null"})`);
          }}
          setTheme={(t) => {
            setTheme(t);
            setLastAction(`setTheme(${t})`);
          }}
          onBasemapStatus={(s) => {
            setBasemapStatus(s);
            setLastAction(`onBasemapStatus(${s})`);
          }}
        />
      </main>
      <footer className="harness-foot">
        DEMO CHAIN = REAL ENGINE RUN {chain.run_id} · HASH{" "}
        {chain.stream_hash.slice(0, 12)}… · GRAPH LOADED VIA BUILD-TIME IMPORT
        PORTWATCH = SNAPSHOT{" "}
        {portwatch.provenance.retrieved_at.slice(0, 10)} · AIS LIVE = NEEDS-KEY
      </footer>
    </div>
  );
}
