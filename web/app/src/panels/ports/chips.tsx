import React from 'react';
import { CHIP_STATE, type CellView, type ChipKind } from './model';
import { Status } from '../../ui/Status';
import './ports.css';

/* Provenance / state chips shared by the Ports and Live panels: the one
 * `Status` (drawn mark + word, state is never colour alone); the as-of rides
 * inside the chip when one was served. */

export const chipClass = (k: ChipKind): string => `pv-prov pv-prov--${k.toLowerCase().replace(' ', '-')}`;

export function ProvChip({ kind, asOf, title, label }: { kind: ChipKind; asOf?: string | null; title?: string; label?: string }) {
  return (
    <span className={chipClass(kind)} data-chip-kind={kind}>
      <Status state={CHIP_STATE[kind]} word={label ?? kind} meta={asOf ?? undefined} title={title} />
    </span>
  );
}

/** A cell as two stacked lines: figure + unit, then its chip with as-of.
 *  A NOT SERVED cell is the hatched chip alone (never a zero). */
export function CellBox({ v, align = 'left' }: { v: CellView; align?: 'left' | 'right' }) {
  return (
    <span className={`pv-cell pv-cell--${align}`} title={v.title || undefined}>
      {v.notServed ? null : <span className="pv-fig"><b>{v.text}</b>{v.unit ? <small>{v.unit}</small> : null}</span>}
      <ProvChip kind={v.chip} asOf={v.asOf} />
    </span>
  );
}

/** Plain small tag (tier / kind / rule / meta): hairline, mono caps. */
export function MetaTag({ children, title, strong }: { children: React.ReactNode; title?: string; strong?: boolean }) {
  return <span className={`pv-tag${strong ? ' pv-tag--strong' : ''}`} title={title}>{children}</span>;
}
