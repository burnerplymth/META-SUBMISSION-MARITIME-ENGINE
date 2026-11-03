/* Summary strip: count, min, max, median per numeric column, computed
 * client-side from the rows currently loaded (and filtered). A column
 * whose unit varies per row is summarised once per unit, never across
 * units. */
import type { Cell, Column, Dataset } from './columns';
import { unitOf } from './columns';

export interface Stat { n: number; notServed: number; min: number | null; max: number | null; median: number | null }
export interface ColumnSummary {
  col: number; name: string; header: string; type: 'number' | 'time';
  /** one entry per unit group; a single entry with unit null when units do not vary */
  groups: Array<{ unit: string | null; stat: Stat }>;
  /** rows whose value is absent, counted once for the column (never a unit group of their own) */
  notServed: number;
}

export function median(sorted: readonly number[]): number | null {
  const n = sorted.length;
  if (n === 0) return null;
  const mid = n >> 1;
  return n % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

export function statOf(values: ReadonlyArray<number | null>): Stat {
  const v = values.filter((x): x is number => x != null && Number.isFinite(x)).sort((a, b) => a - b);
  return {
    n: v.length, notServed: values.length - v.length,
    min: v.length ? v[0] : null, max: v.length ? v[v.length - 1] : null, median: median(v),
  };
}

export function summarise(ds: Dataset, idx: readonly number[], cols?: readonly number[]): ColumnSummary[] {
  const pick = (cols ?? ds.columns.map((c) => c.index)).map((i) => ds.columns[i]).filter((c): c is Column => !!c && (c.type === 'number' || c.type === 'time'));
  return pick.map((c) => {
    const type = c.type as 'number' | 'time';
    if (c.unitFrom == null) {
      const st = statOf(idx.map((i) => ds.rows[i][c.index] as Cell as number | null));
      return { col: c.index, name: c.name, header: c.header, type, groups: [{ unit: null, stat: { ...st, notServed: 0 } }], notServed: st.notServed };
    }
    const by = new Map<string, Array<number | null>>();
    let absent = 0;
    for (const i of idx) {
      const v = ds.rows[i][c.index] as number | null;
      if (v == null) { absent++; continue; }
      const u = unitOf(ds, c, ds.rows[i]) ?? 'NO UNIT';
      let arr = by.get(u); if (!arr) { arr = []; by.set(u, arr); }
      arr.push(v);
    }
    return { col: c.index, name: c.name, header: c.header, type, groups: [...by.entries()].sort(([a], [b]) => (a < b ? -1 : 1)).map(([unit, vals]) => ({ unit, stat: statOf(vals) })), notServed: absent };
  });
}
