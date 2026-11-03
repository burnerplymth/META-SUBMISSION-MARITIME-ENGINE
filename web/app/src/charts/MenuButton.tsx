import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { placePop, type PopStyle } from './popPlace';
import './charts.css';
import { Icon } from '../ui/Icon';

/** A small keyboard-operable menu button (same look and keys as the
 *  chart EXPORT menu): Enter / Down opens, arrows move, Escape closes. */
export function MenuButton({ label, items, onPick, disabled, aria }: {
  label: string; items: ReadonlyArray<{ id: string; label: string }>; onPick: (id: string) => void; disabled?: boolean; aria?: string;
}) {
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const [at, setAt] = useState<PopStyle | null>(null);
  useLayoutEffect(() => {
    if (!open) { setAt(null); return; }
    const place = () => { const r = btn.current?.getBoundingClientRect(); if (r) setAt(placePop(r, window.innerWidth, window.innerHeight)); };
    place();
    window.addEventListener('resize', place); window.addEventListener('scroll', place, true);
    return () => { window.removeEventListener('resize', place); window.removeEventListener('scroll', place, true); };
  }, [open]);
  const close = useCallback((refocus: boolean) => { setOpen(false); if (refocus) btn.current?.focus(); }, []);
  useEffect(() => {
    if (!open) return;
    list.current?.querySelector<HTMLElement>('[role=menuitem]')?.focus();
    const away = (e: MouseEvent) => { const t = e.target as Node; if (!list.current?.contains(t) && !btn.current?.contains(t)) setOpen(false); };
    document.addEventListener('mousedown', away);
    return () => document.removeEventListener('mousedown', away);
  }, [open]);
  const onKey = (e: React.KeyboardEvent) => {
    const els = Array.from(list.current?.querySelectorAll<HTMLElement>('[role=menuitem]') ?? []);
    const i = els.indexOf(document.activeElement as HTMLElement);
    if (e.key === 'Escape') { e.preventDefault(); close(true); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); els[(i + 1) % els.length]?.focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); els[(i - 1 + els.length) % els.length]?.focus(); }
    else if (e.key === 'Tab') setOpen(false);
  };
  return (
    <span className="cx-menu">
      <button ref={btn} type="button" className="cx-btn" aria-haspopup="menu" aria-expanded={open} disabled={disabled} onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => { if (e.key === 'ArrowDown' && !open) { e.preventDefault(); setOpen(true); } }}>{label} <Icon name="chevron-down" size="xs" /></button>
      {open && at && createPortal(
        <div ref={list} className="cx-pop cx-pop--fixed" role="menu" aria-label={aria ?? label} onKeyDown={onKey} style={at}>
          {items.map((i) => <button key={i.id} type="button" role="menuitem" className="cx-item" onClick={() => { close(true); onPick(i.id); }}>{i.label}</button>)}
        </div>, document.body)}
    </span>
  );
}
