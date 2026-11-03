/* Run report of the Monte Carlo window: a finished run, or two runs compared. The same drawings as the window (charts.ts),
 * laid out for a page: cover with the label chips, inputs, seed and digest, the P10 / P50 / P90 table, the fan per place,
 * the distribution with its "k of N simulations" threshold, the drivers, and the provenance. Pure data in, ReportDoc out. */
import type { McChart } from './charts';
import { buildCompareHistogram, buildConvergence, buildFan, buildHistogram, buildPlaceDeltas, buildTornado, COLOR_MARK, type Marker } from './charts';
import { checkDigest } from './digest';
import { deltaRow, docFits, fanPair, metricHist, overlapCoefficient, pairedFor, placeDeltas, sharedMetrics, type CompareDoc } from './compare';
import { digestShort, fmt, fmt1, fmtInt, fmtPct, fmtSigned, kOfN, MC_LABEL, metricLabel, requestLine, seedWord, withUnit } from './fmt';
import { inputLabel } from './inputs';
import { clampThreshold, defaultThreshold, finite, lastStepChange, mostMoving, thresholdRange, thresholdShare, topPlaces } from './math';
import { runSub, runTitle, type RunRec } from './runs';
import { METRIC_ORDER, type McJob, type McResults, type MetricStats } from './types';
import { figureFromNodes } from '../../exports/report/figure';
import { CONTENT_PX, chipFromWord, chipOf, type Block, type FigureBlock, type ReportChip, type ReportDoc, type TableCell } from '../../exports/report/model';

const ord = (k: string): number => { const i = METRIC_ORDER.indexOf(k); return i < 0 ? METRIC_ORDER.length : i; };
const metricKeys = (r: McResults): string[] => Object.keys(r.metrics).sort((a, b) => ord(a) - ord(b) || a.localeCompare(b));

const W = CONTENT_PX.portrait;
const HALF = Math.floor((W - 18) / 2);

const fig = (title: string, c: McChart, o: { chips?: ReportChip[]; caption?: string } = {}): FigureBlock =>
  figureFromNodes(title, { nodes: c.nodes, width: c.width, height: c.height }, o);

const sourceChip = (r: RunRec): ReportChip => (r.source === 'recorded' ? chipOf('Recorded', 'recorded') : chipOf('Simulated', 'simulated'));
const MODELLED = chipOf('Modelled', 'modelled');

type Job = McJob & { results: McResults };
const withResults = (j: McJob): j is Job => !!j.results;

function inputsTable(job: McJob, title = 'Input ranges'): Block {
  return {
    t: 'table', title, chips: [chipOf(`Digest ${digestShort(job.results?.runs_digest)}`, 'info')],
    columns: [{ label: 'Input', w: 3 }, { label: 'Shape', w: 2 }, { label: 'Low', align: 'r', w: 1.4 }, { label: 'Mode', align: 'r', w: 1.4 }, { label: 'High', align: 'r', w: 1.4 }, { label: 'Source', w: 1.6 }],
    rows: job.inputs.map((i) => [inputLabel(i.name), i.dist.charAt(0).toUpperCase() + i.dist.slice(1), i.dist === 'fixed' ? '—' : fmt1(i.low), i.dist === 'uniform' ? '—' : fmt1(i.mode), i.dist === 'fixed' ? '—' : fmt1(i.high), i.source === 'request' ? 'Request' : 'Default']),
  };
}

function metricsTable(r: McResults): Block {
  const rows: TableCell[][] = metricKeys(r).map((k) => {
    const m = r.metrics[k];
    return [`${metricLabel(k)} (${m.unit})`, fmt(m.p10, m.unit), fmt(m.p50, m.unit), fmt(m.p90, m.unit), fmt(m.mean, m.unit), `${fmt(m.min, m.unit)} to ${fmt(m.max, m.unit)}`];
  });
  for (const n of r.metrics_not_defined ?? []) rows.push([metricLabel(n.metric), { text: 'Not served', chip: 'notserved' }, '', '', '', '']);
  return {
    t: 'table', title: 'Added delay and chain size', chips: [MODELLED],
    columns: [{ label: 'Metric', w: 3.4 }, { label: 'P10', align: 'r', w: 1 }, { label: 'P50', align: 'r', w: 1 }, { label: 'P90', align: 'r', w: 1 }, { label: 'Mean', align: 'r', w: 1 }, { label: 'Range', align: 'r', w: 2 }],
    rows,
  };
}

