/* Run report of "My shipments": summary tiles, the exposed shipments, added transit per lane as a chart, and the
 * Monte Carlo overlay with its caveat chips. Everything is Modelled; value in affected shipments is the listed value,
 * not a loss estimate. Landscape, because the table is wide. */
import type { BarSpec } from '../../charts/types';
import { figureFromSpec } from '../../exports/report/figure';
import { CONTENT_PX, chipFromWord, chipOf, type Block, type ReportChip, type ReportDoc, type TableCell } from '../../exports/report/model';
import { fmtD, fmtPct, fmtQty, fmtRangeD, fmtUsd, fmtUsdShort, mcView, MODE_WORD, QUALITY_WORD, sortRows, STATUS_CHIP, STATUS_WORD, DEFAULT_SORT, type Row } from './logic';
import type { EvalDoc } from './types';

const W = CONTENT_PX.landscape;
const MODELLED = chipOf('Modelled', 'modelled');
const MAX_ROWS = 400;

export interface ShipmentsReportInput { doc: EvalDoc; rows: readonly Row[]; sample: boolean; scenarioLabel: string }

const statusCell = (r: Row): TableCell => (r.evaluated ? { text: STATUS_WORD[r.status], chip: chipFromWord(STATUS_CHIP[r.status]).tone } : 'Pending');

/** Added transit per lane: low, median and high over the named variants, for the lanes with an added transit. */
export function laneSpec(doc: EvalDoc): BarSpec | null {
  const lanes = doc.by_lane.filter((l) => l.added_days && (l.status === 'delayed' || l.status === 'rerouted'))
    .sort((a, b) => (b.added_days?.median ?? 0) - (a.added_days?.median ?? 0)).slice(0, 10);
  if (!lanes.length) return null;
  return {
    id: 'shipments-lanes', type: 'bar', title: 'Added transit per lane', subtitle: 'Low, median and high over the named variants', mode: 'grouped', horizontal: true,
    provenance: { chip: 'MODELLED', source: doc.label, asOf: null, runId: null },
    categories: lanes.map((l) => ({ key: l.lane_key, label: `${l.origin_name} to ${l.destination_name}` })),
    series: [
      { key: 'low', label: 'Low', values: lanes.map((l) => l.added_days!.low) },
      { key: 'median', label: 'Median', values: lanes.map((l) => l.added_days!.median) },
      { key: 'high', label: 'High', values: lanes.map((l) => l.added_days!.high) },
    ],
    xLabel: null, yLabel: 'Added transit', xUnit: null, yUnit: 'd',
  };
}

