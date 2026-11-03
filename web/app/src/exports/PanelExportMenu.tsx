import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ContextMenu, type ContextMenuItem } from '../components/ContextMenu';
import { Icon } from '../ui/Icon';
import { wm } from '../windows/controller';
import { useExportContext } from './context';
import { windowTitle } from './header';
import { managerStore, quickExport, type QuickResult } from './managerStore';
import { getExportable, useExportables } from './registry';
import { FORMAT_LABEL, STATE_WORD, type ExportFormat, type ExportableDef } from './types';
import { SAVE_PDF_LABEL, SAVE_PDF_TIP } from './printPdf';
import './export.css';

/* The per-panel Export menu: the same registry as the
 * Export window, restricted to one window's items. Used in the window
 * chrome and, through <ExportButton>, inside any panel. */

function Popover({ anchor, onClose, children, label }: { anchor: DOMRect; onClose: () => void; children: React.ReactNode; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>('button:not(:disabled)')?.focus();
    const down = (e: PointerEvent) => { if (!ref.current?.contains(e.target as Node)) onClose(); };
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); onClose(); return; }
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
      const els = [...(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled)') ?? [])];
      if (!els.length) return;
      e.preventDefault();
      const i = els.indexOf(document.activeElement as HTMLElement);
      els[(i + (e.key === 'ArrowDown' ? 1 : els.length - 1)) % els.length].focus();
    };
    window.addEventListener('pointerdown', down, true); window.addEventListener('keydown', key, true);
    return () => { window.removeEventListener('pointerdown', down, true); window.removeEventListener('keydown', key, true); };
  }, [onClose]);
  const left = Math.max(8, Math.min(anchor.right - 300, window.innerWidth - 320));
  return createPortal(
    <div ref={ref} className="xm-pop xm-pop--fixed" role="menu" aria-label={label} style={{ left, top: anchor.bottom + 4 }}>{children}</div>, document.body);
}

export function ExportMenuBody({ defs, windowId, onClose }: { defs: readonly ExportableDef[]; windowId: string; onClose: () => void }) {
  const ctx = useExportContext();
  const [status, setStatus] = useState<QuickResult | null>(null);
  const [busy, setBusy] = useState(false);
  const run = useCallback(async (sel: ExportableDef[], formats: (d: ExportableDef) => ExportFormat[], pack: boolean) => {
    setBusy(true);
    const r = await quickExport(sel, Object.fromEntries(sel.map((d) => [d.id, formats(d)])), pack, ctx);
    setStatus(r); setBusy(false);
  }, [ctx]);
  const ready = defs.filter((d) => (d.describe().state ?? 'ready') === 'ready');
  const savePdf = useCallback(async (d: ExportableDef) => {
    setBusy(true);
    try {
      const ok = await d.print?.({ ...managerStore.get().options });
      setStatus({ ok: !!ok, word: ok ? 'Print dialog open' : 'Print blocked' });
    } catch { setStatus({ ok: false, word: 'Print blocked' }); }
    setBusy(false);
  }, []);
  const open = (selectAll: boolean) => {
    if (selectAll) managerStore.dispatch({ t: 'selectMany', defs: ready, on: true });
    managerStore.dispatch({ t: 'query', q: '' });
    wm.openWindow('expt', 'dock');
    onClose();
  };
  return (
    <>
      <div className="xm-head">{`${windowTitle(windowId)}, ${defs.length} ${defs.length === 1 ? 'item' : 'items'}`}</div>
      {defs.length === 0 && <div className="xm-head">Nothing to export</div>}
      {defs.map((d) => {
        const desc = d.describe(); const ok = (desc.state ?? 'ready') === 'ready';
        return (
          <div key={d.id} className="xm-row" role="group" aria-label={d.title}>
            <div className="xm-rowhead"><span>{d.title}</span><small>{ok ? desc.provenance.label : STATE_WORD[desc.state ?? 'not-served']}</small></div>
            <div className="xm-fmts">
              {d.formats.map((f) => (
                <button key={f} type="button" role="menuitem" className="xm-fmt" disabled={!ok || busy} aria-label={`${d.title}, ${FORMAT_LABEL[f]}`}
                  onClick={() => void run([d], () => [f], false)}>{FORMAT_LABEL[f]}</button>
              ))}
              {d.print && (
                <button type="button" role="menuitem" className="xm-fmt" disabled={!ok || busy} title={SAVE_PDF_TIP} aria-label={`${d.title}, ${SAVE_PDF_LABEL}`} data-testid="xm-print-pdf"
                  onClick={() => void savePdf(d)}>{SAVE_PDF_LABEL}</button>
              )}
            </div>
          </div>
        );
      })}
      {defs.length > 0 && (
        <>
          <div className="xm-sep" role="separator" />
          <button type="button" role="menuitem" className="xm-item" disabled={ready.length === 0 || busy}
            onClick={() => void run(ready, (d) => [d.formats[0]], true)}>Export all as one pack<small>{`${ready.length} ${ready.length === 1 ? 'file' : 'files'}`}</small></button>
        </>
      )}
      <button type="button" role="menuitem" className="xm-item" onClick={() => open(true)}>Choose in Export<Icon name="external-link" size="xs" /></button>
      {status && <div className={`xm-head xm-status ${status.ok ? 'ok' : 'bad'}`} role="status">{status.word}</div>}
    </>
  );
}

