/* Client-side "Save as PDF": a self-contained HTML document is made printable (light theme, A4, no chrome, page-break
 * rules) and printed from our own code, so the browser's Save as PDF writes the file. No server renderer is involved.
 *
 * The document is cleaned first (scripts, event handlers, javascript: URLs, embeds and <base> are removed and
 * a CSP forbids scripts), then shown in a hidden iframe whose sandbox has no allow-scripts: nothing inside the document
 * can run. window.print() is called here, on the iframe's window, never by the document. */

export const PRINT_STYLE_ATTR = 'data-sm-print';

/** Light theme regardless of the app theme; Modelled / Recorded / Simulated chips and provenance footers stay visible. */
export const PRINT_CSS = [
  ':root{color-scheme:light;--bg:#fff;--paper:#fff;--fg:#000;--fg2:#222;--muted:#4a525a;--rule:#9aa3ab;--rule2:#d5d9dd;--card:#fff;--strip:#f2f4f6;',
  '--accent:#1d4f91;--input:#44505c;--modelled:#4b43a8;--simulated:#8f5300;--sourced:#16703f;--snapshot:#245a9a;--live:#16703f;--notserved:#68717b;--recorded:#8f5300}',
  'html,body{background:#fff@I;color:#000}',
  '*{-webkit-print-color-adjust:exact;print-color-adjust:exact}',
  '@page{size:A4;margin:14mm 13mm 17mm}',
  '@media print{',
  'html,body{margin:0}main,.note,.sheet{max-width:none@I;padding:0@I;margin:0@I;box-shadow:none@I}',
  'nav,.printbar,.no-print,[data-no-print],button,[role=toolbar],.toolbar{display:none@I}',
  'section,.sec,figure,.fig,blockquote{break-inside:avoid;page-break-inside:avoid}',
  'h1,h2,h3,h4{break-after:avoid;page-break-after:avoid}',
  'table{break-inside:auto;border-collapse:collapse}thead{display:table-header-group}tfoot{display:table-footer-group}',
  'tr,img,svg,.chip,.tag,[data-label],[data-prov]{break-inside:avoid;page-break-inside:avoid}',
  'img,svg{max-width:100%;height:auto}',
  'a{color:inherit;text-decoration:none}',
  '.chip,.tag,[data-label],[data-prov]{display:inline-block}',
  'footer,.foot{break-inside:avoid;page-break-inside:avoid}',
  '}',
].join('\n').replace(/@I/g, `${String.fromCharCode(33)}important`);

const CSP = "default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; font-src data:; script-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";

const cssString = (s: string): string => `"${s.replace(/[\\"<>\n\r]/g, ' ').slice(0, 120)}"`;

/** The page-box footer: document label on the left, page count on the right. */
export function footerCss(label: string): string {
  return `@page{@bottom-left{content:${cssString(label)};font:7.5pt ui-monospace,Menlo,monospace;color:#4a525a}@bottom-right{content:counter(page) " / " counter(pages);font:7.5pt ui-monospace,Menlo,monospace;color:#4a525a}}`;
}

const BLOCK_TAGS = ['script', 'iframe', 'frame', 'frameset', 'object', 'embed', 'applet', 'noscript', 'form'];
const VOID_TAGS = ['base', 'meta\\s+http-equiv', 'link\\s+[^>]*rel\\s*=\\s*["\']?import'];

