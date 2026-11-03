/* Browser side of the PDF path: loads jsPDF, svg2pdf.js and the fonts on first use (this module is itself a dynamic
 * import, so none of it is in the main bundle) and draws a figure's SVG as vector paths. */
import type { jsPDF as JsPdf } from 'jspdf';
import { printHtmlAsPdf } from '../printPdf';
import { exportClock } from '../header';
import { loadHtmlFontCss, loadPdfFonts } from './fonts';
import { reportsToHtml } from './html';
import type { ReportDoc } from './model';
import { renderPdf, type PdfDeps, type PdfResult } from './pdf';

const stripProlog = (s: string): string => s.replace(/^<\?xml[^>]*\?>\s*/, '');

/** Parse an SVG string without running anything, drop active content, and return the element. */
export function parseSvg(svg: string): SVGSVGElement {
  const doc = new DOMParser().parseFromString(stripProlog(svg), 'image/svg+xml');
  const root = doc.documentElement;
  if (!root || root.nodeName !== 'svg' || doc.querySelector('parsererror')) throw new Error('svg_unreadable');
  doc.querySelectorAll('script,foreignObject,iframe,object,embed,image,use').forEach((n) => n.remove());
  doc.querySelectorAll('*').forEach((n) => { for (const a of [...n.attributes]) if (/^on/i.test(a.name) || /^\s*(javascript|data):/i.test(a.value)) n.removeAttribute(a.name); });
  return document.importNode(root, true) as unknown as SVGSVGElement;
}

export async function loadPdfDeps(): Promise<PdfDeps> {
  const [{ jsPDF }, { svg2pdf }, fonts] = await Promise.all([import('jspdf'), import('svg2pdf.js'), loadPdfFonts()]);
  const drawSvg = async (pdf: JsPdf, svg: string, x: number, y: number, w: number, h: number): Promise<void> => {
    const host = document.createElement('div');
    host.setAttribute('aria-hidden', 'true');
    host.style.cssText = 'position:fixed;left:-99999px;top:0;width:0;height:0;overflow:hidden;visibility:hidden';
    const el = parseSvg(svg);
    host.appendChild(el);
    document.body.appendChild(host);
    try { await svg2pdf(el, pdf, { x, y, width: w, height: h }); } finally { host.remove(); }
  };
  return { jsPDF, fonts, drawSvg, generated: exportClock.now().replace(/\.\d+Z$/, 'Z').replace('T', ' ') };
}

export async function renderReportsPdf(docs: readonly ReportDoc[], o: { packTitle?: string } = {}): Promise<PdfResult & { blob: Blob }> {
  const r = await renderPdf(docs, await loadPdfDeps(), o);
  return { ...r, blob: new Blob([r.bytes], { type: 'application/pdf' }) };
}

/** The browser's own print path (Save as PDF): the same documents as HTML, shown in the sandboxed print frame. */
export async function printReports(docs: readonly ReportDoc[], o: { packTitle?: string } = {}): Promise<boolean> {
  let fontCss = '';
  try { fontCss = await loadHtmlFontCss(); } catch { /* the installed fonts or the system face are used */ }
  const html = reportsToHtml(docs, { fontCss, packTitle: o.packTitle });
  return printHtmlAsPdf(html, { label: docs.length > 1 ? (o.packTitle ?? 'Run pack') : docs[0].title });
}
