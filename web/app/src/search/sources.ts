/**
 * ADVANCED SEARCH — sources adapter. Maps the
 * store's already-loaded state slices onto the engine's
 * SearchSources. Pure: no fetching, no store import at runtime — the
 * index spans exactly what the app already holds (the served world
 * payload, the disruption feed, the money layer's exposures document,
 * Ledger entries, satellite scans, commodity lenses, and the one run
 * currently loaded). A slice that has not loaded
 * contributes no rows; nothing is derived that the service did not
 * serve, and offline/snapshot labelling stays with the panels that
 * own those datasets.
 *
 * Caller-built titles follow the product's own idiom: a Ledger entry
 * is titled by its disruption (the feed headline when the feed holds
 * it, else "Disruption at <place>"), a run by the shared run
 * reference (place + day). If the app cannot name a record in words
 * it is still indexed only under a words-title — never under an id.
 */
import { humaniseIds, nodeLabel, runRef } from '../format';
import type {
  Chain, EnsExplorationEntry, FeedEvent, GoodsClass, GraphData, LedgerEntry, Lens,
  MoneyExposuresDoc, ReplaySummary, RunCatalogueEntry, SatScan, Watchlist,
} from '../types';
import type { RunMeta } from '../store';
import type { PlaceInput, SearchSources } from './engine';

export interface SearchStateSlices {
  graph: GraphData;
  feed: FeedEvent[];
  ledger: LedgerEntry[] | null;
  moneyExposures: MoneyExposuresDoc | null;
  lenses: Lens[] | null;
  satScans: SatScan[] | null;
  chain: Chain | null;
  runMeta: RunMeta | null;
  /** Slices (boot-fetched; null until loaded, absent = none). */
  runsCatalogue?: RunCatalogueEntry[] | null;
  explorations?: EnsExplorationEntry[] | null;
  watchlists?: Watchlist[] | null;
  goodsClasses?: GoodsClass[] | null;
  replays?: ReplaySummary[] | null;
}

export function buildSearchSources(st: SearchStateSlices): SearchSources {
  const headlineByDisruption = new Map(st.feed.map((f) => [f.id, humaniseIds(f.headline, st.graph)]));
  return {
    // The served world payload's records, as-is (same shape as PlaceInput).
    places: st.graph.nodes as unknown as PlaceInput[],
    events: st.feed.map((f) => ({
      id: f.id,
      headline: humaniseIds(f.headline, st.graph),
      placeId: f.node_id,
      kind: f.kind,
      source: f.source,
      severity: f.severity,
      status: f.status,
      detectedAt: f.detected_at,
    })),
    companies: (st.moneyExposures?.mappings ?? []).map((m) => ({
      ticker: m.ticker,
      company: m.company,
      placeId: m.node_id,
      exposureKind: m.exposure_kind,
      filingForm: m.filing?.form,
      filedDate: m.filing?.filed_date,
    })),
    ledger: (st.ledger ?? []).map((e) => ({
      entryId: e.entry_id,
      title: headlineByDisruption.get(e.disruption_id)
        ?? (e.predicted_chain.length
          ? `Disruption at ${nodeLabel(e.predicted_chain[0].node_id, st.graph)}`
          : 'Locked chain'),
      lockedAt: e.locked_at,
      status: e.grade ? 'graded' : 'open',
      placeId: e.predicted_chain[0]?.node_id,
      stepCount: e.predicted_chain.length,
    })),
    // Session-known runs: the run currently loaded, under its human
    // reference — SUPPRESSED when the served catalogue already names
    // the same run, so a loaded run appears exactly once (via the
    // catalogue row, under the server's title for it).
    runs: st.chain && !(st.runsCatalogue ?? []).some((r) => r.id === st.chain!.run_id)
      ? [{ runId: st.chain.run_id, title: runRef(st.chain, st.graph), subtitle: st.runMeta?.label ?? 'Stored run' }]
      : [],
    // Run catalogue (GET /runs): every stored run the server names,
    // title verbatim; the id rides along as the action payload and
    // the engine's matching-only haystack, never as display text.
    catalogueRuns: (st.runsCatalogue ?? []).map((r) => ({
      runId: r.id, kind: r.kind, title: r.title,
      createdAt: r.created_at, status: r.status, facts: r.facts,
    })),
    explorations: (st.explorations ?? []).map((x) => ({
      id: x.id, name: x.name, members: x.members, expansions: x.expansions, updatedAt: x.updated_at,
    })),
    watchlists: (st.watchlists ?? []).map((w) => ({
      id: w.id, name: w.name, placeCount: w.node_ids.length,
    })),
    goods: (st.goodsClasses ?? []).map((g) => ({ code: g.code, label: g.label })),
    // Replays are indexed only under a served title — a replay the
    // service cannot name in words is not indexed under its id.
    replays: (st.replays ?? []).filter((r) => !!r.title).map((r) => ({
      eventId: r.event_id, title: r.title!, observedSteps: r.observed_steps,
    })),
    lenses: (st.lenses ?? []).map((l) => ({
      lensId: l.lens_id, label: l.label, placeCount: l.node_ids.length,
    })),
    scans: (st.satScans ?? []).map((sc) => ({
      placeId: sc.node_id,
      name: sc.name,
      nowDate: sc.scene_now?.date,
      baselineDate: sc.scene_baseline?.date,
    })),
  };
}
