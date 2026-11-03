/**
 * ENSEMBLE AGGREGATES — the shared rendering for the
 * aggregate blocks. The flat panel's results view, a
 * tree member's subtree aggregates, and an exploration member's
 * aggregates are the same served shape (outcome statistics +
 * distribution + per-place / per-leg branch figures), so they render
 * through these components and nowhere else.
 *
 * JobAggregates owns the three sub-views (statistics / branches /
 * one branch) for ONE flat job: it loads that job's results and
 * branches, opens branch details, and opens variant chains through
 * the caller's store seam. An optional `deepen` wiring adds the
 * exploration affordances (deepen from the base variant, from any
 * variant by number, and from each firing variant in a branch view)
 * without changing anything when it is absent — the flat panel
 * renders unchanged.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api, ApiError } from '../api';
import { useStore } from '../store';
import { humaniseProse, nodeLabel, prettyKind } from '../format';
import type {
  EnsBranchDetail, EnsCurveBin, EnsLegBranch, EnsOutcomeStats, EnsPlaceBranch,
  EnsStatsBranches, EnsStatsResults, EnsVariantData,
} from '../types';
import {
  AXIS_LABELS, ENS_COPY, OUTCOME_META, aggCurve, carriedText, colLabel, combinationText,
  countOf, fmtCountStat, fmtInt, fmtVal, groupedNote, levelsText, overflowNote, reachedText, shownNote,
  type CurveCol,
} from './ensstats';
import { ENX_COPY } from './enexplorer';
import { labelWord } from '../copy';
import { failureSentence } from './failureText';
import { Icon } from '../ui/Icon';

const VARIANT_LIST_CAP = 200;

/** A failed call as one plain sentence from its short code. The server's own message (which can carry job ids,
 *  paths or status text) is never shown. */
export const cleanErr = (e: unknown): string => failureSentence(e instanceof ApiError ? e.code : null);

/** Distribution curve: sparse series are centred on
 *  the axis at a capped bar width — never one full-width slab; dense
 *  series arrive pre-aggregated by aggCurve with the grouping stated
 *  beside the chart. Crosshair readout in the chain chart's idiom. */
export function DistributionCurve({ bins, unit }: { bins: EnsCurveBin[]; unit: string }) {
  const [hov, setHov] = useState<number | null>(null);
  const { cols, aggregated, total } = useMemo(() => aggCurve(bins, 48), [bins]);
  const W = 560, H = 148, top = 16, bottom = 20, left = 6, right = 40;
  const plotW = W - left - right, plotH = H - top - bottom;
  const max = Math.max(1, ...cols.map((c) => c.count));
  const natural = plotW / Math.max(1, cols.length);
  const slot = Math.min(natural, 50);
  const startX = left + (plotW - slot * cols.length) / 2;
  const hovCol: CurveCol | undefined = hov !== null ? cols[hov] : undefined;
  const hovX = hov !== null ? startX + hov * slot + slot / 2 : 0;
  const overflow = cols.find((c) => c.hi === null);
  return (
    <div className="chart-wrap ens-curve">
      <svg viewBox={`0 0 ${W} ${H}`} className="chart-svg">
        {[0.25, 0.5, 0.75, 1].map((f) => (
          <g key={f}>
            <line x1={left} x2={W - right + 34} y1={top + plotH - f * plotH} y2={top + plotH - f * plotH} className="tv-grid" />
            <text x={W - 4} y={top + plotH - f * plotH + 3} textAnchor="end" className="axis-chip">{fmtInt(Math.round(max * f))}</text>
          </g>
        ))}
        {cols.map((c, i) => (
          <g key={i} onMouseEnter={() => setHov(i)} onMouseLeave={() => setHov(null)}>
            <rect x={startX + i * slot + 1} y={top + plotH - (c.count / max) * plotH}
              width={Math.max(2, slot - 2)} height={(c.count / max) * plotH} className="ens-bar" />
          </g>
        ))}
        {cols.length > 0 && (
          <>
            <text x={startX + slot / 2} y={H - 6} textAnchor="middle" className="axis-chip">{colLabel(cols[0])}</text>
            {cols.length > 2 && <text x={startX + (cols.length / 2) * slot} y={H - 6} textAnchor="middle" className="axis-chip">{colLabel(cols[Math.floor(cols.length / 2)])}</text>}
            <text x={startX + cols.length * slot - slot / 2} y={H - 6} textAnchor="middle" className="axis-chip">{colLabel(cols[cols.length - 1])}</text>
          </>
        )}
        {hovCol && (
          <g pointerEvents="none">
            <line x1={hovX} x2={hovX} y1={top - 4} y2={top + plotH} className="crosshair" />
            <text x={Math.min(Math.max(hovX, 92), W - 92)} y={10} textAnchor="middle" className="axis-chip axis-chip--val">
              {colLabel(hovCol)} {unit} · {fmtInt(hovCol.count)} variants
            </text>
          </g>
        )}
      </svg>
      <div className="chart-foot mono">
        <span>{fmtInt(total)} variants</span>
        <span className="spacer" />
        {aggregated && <span>{groupedNote(bins.length, cols.length)}</span>}
        {overflow && <span>{overflowNote(overflow, unit)}</span>}
      </div>
    </div>
  );
}

