/* PDF renderer of a ReportDoc (or a pack of them): a vector PDF written in the browser with jsPDF. Text is real text in
 * the embedded IBM Plex fonts (selectable and searchable); the chart kit's SVG figures are drawn as vector paths by
 * svg2pdf.js; nothing is rasterised. This module and the two libraries are loaded on demand, never with the main bundle.
 * Layout is a simple flow: header and footer on every page, blocks keep together at page breaks, table headings repeat. */
import type { jsPDF as JsPdf } from 'jspdf';
import { pdfSvgText, pdfText } from './glyphs';
import { footerParts, PRODUCT, REPORT_KIND, cellText, type Block, type FigureBlock, type ReportChip, type ReportDoc, type TableCell, type TableColumn } from './model';
import { TONE } from './tokens';

export interface PdfFonts { sans: string; bold: string; mono: string }
export interface PdfDeps {
  jsPDF: typeof JsPdf;
  /** base64 of the three TrueType files */
  fonts: PdfFonts;
  /** draws one SVG figure into the box (millimetres); the browser path uses svg2pdf.js */
  drawSvg: (pdf: JsPdf, svg: string, x: number, y: number, w: number, h: number) => Promise<void>;
  /** Generation stamp for the cover; may be omitted. */
  generated?: string | null;
}
export interface PdfOptions { packTitle?: string }
export interface PdfResult { bytes: ArrayBuffer; pages: number; starts: number[] }

const PX = 25.4 / 96;
const MARGIN = 14;
const HEAD_H = 18;
const FOOT_H = 17;
const PT = 25.4 / 72;
const INK = '#1b1d1f'; const INK2 = '#4a5158'; const RULE = '#8d939a'; const HAIR = '#dfe2e5'; const PANEL = '#f5f6f7'; const BAR = '#eceef0';

type Face = 'sans' | 'bold' | 'mono';
const FAMILY: Record<Face, [string, string]> = { sans: ['IBM Plex Sans', 'normal'], bold: ['IBM Plex Sans', 'bold'], mono: ['IBM Plex Mono', 'normal'] };

interface PageRec { doc: ReportDoc; w: number; h: number }

class Writer {
  pdf: JsPdf;
  pages: PageRec[] = [];
  y = 0;
  w = 210; h = 297;
  cur: ReportDoc | null = null;
  constructor(private deps: PdfDeps) {
    this.pdf = new deps.jsPDF({ unit: 'mm', format: 'a4', orientation: 'portrait', compress: true, putOnlyUsedFonts: true });
    const add = (file: string, data: string, face: Face) => { this.pdf.addFileToVFS(file, data); this.pdf.addFont(file, FAMILY[face][0], FAMILY[face][1]); };
    add('plex-sans-400.ttf', deps.fonts.sans, 'sans'); add('plex-sans-600.ttf', deps.fonts.bold, 'bold'); add('plex-mono-400.ttf', deps.fonts.mono, 'mono');
    this.pdf.deletePage(1);
  }

  get cw(): number { return this.w - 2 * MARGIN; }
  get bottom(): number { return this.h - FOOT_H; }
  get top(): number { return HEAD_H; }

  font(face: Face, pt: number): void { this.pdf.setFont(FAMILY[face][0], FAMILY[face][1]); this.pdf.setFontSize(pt); }
  tw(s: string, face: Face, pt: number): number { this.font(face, pt); return this.pdf.getTextWidth(pdfText(s)); }
  lh(pt: number): number { return pt * PT * 1.38; }
  wrap(s: string, face: Face, pt: number, width: number): string[] {
    this.font(face, pt);
    const lines = this.pdf.splitTextToSize(pdfText(s), Math.max(4, width)) as string[];
    return lines.length ? lines : [''];
  }
  /** one line of text whose line box starts at y */
  text(s: string, x: number, y: number, face: Face, pt: number, color = INK, align: 'left' | 'right' | 'center' = 'left'): void {
    this.font(face, pt); this.pdf.setTextColor(color);
    this.pdf.text(pdfText(s), x, y + this.lh(pt) / 2 + pt * PT * 0.33, { align });
  }
  lines(ls: string[], x: number, y: number, face: Face, pt: number, color = INK): number {
    ls.forEach((l, i) => this.text(l, x, y + i * this.lh(pt), face, pt, color));
    return ls.length * this.lh(pt);
  }

