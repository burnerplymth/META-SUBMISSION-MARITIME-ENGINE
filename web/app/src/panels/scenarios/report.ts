/* Run report of the Scenario builder (timeline): the shock timeline drawn with the chart kit's type and axes, the shock
 * table with the book's verdicts, the combined delay by place marked Approximated, and the exact per-shock chain runs
 * (or the simulation windows). The combined table is the chain engine's approximation and says so. Landscape. */
import { AXIS, GRID, INK, INK2, INK3, SURFACE, hatchDef, text } from '../../charts/draw';
import { CHART_FS, clipLabel } from '../../charts/format';
import { linearScale, niceTicks } from '../../charts/scale';
import { h, round, type VNode } from '../../charts/vnode';
import { figureFromNodes } from '../../exports/report/figure';
import { CONTENT_PX, chipOf, type Block, type ReportDoc, type TableCell } from '../../exports/report/model';
import { effectiveLoss, KIND_LABEL } from './doc';
import { fmtD, plural, toPct } from './logic';
import { dateLabel, dayLabel, endOf, overlapSegments, spanOf, tickStep, ticks, viewDays } from './timeline';
import type { BuilderDoc, ChainRunDoc, RulesDoc, ShockVerdictRow, SimRunDoc, UiShock, ValidateDoc } from './types';

const W = CONTENT_PX.landscape;
const MODELLED = chipOf('Modelled', 'modelled');
const ROW = 26; const AXIS_H = 34; const LABEL_W = 190; const PAD_R = 24; const BAR = 15;

const KIND_FILL: Record<string, string> = {
  closure: 'var(--cs-2)', capacity_cut: 'var(--cs-4)', congestion: 'var(--cs-1)', strike: 'var(--cs-7)', weather: 'var(--cs-3)', grounding: 'var(--cs-8)', accident: 'var(--cs-5)', policy: 'var(--cs-6)', other: 'var(--text-3)',
};

/** The shock timeline as a standalone drawing: a row per shock on a day axis with the date beneath, the overlaps hatched, the span bracketed. */
export function buildScenarioTimeline(doc: BuilderDoc, nameOf: (id: string) => string, width = W): { nodes: VNode[]; width: number; height: number } {
  const shocks = doc.shocks;
  const days = viewDays(shocks, 14);
  const plotX = LABEL_W + 8; const plotW = Math.max(200, width - plotX - PAD_R);
  const px = plotW / days;
  const x = (d: number) => round(plotX + d * px);
  const height = AXIS_H + Math.max(1, shocks.length) * ROW + 10;
  const nodes: VNode[] = [h('defs', null, hatchDef('scb-ov'))];
  const step = tickStep(px, 56);
  for (const d of ticks(days, step)) {
    nodes.push(h('line', { x1: x(d), x2: x(d), y1: AXIS_H - 8, y2: height - 6, stroke: GRID, strokeWidth: 1, strokeOpacity: 0.7 }));
    nodes.push(text(x(d) + 3, 11, `Day ${d}`, { fill: INK2 }));
    nodes.push(text(x(d) + 3, 23, dateLabel(doc.startDate, d), { fill: INK3, size: 9 }));
  }
  nodes.push(h('line', { x1: plotX, x2: plotX + plotW, y1: AXIS_H - 8, y2: AXIS_H - 8, stroke: AXIS, strokeWidth: 1 }));
  const span = spanOf(shocks);
  if (shocks.length && span.totalSpanD > 0) {
    const y = AXIS_H - 4;
    nodes.push(h('line', { x1: x(span.firstStartD), x2: x(span.lastEndD), y1: y, y2: y, stroke: INK, strokeWidth: 1.2 }));
    nodes.push(h('line', { x1: x(span.firstStartD), x2: x(span.firstStartD), y1: y - 3, y2: y + 3, stroke: INK, strokeWidth: 1.2 }));
    nodes.push(h('line', { x1: x(span.lastEndD), x2: x(span.lastEndD), y1: y - 3, y2: y + 3, stroke: INK, strokeWidth: 1.2 }));
  }
  if (shocks.length === 0) nodes.push(text(8, AXIS_H + ROW / 2, 'No shocks', { fill: INK2, baseline: 'middle' }));
  shocks.forEach((s, i) => {
    const y0 = AXIS_H + i * ROW; const cy = y0 + ROW / 2;
    nodes.push(h('line', { x1: 0, x2: width, y1: y0 + ROW, y2: y0 + ROW, stroke: GRID, strokeWidth: 1, strokeOpacity: 0.5 }));
    nodes.push(text(4, cy - 5, clipLabel(nameOf(s.placeId), LABEL_W - 6, 10), { fill: INK, baseline: 'middle', size: 10, weight: 600 }));
    nodes.push(text(4, cy + 6, KIND_LABEL[s.kind], { fill: INK2, baseline: 'middle', size: 9 }));
    const w = Math.max(3, s.durationD * px);
    nodes.push(h('rect', { x: x(s.startD), y: cy - BAR / 2, width: round(w), height: BAR, rx: 2, fill: KIND_FILL[s.kind] ?? INK3, fillOpacity: 0.75, stroke: KIND_FILL[s.kind] ?? INK3, strokeWidth: 1 }));
    const lab = `${dayLabel(s.durationD)}${s.kind === 'closure' ? '' : `, ${toPct(effectiveLoss(s))}%`}`;
    if (w > lab.length * 5.6 + 8) nodes.push(text(x(s.startD) + 5, cy, lab, { fill: INK, baseline: 'middle', size: 9, weight: 600 }));
    else nodes.push(text(x(endOf(s)) + 5, cy, lab, { fill: INK, baseline: 'middle', size: 9 }));
  });
  const idx = new Map(shocks.map((s, i) => [s.id, i]));
  for (const o of overlapSegments(shocks)) for (const id of [o.a, o.b]) {
    const i = idx.get(id); if (i === undefined) continue;
    const cy = AXIS_H + i * ROW + ROW / 2;
    nodes.push(h('rect', { x: x(o.fromD), y: cy - BAR / 2, width: Math.max(2, round((o.toD - o.fromD) * px)), height: BAR, rx: 2, fill: 'url(#scb-ov)', stroke: INK, strokeWidth: 0.8 }));
  }
  nodes.unshift(h('rect', { x: 0, y: 0, width, height, fill: SURFACE }));
  void CHART_FS;
  return { nodes, width, height };
}

