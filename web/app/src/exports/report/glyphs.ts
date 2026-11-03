/* The embedded report fonts are the Latin subsets of IBM Plex (the same files the app ships). A character outside the
 * subset would print as an empty box, so text written to a PDF is mapped to what the fonts hold. */
const ALLOWED: Array<[number, number]> = [
  [0x20, 0x7e], [0xa0, 0xff], [0x131, 0x131], [0x152, 0x153], [0x2013, 0x2014], [0x2018, 0x201a], [0x201c, 0x201e], [0x2022, 0x2022], [0x2026, 0x2026],
  [0x2032, 0x2033], [0x2039, 0x203a], [0x2044, 0x2044], [0x20ac, 0x20ac], [0x2122, 0x2122], [0x2191, 0x2191], [0x2193, 0x2193], [0x2212, 0x2212],
];
const MAP: Record<string, string> = {
  'ρ': 'rho', '≈': '~', '≤': '<=', '≥': '>=', '→': 'to',
  '\u2009': ' ', '\u202f': ' ', '\u2002': ' ', '\u200b': '', '\t': ' ', '\r': '', '\n': ' ',
  '‑': '-', '‒': '-', 'ł': 'l', 'Ł': 'L', 'đ': 'd', 'Đ': 'D', 'ı': 'i',
};
/** Status and mark glyphs of the chart kit, by code point: they have no place in a PDF that carries the word beside them. */
const DROP_OR_WORD = new Map<number, string>([[0x2190, 'from'], [0x2260, '!='], [0x25c7, ''], [0x25cf, ''], [0x25f7, ''], [0x2298, ''], [0x25c6, ''], [0x25a4, ''], [0x25a3, ''], [0x25a0, ''], [0x2715, 'x']]);
const ok = (cp: number): boolean => ALLOWED.some(([a, b]) => cp >= a && cp <= b);

/** Text as the report fonts can print it. */
export function pdfText(s: string): string {
  let out = '';
  for (const ch of s) {
    if (ch in MAP) { out += MAP[ch]; continue; }
    const w = DROP_OR_WORD.get(ch.codePointAt(0) as number);
    if (w !== undefined) { out += w; continue; }
    if (ok(ch.codePointAt(0) as number)) { out += ch; continue; }
    /* a letter the subset lacks falls back to its base letter (n for ń) */
    const base = ch.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    out += base !== ch && [...base].every((c) => ok(c.codePointAt(0) as number)) ? base : '?';
  }
  return out;
}

const unescapeXml = (s: string): string => s.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&amp;/g, '&');
const escapeXml = (s: string): string => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/** The PDF fonts have two weights per family (regular and bold): numeric weights are folded onto them, or the figure's text would fall back to a default face. */
export function pdfFontWeights(svg: string): string {
  return svg.replace(/font-weight="(\d+|bold|bolder|normal|lighter)"/g, (_m, v: string) => {
    const n = Number(v);
    const bold = Number.isFinite(n) ? n >= 600 : v === 'bold' || v === 'bolder';
    return `font-weight="${bold ? 'bold' : 'normal'}"`;
  });
}

/** The same mapping applied to the text nodes of an SVG string, and the weights folded onto the embedded faces. */
export function pdfSvgText(svg: string): string {
  return pdfFontWeights(svg.replace(/>([^<>]+)</g, (_m, t: string) => `>${escapeXml(pdfText(unescapeXml(t)))}<`));
}