  newPage(doc: ReportDoc): void {
    this.cur = doc;
    this.w = doc.orientation === 'landscape' ? 297 : 210; this.h = doc.orientation === 'landscape' ? 210 : 297;
    this.pdf.addPage('a4', doc.orientation);
    this.pages.push({ doc, w: this.w, h: this.h });
    this.y = this.top;
  }
  ensure(need: number): void { if (this.y + need > this.bottom && this.cur) this.newPage(this.cur); }
  get free(): number { return this.bottom - this.y; }

  chip(c: ReportChip, x: number, y: number): number {
    const pt = 6.6; const t = TONE[c.tone] ?? TONE.info;
    const w = this.tw(c.word, 'bold', pt) + 3.4; const hh = 3.7;
    this.pdf.setFillColor(t.bg); this.pdf.setDrawColor(t.line); this.pdf.setLineWidth(0.2);
    this.pdf.roundedRect(x, y, w, hh, 0.9, 0.9, 'FD');
    this.text(c.word, x + 1.7, y + (hh - this.lh(pt)) / 2, 'bold', pt, t.fg);
    return w;
  }
  chipRow(cs: readonly ReportChip[], x: number, y: number, maxW: number): number {
    let cx = x; let cy = y;
    for (const c of cs) {
      const w = this.tw(c.word, 'bold', 6.6) + 3.4;
      if (cx + w > x + maxW && cx > x) { cx = x; cy += 5; }
      this.chip(c, cx, cy); cx += w + 1.4;
    }
    return cy + 3.7 - y;
  }
  rule(y: number, color = HAIR, lw = 0.2, x0 = MARGIN, x1?: number): void {
    this.pdf.setDrawColor(color); this.pdf.setLineWidth(lw); this.pdf.line(x0, y, x1 ?? this.w - MARGIN, y);
  }
}

/* ── blocks ─────────────────────────────────────────────────────────────── */
function heading(w: Writer, b: Extract<Block, { t: 'heading' }>, nextNeeds = 24): void {
  const pt = b.level === 1 ? 12 : b.level === 2 ? 10 : 9;
  /* a heading never ends a page: it moves with the first part of what it introduces */
  w.ensure(w.lh(pt) + 4 + nextNeeds);
  w.y += b.level === 1 ? 3 : 2.2;
  w.text(b.text, MARGIN, w.y, 'bold', pt);
  let used = w.lh(pt);
  if (b.chips?.length) { const tx = MARGIN + w.tw(b.text, 'bold', pt) + 3; w.chipRow(b.chips, tx, w.y + (used - 3.7) / 2, w.w - MARGIN - tx); }
  if (b.level === 1) { w.rule(w.y + used + 0.3, RULE, 0.25); used += 1.2; }
  w.y += used + 1.2;
}

function para(w: Writer, text: string, muted: boolean): void {
  const ls = w.wrap(text, 'sans', 8.6, w.cw);
  const per = w.lh(8.6);
  let i = 0;
  while (i < ls.length) {
    const fit = Math.max(1, Math.min(ls.length - i, Math.floor(w.free / per)));
    if (w.free < per) { w.ensure(per); continue; }
    w.lines(ls.slice(i, i + fit), MARGIN, w.y, 'sans', 8.6, muted ? INK2 : INK);
    w.y += fit * per; i += fit;
    if (i < ls.length) w.ensure(per);
  }
  w.y += 1.6;
}

function noteBox(w: Writer, text: string): void {
  const ls = w.wrap(text, 'sans', 8, w.cw - 5);
  const hh = ls.length * w.lh(8) + 3;
  w.ensure(hh + 1.5);
  w.pdf.setFillColor(PANEL); w.pdf.setDrawColor('#c3c7cc'); w.pdf.setLineWidth(0.2);
  w.pdf.rect(MARGIN, w.y, w.cw, hh, 'FD');
  w.lines(ls, MARGIN + 2.5, w.y + 1.5, 'sans', 8);
  w.y += hh + 2;
}

function kv(w: Writer, rows: Array<[string, string]>, mono: boolean): void {
  const colW = (w.cw - 6) / 2; const keyW = colW * 0.34; const pt = 7.6;
  for (let i = 0; i < rows.length; i += 2) {
    const pair = rows.slice(i, i + 2);
    const cells = pair.map(([k, v]) => ({ k: w.wrap(k, 'sans', pt, keyW - 1), v: w.wrap(v, mono ? 'mono' : 'sans', mono ? 7 : pt, colW - keyW - 1) }));
    const hh = Math.max(...cells.map((c) => Math.max(c.k.length * w.lh(pt), c.v.length * w.lh(pt)))) + 1.6;
    w.ensure(hh);
    cells.forEach((c, j) => {
      const x = MARGIN + j * (colW + 6);
      w.lines(c.k, x, w.y + 0.8, 'sans', pt, INK2);
      w.lines(c.v, x + keyW, w.y + 0.8, mono ? 'mono' : 'sans', mono ? 7 : pt);
      w.rule(w.y + hh, HAIR, 0.2, x, x + colW);
    });
    w.y += hh;
  }
  w.y += 2.2;
}

