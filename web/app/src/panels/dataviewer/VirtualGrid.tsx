import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Chip } from '../reports/chips';
import { absentWord, cellChip, unitOf, type Dataset } from './columns';
import { columnWidths, formatCell, rangeSelect, scrollToRow, windowRows, type SortKey } from './grid';

/* Virtualised grid. Only the rows in (and just around) the viewport are
 * in the DOM; padding keeps the scroll height true. Sticky header with
 * sort buttons and a filter row; shift / ctrl selection; arrow keys
 * move the active row. */

export const ROW_H = 28; // table row height (--row-h); the grid is virtualised so the height is a number here
const SEL_W = 30;
const CHIP_COL = /(_chip|^provenance|^grade_state)$/;

export interface GridProps {
  ds: Dataset;
  /** dataset row indexes in display order */
  rows: number[];
  cols: number[];
  sort: SortKey[];
  onSort: (col: number, additive: boolean) => void;
  filters: Record<number, string>;
  filterErrors: Record<number, string>;
  onFilter: (col: number, text: string) => void;
  selected: ReadonlySet<number>;
  onSelect: (rows: number[], anchor: number | null) => void;
  /** row position to scroll to (e.g. after a chart click) */
  reveal?: number | null;
}

export function VirtualGrid({ ds, rows, cols, sort, onSort, filters, filterErrors, onFilter, selected, onSelect, reveal }: GridProps) {
  const box = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewH, setViewH] = useState(400);
  const [active, setActive] = useState<number | null>(null);
  const anchor = useRef<number | null>(null);
  const widths = useMemo(() => columnWidths(ds, cols), [ds, cols]);
  const total = cols.reduce((a, c) => a + (widths[c] ?? 100), SEL_W);
  const tpl = `${SEL_W}px ${cols.map((c) => `${widths[c] ?? 100}px`).join(' ')}`;
  const w = windowRows({ total: rows.length, rowH: ROW_H, viewportH: viewH, scrollTop });

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    setViewH(el.clientHeight);
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => setViewH(el.clientHeight));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  /* the header + filter rows are sticky inside the same scroller; rows
   * start below them */
  const HEAD_H = 54;
  const goTo = useCallback((pos: number) => {
    const el = box.current;
    if (!el) return;
    const next = scrollToRow(pos, ROW_H, Math.max(ROW_H, el.clientHeight - HEAD_H), el.scrollTop);
    if (next !== el.scrollTop) el.scrollTop = next;
  }, []);
  useEffect(() => { if (reveal != null && reveal >= 0) { goTo(reveal); setActive(reveal); } }, [reveal, goTo]);
  useEffect(() => { setActive(null); anchor.current = null; if (box.current) box.current.scrollTop = 0; }, [ds]);
  useEffect(() => { setActive((a) => (a != null && a >= rows.length ? null : a)); }, [rows.length]);

  const pick = (pos: number, e: { shiftKey: boolean; ctrlKey: boolean; metaKey: boolean }) => {
    setActive(pos);
    if (e.shiftKey && anchor.current != null) onSelect(rangeSelect(rows, anchor.current, pos), anchor.current);
    else if (e.ctrlKey || e.metaKey) {
      const next = new Set(selected);
      if (next.has(rows[pos])) next.delete(rows[pos]); else next.add(rows[pos]);
      anchor.current = pos; onSelect([...next], pos);
    } else { anchor.current = pos; onSelect([rows[pos]], pos); }
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (rows.length === 0) return;
    const page = Math.max(1, Math.floor((viewH - HEAD_H) / ROW_H) - 1);
    const move = (to: number) => {
      const pos = Math.max(0, Math.min(rows.length - 1, to));
      e.preventDefault(); goTo(pos); pick(pos, { shiftKey: e.shiftKey, ctrlKey: false, metaKey: false });
    };
    const cur = active ?? -1;
    if (e.key === 'ArrowDown') move(cur + 1);
    else if (e.key === 'ArrowUp') move(cur < 0 ? 0 : cur - 1);
    else if (e.key === 'PageDown') move(cur + page);
    else if (e.key === 'PageUp') move(cur - page);
    else if (e.key === 'Home' && !e.ctrlKey) move(0);
    else if (e.key === 'End' && !e.ctrlKey) move(rows.length - 1);
    else if ((e.key === ' ' || e.key === 'Spacebar') && active != null) { e.preventDefault(); pick(active, { shiftKey: false, ctrlKey: true, metaKey: false }); }
    else if (e.key === 'Enter' && active != null) { e.preventDefault(); pick(active, { shiftKey: false, ctrlKey: false, metaKey: false }); }
    else if ((e.key === 'a' || e.key === 'A') && (e.ctrlKey || e.metaKey)) { e.preventDefault(); onSelect([...rows], 0); }
    else if (e.key === 'Escape') { onSelect([], null); anchor.current = null; }
  };

  const sortOf = (c: number) => sort.findIndex((k) => k.col === c);
  const slice = rows.slice(w.start, w.end);
  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r));

  return (
    <div ref={box} className="dv-grid" role="grid" aria-label={`${ds.id} rows`} aria-rowcount={rows.length + 1} aria-colcount={cols.length + 1}
      tabIndex={0} onScroll={(e) => setScrollTop((e.target as HTMLElement).scrollTop)} onKeyDown={onKey}
      aria-activedescendant={active != null ? `dv-r-${ds.id}-${rows[active]}` : undefined}>
      <div className="dv-inner" style={{ width: total, minHeight: HEAD_H + w.totalH }}>
        <div className="dv-head" role="row" style={{ gridTemplateColumns: tpl }}>
          <div className="dv-h dv-selcell" role="columnheader">
            <input type="checkbox" aria-label="Select all rows" checked={allSelected} onChange={() => onSelect(allSelected ? [] : [...rows], 0)} />
          </div>
          {cols.map((c) => {
            const col = ds.columns[c]; const si = sortOf(c);
            const dir = si >= 0 ? sort[si].dir : null;
            return (
              <div key={c} className={`dv-h${col.type === 'number' ? ' num' : ''}`} role="columnheader" aria-sort={dir === 'asc' ? 'ascending' : dir === 'desc' ? 'descending' : 'none'}>
                <button type="button" className="dv-sortbtn" title={col.header} onClick={(e) => onSort(c, e.shiftKey)}>
                  <span className="dv-hname">{col.name}</span>
                  {col.unit && col.unit !== 'UTC' && <span className="dv-hunit">{col.unit}</span>}
                  {col.unitFrom != null && <span className="dv-hunit">Per row</span>}
                  <span className="dv-sortmark" aria-hidden="true">{dir === 'asc' ? '▲' : dir === 'desc' ? '▼' : ''}{sort.length > 1 && si >= 0 ? si + 1 : ''}</span>
                </button>
              </div>
            );
          })}
        </div>
        <div className="dv-filters" role="row" style={{ gridTemplateColumns: tpl }}>
          <div className="dv-f dv-selcell" role="gridcell" />
          {cols.map((c) => (
            <div key={c} className="dv-f" role="gridcell">
              <input className="dv-filter" value={filters[c] ?? ''} aria-label={`Filter ${ds.columns[c].name}`} aria-invalid={!!filterErrors[c]}
                placeholder={ds.columns[c].type === 'number' || ds.columns[c].type === 'time' ? '>5  1..9' : 'contains'} spellCheck={false}
                onChange={(e) => onFilter(c, e.target.value)} />
            </div>
          ))}
        </div>
        <div className="dv-body" style={{ paddingTop: w.padTop, paddingBottom: w.padBottom }}>
          {slice.map((ri, k) => {
            const pos = w.start + k;
            const row = ds.rows[ri];
            const sel = selected.has(ri);
            return (
              <div key={ri} id={`dv-r-${ds.id}-${ri}`} role="row" aria-rowindex={pos + 2} aria-selected={sel}
                className={`dv-row${sel ? ' sel' : ''}${active === pos ? ' act' : ''}${pos % 2 ? ' alt' : ''}`}
                style={{ gridTemplateColumns: tpl, height: ROW_H }} onClick={(e) => pick(pos, e)}>
                <div className="dv-c dv-selcell" role="gridcell"><input type="checkbox" tabIndex={-1} aria-label={`Select row ${ri + 1}`} checked={sel}
                  onClick={(e) => e.stopPropagation()} onChange={() => pick(pos, { shiftKey: false, ctrlKey: true, metaKey: false })} /></div>
                {cols.map((c) => {
                  const col = ds.columns[c];
                  const cell = row[c];
                  const unit = col.unitFrom != null ? unitOf(ds, col, row) : null;
                  const v = formatCell(col, cell, unit, absentWord(ds, col, row));
                  const chip = cell != null && CHIP_COL.test(col.name) && typeof cell === 'string' ? cell : null;
                  void cellChip;
                  return (
                    <div key={c} className={`dv-c${v.align === 'right' ? ' num' : ''}`} role="gridcell" title={v.kind === 'value' ? `${v.text}${v.unit ? ` ${v.unit}` : ''}` : v.text}>
                      {chip ? <Chip chip={chip} />
                        : v.kind === 'value'
                          ? <><span className="dv-v">{v.text}</span>{v.unit && <span className="dv-unit">{v.unit}</span>}</>
                          : <span className={`dv-absent${v.kind === 'applicable' ? ' na' : ''}`}>{v.text}</span>}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
      {rows.length === 0 && <div className="dv-empty"><Chip chip="NOT SERVED" word="NO ROWS" /></div>}
    </div>
  );
}
