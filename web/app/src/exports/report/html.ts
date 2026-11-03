/* HTML renderer of a ReportDoc (or a pack of them): the print path. A self-contained document, light skin whatever the
 * app theme, A4, inline vector SVG, selectable text, page-break rules, no scripts and no UI chrome. It is shown in the
 * sandboxed print frame of printPdf.ts, which also forbids scripts through a content security policy. */
import { escHtml } from '../header';
import { footerParts, PRODUCT, REPORT_KIND, cellText, type Block, type ChipTone, type FigureBlock, type ReportChip, type ReportDoc, type TableCell } from './model';
import { TONE } from './tokens';

export const REPORT_HTML_MARK = 'data-sm-print';

const chipHtml = (c: ReportChip): string => `<span class="rp-chip rp-chip--${c.tone}" data-label="${escHtml(c.word)}">${escHtml(c.word)}</span>`;
const chipsHtml = (cs: readonly ReportChip[] | undefined): string => (cs && cs.length ? `<span class="rp-chips">${cs.map(chipHtml).join('')}</span>` : '');

const stripProlog = (svg: string): string => svg.replace(/^<\?xml[^>]*\?>\s*/, '');

function cellHtml(c: TableCell, right: boolean, mono: boolean): string {
  const cls = [right ? 'n' : '', mono ? 'm' : ''].filter(Boolean).join(' ');
  const inner = typeof c === 'object' && c !== null
    ? (c.chip ? chipHtml({ word: c.text, tone: c.chip }) : c.bold ? `<b>${escHtml(c.text)}</b>` : escHtml(c.text))
    : escHtml(cellText(c));
  return `<td${cls ? ` class="${cls}"` : ''}>${inner}</td>`;
}

function figureHtml(f: FigureBlock): string {
  return `<figure class="rp-fig"><figcaption class="rp-figh"><span class="rp-figt">${escHtml(f.title)}</span>${chipsHtml(f.chips)}</figcaption>`
    + `<div class="rp-svg">${stripProlog(f.svg)}</div>${f.caption ? `<p class="rp-cap">${escHtml(f.caption)}</p>` : ''}</figure>`;
}

export function blockHtml(b: Block): string {
  switch (b.t) {
    case 'heading': return `<h${b.level + 1} class="rp-h rp-h${b.level}">${escHtml(b.text)}${chipsHtml(b.chips)}</h${b.level + 1}>`;
    case 'para': return `<p class="rp-p${b.muted ? ' rp-muted' : ''}">${escHtml(b.text)}</p>`;
    case 'note': return `<p class="rp-note rp-note--${b.tone ?? 'info'}">${escHtml(b.text)}</p>`;
    case 'chips': return `<div class="rp-chiprow">${b.chips.map(chipHtml).join('')}</div>`;
    case 'kv': return `<dl class="rp-kv${b.mono ? ' rp-mono' : ''}">${b.rows.map(([k, v]) => `<div><dt>${escHtml(k)}</dt><dd>${escHtml(v)}</dd></div>`).join('')}</dl>`;
    case 'tiles': return `<div class="rp-tiles">${b.tiles.map((t) => `<div class="rp-tile"><span class="rp-tl">${escHtml(t.label)}</span><span class="rp-tv">${escHtml(t.value)}</span>`
      + `${t.sub ? `<span class="rp-ts">${escHtml(t.sub)}</span>` : ''}${t.chip ? chipHtml(t.chip) : ''}</div>`).join('')}</div>`;
    case 'table': {
      const head = `<tr>${b.columns.map((c) => `<th scope="col"${c.align === 'r' ? ' class="n"' : ''}>${escHtml(c.label)}</th>`).join('')}</tr>`;
      const rows = b.rows.map((r) => `<tr>${b.columns.map((c, i) => cellHtml(r[i] ?? null, c.align === 'r', !!c.mono)).join('')}</tr>`).join('');
      return `<section class="rp-tblwrap">${b.title ? `<h4 class="rp-h rp-h3">${escHtml(b.title)}${chipsHtml(b.chips)}</h4>` : ''}`
        + `<table class="rp-table"><thead>${head}</thead><tbody>${rows}</tbody></table>${b.note ? `<p class="rp-cap">${escHtml(b.note)}</p>` : ''}</section>`;
    }
    case 'figure': return figureHtml(b);
    case 'figures': return `<div class="rp-figs rp-figs--${b.items.length}">${b.items.map(figureHtml).join('')}</div>`;
    case 'pagebreak': return '<div class="rp-break"></div>';
    default: return '';
  }
}

const cssStr = (s: string): string => `"${s.replace(/[\\"<>\n\r]/g, ' ').slice(0, 220)}"`;

