import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { LiveStatus } from '../../ui/LiveStatus';
import { Panel } from '../../components/Panel';
import { rootEvent, softenNegation } from '../../format';
import { shipRunSlot, shipSlot } from '../../handoffs/pick';
import { safeStorage, useStore } from '../../store';
import { Button, IconButton } from '../../ui';
import { fetchScenarios } from '../laneplanner/client';
import type { Scenario } from '../laneplanner/types';
import { Chip, MetaChip, StateLine } from '../reports/chips';
import { ProvenanceCell } from '../../provenance/ProvenanceCell';
import { shipmentProvenance, type ShipField } from '../../provenance/registry';
import { AddForm } from './AddForm';
import { useShipmentsReport } from './exportReport';
import { fetchSample, postEvaluate, postExport, type EvalRequest } from './client';
import { importShipments, type ImportResult } from './csv';
import {
  chainDisruption, clampDuration, csvRows, DEFAULT_SORT, failWord, filterCounts, FILTERS, filterRows, fmtD, fmtPct, fmtQty, fmtRangeD, fmtUsd, fmtUsdShort,
  choiceFromMcRun, joinRows, mcView, MC_ID_RE, MODE_WORD, QUALITY_WORD, scenarioBody, sortRows, STATUS_CHIP, STATUS_WORD, type FilterKey, type Row, type SortKey, type SortState,
} from './logic';
import { clearList, loadList, nextId, sanitiseList, saveList, type StoredList } from './storage';
import type { EvalResult, ScenarioChoice, Shipment } from './types';
import './shipments.css';

/* MY SHIPMENTS (SHP). A shipment list kept in this browser only (typed in,
 * imported from CSV, or the synthetic sample), read against a library scenario
 * or the open causal chain: which shipments are exposed, the added transit as a
 * range over named variants, and the value in affected shipments. All of it is
 * MODELLED; none of it is a forecast. A feed that is down reads NOT SERVED. */

const MAX_FILE_BYTES = 1_500_000;

type Phase = { s: 'idle' } | { s: 'loading' } | { s: 'done'; r: EvalResult };
type Notice = { chips: Array<{ chip: string; word: string }> } | null;

function download(name: string, blob: Blob) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

