import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Chip, EmptyState } from '../shell/Chip';
import { labelWord } from '../copy';
import { adapterStateKind } from '../shell/stateChips';
import { api, ApiError } from '../api';
import { Panel } from '../components/Panel';
import { SafeLink } from '../components/SafeLink';
import { KINDS, SHAPES, nodeName } from '../seed';
import { adapterLabel, cleanNarrative, fmtDayTime, humaniseIds, humaniseNote, humaniseProse, nodeLabel, nodeNameOptions, prettyKey, prettyKind, resolveAdapterInput, resolveNodeInput, runRef, satCounts, srcStamp, statusLabel, transportLabel } from '../format';
import { useStore } from '../store';
import { adapterFeedState, adapterRowView, feedState, feedStamp, healthModeCell, FEED_ADAPTERS } from '../shell/feedState';
import { BasisStamp } from '../shell/BasisStamp';
import { EnsembleStatsPanel } from './ensstatsPanel';
import { ExplainChips } from '../meta/ExplainChips';
import { AskChain } from '../meta/AskChain';
import { consumeWatchlist, registerWatchlistConsumer } from '../search/navBridge';
import type { AlertRule, AlertRuleType, DisruptionSpec, FiredAlert, LlmExplain, SatScan, SimulatedCostPressure, Watchlist, WatchlistView } from '../types';
import { Icon } from '../ui/Icon';
import { safeExcerpt } from '../contentGuard';
import { ALERT_STATE_DEFAULT, ALERT_STATE_OPTIONS, alertFactLabel, ledgerCounts, ledgerCountsLine, type AlertState } from './deskLogic';
import { MethodDisclosure } from './MethodDisclosure';
import { NarrativeDisclosure } from './NarrativeDisclosure';
import { clampWords, displayValue, friendlyError, noteWords, parseSource } from './display';
import { fmtDay, fmtDelayD, fmtMagnitude } from '../format';

export function WhatIfPanel() {
  const s = useStore();
  const [node, setNode] = useState(() => nodeName('chk_suez'));
  const [kind, setKind] = useState('closure');
  const [loss, setLoss] = useState(1.0);
  const [dur, setDur] = useState(7);
  const [shape, setShape] = useState<DisruptionSpec['shape']>('step');
  const [forkSeq, setForkSeq] = useState(2);
  const [forkLoss, setForkLoss] = useState(0.5);
  const [forkDur, setForkDur] = useState(3);
  const submit = () => s.runWhatIf({ node_id: resolveNodeInput(node, s.graph), kind, capacity_loss: kind === 'closure' ? 1.0 : loss, duration_d: dur, shape, started_at: new Date().toISOString().slice(0, 19) + 'Z' });
  return (
    <Panel id="whatif" code="WIF" title="What-if desk" updated={s.busy === 'run' || s.busy === 'fork' ? 'Running' : 'On demand'}
      exportSpec={{ json: () => ({ compare: s.compare, chain: s.chain }) }}>
      <div className="form-grid">
        <label>Place<input className="mono" value={node} onChange={(e) => setNode(e.target.value)} placeholder="Suez Canal" /></label>
        <label>Kind<select value={kind} onChange={(e) => setKind(e.target.value)}>{KINDS.map((k) => <option key={k} value={k}>{prettyKind(k)}</option>)}</select></label>
        <label>Capacity loss (fraction)<input type="number" step="0.05" min="0.05" max="1" value={loss} disabled={kind === 'closure'} onChange={(e) => setLoss(Number(e.target.value))} /></label>
        <label>Duration (days)<input type="number" step="1" min="1" max="3650" value={dur} onChange={(e) => setDur(Number(e.target.value))} /></label>
        <label>Shape<select value={shape} onChange={(e) => setShape(e.target.value as DisruptionSpec['shape'])}>{SHAPES.map((x) => <option key={x} value={x}>{labelWord(x)}</option>)}</select></label>
      </div>
      <button className="btn btn--amber" onClick={submit} disabled={s.busy !== null}>Run what-if</button>
      <hr className="rule" />
      <h5>Fork at step</h5>
      <div className="form-grid">
        <label>At step<input type="number" min="0" value={forkSeq} onChange={(e) => setForkSeq(Number(e.target.value))} /></label>
        <label>Capacity loss override<input type="number" step="0.05" min="0.05" max="1" value={forkLoss} onChange={(e) => setForkLoss(Number(e.target.value))} /></label>
        <label>Duration override (days)<input type="number" min="1" value={forkDur} onChange={(e) => setForkDur(Number(e.target.value))} /></label>
      </div>
      <button className="btn" onClick={() => s.forkAt(forkSeq, { capacity_loss: forkLoss, duration_d: forkDur })} disabled={!s.chain || s.busy !== null}>Fork this run</button>
      {s.compare && (
        <div className="compare">
          <h5>Compare</h5>
          <p className="mono">parent {s.compare.parent.event_count} events · fork {s.compare.child.event_count} events · forked at step {s.compare.atSeq}</p>
          <p><b>First differing step:</b> <span className="amber mono">{s.compare.divergence === null ? 'None' : `step ${s.compare.divergence}`}</span></p>
        </div>
      )}
    </Panel>
  );
}

/** Ensemble statistics: the Ensembles panel lives in ./ensstatsPanel.tsx —
 *  variant jobs with a count selector, outcome statistics + distribution
 *  curve, per-place / per-leg branch statistics, firing variants, and
 *  variant chains replayed through the chain view, plus the explorer modes
 *  (combination tree + explorations). Wrapped
 *  here so existing imports of EnsemblePanel keep working, forwarding
 *  the AUTO DEMO state that keeps the explorer actions inert. */
export function EnsemblePanel({ demoActive = false }: { demoActive?: boolean }) {
  return <EnsembleStatsPanel demoActive={demoActive} />;
}

/** A source cited as a link: publisher and date, never the raw URL. */
function SourceLink({ source, date }: { source: string; date?: string }) {
  const { publisher, url } = parseSource(source);
  const text = date ? `${publisher} · ${fmtDay(date)}` : publisher;
  return url ? <SafeLink href={url}>{text}</SafeLink> : <span>{text}</span>;
}

