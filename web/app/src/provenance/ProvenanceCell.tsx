import React, { useCallback, useState } from 'react';
import { ContextMenu } from '../components/ContextMenu';
import { openProvenance, type ProvRef } from './registry';

/* ProvenanceCell: wraps one displayed number. Click or Enter opens the
 * drawer; right-click, the ContextMenu key or Shift+F10 offers "Where from".
 * `make` builds the reference lazily so a table of cells costs nothing until
 * one is opened. */
export function ProvenanceCell({ make, children, className = '' }: { make: () => ProvRef; children: React.ReactNode; className?: string }) {
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const open = useCallback(() => openProvenance(make()), [make]);
  const close = useCallback(() => setMenu(null), []);
  return (
    <>
      <span className={`prov-cell ${className}`.trim()} role="button" tabIndex={0} aria-haspopup="dialog" data-prov
        onClick={(e) => { e.stopPropagation(); open(); }}
        onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); setMenu({ x: e.clientX, y: e.clientY }); }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); open(); return; }
          if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
            e.preventDefault(); e.stopPropagation();
            const r = e.currentTarget.getBoundingClientRect();
            setMenu({ x: r.left, y: r.bottom });
          }
        }}>
        {children}
      </span>
      {menu && <ContextMenu x={menu.x} y={menu.y} label="Value actions" onClose={close} items={[{ key: 'where', label: 'Where from', onSelect: open }]} />}
    </>
  );
}
