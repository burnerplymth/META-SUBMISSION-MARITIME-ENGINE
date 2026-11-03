/* Run report of the Lane planner: the transit time range chart, the voyage timeline, the comparison and checks tables,
 * the named variants and the sources. Modelled; a plan with a simulated queue says Simulated. Landscape. */
import { figureFromNodes } from '../../exports/report/figure';
import { CONTENT_PX, chipFromWord, chipOf, type Block, type ReportChip, type ReportDoc, type TableCell } from '../../exports/report/model';
import { fmtD, fmtKm, fmtPct, fmtRange, fmtSigned, buildRangeChart, buildTimeline, chipFor, dPlus, hasLaneBasis, planTitle, stateChip, tableRows, variantWord, NOT_SERVED } from './logic';
import { provenanceOf } from './exportChart';
import type { PlanDoc } from './types';

const W = CONTENT_PX.landscape;
const chipTone = (word: string): ReportChip => chipFromWord(word);

export function laneReport(doc: PlanDoc): ReportDoc {
  const prov = provenanceOf(doc);
  const label = prov.chip === 'SIMULATED' ? 'Simulated' : 'Modelled';
  const rows = tableRows(doc);
  const hasQueue = !!doc.disruption;
  const blocks: Block[] = [];

  blocks.push({ t: 'heading', level: 1, text: 'Transit time range', chips: [chipOf(label, label === 'Simulated' ? 'simulated' : 'modelled'), chipOf('Range', 'info')] });
  const range = buildRangeChart(doc, W, null);
  blocks.push(figureFromNodes(`${planTitle(doc)}, transit days`, range, { chips: [chipOf(label, label === 'Simulated' ? 'simulated' : 'modelled')], caption: 'Low to high over named variants · Heavy mark: median · Dashed line: baseline' }));

  blocks.push({
    t: 'table', title: 'Options', chips: [chipOf(label, label === 'Simulated' ? 'simulated' : 'modelled')],
    columns: [
      { label: 'Option', w: 3 }, { label: 'Median (d)', align: 'r', w: 1 }, { label: 'Range (d)', align: 'r', w: 1.2 }, { label: 'Against baseline (d)', align: 'r', w: 1.3 },
      ...(hasQueue ? [{ label: 'Queue peak (d)', align: 'r' as const, w: 1.6 }, { label: 'Wait at arrival (d)', align: 'r' as const, w: 1.6 }] : []), { label: 'Length (km)', align: 'r', w: 1 }, { label: 'Basis', w: 1 },
    ],
    rows: rows.map((r): TableCell[] => [
      r.label, fmtD(r.median), fmtRange(r.range), r.vs == null ? '—' : fmtSigned(r.vs),
      ...(hasQueue ? [r.baseline ? '—' : r.queue ? `${fmtD(r.queue.median)} (${fmtRange(r.queue)})` : r.queueState === 'not_served' ? NOT_SERVED : '0.0',
        r.baseline ? '—' : r.arrival ? (r.arrival.high > 0 ? `${fmtD(r.arrival.median)} (${fmtRange(r.arrival)})` : '0.0') : NOT_SERVED] : []),
      fmtKm(r.km), { text: String(r.chip).charAt(0).toUpperCase() + String(r.chip).slice(1).toLowerCase(), chip: chipTone(String(r.chip)).tone },
    ]),
  });

  if (doc.routes.some((r) => r.timeline.length > 0)) {
    blocks.push({ t: 'heading', level: 1, text: 'Voyage', chips: [chipOf('Timeline', 'info'), ...(doc.disruption ? [chipOf(`Closure ${dPlus(0)} to ${dPlus(doc.disruption.end_d.median)}`, 'info')] : [])] });
    blocks.push(figureFromNodes(`${planTitle(doc)}, voyage`, buildTimeline(doc, W, null), { chips: [chipOf(label, label === 'Simulated' ? 'simulated' : 'modelled')], caption: 'Sailing legs and simulated queues · Diamond: median arrival' }));
  }

  if (doc.checks.length) {
    const pair = doc.checks.find((c) => c.id === 'cape_vs_suez');
    const floor = doc.checks.find((c) => c.id === 'great_circle_floor');
    const rowsC: TableCell[][] = [];
    if (pair) rowsC.push(['Cape vs Suez', pair.engine?.suez_km == null ? NOT_SERVED : fmtKm(pair.engine.suez_km), pair.engine?.cape_km == null ? NOT_SERVED : fmtKm(pair.engine.cape_km),
      pair.engine?.detour_km == null ? NOT_SERVED : fmtKm(pair.engine.detour_km), pair.engine?.cut_pct == null ? NOT_SERVED : fmtPct(pair.engine.cut_pct),
      pair.reference ? `${pair.reference.cut_pct.lo.toFixed(1)} to ${pair.reference.cut_pct.hi.toFixed(1)} %` : NOT_SERVED, { text: pair.state, chip: chipTone(stateChip(pair.state)).tone }]);
    if (floor) rowsC.push(['Great circle floor', floor.engine?.great_circle_km == null ? NOT_SERVED : fmtKm(floor.engine.great_circle_km), '', '', '', '—', { text: floor.state, chip: chipTone(stateChip(floor.state)).tone }]);
    blocks.push({ t: 'heading', level: 1, text: 'Distance checks', chips: [chipOf('Reference', 'sourced')] });
    blocks.push({ t: 'table', columns: [{ label: 'Check', w: 2 }, { label: 'Suez (km)', align: 'r' }, { label: 'Cape (km)', align: 'r' }, { label: 'Detour (km)', align: 'r' }, { label: 'Cut (%)', align: 'r' }, { label: 'Cited (%)', align: 'r' }, { label: 'State', w: 1.8 }], rows: rowsC });
  }

  blocks.push({ t: 'heading', level: 2, text: 'Named variants' });
  blocks.push({ t: 'chips', chips: [
    ...doc.variants.sail.map((v) => chipOf(`${variantWord(v.id)} ${v.speed_kn} kn`, 'info')), ...doc.variants.queue.map((v) => chipOf(variantWord(v.id), 'info')),
    ...(doc.time_basis.leg_days != null ? [chipOf(`Basis ${doc.time_basis.kind.replace(/_/g, ' ')} ${fmtD(doc.time_basis.leg_days)} d`, 'info')] : []), ...(hasLaneBasis(doc) ? [chipOf('Lane nautical miles', 'info')] : []),
  ] });
  if (doc.sources.length) blocks.push({ t: 'table', title: 'Sources', columns: [{ label: 'Source', w: 3 }, { label: 'State', w: 1.4 }], rows: doc.sources.map((s) => [s.label, { text: s.state, chip: chipTone(chipFor(s.state)).tone }]) });

  return {
    id: 'laneplanner:report', title: `Lane plan: ${planTitle(doc)}`, windowTitle: 'Lane planner', orientation: 'landscape', label,
    chips: [chipOf(label, label === 'Simulated' ? 'simulated' : 'modelled'), chipOf(doc.engine_label, 'info'), ...(doc.disruption ? [chipOf(`${doc.disruption.name} · ${doc.disruption.duration_d} d`, 'info')] : [])],
    source: prov.source ?? 'Lane planner', runId: doc.plan_id, hash: doc.journal.inputs_sha256 ?? null,
    labelText: 'Ranges over named variants · Modelled from lane distances',
    meta: [['Origin', doc.places.origin.name], ['Destination', doc.places.destination.name], ['Engine', doc.engine_label], ['Disruption', doc.disruption ? `${doc.disruption.name}, ${doc.disruption.kind.replace(/_/g, ' ')}, ${doc.disruption.duration_d} d` : 'None'],
      ['Plan', doc.plan_id], ['Inputs sha256', doc.journal.inputs_sha256 ?? 'Not served'], ['Map hash', doc.journal.graph_hash ?? 'Not served']],
    blocks,
  };
}
