import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { VNodes } from '../../charts/VNodeView';
import { Button, SegmentedControl } from '../../ui';
import { Chip, MetaChip } from '../reports/chips';
import {
  buildConvergence, buildFan, buildHistogram, buildTornado, COLOR_MARK, stripLayout, type Marker,
} from './charts';
import { checkDigest, type DigestCheck } from './digest';
import type { ExportCtx } from './exportChart';
import { saveSamplesCsv, saveWorkbook } from './exportChart';
import {
  digestShort, fmt, fmt1, fmtInt, fmtPct, kOfN, metricLabel, withUnit,
} from './fmt';
import { inputLabel } from './inputs';
import {
  bucketOf, clampThreshold, defaultThreshold, finite, lastStepChange, mostMoving, thresholdRange, thresholdShare, topPlaces, BUCKET_WORDS,
} from './math';
import { ChartCard, LabelChip, TableHead } from './parts';
import type { RunRec } from './runs';
import { METRIC_ORDER, type McJob, type McResults, type MetricStats } from './types';
import { useWidth } from './ChartHost';
import { ProvenanceCell } from '../../provenance/ProvenanceCell';
import { monteCarloProvenance, type McStat } from '../../provenance/registry';

/* The views of one finished run: bands (P10, P50, P90 over time and by metric), the distribution of one metric with
 * the share of simulations above a threshold, and what drives the spread (sensitivity, convergence, inputs, samples). */

export type View = 'bands' | 'distribution' | 'drivers';

const ord = (k: string): number => { const i = METRIC_ORDER.indexOf(k); return i < 0 ? METRIC_ORDER.length : i; };
export const metricKeys = (r: McResults): string[] => Object.keys(r.metrics).sort((a, b) => ord(a) - ord(b) || a.localeCompare(b));

export function Results({ run, view, ctx, metric, onMetric }: { run: RunRec; view: View; ctx: ExportCtx; metric: string; onMetric: (m: string) => void }) {
  const job = run.job as McJob & { results: McResults };
  if (view === 'bands') return <Bands run={run} job={job} ctx={ctx} />;
  if (view === 'distribution') return <Distribution run={run} job={job} ctx={ctx} metric={metric} onMetric={onMetric} />;
  return <Drivers run={run} job={job} ctx={ctx} />;
}

/* ── bands ────────────────────────────────────────────────────────────── */

