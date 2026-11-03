import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTabKeys } from '../../ui/Controls';
import { csvEscape } from '../../charts/serialize';
import type { Hit } from '../../charts/types';
import { Panel } from '../../components/Panel';
import { shortRunId } from '../../exports/columnLabels';
import { useStore } from '../../store';
import { Chip, MetaChip, StateLine } from '../reports/chips';
import { CentreError, fetchCatalogue, saveBlob } from '../reports/client';
import { clampRowsCap, failureState, hasData, rowsLabel, type CsvResult, type ExportCatalogue, type ExportDataset } from '../reports/model';
import { ChartPane } from './ChartPane';
import { MapView } from './MapView';
import { SummaryStrip } from './SummaryStrip';
import { VirtualGrid } from './VirtualGrid';
import { defaultColumns, placeColumns, placeIdsOf, type Dataset } from './columns';
import { buildChartSpec, defaultView, normaliseView, type ChartView, type ProvIn } from './chartSpec';
import { MenuButton } from '../../charts/MenuButton';
import { formatTimeFull } from '../../charts/scale';
import { fetchDataset } from './dataClient';
import { applyFilters, rowsToCsv, sortRows, toggleSort, type SortKey } from './grid';
import { readPlaces } from './miniMap';
import { summarise } from './summary';
import { subscribeOpen, takePending } from './viewerBus';
import { linkToFilters, linkToSort, linkToView, readViewerParam, stateToLink, viewerUrl, type ViewerLink, type ViewerTab } from './viewerLink';
import { useServerCharts } from '../reports/useServerCharts';
import { figureReady, serverTarget } from '../reports/serverCharts';
import { addRecent } from '../reports/recentStore';
import type { ServerExport } from '../../charts/exportDom';
import './dataviewer.css';

/* DATA VIEWER (DAT). Opens any export dataset (or one posted by the
 * Export Centre) as a virtualised grid with sort, multi-column filter,
 * column picker and sticky header; a CHART view with sensible defaults
 * per dataset; a MAP view for place datasets that drives the shell's
 * place bus; and a SUMMARY strip computed from the loaded rows. Absent
 * cells read NOT SERVED, never 0. Self-contained: it asks nothing of its
 * container beyond a width and a height. */

type Load =
  | { s: 'idle' } | { s: 'loading' }
  | { s: 'ok'; ds: Dataset; result: CsvResult; fetchedAt: string }
  | { s: 'err'; code: string };
type CatLoad = { s: 'loading' } | { s: 'ok'; cat: ExportCatalogue } | { s: 'err'; code: string };

const DEFAULT_CAP = 10000;

