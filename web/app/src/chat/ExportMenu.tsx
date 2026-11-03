/**
 * EXPORT MENU: a button that opens a short menu of formats (HTML, Markdown,
 * PDF, JSON). Menu pattern: aria-haspopup, role=menu, Escape closes and returns
 * focus, Up/Down/Home/End move, a click outside closes.
 */
import React, { useEffect, useId, useRef, useState } from 'react';
import { Button } from '../ui';
import { EXPORT_FORMATS, EXPORT_WORD, type ExportFormat } from './types';

export interface ExportMenuProps {
  label: string;
  onPick: (f: ExportFormat) => void;
  disabled?: boolean;
  className?: string;
  size?: 'sm' | 'md';
}

export function ExportMenu({ label, onPick, disabled, className = '', size = 'sm' }: ExportMenuProps) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement | null>(null);
  const focusBtn = () => root.current?.querySelector<HTMLButtonElement>('.ch-menu-btn')?.focus();
  const id = useId();

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', away);
    root.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus();
    return () => document.removeEventListener('mousedown', away);
  }, [open]);

  const items = () => Array.from(root.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? []);
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { e.stopPropagation(); setOpen(false); focusBtn(); return; }
    const els = items();
    const at = els.indexOf(document.activeElement as HTMLButtonElement);
    const go = (i: number) => { e.preventDefault(); els[(i + els.length) % els.length]?.focus(); };
    if (e.key === 'ArrowDown') go(at + 1);
    else if (e.key === 'ArrowUp') go(at - 1);
    else if (e.key === 'Home') go(0);
    else if (e.key === 'End') go(els.length - 1);
  };

  return (
    <span className={`ch-menu${className ? ` ${className}` : ''}`} ref={root} onKeyDown={open ? onKey : undefined}>
      <Button size={size} icon="download" disabled={disabled} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined}
        onClick={() => setOpen((o) => !o)} className="ch-menu-btn">{label}</Button>
      {open && (
        <div className="ch-menu-pop" role="menu" id={id} aria-label={label}>
          {EXPORT_FORMATS.map((f) => (
            <button key={f} type="button" role="menuitem" className="ch-menu-item" data-format={f}
              onClick={() => { setOpen(false); onPick(f); focusBtn(); }}>{EXPORT_WORD[f]}</button>
          ))}
        </div>
      )}
    </span>
  );
}
