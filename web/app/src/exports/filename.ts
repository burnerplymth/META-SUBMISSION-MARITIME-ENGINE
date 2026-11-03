/* Filename pattern for exports. Tokens {title} {kind}
 * {window} {run} {date} {label}. {date} is the data as-of date, never the
 * clock, so equal data gives equal names. Pure. */
import { DEFAULT_FILENAME_PATTERN, type ExportFormat, FORMAT_EXT } from './types';

export function slugPart(s: string | null | undefined, max = 40): string {
  if (!s) return '';
  return s.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, max).replace(/-+$/g, '');
}

export interface NameTokens { title: string; kind: string; window: string; run?: string | null; date?: string | null; label?: string | null }

export const PATTERN_TOKENS = ['title', 'kind', 'window', 'run', 'date', 'label'] as const;

/** A pattern is kept to a safe alphabet; unknown tokens are dropped; an empty result falls back to the default. */
export function sanitisePattern(raw: string): string {
  const cleaned = raw.replace(/\{(\w+)\}/g, (m, t: string) => ((PATTERN_TOKENS as readonly string[]).includes(t) ? m : ''))
    .replace(/[^A-Za-z0-9._\-{}]+/g, '-').slice(0, 120);
  return /[A-Za-z0-9{]/.test(cleaned) ? cleaned : DEFAULT_FILENAME_PATTERN;
}

export function applyPattern(pattern: string, t: NameTokens): string {
  const date = (() => { const m = t.date && /^(\d{4})-(\d{2})-(\d{2})/.exec(t.date); return m ? `${m[1]}${m[2]}${m[3]}` : ''; })();
  const map: Record<string, string> = {
    title: slugPart(t.title), kind: slugPart(t.kind, 12), window: slugPart(t.window, 20),
    run: slugPart((t.run ?? '').replace(/^run_/, ''), 12), date, label: slugPart(t.label, 12),
  };
  const out = sanitisePattern(pattern).replace(/\{(\w+)\}/g, (_m, k: string) => map[k] ?? '')
    .replace(/-{2,}/g, '-').replace(/^[-.]+|[-.]+$/g, '');
  return out || 'supermap-export';
}

export function fileNameFor(pattern: string, t: NameTokens, format: ExportFormat | 'zip', suffix = ''): string {
  const ext = format === 'zip' ? 'zip' : FORMAT_EXT[format];
  return `${applyPattern(pattern, t)}${suffix}.${ext}`;
}

/** Make names unique within one export: a repeat gets -2, -3 before the extension. */
export function uniqueNames(names: readonly string[]): string[] {
  const seen = new Map<string, number>();
  return names.map((n) => {
    const k = n.toLowerCase();
    const c = (seen.get(k) ?? 0) + 1;
    seen.set(k, c);
    if (c === 1) return n;
    const dot = n.lastIndexOf('.');
    return dot > 0 ? `${n.slice(0, dot)}-${c}${n.slice(dot)}` : `${n}-${c}`;
  });
}
