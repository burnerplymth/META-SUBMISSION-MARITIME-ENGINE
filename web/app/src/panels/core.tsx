import React, { Suspense, lazy, useEffect, useId, useMemo, useState } from 'react';
import { Background, Controls, Handle, Position, ReactFlow, getNodesBounds, useReactFlow, useStore as useFlowStore, type Edge, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { chainFitViewport } from './chainFit';
import { Panel } from '../components/Panel';
import { Citation } from '../components/Citation';
import { PanelBoundary } from '../components/PanelBoundary';
import { ChainLineageChip } from './history/lineageChip';
/* The map (MapLibre + deck.gl, the largest code
 * in the bundle) loads LAZILY behind this seam — the desk shell and
 * its panels ship in the main chunk and the map lands in its own
 * chunk on first render of the map panel. mapModule remains the
 * single swap point (real map vs stub) behind the lazy boundary. */
const MapView = lazy(() => import('../mapModule').then((m) => ({ default: m.MapView })));
import { focusPlaceOnMap } from '../mapFlyTo';
import { SimModeSwitch } from './sim';
import { EmptyState } from '../shell/Chip';
import { ChainBriefAction } from '../brief/hooks';
import { adapterFeedState, feedStamp, FEED_ADAPTERS } from '../shell/feedState';
import { CHAIN_FALLBACK, FEED_FALLBACK_LABEL, GRAPH_FALLBACK_LABEL, nodeName } from '../seed';
import { humaniseIds, nodeLabel, runRef, satCounts, srcStamp, statusLabel, vocab } from '../format';
import { GoodsBlock } from './impact';
import { ExplainChips } from '../meta/ExplainChips';
import { NarrativeDisclosure } from './NarrativeDisclosure';
import { CLASSIFY_COPY, ClassifyBlock, useFeedClassify, type ClassifyItem } from '../meta/FeedClassify';
import { ContextMenu } from '../components/ContextMenu';
import { Button, SegmentedControl } from '../ui/Controls';
import { BasisStamp } from '../shell/BasisStamp';
import { compositeMeta, feedBasisRows } from '../shell/basis';
import { ProvenanceCell } from '../provenance/ProvenanceCell';
import { chainStepProvenance, journalSections } from '../provenance/registry';
import { basemapForTheme, safeStorage, srcWord, useDeskStore, useSimStore } from '../store';
import { api } from '../api';
import { cleanNarrative, transportLabel } from '../format';
import { FEED_COPY, RAIL_COPY, activeSevAt, groupFeed, rankedPlaces } from './editorial';
import { barLayout, queueBins } from './charts';
import type { CausalEvent, Chain, FeedEvent, GraphData, Lens, LlmExplain, SatScan, Watchlist } from '../types';
import { labelWord } from '../copy';
import { useChainExportables } from '../exports/adapters';
import { useChainReport } from './chain/exportReport';
import { Icon } from '../ui/Icon';
import { Chip } from '../shell/Chip';
import { aggregateWord, eventLabel, runTitle, runWord } from '../shell/truthLabel';

const gmt = (iso: string) => `${iso.slice(11, 16)} GMT`;
/* a provenance entry carries a retrieved_at ONLY when a served
 * record actually carries one — export time is never stamped as
 * retrieval time (the exporter's own exported_at is the honest export
 * stamp). ExportSpec (components/Panel.tsx) still types
 * retrieved_at as required, so the omission is cast through at the
 * pack boundary via PackProvenance. */
type ProvEntry = { file: string; source: string; retrieved_at?: string; mode: string };
type PackProvenance = Array<{ file: string; source: string; retrieved_at: string; mode: string }>;
const prov = (source: string, retrieved: string | undefined, mode: string): ProvEntry[] =>
  [{ file: 'panel-data.json', source, ...(retrieved ? { retrieved_at: retrieved } : {}), mode }];

/* ── Helpers ──────────────────────────────────────────────────────────── */
const MONS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
/** Chain `at` timestamp → "06 Apr 23" (UTC) for the chart crosshair chip. */
const fmtDay = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso.slice(0, 10)
    : `${String(d.getUTCDate()).padStart(2, '0')} ${MONS[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)}`;
};
/* GET /graph nodes/edges are `unknown[]` at the API boundary; the
 * node fields the mini-strip derivations read are named here, nothing more. */
interface GNode { id: string; name?: string; lat: number; lon: number; capacity?: number; capacity_unit?: string; mode?: string; kind?: string; tier?: string }
interface GEdge { from: string; to: string; base_transit_d: number }
const gNodes = (g: GraphData) => g.nodes as unknown as GNode[];
const gEdges = (g: GraphData) => g.edges as unknown as GEdge[];
const fmtN = (n: number) => n.toLocaleString('en-US');
/** Network census as printed on screen: places and legs with the tier
 *  split, computed from the served places' tier fields (never
 *  hardcoded, so it stays true when the census changes). The split is
 *  shown only when the served network actually carries both tiers. */
