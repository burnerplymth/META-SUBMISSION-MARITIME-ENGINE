/* Run report of the causal chain: the diagram (places in steps, legs between them), the cascade timeline and the table of
 * steps. Modelled: every step is the engine's, replayed from the stored run. The diagram is drawn here with the chart
 * kit's type and channel colours (the window's interactive diagram is HTML and cannot be printed as vectors). Landscape. */
import { CHANNEL_COLOR, INK, INK2, INK3, SURFACE, seriesVar, text } from '../../charts/draw';
import { clipLabel } from '../../charts/format';
import { h, round, type VNode } from '../../charts/vnode';
import { labelWord } from '../../copy';
import { chainCascadeSpec } from '../../exports/adapters';
import { figureFromNodes, figureFromSpec } from '../../exports/report/figure';
import { CONTENT_PX, chipOf, type Block, type ReportDoc } from '../../exports/report/model';
import { nodeName } from '../../seed';
import type { Chain } from '../../types';

const W = CONTENT_PX.landscape;
const MODELLED = chipOf('Modelled', 'modelled');
const BOX_H = 40; const ROW_GAP = 14;

const colorOf = (channel: string, i: number): string => CHANNEL_COLOR[channel.toLowerCase()] ?? seriesVar(i);

/** The chain as a diagram: one column per generation (distance from the disruption), one box per step, an arrow per leg. */
export function buildChainDiagram(chain: Chain, width = W): { nodes: VNode[]; width: number; height: number } {
  const evs = [...chain.events].sort((a, b) => a.seq - b.seq);
  const depth = new Map<number, number>();
  evs.forEach((e) => depth.set(e.seq, e.parent_id === null ? 0 : (depth.get(e.parent_id) ?? 0) + 1));
  const cols = Math.max(1, ...evs.map((e) => (depth.get(e.seq) ?? 0) + 1));
  const colW = Math.min(230, Math.floor((width - 12) / cols));
  const boxW = Math.max(110, colW - 42);
  const rowIn = new Map<number, number>();
  const pos = new Map<number, { x: number; y: number }>();
  for (const e of evs) {
    const d = depth.get(e.seq) ?? 0; const r = rowIn.get(d) ?? 0; rowIn.set(d, r + 1);
    pos.set(e.seq, { x: 6 + d * colW, y: 8 + r * (BOX_H + ROW_GAP) });
  }
  const rows = Math.max(1, ...rowIn.values());
  const height = 16 + rows * (BOX_H + ROW_GAP);
  const channels = [...new Set(evs.map((e) => e.channel))];
  const nodes: VNode[] = [h('rect', { x: 0, y: 0, width, height, fill: SURFACE })];
  for (const e of evs) {
    if (e.parent_id === null) continue;
    const a = pos.get(e.parent_id); const b = pos.get(e.seq);
    if (!a || !b) continue;
    const x1 = a.x + boxW; const y1 = a.y + BOX_H / 2; const x2 = b.x; const y2 = b.y + BOX_H / 2; const mx = x1 + (x2 - x1) / 2;
    const c = colorOf(e.channel, channels.indexOf(e.channel));
    nodes.push(h('path', { d: `M${round(x1)} ${round(y1)}H${round(mx)}V${round(y2)}H${round(x2 - 5)}`, fill: 'none', stroke: c, strokeWidth: 1.6 }));
    nodes.push(h('path', { d: `M${round(x2)} ${round(y2)}l-6 -3.5v7z`, fill: c }));
  }
  for (const e of evs) {
    const p = pos.get(e.seq) as { x: number; y: number };
    const c = colorOf(e.channel, channels.indexOf(e.channel));
    nodes.push(h('rect', { x: p.x, y: p.y, width: boxW, height: BOX_H, rx: 3, fill: SURFACE, stroke: INK3, strokeWidth: 1 }));
    nodes.push(h('rect', { x: p.x, y: p.y, width: 4, height: BOX_H, fill: c }));
    nodes.push(text(p.x + 10, p.y + 12, `${e.seq + 1}  ${clipLabel(nodeName(e.node_id), boxW - 40, 11)}`, { size: 11, fill: INK, weight: 600, baseline: 'middle' }));
    nodes.push(text(p.x + 10, p.y + 26, `${labelWord(e.channel)} · +${e.delay_d.toFixed(1)} d`, { size: 10, fill: INK2, baseline: 'middle' }));
    nodes.push(text(p.x + boxW - 6, p.y + 12, `${Math.round(e.magnitude * 100)}%`, { size: 9, fill: INK3, anchor: 'end', baseline: 'middle' }));
  }
  return { nodes, width, height };
}