const header = (job: McJob, nameOf: (id: string) => string, run: RunRec): Array<[string, string]> => {
  const q = job.request;
  const d = q.disruption;
  const loss = d.kind !== 'closure' && d.capacity_loss !== undefined ? `${Math.round(d.capacity_loss * 100)}%` : null;
  return [
    ['Scenario', `${nameOf(d.node_id)}, ${d.kind.replace(/_/g, ' ')}${loss ? `, ${loss} capacity loss` : ''}, ${fmtInt(d.duration_d)} d`],
    ['Engine', job.engine?.engine_label ?? `Rev ${q.engine_rev}${q.profile ? ` ${q.profile}` : ''}`],
    ['Simulations', fmtInt(q.n)],
    ['Seed', seedWord(q.seed)],
    ['Run id', job.job_id],
    ['Runs digest', job.results?.runs_digest ?? 'Not served'],
    ['Source', run.source === 'recorded' ? 'Recorded run (stored copy)' : 'Simulation service'],
    ['Samples', run.samples ? `${fmtInt(run.samples.length)} loaded` : 'Not served'],
  ];
};

/** One finished run as a report. */
export async function mcRunReport(run: RunRec, nameOf: (id: string) => string): Promise<ReportDoc | null> {
  const job = run.job;
  if (!withResults(job)) return null;
  const r = job.results;
  const keys = metricKeys(r);
  const primary = r.metrics.worst_added_transit_d ? 'worst_added_transit_d' : keys[0];
  const blocks: Block[] = [];

  /* bands */
  blocks.push({ t: 'heading', level: 1, text: 'Bands', chips: [MODELLED] });
  blocks.push(metricsTable(r));
  const places = topPlaces(r.series, 6);
  const unit = r.series.unit ?? 'd';
  if (places.length) {
    blocks.push({ t: 'heading', level: 2, text: 'Added delay over time, per place', chips: [chipOf('P10 to P90', 'info')] });
    const figs = places.map((p) => fig(`Added delay at ${p.name}`, buildFan({ t: r.series.t_days, place: p, unit }, HALF, 176), { chips: [MODELLED] }));
    for (let i = 0; i < figs.length; i += 2) blocks.push({ t: 'figures', items: figs.slice(i, i + 2) });
    blocks.push({ t: 'note', text: 'Band is P10 to P90 · Line is P50 · Day 0 starts the disruption' });
  } else blocks.push({ t: 'chips', chips: [chipOf('Not served', 'notserved')] });
  blocks.push({
    t: 'table', title: 'Added delay by place', chips: [MODELLED],
    columns: [{ label: 'Place', w: 3 }, { label: 'P10 d', align: 'r' }, { label: 'P50 d', align: 'r' }, { label: 'P90 d', align: 'r' }, { label: 'Simulations affected', align: 'r', w: 2 }],
    rows: r.per_place.slice(0, 12).map((p) => [p.name, fmt1(p.added_delay_d.p10), fmt1(p.added_delay_d.p50), fmt1(p.added_delay_d.p90), finite(p.affected_k) ? `${fmtInt(p.affected_k)} of ${fmtInt(r.n)}` : fmtPct(p.affected_share)]),
  });

  /* distribution */
  const m0 = r.metrics[primary] as MetricStats | undefined;
  if (m0) {
    blocks.push({ t: 'heading', level: 1, text: 'Distribution', chips: [MODELLED] });
    const range = thresholdRange(m0);
    const thr = clampThreshold(defaultThreshold(m0), range);
    const values = run.samples ? run.samples.map((s) => s.outputs[primary]).filter(finite) : null;
    const share = thresholdShare(m0, thr, values);
    const u = m0.unit;
    const markers: Marker[] = [
      { label: `P10 ${fmt(m0.p10, u)}`, value: m0.p10, color: COLOR_MARK, dash: '3 3', kind: 'pct' },
      { label: `P50 ${fmt(m0.p50, u)}`, value: m0.p50, color: COLOR_MARK, kind: 'pct' },
      { label: `P90 ${fmt(m0.p90, u)}`, value: m0.p90, color: COLOR_MARK, dash: '3 3', kind: 'pct' },
    ];
    blocks.push({ t: 'tiles', tiles: [
      { label: `Threshold, ${metricLabel(primary).toLowerCase()}`, value: withUnit(thr, u) },
      { label: 'Simulations above the threshold', value: kOfN(share.k, share.n), sub: fmtPct(share.share), chip: share.source === 'histogram' ? chipOf('Interpolated', 'info') : undefined },
    ] });
    blocks.push({ t: 'figures', items: [
      fig(`${metricLabel(primary)}, simulations`, buildHistogram({ h: m0.histogram, unit: u, markers, threshold: thr, cumulative: false }, HALF, 220), { chips: [MODELLED], caption: `Marked threshold ${withUnit(thr, u)}: ${kOfN(share.k, share.n)} above.` }),
      fig(`${metricLabel(primary)}, share above`, buildHistogram({ h: m0.histogram, unit: u, markers, threshold: thr, cumulative: true }, HALF, 220), { chips: [MODELLED] }),
    ] });
    if (m0.exceedance.length) {
      blocks.push({
        t: 'table', title: 'Share of simulations above a value', chips: [MODELLED],
        columns: [{ label: `Above (${u})`, align: 'r' }, { label: 'Simulations', align: 'r' }, { label: 'Share', align: 'r' }],
        rows: m0.exceedance.map((e) => [withUnit(e.threshold, u), `${fmtInt(e.k)} of ${fmtInt(e.n)}`, fmtPct(e.share)]),
      });
    }
    /* a metric that takes one value in every simulation has no distribution to draw */
    const others = keys.filter((k) => k !== primary && r.metrics[k]?.histogram?.counts?.length && r.metrics[k].max > r.metrics[k].min);
    const oFigs = others.map((k) => {
      const m = r.metrics[k];
      return fig(`${metricLabel(k)}, simulations`, buildHistogram({ h: m.histogram, unit: m.unit, markers: [
        { label: `P50 ${fmt(m.p50, m.unit)}`, value: m.p50, color: COLOR_MARK, kind: 'pct' }], threshold: null, cumulative: false }, HALF, 190), { chips: [MODELLED] });
    });
    for (let i = 0; i < oFigs.length; i += 2) blocks.push({ t: 'figures', items: oFigs.slice(i, i + 2) });
  }

  /* drivers */
  blocks.push({ t: 'heading', level: 1, text: 'Drivers', chips: [MODELLED] });
  if (r.sensitivity.length) blocks.push(fig('Sensitivity of worst added transit', buildTornado(r.sensitivity, r.metrics.worst_added_transit_d?.unit ?? 'd', W), { chips: [MODELLED], caption: 'Swing: top tenth of input minus bottom tenth · Rho is rank correlation' }));
  const conv = (key: 'p50' | 'p90') => r.convergence.map((c) => ({ n: c.n, v: c[key] }));
  const cu = r.metrics.worst_added_transit_d?.unit ?? 'd';
  const step = (k: 'p50' | 'p90') => { const c = lastStepChange(r.convergence, k); return c === null ? undefined : `Last step ${c >= 0 ? '+' : '−'}${(Math.abs(c) * 100).toFixed(1)}%`; };
  if (r.convergence.length > 1) {
    blocks.push({ t: 'figures', items: [
      fig('Convergence, P50', buildConvergence(conv('p50'), cu, HALF, 90), { chips: [MODELLED], caption: step('p50') }),
      fig('Convergence, P90', buildConvergence(conv('p90'), cu, HALF, 90), { chips: [MODELLED], caption: step('p90') }),
    ] });
  }
  const dig = await checkDigest(run.samples, r.n, r.runs_digest).catch(() => 'unchecked' as const);
  blocks.push(inputsTable(job));
  blocks.push({ t: 'chips', chips: [chipOf(`Digest ${digestShort(r.runs_digest)}`, 'info'), ...(dig === 'match' ? [chipOf('Digest matches', 'sourced')] : dig === 'differs' ? [chipOf('Digest differs', 'flag')] : [])] });

  /* samples */
  if (run.samples?.length) {
    const names = job.inputs.filter((i) => i.dist !== 'fixed').map((i) => i.name);
    blocks.push({
      t: 'table', title: 'Simulations', chips: [chipOf(`${Math.min(12, run.samples.length)} of ${fmtInt(run.samples.length)} shown`, 'info'), sourceChip(run)],
      columns: [{ label: 'Simulation', align: 'r' }, ...names.map((n) => ({ label: inputLabel(n), align: 'r' as const })), { label: 'Worst added transit d', align: 'r' }],
      rows: run.samples.slice(0, 12).map((s) => [s.i + 1, ...names.map((n) => fmt1(s.inputs[n])), fmt1(s.outputs.worst_added_transit_d)]),
    });
  }

  blocks.push({ t: 'heading', level: 1, text: 'Provenance' });
  blocks.push({ t: 'kv', mono: true, rows: [
    ['Run id', job.job_id], ['Runs digest', r.runs_digest], ['Request', requestLine(job, nameOf)],
    ['Source', run.source === 'recorded' ? 'Recorded run, stored copy' : 'Simulation service'],
  ] });

  return {
    id: 'montecarlo:run', title: `Monte Carlo run: ${runTitle(run, nameOf)}`, windowTitle: 'Monte Carlo', orientation: 'portrait',
    label: run.source === 'recorded' ? 'Recorded' : 'Simulated', chips: [MODELLED, sourceChip(run), chipOf(`n ${fmtInt(job.request.n)}`, 'info'), chipOf(`Seed ${seedWord(job.request.seed)}`, 'info')],
    source: run.source === 'recorded' ? 'Recorded Monte Carlo run' : 'Seeded input-uncertainty simulation', runId: job.job_id, hash: r.runs_digest,
    labelText: job.label || MC_LABEL, meta: header(job, nameOf, run), blocks,
  };
}

