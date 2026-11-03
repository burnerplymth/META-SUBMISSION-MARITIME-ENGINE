/* Browser half of chart export: resolve the live theme tokens, render
 * the standalone SVG to a PNG on a canvas, hand a Blob to the browser,
 * write to the clipboard. Everything reports a state word instead of
 * throwing. */
import { saveBlob } from '../panels/reports/client';
import { chartToSvg, exportFilename, tableToCsvFile, tableToTsv, type FrameOpts } from './serialize';
import type { ChartSpec } from './types';
import type { VarLookup } from './vnode';

/** 'shown' keeps the live skin (retro skins export as their paired
 *  institutional skin, as the panel exporter does); 'print' pins the
 *  neutral light skin. */
export type ExportSkin = 'shown' | 'print';

/** Read tokens from the document, optionally with the theme pinned for
 *  the duration of the read. getComputedStyle is synchronous, so the
 *  live page never repaints with the pinned skin. */
export function liveLookup(skin: ExportSkin): { lookup: VarLookup; done: () => void } {
  const root = document.documentElement;
  const prevTheme = root.dataset.theme; const prevSkin = root.dataset.skin;
  const retro = prevTheme === 'retro-dark' || prevTheme === 'retro-light';
  if (skin === 'print') { root.dataset.theme = 'light'; delete root.dataset.skin; }
  else if (retro) root.dataset.theme = prevTheme === 'retro-light' ? 'light' : 'dark';
  const cs = getComputedStyle(root);
  const lookup: VarLookup = (name) => cs.getPropertyValue(name).trim() || undefined;
  const done = () => {
    if (prevTheme === undefined) delete root.dataset.theme; else root.dataset.theme = prevTheme;
    if (prevSkin === undefined) delete root.dataset.skin; else root.dataset.skin = prevSkin;
  };
  return { lookup, done };
}

export interface ExportRequest { spec: ChartSpec; frame: FrameOpts; skin: ExportSkin }

/** Build the SVG text with every token resolved from the live page. */
export function renderSvgNow(req: ExportRequest): { svg: string; width: number; height: number; table: ReturnType<typeof chartToSvg>['model']['table'] } {
  const { lookup, done } = liveLookup(req.skin);
  try {
    const r = chartToSvg(req.spec, req.frame, lookup);
    return { svg: r.svg, width: r.width, height: r.height, table: r.model.table };
  } finally { done(); }
}

const nameOf = (spec: ChartSpec, ext: Parameters<typeof exportFilename>[0]['ext'], scale?: number) =>
  exportFilename({ title: spec.title, type: spec.type, runId: spec.provenance.runId, asOf: spec.provenance.asOf, ext, scale });

export type ExportKind = 'svg' | 'png1' | 'png2' | 'csv' | 'copy-image' | 'copy-data' | 'copy-link' | 'srv-svg' | 'srv-png' | 'srv-csv';
/** Figures drawn by the exports route, for a chart that maps to one. */
export interface ServerExport { ok: boolean; note?: string; run: (ext: 'svg' | 'png' | 'csv') => Promise<ExportResult> }
export interface ExportResult { ok: boolean; word: string; file?: string }

export async function svgToPng(svg: string, width: number, height: number, scale: number): Promise<Blob> {
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }));
  try {
    const img = new Image();
    img.decoding = 'sync';
    await new Promise<void>((res, rej) => { img.onload = () => res(); img.onerror = () => rej(new Error('image_decode')); img.src = url; });
    const c = document.createElement('canvas');
    c.width = Math.round(width * scale); c.height = Math.round(height * scale);
    const ctx = c.getContext('2d');
    if (!ctx) throw new Error('no_canvas');
    ctx.scale(scale, scale);
    ctx.drawImage(img, 0, 0, width, height);
    return await new Promise<Blob>((res, rej) => c.toBlob((b) => (b ? res(b) : rej(new Error('toBlob'))), 'image/png'));
  } finally { URL.revokeObjectURL(url); }
}

export async function runExport(kind: ExportKind, req: ExportRequest, link?: string | null, server?: ServerExport | null): Promise<ExportResult> {
  try {
    if (kind === 'srv-svg' || kind === 'srv-png' || kind === 'srv-csv') {
      if (!server?.ok) return { ok: false, word: server?.note ?? 'NOT SERVED' };
      return await server.run(kind === 'srv-svg' ? 'svg' : kind === 'srv-png' ? 'png' : 'csv');
    }
    if (kind === 'copy-link') {
      if (!link) return { ok: false, word: 'No link' };
      await navigator.clipboard.writeText(link);
      return { ok: true, word: 'Link copied' };
    }
    const r = renderSvgNow(req);
    const prov = req.spec.provenance;
    if (kind === 'svg') {
      const file = nameOf(req.spec, 'svg');
      saveBlob(file, new Blob([r.svg], { type: 'image/svg+xml;charset=utf-8' }));
      return { ok: true, word: 'Saved SVG', file };
    }
    if (kind === 'png1' || kind === 'png2') {
      const scale = kind === 'png2' ? 2 : 1;
      const png = await svgToPng(r.svg, r.width, r.height, scale);
      const file = nameOf(req.spec, 'png', scale);
      saveBlob(file, png);
      return { ok: true, word: `Saved PNG ${scale}x`, file };
    }
    if (kind === 'csv') {
      const file = nameOf(req.spec, 'csv');
      saveBlob(file, new Blob([tableToCsvFile(r.table, prov)], { type: 'text/csv;charset=utf-8' }));
      return { ok: true, word: 'Saved CSV', file };
    }
    if (kind === 'copy-data') {
      await navigator.clipboard.writeText(tableToTsv(r.table, prov));
      return { ok: true, word: 'Data copied' };
    }
    if (kind === 'copy-image') {
      const png = await svgToPng(r.svg, r.width, r.height, 2);
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': png })]);
      return { ok: true, word: 'Image copied' };
    }
    return { ok: false, word: 'Not exported' };
  } catch (e) {
    const name = (e as Error)?.name;
    return { ok: false, word: name === 'NotAllowedError' || name === 'SecurityError' ? 'Copy blocked' : 'Export failed' };
  }
}