export const networkCounts = (g: GraphData): string => {
  const ns = gNodes(g);
  const core = ns.filter((n) => n.tier === 'core').length;
  const shell = ns.filter((n) => n.tier === 'shell').length;
  const base = `${fmtN(ns.length)} places · ${fmtN(g.edges.length)} legs`;
  return core > 0 && shell > 0 ? `${base} · ${fmtN(core)} core · ${fmtN(shell)} shell` : base;
};
const havKm = (lat1: number, lon1: number, lat2: number, lon2: number) => {
  const R = 6371, rad = (d: number) => (d * Math.PI) / 180;
  const a = Math.sin(rad(lat2 - lat1) / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(rad(lon2 - lon1) / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
};
const CH_LABEL: Record<string, string> = { reroute: 'Rerouted', queue: 'Queued', congestion: 'Congested', 'mode-shift': 'Mode shift', inventory: 'Inventory', cost: 'Cost' };
export interface LaneRow { from: string; to: string; base: number; added: number; status: string }
/** Leg derivation: a graph lane is affected when its target node
 * (else its source node) is in the loaded chain; the lane's added delay
 * is the worst chain delay at that node, and its status is that event's
 * channel. Base transit is the graph edge's base_transit_d as served. */
export function routeLanes(chain: Chain, graph: GraphData): LaneRow[] {
  const delayAt = new Map<string, { delay: number; channel: string }>();
  for (const e of chain.events) {
    const cur = delayAt.get(e.node_id);
    if (!cur || e.delay_d > cur.delay) delayAt.set(e.node_id, { delay: e.delay_d, channel: e.channel });
  }
  const names = new Map(gNodes(graph).map((n) => [n.id, n.name || n.id]));
  const rows: LaneRow[] = [];
  for (const e of gEdges(graph)) {
    const hit = delayAt.get(e.to) ?? delayAt.get(e.from);
    if (!hit || hit.delay <= 0) continue;
    rows.push({ from: names.get(e.from) ?? e.from, to: names.get(e.to) ?? e.to, base: e.base_transit_d, added: hit.delay, status: CH_LABEL[hit.channel] ?? labelWord(hit.channel) });
  }
  rows.sort((a, b) => b.added - a.added || b.base - a.base);
  return rows;
}

/* Feed category tabs filter by served kind/source class. The
 * mapping reads only fields the records carry (kind, source, node id);
 * a category with no served mapping renders the honest empty state. */
const FEED_TABS = ['Top news', 'Ports', 'Energy', 'Weather', 'Policy'] as const;
type FeedTab = (typeof FEED_TABS)[number];
const feedTabOf = (f: { kind: string; source: string; node_id: string }): Exclude<FeedTab, 'Top news'> | null => {
  const kind = f.kind.toLowerCase(); const src = f.source.toUpperCase();
  if (kind.includes('weather') || kind.includes('storm') || src.includes('NOAA') || src.includes('NWS') || src.includes('GDACS')) return 'Weather';
  if (kind.includes('energy') || kind.includes('fuel') || kind.includes('oil')) return 'Energy';
  if (src.includes('CBP') || kind.includes('border') || kind.includes('customs') || kind.includes('policy')) return 'Policy';
  if (f.node_id.startsWith('sea_') || f.node_id.startsWith('chk_') || src.includes('PORTWATCH')) return 'Ports';
  return null;
};

export function NewsPanel() {
  const s = useDeskStore();
  const [tab, setTab] = useState<FeedTab>('Top news');
  const feed = s.feed;
  const inTab = tab === 'Top news' ? feed : feed.filter((f) => feedTabOf(f) === tab);
  /* Editorial layer (panels/editorial.ts): identical advisories for
   * one place render as one grouped entry — essence headline, office in
   * the meta row, areas as the sub-line, one GMT clock. Display only:
   * the export below still carries the raw records, full text, in the
   * order received. */
  const shown = useMemo(() => groupFeed(inTab), [inTab]);
  /* Meta AI classify: a toolbar action and an item context menu,
   * never a per-item button. The preview and confirm flow is unchanged. */
  const cls = useFeedClassify();
  const [menu, setMenu] = useState<{ x: number; y: number; item: ClassifyItem; back: HTMLElement | null } | null>(null);
  const itemOf = (g: (typeof shown)[number]): ClassifyItem => ({
    key: g.key, body: { text: g.rep.headline, source: g.rep.source, retrieved_at: g.rep.retrieved_at ?? g.rep.detected_at },
  });
  const offline = s.feedSource === 'fallback';
  /* The feed is a composite of several adapters: the header says COMPOSITE and
   * lists each feed with its own state in a chip popover (shell/BasisStamp). */
  const basisRows = useMemo(() => feedBasisRows(feed, s.health, s.snapshotMode), [feed, s.health, s.snapshotMode]);
  const updated = s.feedSource === 'live' ? srcStamp('Composite', basisRows.length ? basisRows.map((r) => r.label).join(', ') : 'Disruption feed')
    : offline ? FEED_FALLBACK_LABEL : 'Loading';
  const newsStamp = s.feedSource === 'live' ? <BasisStamp word="Composite" meta={compositeMeta(basisRows)} rows={basisRows} /> : undefined;
  /* the feed records carry their own SERVED retrieved_at (the
   * api layer normalises it from each record / its provenance); the
   * pack manifest carries the latest served stamp among the records
   * in the pack — and omits the field when no record carries one. */
  const servedRetrieved = feed.reduce<string | undefined>(
    (latest, f) => (f.retrieved_at && (!latest || f.retrieved_at > latest) ? f.retrieved_at : latest), undefined);
  /* TRUTH: the display label of each record comes from the feed's state now
   * and the record's age (shell/truthLabel.ts), never from the stored mode
   * alone. The stored records are exported untouched; `display_mode` rides
   * beside them. */
  const now = Date.now();
  const labelOf = (f: FeedEvent) => eventLabel(f, s.health, s.snapshotMode, now);
  const packWord = aggregateWord(feed.map(labelOf)) ?? (s.snapshotMode ? 'SNAPSHOT' : 'RECORDED');
  const packProv = s.feedSource === 'live'
    ? prov('SupermAP disruption feed (API service)', servedRetrieved, packWord)
    : prov('Bundled copy of the disruption feed (offline fallback)', servedRetrieved, 'OFFLINE COPY');
  return (
    <Panel id="news" code="NWS" title="Disruption news" updated={updated} stamp={newsStamp}
      exportSpec={{ json: () => feed.map((f) => ({ ...f, display_mode: labelOf(f)?.word })), csv: () => feed.map((f) => ({ ...f, display_mode: labelOf(f)?.word })), pack: () => ({ data: feed, provenance: packProv as PackProvenance }), source: 'NOAA / NWS; GDACS; GDELT' }}>
      <div className="ntabs mono">{FEED_TABS.map((t) => (
        <button key={t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)}>{t}</button>))}</div>
      <div className="ntools" role="toolbar" aria-label="News tools">
        <Button variant="ghost" size="sm" icon="ai" aria-label="Classify visible with Meta AI" disabled={!shown.length || cls.busy}
          onClick={() => void cls.startMany(shown.map(itemOf))}>{CLASSIFY_COPY.toolbar}</Button>
      </div>
      {offline && <p className="offline-chip mono">{FEED_FALLBACK_LABEL}</p>}
      {!offline && s.snapshotMode && s.feedSource === 'live' &&
        <p className="snap-chip mono">Snapshot · Captured copy{s.capturedAt ? ` · captured ${s.capturedAt.slice(0, 10)}` : ''} · Not live</p>}
      {s.feedSource === 'live' && s.feedInvalid > 0 &&
        <p className="fineprint">Feed · {s.feedInvalid} unreadable {s.feedInvalid === 1 ? 'line' : 'lines'} skipped</p>}
      {shown.map((g) => {
        const f = g.rep;
        return (
        <article key={g.key} className={`news-item ${g.maxSev >= 4 ? 'news-item--sev' : ''} ${s.selectedNode === f.node_id ? 'news-item--sel' : ''}`}
          tabIndex={0} data-item={g.key}
          onClick={() => { s.selectNode(f.node_id); focusPlaceOnMap(f.node_id); }}
          onContextMenu={(e) => { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY, item: itemOf(g), back: e.currentTarget }); }}
          onKeyDown={(e) => {
            if (e.target !== e.currentTarget) return;
            if (e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey)) {
              e.preventDefault();
              const r = e.currentTarget.getBoundingClientRect();
              setMenu({ x: r.left + 24, y: r.top + 24, item: itemOf(g), back: e.currentTarget });
            }
          }}>
          <div className="news-meta mono"><span>{gmt(f.started_at ?? f.detected_at)}</span><span>·</span>
            <span>{labelWord(f.kind)} · {labelWord(nodeName(f.node_id))}</span>
            {g.office && <><span>·</span><span>{labelWord(g.office)}</span></>}</div>
          <p title={f.headline}>{humaniseIds(g.essence, s.graph)}</p>
          {g.areas.length > 0 && (
            <div className="news-areas">{g.areas.slice(0, 2).join(' · ')}{g.areas.length > 2 ? ` · ${FEED_COPY.moreAreas(g.areas.length - 2)}` : ''}</div>)}
          <div className="news-tags"><span className="src-tag">{f.source}</span>
            {(() => { const l = labelOf(f); return l && <span className={`mode-tag mode--${l.word.toLowerCase()}`}>{l.word}{!l.live && l.asOf ? ` ${l.asOf.slice(5)}` : ''}</span>; })()}
            <span className="sev-tag">Sev {g.maxSev}</span>
            {g.members.length > 1 && <span className="sev-tag">{FEED_COPY.records(g.members.length)}</span>}
          </div>
          <Citation c={f.citation ?? g.members.find((m) => m.citation)?.citation} onClick={(e) => e.stopPropagation()} />
          <ClassifyBlock phase={cls.phases[g.key]} onConfirm={() => cls.confirm(itemOf(g))} onCancel={() => cls.cancel(g.key)} />
        </article>);
      })}
      {menu && (
        <ContextMenu x={menu.x} y={menu.y} label="News item" onClose={() => { const b = menu.back; setMenu(null); b?.focus(); }}
          items={[{ key: 'classify', label: CLASSIFY_COPY.menu, icon: 'ai', onSelect: () => void cls.start(menu.item) }]} />)}
      {!shown.length && <p className="dim">{feed.length ? `No ${tab.toLowerCase()} items` : 'No events'}</p>}
    </Panel>
  );
}