export interface ScenarioReportInput {
  doc: BuilderDoc; nameOf: (id: string) => string; validation: ValidateDoc | null; rules: RulesDoc | null;
  run: ChainRunDoc | SimRunDoc | null; engineRev: 1 | 2;
}

const verdictWord = (row: ShockVerdictRow | undefined, t: 'chain' | 'sim'): TableCell => (!row ? '—' : row.verdicts[t].ok ? { text: 'Accepted', chip: 'sourced' } : { text: 'Refused', chip: 'flag' });

export function scenarioReport(inp: ScenarioReportInput): ReportDoc {
  const { doc, nameOf, validation: val, run } = inp;
  const span = spanOf(doc.shocks);
  const overlaps = overlapSegments(doc.shocks);
  const rows = new Map<string, ShockVerdictRow>((val?.shocks ?? []).map((r) => [r.id, r]));
  const blocks: Block[] = [];

  blocks.push({ t: 'heading', level: 1, text: 'Shock timeline', chips: [MODELLED] });
  blocks.push({ t: 'tiles', tiles: [
    { label: 'Shocks', value: String(doc.shocks.length) }, { label: 'Total span', value: fmtD(span.totalSpanD), sub: `Day ${span.firstStartD} to day ${span.lastEndD}` },
    { label: 'Overlapping pairs', value: String(overlaps.length), sub: overlaps.filter((o) => o.samePlace).length ? `${overlaps.filter((o) => o.samePlace).length} at the same place` : undefined },
    { label: 'Start date', value: doc.startDate },
  ] });
  const tl = buildScenarioTimeline(doc, nameOf);
  blocks.push(figureFromNodes('Shocks on a day axis', tl, { chips: [MODELLED], caption: 'Bar spans one shock · Hatched: overlap · Bracket: total span' }));

  blocks.push({
    t: 'table', title: 'Shocks', chips: [chipOf(plural(doc.shocks.length, 'shock'), 'info')],
    columns: [{ label: '#', align: 'r', w: 0.5 }, { label: 'Place', w: 2.4 }, { label: 'Kind', w: 1.3 }, { label: 'Start', w: 1.6 }, { label: 'Duration d', align: 'r', w: 1 }, { label: 'End', w: 1.6 },
      { label: 'Capacity loss', align: 'r', w: 1.1 }, { label: 'Shape', w: 0.9 }, { label: 'Chain', w: 1.1 }, { label: 'Simulation', w: 1.1 }, { label: 'Note', w: 2 }],
    rows: doc.shocks.map((s: UiShock, i) => [i + 1, nameOf(s.placeId), KIND_LABEL[s.kind], `Day ${s.startD}, ${dateLabel(doc.startDate, s.startD)}`, s.durationD, `Day ${endOf(s)}, ${dateLabel(doc.startDate, endOf(s))}`, `${toPct(effectiveLoss(s))}%`, s.shape, verdictWord(rows.get(s.id), 'chain'), verdictWord(rows.get(s.id), 'sim'), s.note || '']),
  });
  if (overlaps.length) {
    blocks.push({ t: 'table', title: 'Overlaps', columns: [{ label: 'Shock A', w: 2 }, { label: 'Shock B', w: 2 }, { label: 'From day', align: 'r' }, { label: 'To day', align: 'r' }, { label: 'Same place', w: 1 }],
      rows: overlaps.map((o) => [o.a, o.b, o.fromD, o.toD, o.samePlace ? 'Yes' : 'No']) });
  }

  if (run && run.target === 'chain') {
    const c = run;
    blocks.push({ t: 'heading', level: 1, text: 'Chain results', chips: [MODELLED, chipOf(`Engine rev ${c.engine_rev}`, 'info')] });
    blocks.push({
      t: 'table', title: 'One exact run per shock', chips: [chipOf('Exact', 'sourced')],
      columns: [{ label: 'Order', align: 'r', w: 0.7 }, { label: 'Place', w: 2.4 }, { label: 'Kind', w: 1.3 }, { label: 'Start', w: 1.2 }, { label: 'Duration', align: 'r', w: 1 }, { label: 'Events', align: 'r', w: 0.9 }, { label: 'Run', w: 2.2, mono: true }, { label: 'Stream hash', w: 1.8, mono: true }],
      rows: c.runs.map((r) => [r.order, r.place_name ?? nameOf(r.place_id), r.kind.replace(/_/g, ' '), `Day ${r.start_offset_d}`, `${r.duration_d} d`, r.event_count ?? '—', r.run_id, r.stream_hash.slice(0, 16)]),
    });
    blocks.push({
      t: 'table', title: 'Combined delay by place', chips: [chipOf('Approximated', 'approx')],
      columns: [{ label: 'Place', w: 3 }, { label: 'Largest d', align: 'r', w: 1 }, { label: 'Sum d', align: 'r', w: 1 }, { label: 'Hit by', align: 'r', w: 0.8 }, { label: 'Shocks', w: 3 }],
      rows: c.combined.places.slice(0, 20).map((p) => [p.name ?? nameOf(p.place_id), fmtD(p.max_delay_d), fmtD(p.sum_delay_d), p.hit_by.length, p.hit_by.join(', ')]),
      note: `${plural(c.combined.places_reached, 'place')} reached. Sum counts overlapping shocks only. ${c.combined.label}`,
    });
    blocks.push({ t: 'note', tone: 'approx', text: 'Approximated · Delays add only where shocks overlap' });
  } else if (run && run.target === 'sim') {
    blocks.push({ t: 'heading', level: 1, text: 'Simulation windows', chips: [chipOf('Simulated', 'simulated')] });
    blocks.push({
      t: 'table', columns: [{ label: 'Window', w: 0.8 }, { label: 'Place', w: 2.4 }, { label: 'Starts', w: 1.6 }, { label: 'Duration', align: 'r', w: 1 }, { label: 'Capacity loss', align: 'r', w: 1.1 }],
      rows: run.windows.map((w) => [w.sim_id, nameOf(w.place_id), w.starts_at.slice(0, 10), `${w.duration_d} d`, `${Math.round(w.capacity_loss * 100)}%`]),
      note: `${plural(run.ticks, 'tick')} of 1 day, seed ${run.seed}, no weather feed. Simulation run ${run.sim_run_id}.`,
    });
  } else blocks.push({ t: 'note', text: 'No run yet · Scenario as entered' });

  const runId = run ? (run.target === 'sim' ? run.sim_run_id : run.runs[0]?.run_id ?? null) : null;
  return {
    id: 'scenariobuilder:report', title: `Scenario timeline: ${doc.name.trim() || 'Untitled scenario'}`, windowTitle: 'Scenario timeline', orientation: 'landscape', label: run?.target === 'sim' ? 'Simulated' : 'Modelled',
    chips: [MODELLED, ...(run?.target === 'sim' ? [chipOf('Simulated', 'simulated')] : []), ...(run?.target === 'chain' ? [chipOf('Approximated', 'approx')] : [])],
    source: run ? (run.target === 'sim' ? 'Scenario run on the simulator' : 'Scenario run on the causal chain engine') : 'Scenario as entered', runId: run?.target === 'chain' ? null : runId, simRunId: run?.target === 'sim' ? run.sim_run_id : null,
    hash: run?.target === 'chain' ? run.runs[0]?.stream_hash ?? null : null, asOf: doc.startDate,
    meta: [['Scenario', doc.name.trim() || 'Untitled scenario'], ['Start date', doc.startDate], ['Shocks', String(doc.shocks.length)], ['Total span', fmtD(span.totalSpanD)], ['Engine revision', `Rev ${inp.engineRev}`],
      ...(run?.target === 'chain' ? [['Chain runs', String(run.runs.length)] as [string, string]] : [])],
    blocks,
  };
}