function pageCss(d: ReportDoc, name: string | null): string {
  const size = d.orientation === 'landscape' ? 'A4 landscape' : 'A4';
  const foot = `${footerParts(d).join('  ·  ')}`;
  const sel = name ? `@page ${name}` : '@page';
  const mono = 'font:7pt "IBM Plex Mono",ui-monospace,Menlo,monospace;color:#4a5158';
  const sans = 'font:7.5pt "IBM Plex Sans",system-ui,sans-serif;color:#4a5158';
  return `${sel}{size:${size};margin:19mm 14mm 17mm;`
    + `@top-left{content:${cssStr(PRODUCT)};${sans};font-weight:600}`
    + `@top-right{content:${cssStr(`${d.windowTitle}  ·  ${REPORT_KIND}`)};${sans}}`
    + `@bottom-left{content:${cssStr(foot)};${mono};white-space:pre-wrap;width:80%}`
    + `@bottom-right{content:"Page " counter(page) " of " counter(pages);${mono}}}`;
}

export const REPORT_CSS = [
  ':root{color-scheme:light}',
  '*{box-sizing:border-box;-webkit-print-color-adjust:exact;print-color-adjust:exact}',
  'html,body{margin:0;background:#fff;color:#1b1d1f}',
  'body{font:8.6pt/1.4 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;font-variant-numeric:tabular-nums}',
  '.rp-doc{display:block;break-before:page;page-break-before:always}.rp-doc:first-of-type{break-before:auto;page-break-before:auto}',
  '.rp-cover{padding-bottom:3mm;margin-bottom:4mm;border-bottom:0.4mm solid #1b1d1f}',
  '.rp-prod{font-size:7.5pt;letter-spacing:.06em;text-transform:uppercase;color:#4a5158;margin:0 0 1.5mm}',
  '.rp-title{font-size:17pt;line-height:1.2;font-weight:600;margin:0 0 2.5mm}',
  '.rp-chips{display:inline-flex;flex-wrap:wrap;gap:1.2mm;margin-left:2mm;vertical-align:middle}',
  '.rp-chiprow{display:flex;flex-wrap:wrap;gap:1.5mm;margin:0 0 2.5mm}',
  '.rp-chip{display:inline-block;padding:0.2mm 1.8mm;border:0.25mm solid;border-radius:1mm;font-size:7pt;font-weight:600;line-height:1.35;white-space:nowrap;break-inside:avoid}',
  ...Object.entries(TONE).map(([k, t]) => `.rp-chip--${k}{color:${t.fg};background:${t.bg};border-color:${t.line}}`),
  '.rp-kv{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 6mm;margin:0 0 3mm;font-size:8pt}',
  '.rp-kv div{display:flex;gap:2mm;border-bottom:0.2mm solid #dfe2e5;padding:0.8mm 0;break-inside:avoid}',
  '.rp-kv dt{flex:0 0 32%;color:#4a5158;margin:0}.rp-kv dd{margin:0;min-width:0;overflow-wrap:anywhere}',
  '.rp-mono dd,.rp-m,.m{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-size:7.4pt}',
  '.rp-label{margin:0 0 3mm;padding:1.6mm 2.4mm;border-left:0.8mm solid #1f5f9a;background:#f1f2f4;font-size:8pt;break-inside:avoid}',
  '.rp-h{break-after:avoid;page-break-after:avoid;font-weight:600;margin:5mm 0 2mm;line-height:1.25}',
  '.rp-h1{font-size:12pt;border-bottom:0.25mm solid #8d939a;padding-bottom:1mm}.rp-h2{font-size:10pt}.rp-h3{font-size:9pt;margin-top:3mm}',
  '.rp-p{margin:0 0 2mm}.rp-muted{color:#4a5158}',
  '.rp-note{margin:0 0 2.5mm;padding:1.2mm 2.4mm;border:0.25mm solid #c3c7cc;background:#f5f6f7;font-size:8pt;break-inside:avoid}',
  '.rp-tiles{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:2mm;margin:0 0 3mm}',
  '.rp-tile{border:0.25mm solid #c3c7cc;padding:1.8mm 2.2mm;display:flex;flex-direction:column;gap:0.8mm;break-inside:avoid}',
  '.rp-tl{font-size:7pt;color:#4a5158}.rp-tv{font-size:12pt;font-weight:600}.rp-ts{font-size:7pt;color:#4a5158}',
  '.rp-fig{margin:0 0 4mm;break-inside:avoid;page-break-inside:avoid}',
  '.rp-figh{display:flex;align-items:center;flex-wrap:wrap;gap:1.5mm;margin:0 0 1mm;font-weight:600;font-size:8.6pt;break-after:avoid}',
  '.rp-figh .rp-chips{margin-left:0}',
  '.rp-svg svg{display:block;width:100%;height:auto;max-width:100%}',
  '.rp-cap{margin:1mm 0 0;font-size:7.2pt;color:#4a5158}',
  '.rp-figs{display:grid;gap:4mm;margin:0 0 3mm}.rp-figs--2{grid-template-columns:repeat(2,minmax(0,1fr))}.rp-figs--3{grid-template-columns:repeat(3,minmax(0,1fr))}',
  '.rp-tblwrap{margin:0 0 4mm}',
  '.rp-table{width:100%;border-collapse:collapse;font-size:7.6pt;table-layout:auto}',
  '.rp-table th{background:#eceef0;text-align:left;font-weight:600;padding:1mm 1.6mm;border-bottom:0.3mm solid #8d939a;vertical-align:bottom}',
  '.rp-table td{padding:0.9mm 1.6mm;border-bottom:0.2mm solid #dfe2e5;vertical-align:top;overflow-wrap:anywhere}',
  '.rp-table .n{text-align:right;white-space:nowrap}',
  '.rp-table thead{display:table-header-group}.rp-table tr{break-inside:avoid;page-break-inside:avoid}',
  '.rp-toc{margin:0 0 4mm;padding:0;list-style:none}.rp-toc li{display:flex;gap:2mm;align-items:center;padding:1.2mm 0;border-bottom:0.2mm solid #dfe2e5;break-inside:avoid}',
  '.rp-toc .t{flex:1 1 auto;font-weight:600}.rp-toc .w{color:#4a5158;font-size:7.5pt}',
  '.rp-break{break-after:page;page-break-after:always;height:0}',
  'a{color:inherit;text-decoration:none}',
].join('\n');