function tiles(w: Writer, ts: Extract<Block, { t: 'tiles' }>['tiles']): void {
  const per = ts.length === 1 ? 2 : ts.length <= 3 ? 3 : 4; const gap = 2.4; const tw = (w.cw - gap * (per - 1)) / per;
  for (let i = 0; i < ts.length; i += per) {
    const row = ts.slice(i, i + per);
    const parts = row.map((t) => ({ l: w.wrap(t.label, 'sans', 6.8, tw - 4), v: w.wrap(t.value, 'bold', 11, tw - 4), s: t.sub ? w.wrap(t.sub, 'sans', 6.8, tw - 4) : [] }));
    const hh = Math.max(...parts.map((p, j) => p.l.length * w.lh(6.8) + p.v.length * w.lh(11) + p.s.length * w.lh(6.8) + (row[j].chip ? 4.6 : 0))) + 3.6;
    w.ensure(hh + 1);
    row.forEach((t, j) => {
      const x = MARGIN + j * (tw + gap); const p = parts[j];
      w.pdf.setDrawColor('#c3c7cc'); w.pdf.setLineWidth(0.2); w.pdf.rect(x, w.y, tw, hh, 'S');
      let yy = w.y + 1.8;
      yy += w.lines(p.l, x + 2, yy, 'sans', 6.8, INK2);
      yy += w.lines(p.v, x + 2, yy, 'bold', 11);
      if (p.s.length) yy += w.lines(p.s, x + 2, yy, 'sans', 6.8, INK2);
      if (t.chip) w.chip(t.chip, x + 2, yy + 0.6);
    });
    w.y += hh + gap;
  }
  w.y += 0.6;
}

interface Laid { widths: number[] }
function layoutColumns(w: Writer, cols: readonly TableColumn[], rows: readonly TableCell[][], pt: number): Laid {
  const pad = 1.6; const n = cols.length;
  const nat = cols.map((c, i) => {
    let m = w.tw(c.label, 'bold', pt);
    for (const r of rows.slice(0, 120)) { const t = cellText(r[i] ?? null); const cell = r[i]; const chip = typeof cell === 'object' && cell && 'chip' in cell && cell.chip; m = Math.max(m, w.tw(t, c.mono ? 'mono' : 'sans', c.mono ? pt - 0.4 : pt) + (chip ? 3.4 : 0)); }
    return m + 2 * pad + 0.8; /* a little slack: a column measured to the exact width of its text can still wrap it */
  });
  const hdrMin = cols.map((c) => Math.max(...c.label.split(/\s+/).map((s) => w.tw(s, 'bold', pt))) + 2 * pad);
  const fixed = cols.map((c) => c.align === 'r');
  const minW = nat.map((v, i) => (fixed[i] ? v : Math.max(hdrMin[i], Math.min(v, 17))));
  const wts = cols.map((c) => c.w ?? 1);
  let widths: number[];
  const sum = nat.reduce((a, b) => a + b, 0);
  if (sum <= w.cw) {
    /* every column keeps its natural width; the spare width goes to the text columns in proportion to their weights */
    const flex = cols.map((_, i) => !fixed[i]);
    const wsum = wts.reduce((a, x, i) => a + (flex[i] ? x : 0), 0);
    const spare = w.cw - sum;
    widths = nat.map((v, i) => v + (wsum > 0 ? (flex[i] ? (wts[i] / wsum) * spare : 0) : spare / n));
  } else {
    /* too wide: the widest text columns give way first (water filling), numbers and short columns keep their natural width */
    const flexIdx = cols.map((_, i) => i).filter((i) => !fixed[i]);
    const fixedSum = nat.reduce((a, v, i) => a + (fixed[i] ? v : 0), 0);
    let avail = w.cw - fixedSum;
    widths = [...nat];
    if (flexIdx.length && avail > 0) {
      const order = [...flexIdx].sort((x, y) => nat[x] - nat[y]);
      let left = order.length;
      for (const i of order) {
        if (nat[i] * left <= avail) { avail -= nat[i]; left -= 1; } else { const cap = avail / left; for (const j of order.slice(order.indexOf(i))) widths[j] = Math.max(cap, Math.min(nat[j], minW[j])); break; }
      }
    }
    const total = widths.reduce((a, b) => a + b, 0);
    if (total > w.cw + 0.01) widths = widths.map((v) => v * (w.cw / total));
  }
  return { widths };
}

