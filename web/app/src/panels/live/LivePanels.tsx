import { SourceLine } from '../../sources/SourceLine';
import React, { useMemo, useState } from 'react';
import { Panel } from '../../components/Panel';
import { useStore } from '../../store';
import { MetaTag, ProvChip } from '../ports/chips';
import { getJson } from '../ports/http';
import { useAsync, useVisibleTick } from '../ports/useAsync';
import {
  STRIP_CREDIT, ageText, countsByKind, entitiesQuery, entityChip, entityRows, filterEntities, fmtCoord, fmtHeading, fmtSpeed, mergeFeedStrip,
  nextEntitySort, sortEntities, type EntitiesDoc, type EntitySort, type EntityRow, type FeedStripEntry,
} from './model';
import { comtradeGroups, contextMode, eiaView, fmtKbbl, fmtPrice, fmtShare, type ContextDoc } from './context';
import { connKind, connLabel, filterTape, type TapeItem } from './stream';
import { useTape, type LiveData } from './useLive';
import './live.css';
import { labelWord } from '../../copy';
import { failureSentence } from '../failureText';
import { feedName } from '../ports/model';
import { Icon } from '../../ui/Icon';
import { Tabs } from '../../ui/Controls';
import { liveChipKind } from '../../shell/truthLabel';

const hhmm = (iso: string): string => (iso.length >= 16 ? `${iso.slice(11, 16)}Z` : '');
const dayhhmm = (iso: string): string => (iso.length >= 16 ? `${iso.slice(5, 10)} ${iso.slice(11, 16)}Z` : '');

/* ── feed-health strip ──────────────────────────────────────────────── */

export function FeedStrip({ entries, error }: { entries: FeedStripEntry[]; error: boolean }) {
  return (
    <>
    <div className="lv-strip" role="list" aria-label="Feed health">
      {error && entries.length === 0 && <ProvChip kind="DOWN" label="Live health down" />}
      {entries.map((e) => (
        <span role="listitem" key={e.id} className={`lv-feed lv-feed--${e.state.toLowerCase().replace(' ', '-')}`} title={e.title}>
          <ProvChip kind={e.chip} label={`${e.label} · ${labelWord(e.state)}`} asOf={e.state === 'SNAPSHOT' && e.retrievedAt ? e.retrievedAt.slice(5, 10) : null} />
        </span>
      ))}
    </div>
    {entries.length > 0 && (
      <details className="lv-sources">
        <summary className="mono small">Sources</summary>
        <SourceLine ids={entries.map((e) => STRIP_CREDIT[e.id]).filter((x): x is string => !!x)} />
      </details>
    )}
    </>
  );
}

/* ── LVF: counts, entities ──────────────────────────────────────────── */

type Tab = 'vessel' | 'aircraft' | 'air_hub';

function SortTh({ k, cur, onSort, children, num }: { k: EntitySort; cur: { key: EntitySort; order: 'asc' | 'desc' }; onSort: (k: EntitySort) => void; children: React.ReactNode; num?: boolean }) {
  const on = cur.key === k;
  return (
    <th scope="col" aria-sort={on ? (cur.order === 'asc' ? 'ascending' : 'descending') : 'none'} className={num ? 'pt-num' : undefined}>
      <button className="pt-th" onClick={() => onSort(k)}>{children}{on ? <Icon name={cur.order === 'asc' ? 'chevron-up' : 'chevron-down'} size="xs" /> : null}</button>
    </th>
  );
}

