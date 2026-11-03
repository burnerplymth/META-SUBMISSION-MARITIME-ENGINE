/* Column typing and the Dataset model the viewer works on. Pure.
 *
 * A server CSV header reads "delay_d [days]". The unit is part of the
 * header; a unit of the form "see unit_class" means the unit varies per
 * row and lives in the named column (so a column is never read in one
 * unit when its rows disagree). An empty cell is NOT SERVED, never 0;
 * a sibling "<name>_chip" column can refine that to NOT APPLICABLE. */
import { parseHeader } from '../../charts/format';
import { parseCsv, unguard } from './csv';

export type ColType = 'number' | 'time' | 'text' | 'category';
export type Cell = number | string | null;

export interface Column {
  index: number; name: string; header: string; unit: string | null;
  /** index of the column holding this column's per-row unit, when the unit is "see <col>" */
  unitFrom: number | null;
  /** text appended to the unit read from unitFrom ("impaired/day") */
  unitSuffix: string;
  type: ColType;
  /** index of the sibling <name>_chip column */
  chipOf: number | null;
  /** an auxiliary column the default view leaves out */
  aux: boolean;
  /** distinct text values when category */
  categories: string[] | null;
}

export interface Dataset {
  id: string;
  columns: Column[];
  rows: Cell[][];
  /** headers the server declared that the body did not carry */
  warnings: string[];
}

const ISO = /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/;
const NUM = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/;

export function parseTime(s: string): number | null {
  if (!ISO.test(s)) return null;
  const t = Date.parse(s.length === 10 ? `${s}T00:00:00Z` : /(?:Z|[+-]\d{2}:?\d{2})$/.test(s) ? s.replace(' ', 'T') : `${s.replace(' ', 'T')}Z`);
  return Number.isFinite(t) ? t : null;
}

const AUX_NAME = /(?:_chip|_as_of|_unit|_reason|_basis|_rule|_state)$|^status_rule$|^matched_phrase$/;

const SEE = /^see\s+(\S+)(?:\s+(.*))?$/;

/** Parse the CSV body of one dataset. `declared` is the catalogue's
 *  column list (names + units), used to type a column that is empty in
 *  this sample (UTC columns, units). */
export function loadDataset(id: string, csvText: string, declared?: ReadonlyArray<{ name: string; unit?: string | null; header?: string }>): Dataset {
  const raw = parseCsv(csvText);
  const head = raw[0] ?? [];
  const body = raw.slice(1);
  const parsed = head.map((hd) => parseHeader(hd));
  const nameIndex = new Map(parsed.map((p, i) => [p.name, i] as const));
  const decl = new Map((declared ?? []).map((d) => [d.name, d] as const));
  const warnings: string[] = [];
  for (const d of declared ?? []) if (!nameIndex.has(d.name)) warnings.push(`missing column ${d.name}`);

  const columns: Column[] = parsed.map((p, index) => {
    const unit = p.unit ?? decl.get(p.name)?.unit ?? null;
    const see = unit ? SEE.exec(unit) : null;
    const chipIdx = nameIndex.get(`${p.name}_chip`);
    return {
      index, name: p.name, header: head[index], unit: see ? null : unit,
      unitFrom: see ? (nameIndex.get(see[1]) ?? null) : null, unitSuffix: see?.[2] ? ` ${see[2]}` : '',
      type: 'text', chipOf: chipIdx ?? null, aux: AUX_NAME.test(p.name), categories: null,
    };
  });

  // type each column from its non-empty cells
  const rows: Cell[][] = body.map((r) => r.map((c) => unguard(c)));
  columns.forEach((col) => {
    let n = 0; let allNum = true; let allTime = true;
    const distinct = new Set<string>();
    for (const r of rows) {
      const c = r[col.index];
      if (c === undefined || c === '') continue;
      n++;
      if (allNum && !NUM.test(c as string)) allNum = false;
      if (allTime && parseTime(c as string) === null) allTime = false;
      if (distinct.size <= 40) distinct.add(c as string);
    }
    if (n > 0 && allTime && !allNum) col.type = 'time';
    else if (n > 0 && allNum) col.type = 'number';
    else if (n === 0 && col.unit === 'UTC') col.type = 'time';
    else if (n > 0 && distinct.size <= 12 && rows.length >= 3 && distinct.size < Math.max(2, rows.length * 0.6)) { col.type = 'category'; col.categories = [...distinct].sort(); }
    else col.type = 'text';
  });
  const typed: Cell[][] = rows.map((r) => columns.map((col) => {
    const c = r[col.index];
    if (c === undefined || c === '') return null;
    if (col.type === 'number') return Number(c);
    if (col.type === 'time') return parseTime(c as string);
    return c;
  }));
  return { id, columns, rows: typed, warnings };
}

/** Per-row unit text for a cell of `col`, or the column's own unit. */
export function unitOf(ds: Dataset, col: Column, row: Cell[]): string | null {
  if (col.unitFrom != null) {
    const u = row[col.unitFrom];
    return u == null || u === '' ? null : `${u}${col.unitSuffix}`;
  }
  return col.unit && col.unit !== 'UTC' ? col.unit : null;
}

/** What an empty cell reads as: the sibling chip refines it. */
export function absentWord(ds: Dataset, col: Column, row: Cell[]): 'NOT SERVED' | 'NOT APPLICABLE' {
  if (col.chipOf != null && row[col.chipOf] === 'NOT APPLICABLE') return 'NOT APPLICABLE';
  return 'NOT SERVED';
}

/** The chip for one cell: the sibling chip column when there is one. */
export function cellChip(ds: Dataset, col: Column, row: Cell[]): string | null {
  if (col.chipOf != null) { const c = row[col.chipOf]; return typeof c === 'string' && c ? c : null; }
  return null;
}

/** Columns shown at first: everything but chips, as-of, unit and reason
 *  helpers, and a cap on very wide sets. Unit columns stay when another
 *  column reads its unit from them. */
export function defaultColumns(ds: Dataset, max = 14): number[] {
  const needed = new Set<number>();
  ds.columns.forEach((c) => { if (c.unitFrom != null) needed.add(c.unitFrom); });
  const keep = ds.columns.filter((c) => !c.aux || needed.has(c.index)).map((c) => c.index);
  return keep.slice(0, max);
}

/** Every dataset column whose name says it holds a place id. */
export function placeColumns(ds: Dataset): Column[] {
  return ds.columns.filter((c) => (c.type === 'text' || c.type === 'category') && /(^|_)place_id$/.test(c.name));
}

/** Place ids of the given rows, in first-seen order, without repeats. */
export function placeIdsOf(ds: Dataset, rowIdx: readonly number[]): string[] {
  const cols = placeColumns(ds);
  const out: string[] = []; const seen = new Set<string>();
  for (const i of rowIdx) for (const c of cols) {
    const v = ds.rows[i]?.[c.index];
    if (typeof v === 'string' && v && !seen.has(v)) { seen.add(v); out.push(v); }
  }
  return out;
}
