import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Chip } from './Chip';
import type { BasisRow, BasisWord } from './basis';

/** The basis chip in a panel header: COMPOSITE (with a popover that
 *  lists each feed and its own state) or LOCAL (a stored collection, no feed).
 *  The popover is portalled so the header's overflow clip cannot cut it; it
 *  opens on click or Enter, closes on Escape, outside click, scroll or resize.
 *  The button is a normal tab stop. */
export function BasisStamp({ word, meta, rows = [], source }: { word: BasisWord; meta?: string; rows?: readonly BasisRow[]; source?: string }) {
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState<{ left: number; top: number } | null>(null);
  const btn = useRef<HTMLButtonElement | null>(null);
  const pop = useRef<HTMLDivElement | null>(null);
  const hasRows = rows.length > 0;

  useEffect(() => {
    if (!open) return;
    const place = () => {
      const r = btn.current?.getBoundingClientRect();
      if (!r) return;
      const width = 260;
      setAt({ left: Math.max(8, Math.min(r.right - width, window.innerWidth - width - 8)), top: r.bottom + 4 });
    };
    place();
    const raf = window.requestAnimationFrame(() => pop.current?.focus()); // the popover mounts after this effect; focus moves in, Esc returns it to the chip
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); btn.current?.focus(); } };
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (btn.current?.contains(t) || pop.current?.contains(t)) return;
      setOpen(false);
    };
    const close = () => setOpen(false);
    window.addEventListener('keydown', onKey);
    window.addEventListener('pointerdown', onDown);
    window.addEventListener('resize', close);
    window.addEventListener('scroll', close, true);
    return () => {
      window.cancelAnimationFrame(raf);
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('pointerdown', onDown);
      window.removeEventListener('resize', close);
      window.removeEventListener('scroll', close, true);
    };
  }, [open]);

  const tip = hasRows ? rows.map((r) => `${r.state?.word ?? '—'} ${r.label}`).join(' · ') : source;
  const chip = <Chip kind="meta" word={word} meta={meta} />;
  return (
    <span className="basis-stamp" data-basis={word}>
      {hasRows ? (
        <button ref={btn} type="button" className="chip-btn basis-btn" aria-haspopup="true" aria-expanded={open}
          aria-label={`${word}${meta ? ` ${meta}` : ''}: list the feeds`} title={tip} onClick={() => setOpen((o) => !o)}>
          {chip}
        </button>
      ) : <span title={tip}>{chip}</span>}
      {source && <span className="basis-src">{source}</span>}
      {open && at && createPortal(
        <div ref={pop} className="pop basis-pop mono" role="dialog" tabIndex={-1} aria-label={`${word} feeds`} style={{ position: 'fixed', left: at.left, top: at.top, right: 'auto', width: 260 }}>
          <span className="pop-h">Feeds in this panel</span>
          {rows.map((r) => (
            <span key={r.label} className="pop-row basis-row">
              {r.state ? <Chip kind={r.state.kind} word={r.state.word} title={r.state.note ?? undefined} /> : <Chip kind="meta" word="No board entry" />}
              <span className="basis-label">{r.label}</span>
              <span className="pop-k">{r.count}</span>
            </span>
          ))}
        </div>,
        document.body,
      )}
    </span>
  );
}
