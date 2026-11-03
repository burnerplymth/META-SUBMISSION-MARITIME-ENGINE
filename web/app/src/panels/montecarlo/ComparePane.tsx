import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, SegmentedControl } from '../../ui';
import { Chip, MetaChip } from '../reports/chips';
import { buildCompareHistogram, buildFan, buildPlaceDeltas } from './charts';
import { docFits, fanPair, metricHist, overlapCoefficient, pairedFor, placeDeltas, deltaRow, sharedMetrics, type CompareDoc, type RunData } from './compare';
import { saveCompareCsv, saveCompareWorkbook, type ExportCtx } from './exportChart';
import { fmt, fmtPct, fmtSigned, kOfN, metricLabel } from './fmt';
import { finite, mostMoving } from './math';
import { ChartCard, TableHead } from './parts';
import { runSub, runTitle, type RunRec } from './runs';
import { METRIC_ORDER } from './types';

/* Compare: two or more runs side by side. A summary row per run for one metric, then run A against run B: the
 * overlaid distributions, the fans of one place, the difference by place and, when the two runs share their random
 * draws, the number of paired simulations in which B is higher. */

const mostMovingPair = (ps: ReadonlyArray<{ place: string; name: string; a: { p50: number[] }; b: { p50: number[] } }>) => {
  const m = mostMoving(ps.map((p) => ({ place: p.place, p50: [...p.a.p50, ...p.b.p50] })));
  return ps.find((p) => p.place === m?.place) ?? null;
};

export function ComparePane({ runs, a, b, onPick, nameOf, doc, ctxFor, metric, onMetric }: {
  runs: readonly RunRec[]; a: RunRec | null; b: RunRec | null; onPick: (role: 'a' | 'b', key: string) => void; nameOf: (id: string) => string;
  doc: CompareDoc | null; ctxFor: (a: RunRec, b: RunRec) => ExportCtx; metric: string; onMetric: (m: string) => void;
}) {
  const usable = runs.filter((r) => r.job.results);
  if (usable.length < 2 || !a || !b || !a.job.results || !b.job.results) {
    return (
      <div className="mc-view" data-testid="mc-compare">
        <section className="mc-card"><Chip chip="NEEDS RUN" word="Two runs needed" /><MetaChip>{`${usable.length} of 2`}</MetaChip></section>
      </div>
    );
  }
  return <Pair runs={usable} a={a} b={b} onPick={onPick} nameOf={nameOf} doc={doc} ctxFor={ctxFor} metric={metric} onMetric={onMetric} />;
}