export function ProofPanel() {
  const s = useStore();
  const sc = s.scorecard;
  return (
    <Panel id="proof" code="PRF" title="Proof — Replay scorecard" updated="Descriptive"
      exportSpec={{ json: () => sc, pack: () => ({ data: sc, provenance: [{ file: 'scorecard.json', source: 'SupermAP replay scorecard (historical replays)', mode: 'SNAPSHOT' }] }) }}>
      {!sc ? <p className="honest">Not served · Replay scorecard</p> : (
        <>
          <p className="backtest-banner"><b>Recorded replays</b> · Historical <Chip kind="meta" word="Descriptive" /></p>
          <dl className="kv">
            <dt>Observed places modelled</dt><dd className="amber">{sc.aggregate.coverage.covered_count} of {sc.aggregate.coverage.distinct_non_seed_nodes}</dd>
            <dt>Graded steps</dt><dd className="amber">{sc.aggregate.delay_accuracy.graded_steps}</dd>
            <dt>Observed, not modelled</dt><dd className="amber">{sc.aggregate.counts.genuine_misses}</dd>
            <dt>Modelled, not observed</dt><dd>{sc.aggregate.false_positives.count}</dd>
            <dt>Replays</dt><dd>{sc.aggregate.replays} · {sc.graph.nodes} places / {sc.graph.edges} legs</dd>
          </dl>
          <h5>Per replay</h5>
          {sc.replays.map((r) => (
            <p key={r.id} className="mono small">{r.title}: {r.coverage.covered_count ?? '—'} of {r.coverage.distinct_non_seed_nodes ?? '—'} places · {r.delay_accuracy.graded_steps ?? '—'} graded steps · {r.event_count} events</p>
          ))}
          <h5>Observed, not modelled</h5>
          {sc.genuine_misses.map((m, i) => {
            const title = sc.replays.find((r) => r.id === m.replay_id)?.title ?? 'Replay';
            const eff = clampWords(humaniseProse(m.effect, s.graph), 72);
            return (
              <p key={i} className="miss"><b>{nodeName(m.node_id)}</b> ({title}) · observed {m.observed_delay_d} d · modelled {m.predicted_delay_d != null ? `${m.predicted_delay_d} d` : 'none'}
                {' '}<span className="dim" title={eff.truncated ? humaniseProse(m.effect, s.graph) : undefined}>{eff.text}</span>
                {' '}<span className="dim">[<SourceLink source={m.evidence_source} date={m.evidence_date} />]</span></p>
            );
          })}
          <MethodDisclosure data-methodology>
            <p className="fineprint">Replays run on stored event datasets.</p>
            <p className="fineprint">Steps are graded where an observed delay and a source exist.</p>
            <p className="fineprint">Counts only; no rate is published.</p>
          </MethodDisclosure>
        </>
      )}
    </Panel>
  );
}

export function LedgerPanel() {
  const s = useStore();
  const entries = s.ledger ?? [];
  const counts = ledgerCounts(entries);
  /* The ledger is the app's own stored, hash-chained file: its basis is LOCAL, not a feed. */
  const updated = s.ledgerSource === 'live' ? srcStamp('Local', 'Chain ledger')
    : s.ledgerSource === 'unreachable' ? 'Unreachable' : 'Loading';
  return (
    <Panel id="ledger" code="LDG" title="Chain ledger" updated={updated}
      stamp={s.ledgerSource === 'live' ? <BasisStamp word="Local" source="Chain ledger" /> : undefined}
      exportSpec={{ json: () => ({ source: s.ledgerSource, entries: s.ledger }) }}>
      {s.ledgerSource === 'loading' && (
        <p className="honest">Loading · Ledger</p>)}
      {s.ledgerSource === 'unreachable' && (
        <p className="honest">Unreachable · Ledger · No entries shown</p>)}
      {s.ledgerSource === 'live' && entries.length === 0 && (
        <p className="honest">Ledger local · 0 entries · Read-only</p>)}
      {s.ledgerSource === 'live' && entries.map((e) => {
        const g = e.grade;
        const feedEv = s.feed.find((f) => f.id === e.disruption_id);
        return (
          <div key={e.entry_id} className="ledger-entry">
            <p className="mono small"><b className="amber">Locked {fmtDayTime(e.locked_at)}</b></p>
            <dl className="kv">
              <dt>Disruption</dt>
              <dd className="mono">{feedEv ? feedEv.headline : e.predicted_chain.length ? `Disruption at ${nodeLabel(e.predicted_chain[0].node_id, s.graph)}` : 'Disruption record'}</dd>
              <dt>Run</dt><dd className="mono">{e.predicted_chain.length ? `Run · ${nodeLabel(e.predicted_chain[0].node_id, s.graph)} · locked ${fmtDayTime(e.locked_at)}` : '—'}</dd>
              <dt>Chain</dt><dd>{e.predicted_chain.length} step{e.predicted_chain.length === 1 ? '' : 's'}</dd>
              <dt>Grade</dt>
              <dd>{g ? (
                <span><b>Graded</b>{g.graded_at ? <span className="mono"> {fmtDayTime(g.graded_at)}</span> : null} — {g.hit_steps.length} matched / {g.missed_steps.length} unmatched
                  {(g.ungradeable_steps?.length ?? 0) > 0 ? ` / ${g.ungradeable_steps!.length} ungradeable` : ''}
                  {g.delay_error_d != null ? <> · delay difference <span className="mono">{g.delay_error_d} d</span></> : null}</span>
              ) : <span className="amber"><b>Open</b></span>}</dd>
            </dl>
            {e.predicted_chain.map((ev) => (
              <p key={ev.seq} className="mono small dim">Step {ev.seq + 1}: {nodeLabel(ev.node_id, s.graph)} via {labelWord(ev.channel)} — +{ev.delay_d} d · {ev.mechanism} · lands {fmtDayTime(ev.at)}</p>
            ))}
            {g && (g.evidence?.length ?? 0) > 0 && (
              <>
                <h5>Grade evidence</h5>
                {g.evidence!.map((prov, i) => (
                  <p key={i} className="mono small">
                    <SourceLink source={prov.source} />
                    <span className="dim"> · retrieved {fmtDayTime(prov.retrieved_at)} · {labelWord(prov.mode)}</span>
                  </p>
                ))}
              </>
            )}
            {e.supersedes && <p className="fineprint">Supersedes an earlier entry.</p>}
          </div>
        );
      })}
      {s.ledgerSource === 'live' && entries.length > 0 && (
        <p className="mono small">{ledgerCountsLine(counts)} <Chip kind="meta" word="Descriptive" /></p>)}
    </Panel>
  );
}

