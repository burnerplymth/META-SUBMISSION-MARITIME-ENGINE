import React, { useEffect, useMemo } from 'react';
import { nodeName } from '../seed';
import { useDeskStore, useSimStore } from '../store';
import { simClockReadout } from '../../../map/src/sim/phaseD';
import type { CausalEvent } from '../types';
import { Chip } from './Chip';
import { aggregateWord, eventLabel } from './truthLabel';
import type { StateKind } from './stateChips';
import { labelWord } from '../copy';

/** One KPI tile: label, value with its unit,
 *  one sub-line, a state chip at the top right. No sparklines. */
export function Tile({ label, value, unit, sub, kind, word, title, className = '', absent }:
  { label: string; value: string; unit?: string | null; sub?: string; kind: StateKind; word?: string; title?: string; className?: string;
    /** No figure to show: the value line is a state chip (NOT SERVED, or NO CHAIN while none is loaded), never a bare dash. */
    absent?: 'notserved' | 'nochain' }) {
  return (
    <div className={`kpi tile ${className}`.trim()} title={title}>
      <div className="kl"><span title={label}>{label}</span><Chip kind={kind} word={word} /></div>
      <div className="kv">{absent ? <Chip kind={absent === 'nochain' ? 'meta' : 'notserved'} word={absent === 'nochain' ? 'No chain' : undefined} /> : value}{unit ? <small>{unit}</small> : null}</div>
      <div className="ks" title={sub || undefined}>{sub ?? ''}</div>
    </div>
  );
}

/** The state of a tile with no figure: NOT SERVED
 *  when the run is loaded but the figure is not served, NO CHAIN while none is. */
export const absentOf = (missing: boolean, noChain: boolean): 'notserved' | 'nochain' | undefined =>
  (missing ? (noChain ? 'nochain' : 'notserved') : undefined);

const compact = (n: number): string => (n >= 1000 ? `${Math.round(n / 1000)}k` : `${Math.round(n)}`);

/** Headline numbers of a loaded sim state, read straight from the served
 *  tick state: the longest wait among queued vehicles (hours), the
 *  queued count and how many shocks are in force. Pure;
 *  null fields when there is no state. */
export function simStateFigures(state: { entities: Array<{ state: string; queue_wait_h?: number }>; disruptions_active: string[] } | null | undefined) {
  if (!state) return { maxWaitH: null as number | null, queued: null as number | null, active: null as number | null };
  const queued = state.entities.filter((e) => e.state === 'QUEUED');
  const waits = queued.map((e) => e.queue_wait_h).filter((w): w is number => typeof w === 'number');
  return { maxWaitH: waits.length ? Math.max(...waits) : 0, queued: queued.length, active: state.disruptions_active.length };
}

/** SIMULATION tiles: every tile is SIM or MODELLED,
 *  none is a live-feed number. Own component so the playback tick
 *  re-renders these tiles and not the shell. */
function SimTiles() {
  const s = useSimStore();
  if (!s.simRun) {
    return <>
      <Tile label="Simulation run" value="—" sub="No run" kind="sim" word="Simulated" />
      <Tile label="Simulation clock" value="—" sub="No run" kind="sim" word="Simulated" />
      <Tile label="Queue wait" value="—" sub="No run" kind="sim" word="Simulated" />
      <Tile label="Baseline twin" value="—" sub="No run" kind="model" word="Modelled" />
    </>;
  }
  const ro = simClockReadout(s.simRun, s.simPlayhead);
  const fig = simStateFigures(s.simState);
  const base = s.simBaselineRun;
  return <>
    <Tile label="Simulation run" value={s.simRun.sim_run_id.slice(0, 10)} sub={`${s.simRun.ticks} ticks · ${s.simRun.journal.disruptions.length} shocks`} kind="sim" word="Simulated" />
    <Tile label="Simulation clock" value={ro.dateTime.replace(/\s*UTC$/, '')} unit="UTC" sub={`Tick ${ro.tickIndex + 1} / ${s.simRun.ticks}`} kind="sim" word="Simulated" className="tile--clock" />
    <Tile label="Queue wait" value={fig.maxWaitH === null ? '—' : fig.maxWaitH.toFixed(1)} unit="h" absent={fig.maxWaitH === null ? 'notserved' : undefined}
      sub={fig.queued === null ? '—' : `${fig.queued} queued · longest`} kind="sim" word="Simulated" />
    <Tile label="Shocks in force" value={fig.active === null ? '—' : String(fig.active)} unit={`of ${s.simRun.journal.disruptions.length}`} absent={fig.active === null ? 'notserved' : undefined}
      sub="This tick" kind="sim" word="Simulated" />
    <Tile label="Baseline twin" value={base ? base.sim_run_id.slice(0, 10) : '—'} sub={base ? (base.sim_run_id === s.simRun.sim_run_id ? 'Run is its own baseline' : 'Undisrupted twin') : 'Not attached'}
      kind="model" word="Modelled" />
  </>;
}

/** The KPI strip. LIVE: the six desk figures (unchanged derivations), each
 *  with its provenance chip. SIMULATION: only SIM / MODELLED tiles from
 *  the loaded run (wireframe 14.2); the live-feed tiles do not render
 *  there, so a LIVE chip never sits beside SIM CLOCK. */
export function Tiles() {
  const mode = useDeskStore().mode;
  return mode === 'sim'
    ? <div className="kpis tiles tiles--sim mono"><SimTiles /></div>
    : <LiveTiles />;
}

