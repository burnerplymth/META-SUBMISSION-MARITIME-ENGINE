import { SourceLine } from '../../sources/SourceLine';
import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { LiveStatus } from '../../ui/LiveStatus';
import { Panel } from '../../components/Panel';
import { useStore } from '../../store';
import { CellBox, MetaTag, ProvChip } from './chips';
import { PortDrawer } from './PortDrawer';
import { getJson } from './http';
import { useAsync, useVisibleTick } from './useAsync';
import {
  INITIAL_BOARD, KINDS, KIND_WORD, TIER_TITLE, MODES, PAGE_SIZES, REGIONS, STATUS_MARK, STATUS_LABELS, TIERS, TIER_WORD,
  activeFilterCount, ariaSort, buildPortsQuery, exportRows, feedChip, nextSort, overlayChoices, overlayTickFor, pageInfo, reconcileOverlay,
  rowView, sanitiseSort, statusKey, summaryCounts, toggleIn, withFilters,
  type BoardState, type PortsDoc, type PortsSummary, type RowView, type SortKey,
} from './model';
import { labelWord } from '../../copy';
import { failureSentence } from '../failureText';
import { Icon } from '../../ui/Icon';
import { ToggleGroup } from '../../ui/Controls';
import { StateMark } from '../../ui/Status';
import { useExportContextMenu } from '../../exports/PanelExportMenu';

/* PORTS board (PRT): a dense, sortable, filterable table over GET /ports.
 * Server-side sort / filter / paging (the board is 992 places); the
 * summary strip is GET /ports/summary; a row click selects the place
 * through the shared bus (map, chain and inspector follow) and opens
 * the detail drawer. No explanatory prose: chips and state words only. */

const Th = ({ board, k, children, onSort, className }: { board: BoardState; k: SortKey; children: React.ReactNode; onSort: (k: SortKey) => void; className?: string }) => {
  const dir = ariaSort(board, k);
  return (
    <th scope="col" aria-sort={dir} className={className}>
      <button className="pt-th" onClick={() => onSort(k)} title="Sort">
        {children}{dir === 'ascending' ? <Icon name="chevron-up" size="xs" /> : dir === 'descending' ? <Icon name="chevron-down" size="xs" /> : null}
      </button>
    </th>
  );
};

const Row = memo(function Row({ r, selected, overlay, onPick, onKey }: {
  r: RowView; selected: boolean; overlay: boolean; onPick: (id: string) => void; onKey: (e: React.KeyboardEvent<HTMLButtonElement>) => void;
}) {
  return (
    <tr className={selected ? 'pt-row pt-row--sel' : 'pt-row'} onClick={() => onPick(r.id)} aria-selected={selected}>
      <td className="pt-c-status">
        <span className={`pt-status pt-status--${r.status.key}`} title={r.status.title}><StateMark name={r.status.mark} size={9} />{labelWord(r.status.label)}</span>
      </td>
      <td className="pt-c-place">
        <button className="pt-place" onClick={(e) => { e.stopPropagation(); onPick(r.id); }} onKeyDown={onKey} data-place={r.id}>{r.name}</button>
        <span className="pt-sub"><MetaTag title={r.tierTitle} strong={r.tier === 'core'}>{r.tierWord}</MetaTag><span className="mono">{r.kindWord}</span>{r.country ? <span className="pt-country">{r.country}</span> : null}</span>
      </td>
      <td className="pt-c-num"><CellBox v={r.capacity} /></td>
      <td className="pt-c-num"><CellBox v={r.signal} /></td>
      <td className="pt-c-num pt-wide"><CellBox v={r.ratio} /></td>
      <td className="pt-c-num pt-wide"><CellBox v={r.events} /></td>
      <td className="pt-c-region pt-wide"><span className="mono small">{labelWord(r.region)}</span></td>
      {overlay && <td className="pt-c-num"><CellBox v={r.overlay!} />{r.overlayDetail ? <small className="pt-kv-extra">{r.overlayDetail}</small> : null}</td>}
    </tr>
  );
});