function table(w: Writer, b: Extract<Block, { t: 'table' }>): void {
  const pt = 7.4; const pad = 1.6; const padY = 0.9; const lh = w.lh(pt);
  if (b.title) heading(w, { t: 'heading', level: 3, text: b.title, chips: b.chips });
  const { widths } = layoutColumns(w, b.columns, b.rows, pt);
  const xs = widths.reduce<number[]>((a, v, i) => [...a, i === 0 ? MARGIN : a[i - 1] + widths[i - 1]], []);
  const headLines = b.columns.map((c, i) => w.wrap(c.label, 'bold', pt, widths[i] - 2 * pad));
  const headH = Math.max(...headLines.map((l) => l.length)) * lh + 2 * padY;
  const drawHead = () => {
    w.pdf.setFillColor(BAR); w.pdf.rect(MARGIN, w.y, w.cw, headH, 'F');
    b.columns.forEach((c, i) => {
      headLines[i].forEach((l, k) => w.text(l, c.align === 'r' ? xs[i] + widths[i] - pad : xs[i] + pad, w.y + padY + k * lh, 'bold', pt, INK, c.align === 'r' ? 'right' : 'left'));
    });
    w.rule(w.y + headH, RULE, 0.3);
    w.y += headH;
  };
  const rowLines = (r: TableCell[]) => b.columns.map((c, i) => {
    const cell = r[i] ?? null; const t = cellText(cell);
    const chip = typeof cell === 'object' && cell !== null && 'chip' in cell && !!cell.chip;
    if (chip) return [t];
    return c.align === 'r' ? [t] : w.wrap(t, c.mono ? 'mono' : 'sans', c.mono ? pt - 0.4 : pt, widths[i] - 2 * pad);
  });
  const first = b.rows[0] ? rowLines(b.rows[0]) : [['']];
  w.ensure(headH + Math.max(...first.map((l) => l.length)) * lh + 2 * padY + 2);
  drawHead();
  for (const r of b.rows) {
    const ls = rowLines(r);
    const hh = Math.max(...ls.map((l) => l.length)) * lh + 2 * padY;
    if (w.y + hh > w.bottom) { w.newPage(w.cur as ReportDoc); drawHead(); }
    b.columns.forEach((c, i) => {
      const cell = r[i] ?? null;
      const x = xs[i];
      if (typeof cell === 'object' && cell !== null && 'chip' in cell && cell.chip) {
        w.chip({ word: cell.text, tone: cell.chip }, c.align === 'r' ? x + widths[i] - pad - (w.tw(cell.text, 'bold', 6.6) + 3.4) : x + pad, w.y + (hh - 3.7) / 2);
        return;
      }
      const bold = typeof cell === 'object' && cell !== null && !!cell.bold;
      ls[i].forEach((l, k) => w.text(l, c.align === 'r' ? x + widths[i] - pad : x + pad, w.y + padY + k * lh, bold ? 'bold' : c.mono ? 'mono' : 'sans', c.mono ? pt - 0.4 : pt, INK, c.align === 'r' ? 'right' : 'left'));
    });
    w.rule(w.y + hh, HAIR, 0.2);
    w.y += hh;
  }
  if (b.note) { w.y += 0.8; const ls = w.wrap(b.note, 'sans', 7, w.cw); w.ensure(ls.length * w.lh(7)); w.y += w.lines(ls, MARGIN, w.y, 'sans', 7, INK2); }
  w.y += 3.2;
}

/** Figure geometry in millimetres inside a column of width `colW`. */
function figureBox(f: FigureBlock, colW: number, maxH: number): { fw: number; fh: number } {
  let fw = Math.min(colW, f.width * PX); let fh = (fw * f.height) / f.width;
  if (fh > maxH) { fh = maxH; fw = (fh * f.width) / f.height; }
  return { fw, fh };
}