export function ShipmentsPanel() {
  const st = useStore();
  const [list, setList] = useState<StoredList>(() => loadList(safeStorage));
  const [scenarios, setScenarios] = useState<Scenario[] | null | undefined>(undefined);
  const [phase, setPhase] = useState<Phase>({ s: 'idle' });
  const [filter, setFilter] = useState<FilterKey>('all');
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT);
  const [adding, setAdding] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [exportState, setExportState] = useState<'idle' | 'busy' | 'failed'>('idle');
  const fileRef = useRef<HTMLInputElement>(null);
  const abort = useRef<AbortController | null>(null);
  const [retryTick, setRetryTick] = useState(0);
  const [mcId, setMcId] = useState('');
  const mcJob = MC_ID_RE.test(mcId.trim()) ? mcId.trim() : undefined;

  useEffect(() => {
    const ctrl = new AbortController();
    void fetchScenarios(ctrl.signal).then((s) => setScenarios(s));
    return () => ctrl.abort();
  }, []);
  useEffect(() => { saveList(safeStorage, list); }, [list]);
  /* a scenario handed over by the Scenario builder becomes the disruption; read on mount and on every later hand-off */
  useEffect(() => {
    const apply = () => {
      const p = shipSlot.take();
      if (!p) return;
      const duration = clampDuration(p.durationD);
      const c: ScenarioChoice = { kind: 'builder', place: p.placeId, shockKind: p.kind, duration, loss: p.kind === 'closure' ? null : p.capacityLoss, name: p.scenarioName, of: p.of, rounded: duration !== p.durationD };
      setList((l) => ({ ...l, scenario: c }));
    };
    apply();
    return shipSlot.subscribe(apply);
  }, []);

  /* a finished Monte Carlo run handed over: its job and its disruption become the evaluation */
  const [mcFromRun, setMcFromRun] = useState(false);
  const mcInput = useRef<HTMLInputElement>(null);
  const detached = useRef(false);
  /* detaching removes the focused button: focus moves to the job field, never to the page */
  useEffect(() => { if (!mcFromRun && detached.current) { detached.current = false; mcInput.current?.focus(); } }, [mcFromRun]);
  useEffect(() => {
    const apply = () => {
      const h = shipRunSlot.take();
      if (!h) return;
      setMcId(h.jobId); setMcFromRun(true);
      setList((l) => ({ ...l, scenario: choiceFromMcRun(h) }));
    };
    apply();
    return shipRunSlot.subscribe(apply);
  }, []);

  const chain = st.chain;
  const chainDis = useMemo(() => {
    if (!chain || chain.events.length === 0) return null;
    return chainDisruption(st.runMeta?.spec ?? null, rootEvent(chain).node_id);
  }, [chain, st.runMeta]);
  const library = scenarios ?? [];
  const closureIds = useMemo(() => new Set(library.filter((s) => s.kind === 'closure').map((s) => s.id)), [library]);
  const choice: ScenarioChoice = list.scenario.kind === 'chain' && !chainDis ? { kind: 'none' } : list.scenario;
  const body = useMemo(() => scenarioBody(choice, chainDis, closureIds), [choice, chainDis, closureIds]);
  const scn = choice.kind === 'library' ? library.find((s) => s.id === choice.id) ?? null : null;
  const setChoice = (c: ScenarioChoice) => setList((l) => ({ ...l, scenario: c }));

  /* evaluate when the list or the scenario changes (debounced, one call in flight) */
  const shipments = list.rows;
  useEffect(() => {
    abort.current?.abort();
    if (shipments.length === 0 || scenarios === undefined) { setPhase({ s: 'idle' }); return; }
    const ctrl = new AbortController(); abort.current = ctrl;
    setPhase((p) => (p.s === 'done' ? p : { s: 'loading' }));
    const timer = setTimeout(() => {
      const req: EvalRequest = { shipments, ...(body ? { scenario: body } : {}), ...(mcJob ? { mc_job_id: mcJob } : {}) };
      void postEvaluate(req, ctrl.signal).then((r) => { if (!ctrl.signal.aborted) setPhase({ s: 'done', r }); });
    }, 320);
    return () => { clearTimeout(timer); ctrl.abort(); };
  }, [shipments, body, mcJob, scenarios, retryTick]);
  useEffect(() => () => abort.current?.abort(), []);

  const doc = phase.s === 'done' && phase.r.kind === 'ok' ? phase.r.doc : null;
  const rows = useMemo(() => joinRows(shipments, doc), [shipments, doc]);
  const counts = useMemo(() => filterCounts(rows), [rows]);
  const shown = useMemo(() => sortRows(filterRows(rows, filter, query), sort), [rows, filter, query, sort]);
  const scenarioLabel = doc?.scenario.label ?? 'No disruption';
  useShipmentsReport(doc, rows, list.origin === 'sample', scenarioLabel);

  /* list edits */
  const edit = (fn: (rows: Shipment[]) => Shipment[]) => setList((l) => ({ ...l, rows: fn(l.rows), origin: 'user' }));
  const add = (s: Shipment) => { edit((r) => [...r, s]); setNotice(null); };
  const remove = (id: string) => edit((r) => r.filter((x) => x.id !== id));
  const setText = (id: string, field: 'origin' | 'destination' | 'via', text: string) => edit((r) => r.map((x) => (x.id === id ? { ...x, [field]: text } : x)));
  const clearAll = () => { clearList(safeStorage); setList({ rows: [], origin: 'user', scenario: list.scenario }); setNotice(null); setFilter('all'); };
  const loadSample = async () => {
    const s = await fetchSample();
    if (!s) { setNotice({ chips: [{ chip: 'NOT SERVED', word: 'Sample' }] }); return; }
    const rows = sanitiseList(s);
    setList((l) => ({ ...l, rows, origin: 'sample' }));
    setNotice({ chips: [{ chip: 'INFO', word: `${rows.length} loaded` }] });
  };
  const onFile = async (f: File | undefined) => {
    if (!f) return;
    if (f.size > MAX_FILE_BYTES) { setNotice({ chips: [{ chip: 'FLAG', word: 'File too large' }] }); return; }
    const text = await f.text().catch(() => '');
    const res: ImportResult = importShipments(text, list.origin === 'sample' ? [] : list.rows.map((r) => r.id));
    const chips: Array<{ chip: string; word: string }> = [];
    if (res.fileError === 'no_columns') chips.push({ chip: 'FLAG', word: 'No origin and destination columns' });
    else if (res.fileError === 'no_rows') chips.push({ chip: 'FLAG', word: 'No rows' });
    else {
      chips.push({ chip: 'INFO', word: `${res.shipments.length} imported` });
      if (res.rejected.length) chips.push({ chip: 'FLAG', word: `${res.rejected.length} skipped` });
      if (res.unread.dates) chips.push({ chip: 'FLAG', word: `${res.unread.dates} dates not read` });
      if (res.unread.values) chips.push({ chip: 'FLAG', word: `${res.unread.values} values not read` });
      if (res.unread.modes) chips.push({ chip: 'FLAG', word: `${res.unread.modes} modes not read` });
      const room = 500 - (list.origin === 'sample' ? 0 : list.rows.length);
      const take = res.shipments.slice(0, Math.max(0, room));
      if (take.length < res.shipments.length) chips.push({ chip: 'FLAG', word: 'List full at 500' });
      edit((r) => (list.origin === 'sample' ? take : [...r, ...take]));
    }
    setNotice({ chips });
    if (fileRef.current) fileRef.current.value = '';
  };

  const exportCsv = useCallback(async () => {
    const ex = await import('../../exporter');
    ex.exportCSV('shipments-exposure', csvRows(shown, scenarioLabel));
  }, [shown, scenarioLabel]);
  const exportFile = async (format: 'csv' | 'xlsx') => {
    setExportState('busy');
    const req: EvalRequest = { shipments, ...(body ? { scenario: body } : {}), ...(mcJob ? { mc_job_id: mcJob } : {}) };
    const blob = await postExport(req, format);
    if (!blob) { setExportState('failed'); return; }
    download(`shipment-exposure.${format}`, blob); setExportState('idle');
  };

  const sortBy = (key: SortKey) => setSort((s) => (s.key === key ? { key, dir: (s.dir * -1) as 1 | -1 } : { key, dir: key === 'added' || key === 'value' ? -1 : 1 }));
  const stamp = doc ? `Modelled · ${shipments.length} shipments` : shipments.length ? (phase.s === 'loading' ? 'EVALUATING' : 'NO RESULT') : 'EMPTY';

  return (
    <Panel id="shipments" code="SHP" title="My shipments" updated={stamp} className="sh-panel"
      exportSpec={{ csv: shown.length ? () => csvRows(shown, scenarioLabel) : undefined }}>
      <div className="sh-root">
        <LiveStatus text={phase.s === 'loading' ? 'Evaluating' : phase.s === 'done' ? (phase.r.kind === 'ok' ? 'Result ready' : ((phase.r as { code?: string }).code ? failWord((phase.r as { code?: string }).code as string) : 'Not served')) : ''} />
        <div className="sh-controls" role="group" aria-label="Scenario">
          <div className="sh-field sh-field--sel">
            <label className="sh-fl"><span className="sh-lab">Disruption</span>
              <select className="sh-input" value={choice.kind === 'library' ? `lib:${choice.id}` : choice.kind === 'chain' ? 'chain' : choice.kind === 'builder' ? 'builder' : ''}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v === 'chain' && chainDis) setChoice({ kind: 'chain', duration: clampDuration(chainDis.duration_d ?? 14), loss: chainDis.capacity_loss });
                  else if (v.startsWith('lib:')) {
                    const s = library.find((x) => x.id === v.slice(4));
                    if (s) setChoice({ kind: 'library', id: s.id, duration: clampDuration(s.default_duration_d), loss: s.kind === 'closure' ? null : s.default_capacity_loss ?? null });
                  } else setChoice({ kind: 'none' });
                }}>
                <option value="">None</option>
                {choice.kind === 'builder' && <option value="builder">{choice.via === 'montecarlo' ? 'Monte Carlo run' : 'Scenario timeline'}</option>}
                {chainDis && <option value="chain">Open causal chain</option>}
                {library.map((s) => <option key={s.id} value={`lib:${s.id}`}>{s.label}</option>)}
              </select>
            </label>
          </div>
          {choice.kind !== 'none' && (
            <div className="sh-field sh-field--num">
              <label className="sh-fl"><span className="sh-lab">Duration</span>
                <span className="sh-unit"><input className="sh-input" type="number" min={1} max={3650} step={1} value={choice.duration}
                  onChange={(e) => setChoice({ ...choice, duration: clampDuration(Number(e.target.value) || 1) })} /><i>d</i></span>
              </label>
            </div>
          )}
          {choice.kind === 'library' && scn && scn.kind !== 'closure' && scn.capacity_loss_options && (
            <div className="sh-field sh-field--num">
              <label className="sh-fl"><span className="sh-lab">Capacity loss</span>
                <select className="sh-input" value={choice.loss ?? scn.default_capacity_loss ?? ''} onChange={(e) => setChoice({ ...choice, loss: Number(e.target.value) })}>
                  {scn.capacity_loss_options.map((l) => <option key={l} value={l}>{Math.round(l * 100)}%</option>)}
                </select>
              </label>
            </div>
          )}
          <div className="sh-field sh-field--sel">
            {mcFromRun && mcJob ? (
              <div className="sh-fl"><span className="sh-lab">Monte Carlo run</span>
                <span className="sh-attached" data-testid="sh-mc-attached"><Chip chip="MODELLED" word="Attached" /><IconButton icon="x" label="Detach Monte Carlo run" size="sm" onClick={() => { detached.current = true; setMcId(''); setMcFromRun(false); }} /></span></div>
            ) : (
              <label className="sh-fl"><span className="sh-lab">Monte Carlo job</span>
                <input ref={mcInput} className="sh-input" value={mcId} maxLength={70} placeholder="mc_…" spellCheck={false} onChange={(e) => setMcId(e.target.value)} data-testid="sh-mc-id" /></label>
            )}
          </div>
          <span className="sh-spacer" />
          <div className="sh-actions">
            <Button icon="plus" onClick={() => setAdding((a) => !a)} aria-pressed={adding}>Add shipment</Button>
            <Button icon="upload" onClick={() => fileRef.current?.click()}>Import CSV</Button>
            <input ref={fileRef} type="file" accept=".csv,.tsv,.txt,text/csv" className="sh-file" tabIndex={-1} aria-hidden="true" aria-label="Import CSV file" data-testid="sh-file" onChange={(e) => void onFile(e.target.files?.[0])} />
            <Button icon="package" onClick={() => void loadSample()}>Sample list</Button>
            <Button icon="download" onClick={() => void exportCsv()} disabled={shown.length === 0}>Export CSV</Button>
            <Button icon="file-down" onClick={() => void exportFile('xlsx')} disabled={shipments.length === 0 || exportState === 'busy'}>Export XLSX</Button>
            <IconButton icon="trash-2" label="Clear list" onClick={clearAll} disabled={shipments.length === 0} />
          </div>
        </div>
        <div className="sh-chips" role="group" aria-label="State">
          <MetaChip>Stored in this browser</MetaChip>
          <Chip chip="MODELLED" />
          {list.origin === 'sample' && shipments.length > 0 && <Chip chip="INFO" word="Sample (synthetic)" />}
          {choice.kind === 'chain' && st.runMeta && <MetaChip>{st.runMeta.label}</MetaChip>}
          {choice.kind === 'builder' && <MetaChip>{choice.via === 'montecarlo' ? 'Monte Carlo run' : 'Scenario timeline'}</MetaChip>}
          {choice.kind === 'builder' && choice.of > 1 && <Chip chip="INFO" word={`Earliest of ${choice.of} shocks`} />}
          {choice.kind === 'builder' && choice.rounded && <Chip chip="INFO" word={`Duration rounded to ${choice.duration} d`} />}
          {notice?.chips.map((c, i) => <Chip key={i} chip={c.chip} word={c.word} />)}
          {exportState === 'failed' && <Chip chip="NOT SERVED" word="Export" />}
        </div>
        {adding && <AddForm nextId={nextId(shipments)} onAdd={add} onClose={() => setAdding(false)} />}
        <Body phase={phase} shipments={shipments.length} onRetry={() => setRetryTick((n) => n + 1)}
          onAdd={() => setAdding(true)} onImport={() => fileRef.current?.click()} onSample={() => void loadSample()}>
          {doc && (
            <>
              {choice.kind === 'none' && <div className="sh-chips" data-testid="sh-pick"><Chip chip="INFO" word="Pick a disruption" /></div>}
              <Summary doc={doc} onPlace={(id) => st.selectNode(id)} />
              <McOverlay mc={doc.mc} lanes={doc.by_lane} />
              <section className="sh-sec" aria-label="Shipments" data-testid="sh-table-sec">
                <div className="sh-sech">
                  <span className="sh-st">Shipments</span>
                  <div className="sh-filters" role="group" aria-label="Filter">
                    {FILTERS.map((f) => (
                      <button key={f.key} type="button" className={`sh-fchip${filter === f.key ? ' on' : ''}`} aria-pressed={filter === f.key} onClick={() => setFilter(f.key)}>
                        {f.label}<b className="sh-n">{counts[f.key]}</b>
                      </button>
                    ))}
                  </div>
                  <span className="sh-spacer" />
                  <input className="sh-input sh-search" type="search" placeholder="Filter" aria-label="Filter shipments" value={query} onChange={(e) => setQuery(e.target.value)} />
                </div>
                <div className="sh-tablewrap">
                  <table className="sh-table" data-testid="sh-table">
                    <thead><tr>
                      <Th k="id" sort={sort} onSort={sortBy}>Shipment</Th>
                      <Th k="lane" sort={sort} onSort={sortBy}>Lane</Th>
                      <th scope="col">Mode</th>
                      <th scope="col" className="num">Quantity</th>
                      <Th k="status" sort={sort} onSort={sortBy}>Status</Th>
                      <Th k="added" sort={sort} onSort={sortBy} num>Added transit d</Th>
                      <th scope="col" className="num">Range d</th>
                      <Th k="value" sort={sort} onSort={sortBy} num>Value USD</Th>
                      <th scope="col" className="num">Value in affected USD <Chip chip="MODELLED" /></th>
                      <th scope="col">Notes</th>
                      <th scope="col"><span className="sh-sr">Remove</span></th>
                    </tr></thead>
                    <tbody>
                      {shown.length === 0 && <tr><td colSpan={11}><StateLine chip="NO DATA" word="No shipments" /></td></tr>}
                      {shown.map((r) => <TableRow key={r.s.id} r={r} doc={doc} onRemove={remove} onText={setText} onPlace={(id) => st.selectNode(id)} />)}
                    </tbody>
                  </table>
                </div>
              </section>
              <details className="sh-method">
                <summary>Method</summary>
                <ul>
                  <li>Added transit: wait at arrival, named variants.</li>
                  <li>A reroute shows only when shorter.</li>
                  <li>Value is the listed value, not a loss estimate.</li>
                </ul>
              </details>
            </>
          )}
        </Body>
      </div>
    </Panel>
  );
}

