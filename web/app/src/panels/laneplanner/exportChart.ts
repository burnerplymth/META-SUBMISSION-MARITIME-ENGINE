/* Export of the lane planner's two drawings through the shared chart-export
 * machinery (charts/serialize.ts, charts/exportDom.ts, ExportMenu): the SVG is
 * the one on screen with a title and a provenance caption, the CSV is the
 * plotted table plus a provenance column. The server figure (POST
 * /exports/chart, a grouped bar of low, median, high) is offered beside them. */
import { INK, INK2, SURFACE, text } from '../../charts/draw';
import { clipLabel } from '../../charts/format';
import { liveLookup, svgToPng, type ExportKind, type ExportResult, type ExportSkin, type ServerExport } from '../../charts/exportDom';
import { exportFilename, tableToCsv, tableToCsvFile, tableToTsv, wrapCaption } from '../../charts/serialize';
import type { ExportTable, Provenance } from '../../charts/types';
import { h, toSvgString, translate, type VNode } from '../../charts/vnode';
import { call } from '../reports/client';
import { saveBlob } from '../reports/client';
import { serverBarSpec } from './logic';
import type { PlanDoc } from './types';

const PAD = 12;

export function provenanceOf(doc: PlanDoc): Provenance {
  const sim = doc.routes.some((r) => r.queue && r.queue.high > 0);
  return {
    chip: sim ? 'SIMULATED' : 'MODELLED',
    source: `Lane planner · ${doc.engine_label} · named variants`,
    runId: doc.plan_id, scope: doc.disruption ? `${doc.disruption.name} ${doc.disruption.kind} ${doc.disruption.duration_d} d` : 'No disruption',
  };
}

/** The chart with its title and caption, as one standalone SVG tree. */
export function frameChart(title: string, chart: { nodes: VNode[]; width: number; height: number }, prov: Provenance): { nodes: VNode[]; width: number; height: number } {
  const inner = chart.width;
  const width = inner + PAD * 2;
  const lines = wrapCaption(prov, inner);
  const y0 = PAD + 12;
  const body: VNode[] = [];
  body.push(text(PAD, PAD + 8, clipLabel(title, inner, 11), { size: 11, fill: INK, weight: 600 }));
  body.push(translate(PAD, y0 + 4, chart.nodes));
  const yCap = y0 + 4 + chart.height + 6;
  body.push(h('line', { x1: PAD, x2: width - PAD, y1: yCap, y2: yCap, stroke: 'var(--line)', strokeWidth: 1 }));
  lines.forEach((l, i) => body.push(text(PAD, yCap + 13 + i * 12, l, { fill: INK2 })));
  const height = yCap + 13 + lines.length * 12 + PAD - 4;
  return { nodes: [h('rect', { x: 0, y: 0, width, height, fill: SURFACE }), ...body], width, height };
}

export interface LaneExportInput { doc: PlanDoc; title: string; kind: string; chart: { nodes: VNode[]; width: number; height: number; table: ExportTable } }

const nameOf = (i: LaneExportInput, ext: 'svg' | 'png' | 'csv', scale?: number) =>
  exportFilename({ title: i.title, type: i.kind, runId: i.doc.plan_id, ext, scale });

export async function runLaneExport(kind: ExportKind, skin: ExportSkin, i: LaneExportInput, server: ServerExport | null): Promise<ExportResult> {
  try {
    if (kind === 'srv-svg' || kind === 'srv-png' || kind === 'srv-csv') {
      if (!server?.ok) return { ok: false, word: server?.note ?? 'Not served' };
      return await server.run(kind === 'srv-svg' ? 'svg' : kind === 'srv-png' ? 'png' : 'csv');
    }
    if (kind === 'copy-link') return { ok: false, word: 'No link' };
    const prov = provenanceOf(i.doc);
    const framed = frameChart(i.title, i.chart, prov);
    const { lookup, done } = liveLookup(skin);
    let svg: string;
    try { svg = toSvgString(framed.nodes, framed, lookup, { role: 'img', 'aria-label': i.title }); } finally { done(); }
    if (kind === 'svg') { const file = nameOf(i, 'svg'); saveBlob(file, new Blob([svg], { type: 'image/svg+xml;charset=utf-8' })); return { ok: true, word: 'Saved SVG', file }; }
    if (kind === 'png1' || kind === 'png2') {
      const scale = kind === 'png2' ? 2 : 1;
      const png = await svgToPng(svg, framed.width, framed.height, scale);
      const file = nameOf(i, 'png', scale); saveBlob(file, png); return { ok: true, word: `Saved PNG ${scale}×`, file };
    }
    if (kind === 'csv') { const file = nameOf(i, 'csv'); saveBlob(file, new Blob([tableToCsvFile(i.chart.table, prov)], { type: 'text/csv;charset=utf-8' })); return { ok: true, word: 'Saved CSV', file }; }
    if (kind === 'copy-data') { await navigator.clipboard.writeText(tableToTsv(i.chart.table, prov)); return { ok: true, word: 'Data copied' }; }
    if (kind === 'copy-image') {
      const png = await svgToPng(svg, framed.width, framed.height, 2);
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': png })]);
      return { ok: true, word: 'Image copied' };
    }
    return { ok: false, word: 'Unknown' };
  } catch (e) {
    const name = (e as Error)?.name;
    return { ok: false, word: name === 'NotAllowedError' || name === 'SecurityError' ? 'Copy blocked' : 'Export failed' };
  }
}

/** The same figure drawn by the exports route (a grouped bar). */
export function laneServerExport(doc: PlanDoc): ServerExport {
  return {
    ok: true,
    run: async (ext) => {
      try {
        const res = await call('/exports/chart', { method: 'POST', body: { spec: serverBarSpec(doc), format: ext, theme: 'light' }, timeoutMs: 90000 });
        const blob = await res.blob();
        const file = `supermap-lane-plan-${doc.plan_id.replace(/^lp_/, '')}.${ext}`;
        saveBlob(file, blob);
        return { ok: true, word: `Saved server ${ext.toUpperCase()}`, file };
      } catch (e) {
        const code = (e as { code?: string }).code ?? 'failed';
        return { ok: false, word: code === 'http_404' || code === 'http_405' ? 'Not served' : 'Export failed' };
      }
    },
  };
}