export function PortsPanel() {
  const s = useStore();
  const tableMenu = useExportContextMenu('ports:table');
  const [board, setBoard] = useState<BoardState>(INITIAL_BOARD);
  const [qInput, setQInput] = useState('');
  const [drawerId, setDrawerId] = useState<string | null>(null);
  const [tick, refresh] = useVisibleTick(60000);

  /* overlay: only a loaded run can be overlaid; a closed run falls back */
  const chainRunId = s.chain?.run_id ?? null;
  const simRunId = s.simActive && s.simRun ? s.simRun.sim_run_id : null;
  const heldTick = useRef(s.simTick);
  const simTick = overlayTickFor(s.simPlaying, s.simTick, heldTick.current);
  heldTick.current = simTick;
  const choices = useMemo(() => overlayChoices(chainRunId, simRunId, simTick), [chainRunId, simRunId, simTick]);
  const effective = useMemo(() => sanitiseSort({ ...board, overlay: reconcileOverlay(board.overlay, chainRunId, simRunId, simTick) }), [board, chainRunId, simRunId, simTick]);
  const query = buildPortsQuery(effective);
  const overlayType = effective.overlay.type;

  /* debounce the search box into the filter */
  useEffect(() => {
    const t = window.setTimeout(() => setBoard((b) => (b.filters.q === qInput ? b : withFilters(b, { q: qInput }))), 250);
    return () => window.clearTimeout(t);
  }, [qInput]);

  const list = useAsync<PortsDoc>((signal) => getJson<PortsDoc>(`/ports?${query}`, { signal }), query, true, tick);
  const summary = useAsync<PortsSummary>((signal) => getJson<PortsSummary>('/ports/summary', { signal }), 'summary', true, tick);

  const rows = useMemo(() => (list.data?.places ?? []).map((p) => rowView(p, overlayType)), [list.data, overlayType]);
  const counts = useMemo(() => summaryCounts(summary.data), [summary.data]);
  const page = pageInfo(list.data?.total ?? 0, effective.limit, effective.offset);
  const nFilters = activeFilterCount(effective.filters);

  const selectedNode = s.selectedNode;
  const selectNode = s.selectNode;
  const pick = useCallback((id: string) => { selectNode(id); setDrawerId(id); }, [selectNode]);
  /* an open drawer follows the shared selection (map click, chain row) */
  useEffect(() => { if (drawerId && selectedNode && selectedNode !== drawerId) setDrawerId(selectedNode); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [selectedNode]);

  const onKey = useCallback((e: React.KeyboardEvent<HTMLButtonElement>) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    const all = Array.from(e.currentTarget.closest('tbody')?.querySelectorAll<HTMLButtonElement>('button.pt-place') ?? []);
    const i = all.indexOf(e.currentTarget);
    const nxt = all[i + (e.key === 'ArrowDown' ? 1 : -1)];
    if (nxt) { e.preventDefault(); nxt.focus(); }
  }, []);

  const set = (patch: Partial<BoardState>) => setBoard((b) => ({ ...b, ...patch }));
  const setFilter = (k: 'mode' | 'kind' | 'tier' | 'status' | 'region', v: string) =>
    setBoard((b) => withFilters(b, { [k]: toggleIn(b.filters[k], v) }));
  const clearFilters = () => { setQInput(''); setBoard((b) => withFilters(b, { mode: [], kind: [], tier: [], region: [], status: [], q: '', hasEvents: false })); };

  const rule = list.data?.rule ?? summary.data?.rule ?? null;
  const updated = list.data?.as_of ? `As of ${list.data.as_of.slice(11, 16)}Z` : undefined;

  return (
    <Panel id="ports" code="PRT" title="Ports board" updated={updated}
      exportSpec={{ csv: () => exportRows(list.data?.places ?? []), json: () => list.data, source: 'IMF PortWatch; NOAA / NWS; GDACS; OpenSky; CBP' }}>
      <div className="pt-root">
        <LiveStatus text={list.error ? (list.data ? 'Board stale' : 'Ports down') : ''} />
        {/* ── summary strip ─────────────────────────────────────────── */}
        <div className="pt-strip" role="group" aria-label="Board summary">
          <ToggleGroup label="Status filter" size="sm" className="pt-status-tog" values={effective.filters.status} onToggle={(v) => setFilter('status', v)}
            options={counts.map((c) => ({
              value: c.label, label: labelWord(c.label), title: `Filter ${labelWord(c.label)}`, className: `pt-status--${statusKey(c.label)}`,
              content: <><StateMark name={STATUS_MARK[c.label]} size={9} /><span>{labelWord(c.label)}</span><b className="mono">{c.count === null ? <ProvChip kind="NOT SERVED" /> : c.count.toLocaleString('en-US')}</b></>,
            }))} />
          {rule && <MetaTag title="Published status rule">{String(rule.id)} v{String(rule.version)}</MetaTag>}
          {summary.data && (
            <span className="pt-tiers mono small" role="group" aria-label="Places by tier">
              {Object.entries(summary.data.by_tier).map(([k, n]) => <span key={k}>{TIER_WORD[k] ?? labelWord(k)} {n.toLocaleString('en-US')}</span>)}
            </span>
          )}
          <button className="pt-btn pt-refresh" onClick={refresh} title="Reload" aria-label="Reload board"><Icon name="refresh" size="xs" /></button>
        </div>
        <div className="pt-strip pt-feeds" role="group" aria-label="Feed health">
          {summary.error && !summary.data && <ProvChip kind="DOWN" label="Summary down" title={failureSentence(summary.error.code, summary.error.status)} />}
          {summary.error && summary.data && <ProvChip kind="DOWN" label="Summary stale" title="Showing last copy" />}
          {(summary.data?.feeds ?? []).map((f) => {
            const c = feedChip(f);
            return <ProvChip key={f.feed} kind={c.kind} label={`${c.label} · ${labelWord(c.kind)}`} title={c.title} />;
          })}
          {summary.data?.events_store && (
            <ProvChip kind={feedChip({ feed: 'events', state: summary.data.events_store.state }).kind} label={`Events · ${summary.data.events_store.active} active`}
              title={`Events store ${summary.data.events_store.records}`} />
          )}
        </div>

        <SourceLine ids={['imf-portwatch']} />

        {/* ── filters ───────────────────────────────────────────────── */}
        <div className="pt-filters" role="group" aria-label="Board filters">
          <input className="pt-input" type="search" value={qInput} onChange={(e) => setQInput(e.target.value)} placeholder="Name, ID or country" aria-label="Search places" />
          <ToggleGroup label="Mode" size="sm" values={effective.filters.mode} onToggle={(v) => setFilter('mode', v)} options={MODES.map((m) => ({ value: m, label: labelWord(m) }))} />
          <ToggleGroup label="Kind" size="sm" values={effective.filters.kind} onToggle={(v) => setFilter('kind', v)} options={KINDS.map((k) => ({ value: k, label: KIND_WORD[k] }))} />
          <ToggleGroup label="Place tier" size="sm" values={effective.filters.tier} onToggle={(v) => setFilter('tier', v)}
            options={TIERS.map((t) => ({ value: t, label: TIER_WORD[t], title: TIER_TITLE[t] }))} />
          <select className="pt-input pt-select" aria-label="Region" value={effective.filters.region[0] ?? ''} onChange={(e) => setBoard((b) => withFilters(b, { region: e.target.value ? [e.target.value] : [] }))}>
            <option value="">All regions</option>
            {REGIONS.map((r) => <option key={r} value={r}>{labelWord(r)}</option>)}
          </select>
          <ToggleGroup label="Events" size="sm" values={effective.filters.hasEvents ? ['has'] : []} onToggle={() => setBoard((b) => withFilters(b, { hasEvents: !b.filters.hasEvents }))}
            options={[{ value: 'has', label: 'Has events' }]} />
          <select className="pt-input pt-select" aria-label="Overlay run" value={effective.overlay.type}
            onChange={(e) => { const c = choices.find((x) => x.id === e.target.value); if (c) set({ overlay: c.overlay, offset: 0 }); }}>
            {choices.map((c) => <option key={c.id} value={c.id}>Overlay {c.label}</option>)}
          </select>
          <select className="pt-input pt-select pt-select--sm" aria-label="Rows per page" value={effective.limit} onChange={(e) => set({ limit: Number(e.target.value), offset: 0 })}>
            {PAGE_SIZES.map((n) => <option key={n} value={n}>{n} / page</option>)}
          </select>
          {nFilters > 0 && <button className="pt-btn" onClick={clearFilters}>Clear {nFilters}</button>}
        </div>

        {/* ── table ─────────────────────────────────────────────────── */}
        <div className={`pt-main${drawerId ? ' pt-main--drawer' : ''}`}>
          <div className="pt-scroll" tabIndex={-1}>
            {list.error && !list.data ? (
              <p className="pt-state"><ProvChip kind="DOWN" label="Ports down" title={failureSentence(list.error.code, list.error.status)} /> <span className="mono small dim">{list.error.code}</span></p>
            ) : !list.data ? (
              <p className="pt-state mono small dim">Loading</p>
            ) : (
              <><table className="pt-table" aria-label="Ports board" aria-busy={list.loading} onContextMenu={tableMenu.onContextMenu}>
                <thead>
                  <tr>
                    <Th board={effective} k="status" onSort={(k) => setBoard((b) => nextSort(b, k))} className="pt-w-s">Status</Th>
                    <Th board={effective} k="name" onSort={(k) => setBoard((b) => nextSort(b, k))}>Place</Th>
                    <Th board={effective} k="capacity" onSort={(k) => setBoard((b) => nextSort(b, k))} className="pt-num pt-w-n">Capacity</Th>
                    <Th board={effective} k="activity" onSort={(k) => setBoard((b) => nextSort(b, k))} className="pt-num pt-w-n">Signal</Th>
                    <Th board={effective} k="ratio" onSort={(k) => setBoard((b) => nextSort(b, k))} className="pt-num pt-wide pt-w-n">Ratio</Th>
                    <Th board={effective} k="events" onSort={(k) => setBoard((b) => nextSort(b, k))} className="pt-num pt-wide pt-w-n">Events</Th>
                    <th scope="col" className="pt-wide pt-w-r"><span className="pt-th pt-th--plain">Region</span></th>
                    {overlayType !== 'none' && (overlayType === 'chain'
                      ? <Th board={effective} k="chain" onSort={(k) => setBoard((b) => nextSort(b, k))} className="pt-num pt-w-o">Chain</Th>
                      : <th scope="col" className="pt-num pt-w-o"><span className="pt-th pt-th--plain">Simulation, tick {effective.overlay.type === 'sim' ? effective.overlay.tick : ''}</span></th>)}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => <Row key={r.id} r={r} selected={selectedNode === r.id} overlay={overlayType !== 'none'} onPick={pick} onKey={onKey} />)}
                  {rows.length === 0 && <tr><td colSpan={8} className="pt-state"><ProvChip kind="NOT SERVED" label="No places" /></td></tr>}
                </tbody>
              </table>{tableMenu.menu}</>
            )}
          </div>
          <div className="pt-pager mono">
            <button className="pt-btn" disabled={!page.hasPrev} onClick={() => set({ offset: page.prevOffset })} aria-label="Previous page"><Icon name="chevron-left" size="xs" /></button>
            <span aria-live="polite">{page.from.toLocaleString('en-US')}–{page.to.toLocaleString('en-US')} of {page.total.toLocaleString('en-US')}</span>
            <button className="pt-btn" disabled={!page.hasNext} onClick={() => set({ offset: page.nextOffset })} aria-label="Next page"><Icon name="chevron-right" size="xs" /></button>
            {list.loading && list.data && <span className="dim small">Refreshing</span>}
            {list.error && list.data && <ProvChip kind="DOWN" label="Board stale" title="Showing last copy" />}
            {overlayType === 'sim' && <ProvChip kind="SIMULATED" title="Stored run" />}
          </div>
          {drawerId && (
            <PortDrawer placeId={drawerId} overlay={effective.overlay} tick={tick} onClose={() => setDrawerId(null)} onSelect={pick} />
          )}
        </div>
      </div>
    </Panel>
  );
}
