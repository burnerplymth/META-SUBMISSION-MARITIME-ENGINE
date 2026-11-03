/* Recent exports of this browser. Pure list logic over
 * an injected storage; exports/historyStore.ts binds it to safeStorage. */
import type { ExportResultRow } from './types';

export const HISTORY_KEY = 'supermap.export.history';
export const HISTORY_MAX = 20;
export interface StorageLike { get(k: string): string | null; set(k: string, v: string): void }

export interface HistoryEntry {
  at: string; filename: string; format: string; bytes: number; sha256: string; title: string; files: number;
}

export function entryOf(r: ExportResultRow): HistoryEntry {
  return { at: r.at, filename: r.filename, format: r.format, bytes: r.bytes, sha256: r.sha256, title: r.title, files: r.members?.length ?? 1 };
}

export function pushHistory(list: readonly HistoryEntry[], rows: readonly ExportResultRow[], max = HISTORY_MAX): HistoryEntry[] {
  return [...rows.map(entryOf).reverse(), ...list].slice(0, max);
}

export function parseHistory(raw: string | null): HistoryEntry[] {
  if (!raw) return [];
  try {
    const v = JSON.parse(raw) as unknown;
    if (!Array.isArray(v)) return [];
    return v.filter((e): e is HistoryEntry => !!e && typeof e === 'object'
      && typeof (e as HistoryEntry).filename === 'string' && typeof (e as HistoryEntry).sha256 === 'string'
      && typeof (e as HistoryEntry).at === 'string').slice(0, HISTORY_MAX)
      .map((e) => ({ at: e.at, filename: e.filename, format: String(e.format ?? ''), bytes: Number(e.bytes) || 0, sha256: e.sha256, title: String(e.title ?? ''), files: Number(e.files) || 1 }));
  } catch { return []; }
}
export const loadHistory = (s: StorageLike): HistoryEntry[] => parseHistory(s.get(HISTORY_KEY));
export const saveHistory = (s: StorageLike, list: readonly HistoryEntry[]): void => { s.set(HISTORY_KEY, JSON.stringify(list.slice(0, HISTORY_MAX))); };
