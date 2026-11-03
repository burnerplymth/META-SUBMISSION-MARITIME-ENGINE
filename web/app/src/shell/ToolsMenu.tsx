import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { RailEntry } from './rail';
import { Icon } from '../ui/Icon';
import { useMenuKeys } from '../ui/menuKeys';

/** The tools the left rail offers, as a menu. The rail is hidden on a
 *  phone, so without this the tool panels (HIS, PRT, RLS, EXP, SCN, LTV)
 *  could not be opened at 390 px. Hidden at desktop widths by
 *  CSS; the same entries, in the same order, and the same hatched mark
 *  for a tool whose route is not served. */
export const toolMenuEntries = (rail: readonly RailEntry[]): RailEntry[] => rail.filter((r) => r.action.t !== 'func');

export function ToolsMenu({ entries, activeTool, onPick }:
  { entries: readonly RailEntry[]; activeTool: string | null; onPick: (e: RailEntry) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLSpanElement>(null);
  const close = useCallback(() => setOpen(false), []);
  const onMenuKey = useMenuKeys(open, pop, btn, close);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); btn.current?.focus(); } };
    document.addEventListener('mousedown', onDown); document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);
  if (!entries.length) return null;
  return (
    <span className="tools-wrap" ref={ref}>
      <button ref={btn} type="button" className="layouts-btn tools-btn mono" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>Tools <Icon name="chevron-down" size="xs" /></button>
      {open && (
        <span ref={pop} className="pop tools-menu mono" role="menu" aria-label="Tools" onKeyDown={onMenuKey}>
          {entries.map((e) => (
            <button key={e.code} type="button" role="menuitem" data-tool-code={e.code}
              className={e.action.t === 'tool' && activeTool === e.action.tool ? 'on' : ''}
              onClick={() => { onPick(e); setOpen(false); }}>
              <b>{e.code}</b> {e.title}{e.mark === 'notserved' ? <> <Icon name="unavailable" size="xs" label="Not served" /></> : null}
            </button>
          ))}
        </span>
      )}
    </span>
  );
}
