/**
 * ENSEMBLE STATISTICS — panel with the ENSEMBLE EXPLORER: three modes over one layer.
 * STATISTICS is the flat surface (variant jobs over the
 * declared axes: outcome statistics with percentiles, the
 * distribution curve, per-place / per-leg branch statistics, firing
 * variants, variant chains replayed through the chain view).
 * COMBINATION TREE is the precomputed mode (quote-gated per-level
 * branching, a member explorer with subtree aggregates). EXPLORATIONS
 * is interactive deepening (saved records whose members are flat
 * jobs, deepened one variant at a time). The aggregate blocks are
 * shared across all three (./enaggregate.tsx) — the modes serve the
 * same shapes under the same labelling by design.
 *
 * Honesty rules, enforced in the rendering, not just the copy:
 *  - the payload's own labelling title + statement print beside the
 *    numbers, before and after any job;
 *  - a share is ALWAYS the count form ("reached in X of N variants")
 *    — no percentage is ever rendered for a share;
 *  - when the requested count exceeds the declared grid, the API's
 *    count note prints where the count is chosen and the run number
 *    shown is the planned/run figure, never the requested one;
 *  - a persisted job with no live worker reads INTERRUPTED (the API's
 *    word), never a spinner;
 *  - places are named with their kind as the sub-line — no raw
 *    identifier reaches the screen;
 *  - while the AUTO DEMO runs, the tree and exploration actions are
 *    inert (the search surface's discipline).
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Panel } from '../components/Panel';
import { api, pollBackoffMs } from '../api';
import { useDeskStore } from '../store';
import { fmtDayTime, humaniseProse, nodeLabel, prettyKind } from '../format';
import type {
  DisruptionSpec, EnsStatsBranches, EnsStatsJobSummary, EnsStatsResults, EnsVariantData,
} from '../types';
import {
  ENS_COPY, fmtInt, parseCount, progressText, specKey, statusWord, variantChain,
} from './ensstats';
import { failureSentence } from './failureText';
import { JobAggregates, cleanErr } from './enaggregate';
import { TreeSection } from './entree';
import { ExplorationsSection } from './enexplorations';
import { consumeExploration, registerExplorationConsumer } from '../search/navBridge';
import { ENX_COPY } from './enexplorer';

const COUNTS = [100, 1000, 10000];

type Mode = 'stats' | 'tree' | 'explorations';

export function EnsembleStatsPanel({ demoActive = false }: { demoActive?: boolean }) {
  const s = useDeskStore(); // desk-slice consumer
  const spec: DisruptionSpec = useMemo(() => {
    if (s.runMeta?.spec) return s.runMeta.spec;
    const root = s.chain?.events[0]?.node_id ?? s.selectedNode ?? 'chk_suez';
    return { node_id: root, kind: 'closure', capacity_loss: 1.0, duration_d: 7, shape: 'step' };
  }, [s.runMeta, s.chain, s.selectedNode]);
  const derived = !s.runMeta?.spec;

  const [mode, setMode] = useState<Mode>('stats');
  /* Search nav bridge: an exploration request switches the
   * mode and hands the id to the explorations section, which opens
   * it through its existing load path. */
  const [navExplorationId, setNavExplorationId] = useState<string | null>(null);
  useEffect(() => {
    const onNav = () => {
      const id = consumeExploration();
      if (id) { setMode('explorations'); setNavExplorationId(id); }
    };
    registerExplorationConsumer(onNav);
    return () => registerExplorationConsumer(null);
  }, []);
  const [count, setCount] = useState<number>(1000);
  const [custom, setCustom] = useState(false);
  const [customText, setCustomText] = useState('2500');
  const [countError, setCountError] = useState<string | null>(null);
  const [job, setJob] = useState<EnsStatsJobSummary | null>(null);
  const [jobs, setJobs] = useState<EnsStatsJobSummary[]>([]);
  const [exportData, setExportData] = useState<{ results: EnsStatsResults; branches: EnsStatsBranches } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const onError = useCallback((msg: string) => setError(msg), []);
  const onLoaded = useCallback((results: EnsStatsResults, branches: EnsStatsBranches) => {
    setExportData({ results, branches });
  }, []);

  const refreshList = useCallback(async (autoLoad: boolean) => {
    try {
      const { jobs: list } = await api.ensStatsList();
      setJobs(list);
      if (autoLoad) {
        const newest = list[0];
        if (newest && (newest.status === 'complete' || newest.status === 'running')) setJob(newest);
      }
    } catch { /* the list is a convenience; its absence asserts nothing */ }
  }, []);

  useEffect(() => { void refreshList(true); }, [refreshList]);

  /* Poll the active job while the API says it is running. A persisted
   * record with no worker arrives as INTERRUPTED and stops the poll —
   * the word renders; nothing spins over it. JobAggregates loads the
   * results itself when the status flips to complete. */
  /* the poll backs off on consecutive failures (pollBackoffMs:
   *  1200 ms base, doubling to a 30 s cap, reset on success) instead
   *  of hammering a failing service at a fixed 1200 ms forever. */
  useEffect(() => {
    if (!job || job.status !== 'running') return;
    let dead = false;
    let failures = 0;
    let timer: number | null = null;
    const poll = async () => {
      try {
        const fresh = await api.ensStatsJob(job.id);
        if (dead) return;
        failures = 0;
        setJob(fresh);
        if (fresh.status !== 'running') void refreshList(false);
      } catch (e) {
        if (dead) return;
        failures += 1;
        setError(cleanErr(e));
      }
      if (!dead) timer = window.setTimeout(() => void poll(), pollBackoffMs(failures));
    };
    timer = window.setTimeout(() => void poll(), pollBackoffMs(0));
    pollRef.current = timer;
    return () => { dead = true; if (timer) window.clearTimeout(timer); };
  }, [job, refreshList]);

  const run = async () => {
    let n = count;
    if (custom) {
      const parsed = parseCount(customText);
      if ('error' in parsed) { setCountError(parsed.error); return; }
      n = parsed.count;
    }
    setCountError(null); setError(null); setBusy('run');
    setExportData(null);
    try {
      const summary = await api.ensStatsCreate(spec, n);
      setJob(summary);
      void refreshList(false);
    } catch (e) { setError(cleanErr(e)); } finally { setBusy(null); }
  };

  const openStoredJob = (j: EnsStatsJobSummary) => {
    setJob(j); setError(null); setExportData(null);
  };

  const openFlatVariant = useCallback((data: EnsVariantData, index: number) => {
    const label = `ENSEMBLE VARIANT ${index} — ${nodeLabel(spec.node_id, s.graph)}`;
    s.openVariantChain(variantChain(data), label);
    s.setFocus('chain');
  }, [s, spec]);

  const stale = job !== null && specKey(job.disruption) !== specKey(spec);
  const running = job?.status === 'running';
  const updated = !job ? 'ON DEMAND'
    : running ? `${statusWord(job.status)} — ${progressText(job.progress.done, job.progress.planned)}`
    : `${statusWord(job.status)} · ${fmtInt(job.variants_planned)} VARIANTS`;
  const labelling = job?.labelling;

  return (
    <Panel id="ensemble" code="ENS" title={ENS_COPY.title} updated={updated}
      exportSpec={{ json: () => (exportData ? { results: exportData.results, branches: exportData.branches } : null) }}>
      {/* ── Labelling: the payload's own words, beside the numbers ── */}
      <p className="honest"><b>{labelling?.title ?? ENS_COPY.labellingTitle}</b></p>
      <details className="sim-method" data-methodology><summary className="mono">Methodology</summary>
        <p className="fineprint">{labelling?.statement ?? ENS_COPY.labellingStatement}</p>
      </details>
      <p className="fineprint">Context · {derived
        ? <>closure at {nodeLabel(spec.node_id, s.graph)} for {spec.duration_d} d · {ENS_COPY.contextDerived}</>
        : <>{nodeLabel(spec.node_id, s.graph)} · {prettyKind(spec.kind)} · loss {spec.capacity_loss} · {spec.duration_d} d · {spec.shape} · {ENS_COPY.contextStored}</>}
      </p>
      {error && <p className="honest">{ENS_COPY.unreachable} — {error}.</p>}

      {/* ── Mode switch: the three surfaces over the one layer ── */}
      <div className="ens-controls">
        <span className="seg" role="group" aria-label="Ensemble view">
          <button type="button" className={`seg-b${mode === 'stats' ? ' on' : ''}`} aria-pressed={mode === 'stats'} onClick={() => setMode('stats')}>{ENX_COPY.modeStats}</button>
          <button type="button" className={`seg-b${mode === 'tree' ? ' on' : ''}`} aria-pressed={mode === 'tree'} onClick={() => setMode('tree')}>{ENX_COPY.modeTree}</button>
          <button type="button" className={`seg-b${mode === 'explorations' ? ' on' : ''}`} aria-pressed={mode === 'explorations'} onClick={() => setMode('explorations')}>{ENX_COPY.modeExplorations}</button>
        </span>
      </div>

      {mode === 'stats' && (
        <>
          {/* ── Count selector + run ── */}
          <div className="ens-controls">
            <span className="seg" role="group" aria-label="Member count">
              {COUNTS.map((n) => (
                <button key={n} type="button" className={`seg-b${!custom && count === n ? ' on' : ''}`} aria-pressed={!custom && count === n}
                  onClick={() => { setCustom(false); setCount(n); setCountError(null); }}>{fmtInt(n)}</button>
              ))}
              <button type="button" className={`seg-b${custom ? ' on' : ''}`} aria-pressed={custom} onClick={() => setCustom(true)}>Custom</button>
            </span>
            {custom && (
              <input className="ens-count mono" value={customText} inputMode="numeric"
                onChange={(e) => setCustomText(e.target.value)} aria-label="Custom variant count" />
            )}
            <button className="btn btn--amber" onClick={() => void run()} disabled={busy === 'run' || running}>
              {busy === 'run' ? 'Starting…' : job ? ENS_COPY.rerunButton : ENS_COPY.runButton}
            </button>
          </div>
          {countError && <p className="honest">{countError}</p>}
          {job?.count_note && (
            <p className="honest"><b>{ENS_COPY.requested} {fmtInt(job.variants_requested)} · {ENS_COPY.runs} {fmtInt(job.variants_planned)}.</b>{' '}
              {humaniseProse(job.count_note, s.graph)}</p>
          )}
          {stale && <p className="fineprint">{ENS_COPY.staleNote}</p>}

          {/* ── Job state ── */}
          {running && job && (
            <div className="ens-jobstate">
              <div className="mono small">{statusWord(job.status)} — {progressText(job.progress.done, job.progress.planned)}</div>
              <div className="ens-progress"><div style={{ width: `${job.progress.planned ? (100 * job.progress.done) / job.progress.planned : 0}%` }} /></div>
            </div>
          )}
          {job?.status === 'interrupted' && (
            <p className="honest"><b>{statusWord(job.status)}</b> · {ENS_COPY.interruptedNote}</p>
          )}
          {job?.status === 'failed' && (
            <p className="honest"><b>{statusWord(job.status)}.</b> {job.error ? failureSentence(job.error.code) : ''}</p>
          )}

          {/* ── Aggregates (shared blocks) ── */}
          {job && (
            <JobAggregates
              key={job.id}
              jobId={job.id}
              status={job.status}
              onOpenVariant={openFlatVariant}
              onError={onError}
              onLoaded={onLoaded}
            />
          )}

          {/* ── Stored jobs ── */}
          <h5>{ENS_COPY.storedJobs}</h5>
          {jobs.length === 0 && <p className="dim">{ENS_COPY.noJobs}</p>}
          <table className="data-table">
            <tbody>{jobs.map((j) => (
              <tr key={j.id} className={job?.id === j.id ? 'row--sel' : ''} onClick={() => openStoredJob(j)} title="Open job">
                <td>{nodeLabel(j.disruption.node_id, s.graph)}
                  <div className="cell-sub">{prettyKind(j.disruption.kind)} · {fmtDayTime(j.created_at)}</div></td>
                <td className="mono">{j.count_note ? `${fmtInt(j.variants_requested)} → ${fmtInt(j.variants_planned)}` : fmtInt(j.variants_planned)}</td>
                <td className="mono">{statusWord(j.status)}</td>
              </tr>
            ))}</tbody>
          </table>
        </>
      )}

      {mode === 'tree' && (
        <TreeSection spec={spec} inert={demoActive} onError={onError} />
      )}

      {mode === 'explorations' && (
        <ExplorationsSection
          flatJobs={jobs}
          currentFlatJobId={job?.id ?? null}
          inert={demoActive}
          onError={onError}
          openExplorationId={navExplorationId}
          onExplorationOpened={() => setNavExplorationId(null)}
        />
      )}
    </Panel>
  );
}

export default EnsembleStatsPanel;