/** Lens view state for one chain step: with no lens everything is 'on';
 *  seed steps (parent_id null — the disruption itself) are always 'origin'
 *  (full opacity, marked ORIGIN); other steps are 'on' iff their node is in
 *  the lens, else 'off' (dimmed). Client-side only — no engine calls. */
export type LensState = 'on' | 'off' | 'origin';
export const lensStateFor = (lens: Lens | null, ev: CausalEvent): LensState => {
  if (!lens) return 'on';
  if (ev.parent_id === null) return 'origin';
  return lens.node_ids.includes(ev.node_id) ? 'on' : 'off';
};
export function useActiveLens(): Lens | null {
  const s = useDeskStore();
  return useMemo(() => (s.lenses ?? []).find((l) => l.lens_id === s.activeLens) ?? null, [s.lenses, s.activeLens]);
}

/** Lens selector (GET /lenses): All + one chip per curated lens; when a
 *  lens is active its name and a Modelled chip close the chain views. */
const ALL_LENS = '__all__';
export function LensBar() {
  const s = useDeskStore();
  const active = useActiveLens();
  if (s.lensesSource !== 'live') {
    return <p className="fineprint mono">Lens · {s.lensesSource === 'unreachable'
      ? 'Unreachable' : 'Loading'}</p>;
  }
  return (
    <div className="lens-bar">
      <span className="dim">Lens</span>
      <SegmentedControl label="Lens" size="sm" className="ui-seg--wrap" value={s.activeLens ?? ALL_LENS}
        onChange={(v) => s.setLens(v === ALL_LENS ? null : v)}
        options={[{ value: ALL_LENS, label: 'All', title: 'All places' }, ...(s.lenses ?? []).map((l) => ({ value: l.lens_id, label: l.label }))]} />
      {active && <span className="dim small">{active.node_ids.length} places</span>}
    </div>
  );
}
export function LensFooter() {
  const active = useActiveLens();
  if (!active) return null;
  return <p className="fineprint lens-basis">Lens · {active.label} <Chip kind="model" word="Modelled" /></p>;
}

export function MarketWatchPanel() {
  const s = useDeskStore();
  const mapped = s.moneyExposures?.mappings ?? [];
  const byNode = useMemo(() => {
    const m = new Map<string, number>();
    mapped.forEach((x) => m.set(x.node_id, (m.get(x.node_id) ?? 0) + 1));
    return m;
  }, [mapped]);
  const nodes = useMemo(() => {
    const chainNodes = [...new Set((s.chain?.events ?? []).map((e) => e.node_id))];
    return [...new Set([...byNode.keys(), ...chainNodes])].slice(0, 10);
  }, [s.chain, byNode]);
  const updated = s.moneyExposuresSource === 'live' ? feedStamp(adapterFeedState(s.health, FEED_ADAPTERS.edgar, s.snapshotMode), `SEC EDGAR · ${mapped.length} mappings`)
    : s.moneyExposuresSource === 'unreachable' ? 'SEC EDGAR · Unreachable' : 'SEC EDGAR · Loading';
  return (
    <Panel id="market" code="MKT" title="Market watch" updated={updated}
      exportSpec={{ json: () => s.moneyExposures, source: 'SEC EDGAR' }}>
      <p className="fineprint">Source: SEC EDGAR</p>
      <div className="watch-list">
        {nodes.map((n) => (
          <button key={n} className="watch-row" onClick={() => s.showMoney(n)}>
            <span>{nodeName(n)}</span>
            <span className="amber">{byNode.has(n) ? `${byNode.get(n)} cited` : 'Exposures'}</span>
          </button>
        ))}
        {!nodes.length && <EmptyState word="No chain" />}
      </div>
    </Panel>
  );
}

/** RTE route table for the home mini-strip:
 *  top affected lanes for the loaded run, derived only from GET /graph
 *  edges + the loaded chain (see routeLanes). With no run loaded, the
 *  bundled CANNED replay's chain drives the table and the block is
 *  labelled CANNED REPLAY. */
export function RouteTable() {
  const s = useDeskStore();
  const canned = !s.chain;
  const chain = s.chain ?? CHAIN_FALLBACK;
  const allRows = useMemo(() => routeLanes(chain, s.graph), [chain, s.graph]);
  /* Shows the top 3 + an overflow count line. Status wording
   * comes from the served channel (reroute channel → REROUTED via
   * CH_LABEL); graph edges carry no bypass field as served. Numerics stay
   * right-aligned — deliberate deviation, see the register. */
  const rows = allRows.slice(0, 3);
  return (
    <div className="ms-block ms-rte">
      <div className="ms-head"><span className="code-chip">Leg</span><span className="ms-title">Top affected legs</span>
        <span className="ms-upd">{canned ? 'Stored replay' : (s.runMeta ? runWord(s.runMeta, s.health, s.snapshotMode, Date.now()) : '')}</span></div>
      {rows.length ? (
        <table className="rte-table mono">
          <thead><tr><th>Leg</th><th className="num">Base (d)</th><th className="num">Now (d)</th><th className="num">Added (d)</th><th>Status</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={`${r.from}→${r.to}`}>
              <td className="rte-name">{r.from} → {r.to}</td>
              <td className="num">{r.base.toFixed(1)}</td>
              <td className="num">{(r.base + r.added).toFixed(1)}</td>
              <td className="num amber">+{r.added.toFixed(1)}</td>
              <td>{r.status}</td>
            </tr>))}
          </tbody>
        </table>
      ) : <p className="ms-empty">No affected legs</p>}
      {allRows.length > 3 && <p className="ms-note">+{allRows.length - 3} more affected legs</p>}
    </div>
  );
}

/** EXP exposure mini-panel for the home strip:
 *  the selected node's top EDGAR-cited exposures from GET /money/node/{id},
 *  filing citation enriched from GET /money/exposures, prices exactly as
 *  served (null = Unpriced, never invented). Same fetch + honesty states
 *  as the full Money panel. */
export function ExposureMini() {
  const s = useDeskStore();
  const node = s.selectedNode;
  useEffect(() => { if (node) void s.fetchMoney(node); /* eslint-disable-next-line */ }, [node]);
  const panel = s.moneyPanel && s.moneyPanel.node_id === node ? s.moneyPanel : null;
  const top = (panel?.exposed ?? []).slice(0, 4);
  const filingFor = (ticker: string) =>
    (s.moneyExposures?.mappings ?? []).find((m) => m.node_id === node && m.ticker === ticker)?.filing;
  return (
    <div className="ms-block ms-exp">
      <div className="ms-head"><span className="code-chip">Cos</span><span className="ms-title">Exposed{node ? ` · ${nodeName(node)}` : ''}</span>
        <span className="ms-upd">SEC EDGAR</span></div>
      {!node && <p className="ms-empty">Select a place</p>}
      {node && s.moneySource === 'unreachable' &&
        <p className="ms-empty">Unreachable · Company exposure</p>}
      {node && s.moneySource !== 'unreachable' && !panel &&
        <p className="ms-empty">Loading · {nodeName(node)}</p>}
      {node && panel && top.length === 0 &&
        <p className="ms-empty">No exposures mapped</p>}
      {top.map((x) => {
        const f = filingFor(x.ticker);
        return (
          <div key={x.ticker} className="exp-row mono">
            <span className="exp-ticker">{x.ticker}</span>
            <span className="exp-co">{x.company}</span>
            <span className="exp-cite">{f ? `${f.form} · ${f.filed_date}` : 'EDGAR'}</span>
            <span className="exp-px">{x.last_price != null ? `$${x.last_price.toFixed(2)}` : 'Unpriced'}</span>
          </div>
        );
      })}
    </div>
  );
}

