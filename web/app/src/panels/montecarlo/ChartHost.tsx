import React, { useEffect, useRef, useState } from 'react';
import { VNodes } from '../../charts/VNodeView';
import type { McChart } from './charts';

/** The width of an element, following its size. */
export function useWidth(ref: React.RefObject<HTMLElement>, fallback = 480): number {
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => { const x = Math.floor(el.getBoundingClientRect().width); if (x > 0) setW(x); };
    read();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

/** One chart: the shared VNode tree on an svg, a vertical guide and a tooltip over the hovered column. */
export function ChartHost({ chart, label, testid, onPick }: { chart: McChart; label: string; testid?: string; onPick?: (cx: number) => void }) {
  const [at, setAt] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const col = at === null ? null : chart.hover[at] ?? null;
  const move = (e: React.MouseEvent) => {
    if (chart.hover.length === 0) return;
    const r = box.current?.getBoundingClientRect();
    if (!r) return;
    const x = e.clientX - r.left;
    const i = chart.hover.findIndex((c) => x >= c.x0 && x < c.x1);
    setAt(i < 0 ? null : i);
  };
  return (
    <div className="mc-chart" ref={box} data-testid={testid} onMouseMove={move} onMouseLeave={() => setAt(null)}
      onClick={() => { if (col && onPick) onPick(col.cx); }}>
      <svg className="mc-svg" width={chart.width} height={chart.height} viewBox={`0 0 ${chart.width} ${chart.height}`} role="img" aria-label={label}>
        <VNodes nodes={chart.nodes} />
        {col && <line className="mc-guide" x1={col.cx} x2={col.cx} y1={chart.plot.y} y2={chart.plot.y + chart.plot.h} />}
      </svg>
      {col && (
        <div className="mc-tip" style={{ left: Math.min(chart.width - 150, Math.max(0, col.cx + 10)), top: chart.plot.y }} role="status">
          <b>{col.title}</b>
          {col.rows.map((r) => <div key={r.k} className="mc-tip-r"><span>{r.k}</span><span className="mc-tv">{r.v}</span></div>)}
        </div>
      )}
    </div>
  );
}