function LiveTiles() {
  const s = useDeskStore();
  const worst = useMemo(() => (s.chain?.events ?? []).reduce((m, e) => Math.max(m, e.delay_d), 0), [s.chain]);
  const worstEv = useMemo(() => (s.chain?.events ?? []).reduce<CausalEvent | null>((m, e) => (!m || e.delay_d > m.delay_d ? e : m), null), [s.chain]);
  const worstRoute = useMemo(() => {
    if (!worstEv || !s.chain) return null;
    const edge = s.chain.edges.find((e) => e.to_seq === worstEv.seq && e.from_seq !== null);
    return edge ? `${nodeName(edge.from_node)} → ${nodeName(worstEv.node_id)} · ${worstEv.channel}`
      : `${nodeName(worstEv.node_id)} · ${worstEv.channel}`;
  }, [worstEv, s.chain]);
  const activeFeed = useMemo(() => s.feed.filter((f) => f.status === 'active'), [s.feed]);
  const maxSev = useMemo(() => activeFeed.reduce((m, f) => Math.max(m, f.severity), 0), [activeFeed]);
  const topDisruptions = useMemo(() => [...activeFeed].sort((a, b) => b.severity - a.severity).slice(0, 3), [activeFeed]);
  // IMPACT tiles: the loaded run's impact payload, fetched once per run (the
  // store dedupes), read straight from it; '—' whenever no payload answers.
  useEffect(() => { if (s.chain?.run_id) void s.fetchImpactRun(s.chain.run_id); /* eslint-disable-next-line */ }, [s.chain?.run_id]);
  const impRun = s.chain && s.impactRun && s.impactRun.run_id === s.chain.run_id ? s.impactRun : null;
  const impDom = impRun ? [...impRun.volume_at_risk.by_unit].sort((a, b) => b.places.length - a.places.length)[0] : undefined;
  const impDomFig = impDom?.daily_volume_at_risk;
  const impHorizon = impRun?.volume_at_risk.clearance_horizon;
  /* TEU IN DISRUPTED PATHS: the sum of daily TEU capacity over the loaded
   * chain's affected port places: a derived capacity figure, never a
   * measured flow (hence MODELLED). */
  const teu = useMemo(() => {
    if (!s.chain) return null;
    const byId = new Map((s.graph.nodes as unknown as Array<{ id: string; capacity?: number; capacity_unit?: string }>).map((n) => [n.id, n]));
    let sum = 0; let ports = 0;
    for (const id of new Set(s.chain.events.map((e) => e.node_id))) {
      const n = byId.get(id);
      if (n && n.capacity_unit === 'TEU/day' && typeof n.capacity === 'number') { sum += n.capacity; ports += 1; }
    }
    return { sum, ports };
  }, [s.chain, s.graph]);

  /* TRUTH: the tile counts active records, so it wears what those records
   * earn now (feed state + age), never the fact that the fetch succeeded. */
  const feedTruth = useMemo(() => {
    const now = Date.now();
    return aggregateWord(activeFeed.map((f) => eventLabel(f, s.health, s.snapshotMode, now)));
  }, [activeFeed, s.health, s.snapshotMode]);
  const feedKind: StateKind = s.feedSource === 'fallback' ? 'snapshot' : s.feedSource === 'loading' ? 'meta'
    : feedTruth === 'LIVE' ? 'live' : feedTruth === null ? 'meta' : feedTruth === 'MODELLED' ? 'model' : 'snapshot';
  const feedWord = s.feedSource === 'fallback' ? 'Offline copy' : s.feedSource === 'loading' ? 'Loading'
    : feedTruth === 'RECORDED' ? 'Recorded' : undefined;
  return (
    <div className="kpis tiles mono">
      <Tile label="Active disruptions" value={String(activeFeed.length)} unit={maxSev ? `Sev ${maxSev}` : null}
        sub={topDisruptions.length ? topDisruptions.map((f) => `${nodeName(f.node_id)} ${f.severity}`).join(' · ') : '—'}
        kind={feedKind} word={feedWord} />
      <Tile label="TEU in disrupted paths" value={!teu || teu.ports === 0 ? '—' : compact(teu.sum)} unit="TEU/day" absent={absentOf(!teu || teu.ports === 0, !s.chain)}
        sub={teu && teu.ports > 0 ? `${teu.ports} ports` : '—'} kind="model" word="Modelled" />
      <Tile label="Worst added transit" value={s.chain ? `+${worst.toFixed(1)}` : '—'} unit="d" absent={absentOf(!s.chain, !s.chain)}
        sub={worstRoute ?? '—'} kind="model" word="Modelled" />
      {/* The headline is COVERAGE (observed places the model found), never the
          delay hit fraction alone: 3/3 delay hits would read as '100%'. */}
      <Tile label="Recorded replays" value={s.scorecard ? `${s.scorecard.aggregate.coverage.covered_count}/${s.scorecard.aggregate.coverage.covered_count + s.scorecard.aggregate.coverage.uncovered_count}` : '—'} unit="places" absent={absentOf(!s.scorecard, false)}
        sub={s.scorecard ? `${s.scorecard.aggregate.delay_accuracy.graded_steps} graded entries, ${s.scorecard.aggregate.delay_accuracy.hits} matched` : '—'} kind="meta" word="Descriptive" />
      <Tile label="Volume at risk" value={impDomFig?.value != null ? compact(impDomFig.value) : '—'} unit={impDomFig?.value != null && impDom ? labelWord(impDom.unit) : null}
        absent={absentOf(impDomFig?.value == null, !s.chain)}
        sub={impDomFig?.value != null && impDom ? `${impDom.places.length} places` : '—'}
        kind="model" word="Modelled" />
      <Tile label="Clearance horizon" value={impHorizon?.value != null ? impHorizon.value.toFixed(1) : '—'} unit="d" absent={absentOf(impHorizon?.value == null, !s.chain)}
        sub={impHorizon?.value != null ? 'Last place clears' : '—'} kind="model" word="Modelled" />
    </div>
  );
}