/** Removes executable and embedding content. The CSP and the sandbox are what guarantee nothing runs; this keeps the markup clean. */
export function stripActive(html: string): string {
  let out = html;
  for (const t of BLOCK_TAGS) out = out.replace(new RegExp(`<${t}\\b[\\s\\S]*?<\\/${t}\\s*>`, 'gi'), '').replace(new RegExp(`<\\/?${t}\\b[^>]*>`, 'gi'), '');
  for (const t of VOID_TAGS) out = out.replace(new RegExp(`<${t}\\b[^>]*>`, 'gi'), '');
  out = out.replace(/(<[a-z][^>]*?)\s+on[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, '$1');
  /* repeat once: adjacent handlers share a boundary */
  out = out.replace(/(<[a-z][^>]*?)\s+on[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, '$1');
  out = out.replace(/\b(href|src|action|formaction|xlink:href)\s*=\s*(["']?)\s*(?:javascript|vbscript|data:text\/html)[^"'>\s]*\2/gi, '$1="#"');
  return out;
}

const titleOf = (html: string): string => (/<title[^>]*>([\s\S]*?)<\/title>/i.exec(html)?.[1] ?? '').replace(/<[^>]*>/g, '').replace(/&amp;/g, '&').trim();

/** Cleans a document, injects the CSP and the print stylesheet once, and returns the HTML to print. */
export function preparePrintHtml(html: string, label?: string): string {
  let out = stripActive(html);
  const meta = `<meta http-equiv="Content-Security-Policy" content="${CSP}">`;
  const name = (label ?? titleOf(out)).trim();
  const style = out.includes(`${PRINT_STYLE_ATTR}`) ? '' : `<style ${PRINT_STYLE_ATTR}>${PRINT_CSS}${name ? `\n${footerCss(name)}` : ''}</style>`;
  if (/<head\b[^>]*>/i.test(out)) out = out.replace(/<head\b[^>]*>/i, (m) => `${m}${meta}`);
  else if (/<html\b[^>]*>/i.test(out)) out = out.replace(/<html\b[^>]*>/i, (m) => `${m}<head>${meta}</head>`);
  else out = `<!doctype html><html><head>${meta}<meta charset="utf-8"></head><body>${out}</body></html>`;
  if (style) out = /<\/head\s*>/i.test(out) ? out.replace(/<\/head\s*>/i, () => `${style}</head>`) : out.replace(/<body\b[^>]*>/i, (m) => `${m}${style}`);
  return out;
}

export interface PrintOptions {
  /** Footer label; defaults to the document title. */
  label?: string;
  /** Document that hosts the print frame; defaults to the page. */
  doc?: Document;
  /** Milliseconds before the frame and blob URL are released. */
  keepMs?: number;
}

/** Builds the printable blob URL for a document. Caller revokes it. */
export function printableBlobUrl(html: string, label?: string): string {
  return URL.createObjectURL(new Blob([preparePrintHtml(html, label)], { type: 'text/html;charset=utf-8' }));
}

/** Prints an HTML document through the browser (Save as PDF). Returns false when nothing could be started. */
export function printHtmlAsPdf(html: string, opts: PrintOptions = {}): boolean {
  const host = opts.doc ?? document;
  if (!html || !host.body) return false;
  let url: string;
  try { url = printableBlobUrl(html, opts.label); } catch { return false; }
  const frame = host.createElement('iframe');
  frame.setAttribute('sandbox', 'allow-same-origin allow-modals');
  frame.setAttribute('aria-hidden', 'true');
  frame.setAttribute('tabindex', '-1');
  frame.setAttribute('data-print-frame', '');
  frame.style.cssText = 'position:fixed;left:-9999px;top:0;width:210mm;height:297mm;border:0;visibility:hidden';
  let done = false;
  const cleanup = () => { if (done) return; done = true; frame.remove(); URL.revokeObjectURL(url); };
  frame.addEventListener('load', () => {
    const w = frame.contentWindow;
    if (!w) { cleanup(); return; }
    try { w.addEventListener('afterprint', () => setTimeout(cleanup, 0)); w.focus(); w.print(); } catch { cleanup(); }
  }, { once: true });
  frame.src = url;
  host.body.appendChild(frame);
  setTimeout(cleanup, opts.keepMs ?? 300000);
  return true;
}

/** Wording for every Save as PDF control. */
export const SAVE_PDF_LABEL = 'Save as PDF';
export const SAVE_PDF_TIP = 'Browser print';