/** SAT INTEL block in the Map Content rail:
 *  the selected node's scan from GET /satwatch/scans when one exists,
 *  else the nearest scanned anchorage (haversine over served graph
 *  lat/lon, labelled as such), else the latest scan. Dated always;
 *  detection status verbatim; counts only when non-null as served. */
function SatIntelBlock() {
  const s = useDeskStore();
  const scans = s.satScans ?? [];
  let body: React.ReactNode;
  if (s.satSource !== 'live') {
    body = <dl className="kv"><dt>Scans</dt><dd>{s.satSource === 'unreachable'
      ? 'Unreachable · Satellite scans' : 'Loading · Satellite scans'}</dd></dl>;
  } else if (!scans.length) {
    body = <dl className="kv"><dt>Scans</dt><dd>No scans</dd></dl>;
  } else {
    const nodesById = new Map(gNodes(s.graph).map((n) => [n.id, n]));
    let scan: SatScan | undefined = s.selectedNode
      ? scans.find((sc) => sc.node_id === s.selectedNode) : undefined;
    let basis = scan ? 'Selected place' : '';
    if (!scan) {
      const sel = s.selectedNode ? nodesById.get(s.selectedNode) : undefined;
      let best: { sc: SatScan; d: number } | null = null;
      if (sel) for (const sc of scans) {
        const an = nodesById.get(sc.node_id); if (!an) continue;
        const d = havKm(sel.lat, sel.lon, an.lat, an.lon);
        if (!best || d < best.d) best = { sc, d };
      }
      if (best) { scan = best.sc; basis = `Nearest scan · ${Math.round(best.d)} km`; }
      else { scan = [...scans].sort((a, b) => b.scene_now.date.localeCompare(a.scene_now.date))[0]; basis = 'Latest scan'; }
    }
    const sc = scan as SatScan;
    body = (
      <dl className="kv">
        <dt>Anchorage</dt><dd className="amber">{sc.name}</dd>
        <dt>Basis</dt><dd className="mono">{basis}</dd>
        <dt>Imagery</dt><dd className="mono">{sc.scene_now.date}{sc.scene_now.platform ? ` · ${sc.scene_now.platform}` : ''}</dd>
        <dt>Baseline</dt><dd className="mono">{sc.scene_baseline.date}</dd>
        <dt>Status</dt><dd className="mono small">{statusLabel(sc.detection.status)}</dd>
        {sc.detection.provider === 'reference-counts' && (
          <><dt>Detection</dt><dd className="mono small">Offline test replay</dd></>
        )}
        <dt>Counts</dt><dd className="mono small">{(() => {
          const c = satCounts(sc.detection.counts);
          if (sc.detection.counts == null) return 'Not served';
          if (!c) return JSON.stringify(sc.detection.counts);
          return `Anchorage vessels now ${c.vesselsNow} · baseline ${c.vesselsBaseline} · change ${c.delta >= 0 ? '+' : ''}${c.delta}`;
        })()}</dd>
      </dl>
    );
  }
  return <div className="sec"><div className="sec-t"><Icon name="chevron-down" size="xs" className="sec-chev" /> Satellite intel</div>{body}</div>;
}

/** Top level of the rail: the ranked list a desk scans
 *  first — active disruptions by severity, then the watchlists'
 *  places with their live state read off the same served feed. Rows
 *  select their place in every linked panel; nothing here computes a
 *  fact the feed does not serve. */
function RankedBlock() {
  const s = useDeskStore();
  const ranked = useMemo(() => rankedPlaces(s.feed, 6), [s.feed]);
  const [watch, setWatch] = useState<Watchlist[] | null>(null);
  const [watchFailed, setWatchFailed] = useState(false);
  useEffect(() => {
    let dead = false;
    api.watchlists()
      .then((w) => { if (!dead) setWatch(w.watchlists); })
      .catch(() => { if (!dead) setWatchFailed(true); });
    return () => { dead = true; };
  }, []);
  const watchPlaces = (watch ?? []).flatMap((w) => w.node_ids.map((id) => ({ list: w.name, id })));
  const shownWatch = watchPlaces.slice(0, 8);
  return (
    <div className="sec"><div className="sec-t"><Icon name="chevron-down" size="xs" className="sec-chev" /> {RAIL_COPY.rankedTitle}</div>
      {ranked.length ? ranked.map((r) => (
        <button key={r.node_id} className="rank-row" onClick={() => { s.selectNode(r.node_id); focusPlaceOnMap(r.node_id); }}>
          <span className={`sev-tag ${r.maxSev >= 4 ? 'sev-tag--hot' : ''}`}>Sev {r.maxSev}</span>
          <b>{nodeName(r.node_id)}</b>
          <span className="rank-r dim">{labelWord(r.kind)} · {gmt(r.latest)}{r.activeCount > 1 ? ` · ${r.activeCount} active` : ''}</span>
        </button>)) : <p className="ms-empty">{RAIL_COPY.rankedEmpty}</p>}
      <div className="sec-sub mono">{RAIL_COPY.watchHead}</div>
      {watchFailed && <p className="ms-empty">{RAIL_COPY.watchUnavailable}</p>}
      {watch !== null && watch.length === 0 && <p className="ms-empty">{RAIL_COPY.watchNone}</p>}
      {shownWatch.map(({ list, id }) => {
        const sev = activeSevAt(s.feed, id);
        return (
          <button key={`${list}:${id}`} className="rank-row" onClick={() => { s.selectNode(id); focusPlaceOnMap(id); }}>
            <span className="dim">{labelWord(list)}</span>
            <b>{nodeName(id)}</b>
            <span className={`rank-r ${sev !== null && sev >= 4 ? 'sev-tag sev-tag--hot' : 'dim'}`}>{sev !== null ? RAIL_COPY.activeAt(sev) : RAIL_COPY.quietAt}</span>
          </button>);
      })}
      {watchPlaces.length > shownWatch.length && <p className="ms-note">{RAIL_COPY.watchMore(watchPlaces.length - shownWatch.length)}</p>}
    </div>
  );
}

/** Bottom level of the rail: the loaded run's narrative, pinned at
 *  the foot of Map Content. The same seam EXP uses (POST /llm/explain —
 *  the service caches per run, so this costs nothing the panel has not
 *  already paid): transport/cached chips exactly as served, narrative
 *  cleaned exactly as EXP cleans it, and the full step record stays in
 *  EXP. A failed fetch is one plain line, never a fabricated summary. */
function RailNarrative() {
  const s = useDeskStore();
  const runId = s.chain?.run_id ?? null;
  const [ex, setEx] = useState<{ runId: string; data: LlmExplain } | 'loading' | 'unavailable' | null>(null);
  useEffect(() => {
    if (!runId) { setEx(null); return; }
    let dead = false;
    setEx('loading');
    api.explain(runId)
      .then((data) => { if (!dead) setEx({ runId, data }); })
      .catch(() => { if (!dead) setEx('unavailable'); });
    return () => { dead = true; };
  }, [runId]);
  const live = ex && typeof ex === 'object' && ex.runId === runId ? ex.data : null;
  return (
    <div className="sec"><div className="sec-t"><Icon name="chevron-down" size="xs" className="sec-chev" /> {RAIL_COPY.narrativeTitle}</div>
      {!runId && <p className="ms-empty">{RAIL_COPY.narrativeNone}</p>}
      {runId && ex === 'loading' && <p className="explain-line">{RAIL_COPY.narrativeLoading}</p>}
      {runId && ex === 'unavailable' && <p className="explain-line">{RAIL_COPY.narrativeUnavailable}</p>}
      {live && (
        <>
          <ExplainChips data={live} />
          <NarrativeDisclosure text={cleanNarrative(live.narrative, s.graph)} />
          <p className="ms-note">{RAIL_COPY.narrativeFull}</p>
        </>
      )}
    </div>
  );
}

