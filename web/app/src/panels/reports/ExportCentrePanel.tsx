import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Panel } from '../../components/Panel';
import { shortRunId } from '../../exports/columnLabels';
import { useStore } from '../../store';
import { Chip, MetaChip, StateLine } from './chips';
import { CentreError, fetchCatalogue } from './client';
import { DatasetList, type DatasetActions } from './DatasetList';
import { PackBuilder } from './PackBuilder';
import { RecentList } from './RecentList';
import { ReportBuilder } from './ReportBuilder';
import { SnapshotPane } from './SnapshotPane';
import { FiguresPane } from './FiguresPane';
import { useServerCharts } from './useServerCharts';
import { saveCsv, saveJson, saveXlsx, xlsxPath, type Done, type Query } from './exportActions';
import { searchDatasets } from './exportPlan';
import {
  boundsOrdered, clampRowsCap, failureState, hasData, parseTimeBound, supportsFilter,
  type ExportCatalogue, type ExportDataset,
} from './model';
import { addRecent, clearRecent, getRecent, subscribeRecent } from './recentStore';
import { usePreviews } from './usePreviews';
import './exportpage.css';
import { openInViewer } from '../dataviewer/viewerBus';
import { probeRoute, type RouteState } from '../dataviewer/dataClient';

/* EXPORT CENTRE. DATASETS: a searchable catalogue with state, rows,
 * size, provenance, a mini-preview and one-click OPEN / CHART / CSV /
 * JSON / XLSX. DATA PACK: checklist, live estimate, progress, manifest.
 * REPORT: the report builder with figures. SNAPSHOT: the chart set as a
 * print page or PNG. RECENT: this browser's last exports. A route the
 * server does not ship shows NOT SERVED; nothing is faked. */

type Load = { s: 'loading' } | { s: 'ok'; cat: ExportCatalogue } | { s: 'err'; code: string };
type Tab = 'datasets' | 'figures' | 'pack' | 'report' | 'snapshot' | 'recent';
const TABS: ReadonlyArray<{ id: Tab; label: string }> = [
  { id: 'datasets', label: 'Datasets' }, { id: 'figures', label: 'Figures' }, { id: 'pack', label: 'Data pack' }, { id: 'report', label: 'Report' },
  { id: 'snapshot', label: 'Snapshot' }, { id: 'recent', label: 'Recent' },
];

