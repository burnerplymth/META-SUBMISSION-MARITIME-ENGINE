/* Run report of Replay: the recorded episode in three columns on one time axis (what happened, what the model produced,
 * the simulation layer), the dated reports, the published misses first and the full comparison. Counts are per relation
 * beside a Descriptive chip; nothing here scores the model. Landscape, state Recorded. */
import { buildModel, buildObserved, buildSim, type ChartOut } from './chart';
import { figureFromNodes } from '../../exports/report/figure';
import { CONTENT_PX, chipOf, type Block, type ReportDoc, type TableCell } from '../../exports/report/model';
import type { ExportInput } from './exportCompare';
import { BASIS_LABEL, axisOf, fmtDate, fmtRange, h8, hashChips, methodLines, missesOf, safeText, signed, splitWord, STATUS_LABEL, summarize, type Counts } from './logic';
import type { Row, Status } from './types';

const W = CONTENT_PX.landscape;
const COL = Math.floor((W - 2 * 18) / 3);
const RECORDED = chipOf('Recorded', 'recorded');
const DESC = chipOf('Descriptive', 'descriptive');
const ORDER: Status[] = ['matched', 'not_flagged', 'not_observed', 'opposite', 'not_produced', 'outside', 'within', 'quiet', 'not_compared', 'ungraded'];

const fig = (title: string, c: ChartOut, chips = [RECORDED]) => figureFromNodes(title, { nodes: c.nodes, width: c.width, height: c.height }, { chips });

const rowCells = (r: Row): TableCell[] => [
  safeText(r.subject), BASIS_LABEL[r.basis], safeText(r.observed), safeText(r.model), r.difference ? `${signed(r.difference.value)} ${r.difference.unit}` : '', r.timingD == null ? '' : signed(r.timingD),
  r.magnitude ?? '', STATUS_LABEL[r.status], r.publisher ? safeText(r.publisher).slice(0, 48) : r.observedDate ? fmtDate(r.observedDate) : '',
];
const COLS = [{ label: 'Subject', w: 2.6 }, { label: 'Basis', w: 1.1 }, { label: 'Observed', w: 2.2 }, { label: 'Model', w: 2 }, { label: 'Difference', align: 'r' as const, w: 1.2 }, { label: 'Timing d', align: 'r' as const, w: 0.9 },
  { label: 'Magnitude', w: 1.5 }, { label: 'Relation', w: 1.4 }, { label: 'Source', w: 1.6 }];

