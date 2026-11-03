/* The report fonts: IBM Plex Sans (400, 600) and IBM Plex Mono (400), the files the app already ships, as woff2 for the
 * print document and as TrueType (converted, unchanged outlines) for the PDF. Fetched on first use, then kept. */
import sans400Woff from '../../assets/fonts/ibm-plex-sans-latin-400-normal.woff2?url';
import sans600Woff from '../../assets/fonts/ibm-plex-sans-latin-600-normal.woff2?url';
import mono400Woff from '../../assets/fonts/ibm-plex-mono-latin-400-normal.woff2?url';
import sans400Ttf from '../../assets/fonts/pdf/ibm-plex-sans-latin-400-normal.ttf?url';
import sans600Ttf from '../../assets/fonts/pdf/ibm-plex-sans-latin-600-normal.ttf?url';
import mono400Ttf from '../../assets/fonts/pdf/ibm-plex-mono-latin-400-normal.ttf?url';
import type { PdfFonts } from './pdf';

export function bytesToBase64(b: Uint8Array): string {
  let s = '';
  for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode(...b.subarray(i, i + 0x8000));
  return btoa(s);
}

async function fetchBase64(url: string): Promise<string> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`font_${r.status}`);
  return bytesToBase64(new Uint8Array(await r.arrayBuffer()));
}

let pdfFonts: Promise<PdfFonts> | null = null;
export function loadPdfFonts(): Promise<PdfFonts> {
  if (!pdfFonts) {
    pdfFonts = Promise.all([fetchBase64(sans400Ttf), fetchBase64(sans600Ttf), fetchBase64(mono400Ttf)]).then(([sans, bold, mono]) => ({ sans, bold, mono }));
    pdfFonts.catch(() => { pdfFonts = null; });
  }
  return pdfFonts;
}

let htmlFonts: Promise<string> | null = null;
/** @font-face rules with data: URLs, so the sandboxed print frame renders the same type as the app (its CSP allows font data only). */
export function loadHtmlFontCss(): Promise<string> {
  if (!htmlFonts) {
    const face = (family: string, weight: number, b64: string) => `@font-face{font-family:"${family}";font-weight:${weight};font-style:normal;src:url(data:font/woff2;base64,${b64}) format("woff2")}`;
    htmlFonts = Promise.all([fetchBase64(sans400Woff), fetchBase64(sans600Woff), fetchBase64(mono400Woff)])
      .then(([a, b, c]) => [face('IBM Plex Sans', 400, a), face('IBM Plex Sans', 600, b), face('IBM Plex Mono', 400, c)].join('\n'));
    htmlFonts.catch(() => { htmlFonts = null; });
  }
  return htmlFonts;
}