async function figure(w: Writer, deps: PdfDeps, f: FigureBlock, x: number, colW: number, measureOnly = false): Promise<number> {
  const titleH = w.lh(8.6) + 1;
  const capLines = f.caption ? w.wrap(f.caption, 'sans', 7, colW) : [];
  const capH = capLines.length ? capLines.length * w.lh(7) + 1 : 0;
  const maxH = w.bottom - w.top - titleH - capH - 4;
  const { fw, fh } = figureBox(f, colW, maxH);
  const total = titleH + fh + capH + 3;
  if (measureOnly) return total;
  w.text(f.title, x, w.y, 'bold', 8.6);
  if (f.chips?.length) { const tx = x + w.tw(f.title, 'bold', 8.6) + 3; w.chipRow(f.chips, tx, w.y + (w.lh(8.6) - 3.7) / 2, x + colW - tx); }
  await deps.drawSvg(w.pdf, pdfSvgText(f.svg), x, w.y + titleH, fw, fh);
  if (capLines.length) w.lines(capLines, x, w.y + titleH + fh + 1, 'sans', 7, INK2);
  return total;
}

/** The smallest part of a block that must sit on one page: what a heading in front of it has to stay with. */
async function minHeight(w: Writer, deps: PdfDeps, b: Block | undefined): Promise<number> {
  if (!b) return 0;
  switch (b.t) {
    case 'figure': return figure(w, deps, b, MARGIN, w.cw, true);
    case 'figures': { const gap = 5; const colW = (w.cw - gap * (b.items.length - 1)) / b.items.length; return Math.max(...await Promise.all(b.items.map((f) => figure(w, deps, f, MARGIN, colW, true)))); }
    case 'table': return (b.title ? 10 : 0) + 18;
    case 'tiles': return 18;
    case 'kv': return 10;
    case 'heading': return 26;
    default: return 12;
  }
}

async function renderBlock(w: Writer, deps: PdfDeps, b: Block, next?: Block): Promise<void> {
  switch (b.t) {
    case 'heading': heading(w, b, await minHeight(w, deps, next)); break;
    case 'para': para(w, b.text, !!b.muted); break;
    case 'note': noteBox(w, b.text); break;
    case 'chips': { w.ensure(6); const used = w.chipRow(b.chips, MARGIN, w.y, w.cw); w.y += used + 2.4; break; }
    case 'kv': kv(w, b.rows, !!b.mono); break;
    case 'tiles': tiles(w, b.tiles); break;
    case 'table': table(w, b); break;
    case 'figure': {
      const need = await figure(w, deps, b, MARGIN, w.cw, true);
      w.ensure(need);
      w.y += await figure(w, deps, b, MARGIN, w.cw);
      break;
    }
    case 'figures': {
      const gap = 5; const per = b.items.length; const colW = (w.cw - gap * (per - 1)) / per;
      const need = Math.max(...await Promise.all(b.items.map((f) => figure(w, deps, f, MARGIN, colW, true))));
      w.ensure(need);
      let used = 0;
      for (let i = 0; i < per; i++) used = Math.max(used, await figure(w, deps, b.items[i], MARGIN + i * (colW + gap), colW));
      w.y += used;
      break;
    }
    case 'pagebreak': w.newPage(w.cur as ReportDoc); break;
    default: break;
  }
}

function cover(w: Writer, d: ReportDoc, deps: PdfDeps): void {
  w.y += 3;
  const tl = w.wrap(d.title, 'bold', 17, w.cw);
  w.lines(tl, MARGIN, w.y, 'bold', 17);
  w.y += tl.length * w.lh(17) + 1.6;
  if (d.chips.length) { w.y += w.chipRow(d.chips, MARGIN, w.y, w.cw) + 2.6; }
  if (d.labelText) {
    const ls = w.wrap(d.labelText, 'sans', 8, w.cw - 6);
    const hh = ls.length * w.lh(8) + 3.4;
    w.pdf.setFillColor(PANEL); w.pdf.rect(MARGIN, w.y, w.cw, hh, 'F');
    w.pdf.setFillColor('#1f5f9a'); w.pdf.rect(MARGIN, w.y, 0.8, hh, 'F');
    w.lines(ls, MARGIN + 3, w.y + 1.7, 'sans', 8);
    w.y += hh + 3;
  }
  const meta: Array<[string, string]> = [...d.meta];
  if (deps.generated) meta.push(['Generated', deps.generated]);
  if (meta.length) kv(w, meta, false);
  w.rule(w.y, INK, 0.4); w.y += 3;
}