export interface ChainReportInput { chain: Chain; label: string | null; mode: 'LIVE' | 'CANNED' | 'SCENARIO' | null; spec: { node_id: string; kind: string; capacity_loss: number; duration_d: number } | null; asOf: string | null }

export function chainReport(inp: ChainReportInput): ReportDoc | null {
  const { chain } = inp;
  if (!chain.events.length) return null;
  const evs = [...chain.events].sort((a, b) => a.seq - b.seq);
  const cum = new Map<number, number>();
  let total = 0;
  for (const e of evs) { const c = e.delay_d + (e.parent_id !== null ? (cum.get(e.parent_id) ?? 0) : 0); cum.set(e.seq, c); total = Math.max(total, c); }
  const places = new Set(evs.map((e) => e.node_id)).size;
  const source = inp.mode === 'CANNED' ? 'Stored run record (causal chain)' : inp.mode === 'LIVE' ? 'Causal chain engine, seeded from a feed event' : 'Causal chain engine, stated scenario';
  const blocks: Block[] = [];

  blocks.push({ t: 'heading', level: 1, text: 'Diagram', chips: [MODELLED] });
  blocks.push({ t: 'tiles', tiles: [
    { label: 'Steps', value: String(evs.length) }, { label: 'Places reached', value: String(places) }, { label: 'Longest path', value: `+${total.toFixed(1)} d`, sub: 'Cumulative delay, root to last step' },
    { label: 'Stream hash', value: chain.stream_hash.slice(0, 12), sub: 'Replays byte for byte' },
  ] });
  blocks.push(figureFromNodes('Causal chain, step by step', buildChainDiagram(chain), { chips: [MODELLED], caption: 'Steps by place and channel · Arrows are legs' }));

  const spec = chainCascadeSpec(chain, inp.asOf);
  blocks.push({ t: 'heading', level: 1, text: 'Timeline', chips: [MODELLED] });
  if (spec) blocks.push(figureFromSpec(spec, W, 320, { title: 'Causal chain timeline', chips: [MODELLED], caption: 'Arrival time per step · Dot size is magnitude' }));

  blocks.push({ t: 'heading', level: 1, text: 'Steps', chips: [MODELLED] });
  blocks.push({
    t: 'table', columns: [{ label: '#', align: 'r', w: 0.4 }, { label: 'Place', w: 2.2 }, { label: 'Channel', w: 1.2 }, { label: 'Delay (d)', align: 'r', w: 0.9 }, { label: 'Magnitude', align: 'r', w: 0.9 }, { label: 'Lands (UTC)', w: 1.5 }, { label: 'Mechanism', w: 4 }],
    rows: evs.map((e) => [e.seq + 1, nodeName(e.node_id), labelWord(e.channel), e.delay_d.toFixed(1), e.magnitude.toFixed(2), e.at.replace('T', ' ').slice(0, 16), e.mechanism]),
  });
  const legs = chain.edges.filter((e) => e.from_seq !== null);
  if (legs.length) {
    blocks.push({ t: 'table', title: 'Legs', columns: [{ label: 'From step', align: 'r' }, { label: 'To step', align: 'r' }, { label: 'From place', w: 2 }, { label: 'To place', w: 2 }, { label: 'Channel', w: 1.4 }],
      rows: legs.map((e) => [(e.from_seq ?? 0) + 1, e.to_seq + 1, nodeName(e.from_node), nodeName(e.to_node), labelWord(e.channel)]) });
  }
  blocks.push({ t: 'heading', level: 1, text: 'Provenance' });
  blocks.push({ t: 'kv', mono: true, rows: [['Run id', chain.run_id], ['Stream hash', chain.stream_hash], ['Source', source], ['Status', chain.status], ['Events', String(chain.event_count)]] });

  const d = inp.spec;
  return {
    id: 'chain:report', title: `Causal chain: ${nodeName(evs[0].node_id)}`, windowTitle: 'Causal chain', orientation: 'landscape', label: 'Modelled',
    chips: [MODELLED, ...(inp.mode === 'CANNED' ? [chipOf('Recorded', 'recorded')] : [])], source, runId: chain.run_id, hash: chain.stream_hash, asOf: inp.asOf,
    labelText: 'Modelled from stated inputs · Replayed from the stored run',
    meta: [['Run id', chain.run_id], ['Source', source], ['Steps', String(evs.length)], ['Places reached', String(places)],
      ...(d ? [['Disruption', `${nodeName(d.node_id)}, ${d.kind.replace(/_/g, ' ')}, ${Math.round(d.capacity_loss * 100)}% for ${d.duration_d} d`] as [string, string]] : []), ...(inp.label ? [['Run label', inp.label] as [string, string]] : [])],
    blocks,
  };
}