/** Right rail, top: Map Content rail. Sections:
 *  01 DISRUPTION (served feed event + chain run +
 *  ledger lock state for the selected node), 02 SELECTION (identity kv),
 *  03 LAYERS (checkbox group wired to real layer state), 04 SAT
 *  INTEL (product block), 05 GRAPH (folded counts/source/hash).
 *  The head stamp keeps the served run mode.
 *  The rail composes three levels —
 *  00 RANKED (list) above the detail sections, 07 NARRATIVE (the loaded
 *  run's explanation) pinned at the foot; the SIM status boards compose
 *  in the same column via the SIM panel when SIM is active. */
export function MapContentPanel() {
  const s = useDeskStore();
  const sel = s.selectedNode;
  const evs = (s.chain?.events ?? []).filter((e) => !sel || e.node_id === sel);
  const worst = evs.reduce((m, e) => Math.max(m, e.delay_d), 0);
  const channels = [...new Set(evs.map((e) => e.channel))];
  const node = sel ? gNodes(s.graph).find((n) => n.id === sel) : undefined;
  const feedEv = sel ? (s.feed.find((f) => f.node_id === sel && f.status === 'active') ?? s.feed.find((f) => f.node_id === sel)) : undefined;
  const lock = s.chain && s.ledger ? s.ledger.find((e) => e.run_id === s.chain?.run_id) : undefined;
  const lv = s.layerVis;
  return (
    <Panel id="mapcontent" code="RNK" title="Ranked now" updated={s.runMeta ? runWord(s.runMeta, s.health, s.snapshotMode, Date.now()) : 'No chain'}>
      <RankedBlock />
      <div className="sec"><div className="sec-t"><Icon name="chevron-down" size="xs" className="sec-chev" /> Disruption{sel ? ` · ${nodeName(sel)}` : ''}</div>
        <dl className="kv">
          <dt>Severity</dt><dd className={feedEv && feedEv.severity >= 4 ? 'r' : ''}>{feedEv ? `Sev ${feedEv.severity} · ${labelWord(feedEv.status)}` : sel ? 'None' : '—'}</dd>
          <dt>Detected</dt><dd className="w">{feedEv ? `${feedEv.detected_at.slice(11, 16)} UTC · ${feedEv.source}` : '—'}</dd>
          <dt>Chain run</dt><dd className="mono">{s.chain ? runRef(s.chain, s.graph) : '—'}</dd>
          <dt>Chain ledger</dt><dd className={lock && !lock.grade ? 'g' : ''}>{s.ledgerSource !== 'live' ? 'Not served'
            : lock ? (lock.grade ? `Graded${lock.grade.graded_at ? ` ${lock.grade.graded_at.slice(0, 10)}` : ''}` : `Locked ${lock.locked_at.slice(11, 16)} UTC`)
            : 'Open'}</dd>
        </dl></div>
      <div className="sec"><div className="sec-t"><Icon name="chevron-down" size="xs" className="sec-chev" /> Selection</div>
        <dl className="kv">
          <dt>Selected place</dt><dd className="amber">{sel ? nodeName(sel) : '—'}</dd>
          <dt>Mode / kind</dt><dd className="w">{node ? `${node.mode ? labelWord(node.mode) : '—'} · ${node.kind ? labelWord(node.kind) : '—'}` : '—'}</dd>
          <dt>Capacity</dt><dd className="mono">{node && node.capacity != null ? `${node.capacity} ${node.capacity_unit ?? ''}` : '—'}</dd>
          <dt>Events at place</dt><dd className="amber">{evs.length}</dd>
          <dt>Worst added transit</dt><dd className="amber">{s.chain ? `${worst.toFixed(1)} d` : '—'}</dd>
          <dt>Channels</dt><dd className="w">{channels.map(labelWord).join(' · ') || '—'}</dd>
          <dt>Run label</dt><dd className="w">{s.runMeta ? runTitle(s.runMeta, s.health, s.snapshotMode, Date.now()) : '—'}</dd>
        </dl></div>
      <div className="sec"><div className="sec-t"><Icon name="chevron-down" size="xs" className="sec-chev" /> Layers</div>
        <div className="lyr mono">
          <label><input type="checkbox" checked={lv.lanes} onChange={(e) => s.setLayerVis({ lanes: e.target.checked })} /> Shipping lanes</label>
          <label><input type="checkbox" checked={lv.ports} onChange={(e) => s.setLayerVis({ ports: e.target.checked })} /> Anchorages</label>
          <label><input type="checkbox" checked={lv.chokes} onChange={(e) => s.setLayerVis({ chokes: e.target.checked })} /> Chokepoints</label>
          <label><input type="checkbox" checked={lv.air} onChange={(e) => s.setLayerVis({ air: e.target.checked })} /> Air corridors</label>
          <label className="off" title="Not served">
            <input type="checkbox" checked={false} disabled /> Weather · Not served</label>
          <label><input type="checkbox" checked={s.basemap === 'sat'}
            onChange={(e) => s.setBasemap(e.target.checked ? 'sat' : basemapForTheme(s.theme))} /> Satellite</label>
        </div>

      </div>
      <SatIntelBlock />
      <div className="sec"><div className="sec-t"><Icon name="chevron-down" size="xs" className="sec-chev" /> Network</div>
        <dl className="kv">
          <dt>Network</dt><dd className="mono">{s.graphSource === 'loading' ? '—' : networkCounts(s.graph)}</dd>
          <dt>Network source</dt><dd className="w">{s.graphSource === 'live' ? srcWord(s.snapshotMode) : s.graphSource === 'fallback' ? GRAPH_FALLBACK_LABEL : 'Loading'}</dd>
        </dl></div>
      <GoodsBlock />
      <button className="btn" disabled={!s.selectedNode} onClick={() => s.selectedNode && s.showMoney(s.selectedNode)}>Show exposures</button>
      <RailNarrative />
    </Panel>
  );
}

/** QUE block: cascade counts + the derived queue chart. The
 *  derivation buckets the loaded chain's queue/congestion events by
 *  lands-day (chain `at`, relative to the first such event) — an engine
 *  projection of chain events, captioned as exactly that. It is never
 *  labelled "VS AIS LIVE": no live AIS series exists in this build. */
function QueBlock() {
  const s = useDeskStore();
  const que = (s.chain?.events ?? []).filter((e) => e.channel === 'queue' || e.channel === 'congestion').length;
  const rte = (s.chain?.events ?? []).filter((e) => e.channel === 'reroute').length;
  /* Time bins (panels/charts.ts) whose width widens openly with the
   * span; the caption states the bin width whenever it exceeds one
   * day, and every event is counted. */
  const buckets = useMemo(() => queueBins(
    (s.chain?.events ?? []).filter((e) => e.channel === 'queue' || e.channel === 'congestion').map((e) => e.at),
  ), [s.chain]);
  return (
    <div className="ms-block ms-que">
      <div className="ms-head"><span className="code-chip">Que</span><span className="ms-title">Cascade state</span>
        <span className="ms-upd">{s.runMeta ? runWord(s.runMeta, s.health, s.snapshotMode, Date.now()) : '—'}</span></div>
      <dl className="ms-kv mono">
        <div><dt>Queue / congestion</dt><dd>{que}</dd></div>
        <div><dt>Reroute</dt><dd>{rte}</dd></div>
      </dl>
      {buckets ? (
        <>
          <svg className="que-chart" width="262" height="34" viewBox="0 0 262 34" aria-hidden>
            {buckets.counts.map((c, i) => {
              const h = buckets.peak ? (c / buckets.peak) * 30 : 0;
              const fill = c === 0 ? 'var(--text-3)' : c >= buckets.peak * 0.85 ? 'var(--bar-queue)' : c >= buckets.peak * 0.6 ? 'var(--range)' : 'var(--text-3)';
              return <rect key={i} x={4 + (15 - buckets.counts.length) * 8.5 + i * 17} y={32 - h} width={11} height={Math.max(h, 1)} fill={fill} fillOpacity={0.9} />;
            })}
          </svg>
          <p className="que-cap mono">Modelled · Queue events {buckets.binDays > 1 ? `per ${buckets.binDays} days` : 'by day'} · Peak {buckets.peak} at D+{buckets.peakDay}</p>
        </>
      ) : <p className="ms-empty">No queue events</p>}
    </div>
  );
}

