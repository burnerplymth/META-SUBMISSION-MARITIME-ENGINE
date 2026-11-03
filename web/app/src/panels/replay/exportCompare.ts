/* Export of the comparison through the shared chart-export machinery (charts/serialize, charts/exportDom, ExportMenu):
 * the SVG is the three drawings on one axis with an event key and a provenance caption that names the episode, the
 * engine and the three hashes; the CSV is the comparison table with the same hashes on every row; the XLSX is written by
 * the server from the same tables. Nothing here reads the network except the XLSX post. */
import { INK, INK2, SURFACE, text } from '../../charts/draw';
import { clipLabel } from '../../charts/format';
import { liveLookup, svgToPng, type ExportKind, type ExportResult, type ExportSkin } from '../../charts/exportDom';
import { exportFilename, slug, tableToCsv, tableToCsvFile, tableToTsv, wrapCaption } from '../../charts/serialize';
import type { Provenance } from '../../charts/types';
import { h, toSvgString, translate, type VNode } from '../../charts/vnode';
import { call, saveBlob } from '../reports/client';
import type { ChartOut } from './chart';
import {
  COMPARISON_COLUMNS, EVENT_COLUMNS, STEP_COLUMNS, comparisonRows, eventRows, fmtDate, h8, safeText, splitWord, stepRows,
  type Labels, type Step,
} from './logic';
import type { EngineDoc, EpisodeDoc, Row } from './types';

const PAD = 12;
const GAP = 8;

export interface ExportInput {
  doc: EpisodeDoc; engine: EngineDoc | null; rows: Row[]; steps: Step[]; name: (id: string) => string;
  observed: ChartOut; model: ChartOut | null; sim: ChartOut | null;
}

export const labelsOf = (doc: EpisodeDoc, engine: EngineDoc | null): Labels => ({
  episode: doc.title, engine: engine?.label ?? 'No engine', prereg: engine?.prereg?.sha256 ?? '', config: engine?.config_hash ?? '', scorecard: engine?.scorecard?.sha256 ?? '',
});

export function provenanceOf(doc: EpisodeDoc, engine: EngineDoc | null): Provenance {
  const parts = engine && engine.available
    ? [engine.prereg ? `Preregistration ${h8(engine.prereg.sha256)}` : null, engine.config_hash ? `Config ${h8(engine.config_hash)}` : null, engine.scorecard ? `Scorecard ${h8(engine.scorecard.sha256)}` : null]
    : [];
  return {
    chip: 'RECORDED', source: `Replay · ${doc.title} · ${engine?.label ?? 'No engine'}`, runId: engine?.chain?.run_id ?? null,
    scope: [...parts, splitWord(doc.status.split), doc.status.not_blind ? 'Not blind' : null, 'Descriptive'].filter(Boolean).join(' · '),
  };
}

function keyLines(input: ExportInput, max: number): string[] {
  const ev = input.doc.observed.events.map((e, i) => `${i + 1}  ${safeText(e.label)}, ${fmtDate(e.date)}  ${safeText(e.publisher)}`);
  const st = input.steps.slice(0, max).map((s, i) => `${i + 1}  ${input.name(s.placeId)}, ${s.channel}, +${s.addedD.toFixed(1)} d`);
  return [...ev, ...(st.length ? ['Causal chain'] : []), ...st];
}

/** The drawings with their headings, an event key and the caption, as one standalone SVG tree. */
export function frameComparison(input: ExportInput, title: string, prov: Provenance): { nodes: VNode[]; width: number; height: number } {
  const parts: Array<{ heading: string; chart: ChartOut }> = [{ heading: 'WHAT HAPPENED', chart: input.observed }];
  if (input.model) parts.push({ heading: 'WHAT THE MODEL PRODUCED', chart: input.model });
  if (input.sim) parts.push({ heading: 'SIMULATION LAYER', chart: input.sim });
  const inner = Math.max(...parts.map((p) => p.chart.width));
  const width = inner + PAD * 2;
  const body: VNode[] = [];
  let y = PAD + 8;
  body.push(text(PAD, y, clipLabel(title.toUpperCase(), inner, 11), { size: 11, fill: INK, weight: 600, tracking: 0.5 }));
  y += 10;
  for (const p of parts) {
    y += 12;
    body.push(text(PAD, y, p.heading, { size: 9, fill: INK2, tracking: 0.6 }));
    body.push(translate(PAD, y + 4, p.chart.nodes));
    y += 4 + p.chart.height + GAP;
  }
  const lines = keyLines(input, 12);
  for (const l of lines) { y += 12; body.push(text(PAD, y, clipLabel(l, inner, 9), { fill: l === 'Causal chain' ? INK2 : INK, size: 9 })); }
  const yCap = y + 8;
  body.push(h('line', { x1: PAD, x2: width - PAD, y1: yCap, y2: yCap, stroke: 'var(--line)', strokeWidth: 1 }));
  const cap = wrapCaption(prov, inner);
  cap.forEach((l, i) => body.push(text(PAD, yCap + 13 + i * 12, l, { fill: INK2 })));
  const height = yCap + 13 + cap.length * 12 + PAD - 4;
  return { nodes: [h('rect', { x: 0, y: 0, width, height, fill: SURFACE }), ...body], width, height };
}