export function MetricTable({ job, recorded = false }: { job: McJob & { results: McResults }; recorded?: boolean }) {
  const r = job.results;
  const keys = metricKeys(r);
  return (
    <section className="mc-card" aria-label="Metrics">
      <TableHead heading="Added delay and chain size" />
      <div className="mc-tablewrap">
        <table className="mc-table" data-testid="mc-metrics">
          <thead><tr><th scope="col">Metric</th><th scope="col" className="num">P10</th><th scope="col" className="num">P50</th><th scope="col" className="num">P90</th><th scope="col" className="num">Mean</th><th scope="col" className="num">Range</th></tr></thead>
          <tbody>
            {keys.map((k) => {
              const m = r.metrics[k];
              return (
                <tr key={k}>
                  <th scope="row">{metricLabel(k)}<span className="mc-unit-tag">{m.unit}</span></th>
                  {([['p10', fmt(m.p10, m.unit)], ['p50', fmt(m.p50, m.unit)], ['p90', fmt(m.p90, m.unit)], ['mean', fmt(m.mean, m.unit)], ['range', `${fmt(m.min, m.unit)} to ${fmt(m.max, m.unit)}`]] as Array<[McStat, string]>).map(([st, shown]) => (
                    <td key={st} className="num"><ProvenanceCell make={() => monteCarloProvenance({ job, metric: k, metricLabel: metricLabel(k), stat: st, display: shown, recorded, m })}>{shown}</ProvenanceCell></td>
                  ))}
                </tr>
              );
            })}
            {(r.metrics_not_defined ?? []).map((n) => (
              <tr key={n.metric} className="mc-nd"><th scope="row">{metricLabel(n.metric)}</th><td colSpan={5}><Chip chip="NOT SERVED" word="Not served" /></td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Bands({ run, job, ctx }: { run: RunRec; job: McJob & { results: McResults }; ctx: ExportCtx }) {
  const r = job.results;
  const places = useMemo(() => topPlaces(r.series, 5), [r.series]);
  const [placeId, setPlaceId] = useState<string>(mostMoving(places)?.place ?? '');
  useEffect(() => { if (!places.some((p) => p.place === placeId)) setPlaceId(mostMoving(places)?.place ?? ''); }, [places, placeId]);
  const sel = places.find((p) => p.place === placeId) ?? places[0] ?? null;
  const unit = r.series.unit ?? 'd';
  const build = useCallback((w: number) => buildFan({ t: r.series.t_days, place: sel!, unit }, w, 240), [r.series.t_days, sel, unit]);
  return (
    <div className="mc-view" data-testid="mc-bands">
      <MetricTable job={job} recorded={run.source === 'recorded'} />
      {sel ? (
        <ChartCard heading={`Added delay at ${sel.name}`} kind="fan" ctx={ctx} build={build} label={job.label} testid="mc-fan"
          chips={<MetaChip>P10 to P90</MetaChip>} />
      ) : <section className="mc-card"><Chip chip="NOT SERVED" word="Not served" /></section>}
      {places.length > 1 && (
        <div className="mc-picker">
          <SegmentedControl label="Place" size="sm" value={sel?.place ?? ''} onChange={setPlaceId}
            options={places.map((p) => ({ value: p.place, label: p.name }))} />
        </div>
      )}
      <section className="mc-card" aria-label="Places">
        <TableHead heading="Added delay by place" />
        <div className="mc-tablewrap">
          <table className="mc-table" data-testid="mc-places">
            <thead><tr><th scope="col">Place</th><th scope="col" className="num">P10 d</th><th scope="col" className="num">P50 d</th><th scope="col" className="num">P90 d</th><th scope="col" className="num">Simulations affected</th></tr></thead>
            <tbody>
              {r.per_place.slice(0, 10).map((p) => (
                <tr key={p.place}>
                  <th scope="row">{p.name}</th>
                  <td className="num">{fmt1(p.added_delay_d.p10)}</td><td className="num">{fmt1(p.added_delay_d.p50)}</td><td className="num">{fmt1(p.added_delay_d.p90)}</td>
                  <td className="num">{finite(p.affected_k) ? `${fmtInt(p.affected_k)} of ${fmtInt(r.n)}` : fmtPct(p.affected_share)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <Samples run={run} job={job} ctx={ctx} />
    </div>
  );
}

/* ── distribution ─────────────────────────────────────────────────────── */

function Distribution({ run, job, ctx, metric, onMetric }: { run: RunRec; job: McJob & { results: McResults }; ctx: ExportCtx; metric: string; onMetric: (m: string) => void }) {
  const r = job.results;
  const keys = metricKeys(r);
  const key = r.metrics[metric] ? metric : keys[0];
  const m = r.metrics[key] as MetricStats | undefined;
  const [cum, setCum] = useState(false);
  const [thr, setThr] = useState<number | null>(null);
  useEffect(() => { setThr(null); }, [key, job.job_id]);
  const range = useMemo(() => (m ? thresholdRange(m) : { min: 0, max: 1, step: 0.1 }), [m]);
  const x = m ? clampThreshold(thr ?? defaultThreshold(m), range) : 0;
  const values = useMemo(() => (run.samples ? run.samples.map((s) => s.outputs[key]).filter(finite) : null), [run.samples, key]);
  const share = m ? thresholdShare(m, x, values) : null;
  const unit = m?.unit ?? 'd';
  const markers = useMemo<Marker[]>(() => (m ? [
    { label: `P10 ${fmt(m.p10, unit)}`, value: m.p10, color: COLOR_MARK, dash: '3 3', kind: 'pct' },
    { label: `P50 ${fmt(m.p50, unit)}`, value: m.p50, color: COLOR_MARK, kind: 'pct' },
    { label: `P90 ${fmt(m.p90, unit)}`, value: m.p90, color: COLOR_MARK, dash: '3 3', kind: 'pct' },
  ] : []), [m, unit]);
  const build = useCallback((w: number) => buildHistogram({ h: m!.histogram, unit, markers, threshold: x, cumulative: cum }, w, 240), [m, unit, markers, x, cum]);
  if (!m) return <section className="mc-card"><Chip chip="NOT SERVED" word="Not served" /></section>;
  return (
    <div className="mc-view" data-testid="mc-distribution">
      <div className="mc-picker">
        <SegmentedControl label="Metric" size="sm" value={key} onChange={(v) => { onMetric(v); }} options={keys.map((k) => ({ value: k, label: metricLabel(k) }))} />
        <SegmentedControl label="Chart" size="sm" value={cum ? 'cum' : 'hist'} onChange={(v) => setCum(v === 'cum')}
          options={[{ value: 'hist', label: 'Histogram' }, { value: 'cum', label: 'Cumulative' }]} />
      </div>
      <ChartCard heading={`${metricLabel(key)}, ${cum ? 'share above' : 'simulations'}`} kind={cum ? 'exceedance' : 'histogram'} ctx={ctx} build={build} label={job.label} testid="mc-hist" />
      <section className="mc-card" aria-label="Share of simulations">
        <TableHead heading="Share of simulations" />
        <div className="mc-thr">
          <label htmlFor="mc-thr" className="mc-lab">Above</label>
          <input id="mc-thr" type="range" className="mc-range" min={range.min} max={range.max} step={range.step} value={x} onChange={(e) => setThr(Number(e.target.value))} aria-label="Threshold" />
          <span className="mc-unit"><input className="mc-input mc-input--thr" type="number" min={range.min} max={range.max} step={range.step} value={Number(x.toFixed(4))} aria-label="Threshold value"
            onChange={(e) => setThr(clampThreshold(Number(e.target.value), range))} /><i>{unit === 'd' ? 'd' : unit}</i></span>
          {share && <strong className="mc-kofn" data-testid="mc-kofn">{kOfN(share.k, share.n)}</strong>}
          {share && <span className="mc-dim">{fmtPct(share.share)}</span>}
          {share && share.source === 'histogram' && <span data-testid="mc-interp"><MetaChip>Interpolated</MetaChip></span>}
        </div>
        <div className="mc-tablewrap">
          <table className="mc-table" data-testid="mc-exceed">
            <thead><tr><th scope="col" className="num">Above</th><th scope="col" className="num">Simulations</th><th scope="col" className="num">Share</th></tr></thead>
            <tbody>
              {m.exceedance.map((e) => (
                <tr key={e.threshold}><td className="num">{withUnit(e.threshold, unit)}</td><td className="num">{`${fmtInt(e.k)} of ${fmtInt(e.n)}`}</td><td className="num">{fmtPct(e.share)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        {run.samples ? <Strip run={run} metric={key} m={m} x={x} /> : <Chip chip="NOT SERVED" word="Samples not served" />}
      </section>
    </div>
  );
}

/** One cell per simulation: filled when the output is above the threshold. */
function Strip({ run, metric, m, x }: { run: RunRec; metric: string; m: MetricStats; x: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const w = useWidth(ref, 480);
  const rows = run.samples ?? [];
  const lay = useMemo(() => stripLayout(rows.length, w), [rows.length, w]);
  const [hover, setHover] = useState<number>(-1);
  const hv = hover >= 0 ? rows[hover] : null;
  return (
    <div className="mc-strip" ref={ref} data-testid="mc-strip">
      <svg width={lay.width} height={lay.height} role="img" aria-label={`Each cell is one simulation; filled above ${fmt(x, m.unit)}`}
        onMouseMove={(e) => { const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect(); setHover(lay.indexAt(e.clientX - r.left, e.clientY - r.top)); }} onMouseLeave={() => setHover(-1)}>
        {rows.map((s, i) => {
          const v = s.outputs[metric]; const p = lay.at(i); const above = finite(v) && v > x;
          const b = bucketOf(v ?? undefined, m);
          return <rect key={s.i} x={p.x} y={p.y} width={lay.cell} height={lay.cell} rx={1} className={`mc-cell${above ? ' on' : ''}${hover === i ? ' hv' : ''}`} data-bucket={b ?? ''} />;
        })}
      </svg>
      <div className="mc-strip-read" role="status">
        {hv ? `Simulation ${hv.i + 1}: ${withUnit(hv.outputs[metric], m.unit)}${finite(hv.outputs[metric]) ? ` · ${BUCKET_WORDS[bucketOf(hv.outputs[metric] ?? undefined, m) ?? 0]}` : ''}` : ''}
      </div>
    </div>
  );
}

/* ── drivers ──────────────────────────────────────────────────────────── */

function Drivers({ run, job, ctx }: { run: RunRec; job: McJob & { results: McResults }; ctx: ExportCtx }) {
  const r = job.results;
  const worst = r.metrics.worst_added_transit_d;
  const unit = worst?.unit ?? 'd';
  const tornado = useCallback((w: number) => buildTornado(r.sensitivity, unit, w), [r.sensitivity, unit]);
  const conv = (key: 'p50' | 'p90') => r.convergence.map((c) => ({ n: c.n, v: c[key] }));
  const [dig, setDig] = useState<DigestCheck>('unchecked');
  useEffect(() => { let on = true; void checkDigest(run.samples, r.n, r.runs_digest).then((d) => { if (on) setDig(d); }); return () => { on = false; }; }, [run.samples, r.n, r.runs_digest]);
  return (
    <div className="mc-view" data-testid="mc-drivers">
      {r.sensitivity.length > 0 && <ChartCard heading="Sensitivity of worst added transit" kind="sensitivity" ctx={ctx} build={tornado} label={job.label} testid="mc-tornado" />}
      <section className="mc-card" aria-label="Convergence">
        <TableHead heading="Convergence" />
        <div className="mc-conv">
          {(['p50', 'p90'] as const).map((k) => <ConvSpark key={k} title={k.toUpperCase()} rows={conv(k)} unit={unit} change={lastStepChange(r.convergence, k)} />)}
        </div>
      </section>
      <section className="mc-card" aria-label="Inputs">
        <TableHead heading="Input ranges"><MetaChip>{`Digest ${digestShort(r.runs_digest)}`}</MetaChip>
          {dig === 'match' && <Chip chip="SOURCED" word="Digest matches" />}{dig === 'differs' && <Chip chip="FLAG" word="Digest differs" />}</TableHead>
        <div className="mc-tablewrap">
          <table className="mc-table" data-testid="mc-inputs">
            <thead><tr><th scope="col">Input</th><th scope="col">Shape</th><th scope="col" className="num">Low</th><th scope="col" className="num">Mode</th><th scope="col" className="num">High</th><th scope="col">Source</th></tr></thead>
            <tbody>
              {job.inputs.map((i) => (
                <tr key={i.name}>
                  <th scope="row">{inputLabel(i.name)}</th><td>{i.dist.charAt(0).toUpperCase() + i.dist.slice(1)}</td>
                  <td className="num">{i.dist === 'fixed' ? '—' : fmt1(i.low)}</td><td className="num">{i.dist === 'uniform' ? '—' : fmt1(i.mode)}</td><td className="num">{i.dist === 'fixed' ? '—' : fmt1(i.high)}</td>
                  <td>{i.source === 'request' ? 'Request' : 'Default'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <Samples run={run} job={job} ctx={ctx} />
    </div>
  );
}

function ConvSpark({ title, rows, unit, change }: { title: string; rows: Array<{ n: number; v: number }>; unit: string; change: number | null }) {
  const ref = useRef<HTMLDivElement>(null);
  const w = useWidth(ref, 200);
  const chart = useMemo(() => buildConvergence(rows, unit, w), [rows, unit, w]);
  return (
    <div className="mc-spark" ref={ref}>
      <div className="mc-spark-h"><span className="mc-lab">{title}</span>{change !== null && <span className="mc-dim">{`Last step ${change >= 0 ? '+' : '−'}${(Math.abs(change) * 100).toFixed(1)}%`}</span>}</div>
      <svg width={chart.width} height={chart.height} viewBox={`0 0 ${chart.width} ${chart.height}`} role="img" aria-label={`${title} against simulations used`}>
        <VNodes nodes={chart.nodes} />
      </svg>
    </div>
  );
}

/* ── samples ──────────────────────────────────────────────────────────── */

function Samples({ run, job, ctx }: { run: RunRec; job: McJob & { results: McResults }; ctx: ExportCtx }) {
  const rows = run.samples;
  const names = job.inputs.filter((i) => i.dist !== 'fixed').map((i) => i.name);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => { if (!note) return; const t = setTimeout(() => setNote(null), 3200); return () => clearTimeout(t); }, [note]);
  return (
    <section className="mc-card" aria-label="Samples">
      <TableHead heading="Simulations">
        {rows ? <MetaChip>{`${fmtInt(Math.min(8, rows.length))} of ${fmtInt(rows.length)} shown`}</MetaChip> : <Chip chip="NOT SERVED" word="Samples not served" />}
        <span className="mc-spacer" />
        <Button size="sm" variant="secondary" icon="file-down" disabled={!rows} data-testid="mc-csv" onClick={() => { if (rows) setNote(saveSamplesCsv(job, rows, ctx).word); }}>CSV</Button>
        <Button size="sm" variant="secondary" icon="file-spreadsheet" disabled={!rows} data-testid="mc-xlsx" onClick={() => { if (rows) setNote(saveWorkbook(job, rows, ctx).word); }}>XLSX</Button>
        {note && <span className="cx-status ok" role="status">{note}</span>}
      </TableHead>
      {rows && (
        <div className="mc-tablewrap">
          <table className="mc-table" data-testid="mc-samples">
            <thead><tr><th scope="col" className="num">Simulation</th>{names.map((n) => <th key={n} scope="col" className="num">{inputLabel(n)}</th>)}<th scope="col" className="num">Worst added transit d</th></tr></thead>
            <tbody>
              {rows.slice(0, 8).map((s) => (
                <tr key={s.i}><td className="num">{s.i + 1}</td>{names.map((n) => <td key={n} className="num">{fmt1(s.inputs[n])}</td>)}<td className="num">{fmt1(s.outputs.worst_added_transit_d)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
