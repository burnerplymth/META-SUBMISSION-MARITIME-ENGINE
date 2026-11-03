import demoChainJson from './fixtures/demo-chain.json';
import type { Citation } from './citation';
import type { Chain, DisruptionSpec, FeedEvent, GraphData } from './types';

// ── OFFLINE FALLBACKS ONLY ─────────────────────────────────────────────────
// The live data comes from the API at runtime (GET /events, GET /graph;
// fetched in src/api.ts, held in src/store.tsx). The
// bundled copies below are used ONLY when those fetches fail, and the UI
// labels them with the *_LABEL chips wherever they surface, so a stale copy
// can never masquerade as live data.

// Disruption feed fallback: verbatim copy of data/events.jsonl
// (3 real NOAA/NWS events, retrieved 2026-10-21T00:35:56Z).
export const FEED_FALLBACK_LABEL = 'Offline copy · bundled 2026-10-20';
/* The three bundled alerts were issued by NWS Charleston SC (their headlines
 * end "by NWS Charleston SC"); the stored copy is not live, so the citation is
 * the office's own page rather than a current-alerts listing. */
const CHS_OFFICE_CITATION: Citation = {
  publisher: 'NWS Charleston SC', title: 'Small Craft Advisory',
  url: 'https://www.weather.gov/chs/', domain: 'weather.gov', kind: 'ALERT', published_at: '2026-10-21T00:27:00Z',
};
export const FEED_FALLBACK: FeedEvent[] = [
  { id: 'evt_20261020_sea_savannah_weather_200da99e', node_id: 'sea_savannah', kind: 'weather', severity: 2, status: 'active',
    started_at: '2026-10-20T20:27:00Z', detected_at: '2026-10-21T00:35:56Z', source: 'NOAA/NWS', mode: 'LIVE',
    headline: 'Small Craft Advisory — coastal waters from Savannah GA to Altamaha Sound GA out 20 nm, incl. Grays Reef NMS. Until Oct 21 21:00 EDT.',
    evidence_url: 'https://api.weather.gov/alerts/urn:oid:2.49.0.1.840.0.b54a4b14fb319569ebdc518c6778e70a800df02a.003.1',
    citation: CHS_OFFICE_CITATION },
  { id: 'evt_20261020_sea_savannah_weather_8ca6f0a1', node_id: 'sea_savannah', kind: 'weather', severity: 2, status: 'active',
    started_at: '2026-10-20T20:27:00Z', detected_at: '2026-10-21T00:35:56Z', source: 'NOAA/NWS', mode: 'LIVE',
    headline: 'Small Craft Advisory — coastal waters from Edisto Beach SC to Savannah GA out 20 NM. Until Oct 21 21:00 EDT.',
    evidence_url: 'https://api.weather.gov/alerts/urn:oid:2.49.0.1.840.0.b54a4b14fb319569ebdc518c6778e70a800df02a.002.1',
    citation: CHS_OFFICE_CITATION },
  { id: 'evt_20261020_sea_savannah_weather_026528c5', node_id: 'sea_savannah', kind: 'weather', severity: 2, status: 'active',
    started_at: '2026-10-20T20:27:00Z', detected_at: '2026-10-21T00:35:56Z', source: 'NOAA/NWS', mode: 'LIVE',
    headline: 'Small Craft Advisory — waters from Savannah GA to Altamaha Sound GA, 20–60 NM offshore. Until Oct 25 08:00 EDT.',
    evidence_url: 'https://api.weather.gov/alerts/urn:oid:2.49.0.1.840.0.b54a4b14fb319569ebdc518c6778e70a800df02a.004.1',
    citation: CHS_OFFICE_CITATION },
];

// Network fallback: the calibrated network (data/graph/graph.json —
// 992 places / 4,550 routes: 200 CORE + 792 SHELL), bundled read-only. The live service serves
// this same file.
export const GRAPH_FALLBACK_LABEL = 'Offline copy · bundled 2026-10-21';
/* The bundled graph is loaded through a DYNAMIC import so
 * the 5.4 MB graph JSON lands in its own chunk instead of the app's
 * main chunk (main was 7,052 kB with it inlined). No cast needed on
 * the result — the app's GraphData types nodes/edges as unknown[]
 * precisely because GET /graph serves them verbatim, so the bundled
 * JSON (the same file the service serves) assigns directly. The
 * result is cached: the fallback is fetched at most once per session.
 * Callers: store.tsx boot (initial state starts EMPTY with
 * graphSource 'loading' — the fallback is only ever applied, labelled,
 * once this resolves or the live fetch fails). */
