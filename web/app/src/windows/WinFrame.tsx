import React, { useEffect, useRef } from 'react';
import { applyHeadingLevels } from './headings';

/** The frame every window body sits in (dock and phone): the `.wm-win` element, plus the window's heading levels,
 *  re-applied when the window's content changes. */
export function WinFrame({ id, children }: { id: string; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    let raf = 0;
    const run = () => { raf = 0; applyHeadingLevels(el); };
    run();
    if (typeof MutationObserver === 'undefined') return undefined;
    const mo = new MutationObserver(() => { if (!raf) raf = window.requestAnimationFrame(run); });
    mo.observe(el, { childList: true, subtree: true });
    return () => { mo.disconnect(); if (raf) window.cancelAnimationFrame(raf); };
  }, []);
  return <div ref={ref} className="wm-win" data-wid={id}>{children}</div>;
}