/** Outcome statistics table + the selected outcome's distribution,
 *  over one served outcomes block. Definitions print when the
 *  payload carries them. */
export function OutcomeStatsBlock({ outcomes, definitions }: {
  outcomes: Record<string, EnsOutcomeStats>;
  definitions?: Record<string, string>;
}) {
  const s = useStore();
  const [outcomeKey, setOutcomeKey] = useState<string>('worst_path_delay_d');
  const outcome = outcomes[outcomeKey];
  const outcomeMeta = OUTCOME_META.find((o) => o.key === outcomeKey) ?? OUTCOME_META[0];
  return (
    <>
      <h5>{ENS_COPY.outcomesHead}</h5>
      <table className="data-table">
        <thead><tr><th>Outcome</th><th className="num">Mean</th><th className="num">P10</th><th className="num">P50</th><th className="num">P90</th><th className="num">Min</th><th className="num">Max</th></tr></thead>
        <tbody>{OUTCOME_META.filter((o) => outcomes[o.key]).map((o) => {
          const b = outcomes[o.key];
          const cap = b.percentile_capped ? '†' : '';
          const fmtStat = o.count ? fmtCountStat : fmtVal;
          return (
            <tr key={o.key} className={outcomeKey === o.key ? 'row--sel' : ''} onClick={() => setOutcomeKey(o.key)} title="Show distribution">
              <td>{o.label}<div className="cell-sub">{o.unit}</div></td>
              <td className="mono num">{fmtVal(b.mean)}</td>
              <td className="mono num">{fmtStat(b.p10)}{cap}</td>
              <td className="mono num">{fmtStat(b.p50)}{cap}</td>
              <td className="mono num">{fmtStat(b.p90)}{cap}</td>
              <td className="mono num">{fmtStat(b.min)}</td>
              <td className="mono num">{fmtStat(b.max)}</td>
            </tr>
          );
        })}</tbody>
      </table>
      {OUTCOME_META.some((o) => outcomes[o.key]?.percentile_capped) && (
        <p className="fineprint">{ENS_COPY.cappedNote}</p>
      )}
      <h5>{ENS_COPY.curveHead} — {outcomeMeta.label}</h5>
      <span className="seg" role="group" aria-label="Outcome">
        {OUTCOME_META.filter((o) => outcomes[o.key]).map((o) => (
          <button key={o.key} type="button" className={`seg-b${outcomeKey === o.key ? ' on' : ''}`} aria-pressed={outcomeKey === o.key} onClick={() => setOutcomeKey(o.key)}>{labelWord(o.label)}</button>
        ))}
      </span>
      {outcome && <DistributionCurve bins={outcome.curve} unit={outcomeMeta.unit} />}
      {definitions?.[outcomeKey] && (
        <p className="fineprint">{humaniseProse(definitions[outcomeKey], s.graph)}</p>
      )}
      {definitions?.percentiles && (
        <p className="fineprint">{humaniseProse(definitions.percentiles, s.graph)}
          {outcome?.percentile_resolution_d != null && <> {ENS_COPY.resolutionNote}: {fmtVal(outcome.percentile_resolution_d)} days.</>}</p>
      )}
    </>
  );
}

/** Per-place / per-leg branch tables over one served branches block.
 *  Rows open a branch when the caller wires onOpenBranch; without
 *  it (a tree member's aggregates — the tree serves no branch view)
 *  the rows are plain readouts. */