let graphFallbackCache: GraphData | null = null;
export async function loadGraphFallback(): Promise<GraphData> {
  if (graphFallbackCache) return graphFallbackCache;
  const mod = await import('../../../data/graph/graph.json');
  graphFallbackCache = mod.default;
  return graphFallbackCache;
}

// CANNED fallback: labelled on screen wherever used. Spec mirrors
// the engine fixture's Suez closure shape, on the real graph node id.
export const CANNED_SPEC: DisruptionSpec = {
  node_id: 'chk_suez', kind: 'closure', capacity_loss: 1.0, duration_d: 14, shape: 'step',
  started_at: '2023-03-23T00:00:00Z',
};
export const CANNED_LABEL = 'RECORDED — Suez closure replay, 14 d';

// Bundled chain fixture — OFFLINE BOOT FALLBACK. Provenance: REAL
// engine output, the map package's demo fixture
// (web/map/src/fixtures/demo-chain.json, generated via the API
// against the real graph: Suez closure run run_fcc6087c07f34899, 10 chain
// events + materialised edges, stream_hash 66b7a965…). Copied verbatim into
// src/fixtures/demo-chain.json (byte-identical, sha256 44064b22…) so the
// app boot path does not depend on the live API for a CANNED replay:
// when the live run AND the API-executed CANNED spec both fail (API down),
// boot loads this chain labelled CANNED · OFFLINE COPY (store.tsx). It is a
// recorded run, never presented as a fresh live computation.
export const CHAIN_FALLBACK: Chain = demoChainJson;
export const CHAIN_FALLBACK_LABEL = 'RECORDED — Suez closure replay, offline copy';

export const NODE_NAMES: Record<string, string> = {
  chk_suez: 'Suez Canal', chk_babelmandeb: 'Bab el-Mandeb', chk_panama: 'Panama Canal',
  chk_hormuz: 'Strait of Hormuz', chk_malacca: 'Strait of Malacca', chk_taiwan: 'Taiwan Strait',
  chk_capeofgoodhope: 'Cape of Good Hope', chk_danish: 'Danish Straits', chk_dover: 'Dover Strait',
  chk_gibraltar: 'Strait of Gibraltar', chk_mozambique: 'Mozambique Channel', chk_turkish: 'Turkish Straits',
  sea_savannah: 'Savannah', sea_rotterdam: 'Rotterdam', sea_losangeles: 'Los Angeles',
  sea_longbeach: 'Long Beach', sea_newyork: 'New York', sea_singapore: 'Singapore',
  sea_shanghai: 'Shanghai', sea_houston: 'Houston', sea_jebelali: 'Jebel Ali',
  sea_piraeus: 'Piraeus', sea_antwerp: 'Antwerp', sea_hamburg: 'Hamburg', sea_busan: 'Busan',
  gnd_inlandempire: 'Inland Empire', gnd_chicago: 'Chicago', gnd_dallas: 'Dallas',
  air_losangeles: 'Los Angeles Intl', sea_colon: 'Colón', sea_portsaid: 'Port Said',
  sea_jeddah: 'Jeddah', sea_salalah: 'Salalah', sea_colombo: 'Colombo', sea_mundra: 'Mundra',
  sea_portklang: 'Port Klang', sea_tanjungpelepas: 'Tanjung Pelepas', gnd_dadri: 'Dadri', gnd_riyadh: 'Riyadh',
};
/** Last-resort display form for an id with no curated or served name:
 *  strip the class prefix and title-case the remainder (sea_ningbo →
 *  "Ningbo"). The raw id itself never reaches the screen. */
export const prettyNodeId = (id: string): string => {
  const bare = String(id).replace(/^(sea|air|gnd|chk)_/, '').replace(/_/g, ' ').trim();
  return bare ? bare.replace(/\b\w/g, (c) => c.toUpperCase()) : String(id);
};
export const nodeName = (id: string) => NODE_NAMES[id] ?? prettyNodeId(id);
export const KINDS = ['closure', 'capacity_cut', 'congestion', 'strike', 'weather', 'grounding', 'accident', 'policy', 'other'];
export const SHAPES: Array<DisruptionSpec['shape']> = ['step', 'ramp', 'pulse'];
