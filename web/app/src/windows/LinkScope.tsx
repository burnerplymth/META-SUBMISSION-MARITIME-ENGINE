import React, { useCallback, useMemo, useSyncExternalStore } from 'react';
import { Ctx, DeskCtx } from '../store';
import { noteWindowInteraction } from './linkGate';
import { EMPTY_CELL, cellKey, linkOf, linkStore, setCell, type Cell } from './linkGroups';

/**
 * Scopes a window's view of the selection to its link group. Group A is the
 * store's own selection, so those windows get the store untouched (same
 * object, no extra render). A window in group B, or unlinked, reads and
 * writes a separate cell: it sees its own selected place and selecting there
 * does not move the windows of the other groups.
 *
 * The providers are always rendered (never swapped in and out), so changing a
 * window's group re-scopes it without remounting its content.
 */
export function LinkScope({ wid, children }: { wid: string; children: React.ReactNode }) {
  const real = React.useContext(Ctx);
  const desk = React.useContext(DeskCtx);
  const ls = useSyncExternalStore(linkStore.subscribe, linkStore.get, linkStore.get);
  const key = cellKey(ls, wid);
  const cell: Cell = key ? ls.cells[key] ?? EMPTY_CELL : EMPTY_CELL;
  const grp = linkOf(ls, wid);
  const select = useCallback((id: string | null, seq?: number | null) => {
    if (key) linkStore.set(setCell(linkStore.get(), key, { place: id, seq: seq ?? null }));
  }, [key]);
  const scoped = useMemo(() => {
    if (!real || key === null) return real;
    return { ...real, selectedNode: cell.place, selectedSeq: cell.seq, selectNode: select };
  }, [real, key, cell.place, cell.seq, select]);
  const scopedDesk = useMemo(() => {
    if (!desk || key === null) return desk;
    return { ...desk, selectedNode: cell.place, selectedSeq: cell.seq, selectNode: select };
  }, [desk, key, cell.place, cell.seq, select]);
  return (
    <Ctx.Provider value={scoped}>
      <DeskCtx.Provider value={scopedDesk}>
        <div className="wm-scope" onPointerDownCapture={() => noteWindowInteraction(wid)} data-link={grp ?? 'none'} data-place={key ? cell.place ?? '' : real?.selectedNode ?? ''}>{children}</div>
      </DeskCtx.Provider>
    </Ctx.Provider>
  );
}
