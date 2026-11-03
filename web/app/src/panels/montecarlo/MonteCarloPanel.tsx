import React, { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { Panel } from '../../components/Panel';
import { labelWord } from '../../copy';
import { safeStorage, useDeskStore } from '../../store';
import { sendRunToShipments } from '../../handoffs/install';
import { mcRunHandoff } from '../../handoffs/pick';
import { Button, IconButton, Tabs } from '../../ui';
import { fetchScenarios } from '../laneplanner/client';
import { placeChoices } from '../laneplanner/logic';
import type { Scenario } from '../laneplanner/types';
import { Chip, MetaChip, StateLine } from '../reports/chips';
import { cancelJob, getAllSamples, getCompare, getJob, listJobs, startJob, type McJobRow } from './client';
import type { CompareDoc } from './compare';
import { ctxOf, ctxOfPair, samplesTable } from './exportChart';
import { useMcReportExportables } from './exportReport';
import { fmtInt, seedWord } from './fmt';
import { ComparePane } from './ComparePane';
import { LabelChip, SourceChip } from './parts';
import { loadRecorded, loadRecordedCompare, RECORDED_IDS } from './recorded';
import { metricKeys, Results, type View } from './Results';
import { addRun, defaultPair, removeRun, runKey, runSub, runTitle, validPair, type RunRec } from './runs';
import { Setup } from './Setup';
import { INITIAL, LIVE_DEPS, reducer, runJob } from './state';
import { forgetToken, keepingToken, loadTokens } from './tokens';
import type { McFixture, McRequest } from './types';
import '../../charts/charts.css';
import './montecarlo.css';

/* MONTE CARLO (MCS). A seeded input-uncertainty simulation over one disruption: the engine stays deterministic, a
 * sampler outside it draws the inputs from stated ranges, and the results read as bands (P10, P50, P90), distributions
 * and "k of N simulations". Two or more runs compare side by side. Every figure is MODELLED; a recorded run says
 * RECORDED and is never shown as a served one; a route that is not served says NOT SERVED. */

type Tab = View | 'compare';
type ServerState = 'unknown' | 'ok' | 'notserved' | 'down';

/** Runs and choices kept while the window is closed and opened again. */
const SESSION: { runs: RunRec[]; cur: string | null; a: string | null; b: string | null; tab: Tab; metric: string } = { runs: [], cur: null, a: null, b: null, tab: 'bands', metric: 'worst_added_transit_d' };

const placeWord = (id: string): string => labelWord(id.replace(/^(chk|sea|air|gnd)_/, ''));

export function MonteCarloPanel() {
  const st = useDeskStore();
  const places = useMemo(() => placeChoices(st.graph.nodes), [st.graph.nodes]);
  const nameOf = useCallback((id: string) => places.find((p) => p.id === id)?.name ?? placeWord(id), [places]);
  const chainSpec = st.runMeta?.spec ?? null;

  const [runs, setRunsRaw] = useState<RunRec[]>(SESSION.runs);
  const [cur, setCurRaw] = useState<string | null>(SESSION.cur);
  const [pair, setPairRaw] = useState<{ a: string | null; b: string | null }>({ a: SESSION.a, b: SESSION.b });
  const [tab, setTabRaw] = useState<Tab>(SESSION.tab);
  const [metric, setMetricRaw] = useState<string>(SESSION.metric);
  const setRuns = (r: RunRec[]) => { SESSION.runs = r; setRunsRaw(r); };
  const setCur = (k: string | null) => { SESSION.cur = k; setCurRaw(k); };
  const setPair = (p: { a: string | null; b: string | null }) => { SESSION.a = p.a; SESSION.b = p.b; setPairRaw(p); };
  const setTab = (t: Tab) => { SESSION.tab = t; setTabRaw(t); };
  const setMetric = (m: string) => { SESSION.metric = m; setMetricRaw(m); };

  const [scenarios, setScenarios] = useState<Scenario[] | null | undefined>(undefined);
  const [server, setServer] = useState<ServerState>('unknown');
  const [jobRows, setJobRows] = useState<McJobRow[]>([]);
  const [recDoc, setRecDoc] = useState<CompareDoc | null>(null);
  const [servedDoc, setServedDoc] = useState<{ key: string; doc: CompareDoc } | null>(null);
  const [sPerRun, setSPerRun] = useState<number | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [s, dispatch] = useReducer(reducer, INITIAL);
  const abort = useRef<AbortController | null>(null);
  const [settled, setSettled] = useState<string | null>(null);
  const committed = useRef<string | null>(null);

  const rs = useRef(runs); rs.current = runs;
  const absorb = useCallback((rec: RunRec) => {
    const next = addRun(rs.current, rec);
    setRuns(next);
    setCur(rec.key);
    setPair(defaultPair(next));
  }, []);

  /* the library, and whether the routes are served at all */
  useEffect(() => {
    const ctrl = new AbortController();
    void fetchScenarios(ctrl.signal).then(setScenarios);
    void listJobs(ctrl.signal).then((r) => {
      if (ctrl.signal.aborted) return;
      if (r.kind === 'ok') { setServer('ok'); setJobRows(r.doc.filter((j) => j.status === 'done')); } else setServer(r.kind === 'down' ? 'down' : 'notserved');
    });
    return () => ctrl.abort();
  }, []);
  useEffect(() => () => abort.current?.abort(), []);

  /* recorded runs */
  const showRecorded = useCallback(async () => {
    try {
      const { a, b, doc } = await loadRecordedCompare();
      const mk = (f: McFixture): RunRec => ({ key: runKey('recorded', f.job.job_id), job: f.job, samples: f.samples, source: 'recorded' });
      let next = rs.current;
      next = addRun(next, mk(a)); next = addRun(next, mk(b));
      setRuns(next);
      setCur(runKey('recorded', a.job.job_id));
      setPair({ a: runKey('recorded', a.job.job_id), b: runKey('recorded', b.job.job_id) });
      setRecDoc(doc);
    } catch { setNote('Recorded run not available'); }
  }, []);
  const nothingToShow = useRef(true);
  useEffect(() => {
    /* the routes are not served and the window is empty: the recorded run is the only thing to read */
    if ((server === 'notserved' || server === 'down') && rs.current.length === 0 && nothingToShow.current) { nothingToShow.current = false; void showRecorded(); }
  }, [server, showRecorded]);

  /* a run in flight */
  const onRun = (req: McRequest) => {
    abort.current?.abort();
    const ctrl = new AbortController();
    abort.current = ctrl;
    setNote(null);
    /* an identical request returns the same job id: let it be absorbed again (or restored after a removal) */
    committed.current = null;
    setSettled(null);
    void runJob(req, dispatch, ctrl.signal, { ...LIVE_DEPS, startJob: keepingToken(safeStorage, startJob) }).then((id) => { if (!ctrl.signal.aborted && id) setSettled(id); });
  };
  useEffect(() => {
    if (!settled || s.jobId !== settled || s.phase !== 'done' || !s.job?.results || committed.current === settled) return;
    committed.current = settled;
    if (s.job.quote?.s_per_run) setSPerRun(s.job.quote.s_per_run);
    setServer('ok');
    absorb({ key: runKey('server', s.job.job_id), job: s.job, samples: s.samples, source: 'server' });
    dispatch({ t: 'reset' });
  }, [settled, s, absorb]);
  const onCancel = () => {
    abort.current?.abort();
    const id = s.jobId;
    if (id) {
      const token = loadTokens(safeStorage)[id];
      if (token) void cancelJob(id, token).then((r) => { if (r.kind === 'ok' || (r.kind === 'refused' && r.code === 'montecarlo_not_found')) forgetToken(safeStorage, id); });
      else setNote('Not yours');                      /* the job was created elsewhere: it keeps running there */
    }
    dispatch({ t: 'cancelled' });
  };

  /* a finished job from the server's list */
  const openJob = async (id: string) => {
    if (!id) return;
    const key = runKey('server', id);
    if (rs.current.some((r) => r.key === key)) { setCur(key); return; }
    const j = await getJob(id);
    if (j.kind !== 'ok' || !j.doc.results) { setNote('Run not available'); return; }
    const samples = await getAllSamples(id);
    absorb({ key, job: j.doc, samples, source: 'server' });
  };

  const drop = (key: string) => {
    const next = removeRun(runs, key);
    setRuns(next);
    if (cur === key) setCur(next.length ? next[next.length - 1].key : null);
    setPair(validPair(next, pair.a === key ? null : pair.a, pair.b === key ? null : pair.b));
  };

  /* the pair being compared, and its served comparison */
  const pr = validPair(runs, pair.a, pair.b);
  const runA = runs.find((r) => r.key === pr.a) ?? null;
  const runB = runs.find((r) => r.key === pr.b) ?? null;
  useEffect(() => {
    if (!runA || !runB || runA.source !== 'server' || runB.source !== 'server') { setServedDoc(null); return; }
    const key = `${runA.job.job_id}|${runB.job.job_id}`;
    const ctrl = new AbortController();
    void getCompare(runA.job.job_id, runB.job.job_id, undefined, ctrl.signal).then((r) => { if (!ctrl.signal.aborted) setServedDoc(r.kind === 'ok' ? { key, doc: r.doc } : null); });
    return () => ctrl.abort();
  }, [runA?.key, runB?.key]); // eslint-disable-line react-hooks/exhaustive-deps
  const compareDoc: CompareDoc | null = runA && runB
    ? (runA.source === 'recorded' && runB.source === 'recorded' && runA.job.job_id === RECORDED_IDS.d14 && runB.job.job_id === RECORDED_IDS.d28 ? recDoc
      : servedDoc && servedDoc.key === `${runA.job.job_id}|${runB.job.job_id}` ? servedDoc.doc : null)
    : null;
  const onPick = (role: 'a' | 'b', key: string) => {
    const other = role === 'a' ? pr.b : pr.a;
    const next = key === other ? { a: role === 'a' ? key : pr.b, b: role === 'a' ? pr.a : key } : { a: role === 'a' ? key : pr.a, b: role === 'b' ? key : pr.b };
    setPair(next);
  };

  const curRun = runs.find((r) => r.key === cur) ?? runs[runs.length - 1] ?? null;
  useMcReportExportables(curRun, runA, runB, compareDoc, nameOf);
  const hasResult = !!curRun?.job.results;
  const recordedLoaded = runs.some((r) => r.source === 'recorded');
  const openable = jobRows.filter((j) => !runs.some((r) => r.key === runKey('server', j.job_id)));
  const stamp = curRun ? `${curRun.source === 'recorded' ? 'RECORDED' : 'SIMULATED'} · ${runSub(curRun)}` : 'NO RUN';
  const keys = hasResult ? metricKeys(curRun!.job.results!) : [];
  const metricNow = keys.includes(metric) ? metric : keys[0] ?? metric;

  return (
    <Panel id="montecarlo" code="MCS" title="Monte Carlo" updated={stamp} className="mc-panel"
      exportSpec={{
        csv: curRun?.samples ? () => { const t = samplesTable(curRun.job, curRun.samples!); return t.rows.map((r) => Object.fromEntries(t.columns.map((c, i) => [c, r[i]]))); } : undefined,
        json: curRun ? () => ({ job: curRun.job, source: curRun.source }) : undefined,
      }}>
      <div className="mc-root" data-testid="mc-root">
        <Setup places={places} scenarios={scenarios ?? null} chainSpec={chainSpec} runs={runs} nameOf={nameOf} state={s} sPerRun={sPerRun} onRun={onRun} onCancel={onCancel} />

        <div className="mc-runsbar" data-testid="mc-runs">
          {server === 'notserved' && <Chip chip="NOT SERVED" word="Simulation service not served" />}
          {server === 'down' && <Chip chip="DOWN" word="Simulation service down" />}
          {runs.length > 0 && (
            <ul className="mc-runs" aria-label="Runs">
              {runs.map((r) => {
                const role = r.key === pr.a ? 'A' : r.key === pr.b ? 'B' : null;
                return (
                  <li key={r.key} className={`mc-run${r.key === curRun?.key ? ' on' : ''}`}>
                    <button type="button" className="mc-run-b" aria-pressed={r.key === curRun?.key} onClick={() => { setCur(r.key); if (tab === 'compare') setTab('bands'); }}>
                      {role && <i className={`mc-dot mc-dot--${role.toLowerCase()}`} role="img" aria-label={role} />}
                      <span className="mc-run-t">{runTitle(r, nameOf)}</span>
                      <span className="mc-run-s">{runSub(r)}</span>
                      {r.source === 'recorded' && <Chip chip="RECORDED" tone="snapshot" />}
                    </button>
                    <IconButton icon="x" label="Remove run" size="sm" onClick={() => drop(r.key)} />
                  </li>
                );
              })}
            </ul>
          )}
          {openable.length > 0 && (
            <span className="mc-open">
              <label htmlFor="mc-open" className="mc-lab">Open</label>
              <select id="mc-open" className="mc-input" value="" onChange={(e) => void openJob(e.target.value)}>
                <option value="">Server runs</option>
                {openable.map((j) => <option key={j.job_id} value={j.job_id}>{`${placeWord(j.request.disruption.node_id)} ${labelWord(j.request.disruption.kind).toLowerCase()} ${j.request.disruption.duration_d} d · n ${j.request.n}`}</option>)}
              </select>
            </span>
          )}
          {!recordedLoaded && <button type="button" className="rk-btn" data-testid="mc-recorded" onClick={() => void showRecorded()}>Recorded run</button>}
          {note === 'Not yours' ? <MetaChip>Not yours</MetaChip> : note && <Chip chip="NOT SERVED" word={note} />}
        </div>

        {!curRun && <StateLine chip="NO DATA" word="No run" />}
        {curRun && (
          <>
            <header className="mc-head">
              <h2 className="mc-title">{runTitle(curRun, nameOf)}</h2>
              <LabelChip label={curRun.job.label} />
              <SourceChip recorded={curRun.source === 'recorded'} />
              <MetaChip>{curRun.job.engine?.engine_label ? curRun.job.engine.engine_label : `Rev ${curRun.job.request.engine_rev}`}</MetaChip>
              <MetaChip>{`${fmtInt(curRun.job.request.n)} simulations`}</MetaChip>
              <MetaChip>{`Seed ${seedWord(curRun.job.request.seed)}`}</MetaChip>
              {curRun.job.results && !curRun.samples && <Chip chip="NOT SERVED" word="Samples not served" />}
              {curRun.job.results && mcRunHandoff(curRun.job, curRun.source) && (
                <Button size="sm" icon="package" className="mc-use" data-testid="mc-use-shipments" onClick={() => sendRunToShipments(curRun.job, curRun.source)}>Use in My shipments</Button>
              )}
            </header>
            <Tabs<Tab> label="View" value={tab} onChange={setTab}
              options={[{ value: 'bands', label: 'Bands' }, { value: 'distribution', label: 'Distribution' }, { value: 'drivers', label: 'Drivers' }, { value: 'compare', label: 'Compare', note: runs.length }]} />
            {tab !== 'compare' && hasResult && (
              <Results run={curRun} view={tab} ctx={ctxOf(curRun.job, nameOf, curRun.source === 'recorded')} metric={metricNow} onMetric={setMetric} />
            )}
            {tab === 'compare' && (
              <ComparePane runs={runs} a={runA} b={runB} onPick={onPick} nameOf={nameOf} doc={compareDoc}
                ctxFor={(a, b) => ctxOfPair(a.job, b.job, nameOf, a.source === 'recorded' || b.source === 'recorded')} metric={metricNow} onMetric={setMetric} />
            )}
            {!hasResult && <StateLine chip="NOT SERVED" word="Not served" />}
          </>
        )}
      </div>
    </Panel>
  );
}
