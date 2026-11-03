import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { Icon, type IconName } from '../ui/Icon';
import './contextMenu.css';

export interface ContextMenuItem {
  key: string;
  label: string;
  icon?: IconName;
  disabled?: boolean;
  onSelect: () => void;
}

/**
 * A small context menu: portalled so no window clip can cut it,
 * opened at a point (right click) or at an element (the ContextMenu key or
 * Shift+F10). Arrow keys move, Enter or Space picks, Esc closes and the
 * caller gets focus back through `onClose`. Entries are control names, not
 * sentences.
 */
export function ContextMenu({ x, y, items, label, onClose }: { x: number; y: number; items: ContextMenuItem[]; label: string; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const first = ref.current?.querySelector<HTMLButtonElement>('button:not(:disabled)');
    first?.focus();
    const down = (e: PointerEvent) => { if (!ref.current?.contains(e.target as Node)) onClose(); };
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); onClose(); return; }
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
      const btns = [...(ref.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? [])];
      if (!btns.length) return;
      e.preventDefault();
      const i = btns.indexOf(document.activeElement as HTMLButtonElement);
      btns[(i + (e.key === 'ArrowDown' ? 1 : btns.length - 1)) % btns.length].focus();
    };
    window.addEventListener('pointerdown', down, true);
    window.addEventListener('keydown', key, true);
    window.addEventListener('blur', onClose);
    return () => { window.removeEventListener('pointerdown', down, true); window.removeEventListener('keydown', key, true); window.removeEventListener('blur', onClose); };
  }, [onClose]);
  const left = Math.max(8, Math.min(x, window.innerWidth - 220));
  const top = Math.max(8, Math.min(y, window.innerHeight - 8 - 30 * items.length));
  return createPortal(
    <div ref={ref} className="ctx-menu" role="menu" aria-label={label} style={{ left, top }}>
      {items.map((it) => (
        <button key={it.key} type="button" role="menuitem" className="ctx-menu-item" disabled={it.disabled}
          onClick={() => { onClose(); it.onSelect(); }}>
          {it.icon && <Icon name={it.icon} size="sm" />}<span>{it.label}</span>
        </button>))}
    </div>, document.body);
}
