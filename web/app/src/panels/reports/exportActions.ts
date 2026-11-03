/* Browser actions of the export page: fetch-and-save one dataset in each
 * format, open a print window, stack several charts into one PNG. Each
 * resolves to a small result so the page can show a state word and add
 * the file to the recent list. */
import { svgToPng } from '../../charts/exportDom';
import { CentreError, call, fetchCsv, saveBlob } from './client';
import { figurePath, pngFallback, svgSize, type FigureExt, type FigureQuery } from './serverCharts';
import { csvPath, type ExportDataset } from './model';
import { datasetJson } from './exportPlan';
import { fetchDataset } from '../dataviewer/dataClient';
import type { RecentItem, RecentReq } from './recent';

export interface Query { runId: string | null; simRunId: string | null; from: string | null; to: string | null; rowsCap: number }
export type Done = Omit<RecentItem, 'key'>;

const stamp = (): string => new Date().toISOString();

export async function saveCsv(d: ExportDataset, q: Query): Promise<Done> {
  const path = csvPath(d, q);
  const dl = await fetchCsv(path);
  saveBlob(dl.filename, dl.blob);
  return { label: `${d.label.toUpperCase()} CSV`, file: dl.filename, at: stamp(), runId: q.runId, bytes: dl.blob.size, rows: dl.result.written, req: { t: 'csv', path } };
}

export async function saveJson(d: ExportDataset, q: Query): Promise<Done> {
  const path = csvPath(d, q);
  const r = await fetchDataset(d, q);
  const body = JSON.stringify(datasetJson(r.ds, { runId: q.runId, simRunId: q.simRunId, provenance: d.provenance ?? r.result.provenance, fetchedAt: r.asOf, available: r.result.available }), null, 2);
  const file = `${d.id}.json`;
  const blob = new Blob([body], { type: 'application/json' });
  saveBlob(file, blob);
  return { label: `${d.label.toUpperCase()} JSON`, file, at: stamp(), runId: q.runId, bytes: blob.size, rows: r.ds.rows.length, req: { t: 'json', path, id: d.id } };
}

export const xlsxPath = (d: ExportDataset, q: Query): string => csvPath(d, q).replace(/\.csv/, '.xlsx');

export async function saveXlsx(d: ExportDataset, q: Query): Promise<Done> {
  const path = xlsxPath(d, q);
  const res = await call(path, { timeoutMs: 90000 });
  const blob = await res.blob();
  const m = /filename="?([^";]+)"?/.exec(res.headers.get('content-disposition') ?? '');
  const file = m?.[1] ?? `${d.id}.xlsx`;
  saveBlob(file, blob);
  return { label: `${d.label.toUpperCase()} XLSX`, file, at: stamp(), runId: q.runId, bytes: blob.size, rows: null, req: { t: 'xlsx', path } };
}

/** Run a stored request again (csv / json / xlsx only: the others need page state). */
export async function replay(req: RecentReq, catalogue: ExportDataset[]): Promise<boolean> {
  if (req.t === 'csv' || req.t === 'xlsx') {
    const res = await call(req.path, { timeoutMs: 90000 });
    const blob = await res.blob();
    const m = /filename="?([^";]+)"?/.exec(res.headers.get('content-disposition') ?? '');
    saveBlob(m?.[1] ?? req.path.split('/').pop()?.split('?')[0] ?? 'export', blob);
    return true;
  }
  if (req.t === 'figure') {
    const res = await call(req.path, { timeoutMs: 90000 });
    saveBlob(`${req.path.split('/').pop()?.split('?')[0] ?? 'figure'}`, await res.blob());
    return true;
  }
  if (req.t === 'figpost') {
    const res = await call('/exports/chart', { method: 'POST', body: { ...req.body, format: req.ext }, timeoutMs: 90000 });
    saveBlob(`${String(req.body.dataset ?? 'figure')}-${String(req.body.y ?? 'chart')}.${req.ext}`, await res.blob());
    return true;
  }
  if (req.t === 'json') {
    const d = catalogue.find((x) => x.id === req.id);
    if (!d) return false;
    const u = new URL(req.path, 'http://x.invalid');
    await saveJson(d, { runId: u.searchParams.get('run_id'), simRunId: u.searchParams.get('sim_run_id'), from: u.searchParams.get('from'), to: u.searchParams.get('to'), rowsCap: Number(u.searchParams.get('rows_cap')) || 10000 });
    return true;
  }
  return false;
}

