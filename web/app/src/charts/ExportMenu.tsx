import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { placePop, type PopStyle } from './popPlace';
import type { ExportKind, ExportResult, ExportSkin, ServerExport } from './exportDom';
import { Icon } from '../ui/Icon';
import { StateMark } from '../ui/Status';

/* The chart EXPORT menu: SVG, PNG at 1x and 2x, CSV of the plotted
 * data, copy image, copy data, copy chart link. The skin switch picks
 * the live skin or a neutral light one for print. Keyboard: Enter or
 * Down opens; arrows move; Escape closes and returns focus. */

interface Item { kind: ExportKind; label: string; needs?: 'link' }
const ITEMS: readonly Item[] = [
  { kind: 'svg', label: 'SVG' },
  { kind: 'png1', label: 'PNG 1x' },
  { kind: 'png2', label: 'PNG 2x' },
  { kind: 'csv', label: 'CSV' },
  { kind: 'copy-image', label: 'Copy image' },
  { kind: 'copy-data', label: 'Copy data' },
  { kind: 'copy-link', label: 'Copy link', needs: 'link' },
];

const SERVER_ITEMS: ReadonlyArray<{ kind: ExportKind; label: string }> = [
  { kind: 'srv-svg', label: 'Server SVG' }, { kind: 'srv-png', label: 'Server PNG' }, { kind: 'srv-csv', label: 'Server CSV' },
];

export function ExportMenu({ onRun, hasLink, server, label = 'Export' }: {
  onRun: (kind: ExportKind, skin: ExportSkin) => Promise<ExportResult>; hasLink: boolean; server?: ServerExport | null; label?: string;
}) {
  const [open, setOpen] = useState(false);
  const [skin, setSkin] = useState<ExportSkin>('shown');
  const [status, setStatus] = useState<ExportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const items = ITEMS.filter((i) => i.needs !== 'link' || hasLink);
  const [at, setAt] = useState<PopStyle | null>(null);
  /* the menu is drawn at the page root, placed from the button: a window's scroller never cuts it */
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
  useEffect(() => {
    if (!status) return;
    const t = setTimeout(() => setStatus(null), 3200);
    return () => clearTimeout(t);
  }, [status]);

  const run = async (k: ExportKind) => {
    close(true); setBusy(true);
    try { setStatus(await onRun(k, skin)); } finally { setBusy(false); }
  };
  const onKey = (e: React.KeyboardEvent) => {
    const els = Array.from(list.current?.querySelectorAll<HTMLElement>('[role=menuitem],[role=menuitemradio]') ?? []);
    const i = els.indexOf(document.activeElement as HTMLElement);
    if (e.key === 'Escape') { e.preventDefault(); close(true); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); els[(i + 1) % els.length]?.focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); els[(i - 1 + els.length) % els.length]?.focus(); }
    else if (e.key === 'Home') { e.preventDefault(); els[0]?.focus(); }
    else if (e.key === 'End') { e.preventDefault(); els[els.length - 1]?.focus(); }
    else if (e.key === 'Tab') setOpen(false);
  };

  return (
    <span className="cx-menu">
      <button ref={btn} type="button" className="cx-btn" aria-haspopup="menu" aria-expanded={open} disabled={busy}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => { if (e.key === 'ArrowDown' && !open) { e.preventDefault(); setOpen(true); } }}>
        {busy ? 'Working' : <>{label} <Icon name="chevron-down" size="xs" /></>}
      </button>
      {status && <span className={`cx-status ${status.ok ? 'ok' : 'bad'}`} role="status">{status.word}</span>}
      {open && at && createPortal(
        <div ref={list} className="cx-pop cx-pop--fixed" role="menu" aria-label="Chart export" onKeyDown={onKey} style={at}>
          {items.map((i) => (
            <button key={i.kind} type="button" role="menuitem" className="cx-item" onClick={() => run(i.kind)}>{i.label}</button>
          ))}
          {server && (
            <>
              <div className="cx-sep" role="separator" />
              {SERVER_ITEMS.map((i) => (
                <button key={i.kind} type="button" role="menuitem" className="cx-item" aria-disabled={!server.ok} disabled={!server.ok}
                  title={server.ok ? undefined : server.note} onClick={() => run(i.kind)}>{i.label}{!server.ok && server.note ? ` · ${server.note}` : ''}</button>
              ))}
            </>
          )}
          <div className="cx-sep" role="separator" />
          {(['shown', 'print'] as const).map((s) => (
            <button key={s} type="button" role="menuitemradio" aria-checked={skin === s} className={`cx-item cx-skin${skin === s ? ' on' : ''}`}
              onClick={() => setSkin(s)}><StateMark name={skin === s ? 'dot' : 'ring'} /> {s === 'shown' ? 'Skin as shown' : 'Skin light print'}</button>
          ))}
        </div>, document.body)}
    </span>
  );
}