/** Map section: the bordered panel head is dropped for the map —
 *  a 30px overlay bar carries the title, the served data-mode pills, the
 *  view tabs (wired to centreTab) and the served graph counts.
 *  The AIS pill states the layer's real served state (adapter board) —
 *  "LIVE AIS" is rendered only if the board actually serves LIVE. */
export function MapPanel({ demoActive }: { demoActive?: boolean } = {}) {
  const s = useDeskStore();
  const sim = useSimStore(); /* only the boundary keys + timeline gate read the sim slice */
  /* The map is the hero. One slim bar (title, view tabs, mode, counts); the cascade summary strip
   * (queue state, top affected routes, exposure) is a drawer opened from the bar, closed by default. The AIS
   * and PortWatch state words live in the map's Layers popover (Sources) and in the status bar. */
  const [mini, setMini] = useState<boolean>(() => safeStorage.get('supermap.map.mini') === '1');
  const toggleMini = () => setMini((v) => { const n = !v; safeStorage.set('supermap.map.mini', n ? '1' : '0'); return n; });
  return (
    <section className="panel panel--map" data-panel="map">
      <div className="mapbar mono">
        <span className="seg vt" role="group" aria-label="Centre view">{(['Map', 'Table', 'Chart', 'News'] as const).map((t) =>
          <button key={t} type="button" className={`seg-b${s.centreTab === t ? ' on' : ''}`} aria-pressed={s.centreTab === t} onClick={() => s.setCentreTab(t)}>{labelWord(t)}</button>)}</span>
        {/* While the network source is loading the graph is
            still the empty boot graph — rendering its counts would
            print the false fact "0 PLACES · 0 ROUTES" on the first
            impression. The counts read "—" until the source answers. */}
        <span className="counts" title={s.graphSource === 'loading' ? undefined : `${networkCounts(s.graph)}${gNodes(s.graph).some((n) => n.tier === 'core') ? ' · Core places' : ''}`}>{s.graphSource === 'loading' ? '—' : networkCounts(s.graph).split(' · ')[0].toLowerCase()}</span>
        <button type="button" className={`seg-b mapbar-mini${mini ? ' on' : ''}`} aria-pressed={mini} aria-label="Cascade summary" title="Cascade summary" onClick={toggleMini}>Cascade</button>
        <SimModeSwitch />
        {/* The map is a window: same control idiom as Panel. */}
        <span className="panel-controls">
          <button className="icon-btn" title="Maximize" aria-label="Maximize" onClick={() => s.toggleMax('map')}><Icon name="maximize" /></button>
          <button className="icon-btn" title="Close" aria-label="Close" onClick={() => s.closeMap()}><Icon name="close" /></button>
        </span>
      </div>
      {/* The map section is not a <Panel>, so it carries its own
          boundary: a render fault in the map view or the SIM timeline
          degrades this section to the error note, never the whole app. */}
      <PanelBoundary panelLabel="MAP" resetKeys={[s.centreTab, s.chain?.run_id ?? '', sim.simRun?.sim_run_id ?? '', sim.simActive]}>
      {/* every centre view stays MOUNTED; the tab switch only
          toggles CSS display. The old ternary unmounted MapView on
          every switch, so MapLibre + deck.gl were rebuilt and tiles
          refetched on each return. Re-show recovery is by
          construction: the map is created without a trackResize
          override, so MapLibre's default ResizeObserver on its
          container fires when the container regains size and resizes
          the canvas (no map-side change needed). The map wrapper
          carries the panel's flex column itself so the visible layout
          is byte-identical to the unmounted era. */}
      <div className="map-alt" style={{ display: s.centreTab === 'Table' ? undefined : 'none' }}><ChainTable /></div>
      <div className="map-alt" style={{ display: s.centreTab === 'Chart' ? undefined : 'none' }}><DelayChart large /></div>
      <div className="map-alt" style={{ display: s.centreTab === 'News' ? undefined : 'none' }}><NewsPanel /></div>
      <div style={{ display: s.centreTab === 'Map' ? 'flex' : 'none', flexDirection: 'column', flex: 1, minHeight: 0 }}>
          {s.graphSource === 'fallback' &&
            <p className="offline-chip mono">Network {GRAPH_FALLBACK_LABEL}</p>}
          {s.snapshotMode && s.graphSource === 'live' &&
            <p className="snap-chip mono">Snapshot · Captured copy{s.capturedAt ? ` · captured ${s.capturedAt.slice(0, 10)}` : ''} · Not live</p>}
          <div className="map-view">
            <Suspense fallback={<div className="map-loading mono">Network · Loading</div>}>
              <MapView chain={s.chain} selectedNode={s.selectedNode} graph={s.graph} onSelect={(id, seq) => s.selectNode(id, seq)} demoActive={demoActive} />
            </Suspense>
          </div>
          {mini && (
            <div className="ministrip">
              <QueBlock />
              <RouteTable />
              <ExposureMini />
            </div>
          )}
      </div>
      </PanelBoundary>
    </section>
  );
}

export function ChainTable() {
  const s = useDeskStore();
  const lens = useActiveLens();
  const chain = s.chain;
  const runMeta = s.runMeta;
  const runSource = !runMeta ? null : runMeta.mode === 'LIVE' ? runTitle(runMeta, s.health, s.snapshotMode, Date.now()) : runMeta.mode === 'CANNED' ? 'Stored run record' : 'Stated scenario';
  const prov = (e: CausalEvent, field: 'delay' | 'magnitude') => () => chainStepProvenance({
    chain: chain!, event: e, spec: runMeta?.spec, engineLabel: null, placeName: nodeName, runSource,
    loadJournal: () => api.runOrigin(chain!.run_id).then((o) => journalSections(o as never)),
  }, field);
  return (
    <div className="chain-table-wrap">
    <table className="data-table chain-table">
      <thead><tr><th>#</th><th>Place</th><th>Channel</th><th className="num">Delay (d)</th><th className="num">Magnitude</th><th>Lands (UTC)</th><th>Mechanism</th></tr></thead>
      <tbody>{(s.chain?.events ?? []).map((e) => {
        const ls = lensStateFor(lens, e);
        return (
        <tr key={e.seq} className={`${s.selectedSeq === e.seq ? 'row--sel' : ''} ${ls === 'off' ? 'lens-off' : ''}`} onClick={() => s.selectNode(e.node_id, e.seq)}>
          {/* displayed step numbers are seq + 1 on every surface
              (the map/inspector/focus surfaces' STEP n convention);
              the raw seq stays the identity/selection key only. */}
          <td className="mono">{e.seq + 1}</td><td>{nodeName(e.node_id)}{ls === 'origin' && <span className="origin-tag mono">Origin</span>}</td><td>{labelWord(e.channel)}</td>
          <td className="mono amber num"><ProvenanceCell make={prov(e, 'delay')}>{e.delay_d.toFixed(1)}</ProvenanceCell></td><td className="mono num"><ProvenanceCell make={prov(e, 'magnitude')}>{e.magnitude.toFixed(2)}</ProvenanceCell></td>
          <td className="mono">{e.at.replace('T', ' ').slice(0, 16)}</td><td className="mech">{e.mechanism}</td>
        </tr>);
      })}
      </tbody>
    </table>
    </div>
  );
}