export function DataViewerPanel() {
  const st = useStore();
  const [catLoad, setCatLoad] = useState<CatLoad>({ s: 'loading' });
  const [link, setLink] = useState<ViewerLink | null>(() => (typeof location !== 'undefined' ? readViewerParam(location.search) : null));
  const [dsId, setDsId] = useState<string | null>(link?.d ?? null);
  const [runOverride, setRunOverride] = useState<string | null>(link?.run ?? null);
  const [simOverride, setSimOverride] = useState<string | null>(link?.sim ?? null);
  const [capText, setCapText] = useState('');
  const [load, setLoad] = useState<Load>({ s: 'idle' });
  const [tab, setTab] = useState<ViewerTab>(link?.tab ?? 'grid');
  const [filters, setFilters] = useState<Record<number, string>>({});
  const [sort, setSort] = useState<SortKey[]>([]);
  const [cols, setCols] = useState<number[]>([]);
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set());
  const [view, setView] = useState<ChartView | null>(null);
  const [selOnly, setSelOnly] = useState(false);
  const [showSummary, setShowSummary] = useState(true);
  const [picker, setPicker] = useState(false);
  const [reveal, setReveal] = useState<number | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const VTABS = ['grid', 'chart', 'map'] as const;
  const tk = useTabKeys(VTABS.length, VTABS.indexOf(tab), (i) => { if (VTABS[i] !== 'map' || isPlaceData) setTab(VTABS[i]); });
  const pendingLink = useRef<ViewerLink | null>(link);

  const runId = runOverride ?? st.chain?.run_id ?? null;
  const simRunId = simOverride ?? st.simRun?.sim_run_id ?? null;

  /* catalogue */
  const reloadCat = useCallback(() => {
    const ctrl = new AbortController();
    setCatLoad({ s: 'loading' });
    fetchCatalogue(runId, simRunId, ctrl.signal)
      .then((cat) => setCatLoad({ s: 'ok', cat }))
      .catch((e: unknown) => { if ((e as CentreError).code !== 'aborted') setCatLoad({ s: 'err', code: (e as CentreError).code ?? 'error' }); });
    return () => ctrl.abort();
  }, [runId, simRunId]);
  useEffect(() => reloadCat(), [reloadCat]);
  const cat = catLoad.s === 'ok' ? catLoad.cat : null;
  const meta: ExportDataset | null = cat?.datasets.find((d) => d.id === dsId) ?? null;

  /* open requests (Export Centre, report builder, palette) */
  useEffect(() => {
    const apply = (r: ViewerLink) => {
      pendingLink.current = r;
      setLink(r); setRunOverride(r.run ?? null); setSimOverride(r.sim ?? null);
      setDsId(r.d); if (r.tab) setTab(r.tab);
    };
    const waiting = takePending();
    if (waiting) apply(waiting);
    return subscribeOpen(apply);
  }, []);

  /* choose a first dataset when none is asked for */
  useEffect(() => {
    if (cat && !dsId) setDsId(cat.datasets.find(hasData)?.id ?? null);
  }, [cat, dsId]);

  /* dataset fetch */
  const cap = cat ? clampRowsCap(capText === '' ? Math.min(DEFAULT_CAP, cat.max_rows_cap) : capText, cat) : DEFAULT_CAP;
  const metaKey = meta ? `${meta.id}|${meta.state}` : '';
  const reloadData = useCallback(() => {
    if (!meta || !hasData(meta)) { setLoad({ s: 'idle' }); return () => undefined; }
    const ctrl = new AbortController();
    setLoad({ s: 'loading' });
    fetchDataset(meta, { runId, simRunId, rowsCap: cap }, ctrl.signal)
      .then((r) => {
        const pl = pendingLink.current; pendingLink.current = null;
        const base = defaultView(r.ds);
        setFilters(pl ? linkToFilters(r.ds, pl) : {});
        setSort(pl ? linkToSort(r.ds, pl) : []);
        setView(pl ? linkToView(r.ds, pl, base) : normaliseView(r.ds, base));
        setCols(defaultColumns(r.ds));
        setSelected(new Set()); setReveal(null); setSelOnly(false);
        setLoad({ s: 'ok', ds: r.ds, result: r.result, fetchedAt: r.asOf });
      })
      .catch((e: unknown) => { if ((e as CentreError).code !== 'aborted') setLoad({ s: 'err', code: (e as CentreError).code ?? 'error' }); });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [metaKey, runId, simRunId, cap]);
  useEffect(() => reloadData(), [reloadData]);

  const ds = load.s === 'ok' ? load.ds : null;
  const filtered = useMemo(() => (ds ? applyFilters(ds, filters) : { idx: [] as number[], errors: {} as Record<number, string> }), [ds, filters]);
  const rows = useMemo(() => (ds ? sortRows(ds, filtered.idx, sort) : []), [ds, filtered.idx, sort]);
  const rowSet = useMemo(() => new Set(rows), [rows]);
  const chartRows = useMemo(() => (selOnly && selected.size ? rows.filter((r) => selected.has(r)) : rows), [rows, selOnly, selected]);
  const summaries = useMemo(() => (ds ? summarise(ds, rows, cols) : []), [ds, rows, cols]);
  const places = useMemo(() => readPlaces(st.graph.nodes), [st.graph.nodes]);
  const isPlaceData = !!ds && placeColumns(ds).length > 0;
  const selectedPlaces = useMemo(() => (ds ? placeIdsOf(ds, [...selected].filter((r) => rowSet.has(r))) : []), [ds, selected, rowSet]);

  const prov: ProvIn = useMemo(() => ({
    datasetProvenance: meta?.provenance ?? (load.s === 'ok' ? load.result.provenance : null), runId, asOf: load.s === 'ok' ? load.fetchedAt : null,
    source: ds ? `EXPORT ${ds.id.toUpperCase()}` : null, scope: null,
  }), [meta, load, runId, ds]);

  const built = useMemo(() => (ds && view ? buildChartSpec({ ds, idx: chartRows, view, prov }) : { spec: null, reason: null as string | null }), [ds, view, chartRows, prov]);

  /* the same chart from the exports route, when this view maps to one */
  const serverCat = useServerCharts(runId, simRunId);
  const serverExport: ServerExport | null = useMemo(() => {
    if (!ds || !view) return null;
    if (serverCat.s !== 'served') return { ok: false, note: serverCat.s === 'checking' ? 'CHECKING' : 'NOT SERVED', run: async () => ({ ok: false, word: 'NOT SERVED' }) };
    const ready = serverCat.cat.figures.filter(figureReady);
    const t = serverTarget(ds, view, { runId, simRunId, rowIdx: chartRows, catalogueIds: ready.map((f) => f.id) });
    if (t.t === 'none') return { ok: false, note: t.reason, run: async () => ({ ok: false, word: t.reason }) };
    return {
      ok: true, note: undefined,
      run: async (ext) => {
        const { saveFigure, savePostedFigure } = await import('../reports/exportActions');
        try {
          const d = t.t === 'figure'
            ? await saveFigure(ready.find((f) => f.id === t.id)!, ext, { runId, simRunId, theme: 'light', scale: 2 }, serverCat.cat.pngAvailable)
            : await savePostedFigure({ ...t.body, theme: 'light' }, ext, `${ds.id}-${String((t.body as { y?: string }).y ?? 'chart')}`);
          addRecent(d);
          return { ok: true, word: d.via === 'browser' ? 'PNG RENDERED HERE' : `SAVED ${ext.toUpperCase()}`, file: d.file };
        } catch (e) { return { ok: false, word: `SERVER ${(e as { code?: string }).code ?? 'ERROR'}`.slice(0, 40) }; }
      },
    };
  }, [ds, view, serverCat, runId, simRunId, chartRows]);

  /* selection <-> the place bus */
  const onSelectRows = (picked: number[]) => {
    setSelected(new Set(picked));
    if (!ds || picked.length === 0) return;
    const ids = placeIdsOf(ds, [picked[picked.length - 1]]);
    if (ids.length) st.selectNode(ids[ids.length - 1]);
  };
  const onChartHit = (hit: Hit) => {
    if (!ds) return;
    const seqCol = ds.columns.find((c) => c.name === 'seq');
    const pcols = placeColumns(ds);
    const matches = rows.filter((r) => {
      if (hit.seq != null && seqCol) return ds.rows[r][seqCol.index] === hit.seq;
      return !!hit.placeId && pcols.some((c) => ds.rows[r][c.index] === hit.placeId);
    });
    if (matches.length === 0) return;
    setSelected(new Set(matches));
    setReveal(rows.indexOf(matches[0]));
    if (hit.placeId) st.selectNode(hit.placeId, hit.seq ?? null);
  };
  const pickPlace = (id: string) => {
    st.selectNode(id);
    if (!ds) return;
    const pcols = placeColumns(ds);
    const matches = rows.filter((r) => pcols.some((c) => ds.rows[r][c.index] === id));
    if (matches.length) setSelected(new Set(matches));
  };

  /* actions */
  const currentLink = (): ViewerLink | null => (ds && view ? stateToLink(ds, { runId, simRunId, tab: tab === 'map' ? 'map' : tab, view, filters, sort }) : null);
  const copyLink = async () => {
    const l = currentLink();
    if (!l || typeof location === 'undefined') return;
    try { await navigator.clipboard.writeText(viewerUrl(location, l)); setCopied('LINK COPIED'); } catch { setCopied('COPY BLOCKED'); }
  };
  useEffect(() => { if (!copied) return; const t = setTimeout(() => setCopied(null), 2800); return () => clearTimeout(t); }, [copied]);
  const exportCsv = (onlySelected: boolean) => {
    if (!ds) return;
    const idx = onlySelected ? rows.filter((r) => selected.has(r)) : rows;
    saveBlob(`${ds.id}-view.csv`, new Blob([rowsToCsv(ds, idx, cols, csvEscape as (v: string) => string)], { type: 'text/csv;charset=utf-8' }));
  };

  const snapshot = async (kind: string) => {
    if (!ds || !view) return;
    const [{ liveLookup }, { snapshotHtml }, { openPrintWindow, stackedPng }, { chartToSvg, exportFilename }] = await Promise.all([
      import('../../charts/exportDom'), import('../reports/snapshot'), import('../reports/exportActions'), import('../../charts/serialize'),
    ]);
    const specs = built.spec ? [built.spec] : [];
    const shown = rows.slice(0, 100);
    const table = { title: ds.id, columns: cols.map((c) => ds.columns[c].header), shown: shown.length, total: rows.length,
      rows: shown.map((r) => cols.map((c) => { const v = ds.rows[r][c]; return v != null && ds.columns[c].type === 'time' ? formatTimeFull(v as number) : v; })) };
    const { lookup, done } = liveLookup('print');
    try {
      const asOf = load.s === 'ok' ? load.fetchedAt : null;
      if (kind === 'png') {
        if (!built.spec) { setCopied('NO CHART'); return; }
        const f = chartToSvg(built.spec, { width: 900, chartHeight: view.type === 'cascade' ? 300 : 260 }, lookup);
        const png = await stackedPng([{ svg: f.svg, width: f.width, height: f.height }], 2);
        saveBlob(exportFilename({ title: ds.id, type: 'view', runId, asOf, ext: 'png', scale: 2 }), png); setCopied('SAVED PNG 2X');
        return;
      }
      const html = snapshotHtml({ title: `${ds.id} view`, runId, simRunId, asOf, charts: specs, missing: specs.length ? [] : [{ id: ds.id, reason: built.reason ?? 'NO CHART' }], tables: [table] }, lookup);
      if (kind === 'html') { saveBlob(exportFilename({ title: ds.id, type: 'view', runId, asOf, ext: 'html' }), new Blob([html], { type: 'text/html;charset=utf-8' })); setCopied('SAVED HTML'); }
      else setCopied(openPrintWindow(html) ? 'PRINT VIEW OPEN' : 'POPUP BLOCKED');
    } catch { setCopied('EXPORT FAILED'); } finally { done(); }
  };
  const sortCol = (c: number, additive: boolean) => setSort((s) => toggleSort(s, c, additive));
  const setFilter = (c: number, text: string) => setFilters((f) => { const n = { ...f }; if (text) n[c] = text; else delete n[c]; return n; });
  const activeFilters = Object.keys(filters).filter((k) => filters[Number(k)]).length;

  const stamp = ds ? `${ds.id} · ${rows.length} OF ${ds.rows.length}` : catLoad.s === 'err' ? 'DOWN' : 'DATA';
  const fail = load.s === 'err' ? failureState(load.code) : null;
  const dsProv = meta?.provenance ?? (load.s === 'ok' ? load.result.provenance : null);

  return (
    <Panel id="dataviewer" code="DAT" title="DATA VIEWER" updated={stamp} className="dv-panel"
      exportSpec={ds ? { csv: () => rows.map((r) => Object.fromEntries(cols.map((c) => [ds.columns[c].header, ds.rows[r][c]]))), json: () => ({ dataset: ds.id, run_id: runId, columns: cols.map((c) => ds.columns[c].header), rows: rows.map((r) => cols.map((c) => ds.rows[r][c])) }) } : undefined}>
      <div className="dv-root">
        {/* toolbar */}
        <div className="dv-bar">
          <label className="dv-pick" htmlFor="dv-ds">
            <span className="rk-lbl">Dataset</span>
            <select id="dv-ds" className="rk-input dv-dsel" value={dsId ?? ''} disabled={!cat} onChange={(e) => { pendingLink.current = null; setLink(null); setDsId(e.target.value); }}>
              {!cat && <option value="">Loading</option>}
              {cat?.datasets.map((d) => (
                <option key={d.id} value={d.id} disabled={!hasData(d)}>{d.label} · {hasData(d) ? (rowsLabel(d.rows) ?? 'NOT SERVED') : d.state}</option>
              ))}
            </select>
          </label>
          <label className="dv-pick" htmlFor="dv-cap">
            <span className="rk-lbl">Rows cap</span>
            <input id="dv-cap" className="rk-input rk-input--num mono" inputMode="numeric" value={capText} placeholder={String(Math.min(DEFAULT_CAP, cat?.max_rows_cap ?? DEFAULT_CAP))}
              onChange={(e) => setCapText(e.target.value)} />
          </label>
          <button type="button" className="rk-btn" onClick={() => { reloadCat(); reloadData(); }} disabled={load.s === 'loading'}>Reload</button>
          <span className="rk-spacer" />
          <div className="rk-tabs" role="tablist" aria-label="View">
            {VTABS.map((t, i) => (
              <button key={t} type="button" role="tab" {...tk(i)} aria-selected={tab === t} disabled={t === 'map' && !isPlaceData} className={`rk-tab${tab === t ? ' on' : ''}`} onClick={() => setTab(t)}>{t.charAt(0).toUpperCase() + t.slice(1)}</button>
            ))}
          </div>
        </div>

        {/* provenance row */}
        <div className="dv-prov" role="group" aria-label="Provenance">
          {dsProv ? <Chip chip={/^mixed/i.test(dsProv) ? 'MIXED' : dsProv.toUpperCase()} word={/^mixed/i.test(dsProv) ? 'PER CELL' : undefined} /> : <Chip chip="NOT SERVED" />}
          {runId ? <MetaChip title={runId}>{`Run ${shortRunId(runId)}${runOverride ? ' · link' : ''}`}</MetaChip> : <Chip chip="NOT SERVED" word="NO RUN" />}
          {simRunId && <MetaChip title={simRunId}>SIM {simRunId}</MetaChip>}
          {load.s === 'ok' && (
            <>
              <span className="rk-lbl">Written</span><span className="mono rk-num">{load.result.written ?? '—'}</span>
              <span className="rk-lbl">Available</span><span className="mono rk-num">{load.result.available ?? '—'}</span>
              {(load.result.cut ?? 0) > 0 ? <Chip chip="CUT" word={`Cut ${load.result.cut}`} /> : <MetaChip>Nothing cut</MetaChip>}
              <MetaChip>FETCHED {load.fetchedAt.slice(11, 19)}Z</MetaChip>
              {load.ds.warnings.map((w) => <Chip key={w} chip="NOT SERVED" word={w.toUpperCase()} />)}
            </>
          )}
          {ds && <span className="dv-count mono" aria-live="polite">{rows.length.toLocaleString('en-US')} OF {ds.rows.length.toLocaleString('en-US')} ROWS{selected.size ? ` · ${selected.size} SELECTED` : ''}</span>}
        </div>

        {catLoad.s === 'loading' && <StateLine chip="INFO" word="LOADING" />}
        {catLoad.s === 'err' && <StateLine chip={failureState(catLoad.code).chip} word={failureState(catLoad.code).word} code={catLoad.code} onRetry={reloadCat} />}
        {meta && !hasData(meta) && <div className="rk-state"><Chip chip={meta.state} /><MetaChip>{meta.id}</MetaChip></div>}
        {load.s === 'loading' && <StateLine chip="INFO" word="LOADING" />}
        {fail && <StateLine chip={fail.chip} word={fail.word} code={fail.code} onRetry={reloadData} />}

        {ds && view && (
          <>
            {/* actions */}
            <div className="dv-actions">
              <button type="button" className="rk-btn" aria-expanded={picker} onClick={() => setPicker((p) => !p)}>Columns {cols.length}/{ds.columns.length}</button>
              <button type="button" className="rk-btn" aria-pressed={showSummary} onClick={() => setShowSummary((s) => !s)}>Summary</button>
              <button type="button" className="rk-btn" disabled={activeFilters === 0} onClick={() => setFilters({})}>Clear filters{activeFilters ? ` ${activeFilters}` : ''}</button>
              <button type="button" className="rk-btn" disabled={sort.length === 0} onClick={() => setSort([])}>Clear sort</button>
              <span className="rk-spacer" />
              <button type="button" className="rk-btn" onClick={() => exportCsv(false)}>CSV view</button>
              <button type="button" className="rk-btn" disabled={selected.size === 0} onClick={() => exportCsv(true)}>CSV selection</button>
              <MenuButton label="Snapshot" aria="Snapshot view" items={[{ id: 'print', label: 'Print view' }, { id: 'html', label: 'HTML page' }, { id: 'png', label: 'PNG 2x' }]} onPick={snapshot} />
              <button type="button" className="rk-btn" onClick={copyLink}>Copy link</button>
              {copied && <span className="cx-status ok" role="status">{copied}</span>}
            </div>
            {picker && (
              <div className="dv-picker" role="group" aria-label="Columns">
                <div className="rk-row">
                  <button type="button" className="rk-btn" onClick={() => setCols(defaultColumns(ds))}>Default</button>
                  <button type="button" className="rk-btn" onClick={() => setCols(ds.columns.map((c) => c.index))}>All</button>
                  <button type="button" className="rk-btn" onClick={() => setCols(ds.columns.filter((c) => c.type === 'number').map((c) => c.index))}>Numeric</button>
                </div>
                <div className="dv-pickgrid">
                  {ds.columns.map((c) => (
                    <label key={c.index} className="rk-check">
                      <input type="checkbox" checked={cols.includes(c.index)} onChange={() => setCols((cur) => (cur.includes(c.index) ? cur.filter((x) => x !== c.index) : [...cur, c.index].sort((a, b) => a - b)))} />
                      <span className="dv-pickname" title={c.header}>{c.name}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}
            {showSummary && tab === 'grid' && <SummaryStrip summaries={summaries} rowCount={rows.length} />}

            {tab === 'grid' && (
              <VirtualGrid ds={ds} rows={rows} cols={cols.length ? cols : defaultColumns(ds)} sort={sort} onSort={sortCol} filters={filters} filterErrors={filtered.errors}
                onFilter={setFilter} selected={selected} onSelect={onSelectRows} reveal={reveal} />
            )}
            {tab === 'chart' && (
              <ChartPane ds={ds} rows={chartRows} view={view} onView={setView} built={built} selectionOnly={selOnly} onSelectionOnly={setSelOnly} hasSelection={selected.size > 0}
                link={() => { const l = currentLink(); return l && typeof location !== 'undefined' ? viewerUrl(location, { ...l, tab: 'chart' }) : null; }}
                onHit={onChartHit} highlightPlaces={selectedPlaces} serverExport={serverExport} />
            )}
            {tab === 'map' && isPlaceData && <MapView places={places} highlighted={selectedPlaces} current={st.selectedNode} onPick={pickPlace} />}
          </>
        )}
      </div>
    </Panel>
  );
}

