/* Recent exports, kept in this browser only (safeStorage), so a file
 * can be fetched again without re-entering its options. Pure: storage
 * is passed in. Everything read back is untrusted and re-validated. */

export type RecentReq =
  | { t: 'csv'; path: string }
  | { t: 'json'; path: string; id: string }
  | { t: 'xlsx'; path: string }
  | { t: 'figure'; path: string; ext: 'svg' | 'png' | 'csv' | 'json' }
  | { t: 'figpost'; body: Record<string, unknown>; ext: 'svg' | 'png' | 'csv' }
  | { t: 'pack'; body: Record<string, unknown> }
  | { t: 'report'; body: Record<string, unknown>; ext: 'html' | 'md'; figures: boolean }
  | { t: 'snapshot'; runId: string | null; simRunId: string | null };

export interface RecentItem {
  key: string; label: string; file: string; at: string;
  runId: string | null; bytes: number | null; rows: number | null; req: RecentReq;
}

export const RECENT_KEY = 'supermap.exports.recent';
export const RECENT_MAX = 20;

/** Same request = same entry: it moves to the top instead of repeating. */
export function requestKey(req: RecentReq): string {
  switch (req.t) {
    case 'csv': case 'xlsx': return `${req.t}:${req.path}`;
    case 'figure': return `figure:${req.path}`;
    case 'figpost': return `figpost:${req.ext}:${JSON.stringify(req.body)}`;
    case 'json': return `json:${req.path}`;
    case 'pack': return `pack:${JSON.stringify(req.body)}`;
    case 'report': return `report:${req.ext}:${req.figures ? 1 : 0}:${JSON.stringify(req.body)}`;
    case 'snapshot': return `snapshot:${req.runId ?? ''}:${req.simRunId ?? ''}`;
  }
}

export function pushRecent(list: readonly RecentItem[], item: Omit<RecentItem, 'key'>, max = RECENT_MAX): RecentItem[] {
  const key = requestKey(item.req);
  return [{ ...item, key }, ...list.filter((r) => r.key !== key)].slice(0, max);
}

const PATH = /^\/[A-Za-z0-9_\-./?=&%:,+]{1,500}$/;
const ID = /^[A-Za-z0-9_.:-]{1,128}$/;
const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);

function parseReq(v: unknown): RecentReq | null {
  if (!isObj(v)) return null;
  switch (v.t) {
    case 'csv': case 'xlsx': return typeof v.path === 'string' && PATH.test(v.path) && v.path.startsWith('/exports/') ? { t: v.t, path: v.path } : null;
    case 'json': return typeof v.path === 'string' && PATH.test(v.path) && v.path.startsWith('/exports/') && typeof v.id === 'string' && ID.test(v.id) ? { t: 'json', path: v.path, id: v.id } : null;
    case 'figure': return typeof v.path === 'string' && PATH.test(v.path) && v.path.startsWith('/exports/chart/') && (v.ext === 'svg' || v.ext === 'png' || v.ext === 'csv' || v.ext === 'json') ? { t: 'figure', path: v.path, ext: v.ext } : null;
    case 'figpost': return isObj(v.body) && (v.ext === 'svg' || v.ext === 'png' || v.ext === 'csv') ? { t: 'figpost', body: v.body, ext: v.ext } : null;
    case 'pack': return isObj(v.body) ? { t: 'pack', body: v.body } : null;
    case 'report': return isObj(v.body) && (v.ext === 'html' || v.ext === 'md') ? { t: 'report', body: v.body, ext: v.ext, figures: v.figures === true } : null;
    case 'snapshot': return { t: 'snapshot', runId: typeof v.runId === 'string' && ID.test(v.runId) ? v.runId : null, simRunId: typeof v.simRunId === 'string' && ID.test(v.simRunId) ? v.simRunId : null };
    default: return null;
  }
}

export function parseRecent(raw: string | null): RecentItem[] {
  if (!raw) return [];
  let j: unknown;
  try { j = JSON.parse(raw); } catch { return []; }
  if (!Array.isArray(j)) return [];
  const out: RecentItem[] = [];
  for (const e of j.slice(0, RECENT_MAX)) {
    if (!isObj(e)) continue;
    const req = parseReq(e.req);
    if (!req || typeof e.label !== 'string' || typeof e.file !== 'string' || typeof e.at !== 'string') continue;
    out.push({
      key: requestKey(req), label: e.label.slice(0, 80), file: e.file.slice(0, 120), at: e.at.slice(0, 32),
      runId: typeof e.runId === 'string' && ID.test(e.runId) ? e.runId : null,
      bytes: typeof e.bytes === 'number' && Number.isFinite(e.bytes) ? e.bytes : null,
      rows: typeof e.rows === 'number' && Number.isFinite(e.rows) ? e.rows : null, req,
    });
  }
  return out;
}

export interface StorageLike { get(k: string): string | null; set(k: string, v: string): void }
export const loadRecent = (s: StorageLike): RecentItem[] => parseRecent(s.get(RECENT_KEY));
export const saveRecent = (s: StorageLike, list: readonly RecentItem[]): void => { s.set(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_MAX))); };
