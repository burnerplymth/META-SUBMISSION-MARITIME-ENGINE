import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from '../ui/Icon';
import { closeSources, useSourcesOpen } from './sourcesStore';
import { SourcesList } from './SourcesList';
import { CREDITS_VERSION } from './credits';
import './sources.css';

/** Sources: every external source with its credit line and licence, from the registry. Mounted once. */
export function SourcesDialog() {
  const open = useSourcesOpen();
  const shell = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    shell.current?.querySelector<HTMLElement>('[data-src-close]')?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); closeSources(); } };
    window.addEventListener('keydown', key, true);
    return () => { window.removeEventListener('keydown', key, true); if (opener && document.contains(opener)) opener.focus(); };
  }, [open]);
  if (!open) return null;
  return createPortal(
    <aside ref={shell} className="src-dialog" role="dialog" aria-label="Sources" data-testid="sources-dialog">
      <header className="src-dhead">
        <span className="src-kicker">Sources</span>
        <span className="mono dim small">{CREDITS_VERSION}</span>
        <button type="button" className="icon-btn" data-src-close aria-label="Close sources" onClick={closeSources}><Icon name="close" /></button>
      </header>
      <div className="src-body"><SourcesList /></div>
    </aside>, document.body);
}