export function DelayChart({ large = false, h, w }: { large?: boolean; h?: number; w?: number }) {
  const s = useDeskStore();
  /* every mounted instance owns a unique svg DOM id (the chart
   * strip + the MapPanel Chart tab mount two at once). */
  const svgId = `chart-svg-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const [hov, setHov] = useState<number | null>(null);
  const evs = s.chain?.events ?? [];
  const max = Math.max(0.001, ...evs.map((e) => e.delay_d));
  const W = w ?? 960, H = h ?? (large ? 300 : 120);
  /* Bar layout (panels/charts.ts): bar width capped at 48px and the series
   * block centred when sparse — a one-event chain is one 48px bar,
   * never a full-width slab. Grid and axis chips stay full-width. */
  const { bw, x0 } = barLayout(evs.length, W);
  const hovEv = hov !== null ? evs.find((e) => e.seq === hov) : undefined;
  const hovX = hovEv ? x0 + evs.indexOf(hovEv) * bw + bw / 2 : 0;
  /* chain OHLC-style stats as served (first / max / min / last
   * delay in seq order) for the head line. */
  const delays = evs.map((e) => e.delay_d);
  const ohlc = evs.length ? { o: delays[0], h: Math.max(...delays), l: Math.min(...delays), c: delays[delays.length - 1] } : null;
  const lastY = ohlc ? H - 24 - (ohlc.c / max) * (H - 48) : 0;
  return (
    <div className="chart-wrap">
      <div className="chart-head mono">
        <span className="code-chip">CHT</span><b>{s.runMeta ? runTitle(s.runMeta, s.health, s.snapshotMode, Date.now()) : 'Added delay (d)'}</b>
        <span>{s.runMeta ? runWord(s.runMeta, s.health, s.snapshotMode, Date.now()) : ''} · Chain</span>
        {ohlc && <span className="ohlc">O <b>{ohlc.o.toFixed(1)}</b> H <b>{ohlc.h.toFixed(1)}</b> L <b>{ohlc.l.toFixed(1)}</b> C <b>{ohlc.c.toFixed(1)}</b></span>}
        <span className="spacer" />
        <span>Max {evs.length ? max.toFixed(1) : '—'}d · {evs.length} events</span>
      </div>
      <svg id={svgId} viewBox={`0 0 ${W} ${H}`} className="chart-svg">
        {[0.25, 0.5, 0.75, 1].map((f) => (
          <g key={f}>
            <line x1={20} x2={W - 4} y1={H - 24 - f * (H - 48)} y2={H - 24 - f * (H - 48)} className="tv-grid" />
            <text x={W - 44} y={H - 28 - f * (H - 48)} textAnchor="end" className="axis-chip">{(max * f).toFixed(1)}</text>
          </g>
        ))}
        {evs.map((e, i) => (
          <g key={e.seq} onClick={() => s.selectNode(e.node_id, e.seq)} onMouseEnter={() => setHov(e.seq)} onMouseLeave={() => setHov(null)} className={s.selectedSeq === e.seq ? 'bar--sel' : ''}>
            <rect x={x0 + i * bw + 1} y={H - 24 - (e.delay_d / max) * (H - 48)} width={Math.max(2, bw - 2)} height={(e.delay_d / max) * (H - 48)} className={`bar bar--${e.channel}`} />
            <text x={x0 + i * bw + bw / 2} y={H - 8} textAnchor="middle" className="axis-chip">{e.seq + 1}</text>
          </g>
        ))}
        {hovEv && (
          <g pointerEvents="none">
            <line x1={hovX} x2={hovX} y1={20} y2={H - 24} className="crosshair" />
            <text x={Math.min(Math.max(hovX, 78), W - 78)} y={12} textAnchor="middle" className="axis-chip axis-chip--val">{hovEv.delay_d.toFixed(1)}d · {fmtDay(hovEv.at)} · step {hovEv.seq + 1} · {labelWord(hovEv.channel)}</text>
          </g>
        )}
        {/* right-edge value chips — amber HIGH chip at the top,
            selection-blue LAST chip tracking the final seq's delay. */}
        {ohlc && (
          <g pointerEvents="none">
            <rect x={W - 38} y={4} width={34} height={13} className="chip-max" />
            <text x={W - 21} y={13.5} textAnchor="middle" className="chip-max-tx">{ohlc.h.toFixed(1)}</text>
            <rect x={W - 38} y={Math.min(Math.max(lastY - 6.5, 20), H - 40)} width={34} height={13} className="chip-last" />
            <text x={W - 21} y={Math.min(Math.max(lastY - 6.5, 20), H - 40) + 9.5} textAnchor="middle" className="chip-last-tx">{ohlc.c.toFixed(1)}</text>
          </g>
        )}
      </svg>
      <div className="chart-foot mono">
        <span className="chart-legend">{[...new Set(evs.map((e) => e.channel))].map((c) => <span key={c} className={`lg lg--${c}`}>{labelWord(c)}</span>)}</span>
      </div>
    </div>
  );
}

function ChainNodeCard({ data }: { data: { ev: CausalEvent; selected: boolean; lens: LensState } }) {
  const s = useDeskStore();
  const e = data.ev;
  return (
    <div className={`dag-node ${data.selected ? 'dag-node--sel' : ''} ${data.lens === 'off' ? 'lens-off' : ''}`} onClick={() => s.selectNode(e.node_id, e.seq)}>
      <Handle type="target" position={Position.Top} />
      <span className="order-badge">{e.seq + 1}</span>
      <b>{nodeName(e.node_id)}{data.lens === 'origin' && <span className="origin-tag mono">Origin</span>}</b>
      <span className="mono dim">{labelWord(e.channel)} · +{e.delay_d.toFixed(1)} d</span>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
const nodeTypes = { chain: ChainNodeCard };
/** React Flow's built-in screen-reader strings say node / edge; the chain
 *  diagram speaks of places, legs and the causal chain instead. */
const CHAIN_ARIA = {
  'node.a11yDescription.default': 'Press enter or space to select this step.',
  'node.a11yDescription.keyboardDisabled': 'Press enter or space to select this step.',
  'node.a11yDescription.ariaLiveMessage': ({ direction }: { direction: string; x: number; y: number }) => `Moved step ${direction}.`,
  'edge.a11yDescription.default': 'Press enter or space to select this leg.',
  'controls.ariaLabel': 'Diagram controls',
  'controls.zoomIn.ariaLabel': 'Zoom in',
  'controls.zoomOut.ariaLabel': 'Zoom out',
  'controls.fitView.ariaLabel': 'Fit view',
  'controls.interactive.ariaLabel': 'Toggle interaction',
  'minimap.ariaLabel': 'Overview',
  'handle.ariaLabel': 'Connection point',
};

export function ChainPanel() {
  const s = useDeskStore();
  useChainExportables(s.chain ?? null);
  useChainReport(useMemo(() => (s.chain ? { chain: s.chain, label: s.runMeta?.label ?? null, mode: s.runMeta?.mode ?? null, spec: s.runMeta?.spec ?? null, asOf: null } : null), [s.chain, s.runMeta]), 'chain');
  const lens = useActiveLens();
  const { nodes, edges } = useMemo(() => {
    const evs = s.chain?.events ?? [];
    const depth = new Map<number, number>();
    evs.forEach((e) => depth.set(e.seq, e.parent_id === null ? 0 : (depth.get(e.parent_id) ?? 0) + 1));
    const perDepth = new Map<number, number>();
    const nodes: Node[] = evs.map((e) => {
      const d = depth.get(e.seq) ?? 0; const x = (perDepth.get(d) ?? 0); perDepth.set(d, x + 1);
      return { id: String(e.seq), type: 'chain', position: { x: x * 190, y: d * 118 },
        data: { ev: e, selected: s.selectedSeq === e.seq, lens: lensStateFor(lens, e) } };
    });
    const edges: Edge[] = (s.chain?.edges ?? []).filter((e) => e.from_seq !== null)
      .map((e, i) => ({ id: `e${i}`, source: String(e.from_seq), target: String(e.to_seq), label: labelWord(e.channel), animated: false,
        /* React Flow's default label reads "Edge from 0 to 1": house vocabulary says legs, 1-based like the step badges. */
        ariaLabel: `Leg from step ${(e.from_seq ?? 0) + 1} to step ${e.to_seq + 1}` }));
    return { nodes, edges };
  }, [s.chain, s.selectedSeq, lens]);
  return (
    <Panel id="chain" code="CHN" title="Chain trace — Causal chain" updated={s.chain ? `${s.chain.event_count} events` : undefined} action={<ChainBriefAction />}
      exportSpec={{ csv: () => (s.chain?.events ?? []).map((e) => ({ ...e })), json: () => s.chain,
        /* the chain payload serves no retrieved_at — the field
         * is omitted rather than stamped with export time. */
        pack: () => ({ data: s.chain, provenance: prov('SupermAP engine — loaded chain', undefined, s.runMeta ? runWord(s.runMeta, s.health, s.snapshotMode, Date.now()) : 'MODELED') as PackProvenance }) }}>
      {!s.chain ? <EmptyChain /> : (
        <>
          <ChainLineageChip runId={s.chain.run_id} />
          <LensBar />
          <div className="dag-wrap">
            <ReactFlow key={s.chain.run_id} nodes={nodes} edges={edges} nodeTypes={nodeTypes} ariaLabelConfig={CHAIN_ARIA} aria-label="Causal chain diagram" fitView maxZoom={1} minZoom={0.2} proOptions={{ hideAttribution: true }}>
              <Background gap={16} /><HideDecor /><Controls showInteractive={false} /><FitOnResize runId={s.chain.run_id} />
            </ReactFlow>
          </div>
          <ChainTable />
          <LensFooter />
        </>
      )}
    </Panel>
  );
}

/** Right rail, bottom: linear trace of the chain.
 *  Head stamp `LINK {layout} · {run_id}` (the layout is this build's
 *  link group — panels link through selectNode) + a totals footer with
 *  served values only: TOTAL = the worst root-to-leaf cumulative delay
 *  along served chain parent links; TEU as derived below; replays
 *  byte-identical is an engine property. Step titles stay
 *  node-first (the chain serves no editorial clauses); the
 *  sub line reads: channel · mechanism · mag. */
/** An empty causal-chain pane: one quiet state chip and the run action, never a bare box. */
export function EmptyChain() {
  const sim = useSimStore();
  return (
    <div className="empty-state empty-state--act">
      <Chip kind="meta" word="No chain" />
      <Button size="sm" onClick={() => void sim.showDeskMode('CANNED')}>Run replay</Button>
    </div>
  );
}

/** The grid behind the diagram is decoration: React Flow renders it as a bare <svg> with no way to pass attributes,
 *  so it is hidden from assistive technology here. */
function HideDecor() {
  const dom = useFlowStore((st) => st.domNode);
  useEffect(() => {
    dom?.querySelectorAll('.react-flow__background').forEach((el) => el.setAttribute('aria-hidden', 'true'));
    /* the zoom controls panel is a labelled <div>: give the label a role to hang on */
    dom?.querySelectorAll('.react-flow__controls').forEach((el) => el.setAttribute('role', 'group'));
  });
  return null;
}

/** Fit the diagram to its pane on mount and whenever the pane resizes (dock settle, window drag, preset change),
 *  so the right-most step is never cut off. */
function FitOnResize({ runId }: { runId: string }) {
  const rf = useReactFlow();
  const size = useFlowStore((st) => `${Math.round(st.width)}x${Math.round(st.height)}`);
  useEffect(() => {
    const t = window.setTimeout(() => {
      /* Fit to the pane, but never below the zoom at which step text stays legible:
       * a pane too small for the whole chain shows its origin end at readable size and pans. */
      const nodes = rf.getNodes();
      const [w, h] = size.split('x').map(Number);
      if (nodes.length === 0 || !(w > 0) || !(h > 0)) { void rf.fitView({ padding: 0.14, maxZoom: 1, duration: 0 }); return; }
      void rf.setViewport(chainFitViewport(getNodesBounds(nodes), w, h), { duration: 0 });
    }, 60);
    return () => window.clearTimeout(t);
  }, [rf, size, runId]);
  return null;
}

export function ChainTracePanel() {
  const s = useDeskStore();
  useChainReport(useMemo(() => (s.chain ? { chain: s.chain, label: s.runMeta?.label ?? null, mode: s.runMeta?.mode ?? null, spec: s.runMeta?.spec ?? null, asOf: null } : null), [s.chain, s.runMeta]), 'chaintrace');
  const lens = useActiveLens();
  const total = useMemo(() => {
    if (!s.chain) return 0;
    const cum = new Map<number, number>();
    let best = 0;
    for (const e of s.chain.events) {
      const c = e.delay_d + (e.parent_id !== null ? (cum.get(e.parent_id) ?? 0) : 0);
      cum.set(e.seq, c);
      best = Math.max(best, c);
    }
    return best;
  }, [s.chain]);
  const teu = useMemo(() => {
    if (!s.chain) return null;
    const byId = new Map(gNodes(s.graph).map((n) => [n.id, n]));
    let sum = 0; let ports = 0;
    for (const id of new Set(s.chain.events.map((e) => e.node_id))) {
      const n = byId.get(id);
      if (n && n.capacity_unit === 'TEU/day' && typeof n.capacity === 'number') { sum += n.capacity; ports += 1; }
    }
    return ports ? sum : null;
  }, [s.chain, s.graph]);
  return (
    <Panel id="chaintrace" code="TRC" title="Chain — Domino trace" updated={s.chain ? `${s.layout} layout · ${runRef(s.chain, s.graph)}` : 'No chain'}>
      <LensBar />
      <ol className="trace">
        {(s.chain?.events ?? []).map((e) => {
          const ls = lensStateFor(lens, e);
          return (
          <li key={e.seq} className={`${s.selectedSeq === e.seq ? 'trace--sel' : ''} ${ls === 'off' ? 'lens-off' : ''}`} onClick={() => s.selectNode(e.node_id, e.seq)}>
            <span className={`order-badge bl--${e.channel}`}>{e.seq + 1}</span>
            <span className="trace-main"><b>{nodeName(e.node_id)}{ls === 'origin' && <span className="origin-tag mono">Origin</span>}</b>
            <small className="mono">{labelWord(e.channel)} · {e.mechanism} · magnitude {e.magnitude.toFixed(2)}</small></span>
            <span className="trace-delay mono">+{e.delay_d.toFixed(1)}d</span>
          </li>);
        })}
      </ol>
      {!s.chain && <EmptyChain />}
      {s.chain && (
        <p className="trace-foot mono">Total +{total.toFixed(1)} d{teu !== null ? ` · ${teu >= 1000 ? `${Math.round(teu / 1000)}K` : Math.round(teu)} TEU in path` : ''} · Deterministic</p>
      )}
      <LensFooter />
    </Panel>
  );
}