function docHtml(d: ReportDoc, idx: number, named: boolean): string {
  const cover = `<header class="rp-cover"><p class="rp-prod">${escHtml(PRODUCT)} · ${escHtml(d.windowTitle)} · ${escHtml(REPORT_KIND)}</p>`
    + `<h1 class="rp-title">${escHtml(d.title)}</h1>${d.chips.length ? `<div class="rp-chiprow">${d.chips.map(chipHtml).join('')}</div>` : ''}`
    + `${d.labelText ? `<p class="rp-label" data-label-text>${escHtml(d.labelText)}</p>` : ''}`
    + `${d.meta.length ? `<dl class="rp-kv">${d.meta.map(([k, v]) => `<div><dt>${escHtml(k)}</dt><dd>${escHtml(v)}</dd></div>`).join('')}</dl>` : ''}</header>`;
  const page = named ? ` style="page:d${idx}"` : '';
  return `<article class="rp-doc rp-${d.orientation}" data-report="${escHtml(d.id)}"${page}>${cover}${d.blocks.map(blockHtml).join('')}</article>`;
}

export interface HtmlOptions {
  /** @font-face rules with data: URLs, when the fonts are not installed (browser path) */
  fontCss?: string;
  /** title of a pack; ignored for a single document */
  packTitle?: string;
}

/** One document, or a pack of documents with a contents page first. */
export function reportsToHtml(docs: readonly ReportDoc[], o: HtmlOptions = {}): string {
  if (docs.length === 0) return '';
  const pack = docs.length > 1;
  const title = pack ? (o.packTitle ?? 'Run pack') : docs[0].title;
  const named = docs.some((d) => d.orientation !== docs[0].orientation) || pack;
  const css = [o.fontCss ?? '', REPORT_CSS];
  if (!named) css.push(pageCss(docs[0], null));
  else {
    const first = docs[0];
    css.push(pageCss({ ...first, windowTitle: pack ? 'Contents' : first.windowTitle, source: pack ? 'Run pack' : first.source }, null));
    docs.forEach((d, i) => css.push(pageCss(d, `d${i}`)));
  }
  const toc = pack
    ? `<article class="rp-doc rp-portrait" data-report="contents"><header class="rp-cover"><p class="rp-prod">${escHtml(PRODUCT)} · ${escHtml(REPORT_KIND)}</p><h1 class="rp-title">${escHtml(title)}</h1></header>`
      + `<ol class="rp-toc">${docs.map((d, i) => `<li><span class="m">${i + 1}</span><span class="t">${escHtml(d.title)}</span><span class="w">${escHtml(d.windowTitle)}</span>${chipHtml({ word: d.label, tone: d.chips[0]?.tone ?? 'info' })}</li>`).join('')}</ol></article>`
    : '';
  const body = pack ? `${toc}${docs.map((d, i) => docHtml(d, i, named)).join('')}` : docHtml(docs[0], 0, false);
  return `<!doctype html><html lang="en" ${REPORT_HTML_MARK}><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escHtml(title)}</title>`
    + `<style ${REPORT_HTML_MARK}>${css.join('\n')}</style></head><body>${body}</body></html>\n`;
}

export const reportToHtml = (d: ReportDoc, o: HtmlOptions = {}): string => reportsToHtml([d], o);
