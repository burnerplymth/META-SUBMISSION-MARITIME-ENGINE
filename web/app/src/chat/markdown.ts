/**
 * SAFE MARKDOWN for chat answers: paragraphs, lists, bold, inline code,
 * tables, links and numbered citation markers. Text in, a small AST out. The
 * renderer (Markdown.tsx) builds React text nodes from the AST, so there is no
 * HTML path at all: markup in a model answer, a web page or a tool result is
 * shown as the characters it is. Links survive only as plain http(s) URLs
 * without credentials, shown with their domain; anything else (javascript:,
 * data:, file:) collapses to its label text.
 */

export type Inline =
  | { t: 'text'; s: string }
  | { t: 'bold'; c: Inline[] }
  | { t: 'code'; s: string }
  | { t: 'cite'; n: number }
  | { t: 'link'; text: string; href: string; domain: string };

export type Block =
  | { t: 'p'; i: number; lines: Inline[][] }
  | { t: 'ul' | 'ol'; i: number; items: Inline[][] }
  | { t: 'table'; i: number; head: Inline[][]; rows: Inline[][][] }
  | { t: 'pre'; i: number; s: string };

export const MAX_MARKDOWN_CHARS = 20_000;

/** An http(s) URL with a host, no credentials, no control characters; else null. */
export function safeHref(raw: string): { href: string; domain: string } | null {
  const u = raw.trim();
  // eslint-disable-next-line no-control-regex
  if (!u || u.length > 600 || /[\u0000-\u001f\u007f\s]/.test(u)) return null;
  try {
    const p = new URL(u);
    if ((p.protocol !== 'http:' && p.protocol !== 'https:') || !p.hostname || p.username || p.password) return null;
    return { href: p.href, domain: p.hostname.replace(/^www\./, '').toLowerCase() };
  } catch {
    return null;
  }
}

const INLINE_RE = /\*\*([^*]+?)\*\*|`([^`]+)`|\[(\d{1,2})\]|\[([^\]\n]{1,200})\]\(([^)\s]{1,600})\)/g;

export function parseInline(src: string, nCites: number): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  const push = (s: string) => { if (s) out.push({ t: 'text', s }); };
  for (const m of src.matchAll(INLINE_RE)) {
    const at = m.index ?? 0;
    push(src.slice(last, at));
    last = at + m[0].length;
    if (m[1] !== undefined) out.push({ t: 'bold', c: parseInline(m[1], nCites) });
    else if (m[2] !== undefined) out.push({ t: 'code', s: m[2] });
    else if (m[3] !== undefined) {
      const n = Number(m[3]);
      if (n >= 1 && n <= nCites) out.push({ t: 'cite', n }); else push(m[0]);
    } else {
      const link = safeHref(m[5]);
      if (link) out.push({ t: 'link', text: m[4], href: link.href, domain: link.domain });
      else push(m[4]);                       // not a safe link: the label is all that is kept
    }
  }
  push(src.slice(last));
  return out;
}

const UL = /^\s*(?:[-*•])\s+/;
const OL = /^\s*\d+[.)]\s+/;
const isRow = (l: string) => l.trim().startsWith('|') && l.trim().length > 1;
const isSep = (l: string) => /^\s*\|?[\s:\-|]+\|?\s*$/.test(l) && l.includes('-');
const cells = (l: string) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());

/** Server paragraph split (server/agent/websearch.py paragraphs): blank-line separated. */
export function chunks(text: string): string[] {
  return text.trim().split(/\n\s*\n/).filter((c) => c.trim());
}

export function parseMarkdown(text: string, nCites = 0): Block[] {
  const src = text.length > MAX_MARKDOWN_CHARS ? text.slice(0, MAX_MARKDOWN_CHARS) : text;
  const blocks: Block[] = [];
  chunks(src).forEach((chunk, i) => {
    const lines = chunk.split('\n');
    if (lines[0].trim().startsWith('```')) {
      blocks.push({ t: 'pre', i, s: lines.filter((l) => !l.trim().startsWith('```')).join('\n') });
      return;
    }
    if (lines.length >= 2 && lines.every(isRow)) {
      const rows = lines.filter((l) => !isSep(l)).map(cells);
      if (rows.length === 0) return;
      const [head, ...rest] = rows;
      blocks.push({ t: 'table', i, head: head.map((c) => parseInline(c, nCites)), rows: rest.map((r) => r.map((c) => parseInline(c, nCites))) });
      return;
    }
    let k = 0;
    while (k < lines.length) {
      const listKind = UL.test(lines[k]) ? 'ul' : OL.test(lines[k]) ? 'ol' : null;
      const start = k;
      if (listKind) {
        const items: Inline[][] = [];
        while (k < lines.length && (UL.test(lines[k]) || OL.test(lines[k]))) {
          items.push(parseInline(lines[k].replace(UL.test(lines[k]) ? UL : OL, ''), nCites));
          k += 1;
        }
        blocks.push({ t: listKind, i, items });
      } else {
        while (k < lines.length && !UL.test(lines[k]) && !OL.test(lines[k])) k += 1;
        const para = lines.slice(start, k).map((l) => l.replace(/^#{1,6}\s+/, ''));
        const heading = lines[start].match(/^#{1,6}\s+/);
        blocks.push({ t: 'p', i, lines: para.map((l, idx) => {
          const inl = parseInline(l, nCites);
          return heading && idx === 0 ? [{ t: 'bold', c: inl } as Inline] : inl;
        }) });
      }
    }
  });
  return blocks;
}

/** The text with citation markers removed (spoken replies, previews). */
export const stripMarkers = (s: string): string => s.replace(/\s?\[\d{1,2}\]/g, '');