/** Run A against run B. `doc` is the served comparison, when there is one. */
export function mcCompareReport(a: RunRec, b: RunRec, doc: CompareDoc | null, nameOf: (id: string) => string): ReportDoc | null {
  const ja = a.job; const jb = b.job;
  if (!withResults(ja) || !withResults(jb)) return null;
  const keys = sharedMetrics(ja, jb, METRIC_ORDER);
  if (!keys.length) return null;
  const key = keys.includes('worst_added_transit_d') ? 'worst_added_transit_d' : keys[0];
  const served = docFits(doc, ja, jb) ? doc : null;
  const ma = ja.results.metrics[key]; const mb = jb.results.metrics[key];
  const unit = ma.unit;
  const draws = ja.request.n === jb.request.n && ja.request.seed === jb.request.seed;
  const nameA = runTitle(a, nameOf); const nameB = runTitle(b, nameOf);
  const recorded = a.source === 'recorded' || b.source === 'recorded';
  const blocks: Block[] = [];

  blocks.push({ t: 'heading', level: 1, text: 'Side by side', chips: [MODELLED] });
  blocks.push({
    t: 'table', title: 'Added delay and chain size, A and B', chips: [MODELLED],
    columns: [{ label: 'Metric', w: 2.6 }, { label: 'Run' , w: 0.8 }, { label: 'P10', align: 'r' }, { label: 'P50', align: 'r' }, { label: 'P90', align: 'r' }, { label: 'Mean', align: 'r' }, { label: 'P50 vs A', align: 'r', w: 1.2 }, { label: 'P90 vs A', align: 'r', w: 1.2 }],
    rows: keys.flatMap((k) => {
      const x = ja.results.metrics[k]; const y = jb.results.metrics[k]; const u = x.unit;
      return [
        [`${metricLabel(k)} (${u})`, 'A', fmt(x.p10, u), fmt(x.p50, u), fmt(x.p90, u), fmt(x.mean, u), '—', '—'],
        ['', 'B', fmt(y.p10, u), fmt(y.p50, u), fmt(y.p90, u), fmt(y.mean, u), fmtSigned(y.p50 - x.p50, u), fmtSigned(y.p90 - x.p90, u)],
      ] as TableCell[][];
    }),
  });

  const hist = metricHist(key, a, b, served);
  const overlap = hist ? overlapCoefficient(hist.a, hist.b) : null;
  const row = deltaRow(key, ma, mb);
  const ci = served?.metrics[key]?.shift_ci ?? null;
  const paired = draws ? pairedFor(key, a, b, 0) : null;
  blocks.push({ t: 'heading', level: 1, text: 'B against A', chips: [chipOf(draws ? 'Shared draws' : 'Different draws', draws ? 'sourced' : 'flag')] });
  blocks.push({ t: 'tiles', tiles: [
    { label: `Shift in P50, ${metricLabel(key).toLowerCase()}`, value: `${fmtSigned(row.d50, unit)} ${unit === 'd' ? 'd' : unit}` },
    { label: 'Shift in P90', value: `${fmtSigned(row.d90, unit)} ${unit === 'd' ? 'd' : unit}` },
    { label: 'Overlap of distributions', value: overlap === null ? '—' : fmtPct(overlap) },
    ...(ci ? [{ label: 'Shift interval', value: `${fmtSigned(ci[0], unit)} to ${fmtSigned(ci[1], unit)} ${unit === 'd' ? 'd' : unit}` }] : []),
  ] });
  if (paired) {
    blocks.push({ t: 'tiles', tiles: [{ label: 'Paired simulations', value: `B is higher in ${kOfN(paired.k, paired.n).replace('simulations', 'paired simulations')}`, sub: fmtPct(paired.share) }] });
  } else blocks.push({ t: 'chips', chips: [chipOf(draws ? 'Samples not served' : 'Not paired', 'notserved')] });

  if (hist) {
    blocks.push(fig(`${metricLabel(key)}, A and B`, buildCompareHistogram({ edges: hist.edges, a: hist.a, b: hist.b, unit, p50a: ma.p50, p50b: mb.p50, nameA, nameB }, W, 230), {
      chips: [MODELLED], caption: `A: ${nameA} · B: ${nameB} · Hatched where they overlap` }));
  }
  const fans = fanPair(ja, jb, served);
  const moving = (ps: typeof fans.places) => { const m = mostMoving(ps.map((p) => ({ place: p.place, p50: [...p.a.p50, ...p.b.p50] }))); return m?.place ?? null; };
  const lead = moving(fans.places);
  const ordered = [...fans.places].sort((p, q) => (p.place === lead ? -1 : q.place === lead ? 1 : 0)).slice(0, 4);
  const fanFigs = ordered.map((p) => fig(`Added delay at ${p.name}, A and B`, buildFan(
    { t: fans.t, place: { place: p.place, name: p.name, ...p.a }, unit: 'd' }, HALF, 176, { t: fans.t, place: { place: p.place, name: p.name, ...p.b }, unit: 'd' }), { chips: [MODELLED] }));
  for (let i = 0; i < fanFigs.length; i += 2) blocks.push({ t: 'figures', items: fanFigs.slice(i, i + 2) });
  const deltas = served?.per_place.length ? served.per_place : placeDeltas(ja.results.per_place, jb.results.per_place);
  if (deltas.length) blocks.push(fig('Difference by place', buildPlaceDeltas(deltas.map((d) => ({ place: d.place, name: d.name, d50: d.delta_p50, d90: d.delta_p90 })), 'd', W), { chips: [MODELLED], caption: 'B minus A: P50 light, P90 solid · Right means B is later' }));

  blocks.push({ t: 'heading', level: 1, text: 'Inputs and provenance' });
  blocks.push(inputsTable(ja, 'Input ranges, A'));
  blocks.push(inputsTable(jb, 'Input ranges, B'));
  blocks.push({ t: 'kv', mono: true, rows: [
    ['Run A', ja.job_id], ['Digest A', ja.results.runs_digest], ['Run B', jb.job_id], ['Digest B', jb.results.runs_digest],
    ['Request A', requestLine(ja, nameOf)], ['Request B', requestLine(jb, nameOf)],
  ] });

  return {
    id: 'montecarlo:compare', title: `Monte Carlo comparison: ${nameA} and ${nameB}`, windowTitle: 'Monte Carlo', orientation: 'portrait',
    label: recorded ? 'Recorded' : 'Simulated',
    chips: [MODELLED, recorded ? chipOf('Recorded', 'recorded') : chipOf('Simulated', 'simulated'), chipFromWord(draws ? 'SOURCED' : 'FLAG', draws ? 'Shared draws' : 'Different draws')],
    source: recorded ? 'Recorded Monte Carlo runs' : 'Seeded input-uncertainty simulations', runId: `${ja.job_id} and ${jb.job_id}`, hash: `${digestShort(ja.results.runs_digest)} ${digestShort(jb.results.runs_digest)}`,
    labelText: ja.label || MC_LABEL,
    meta: [['Run A', `${nameA} · ${runSub(a)}`], ['Run B', `${nameB} · ${runSub(b)}`], ['Metric compared', metricLabel(key)], ['Draws', draws ? 'Shared (same n and seed)' : 'Different'], ['Run id A', ja.job_id], ['Run id B', jb.job_id]],
    blocks,
  };
}
