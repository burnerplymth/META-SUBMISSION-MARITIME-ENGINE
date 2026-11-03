/* Grid logic: filter parsing, multi-column sort, row windowing, cell
 * formatting. Pure; the panel only paints what these return. */
import { formatTimeFull } from '../../charts/scale';
import { formatValue, group, trimNumber } from '../../charts/format';
import { parseTime, type Cell, type Column, type Dataset } from './columns';

/* ── filters ──────────────────────────────────────────────────────────
 * number / time: >5  >=5  <5  <=5  =5  !=5  1..9  empty  set
 * text / category: words (contains, any of a,b)  =exact  !not  empty  set
 * `empty` = NOT SERVED cells; `set` = served cells. */
export type Pred = (cell: Cell) => boolean;
export type FilterParse = { ok: true; pred: Pred | null } | { ok: false; reason: string };

function num(col: Column, s: string): number | null {
  const t = s.trim();
  if (t === '') return null;
  if (col.type === 'time') return parseTime(t);
  const n = Number(t.replace(/,/g, ''));
  return Number.isFinite(n) ? n : null;
}

export function parseFilter(col: Column, raw: string): FilterParse {
  const text = raw.trim();
  if (text === '') return { ok: true, pred: null };
  const low = text.toLowerCase();
  if (low === 'empty') return { ok: true, pred: (c) => c == null };
  if (low === 'set') return { ok: true, pred: (c) => c != null };
  if (col.type === 'number' || col.type === 'time') {
    const range = /^(.+?)\.\.(.+)$/.exec(text);
    if (range) {
      const a = num(col, range[1]); const b = num(col, range[2]);
      if (a == null || b == null) return { ok: false, reason: 'bad range' };
      const lo = Math.min(a, b); const hi = Math.max(a, b);
      return { ok: true, pred: (c) => typeof c === 'number' && c >= lo && c <= hi };
    }
    const m = /^(>=|<=|!=|>|<|=)?\s*(.+)$/.exec(text);
    const v = m ? num(col, m[2]) : null;
    if (!m || v == null) return { ok: false, reason: 'bad number' };
    const op = m[1] ?? '=';
    const cmp: Record<string, Pred> = {
      '>': (c) => typeof c === 'number' && c > v, '>=': (c) => typeof c === 'number' && c >= v,
      '<': (c) => typeof c === 'number' && c < v, '<=': (c) => typeof c === 'number' && c <= v,
      '=': (c) => typeof c === 'number' && c === v, '!=': (c) => typeof c === 'number' && c !== v,
    };
    return { ok: true, pred: cmp[op] };
  }
  if (text.startsWith('=')) { const want = text.slice(1).trim().toLowerCase(); return { ok: true, pred: (c) => typeof c === 'string' && c.toLowerCase() === want }; }
  if (text.startsWith('!')) {
    const words = text.slice(1).split(',').map((w) => w.trim().toLowerCase()).filter(Boolean);
    return { ok: true, pred: (c) => typeof c === 'string' && !words.some((w) => c.toLowerCase().includes(w)) };
  }
  const any = text.split(',').map((w) => w.trim().toLowerCase()).filter(Boolean);
  return { ok: true, pred: (c) => typeof c === 'string' && any.some((w) => c.toLowerCase().includes(w)) };
}

export interface FilterSet { [col: number]: string }

/** Row indexes passing every column filter (AND across columns). Bad
 *  filters are reported and skipped, never silently passing rows. */
export function applyFilters(ds: Dataset, filters: FilterSet): { idx: number[]; errors: Record<number, string> } {
  const preds: Array<{ col: number; pred: Pred }> = [];
  const errors: Record<number, string> = {};
  for (const [k, raw] of Object.entries(filters)) {
    const col = ds.columns[Number(k)];
    if (!col) continue;
    const r = parseFilter(col, raw);
    if (!r.ok) errors[col.index] = r.reason;
    else if (r.pred) preds.push({ col: col.index, pred: r.pred });
  }
  const idx: number[] = [];
  for (let i = 0; i < ds.rows.length; i++) {
    const row = ds.rows[i];
    if (preds.every((p) => p.pred(row[p.col]))) idx.push(i);
  }
  return { idx, errors };
}

/* ── sort ─────────────────────────────────────────────────────────── */
export interface SortKey { col: number; dir: 'asc' | 'desc' }

const collator = new Intl.Collator('en', { numeric: true, sensitivity: 'base' });
function cmpCell(a: Cell, b: Cell): number {
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return collator.compare(String(a), String(b));
}

/** Stable multi-key sort. NOT SERVED cells sort last in both directions. */
export function sortRows(ds: Dataset, idx: readonly number[], keys: readonly SortKey[]): number[] {
  if (keys.length === 0) return [...idx];
  return [...idx].sort((ia, ib) => {
    for (const k of keys) {
      const a = ds.rows[ia][k.col]; const b = ds.rows[ib][k.col];
      if (a == null && b == null) continue;
      if (a == null) return 1;
      if (b == null) return -1;
      const c = cmpCell(a, b);
      if (c !== 0) return k.dir === 'asc' ? c : -c;
    }
    return ia - ib;
  });
}