const fileOf = (input: ExportInput, ext: 'svg' | 'png' | 'csv', scale?: number): string =>
  exportFilename({ title: `${input.doc.title} ${input.engine?.label ?? ''}`.trim(), type: 'replay', runId: input.engine?.chain?.run_id ?? null, ext, scale });

export function comparisonTable(input: ExportInput) {
  return { columns: COMPARISON_COLUMNS, rows: comparisonRows(input.rows, labelsOf(input.doc, input.engine)) };
}

export async function runReplayExport(kind: ExportKind, skin: ExportSkin, input: ExportInput): Promise<ExportResult> {
  try {
    if (kind === 'copy-link' || kind === 'srv-svg' || kind === 'srv-png' || kind === 'srv-csv') return { ok: false, word: 'NOT SERVED' };
    const prov = provenanceOf(input.doc, input.engine);
    const table = comparisonTable(input);
    if (kind === 'csv') { const file = fileOf(input, 'csv'); saveBlob(file, new Blob([tableToCsvFile(table, prov)], { type: 'text/csv;charset=utf-8' })); return { ok: true, word: 'SAVED CSV', file }; }
    if (kind === 'copy-data') { await navigator.clipboard.writeText(tableToTsv(table, prov)); return { ok: true, word: 'DATA COPIED' }; }
    const framed = frameComparison(input, `${input.doc.title} · ${input.engine?.label ?? 'No engine'}`, prov);
    const { lookup, done } = liveLookup(skin);
    let svg: string;
    try { svg = toSvgString(framed.nodes, framed, lookup, { role: 'img', 'aria-label': `${input.doc.title} replay` }); } finally { done(); }
    if (kind === 'svg') { const file = fileOf(input, 'svg'); saveBlob(file, new Blob([svg], { type: 'image/svg+xml;charset=utf-8' })); return { ok: true, word: 'SAVED SVG', file }; }
    if (kind === 'png1' || kind === 'png2') {
      const scale = kind === 'png2' ? 2 : 1;
      const png = await svgToPng(svg, framed.width, framed.height, scale);
      const file = fileOf(input, 'png', scale); saveBlob(file, png); return { ok: true, word: `SAVED PNG ${scale}X`, file };
    }
    if (kind === 'copy-image') {
      const png = await svgToPng(svg, framed.width, framed.height, 2);
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': png })]);
      return { ok: true, word: 'IMAGE COPIED' };
    }
    return { ok: false, word: 'UNKNOWN' };
  } catch (e) {
    const name = (e as Error)?.name;
    return { ok: false, word: name === 'NotAllowedError' || name === 'SecurityError' ? 'COPY BLOCKED' : 'EXPORT FAILED' };
  }
}

/** The workbook body: the comparison, the modelled steps, the dated reports, and a provenance sheet with full hashes. */
export function xlsxBody(input: ExportInput, created: string) {
  const l = labelsOf(input.doc, input.engine);
  const meta: Array<[string, string]> = [
    ['Episode', input.doc.title], ['Engine', l.engine], ['State', 'Recorded'], ['Episode split', splitWord(input.doc.status.split) ?? 'None'],
    ['Not blind', input.doc.status.not_blind ? 'Yes' : 'No'], ['Preregistration', l.prereg], ['Config', l.config], ['Scorecard', l.scorecard],
    ['Comparison', 'Descriptive'],
    ...input.doc.provenance.files.slice(0, 30).map((f): [string, string] => [f.file.split('/').pop() ?? f.file, f.sha256]),
  ];
  return {
    title: `Replay · ${input.doc.title} · ${l.engine}`, created, meta,
    sheets: [
      { name: 'Comparison', columns: COMPARISON_COLUMNS, rows: comparisonRows(input.rows, l) },
      { name: 'Steps', columns: STEP_COLUMNS, rows: stepRows(input.steps, input.name, l) },
      { name: 'Reports', columns: EVENT_COLUMNS, rows: eventRows(input.doc.observed.events, input.doc.title) },
    ],
  };
}

export async function runXlsxExport(input: ExportInput): Promise<ExportResult> {
  try {
    const created = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
    const res = await call('/replay/export/xlsx', { method: 'POST', body: xlsxBody(input, created), timeoutMs: 60000 });
    const blob = await res.blob();
    const file = `supermap-${slug(`${input.doc.title} ${input.engine?.label ?? ''}`)}-replay.xlsx`;
    saveBlob(file, blob);
    return { ok: true, word: 'SAVED XLSX', file };
  } catch (e) {
    const code = (e as { code?: string }).code ?? 'failed';
    return { ok: false, word: code === 'http_404' || code === 'http_405' ? 'NOT SERVED' : 'EXPORT FAILED' };
  }
}