export function BranchTables({ places, legs, placesLimit, onOpenBranch }: {
  places: EnsPlaceBranch[];
  legs: EnsLegBranch[];
  placesLimit?: number;
  onOpenBranch?: (branchKey: string) => void;
}) {
  const s = useStore();
  const placeRow = (b: EnsPlaceBranch) => (
    <tr key={b.branch} className={s.selectedNode === b.node_id ? 'row--sel' : ''}
      onClick={onOpenBranch ? () => onOpenBranch(b.branch) : undefined} title={onOpenBranch ? 'Open branch' : undefined}>
      <td>
        <div>{b.name ?? nodeLabel(b.node_id, s.graph)}</div>
        <div className="cell-sub">{b.kind ? prettyKind(b.kind) : ''}{b.mode ? ` · ${b.mode}` : ''}</div>
      </td>
      <td className="mono amber">{reachedText(b.fired, b.variants)}</td>
      <td className="mono num">{fmtVal(b.total_delay_d.mean)}</td>
      <td className="mono num">{fmtVal(b.delay_contribution_d.mean)}</td>
      <td className="mono num">{fmtVal(b.queue_peak_delay_d)}</td>
    </tr>
  );
  const legRow = (b: EnsLegBranch) => (
    <tr key={b.branch} onClick={onOpenBranch ? () => onOpenBranch(b.branch) : undefined} title={onOpenBranch ? 'Open branch' : undefined}>
      <td>
        <div>{b.from_name ?? nodeLabel(b.from, s.graph)} → {b.to_name ?? nodeLabel(b.to, s.graph)}</div>
        <div className="cell-sub">{prettyKind(b.channel)} leg</div>
      </td>
      <td className="mono amber">{carriedText(b.fired, b.variants)}</td>
      <td className="mono num">{fmtVal(b.delay_contribution_d.mean)}</td>
      <td className="mono num">{fmtVal(b.delay_contribution_d.max)}</td>
    </tr>
  );
  const shownPlaces = placesLimit ? places.slice(0, placesLimit) : places;
  return (
    <>
      <h5>{ENS_COPY.placesHead}</h5>
      {places.length === 0 && <p className="dim">{ENS_COPY.emptyBranches}</p>}
      {places.length > 0 && (
        <table className="data-table">
          <thead><tr><th>Place</th><th>Reached</th><th className="num">Total delay mean (d)</th><th className="num">Own delay mean (d)</th><th className="num">Queue peak (d)</th></tr></thead>
          <tbody>{shownPlaces.map(placeRow)}</tbody>
        </table>
      )}
      <h5>{ENS_COPY.legsHead}</h5>
      {legs.length > 0 ? (
        <table className="data-table">
          <thead><tr><th>Leg</th><th>Carried</th><th className="num">Added delay mean (d)</th><th className="num">Added delay max (d)</th></tr></thead>
          <tbody>{legs.map(legRow)}</tbody>
        </table>
      ) : <p className="dim">{ENS_COPY.emptyBranches}</p>}
    </>
  );
}

type AggView = 'main' | 'branches' | 'branch';

export interface DeepenWiring {
  /** Deepening is offered (an exploration member that can deepen). */
  disabled: boolean;
  onDeepen: (variantIndex: number) => void;
}

/** The statistics / branches / branch sub-views for one flat job,
 *  loaded from that job's own routes. Used by the flat panel for its
 *  active job and by the explorations surface for each member. */