function Body({ phase, shipments, onRetry, onAdd, onImport, onSample, children }: {
  phase: Phase; shipments: number; onRetry: () => void; onAdd: () => void; onImport: () => void; onSample: () => void; children: React.ReactNode;
}) {
  if (shipments === 0) {
    return (
      <div className="rk-state sh-empty" role="status" data-testid="sh-empty">
        <Chip chip="NO DATA" word="No shipments" />
        <button type="button" className="rk-btn" onClick={onAdd}>Add shipment</button>
        <button type="button" className="rk-btn" onClick={onImport}>Import CSV</button>
        <button type="button" className="rk-btn" onClick={onSample}>Sample list</button>
      </div>
    );
  }
  if (phase.s === 'idle' || phase.s === 'loading') {
    return phase.s === 'loading' ? <StateLine chip="INFO" word="Evaluating" /> : <StateLine chip="NO DATA" word="No result" />;
  }
  const r = phase.r;
  if (r.kind === 'notserved') return <StateLine chip="NOT SERVED" word="Not served" onRetry={onRetry} />;
  if (r.kind === 'down') return <StateLine chip="DOWN" word={failWord(r.code)} code={r.code} onRetry={onRetry} />;
  if (r.kind === 'refused') return <StateLine chip="FLAG" word={failWord(r.code)} code={r.code} />;
  return <>{children}</>;
}