export function shipmentsReport(inp: ShipmentsReportInput): ReportDoc {
  const { doc, rows } = inp;
  const s = doc.summary;
  const blocks: Block[] = [];
  blocks.push({ t: 'heading', level: 1, text: 'Summary', chips: [MODELLED] });
  blocks.push({ t: 'tiles', tiles: [
    { label: 'Shipments', value: String(s.n) },
    { label: 'Affected', value: String(s.n_affected), sub: s.share_affected == null ? undefined : `${fmtPct(s.share_affected)} of served` },
    { label: 'Added transit, median per shipment', value: s.added_days_median_range ? `${fmtD(s.added_days_median_range.low)} to ${fmtD(s.added_days_median_range.high)} d` : '—' },
    { label: 'Value in affected shipments', value: `${fmtUsdShort(s.value_in_affected_usd)} USD`, sub: `${fmtUsd(s.value_in_affected_usd)} USD listed`, chip: MODELLED },
    ...(s.n_not_served > 0 ? [{ label: 'Not served', value: String(s.n_not_served) }] : []),
    ...(s.n_unresolved > 0 ? [{ label: 'Unresolved', value: String(s.n_unresolved) }] : []),
  ] });
  if (s.most_exposed_places.length) {
    blocks.push({ t: 'table', title: 'Most exposed places', columns: [{ label: 'Place', w: 4 }, { label: 'Shipments', align: 'r', w: 1 }], rows: s.most_exposed_places.slice(0, 8).map((p) => [p.name, p.n]) });
  }

  const spec = laneSpec(doc);
  blocks.push({ t: 'heading', level: 1, text: 'Added transit per lane', chips: [MODELLED] });
  if (spec) blocks.push(figureFromSpec(spec, W, 50 + spec.categories.length * 26, { title: spec.title, chips: [MODELLED], caption: `${doc.scenario.label} · Low, median and high over variants` }));
  else blocks.push({ t: 'chips', chips: [chipOf('No lane with added transit', 'info')] });

  const shown = sortRows(rows, DEFAULT_SORT).slice(0, MAX_ROWS);
  blocks.push({ t: 'heading', level: 1, text: 'Exposed shipments', chips: [MODELLED] });
  blocks.push({
    t: 'table', chips: [chipOf(`${shown.length} of ${rows.length} shown`, 'info')],
    columns: [
      { label: 'Shipment', w: 1.2 }, { label: 'Lane', w: 3.4 }, { label: 'Mode', w: 0.8 }, { label: 'Quantity', align: 'r', w: 1.1 }, { label: 'Status', w: 1.3 },
      { label: 'Added transit d', align: 'r', w: 1.1 }, { label: 'Range d', align: 'r', w: 1.2 }, { label: 'Value USD', align: 'r', w: 1.3 }, { label: 'Value in affected USD', align: 'r', w: 1.4 }, { label: 'Notes', w: 2.6 },
    ],
    rows: shown.map((r) => {
      const hit = r.status === 'delayed' || r.status === 'rerouted';
      const notes = [r.route, ...r.chips, r.originQ && r.originQ !== 'exact' && r.originQ !== 'id' ? `Origin ${(QUALITY_WORD[r.originQ] ?? r.originQ).toLowerCase()}` : null,
        r.destQ && r.destQ !== 'exact' && r.destQ !== 'id' ? `Destination ${(QUALITY_WORD[r.destQ] ?? r.destQ).toLowerCase()}` : null, ...r.unresolved.map((u) => `${u.field} unresolved`)].filter(Boolean).join(', ');
      return [r.s.id, r.lane, MODE_WORD[r.s.mode] ?? r.s.mode, fmtQty(r.s), statusCell(r), r.added && hit ? fmtD(r.added.median) : r.status === 'unaffected' ? '0.0' : '—', r.added && hit ? fmtRangeD(r.added) : '—', fmtUsd(r.value), r.affectedValue == null ? '—' : fmtUsd(r.affectedValue), notes];
    }),
    note: 'Value is the listed value, not a loss · Added transit is the wait at arrival',
  });

  const lanes = doc.by_lane;
  const mv = mcView(doc.mc, (k) => { const l = lanes.find((x) => x.lane_key === k); return l ? `${l.origin_name} to ${l.destination_name}` : k; });
  if (mv.state !== 'none') {
    blocks.push({ t: 'heading', level: 1, text: 'Monte Carlo overlay', chips: mv.chips.map((c) => chipFromWord(c.chip, c.word)) });
    if (mv.rows.length) {
      blocks.push({ t: 'table', title: 'Added transit per lane, Monte Carlo', columns: [{ label: 'Lane', w: 4 }, { label: 'Added transit, d', align: 'r', w: 1.4 }, { label: 'Places', align: 'r', w: 1.6 }], rows: mv.rows.map((r) => [r.name, r.range, r.places]) });
    } else if (mv.state === 'served') blocks.push({ t: 'chips', chips: [chipOf('Lane overlay withheld', 'info')] });
    if (doc.mc?.job_id) blocks.push({ t: 'kv', mono: true, rows: [['Monte Carlo job', doc.mc.job_id], ...(doc.mc.n ? [['Simulations', String(doc.mc.n)] as [string, string]] : [])] });
  }

  const chips: ReportChip[] = [MODELLED, chipOf('Stored in this browser', 'info'), ...(inp.sample ? [chipOf('Sample (synthetic)', 'info')] : [])];
  return {
    id: 'shipments:report', title: `Shipment exposure: ${inp.scenarioLabel}`, windowTitle: 'My shipments', orientation: 'landscape', label: 'Modelled', chips,
    source: 'Exposure evaluation of the listed shipments', labelText: doc.label,
    meta: [['Disruption', doc.scenario.label], ['Shipments', String(doc.counts.shipments)], ['Unique lanes', String(doc.counts.unique_lanes)], ['Schema', doc.schema], ['Engine', doc.mc?.engine?.engine_rev != null ? `Rev ${doc.mc.engine.engine_rev}` : 'Named variants']].filter(([, v]) => v) as Array<[string, string]>,
    runId: doc.mc?.job_id ?? null, blocks,
  };
}