export function JobAggregates({ jobId, status, onOpenVariant, onError, deepen, onLoaded }: {
  jobId: string;
  status: string;
  onOpenVariant: (data: EnsVariantData, index: number) => void;
  onError: (msg: string) => void;
  deepen?: DeepenWiring;
  onLoaded?: (results: EnsStatsResults, branches: EnsStatsBranches) => void;
}) {
  const s = useStore();
  const [results, setResults] = useState<EnsStatsResults | null>(null);
  const [branches, setBranches] = useState<EnsStatsBranches | null>(null);
  const [detail, setDetail] = useState<EnsBranchDetail | null>(null);
  const [view, setView] = useState<AggView>('main');
  const [busy, setBusy] = useState<string | null>(null);
  const [opened, setOpened] = useState<{ index: number; text: string } | null>(null);
  const [deepenText, setDeepenText] = useState('');
  const [deepenError, setDeepenError] = useState<string | null>(null);

  const loadResults = useCallback(async (id: string) => {
    try {
      const [r, b] = await Promise.all([api.ensStatsResults(id), api.ensStatsBranches(id)]);
      setResults(r); setBranches(b);
      onLoaded?.(r, b);
    } catch (e) { onError(cleanErr(e)); }
  }, [onError, onLoaded]);

  useEffect(() => {
    setResults(null); setBranches(null); setDetail(null); setView('main'); setOpened(null);
    if (status === 'complete') void loadResults(jobId);
  }, [jobId, status, loadResults]);

  const openBranch = async (branchKey: string) => {
    setBusy('branch');
    try {
      const d = await api.ensStatsBranch(jobId, branchKey);
      setDetail(d); setView('branch');
    } catch (e) { onError(cleanErr(e)); } finally { setBusy(null); }
  };

  const openVariant = async (index: number) => {
    setBusy('variant');
    try {
      const data: EnsVariantData = await api.ensStatsVariant(jobId, index);
      onOpenVariant(data, index);
      setOpened({ index, text: combinationText(data.combination) });
    } catch (e) { onError(cleanErr(e)); } finally { setBusy(null); }
  };

  const deepenFromText = () => {
    if (!results) return;
    const t = deepenText.trim();
    const n = /^\d+$/.test(t) ? Number(t) : NaN;
    if (!Number.isInteger(n) || n < 0 || n >= results.variants_run) {
      setDeepenError(ENX_COPY.deepenIndexInvalid);
      return;
    }
    setDeepenError(null);
    deepen?.onDeepen(n);
  };

  if (status !== 'complete' || !results) return null;

  return (
    <>
      {view === 'main' && (
        <>
          <OutcomeStatsBlock outcomes={results.outcomes} definitions={results.definitions} />
          <h5>{ENS_COPY.axesHead}</h5>
          <table className="data-table">
            <tbody>{results.axes.map((a) => (
              <tr key={a.axis}>
                <td>{AXIS_LABELS[a.axis] ?? a.axis}<div className="cell-sub">{a.varies ? 'varied' : 'held at one level'}</div></td>
                <td className="mono">{levelsText(a.levels)}</td>
                <td className="fineprint">{humaniseProse(a.description, s.graph)}</td>
              </tr>
            ))}</tbody>
          </table>
          {results.axes_notes.map((n, i) => <p key={i} className="fineprint">{humaniseProse(n, s.graph)}</p>)}

          {results.base_variant && (
            <>
              <h5>{ENS_COPY.baseVariantHead}</h5>
              <p className="mono small">{combinationText(results.base_variant.combination)}</p>
              <p className="fineprint">
                Worst path delay {fmtVal(results.base_variant.outcomes.worst_path_delay_d)} d ·
                queue peak {fmtVal(results.base_variant.outcomes.queue_peak_delay_d)} d ·{' '}
                {fmtVal(results.base_variant.outcomes.places_reached)} places reached ·{' '}
                {fmtVal(results.base_variant.outcomes.chain_events)} chain events.{' '}
                <button className="btn" onClick={() => void openVariant(0)} disabled={busy === 'variant'}>{ENS_COPY.openVariant}</button>
                {deepen && (
                  <> <button className="btn" onClick={() => deepen.onDeepen(0)} disabled={deepen.disabled}>{ENX_COPY.deepenButton}</button></>
                )}
              </p>
            </>
          )}
          {opened && <p className="fineprint">Variant {opened.index} chain opened in the chain view — {opened.text}.</p>}

          {deepen && (
            <>
              <h5>{ENX_COPY.deepenRowHead}</h5>
              <div className="ens-controls">
                <label className="fineprint">{ENX_COPY.deepenIndexLabel}
                  <input className="ens-count mono" value={deepenText} inputMode="numeric"
                    onChange={(e) => setDeepenText(e.target.value)} aria-label={ENX_COPY.deepenIndexLabel} />
                </label>
                <button className="btn" onClick={deepenFromText} disabled={deepen.disabled}>{ENX_COPY.deepenGo}</button>
              </div>
              {deepenError && <p className="honest">{deepenError}</p>}
            </>
          )}

          <h5>{ENS_COPY.branchesTitle}</h5>
          <p className="fineprint">{fmtInt(results.places_reached_any)} places reached · {fmtInt(results.legs_fired_any)} legs carried an effect · in at least one variant</p>
          {branches && branches.places.length > 0 && (
            <table className="data-table">
              <thead><tr><th>Place</th><th>Reached</th><th className="num">Total delay mean (d)</th><th className="num">Own delay mean (d)</th><th className="num">Queue peak (d)</th></tr></thead>
              <tbody>{branches.places.slice(0, 10).map((b) => (
                <tr key={b.branch} className={s.selectedNode === b.node_id ? 'row--sel' : ''} onClick={() => void openBranch(b.branch)} title="Open branch">
                  <td>
                    <div>{b.name ?? nodeLabel(b.node_id, s.graph)}</div>
                    <div className="cell-sub">{b.kind ? prettyKind(b.kind) : ''}{b.mode ? ` · ${b.mode}` : ''}</div>
                  </td>
                  <td className="mono amber">{reachedText(b.fired, b.variants)}</td>
                  <td className="mono num">{fmtVal(b.total_delay_d.mean)}</td>
                  <td className="mono num">{fmtVal(b.delay_contribution_d.mean)}</td>
                  <td className="mono num">{fmtVal(b.queue_peak_delay_d)}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
          <button className="btn" onClick={() => setView('branches')}>{ENS_COPY.branchesButton}</button>
        </>
      )}

      {view === 'branches' && branches && (
        <>
          <button className="btn" onClick={() => setView('main')}><Icon name="arrow-left" size="xs" /> {ENS_COPY.backToStats}</button>
          <BranchTables places={branches.places} legs={branches.legs} onOpenBranch={(k) => void openBranch(k)} />
          {branches.definitions.fire_share && (
            <p className="fineprint">{humaniseProse(branches.definitions.fire_share, s.graph)}</p>
          )}
        </>
      )}

      {view === 'branch' && detail && (
        <>
          <button className="btn" onClick={() => setView('branches')}><Icon name="arrow-left" size="xs" /> {ENS_COPY.backToBranches}</button>
          {'node_id' in detail.branch ? (
            <>
              <h5>{detail.branch.name ?? nodeLabel(detail.branch.node_id, s.graph)}</h5>
              <p className="fineprint">{detail.branch.kind ? prettyKind(detail.branch.kind) : ''}
                {detail.branch.mode ? ` · ${detail.branch.mode}` : ''} — {reachedText(detail.branch.fired, detail.variants_run)}</p>
              <table className="data-table">
                <tbody>
                  <tr><td>Total delay (d)</td><td className="mono num">mean {fmtVal(detail.branch.total_delay_d.mean)} · p50 {fmtVal(detail.branch.total_delay_d.p50)} · p90 {fmtVal(detail.branch.total_delay_d.p90)} · max {fmtVal(detail.branch.total_delay_d.max)}</td></tr>
                  <tr><td>Own delay (d)</td><td className="mono num">mean {fmtVal(detail.branch.delay_contribution_d.mean)} · p50 {fmtVal(detail.branch.delay_contribution_d.p50)} · p90 {fmtVal(detail.branch.delay_contribution_d.p90)} · max {fmtVal(detail.branch.delay_contribution_d.max)}</td></tr>
                  <tr><td>Queue peak (d)</td><td className="mono num">{fmtVal(detail.branch.queue_peak_delay_d)}</td></tr>
                  <tr><td>First lands by</td><td className="mono">{Object.entries(detail.branch.first_hit_channels).map(([c, n]) => `${c} × ${fmtInt(n)}`).join(' · ') || '—'}</td></tr>
                  <tr><td>First step</td><td className="mono num">earliest {fmtVal(detail.branch.first_seq_min)} · mean {fmtVal(detail.branch.first_seq_mean)}</td></tr>
                </tbody>
              </table>
            </>
          ) : (
            <>
              <h5>{detail.branch.from_name ?? nodeLabel(detail.branch.from, s.graph)} → {detail.branch.to_name ?? nodeLabel(detail.branch.to, s.graph)}</h5>
              <p className="fineprint">{prettyKind(detail.branch.channel)} leg — {carriedText(detail.branch.fired, detail.variants_run)}</p>
              <table className="data-table">
                <tbody>
                  <tr><td>Added delay (d)</td><td className="mono num">mean {fmtVal(detail.branch.delay_contribution_d.mean)} · p50 {fmtVal(detail.branch.delay_contribution_d.p50)} · p90 {fmtVal(detail.branch.delay_contribution_d.p90)} · max {fmtVal(detail.branch.delay_contribution_d.max)}</td></tr>
                </tbody>
              </table>
            </>
          )}
          <h5>{ENS_COPY.firingHead} — {countOf(detail.variants_fired.length, detail.variants_run)}</h5>
          <div className="ens-chips">
            {detail.variants_fired.slice(0, VARIANT_LIST_CAP).map((i) => (
              <span key={i} className="enx-chipgroup">
                <button className="ens-chip mono" onClick={() => void openVariant(i)} disabled={busy === 'variant'} title="Open variant">
                  Variant {i}
                </button>
                {deepen && (
                  <button className="ens-chip mono" onClick={() => deepen.onDeepen(i)} disabled={deepen.disabled}
                    title={ENX_COPY.deepenButton}>Deepen</button>
                )}
              </span>
            ))}
          </div>
          {detail.variants_fired.length > VARIANT_LIST_CAP && (
            <p className="fineprint">{shownNote(VARIANT_LIST_CAP, detail.variants_fired.length)}</p>
          )}
          {opened && <p className="fineprint">Variant {opened.index} chain opened in the chain view — {opened.text}.</p>}
        </>
      )}
    </>
  );
}