function Th({ k, sort, onSort, num, children }: { k: SortKey; sort: SortState; onSort: (k: SortKey) => void; num?: boolean; children: React.ReactNode }) {
  const on = sort.key === k;
  return (
    <th scope="col" className={num ? 'num' : undefined} aria-sort={on ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
      <button type="button" className={`sh-sort${on ? ' on' : ''}`} onClick={() => onSort(k)}>{children}</button>
    </th>
  );
}

function Summary({ doc, onPlace }: { doc: NonNullable<Extract<EvalResult, { kind: 'ok' }>['doc']>; onPlace: (id: string) => void }) {
  const s = doc.summary;
  const prov = (field: ShipField, display: string) => () => shipmentProvenance({ doc, field, display });
  return (
    <section className="sh-sec sh-sum" aria-label="Summary" data-testid="sh-summary">
      <div className="sh-tiles">
        <Tile label="Shipments" value={String(s.n)} prov={prov('shipments', String(s.n))} />
        <Tile label="Affected" value={String(s.n_affected)} prov={prov('affected', String(s.n_affected))} sub={s.share_affected == null ? undefined : `${fmtPct(s.share_affected)} of served`} />
        <Tile label="Added transit, median per shipment" value={s.added_days_median_range ? `${fmtD(s.added_days_median_range.low)} to ${fmtD(s.added_days_median_range.high)} d` : '—'} prov={s.added_days_median_range ? prov('added', `${fmtD(s.added_days_median_range.low)} to ${fmtD(s.added_days_median_range.high)} d`) : undefined} />
        <Tile label="Value in affected shipments" value={`${fmtUsdShort(s.value_in_affected_usd)} USD`} chip="MODELLED" prov={prov('value', `${fmtUsdShort(s.value_in_affected_usd)} USD`)} title={`${fmtUsd(s.value_in_affected_usd)} USD`} />
        {s.n_not_served > 0 && <Tile label="Not served" value={String(s.n_not_served)} />}
        {s.n_unresolved > 0 && <Tile label="Unresolved" value={String(s.n_unresolved)} />}
      </div>
      <div className="sh-chips">
        <MetaChip>{doc.scenario.label}</MetaChip>
        <MetaChip>{softenNegation(doc.label)}</MetaChip>
        {s.most_exposed_places.length > 0 && <span className="sh-lab">Most exposed places</span>}
        {s.most_exposed_places.slice(0, 5).map((p) => (
          <button key={p.place} type="button" className="sh-place" onClick={() => onPlace(p.place)} aria-label={`Open ${p.name}`}>{p.name}<b className="sh-n">{p.n}</b></button>
        ))}
      </div>
    </section>
  );
}

function McOverlay({ mc, lanes }: { mc: import('./types').McBlock | null | undefined; lanes: import('./types').EvalLane[] }) {
  const v = mcView(mc, (k) => { const l = lanes.find((x) => x.lane_key === k); return l ? `${l.origin_name} to ${l.destination_name}` : k; });
  if (v.state === 'none') return null;
  return (
    <section className="sh-sec" aria-label="Monte Carlo overlay" data-testid="sh-mc">
      <div className="sh-chips">
        <span className="sh-st">Monte Carlo overlay</span>
        {v.chips.map((c, i) => <Chip key={i} chip={c.chip} word={c.word} />)}
      </div>
      {v.rows.length > 0 && (
        <table className="sh-table" data-testid="sh-mc-lanes">
          <thead><tr><th>Lane</th><th className="num">Added transit, d</th><th className="num">Places</th></tr></thead>
          <tbody>{v.rows.map((r) => <tr key={r.lane_key}><td>{r.name}</td><td className="num">{r.range}</td><td className="num">{r.places}</td></tr>)}</tbody>
        </table>
      )}
    </section>
  );
}

function Tile({ label, value, sub, chip, title, prov }: { label: string; value: string; sub?: string; chip?: string; title?: string; prov?: () => import('../../provenance/registry').ProvRef }) {
  return (
    <div className="sh-tile">
      <span className="sh-lab">{label}</span>
      <span className="sh-val" title={title}>{prov ? <ProvenanceCell make={prov}>{value}</ProvenanceCell> : value}</span>
      <span className="sh-tsub">{sub ? <span>{sub}</span> : null}{chip ? <Chip chip={chip} /> : null}</span>
    </div>
  );
}

function TableRow({ r, doc, onRemove, onText, onPlace }: {
  r: Row; doc: import('./types').EvalDoc | null; onRemove: (id: string) => void; onText: (id: string, f: 'origin' | 'destination' | 'via', t: string) => void; onPlace: (id: string) => void;
}) {
  const chips: Array<{ chip: string; word: string }> = [];
  if (r.originQ && r.originQ !== 'exact' && r.originQ !== 'id') chips.push({ chip: 'INFO', word: `Origin ${(QUALITY_WORD[r.originQ] ?? r.originQ).toLowerCase()}` });
  if (r.destQ && r.destQ !== 'exact' && r.destQ !== 'id') chips.push({ chip: 'INFO', word: `Destination ${(QUALITY_WORD[r.destQ] ?? r.destQ).toLowerCase()}` });
  return (
    <tr className={`sh-row sh-row--${r.status}`} data-status={r.status}>
      <th scope="row" className="sh-id">{r.s.id}</th>
      <td className="sh-lane">
        {r.origin && r.destination ? (
          <>
            <button type="button" className="sh-link" onClick={() => onPlace(r.origin!.id)}>{r.origin.name}</button>
            <span className="sh-to"> to </span>
            <button type="button" className="sh-link" onClick={() => onPlace(r.destination!.id)}>{r.destination.name}</button>
          </>
        ) : (
          <span>{r.s.origin} to {r.s.destination}</span>
        )}
      </td>
      <td>{MODE_WORD[r.s.mode] ?? r.s.mode}</td>
      <td className="num">{fmtQty(r.s)}</td>
      <td>{r.evaluated ? <Chip chip={STATUS_CHIP[r.status]} word={STATUS_WORD[r.status]} /> : <MetaChip>Pending</MetaChip>}</td>
      <td className="num">{r.added && doc && (r.status === 'delayed' || r.status === 'rerouted')
        ? <ProvenanceCell make={() => shipmentProvenance({ doc, field: 'row-added', display: `${fmtD(r.added!.median)} d`, shipment: doc.shipments.find((x) => x.id === r.s.id) })}>{fmtD(r.added.median)}</ProvenanceCell>
        : r.added && (r.status === 'delayed' || r.status === 'rerouted') ? fmtD(r.added.median) : r.status === 'unaffected' ? '0.0' : '—'}</td>
      <td className="num">{r.added && (r.status === 'delayed' || r.status === 'rerouted') ? fmtRangeD(r.added) : '—'}</td>
      <td className="num">{fmtUsd(r.value)}</td>
      <td className="num">{r.affectedValue == null ? '—' : fmtUsd(r.affectedValue)}</td>
      <td className="sh-notes">
        <span className="sh-chiprow">
          {r.route && <MetaChip>{r.route}</MetaChip>}
          {r.chips.map((c) => <MetaChip key={c}>{c}</MetaChip>)}
          {chips.map((c) => <Chip key={c.word} chip={c.chip} word={c.word} />)}
          {r.unresolved.map((u) => (
            <span key={u.field} className="sh-unres">
              <Chip chip="NO DATA" word={`${u.field === 'origin' ? 'Origin' : u.field === 'destination' ? 'Destination' : 'Via'} unresolved`} />
              {u.suggestions.slice(0, 3).map((s) => (
                <button key={s.id} type="button" className="rk-btn" aria-label={`Use ${s.name} as ${u.field}`} onClick={() => onText(r.s.id, u.field, s.name)}>{s.name}</button>
              ))}
            </span>
          ))}
        </span>
      </td>
      <td><IconButton icon="x" label={`Remove ${r.s.id}`} size="sm" onClick={() => onRemove(r.s.id)} /></td>
    </tr>
  );
}