export function ExportCentrePanel() {
  const st = useStore();
  const runId = st.chain?.run_id ?? null;
  const simRunId = st.simRun?.sim_run_id ?? null;
  const [load, setLoad] = useState<Load>({ s: 'loading' });
  const [tab, setTab] = useState<Tab>('datasets');
  const [query, setQuery] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [capText, setCapText] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [word, setWord] = useState<{ ok: boolean; text: string } | null>(null);
  const [xlsx, setXlsx] = useState<RouteState>('checking');
  const charts = useServerCharts(runId, simRunId);
  const [recent, setRecent] = useState(getRecent);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  useEffect(() => subscribeRecent(() => setRecent([...getRecent()])), []);

  const reload = useCallback(() => {
    const ctrl = new AbortController();
    setLoad({ s: 'loading' });
    fetchCatalogue(runId, simRunId, ctrl.signal)
      .then((cat) => setLoad({ s: 'ok', cat }))
      .catch((e: unknown) => { if ((e as CentreError).code !== 'aborted') setLoad({ s: 'err', code: (e as CentreError).code ?? 'error' }); });
    return () => ctrl.abort();
  }, [runId, simRunId]);
  useEffect(() => reload(), [reload]);
  const cat = load.s === 'ok' ? load.cat : null;

  /* optional routes: probed, never assumed */
  const firstWithData = cat?.datasets.find(hasData) ?? null;
  const probeKey = firstWithData?.id ?? '';
  useEffect(() => {
    if (!firstWithData) return;
    const c = new AbortController();
    setXlsx('checking');
    probeRoute(xlsxPath(firstWithData, { runId, simRunId, from: null, to: null, rowsCap: 1 }), c.signal).then((r) => setXlsx(r.state));
    return () => c.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [probeKey, runId, simRunId]);

  const previews = usePreviews(cat, runId, simRunId);
  const fromP = parseTimeBound(from); const toP = parseTimeBound(to);
  const boundsBad = !fromP.ok || !toP.ok || (fromP.ok && toP.ok && !boundsOrdered(fromP.value, toP.value));
  const cap = cat ? clampRowsCap(capText === '' ? cat.default_rows_cap : capText, cat) : 0;
  const q = (d?: ExportDataset): Query => ({
    runId, simRunId, rowsCap: cap,
    from: d && !supportsFilter(d, 'from') ? null : fromP.ok ? fromP.value : null, to: d && !supportsFilter(d, 'to') ? null : toP.ok ? toP.value : null,
  });
  const items = useMemo(() => (cat ? searchDatasets(cat.datasets, query) : []), [cat, query]);

  const flash = (ok: boolean, text: string) => { setWord({ ok, text }); setTimeout(() => setWord(null), 3600); };
  const run = async (key: string, f: () => Promise<Done>) => {
    if (boundsBad) { flash(false, 'BAD TIME BOUND'); return; }
    setBusy(key);
    try { const d = await f(); addRecent(d); flash(true, `SAVED ${d.file}`); } catch (e) { flash(false, failureState((e as CentreError).code ?? 'error').word); } finally { setBusy(null); }
  };
  const showViewer = () => st.setLayout('Data Desk');
  const actions: DatasetActions = {
    open: (d, tabId) => openInViewer({ d: d.id, run: runId, sim: simRunId, tab: tabId }, showViewer),
    csv: (d) => run(`${d.id}:csv`, () => saveCsv(d, q(d))),
    json: (d) => run(`${d.id}:json`, () => saveJson(d, q(d))),
    xlsx: (d) => run(`${d.id}:xlsx`, () => saveXlsx(d, q(d))),
  };

  const onTabKey = (e: React.KeyboardEvent, i: number) => {
    const n = TABS.length;
    const j = e.key === 'ArrowRight' ? (i + 1) % n : e.key === 'ArrowLeft' ? (i - 1 + n) % n : e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : -1;
    if (j < 0) return;
    e.preventDefault(); setTab(TABS[j].id); tabRefs.current[j]?.focus();
  };

  const stamp = cat ? cat.counter.toUpperCase() : load.s === 'err' ? 'DOWN' : 'loading';
  const bounds = { cap, from: fromP.ok ? fromP.value : null, to: toP.ok ? toP.value : null, bad: boundsBad };
  const timeable = !!cat?.datasets.some((d) => supportsFilter(d, 'from'));
  const updatedServed = !!cat?.datasets.some((d) => d.updated_at);

  return (
    <Panel id="exports" code="EXP" title="EXPORT CENTRE" updated={stamp} className="rk-panel xc-panel"
      exportSpec={{ json: () => ({ run_id: runId, sim_run_id: simRunId, catalogue: cat }) }}>
      {load.s === 'loading' && <StateLine chip="INFO" word="LOADING" />}
      {load.s === 'err' && <StateLine chip={failureState(load.code).chip} word={failureState(load.code).word} code={load.code} onRetry={reload} />}
      {cat && (
        <div className="xc-root">
          <div className="xc-top">
            <div className="rk-tabs" role="tablist" aria-label="Export page">
              {TABS.map((t, i) => (
                <button key={t.id} type="button" role="tab" id={`xc-tab-${t.id}`} aria-selected={tab === t.id} aria-controls={`xc-pane-${t.id}`} tabIndex={tab === t.id ? 0 : -1}
                  ref={(el) => { tabRefs.current[i] = el; }} className={`rk-tab${tab === t.id ? ' on' : ''}`} onClick={() => setTab(t.id)} onKeyDown={(e) => onTabKey(e, i)}>
                  {t.label}{t.id === 'recent' && recent.length > 0 ? ` ${recent.length}` : ''}
                </button>
              ))}
            </div>
            <div className="xc-status">
              {runId ? <MetaChip title={runId}>{`Run ${shortRunId(runId)}`}</MetaChip> : <Chip chip="NOT SERVED" word="NO RUN" />}
              {simRunId ? <MetaChip title={simRunId}>{`Simulation run ${shortRunId(simRunId)}`}</MetaChip> : <Chip chip="NOT SERVED" word="NO SIM RUN" />}
              <span className="xc-route" title="/exports/dataset/{id}.xlsx">XLSX {xlsx === 'served' ? <Chip chip="LIVE" word="SERVED" /> : xlsx === 'checking' ? <MetaChip>CHECKING</MetaChip> : <Chip chip={xlsx === 'down' ? 'DOWN' : 'NOT SERVED'} />}</span>
              <span className="xc-route" title="/exports/charts">Server figures {charts.s === 'served' ? <Chip chip="LIVE" word="SERVED" /> : charts.s === 'checking' ? <MetaChip>CHECKING</MetaChip> : <Chip chip={charts.s === 'down' ? 'DOWN' : 'NOT SERVED'} />}</span>
              {!updatedServed && <span className="xc-route">Updated <Chip chip="NOT SERVED" /></span>}
              {word && <span className={`cx-status ${word.ok ? 'ok' : 'bad'}`} role="status">{word.text}</span>}
            </div>
          </div>

          {(tab === 'datasets' || tab === 'pack') && (
            <div className="xc-bounds">
              {tab === 'datasets' && (
                <label className="rk-row" htmlFor="xc-q">
                  <span className="rk-lbl">Search</span>
                  <input id="xc-q" type="search" className="rk-input xc-search" value={query} placeholder="id, label, state" spellCheck={false} onChange={(e) => setQuery(e.target.value)} />
                </label>
              )}
              <label className="rk-row" htmlFor="rk-cap"><span className="rk-lbl">Rows cap</span>
                <input id="rk-cap" className="rk-input rk-input--num mono" inputMode="numeric" value={capText} placeholder={String(cat.default_rows_cap)} onChange={(e) => setCapText(e.target.value)} />
                <MetaChip>MAX {cat.max_rows_cap.toLocaleString('en-US')}</MetaChip></label>
              <label className="rk-row" htmlFor="rk-from"><span className="rk-lbl">From</span>
                <input id="rk-from" className="rk-input rk-input--num mono" value={from} placeholder="YYYY-MM-DD" disabled={!timeable} aria-invalid={!fromP.ok} onChange={(e) => setFrom(e.target.value)} /></label>
              <label className="rk-row" htmlFor="rk-to"><span className="rk-lbl">To</span>
                <input id="rk-to" className="rk-input rk-input--num mono" value={to} placeholder="YYYY-MM-DD" disabled={!timeable} aria-invalid={!toP.ok} onChange={(e) => setTo(e.target.value)} /></label>
              {boundsBad && <Chip chip="DOWN" word="BAD TIME BOUND" />}
              <button type="button" className="rk-btn" onClick={reload}>Reload</button>
            </div>
          )}

          <div role="tabpanel" id={`xc-pane-${tab}`} aria-labelledby={`xc-tab-${tab}`} className="xc-pane">
            {tab === 'datasets' && <DatasetList items={items} previews={previews} rowsCap={cap} xlsx={xlsx} busy={busy} actions={actions} showUpdated={updatedServed} />}
            {tab === 'pack' && (
              <PackBuilder cat={cat} runId={runId} simRunId={simRunId} previews={previews} bounds={bounds} onDone={addRecent} actions={actions}
                onOpen={(id) => openInViewer({ d: id, run: runId, sim: simRunId, tab: 'grid' }, showViewer)} />
            )}
            {tab === 'report' && <ReportBuilder runId={runId} onExported={addRecent} />}
            {tab === 'figures' && <FiguresPane state={charts} runId={runId} simRunId={simRunId} onDone={addRecent} onOpenData={(id) => openInViewer({ d: id, run: runId, sim: simRunId, tab: 'chart' }, showViewer)} />}
            {tab === 'snapshot' && <SnapshotPane cat={cat} runId={runId} simRunId={simRunId} onDone={addRecent} />}
            {tab === 'recent' && <RecentList items={recent} catalogue={cat.datasets} onClear={clearRecent} onGoto={setTab} />}
          </div>
        </div>
      )}
    </Panel>
  );
}
