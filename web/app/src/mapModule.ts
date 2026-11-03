// ── MAP MODULE INDIRECTION (single swap point) ──────────────────────────
// The map view is src/mapReal.tsx, an adapter to the map module's documented
// SupermAPMapProps (importing ../../map/src). It takes { chain, selectedNode,
// onSelect, graph }; the network is fetched by the app layer (store: GET
// /graph with the bundled seed.ts fallback) and passed down as a prop.
export { MapReal as MapView } from './mapReal';