export function replayReport(inp: ExportInput, counts: Counts = summarize(inp.rows)): ReportDoc {
  const { doc, engine, rows } = inp;
  const blocks: Block[] = [];
  /* the three drawings are rebuilt at the width of a column, so their type keeps its size on the page */
  const axis = axisOf(doc.window);
  const series = doc.observed.series[0];
  const observed = series ? buildObserved(doc, series, axis, COL, 230) : inp.observed;
  const model = inp.model && engine?.available ? buildModel(doc, inp.steps, inp.name, axis, COL, 230) : null;
  const simSeries = doc.simulation?.series[0];
  const sim = inp.sim && simSeries ? buildSim(doc, simSeries, axis, COL, 230) : null;
  const cols = [fig('What happened', observed, [chipOf('Reported', 'sourced'), RECORDED])];
  if (model) cols.push(fig('What the model produced', model, [chipOf('Modelled', 'modelled'), RECORDED]));
  if (sim) cols.push(fig('Simulation layer', sim, [chipOf('Simulated', 'simulated'), RECORDED]));

  blocks.push({ t: 'heading', level: 1, text: 'Comparison on one time axis', chips: [RECORDED, DESC] });
  blocks.push({ t: 'figures', items: cols });
  if (engine && !engine.available) blocks.push({ t: 'chips', chips: [chipOf(engine.reason === 'outside_core' ? 'Outside core places' : 'Not run', 'notserved')] });
  blocks.push({ t: 'note', text: 'Shaded band: stated window · Marks: dated reports · One axis for all three' });

  if (doc.observed.events.length) {
    blocks.push({
      t: 'table', title: 'Dated reports', chips: [chipOf('Reported', 'sourced')],
      columns: [{ label: '#', align: 'r', w: 0.4 }, { label: 'Date', w: 1.2 }, { label: 'Report', w: 4 }, { label: 'Publisher', w: 2 }, { label: 'Published', w: 1.2 }],
      rows: doc.observed.events.map((e, i) => [i + 1, fmtDate(e.date), safeText(e.label), safeText(e.publisher), e.published]),
    });
  }
  if (inp.steps.length) {
    blocks.push({
      t: 'table', title: 'Modelled steps', chips: [chipOf('Modelled', 'modelled')],
      columns: [{ label: 'Step', align: 'r', w: 0.5 }, { label: 'Place', w: 3 }, { label: 'Channel', w: 1.4 }, { label: 'Added days d', align: 'r', w: 1.2 }, { label: 'Date', w: 1.2 }, { label: 'Days after start', align: 'r', w: 1.2 }],
      rows: inp.steps.map((s, i) => [i + 1, inp.name(s.placeId), s.channel, s.addedD.toFixed(1), fmtDate(s.date), s.lagD.toFixed(1)]),
    });
  }

  const misses = missesOf(rows);
  const countChips = ORDER.filter((s) => counts[s] > 0).map((s) => chipOf(`${STATUS_LABEL[s]} ${counts[s]}`, 'info'));
  blocks.push({ t: 'heading', level: 1, text: 'Published misses', chips: [DESC] });
  blocks.push({ t: 'chips', chips: countChips });
  if (misses.length) blocks.push({ t: 'table', columns: COLS, rows: misses.map(rowCells) });
  else blocks.push({ t: 'chips', chips: [chipOf('No misses listed', 'info')] });

  blocks.push({ t: 'heading', level: 1, text: 'Comparison', chips: [chipOf(`${rows.length} rows`, 'info'), DESC] });
  blocks.push({ t: 'table', columns: COLS, rows: rows.map(rowCells) });

  const chips = hashChips(engine);
  const lines = methodLines(doc, engine);
  blocks.push({ t: 'heading', level: 1, text: 'Provenance' });
  blocks.push({ t: 'kv', mono: true, rows: [
    ['Episode', safeText(doc.title)], ['Engine', engine?.label ?? 'No engine'], ...chips.map((c): [string, string] => [c.label, c.full]),
    ...(engine?.chain ? [['Chain run', engine.chain.run_id], ['Stream hash', engine.chain.stream_hash]] as Array<[string, string]> : []),
    ...doc.provenance.files.slice(0, 8).map((f): [string, string] => [f.file.split('/').pop() ?? f.file, f.sha256]),
  ] });
  blocks.push({ t: 'para', text: lines.join(' '), muted: true });

  return {
    id: 'replay:report', title: `Replay: ${safeText(doc.title)}`, windowTitle: 'Replay', orientation: 'landscape', label: 'Recorded',
    chips: [RECORDED, DESC, ...(doc.status.split ? [chipOf(splitWord(doc.status.split) ?? '', 'info')] : []), ...(doc.status.not_blind ? [chipOf('Not blind', 'flag')] : [])],
    source: `Replay · ${safeText(doc.title)} · ${engine?.label ?? 'No engine'}`, runId: engine?.chain?.run_id ?? null, hash: engine?.chain?.stream_hash ?? engine?.config_hash ?? null, asOf: doc.window.end,
    labelText: 'Recorded comparison · Descriptive: counts, no score',
    meta: [['Episode', safeText(doc.title)], ['Dates', fmtRange(doc.date_range.start, doc.date_range.end)], ['Engine', engine?.label ?? 'No engine'],
      ['Preregistration', engine?.prereg ? h8(engine.prereg.sha256) : 'Not served'], ['Config', engine?.config_hash ? h8(engine.config_hash) : 'Not served'], ['Scorecard', engine?.scorecard ? h8(engine.scorecard.sha256) : 'Not served'],
      ...(doc.disruption ? [['Input', `${doc.disruption.kind.replace(/_/g, ' ')}, ${Math.round(doc.disruption.capacity_loss * 100)}% for ${doc.disruption.duration_d} d`] as [string, string]] : [])],
    blocks,
  };
}