/** The Export button of a window header: opens the window's menu. `variant` styles it for the title bar or inside a panel. */
export function ExportButton({ windowId, itemId, label = 'Export', variant = 'panel' }: { windowId?: string; itemId?: string; label?: string; variant?: 'panel' | 'bar' | 'icon' }) {
  const all = useExportables();
  const defs = useMemo(() => {
    if (itemId) { const d = getExportable(itemId); return d ? [d] : []; }
    return all.filter((d) => d.windowId === windowId);
  }, [all, windowId, itemId]);
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => { setAnchor(null); btn.current?.focus(); }, []);
  const wid = windowId ?? defs[0]?.windowId ?? '';
  const none = defs.length === 0;
  const cls = variant === 'bar' ? 'wm-btn' : variant === 'icon' ? 'icon-btn' : 'xm-btn';
  return (
    <span className="xm-wrap">
      <button ref={btn} type="button" className={cls} aria-haspopup="menu" aria-expanded={anchor !== null} aria-label={`${label}${none ? ', nothing to export' : ''}`}
        title={none ? 'Nothing to export' : label} disabled={none}
        onClick={() => setAnchor(btn.current?.getBoundingClientRect() ?? null)}>
        <Icon name="file-down" size="sm" />{variant === 'panel' && <span>{label}</span>}
      </button>
      {anchor && <Popover anchor={anchor} onClose={close} label={`${label} ${windowTitle(wid)}`}><ExportMenuBody defs={defs} windowId={wid} onClose={close} /></Popover>}
    </span>
  );
}

/** Title-bar button used by the window chrome. */
export const WindowExportButton = ({ windowId }: { windowId: string }) => <ExportButton windowId={windowId} variant="bar" />;

/** Right click (or the ContextMenu key, Shift+F10) on a table or figure of a panel: export that one registered item.
 *    const cx = useExportContextMenu('ports:table');
 *    <div onContextMenu={cx.onContextMenu} onKeyDown={cx.onKeyDown}>...</div>{cx.menu}
 *  Lists up to four of the item's formats, then "Open Export". */
export function useExportContextMenu(itemId: string) {
  const all = useExportables();
  const ctx = useExportContext();
  const def = useMemo(() => all.find((d) => d.id === itemId) ?? null, [all, itemId]);
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  const [word, setWord] = useState<QuickResult | null>(null);
  const items: ContextMenuItem[] = def ? [
    ...def.formats.slice(0, 4).map((f) => ({ key: f, label: `Export ${FORMAT_LABEL[f]}`, icon: 'download' as const,
      onSelect: () => { void quickExport([def], { [def.id]: [f] }, false, ctx).then((r) => { setWord(r); setTimeout(() => setWord(null), 3200); }); } })),
    { key: 'open', label: 'Open Export', icon: 'file-down' as const, onSelect: () => { managerStore.dispatch({ t: 'selectMany', defs: [def], on: true }); wm.openWindow('expt', 'dock'); } },
  ] : [];
  return {
    word,
    onContextMenu: (e: React.MouseEvent) => { if (!def) return; e.preventDefault(); setAt({ x: e.clientX, y: e.clientY }); },
    onKeyDown: (e: React.KeyboardEvent) => {
      if (!def || !(e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10'))) return;
      e.preventDefault(); const r = (e.currentTarget as Element).getBoundingClientRect(); setAt({ x: r.left + 24, y: r.top + 24 });
    },
    menu: at && def ? <ContextMenu x={at.x} y={at.y} items={items} label={`Export ${def.title}`} onClose={() => setAt(null)} /> : null,
  };
}