/** Click: cycle asc, desc, off. Shift-click adds a secondary key. */
export function toggleSort(keys: readonly SortKey[], col: number, additive: boolean): SortKey[] {
  const cur = keys.find((k) => k.col === col);
  const next: SortKey | null = !cur ? { col, dir: 'asc' } : cur.dir === 'asc' ? { col, dir: 'desc' } : null;
  if (!additive) return next ? [next] : [];
  const rest = keys.filter((k) => k.col !== col);
  return next ? [...rest, next] : rest;
}

/* ── windowing ────────────────────────────────────────────────────── */
export interface WindowIn { total: number; rowH: number; viewportH: number; scrollTop: number; overscan?: number }
export interface WindowOut { start: number; end: number; padTop: number; padBottom: number; totalH: number }
export function windowRows(w: WindowIn): WindowOut {
  const over = w.overscan ?? 6;
  const totalH = w.total * w.rowH;
  const first = Math.floor(Math.max(0, w.scrollTop) / w.rowH);
  const visible = Math.ceil(Math.max(0, w.viewportH) / w.rowH) + 1;
  const start = Math.max(0, Math.min(w.total, first - over));
  const end = Math.max(start, Math.min(w.total, first + visible + over));
  return { start, end, padTop: start * w.rowH, padBottom: Math.max(0, (w.total - end) * w.rowH), totalH };
}

/** Scroll offset that brings row `i` fully into view. */
export function scrollToRow(i: number, rowH: number, viewportH: number, scrollTop: number): number {
  const top = i * rowH; const bottom = top + rowH;
  if (top < scrollTop) return top;
  if (bottom > scrollTop + viewportH) return bottom - viewportH;
  return scrollTop;
}

/* ── cell formatting ──────────────────────────────────────────────── */
export interface CellView { text: string; kind: 'value' | 'absent' | 'applicable'; unit: string | null; align: 'left' | 'right' }

/** Sorted-by-magnitude digits: grouped integers, up to 4 decimals for
 *  small numbers; share-of-capacity values stay as decimals. */
export function formatCell(col: Column, cell: Cell, unit: string | null, absent: 'NOT SERVED' | 'NOT APPLICABLE'): CellView {
  if (cell == null) return { text: absent, kind: absent === 'NOT SERVED' ? 'absent' : 'applicable', unit: null, align: col.type === 'number' ? 'right' : 'left' };
  if (col.type === 'number') return { text: formatValue(cell as number, null), kind: 'value', unit, align: 'right' };
  if (col.type === 'time') return { text: formatTimeFull(cell as number), kind: 'value', unit: null, align: 'left' };
  return { text: String(cell), kind: 'value', unit: null, align: 'left' };
}

/** Plain text for CSV copy / selection export: unit-free number, ISO time. */
export function cellPlain(col: Column, cell: Cell): string {
  if (cell == null) return '';
  if (col.type === 'time') return formatTimeFull(cell as number);
  if (col.type === 'number') return trimNumber(cell as number, 10);
  return String(cell);
}
void group;

/** Rows as CSV text: the visible columns, served values only, with the
 *  header carrying the column's unit. */
export function rowsToCsv(ds: Dataset, idx: readonly number[], cols: readonly number[], esc: (v: string) => string): string {
  const head = cols.map((c) => esc(ds.columns[c].header));
  const lines = [head.join(',')];
  for (const i of idx) lines.push(cols.map((c) => esc(cellPlain(ds.columns[c], ds.rows[i][c]))).join(','));
  return `${lines.join('\n')}\n`;
}

/* ── column widths ────────────────────────────────────────────────── */
/** Pixel width per column from its header and a sample of its cells. */
export function columnWidths(ds: Dataset, cols: readonly number[], sample = 200): Record<number, number> {
  const out: Record<number, number> = {};
  for (const ci of cols) {
    const c = ds.columns[ci];
    let longest = c.name.length + 3 + (c.unit && c.unit !== 'UTC' ? c.unit.length + 1 : 0) + (c.unitFrom != null ? 8 : 0);
    const n = Math.min(sample, ds.rows.length);
    for (let i = 0; i < n; i++) {
      const v = ds.rows[i][ci];
      const len = v == null ? 10 : c.type === 'time' ? 17 : c.type === 'number' ? String(v).length + 8 : String(v).length;
      if (len > longest) longest = len;
    }
    const base = c.type === 'number' ? longest * 6.6 + 24 : longest * 6.4 + 18;
    out[ci] = Math.round(Math.max(72, Math.min(c.type === 'text' ? 280 : 200, base)));
  }
  return out;
}

/** Range select between two row positions, inclusive. */
export function rangeSelect(rows: readonly number[], a: number, b: number): number[] {
  const lo = Math.min(a, b); const hi = Math.max(a, b);
  return rows.slice(Math.max(0, lo), Math.min(rows.length, hi + 1));
}