function EntityTable({ rows, kind }: { rows: EntityRow[]; kind: 'vessel' | 'aircraft' }) {
  const [q, setQ] = useState('');
  const [sort, setSort] = useState<{ key: EntitySort; order: 'asc' | 'desc' }>({ key: 'age', order: 'asc' });
  const shown = useMemo(() => sortEntities(filterEntities(rows, q), sort.key, sort.order), [rows, q, sort]);
  return (
    <>
      <div className="pt-filters">
        <input className="pt-input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={kind === 'vessel' ? 'MMSI or name' : 'ICAO24 or callsign'} aria-label="Search entities" />
        <span className="mono small dim">{shown.length} of {rows.length}</span>
      </div>
      <div className="lv-scroll">
        <table className="pt-table lv-table" aria-label={kind === 'vessel' ? 'Live vessels' : 'Live aircraft'}>
          <thead>
            <tr>
              <SortTh k="name" cur={sort} onSort={(k) => setSort((c) => nextEntitySort(c, k))}>{kind === 'vessel' ? 'Vessel' : 'Aircraft'}</SortTh>
              <SortTh k="lat" cur={sort} onSort={(k) => setSort((c) => nextEntitySort(c, k))} num>Lat</SortTh>
              <SortTh k="lon" cur={sort} onSort={(k) => setSort((c) => nextEntitySort(c, k))} num>Lon</SortTh>
              <SortTh k="spd" cur={sort} onSort={(k) => setSort((c) => nextEntitySort(c, k))} num>Speed, kn</SortTh>
              <th scope="col" className="pt-num lv-wide"><span className="pt-th pt-th--plain">Heading</span></th>
              <SortTh k="age" cur={sort} onSort={(k) => setSort((c) => nextEntitySort(c, k))} num>Age</SortTh>
              <th scope="col"><span className="pt-th pt-th--plain">Source</span></th>
            </tr>
          </thead>
          <tbody>
            {shown.slice(0, 300).map((r) => (
              <tr key={r.id} className="pt-row">
                <td className="lv-id"><b>{r.name || r.id}</b>{r.name ? <span className="mono dim small">{r.id}</span> : null}</td>
                <td className="pt-c-num mono">{fmtCoord(r.lat, 'lat')}</td>
                <td className="pt-c-num mono">{fmtCoord(r.lon, 'lon')}</td>
                <td className="pt-c-num mono">{r.spd === null ? <ProvChip kind="NOT SERVED" label="Not served" /> : fmtSpeed(r.spd)}</td>
                <td className="pt-c-num mono lv-wide">{r.hdg === null ? <ProvChip kind="NOT SERVED" label="Not served" /> : fmtHeading(r.hdg)}</td>
                <td className="pt-c-num mono">{ageText(r.ageS)}</td>
                <td><ProvChip kind={entityChip(r, Date.now())} title={`${r.source} · retrieved ${r.retrievedAt ?? ''}`} /></td>
              </tr>
            ))}
            {shown.length === 0 && <tr><td colSpan={7} className="pt-state"><ProvChip kind="NOT SERVED" label="No entities" /></td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

function EntitiesSection({ kind, tick }: { kind: 'vessel' | 'aircraft'; tick: number }) {
  const { data, error } = useAsync<EntitiesDoc>((signal) => getJson<EntitiesDoc>(`/live/entities?${entitiesQuery(kind)}`, { signal }), kind, true, tick);
  const rows = useMemo(() => entityRows(data), [data]);
  if (error && !data) return <p className="pt-state"><ProvChip kind="DOWN" label="Entities down" title={failureSentence(error.code, error.status)} /></p>;
  if (data && !data.served) return <p className="pt-state"><ProvChip kind="NOT SERVED" title={data.reason ? labelWord(data.reason) : undefined} /></p>;
  if (!data) return <p className="pt-state mono small dim">Loading</p>;
  return (
    <>
      <div className="lv-meta mono small">
        <span>Held {data.held ?? '—'}</span><span>Matched {data.matched ?? '—'}</span>
        {data.truncated && <MetaTag title="Row limit">Showing {data.count}</MetaTag>}
      </div>
      <EntityTable rows={rows} kind={kind} />
    </>
  );
}

export function LiveFeedPanel({ data }: { data: LiveData }) {
  const [tick] = useVisibleTick(10000);
  const [tab, setTab] = useState<Tab | null>(null);
  const strip = useMemo(() => mergeFeedStrip(data.health?.feeds, data.ctxFeeds), [data.health, data.ctxFeeds]);
  const counts = useMemo(() => countsByKind(data.snapshot), [data.snapshot]);
  const snap = data.snapshot;
  /* a tab exists only for a kind whose feed is served; the rest stay as NOT SERVED counts */
  const tabs: Array<{ id: Tab; label: string }> = [];
  if (snap?.vessels?.served) tabs.push({ id: 'vessel', label: 'Vessels' });
  if (snap?.aircraft?.served) tabs.push({ id: 'aircraft', label: 'Aircraft' });
  if (snap?.aircraft?.hubs?.served) tabs.push({ id: 'air_hub', label: 'Air hubs' });
  const active: Tab | null = tabs.find((t) => t.id === tab)?.id ?? tabs[0]?.id ?? null;
  const hubs = snap?.aircraft?.hubs;
  return (
    <Panel id="livefeed" code="LVF" title="Live entities" updated={snap ? `As of ${hhmm(snap.generated_at)}` : undefined}>
      <div className="lv-root">
        <FeedStrip entries={strip} error={!!data.healthError} />
        <div className="lv-counts" role="list" aria-label="Counts by kind">
          {counts.map((c) => (
            <span role="listitem" className="lv-count" key={c.kind} title={c.title || undefined}>
              <span className="lv-count-label">{c.label}</span>
              <b className="mono">{c.count === null ? <ProvChip kind="NOT SERVED" /> : c.count.toLocaleString('en-US')}</b>
              {c.count !== null && <ProvChip kind={c.chip} />}
              {c.count !== null && c.sample && <MetaTag title="Reports received">Sample, not a census</MetaTag>}
            </span>
          ))}
        </div>
        {tabs.length > 0 && (
          <Tabs label="Entity kind" className="lv-tabs" value={active} onChange={setTab} options={tabs.map((t) => ({ value: t.id, label: t.label }))} />
        )}
        {active === 'vessel' && <EntitiesSection kind="vessel" tick={tick} />}
        {active === 'aircraft' && <EntitiesSection kind="aircraft" tick={tick} />}
        {active === 'air_hub' && hubs && (
          <div className="lv-scroll">
            <table className="pt-table lv-table" aria-label="Air hubs">
              <thead><tr><th scope="col"><span className="pt-th pt-th--plain">Hub</span></th><th scope="col" className="pt-num"><span className="pt-th pt-th--plain">Aircraft in box</span></th><th scope="col" className="pt-num"><span className="pt-th pt-th--plain">Age</span></th><th scope="col"><span className="pt-th pt-th--plain">Source</span></th></tr></thead>
              <tbody>
                {hubs.items.map((h) => {
                  const pr = hubs.provenance?.[h.p ?? 0];
                  return (
                    <tr key={h.node_id} className="pt-row">
                      <td className="lv-id"><b>{h.node_id.replace(/^air_/, '').toUpperCase()}</b></td>
                      <td className="pt-c-num mono">{h.aircraft_in_bbox}</td>
                      <td className="pt-c-num mono">{ageText(h.age_s)}</td>
                      <td><ProvChip kind={liveChipKind(pr?.mode, pr?.retrieved_at, pr?.source, Date.now())} title={pr ? `${pr.source} · ${pr.retrieved_at}` : undefined} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Panel>
  );
}

/* ── LVT: event / alert tape ────────────────────────────────────────── */

function TapeRow({ it, label }: { it: TapeItem; label: string }) {
  const mode = liveChipKind(it.mode, it.retrievedAt, it.source, Date.now());
  return (
    <li className={`lv-tape-row${it.ended ? ' lv-tape-row--ended' : ''}`}>
      <span className="mono lv-time" title={it.at}>{dayhhmm(it.at)}</span>
      {it.kind === 'health'
        ? <MetaTag>Feed</MetaTag>
        : <span className="pt-sev mono" title="Out of 5">Severity {it.severity ?? '—'}</span>}
      <span className="lv-tape-title">
        {it.citation ? <a href={it.citation.url} target="_blank" rel="noopener noreferrer" title={`${it.citation.publisher} · ${it.citation.title}`}>{it.title}</a> : it.title}
        {label && <span className="mono dim small"> {label}</span>}
      </span>
      {it.ended && <MetaTag title="Ended">Ended</MetaTag>}
      <ProvChip kind={mode} title={it.source} />
    </li>
  );
}

export function LiveTapePanel({ data }: { data: LiveData }) {
  const s = useStore();
  const [paused, setPaused] = useState(false);
  const [minSev, setMinSev] = useState(0);
  const { state, hidden } = useTape(true, paused);
  const names = useMemo(() => new Map((s.graph.nodes as unknown as Array<{ id: string; name?: string }>).map((n) => [n.id, n.name ?? n.id] as const)), [s.graph]);
  const rows = useMemo(() => filterTape(state.items, { minSeverity: minSev, kinds: [] }), [state.items, minSev]);
  const evFeeds = (data.health?.feeds ?? []).filter((f) => f.topic === 'events');
  /* both event feeds down and nothing buffered: the tape is not rendered (the strip keeps the DOWN chips) */
  if (evFeeds.length > 0 && evFeeds.every((f) => f.state !== 'LIVE' && f.state !== 'SNAPSHOT') && state.items.length === 0) return null;
  return (
    <Panel id="livetape" code="LVT" title="Live tape" updated={state.lastHeartbeatAt ? `Heartbeat ${hhmm(state.lastHeartbeatAt)}` : undefined}>
      <div className="lv-root">
        <div className="pt-filters">
          <ProvChip kind={connKind(state.conn)} label={connLabel(state.conn, hidden)} />
          <button className="pt-btn" aria-pressed={paused} onClick={() => setPaused((p) => !p)}>{paused ? 'Resume' : 'Pause'}</button>
          <select className="pt-input pt-select pt-select--sm" aria-label="Minimum severity" value={minSev} onChange={(e) => setMinSev(Number(e.target.value))}>
            <option value={0}>All severities</option>
            {[2, 3, 4, 5].map((n) => <option key={n} value={n}>Severity {n}+</option>)}
          </select>
          <span className="mono small dim" aria-live="off">{rows.length} / {state.items.length} · Buffer {state.max}</span>
          {state.counters.gaps + state.counters.resets > 0 && <MetaTag title="Stream">Resync {state.counters.gaps + state.counters.resets}</MetaTag>}
          {evFeeds.map((f) => <ProvChip key={f.id} kind={f.state === 'LIVE' ? 'LIVE' : f.state === 'SNAPSHOT' ? 'SNAPSHOT' : 'DOWN'} label={`${feedName(f.id)} · ${labelWord(f.state)}`} />)}
        </div>
        <ul className="lv-tape" aria-label="Event and alert tape">
          {rows.slice(0, 120).map((it) => <TapeRow key={it.key} it={it} label={it.nodeId ? (names.get(it.nodeId) ?? it.nodeId) : ''} />)}
          {rows.length === 0 && <li className="pt-state"><ProvChip kind="NOT SERVED" label="No records" /></li>}
        </ul>
      </div>
    </Panel>
  );
}

/* ── LVC: EIA / Comtrade context ────────────────────────────────────── */

export function ContextRows({ doc, which }: { doc: ContextDoc; which: 'eia' | 'comtrade' }) {
  const [chapter, setChapter] = useState<string | null>(null);
  if (which === 'eia') {
    const v = eiaView(doc);
    return (
      <>
        {v.spot.length > 0 && (
          <table className="pt-table lv-table" aria-label="EIA spot prices">
            <thead><tr><th scope="col"><span className="pt-th pt-th--plain">Series</span></th><th scope="col" className="pt-num"><span className="pt-th pt-th--plain">Latest</span></th><th scope="col"><span className="pt-th pt-th--plain">Source</span></th></tr></thead>
            <tbody>
              {v.spot.map((r) => (
                <tr key={r.id} className="pt-row"><td className="lv-id"><b>{r.label}</b><span className="mono dim small">{r.period}</span></td>
                  <td className="pt-c-num mono"><b>{fmtPrice(r.value)}</b> <span className="dim small">{r.units}</span></td>
                  <td><ProvChip kind="SOURCED" asOf={r.retrievedAt.slice(5, 10)} title={`${r.source} · ${r.mode}`} /></td></tr>
              ))}
            </tbody>
          </table>
        )}
        {v.origins.length > 0 && (
          <>
            <h5>US crude imports by origin <span className="mono dim">{v.originPeriod}</span></h5>
            <table className="pt-table lv-table" aria-label="EIA crude imports by origin">
              <thead><tr><th scope="col"><span className="pt-th pt-th--plain">Origin</span></th><th scope="col" className="pt-num"><span className="pt-th pt-th--plain">Kbbl / month</span></th><th scope="col"><span className="pt-th pt-th--plain">Source</span></th></tr></thead>
              <tbody>
                {v.origins.map((r) => (
                  <tr key={r.id} className="pt-row"><td className="lv-id"><b>{r.name}</b>{r.aggregate ? <MetaTag title="EIA grouping">Aggregate</MetaTag> : null}</td><td className="pt-c-num mono">{fmtKbbl(r.quantityKbbl)}</td>
                    <td><ProvChip kind="SOURCED" asOf={r.retrievedAt.slice(5, 10)} title={`${r.source} · ${r.mode}`} /></td></tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        {v.spot.length === 0 && v.origins.length === 0 && <p className="pt-state"><ProvChip kind="NOT SERVED" label="No records" /></p>}
      </>
    );
  }
  const groups = comtradeGroups(doc);
  if (groups.length === 0) return <p className="pt-state"><ProvChip kind="NOT SERVED" label="No records" /></p>;
  const g = groups.find((x) => x.chapter === chapter) ?? groups[0];
  return (
    <>
      <Tabs label="Goods class (HS chapter)" className="lv-tabs" value={g.chapter} onChange={setChapter} options={groups.map((x) => ({ value: x.chapter, label: `HS ${x.chapter}`, title: x.name }))} />
      <h5>{labelWord(g.name)}</h5>
      <div className="lv-scroll lv-scroll--sm">
        <table className="pt-table lv-table" aria-label={`Comtrade HS ${g.chapter}`}>
          <thead><tr><th scope="col"><span className="pt-th pt-th--plain">Reporter → partner</span></th><th scope="col"><span className="pt-th pt-th--plain">Flow</span></th><th scope="col"><span className="pt-th pt-th--plain">Year</span></th><th scope="col"><span className="pt-th pt-th--plain">Measure</span></th><th scope="col" className="pt-num"><span className="pt-th pt-th--plain">Share</span></th><th scope="col"><span className="pt-th pt-th--plain">Source</span></th></tr></thead>
          <tbody>
            {g.rows.slice(0, 60).map((r) => (
              <tr key={r.key} className="pt-row"><td className="lv-id"><b>{r.reporter}</b><span className="mono dim small"> → {r.partner}</span></td><td className="mono small">{labelWord(r.flow)}</td><td className="mono">{r.period}</td>
                <td className="mono small" title={r.basis}>{r.measure === 'partner_share' ? 'Partner share' : 'Chapter share'}</td>
                <td className="pt-c-num mono">{fmtShare(r.share)}</td>
                <td><ProvChip kind="SOURCED" asOf={r.retrievedAt.slice(5, 10)} title={`${r.source} · ${r.mode}`} /></td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <SourceLine ids={['un-comtrade']} />
    </>
  );
}

export function LiveContextPanel({ data }: { data: LiveData }) {
  const [slow] = useVisibleTick(300000);
  const eiaState = data.ctxFeeds?.find((f) => f.feed === 'eia');
  const ctState = data.ctxFeeds?.find((f) => f.feed === 'comtrade');
  const eiaDown = !eiaState || eiaState.state === 'DOWN';
  const ctDown = !ctState || ctState.state === 'DOWN';
  const eia = useAsync<ContextDoc>((signal) => getJson<ContextDoc>('/feeds/eia', { signal }), 'eia', !eiaDown, slow);
  const ct = useAsync<ContextDoc>((signal) => getJson<ContextDoc>('/feeds/comtrade', { signal }), 'comtrade', !ctDown, slow);
  if (eiaDown && ctDown) return null; // both feeds down: the section is not rendered (the strip keeps the DOWN chips)
  return (
    <Panel id="livectx" code="LVC" title="Context · EIA, Comtrade">
      <div className="lv-root">
        {!eiaDown && (
          <section aria-label="EIA">
            <h5>EIA <ProvChip kind={contextMode(eiaState?.mode, eiaState?.provenance?.retrieved_at, Date.now(), eiaState?.provenance?.source) === 'LIVE' ? 'LIVE' : 'SNAPSHOT'} asOf={eiaState?.provenance?.retrieved_at?.slice(5, 10)} /> <ProvChip kind="SOURCED" /></h5>
            {eia.data ? <ContextRows doc={eia.data} which="eia" /> : eia.error ? <ProvChip kind="DOWN" title={failureSentence(eia.error.code, eia.error.status)} /> : <p className="mono small dim">Loading</p>}
          </section>
        )}
        {!ctDown && (
          <section aria-label="Comtrade">
            <h5>Comtrade <ProvChip kind={contextMode(ctState?.mode, ctState?.provenance?.retrieved_at, Date.now(), ctState?.provenance?.source) === 'LIVE' ? 'LIVE' : 'SNAPSHOT'} asOf={ctState?.provenance?.retrieved_at?.slice(5, 10)} /> <ProvChip kind="SOURCED" /></h5>
            {ct.data ? <ContextRows doc={ct.data} which="comtrade" /> : ct.error ? <ProvChip kind="DOWN" title={failureSentence(ct.error.code, ct.error.status)} /> : <p className="mono small dim">Loading</p>}
          </section>
        )}
      </div>
    </Panel>
  );
}