function Pair({ runs, a, b, onPick, nameOf, doc, ctxFor, metric, onMetric }: {
  runs: readonly RunRec[]; a: RunRec; b: RunRec; onPick: (role: 'a' | 'b', key: string) => void; nameOf: (id: string) => string;
  doc: CompareDoc | null; ctxFor: (a: RunRec, b: RunRec) => ExportCtx; metric: string; onMetric: (m: string) => void;
}) {
  const keys = useMemo(() => sharedMetrics(a.job, b.job, METRIC_ORDER), [a.job, b.job]);
  const key = keys.includes(metric) ? metric : keys[0] ?? '';
  const da: RunData = a; const db: RunData = b;
  const served = docFits(doc, a.job, b.job) ? doc : null;
  const ma = a.job.results!.metrics[key]; const mb = b.job.results!.metrics[key];
  const unit = ma?.unit ?? 'd';
  const [thr, setThr] = useState<number>(0);
  useEffect(() => { setThr(0); }, [key, a.key, b.key]);
  const draws = a.job.request.n === b.job.request.n && a.job.request.seed === b.job.request.seed;
  const paired = draws ? pairedFor(key, da, db, thr) : null;
  const hist = useMemo(() => metricHist(key, da, db, served), [key, a, b, served]); // eslint-disable-line react-hooks/exhaustive-deps
  const overlap = hist ? overlapCoefficient(hist.a, hist.b) : null;
  const ci = served?.metrics[key]?.shift_ci ?? null;
  const row = ma && mb ? deltaRow(key, ma, mb) : null;
  const ctx = ctxFor(a, b);
  const nameA = runTitle(a, nameOf); const nameB = runTitle(b, nameOf);

  const buildHist = useCallback((w: number) => buildCompareHistogram({
    edges: hist?.edges ?? [], a: hist?.a ?? [], b: hist?.b ?? [], unit, p50a: ma?.p50 ?? null, p50b: mb?.p50 ?? null, nameA, nameB,
  }, w, 240), [hist, unit, ma, mb, nameA, nameB]);

  const fans = useMemo(() => fanPair(a.job, b.job, served), [a.job, b.job, served]);
  const [placeId, setPlaceId] = useState<string>(mostMovingPair(fans.places)?.place ?? '');
  useEffect(() => { if (!fans.places.some((p) => p.place === placeId)) setPlaceId(mostMovingPair(fans.places)?.place ?? ''); }, [fans, placeId]);
  const fp = fans.places.find((p) => p.place === placeId) ?? fans.places[0] ?? null;
  const buildFanPair = useCallback((w: number) => buildFan(
    { t: fans.t, place: { place: fp!.place, name: fp!.name, ...fp!.a }, unit: 'd' }, w, 240, { t: fans.t, place: { place: fp!.place, name: fp!.name, ...fp!.b }, unit: 'd' },
  ), [fans.t, fp]);

  const deltas = useMemo(() => (served?.per_place.length ? served.per_place : placeDeltas(a.job.results!.per_place, b.job.results!.per_place)), [served, a.job, b.job]);
  const buildDeltas = useCallback((w: number) => buildPlaceDeltas(deltas.map((d) => ({ place: d.place, name: d.name, d50: d.delta_p50, d90: d.delta_p90 })), 'd', w), [deltas]);

  const [note, setNote] = useState<string | null>(null);
  useEffect(() => { if (!note) return; const t = setTimeout(() => setNote(null), 3200); return () => clearTimeout(t); }, [note]);

  return (
    <div className="mc-view" data-testid="mc-compare">
      <section className="mc-card mc-pickers" aria-label="Runs compared">
        {(['a', 'b'] as const).map((role) => {
          const cur = role === 'a' ? a : b;
          return (
            <div key={role} className="mc-field mc-field--wide">
              <label htmlFor={`mc-cmp-${role}`} className="mc-lab"><i className={`mc-dot mc-dot--${role}`} />{role.toUpperCase()}</label>
              <select id={`mc-cmp-${role}`} className="mc-input" value={cur.key} onChange={(e) => onPick(role, e.target.value)}>
                {runs.map((r) => <option key={r.key} value={r.key}>{`${runTitle(r, nameOf)} · ${runSub(r)}${r.source === 'recorded' ? ' · Recorded' : ''}`}</option>)}
              </select>
            </div>
          );
        })}
        <div className="mc-chips">
          <Chip chip={draws ? 'SOURCED' : 'FLAG'} word={draws ? 'Shared draws' : 'Different draws'} />
          {a.source === 'recorded' || b.source === 'recorded' ? <Chip chip="RECORDED" tone="snapshot" /> : null}
        </div>
      </section>
      <div className="mc-picker">
        <SegmentedControl label="Metric" size="sm" value={key} onChange={onMetric} options={keys.map((k) => ({ value: k, label: metricLabel(k) }))} />
      </div>

      <section className="mc-card" aria-label="Side by side">
        <TableHead heading="Side by side" />
        <div className="mc-tablewrap">
          <table className="mc-table" data-testid="mc-side">
            <thead><tr><th scope="col">Run</th><th scope="col" className="num">P10</th><th scope="col" className="num">P50</th><th scope="col" className="num">P90</th><th scope="col" className="num">Mean</th><th scope="col" className="num">P50 vs A</th><th scope="col" className="num">P90 vs A</th></tr></thead>
            <tbody>
              {runs.map((r) => {
                const m = r.job.results?.metrics[key];
                if (!m) return null;
                const isA = r.key === a.key; const isB = r.key === b.key;
                return (
                  <tr key={r.key} className={isA ? 'is-a' : isB ? 'is-b' : ''}>
                    <th scope="row">{(isA || isB) && <i className={`mc-dot mc-dot--${isA ? 'a' : 'b'}`} />}{runTitle(r, nameOf)}<span className="mc-unit-tag">{runSub(r)}</span></th>
                    <td className="num">{fmt(m.p10, unit)}</td><td className="num">{fmt(m.p50, unit)}</td><td className="num">{fmt(m.p90, unit)}</td><td className="num">{fmt(m.mean, unit)}</td>
                    <td className="num">{r.key === a.key ? '—' : fmtSigned(m.p50 - ma.p50, unit)}</td><td className="num">{r.key === a.key ? '—' : fmtSigned(m.p90 - ma.p90, unit)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="mc-card" aria-label="A against B">
        <TableHead heading="B against A" />
        <dl className="mc-facts" data-testid="mc-facts">
          {row && <div><dt>Shift in P50</dt><dd>{`${fmtSigned(row.d50, unit)} ${unit === 'd' ? 'd' : unit}`}</dd></div>}
          {row && <div><dt>Shift in P90</dt><dd>{`${fmtSigned(row.d90, unit)} ${unit === 'd' ? 'd' : unit}`}</dd></div>}
          <div><dt>Overlap</dt><dd>{overlap === null ? '—' : fmtPct(overlap)}</dd></div>
          {ci && <div><dt>Shift interval</dt><dd>{`${fmtSigned(ci[0], unit)} to ${fmtSigned(ci[1], unit)} ${unit === 'd' ? 'd' : unit}`}</dd></div>}
        </dl>
        {paired ? (
          <div className="mc-thr">
            <label htmlFor="mc-cmp-thr" className="mc-lab">B higher by more than</label>
            <span className="mc-unit"><input id="mc-cmp-thr" className="mc-input mc-input--thr" type="number" min={0} step={unit === 'd' ? 1 : 0.5} value={thr}
              onChange={(e) => setThr(finite(Number(e.target.value)) ? Math.max(0, Number(e.target.value)) : 0)} /><i>{unit === 'd' ? 'd' : unit}</i></span>
            <strong className="mc-kofn" data-testid="mc-paired">{`B is higher in ${kOfN(paired.k, paired.n).replace('simulations', 'paired simulations')}`}</strong>
            <span className="mc-dim">{fmtPct(paired.share)}</span>
          </div>
        ) : <MetaChip>{draws ? 'Samples not served' : 'Not paired'}</MetaChip>}
      </section>

      {hist && <ChartCard heading={`${metricLabel(key)}, A and B`} kind="compare-distribution" ctx={ctx} build={buildHist} label={a.job.label} testid="mc-cmp-hist"
        chips={<><span className="mc-key"><i className="mc-dot mc-dot--a" />A</span><span className="mc-key"><i className="mc-dot mc-dot--b" />B</span></>} />}
      {fp && fans.places.length > 0 && (
        <>
          <div className="mc-picker">
            <SegmentedControl label="Place" size="sm" value={fp.place} onChange={setPlaceId} options={fans.places.map((p) => ({ value: p.place, label: p.name }))} />
          </div>
          <ChartCard heading={`Added delay at ${fp.name}, A and B`} kind="compare-fan" ctx={ctx} build={buildFanPair} label={a.job.label} testid="mc-cmp-fan"
            chips={<><span className="mc-key"><i className="mc-dot mc-dot--a" />A</span><span className="mc-key"><i className="mc-dot mc-dot--b" />B</span></>} />
        </>
      )}
      {deltas.length > 0 && <ChartCard heading="Difference by place" kind="compare-places" ctx={ctx} build={buildDeltas} label={a.job.label} testid="mc-cmp-places" />}

      <section className="mc-card" aria-label="Export comparison">
        <TableHead heading="Comparison export">
          <span className="mc-spacer" />
          <Button size="sm" variant="secondary" icon="file-down" data-testid="mc-cmp-csv" onClick={() => setNote(saveCompareCsv(a.job, b.job, served, a.samples, b.samples, thr, ctx).word)}>CSV</Button>
          <Button size="sm" variant="secondary" icon="file-spreadsheet" data-testid="mc-cmp-xlsx" onClick={() => setNote(saveCompareWorkbook(a.job, b.job, served, a.samples, b.samples, thr, ctx).word)}>XLSX</Button>
          {note && <span className="cx-status ok" role="status">{note}</span>}
        </TableHead>
      </section>
    </div>
  );
}