/** Page numbers of the sections of a pack and a flowing contents page. */
function contents(w: Writer, docs: readonly ReportDoc[], starts: readonly number[], packTitle: string, deps: PdfDeps): void {
  w.y += 3;
  w.text(packTitle, MARGIN, w.y, 'bold', 17); w.y += w.lh(17) + 3;
  if (deps.generated) { w.text(`Generated ${deps.generated}`, MARGIN, w.y, 'sans', 7.6, INK2); w.y += w.lh(7.6) + 2; }
  w.rule(w.y, INK, 0.4); w.y += 3;
  docs.forEach((d, i) => {
    w.ensure(8);
    w.text(String(i + 1), MARGIN, w.y, 'mono', 8, INK2);
    w.text(d.title, MARGIN + 8, w.y, 'bold', 9);
    const tx = MARGIN + 8 + w.tw(d.title, 'bold', 9) + 3;
    w.text(d.windowTitle, tx, w.y, 'sans', 7.6, INK2);
    const cx = tx + w.tw(d.windowTitle, 'sans', 7.6) + 3;
    w.chip(d.chips[0] ?? { word: d.label, tone: 'info' }, cx, w.y + 0.7);
    w.text(`Page ${starts[i]}`, w.w - MARGIN, w.y, 'mono', 7.6, INK2, 'right');
    w.rule(w.y + 5.4, HAIR);
    w.y += 7;
  });
}

function chrome(w: Writer, pages: PageRec[], deps: PdfDeps, packLabel: string | null): void {
  void deps;
  const total = pages.length;
  pages.forEach((p, i) => {
    w.pdf.setPage(i + 1);
    w.w = p.w; w.h = p.h;
    const d = p.doc;
    w.text(PRODUCT, MARGIN, 7.4, 'bold', 7.6, INK);
    w.text(`${d.windowTitle}  ·  ${REPORT_KIND}`, w.w - MARGIN, 7.4, 'sans', 7.6, INK2, 'right');
    w.rule(13.2, RULE, 0.25);
    const fy = w.h - FOOT_H + 2;
    w.rule(fy, RULE, 0.25);
    const parts = packLabel && d.id === 'contents' ? [packLabel] : footerParts(d);
    const ls = w.wrap(parts.join('  ·  '), 'mono', 6.4, w.w - 2 * MARGIN - 26).slice(0, 2);
    w.lines(ls, MARGIN, fy + 0.8, 'mono', 6.4, INK2);
    w.text(`Page ${i + 1} of ${total}`, w.w - MARGIN, fy + 0.8, 'mono', 6.4, INK2, 'right');
  });
}

export async function renderPdf(docs: readonly ReportDoc[], deps: PdfDeps, o: PdfOptions = {}): Promise<PdfResult> {
  if (docs.length === 0) throw new Error('empty');
  const w = new Writer(deps);
  const pack = docs.length > 1;
  const packTitle = o.packTitle ?? 'Run pack';
  const starts: number[] = [];
  let tocDoc: ReportDoc | null = null;
  if (pack) {
    tocDoc = { id: 'contents', title: packTitle, windowTitle: 'Contents', orientation: 'portrait', label: 'Snapshot', chips: [], source: packTitle, meta: [], blocks: [] };
    w.newPage(tocDoc);
  }
  for (const d of docs) {
    w.newPage(d);
    starts.push(w.pages.length);
    cover(w, d, deps);
    for (let i = 0; i < d.blocks.length; i++) await renderBlock(w, deps, d.blocks[i], d.blocks[i + 1]);
  }
  if (pack && tocDoc) {
    w.pdf.setPage(1); w.w = 210; w.h = 297; w.cur = tocDoc; w.y = HEAD_H;
    contents(w, docs, starts, packTitle, deps);
  }
  chrome(w, w.pages, deps, pack ? `${packTitle}  ·  ${docs.length} reports` : null);
  w.pdf.setProperties({
    title: pack ? packTitle : docs[0].title, subject: `${PRODUCT} ${REPORT_KIND}`, author: PRODUCT, creator: PRODUCT,
    keywords: [...new Set(docs.map((d) => d.label))].join(', '),
  });
  if (pack) {
    const outline = (w.pdf as unknown as { outline?: { add: (parent: unknown, title: string, opts: { pageNumber: number }) => unknown } }).outline;
    docs.forEach((d, i) => outline?.add(null, pdfText(d.title), { pageNumber: starts[i] }));
  }
  return { bytes: w.pdf.output('arraybuffer'), pages: w.pages.length, starts };
}