/** Print window for a standalone HTML page. Returns false when blocked. */
export function openPrintWindow(html: string): boolean {
  const url = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }));
  const w = window.open(url, '_blank');
  if (!w) { URL.revokeObjectURL(url); return false; }
  const go = () => { try { w.focus(); w.print(); } catch { /* the print dialog is the viewer's to open */ } };
  w.addEventListener?.('load', () => setTimeout(go, 250));
  setTimeout(() => URL.revokeObjectURL(url), 120000);
  return true;
}

/** Several framed SVGs stacked into one PNG. */
export async function stackedPng(svgs: Array<{ svg: string; width: number; height: number }>, scale = 2, gap = 8, background = '#ffffff'): Promise<Blob> {
  const width = Math.max(...svgs.map((s) => s.width));
  const height = svgs.reduce((a, s) => a + s.height, 0) + gap * Math.max(0, svgs.length - 1);
  const c = document.createElement('canvas');
  c.width = Math.round(width * scale); c.height = Math.round(height * scale);
  const ctx = c.getContext('2d');
  if (!ctx) throw new Error('no_canvas');
  ctx.fillStyle = background; ctx.fillRect(0, 0, c.width, c.height);
  let y = 0;
  for (const s of svgs) {
    const png = await svgToPng(s.svg, s.width, s.height, scale);
    const bmp = await createImageBitmap(png);
    ctx.drawImage(bmp, 0, Math.round(y * scale));
    bmp.close?.();
    y += s.height + gap;
  }
  return new Promise<Blob>((res, rej) => c.toBlob((b) => (b ? res(b) : rej(new Error('toBlob'))), 'image/png'));
}

/* ── server figures (GET /exports/chart/{id}.ext, POST /exports/chart) ── */
export type Via = 'server' | 'browser';

async function pngFromSvg(svg: string, scale: number): Promise<Blob> {
  const size = svgSize(svg);
  if (!size) throw new Error('svg_size');
  return svgToPng(svg, size.width, size.height, scale);
}

/** Save one catalogue figure. A PNG the server cannot draw (501 / 502, or
 *  a catalogue that says so) is drawn here from the server's own SVG. */
export async function saveFigure(f: { id: string; title: string }, ext: FigureExt, q: FigureQuery, pngAvailable = true): Promise<Done & { via: Via }> {
  const path = figurePath(f.id, ext, q);
  let blob: Blob; let via: Via = 'server'; let file = `${f.id}.${ext}`;
  if (ext === 'png') {
    const scale = q.scale ?? 2;
    try {
      if (!pngAvailable) throw new CentreError('renderer_unavailable', 'catalogue says unavailable', 501);
      blob = await (await call(path, { timeoutMs: 90000 })).blob();
    } catch (e) {
      if (!pngFallback((e as CentreError).code)) throw e;
      const svg = await (await call(figurePath(f.id, 'svg', q), { timeoutMs: 60000 })).text();
      blob = await pngFromSvg(svg, scale); via = 'browser';
    }
    file = `${f.id}${scale > 1 ? `@${scale}x` : ''}.png`;
  } else {
    const res = await call(path, { timeoutMs: 60000 });
    blob = await res.blob();
    file = /filename="?([^";]+)"?/.exec(res.headers.get('content-disposition') ?? '')?.[1] ?? file;
  }
  saveBlob(file, blob);
  return { label: `${f.title.toUpperCase().slice(0, 40)} ${ext.toUpperCase()}`, file, at: stamp(), runId: q.runId, bytes: blob.size, rows: null,
    req: { t: 'figure', path, ext }, via };
}

/** Ad-hoc figure from a dataset-shaped body; same PNG fallback. */
export async function savePostedFigure(body: Record<string, unknown>, ext: 'svg' | 'png' | 'csv', name: string, scale = 2): Promise<Done & { via: Via }> {
  let blob: Blob; let via: Via = 'server';
  if (ext === 'png') {
    try { blob = await (await call('/exports/chart', { method: 'POST', body: { ...body, format: 'png', scale }, timeoutMs: 90000 })).blob(); } catch (e) {
      if (!pngFallback((e as CentreError).code)) throw e;
      const svg = await (await call('/exports/chart', { method: 'POST', body: { ...body, format: 'svg' }, timeoutMs: 60000 })).text();
      blob = await pngFromSvg(svg, scale); via = 'browser';
    }
  } else blob = await (await call('/exports/chart', { method: 'POST', body: { ...body, format: ext }, timeoutMs: 60000 })).blob();
  const file = `${name}${ext === 'png' && scale > 1 ? `@${scale}x` : ''}.${ext}`;
  saveBlob(file, blob);
  return { label: `Server figure ${ext.toUpperCase()}`, file, at: stamp(), runId: typeof body.run_id === 'string' ? body.run_id : null, bytes: blob.size, rows: null, req: { t: 'figpost', body, ext }, via };
}