/** The money layer's stated cost-pressure rule, in the panel's own words
 *  (the server's rule, meaning unchanged): the largest magnitude among
 *  the run's cost-channel events at this place. */
const COST_PRESSURE_RULE = 'max cost-channel magnitude at this place · normalised · not currency';

export function MoneyPanel() {
  const s = useStore();
  // Selected node = the link-group selection: showMoney's target wins, else
  // the currently selected node. Fetch GET /money/node/{id} for exactly it.
  const node = s.moneyNode ?? s.selectedNode;
  useEffect(() => { if (node) void s.fetchMoney(node); /* eslint-disable-next-line */ }, [node]);
  const panel = s.moneyPanel && s.moneyPanel.node_id === node ? s.moneyPanel : null;
  const updated = !node ? 'SEC EDGAR · No place selected'
    : s.moneySource === 'live' && panel ? feedStamp(adapterFeedState(s.health, FEED_ADAPTERS.edgar, s.snapshotMode), 'SEC EDGAR')
    : s.moneySource === 'unreachable' ? 'SEC EDGAR · Unreachable' : 'SEC EDGAR · Loading';
  // Simulated cost pressure: the API field when the response carries one;
  // otherwise the rule computed over the CURRENT run's already-loaded chain
  // stream (same rule, same inputs panel.py uses — no engine call, no new
  // data). Absent in both = no cost-channel events at this node in the run.
  const pressure: (SimulatedCostPressure & { source_note: string }) | null = useMemo(() => {
    if (panel?.simulated_cost_pressure) {
      const p = panel.simulated_cost_pressure;
      const raw = p.provenance?.source ?? '';
      return { ...p, source_note: /^GET\s+\//.test(raw) || !raw ? 'SEC EDGAR' : humaniseIds(raw, s.graph) };
    }
    if (!node || !s.chain) return null;
    const mags = s.chain.events.filter((e) => e.node_id === node && e.channel === 'cost' && typeof e.magnitude === 'number')
      .map((e) => e.magnitude);
    if (!mags.length) return null;
    return { value: Math.max(...mags), label: 'SIMULATED', rule: COST_PRESSURE_RULE,
      provenance: { source: `SupermAP engine — ${runRef(s.chain, s.graph)}`, mode: 'MODELED' },
      source_note: 'SupermAP engine' };
  }, [panel, node, s.chain]);
  const filingFor = (ticker: string) =>
    (s.moneyExposures?.mappings ?? []).find((m) => m.node_id === node && m.ticker === ticker)?.filing;
  return (
    <Panel id="money" code="MNY" title="Money — Exposures" updated={updated}
      exportSpec={{ json: () => panel }}>
      {!node && <p className="honest">Select a place</p>}
      {node && s.moneySource === 'loading' && !panel && (
        <p className="honest">Loading · {nodeName(node)}</p>)}
      {node && s.moneySource === 'unreachable' && (
        <p className="honest">Unreachable · EDGAR filings · {nodeName(node)}</p>)}
      {node && panel && (
        <>
          <dl className="kv">
            <dt>Place</dt><dd className="amber">{nodeName(node)}</dd>
            <dt>Exposed</dt><dd>{panel.exposed.length} compan{panel.exposed.length === 1 ? 'y' : 'ies'} · Cited</dd>
          </dl>
          {panel.exposed.map((x) => {
            const f = filingFor(x.ticker);
            return (
              <div key={x.ticker} className="money-row">
                <p className="money-head">
                  <span className="money-ticker mono">{x.ticker}</span>
                  <span>{x.company}</span>
                  <span className="exposure-chip mono">{x.exposure_kind}</span>
                </p>
                <p className="mono small">
                  {x.last_price != null ? (
                    <span>${x.last_price.toFixed(2)}
                      {x.price_change_pct != null && <> · {x.price_change_pct >= 0 ? '+' : ''}{x.price_change_pct.toFixed(2)}%</>}
                      {x.price_date && <> · {x.price_date}</>}
                      {x.price_provenance?.source && <> · {x.price_provenance.source}{x.price_provenance.retrieved_at ? ` ${x.price_provenance.retrieved_at}` : ''}</>}
                    </span>
                  ) : (
                    <span className="unpriced">Unpriced</span>
                  )}
                </p>
                <p className="mono small">
                  <SafeLink href={x.filing_url}>EDGAR filing <Icon name="arrow-up-right" size="xs" /></SafeLink>
                  {f && <span className="dim"> · {f.form} · filed {f.filed_date}</span>}
                </p>
                {safeExcerpt(f?.excerpt) ? <details className="method-disclosure" data-methodology><summary className="mono">Filing excerpt</summary><p className="fineprint money-excerpt">“{safeExcerpt(f?.excerpt)!.slice(0, 240)}…”</p></details> : f ? <p className="fineprint"><span className="exposure-chip mono">Excerpt withheld</span></p> : null}
              </div>
            );
          })}
          {panel.exposed.length === 0 && (
            <p className="honest">No exposures mapped</p>)}
          {pressure ? (
            <div className="sim-block">
              <p><span className="sim-label mono">{pressure.label}</span>{' '}
                <b>Simulated cost pressure: <span className="mono amber">{pressure.value.toFixed(3)}</span></b></p>
              <p className="fineprint">Source: {pressure.source_note}{pressure.provenance?.mode ? ` · ${labelWord(pressure.provenance.mode)}` : ''}</p>
              <MethodDisclosure data-methodology>
                <p className="fineprint">{COST_PRESSURE_RULE}</p>
              </MethodDisclosure>
            </div>
          ) : (
            <p className="fineprint">Simulated cost pressure · None</p>
          )}
          <p className="fineprint">Source: SEC EDGAR · Impacts simulated</p>
        </>
      )}
    </Panel>
  );
}

/** Counted-detection block for one scan card. Counts are the served
 *  values, never recomputed here beyond the now-minus-baseline delta.
 *  The offline reference-counts transport is labelled as what it is — an
 *  offline test replay, not Meta SAM and not live. The change flag
 *  mirrors the server shaper's anchorage-delta bands (>= +10%). */
function SatCountsBlock({ sc }: { sc: SatScan }) {
  const c = satCounts(sc.detection.counts);
  const isTest = sc.detection.provider === 'reference-counts';
  if (!c) {
    return <p className="mono small">Counts not served</p>;
  }
  const pct = c.deltaFrac != null ? Math.round(c.deltaFrac * 100) : null;
  return (
    <>
      {isTest && <span className="await-chip mono">Offline test replay</span>}
      <p className="mono small">
        Anchorage vessels (150 m and over) · now {c.vesselsNow} · baseline {c.vesselsBaseline}
        {' · '}Δ {c.delta >= 0 ? '+' : ''}{c.delta}
        {pct != null && c.deltaFrac != null && Math.abs(c.deltaFrac) >= 0.10 && (
          <span className="amber"> · Change flag {pct >= 0 ? '+' : ''}{pct}% vs baseline</span>
        )}
      </p>
      <p className="fineprint">
        {c.frameNow != null && <>Whole frame {c.frameNow} large vessels · </>}
        {c.berthNow != null && <>at berth {c.berthNow} · </>}
        {c.yardNow && <>yard {c.yardNow} · </>}
        Detection: <span className="mono">{statusLabel(sc.detection.status)}</span>
      </p>
    </>
  );
}

/** Satellite Watch: the Sentinel-2 scans from GET /satwatch/scans.
 *  Detection counts are null while detection is not served — the
 *  panel shows the Awaiting key chip and never a count. Clicking a
 *  scan dispatches selectNode for its node. */
export function SatWatchPanel() {
  const s = useStore();
  const scans = s.satScans ?? [];
  const updated = s.satSource === 'live' ? feedStamp(feedState('RECORDED'), `Sentinel-2 scans · ${scans.length} scans`)
    : s.satSource === 'unreachable' ? 'Satellite scans · Unreachable' : 'Satellite scans · Loading';
  return (
    <Panel id="satwatch" code="SAT" title="Satellite watch" updated={updated}
      exportSpec={{ json: () => ({ source: s.satSource, scans: s.satScans }), source: 'Copernicus Sentinel; Meta AI' }}>
      {s.satSource === 'loading' && <p className="honest">Loading · Satellite scans</p>}
      {s.satSource === 'unreachable' && <p className="honest">Unreachable · Satellite scans</p>}
      {s.satSource === 'live' && scans.length === 0 && <p className="honest">No scans</p>}
      {scans.map((sc) => (
        <article key={sc.node_id} className={`sat-card ${s.selectedNode === sc.node_id ? 'sat-card--sel' : ''}`} data-scan={sc.node_id}>
          <div className="sat-head" onClick={() => s.selectNode(sc.node_id)} title="Open place">
            <b>{sc.name}</b><span className="amber small">Select</span>
          </div>
          <p className="mono small sat-scenes">
            Now {sc.scene_now.date} · AOI cloud {sc.scene_now.cloud_pct_aoi != null ? `${sc.scene_now.cloud_pct_aoi.toFixed(1)}%` : '—'}
            {'  ·  '}baseline {sc.scene_baseline.date} · AOI cloud {sc.scene_baseline.cloud_pct_aoi != null ? `${sc.scene_baseline.cloud_pct_aoi.toFixed(1)}%` : '—'}
          </p>
          <div className="sat-crops">
            <figure>
              <img src={api.satCropUrl(sc.crops.now)} alt={`${sc.name} — current Sentinel-2 crop, ${sc.scene_now.date}`} loading="lazy" />
              <figcaption className="mono">Now · {sc.scene_now.date}{sc.scene_now.platform ? ` · ${sc.scene_now.platform}` : ''}</figcaption>
            </figure>
            <figure>
              <img src={api.satCropUrl(sc.crops.baseline)} alt={`${sc.name} — baseline Sentinel-2 crop, ${sc.scene_baseline.date}`} loading="lazy" />
              <figcaption className="mono">Baseline · {sc.scene_baseline.date}{sc.scene_baseline.platform ? ` · ${sc.scene_baseline.platform}` : ''}</figcaption>
            </figure>
          </div>
          {sc.detection.counts == null ? (
            <>
              <span className="await-chip mono">Awaiting key</span>
              <p className="fineprint">Detection: <span className="mono">{statusLabel(sc.detection.status)}</span> · No counts</p>
            </>
          ) : (
            <SatCountsBlock sc={sc} />
          )}
          <p className="fineprint mono">{sc.source ?? ''}{sc.retrieved_at ? ` · retrieved ${sc.retrieved_at}` : ''}</p>
        </article>
      ))}
      {s.satSource === 'live' && scans.length > 0 && (
        <p className="fineprint">Source: Sentinel-2 via Microsoft Planetary Computer</p>)}
    </Panel>
  );
}

/** POST /llm/explain state for one loaded run. Keyed by run_id so a stale
 *  response for a previous run can never render under a newer one. */
type ExplainState =
  | { runId: string; phase: 'loading' }
  | { runId: string; phase: 'live'; data: LlmExplain }
  | { runId: string; phase: 'unavailable' };

export function ExplainPanel() {
  const s = useStore();
  const ev = useMemo(() => (s.chain?.events ?? []).find((e) => e.seq === s.selectedSeq) ?? (s.chain?.events ?? []).find((e) => e.node_id === s.selectedNode), [s.chain, s.selectedSeq, s.selectedNode]);
  // Narrative for the CURRENTLY LOADED RUN (not the selected step — the
  // endpoint explains a whole run). Fetched exactly when the loaded run
  // changes and only then: the server caches by stream_hash (repeat calls
  // return cached:true at cost 0), and re-renders / step selections must
  // not re-hit the endpoint. A failed fetch degrades to one plain line —
  // never an error bar, never a fabricated narrative — and the raw chain
  // record below stays fully usable.
  const runId = s.chain?.run_id ?? null;
  const [explain, setExplain] = useState<ExplainState | null>(null);
  useEffect(() => {
    if (!runId) { setExplain(null); return; }
    let dead = false;
    setExplain({ runId, phase: 'loading' });
    api.explain(runId)
      .then((data) => { if (!dead) setExplain({ runId, phase: 'live', data }); })
      .catch(() => { if (!dead) setExplain({ runId, phase: 'unavailable' }); });
    return () => { dead = true; };
  }, [runId]);
  const ex = explain && explain.runId === runId ? explain : null;
  return (
    <Panel id="explain" code="XPL" title="Explain — Step inspector" updated={ev ? `step ${ev.seq + 1}` : undefined}
      exportSpec={{ json: () => ev }}>
      {runId && (!ex || ex.phase === 'loading') && (
        <p className="honest">Loading · Explanation</p>)}
      {runId && ex?.phase === 'unavailable' && (
        <p className="honest">Unreachable · Explanation</p>)}
      {runId && ex?.phase === 'live' && (
        <div className="explain-block">
          <ExplainChips data={ex.data} />
          <NarrativeDisclosure text={cleanNarrative(ex.data.narrative, s.graph)} />
        </div>
      )}
      <AskChain chain={s.chain} />
      {!ev ? <p className="dim">Select a chain step</p> : (
        <dl className="kv">
          <dt>Place</dt><dd className="amber">{nodeName(ev.node_id)}</dd>
          <dt>Step</dt><dd className="mono">{ev.seq + 1}</dd>
          <dt>Follows</dt><dd className="mono">{ev.parent_id === null ? 'Origin' : `Step ${ev.parent_id + 1}`}</dd>
          <dt>Channel</dt><dd>{labelWord(ev.channel)}</dd>
          <dt>Delay</dt><dd className="amber">+{fmtDelayD(ev.delay_d)} d</dd>
          <dt>Magnitude</dt><dd className="mono">{fmtMagnitude(ev.magnitude)}</dd>
          <dt>Lands</dt><dd className="mono">{fmtDayTime(ev.at)}</dd>
          <dt>Mechanism</dt><dd className="mech">{ev.mechanism}</dd>
        </dl>
      )}
    </Panel>
  );
}

/** Rule-type labels for the screen (the served type codes stay in the
 *  API and in exports). */
const RULE_TYPE_LABELS: Record<string, string> = {
  adapter_state: 'Source state', event: 'Disruption event',
  run_outcome: 'Run outcome', ledger: 'Chain ledger',
};

/** One-line condition summary for a served rule — derived only from the
 *  rule's own normalized params, nothing else. */
function ruleSummary(r: AlertRule): string {
  const p = r.params as unknown as Record<string, unknown>;
  switch (r.type) {
    case 'adapter_state':
      return `${p.adapter ? adapterLabel(String(p.adapter)) : 'Any source'} ${p.match === 'state_is_not' ? 'is not' : 'is'} ${displayValue(p.state)}`;
    case 'event': {
      const bits = [`severity ≥ ${String(p.min_severity)}`];
      if (p.node_id) bits.push(`at ${nodeName(String(p.node_id))}`);
      if (p.node_class) bits.push(`class ${String(p.node_class)}`);
      if (p.kind) bits.push(`kind ${prettyKind(String(p.kind)).toLowerCase()}`);
      return bits.join(' · ');
    }
    case 'run_outcome':
      return p.metric === 'node_delay_d'
        ? `delay at ${nodeName(String(p.node_id))} > ${Number(p.threshold_d).toFixed(1)} d`
        : `worst run delay > ${Number(p.threshold_d).toFixed(1)} d`;
    case 'ledger':
      return p.event === 'grade' ? 'Entry graded' : 'Entry locked';
    default:
      return '';
  }
}

/** Alerts & Watchlists (server/alerts): rule CRUD over the four
 *  served rule types, the fired-alerts feed, and watchlists with the
 *  per-node state view. The panel owns its fetch cycle (load on mount,
 *  reload after every mutation) — the same on-demand pattern as the
 *  Ensembles panel, since rules/watchlists are operator state, not boot
 *  data. Honesty contract (as every desk panel): if the alerts API is
 *  unreachable the panel shows the standard unavailable line and asserts
 *  no rules, feed or watchlists. Server validation messages (422) are
 *  surfaced verbatim in the panel error line — never a generic failure. */
export function AlertsPanel() {
  const s = useStore();
  const [phase, setPhase] = useState<'loading' | 'live' | 'unreachable'>('loading');
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [feed, setFeed] = useState<FiredAlert[]>([]);
  const [watchlists, setWatchlists] = useState<Watchlist[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [viewId, setViewId] = useState<string | null>(null);
  const [view, setView] = useState<WatchlistView | null>(null);
  const [viewBusy, setViewBusy] = useState(false);
  // ── create-rule form state ──────────────────────────────────────────
  const [rName, setRName] = useState('');
  const [rType, setRType] = useState<AlertRuleType>('event');
  const [rEnabled, setREnabled] = useState(true);
  const [aAdapter, setAAdapter] = useState('');
  const [aMatch, setAMatch] = useState<'state_is' | 'state_is_not'>('state_is');
  const [aState, setAState] = useState<AlertState>(ALERT_STATE_DEFAULT);
  const [eSev, setESev] = useState(3);
  const [eNode, setENode] = useState('');
  const [eClass, setEClass] = useState('');
  const [eKind, setEKind] = useState('');
  const [oMetric, setOMetric] = useState<'worst_delay_d' | 'node_delay_d'>('worst_delay_d');
  const [oNode, setONode] = useState('');
  const [oThreshold, setOThreshold] = useState('5');
  const [lEvent, setLEvent] = useState<'lock' | 'grade'>('lock');
  const [formNote, setFormNote] = useState<string | null>(null);
  // ── watchlist form state ────────────────────────────────────────────
  const [wName, setWName] = useState('');
  const [renameDraft, setRenameDraft] = useState<Record<string, string>>({});
  const [addDraft, setAddDraft] = useState<Record<string, string>>({});

  const nodeIds = useMemo(
    () => (s.graph.nodes as unknown as Array<{ id?: unknown }>).map((n) => n.id).filter((x): x is string => typeof x === 'string'),
    [s.graph]);
  const adapterNames = useMemo(() => (s.health?.adapters ?? []).map((a) => a.adapter), [s.health]);

  const load = useCallback(async () => {
    try {
      const [r, f, w] = await Promise.all([api.alertRules(), api.alertsFeed(50), api.watchlists()]);
      setRules(r.rules); setFeed(f.alerts); setWatchlists(w.watchlists); setPhase('live');
    } catch { setPhase('unreachable'); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  /** Run one mutation, then reload; failures land in the error line with
   *  a short factual line (friendlyError). */
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await fn(); await load(); }
    catch (e) { setErr(friendlyError(e)); }
    finally { setBusy(false); }
  };
  const fail = (e: unknown) => setErr(friendlyError(e));

  const toggleRule = (r: AlertRule) => void act(() =>
    api.replaceRule(r.id, { name: r.name, type: r.type, enabled: !r.enabled, params: r.params as unknown as Record<string, unknown> }));
  const deleteRule = (r: AlertRule) => void act(() => api.deleteRule(r.id));

  const buildBody = (): { name: string; type: AlertRuleType; enabled: boolean; params: Record<string, unknown> } | null => {
    const name = rName.trim();
    if (name.length < 1 || name.length > 80) { setFormNote('Rule name: 1 to 80 characters.'); return null; }
    let params: Record<string, unknown>;
    if (rType === 'adapter_state') {
      params = { adapter: aAdapter.trim() ? resolveAdapterInput(aAdapter, adapterNames) : null, match: aMatch, state: aState };
    } else if (rType === 'event') {
      params = { min_severity: eSev, node_id: eNode.trim() ? resolveNodeInput(eNode, s.graph) : null, node_class: eClass || null, kind: eKind || null };
    } else if (rType === 'ledger') {
      params = { event: lEvent };
    } else {
      const t = Number(oThreshold);
      if (!Number.isFinite(t) || t <= 0 || t > 3650) { setFormNote('Threshold: 0 to 3650 days.'); return null; }
      if (oMetric === 'node_delay_d' && !oNode.trim()) { setFormNote('Place required for this metric.'); return null; }
      params = { metric: oMetric, node_id: oMetric === 'node_delay_d' ? resolveNodeInput(oNode, s.graph) : null, threshold_d: t };
    }
    setFormNote(null);
    return { name, type: rType, enabled: rEnabled, params };
  };
  const createRule = () => {
    const body = buildBody(); if (!body) return;
    void act(async () => { await api.createRule(body); setRName(''); });
  };

  const fetchView = async (id: string) => {
    setViewBusy(true); setErr(null);
    try { setView(await api.watchlistView(id)); }
    catch (e) { fail(e); }
    finally { setViewBusy(false); }
  };
  const toggleView = (w: Watchlist) => {
    if (viewId === w.id) { setViewId(null); setView(null); return; }
    setViewId(w.id); setView(null); void fetchView(w.id);
  };
  /* Search nav bridge: a watchlist request selects that
   * watchlist and opens its view through toggleView's state. The id
   * waits in navWatchlistId until the panel's own load lands; once
   * the list is live, an id that is not in it is dropped quietly. */
  const [navWatchlistId, setNavWatchlistId] = useState<string | null>(null);
  useEffect(() => {
    const onNav = () => {
      const id = consumeWatchlist();
      if (id) setNavWatchlistId(id);
    };
    registerWatchlistConsumer(onNav);
    return () => registerWatchlistConsumer(null);
  }, []);
  useEffect(() => {
    if (!navWatchlistId) return;
    const w = watchlists.find((x) => x.id === navWatchlistId);
    if (w) {
      setNavWatchlistId(null);
      if (viewId !== w.id) { setViewId(w.id); setView(null); void fetchView(w.id); }
    } else if (phase === 'live') {
      setNavWatchlistId(null); // no longer exists — ignore quietly
    }
    // fetchView is a stable in-component helper over setState + api.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [navWatchlistId, watchlists, phase]);
  /** Node add/remove return the updated watchlist server-side; reload
   *  all three collections and refresh an open view for the same list. */
  const nodeMutation = (w: Watchlist, fn: () => Promise<unknown>) => void act(async () => {
    await fn(); if (viewId === w.id) await fetchView(w.id);
  });

  /* Rules, watchlists and the fired feed are stored by the app: LOCAL, not a feed. */
  const updated = phase === 'live' ? srcStamp('Local', 'Alerts & watchlists')
    : phase === 'unreachable' ? 'Alerts · Unreachable' : 'Alerts · Loading';
  const viewFor = view && viewId && view.watchlist.id === viewId ? view : null;

  return (
    <Panel id="alerts" code="ALR" title="Alerts & watchlists" updated={updated}
      stamp={phase === 'live' ? <BasisStamp word="Local" source="Alerts & watchlists" /> : undefined}
      exportSpec={{ json: () => (phase === 'live' ? { rules, feed, watchlists } : null) }}>
      <datalist id="alr-node-ids">{nodeNameOptions(s.graph).map((name) => <option key={name} value={name} />)}</datalist>
      <datalist id="alr-adapter-names">{adapterNames.map((a) => <option key={a} value={adapterLabel(a)} />)}</datalist>
      {phase === 'loading' && (
        <p className="honest">Loading · Alerts</p>)}
      {phase === 'unreachable' && (
        <p className="honest">Unreachable · Alerts</p>)}
      {err && <p className="alr-err mono">{err}</p>}
      {phase === 'live' && (
        <>
          <h5>Rules <button className="btn" onClick={() => void load()} disabled={busy}>Refresh</button></h5>
          {rules.length === 0 && <p className="dim">No rules</p>}
          {rules.map((r) => (
            <div key={r.id} className="alr-row">
              <div className="alr-head">
                <b>{r.name}</b>
                <span className="exposure-chip mono">{RULE_TYPE_LABELS[r.type] ?? r.type}</span>
                <span className={`mono small ${r.enabled ? 'amber' : 'dim'}`}>{r.enabled ? 'Enabled' : 'Disabled'}</span>
                <span className="alr-actions">
                  <button className="btn" onClick={() => toggleRule(r)} disabled={busy}>{r.enabled ? 'Disable' : 'Enable'}</button>
                  <button className="btn" onClick={() => deleteRule(r)} disabled={busy}>Delete</button>
                </span>
              </div>
              <p className="mono small dim">{ruleSummary(r)} · created {fmtDayTime(r.created_at)}</p>
            </div>
          ))}
          <hr className="rule" />
          <h5>New rule</h5>
          <div className="form-grid">
            <label>Name<input className="mono" value={rName} maxLength={80} onChange={(e) => setRName(e.target.value)} placeholder="Rule name" /></label>
            <label>Type<select value={rType} onChange={(e) => setRType(e.target.value as AlertRuleType)}>
              <option value="adapter_state">Source state</option>
              <option value="event">Disruption event</option>
              <option value="run_outcome">Run outcome</option>
              <option value="ledger">Chain ledger</option>
            </select></label>
            {rType === 'adapter_state' && (<>
              <label>Source (blank = any)<input className="mono" value={aAdapter} onChange={(e) => setAAdapter(e.target.value)} placeholder="any source" list="alr-adapter-names" /></label>
              <label>Match<select value={aMatch} onChange={(e) => setAMatch(e.target.value as 'state_is' | 'state_is_not')}>
                <option value="state_is">is</option><option value="state_is_not">is not</option></select></label>
              <label>State<select value={aState} onChange={(e) => setAState(e.target.value as AlertState)}>
                {ALERT_STATE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select></label>
            </>)}
            {rType === 'event' && (<>
              <label>Min severity<select value={eSev} onChange={(e) => setESev(Number(e.target.value))}>
                {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
              <label>Place (blank = any)<input className="mono" value={eNode} onChange={(e) => setENode(e.target.value)} placeholder="any place" list="alr-node-ids" /></label>
              <label>Place class<select value={eClass} onChange={(e) => setEClass(e.target.value)}>
                <option value="">any</option><option value="sea">sea</option><option value="air">air</option><option value="ground">ground</option></select></label>
              <label>Kind<select value={eKind} onChange={(e) => setEKind(e.target.value)}>
                <option value="">any</option>{KINDS.map((k) => <option key={k} value={k}>{prettyKind(k)}</option>)}</select></label>
            </>)}
            {rType === 'run_outcome' && (<>
              <label>Metric<select value={oMetric} onChange={(e) => setOMetric(e.target.value as 'worst_delay_d' | 'node_delay_d')}>
                <option value="worst_delay_d">Worst delay in the run</option><option value="node_delay_d">Delay at one place</option></select></label>
              <label>Threshold (days)<input type="number" step="0.5" min="0.5" max="3650" value={oThreshold} onChange={(e) => setOThreshold(e.target.value)} /></label>
              {oMetric === 'node_delay_d' && (
                <label>Place (required)<input className="mono" value={oNode} onChange={(e) => setONode(e.target.value)} placeholder="Place" list="alr-node-ids" /></label>)}
            </>)}
            {rType === 'ledger' && (
              <label>Event<select value={lEvent} onChange={(e) => setLEvent(e.target.value as 'lock' | 'grade')}>
                <option value="lock">Entry locked</option><option value="grade">Entry graded</option></select></label>)}
            <label>Enabled at creation<input type="checkbox" checked={rEnabled} onChange={(e) => setREnabled(e.target.checked)} /></label>
          </div>
          {s.selectedNode && (rType === 'event' || (rType === 'run_outcome' && oMetric === 'node_delay_d')) && (
            <p className="fineprint"><button className="btn" onClick={() => (rType === 'event' ? setENode(nodeLabel(s.selectedNode!, s.graph)) : setONode(nodeLabel(s.selectedNode!, s.graph)))}>Use {nodeLabel(s.selectedNode, s.graph)}</button></p>)}
          {formNote && <p className="alr-err mono">{formNote}</p>}
          <button className="btn btn--amber" onClick={createRule} disabled={busy}>Create rule</button>
          <hr className="rule" />
          <h5>Fired alerts</h5>
          {feed.length === 0 && <p className="dim">No alerts</p>}
          {feed.map((f) => (
            <div key={f.id} className="alr-feed-row">
              <span className="mono dim">{fmtDayTime(f.fired_at)}</span>{' '}
              <b>{f.rule_name ?? 'Rule'}</b>{f.type ? <span className="exposure-chip mono">{RULE_TYPE_LABELS[f.type] ?? f.type}</span> : null}{' '}
              <span>{alertFactLabel(f, s.graph)}</span>
            </div>
          ))}
          <hr className="rule" />
          <h5>Watchlists</h5>
          <div className="alr-inline">
            <input className="mono" aria-label="Watchlist name" value={wName} maxLength={80} onChange={(e) => setWName(e.target.value)} placeholder="Watchlist name" />
            <button className="btn btn--amber" disabled={busy || !wName.trim()}
              onClick={() => void act(async () => { await api.createWatchlist(wName.trim()); setWName(''); })}>Create</button>
          </div>
          {watchlists.length === 0 && <p className="dim">No watchlists</p>}
          {watchlists.map((w) => (
            <div key={w.id} className="alr-row">
              <div className="alr-head">
                <b>{w.name}</b>
                <span className="mono dim small">{w.node_ids.length} place{w.node_ids.length === 1 ? '' : 's'}</span>
                <span className="alr-actions">
                  <button className="btn" onClick={() => toggleView(w)} disabled={viewBusy}>{viewId === w.id ? 'Hide view' : 'View'}</button>
                  <button className="btn" onClick={() => void act(() => api.deleteWatchlist(w.id))} disabled={busy}>Delete</button>
                </span>
              </div>
              <div className="alr-inline">
                <input className="mono" value={renameDraft[w.id] ?? w.name} maxLength={80}
                  onChange={(e) => setRenameDraft((d) => ({ ...d, [w.id]: e.target.value }))} aria-label={`Rename ${w.name}`} />
                <button className="btn" disabled={busy || !(renameDraft[w.id] ?? w.name).trim()}
                  onClick={() => void act(async () => { await api.renameWatchlist(w.id, (renameDraft[w.id] ?? w.name).trim()); setRenameDraft((d) => { const n = { ...d }; delete n[w.id]; return n; }); })}>Rename</button>
              </div>
              <div className="alr-chips">
                {w.node_ids.map((id) => (
                  <span key={id} className="alr-chip mono">{nodeLabel(id, s.graph)}
                    <button className="icon-btn" title={`Remove ${nodeLabel(id, s.graph)}`} disabled={busy}
                      aria-label={`Remove ${nodeLabel(id, s.graph)}`} onClick={() => nodeMutation(w, () => api.removeWatchlistNode(w.id, id))}><Icon name="close" size="xs" /></button></span>
                ))}
                {w.node_ids.length === 0 && <span className="dim small">No places</span>}
              </div>
              <div className="alr-inline">
                <input className="mono" value={addDraft[w.id] ?? ''} list="alr-node-ids" placeholder="Place name"
                  onChange={(e) => setAddDraft((d) => ({ ...d, [w.id]: e.target.value }))} aria-label={`Add place to ${w.name}`} />
                <button className="btn" disabled={busy || !(addDraft[w.id] ?? '').trim()}
                  onClick={() => nodeMutation(w, async () => { await api.addWatchlistNode(w.id, resolveNodeInput((addDraft[w.id] ?? '').trim(), s.graph)); setAddDraft((d) => ({ ...d, [w.id]: '' })); })}>Add</button>
                <button className="btn" disabled={busy || !s.selectedNode} title={s.selectedNode ? `Add selected place ${nodeLabel(s.selectedNode, s.graph)}` : 'No place selected'}
                  onClick={() => s.selectedNode && nodeMutation(w, () => api.addWatchlistNode(w.id, s.selectedNode!))}>Add selected</button>
              </div>
              {viewId === w.id && (
                viewBusy && !viewFor ? <p className="dim">Loading</p> :
                viewFor ? (
                  <>
                    <table className="data-table">
                      <thead><tr><th>Place</th><th className="num">Active events</th><th>Latest event</th><th>Chain runs · max delay</th></tr></thead>
                      <tbody>{viewFor.nodes.map((n) => {
                        const le = n.latest_events[0];
                        return (
                          <tr key={n.node_id} className={`alr-view-row ${s.selectedNode === n.node_id ? 'row--sel' : ''}`}
                            onClick={() => s.selectNode(n.node_id)}>
                            <td>{n.name ?? nodeName(n.node_id)}
                              {(n.mode || n.kind) && <span className="dim small"> · {[n.mode, n.kind].filter(Boolean).join(' / ')}</span>}</td>
                            <td className="mono num">{n.active_events}</td>
                            <td className="mono small">{le ? `${le.kind ? prettyKind(le.kind) : '—'} · sev ${le.severity ?? '—'} · ${le.status ? labelWord(le.status) : '—'} · ${le.detected_at ? fmtDayTime(le.detected_at) : '—'}` : '—'}</td>
                            <td className="mono small">{n.chain.runs_involved} runs · max {n.chain.max_delay_d != null ? `${n.chain.max_delay_d.toFixed(1)} d` : '—'}</td>
                          </tr>
                        );
                      })}</tbody>
                    </table>
                    {viewFor.nodes.length === 0 && <p className="dim">No places</p>}
                  </>
                ) : <p className="dim">Not served</p>
              )}
            </div>
          ))}
        </>
      )}
    </Panel>
  );
}

export function HealthPanel() {
  const s = useStore();
  return (
    <Panel id="health" code="HLT" title="Source health" updated={s.health ? (s.snapshotMode ? 'Snapshot · captured' : `${s.health.summary.LIVE ?? 0} live`) : 'Unreachable'}>
      {!s.health ? <EmptyState kind="down" word="Down" /> :
        s.health.adapters.length === 0 ? <EmptyState kind="notserved" word="No sources" /> : (
          <table className="data-table health-table">
            <thead><tr><th>Feed</th><th>State</th><th>Mode</th><th className="num">Last success</th></tr></thead>
            <tbody>{s.health.adapters.map((a) => {
              const v = adapterRowView(s.health!, a.adapter, s.snapshotMode);
              return (
              <tr key={a.adapter} data-calm={v.calm ? '1' : undefined}>
                <td>{adapterLabel(a.adapter)}</td>
                <td><Chip kind={v.kind} word={v.label} className={v.className} />{noteWords(a.note) && <span className="health-code">{noteWords(a.note)}</span>}</td>
                <td>{healthModeCell(v, a.mode, labelWord)}</td>
                <td className="num mono">{a.last_success_at ? fmtDayTime(a.last_success_at) : '\u2013'}</td>
              </tr>
              );
            })}</tbody>
          </table>)}
    </Panel>
  );
}
