/**
 * Source citations. The server derives, from what a feed itself
 * provided, the human-readable page behind a feed item:
 * { publisher, title, url, domain, kind, published_at? }. It is absent when
 * the source has no such page (then the item shows only its provenance chip).
 *
 * The server is the authority on what may be linked; this parser repeats the
 * structural checks (https only, no credentials, bounded text, known kind,
 * host allow-list for the non-article kinds) so a mis-served or stale record
 * can never become an odd link.
 */
export type CitationKind = 'ARTICLE' | 'ALERT' | 'REPORT' | 'PORTAL';

export interface Citation {
  publisher: string;
  title: string;
  url: string;
  domain: string;
  kind: CitationKind;
  published_at?: string;
}

const KIND_HOSTS: Record<Exclude<CitationKind, 'ARTICLE'>, readonly string[]> = {
  ALERT: ['www.weather.gov', 'forecast.weather.gov'],
  REPORT: ['www.gdacs.org'],
  PORTAL: ['portwatch.imf.org'],
};
const KIND_LABEL: Record<CitationKind, string> = { ARTICLE: 'Article', ALERT: 'Alert', REPORT: 'Report', PORTAL: 'Portal' };
const URL_MAX = 2048;

const text = (v: unknown, max: number): string | null =>
  typeof v === 'string' && v.trim() && v.length <= max ? v.trim() : null;

/** A served citation object -> a Citation, or undefined when it is not safe to show. */
export function parseCitation(raw: unknown): Citation | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const r = raw as Record<string, unknown>;
  const kind = r.kind;
  if (kind !== 'ARTICLE' && kind !== 'ALERT' && kind !== 'REPORT' && kind !== 'PORTAL') return undefined;
  const url = text(r.url, URL_MAX);
  const publisher = text(r.publisher, 200);
  const title = text(r.title, 400);
  if (!url || !publisher || !title) return undefined;
  let u: URL;
  try { u = new URL(url); } catch { return undefined; }
  if (u.protocol !== 'https:' || u.username || u.password) return undefined;
  if (kind !== 'ARTICLE' && !KIND_HOSTS[kind].includes(u.hostname)) return undefined;
  const host = u.hostname.replace(/^www\./, '');
  const when = typeof r.published_at === 'string' && !Number.isNaN(Date.parse(r.published_at)) ? r.published_at : undefined;
  return { publisher, title, url, domain: text(r.domain, 253) ?? host, kind, ...(when ? { published_at: when } : {}) };
}

/** Quiet kind word shown beside a citation: Alert / Report / Article / Portal. */
export const citationKindLabel = (k: CitationKind): string => KIND_LABEL[k];

/** Single-line shortening with an ellipsis, on a word edge where one is near. */
export function shorten(s: string, max: number): string {
  const t = s.replace(/\s+/g, ' ').trim();
  if (t.length <= max) return t;
  const cut = t.slice(0, max - 1);
  const sp = cut.lastIndexOf(' ');
  return `${(sp > max * 0.6 ? cut.slice(0, sp) : cut).replace(/[\s,;:—-]+$/, '')}…`;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
/** "25 Oct 2026" (UTC) from an ISO stamp; '' when unreadable. */
export function citationDate(iso: string | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}
