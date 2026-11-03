# SupermAP map

Self-contained MapLibre GL and deck.gl map module for SupermAP. The web app (`web/app/`) imports its source
(`web/app/src/mapReal.tsx` imports `../../map/src`), so install the dependencies of both folders. On screen the map
speaks of places and legs; `node` and `edge` are the data-contract field names (`node_id`, `edges[]`).

```tsx
import { SupermapMap, fetchChain } from "../map/src";
```

The package declares its runtime dependencies in `package.json` (`maplibre-gl`, `@deck.gl/*`, `react`). The root
component imports its own styles, so it only needs a sized container.

## Standalone page

```bash
cd web/map
npm ci
npm run build     # tsc --noEmit && vite build
npm run dev       # serve the standalone page
```

The standalone page (`src/harness.tsx`, `index.html`) mounts the map with the shipped network
(`data/graph/graph.json`), the PortWatch snapshot activity layer and a recorded causal chain.

## Props

```tsx
<SupermapMap
  graph={graphData}              // required: {version, nodes[], edges[]} (the shipped network)
  chain={chainResponse | null}   // causal chain response {events, edges[]}
  portwatch={snapshot | null}    // PortWatch snapshot ({provenance, records}) for the activity dots
  theme={theme}                  // optional: "dark" | "light"
  selectedNodeId={id | null}     // optional controlled selection
  selectNode={(id) => ...}       // called on click and on deselect
/>
```

See `src/index.ts` for the full exported surface. A bundled coastline (Natural Earth, public domain) is drawn under
the basemap when tiles cannot be reached; its provenance is in `src/data/README-land-underlay.md`.
