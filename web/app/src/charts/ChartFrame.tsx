import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Chip } from '../panels/reports/chips';
import { VNodes } from './VNodeView';
import { ExportMenu } from './ExportMenu';
import { buildAny } from './build';
import { runExport, type ExportKind, type ExportSkin, type ServerExport } from './exportDom';
import { hitColumns, hoverByX, hoverByXY, placeTooltip, stepColumn, type Hover } from './interact';
import { brushDomain, sameDomain, zoomDomain, type Domain } from './scale';
import { captionParts } from './serialize';
import { useChartExport } from '../exports/chartExport';
import type { ChartSpec, Hit } from './types';
import './charts.css';

/* A chart in a panel: header (title, provenance chip, EXPORT menu),
 * legend toggles, the SVG built by charts/build.ts, a hover crosshair
 * with tooltip, brush-to-zoom on the x axis, a TABLE view of the plotted
 * numbers, and the provenance caption. The same spec feeds the exports,
 * so a file is the chart as drawn here. */

export interface ChartFrameProps {
  spec: ChartSpec;
  /** plot height for line / scatter; other types size themselves */
  height?: number;
  /** a URL that reproduces this chart, or a getter */
  link?: string | null | (() => string | null);
  onSelect?: (hit: Hit) => void;
  /** places to ring on the chart */
  highlightPlaces?: readonly string[];
  /** export canvas width in px */
  exportWidth?: number;
  bare?: boolean;
  /** the same chart drawn by the exports route, when it maps to one */
  serverExport?: ServerExport | null;
}

function useWidth(ref: React.RefObject<HTMLElement>): number {
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(Math.floor(el.getBoundingClientRect().width));
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver((es) => { const cr = es[0]?.contentRect; if (cr) setW(Math.floor(cr.width)); });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

export function ChartFrame({ spec, height = 220, link, onSelect, highlightPlaces, exportWidth = 960, bare = false, serverExport }: ChartFrameProps) {
  const host = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const width = useWidth(host);
  const [domain, setDomain] = useState<Domain | null>(null);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());
  const [hover, setHover] = useState<Hover | null>(null);
  const [brush, setBrush] = useState<{ a: number; b: number } | null>(null);
  const [table, setTable] = useState(false);
  const [kbCol, setKbCol] = useState<number | null>(null);

  useEffect(() => { setDomain(null); setHover(null); setKbCol(null); }, [spec.id]);

  const w = Math.max(160, width || 0);
  const model = useMemo(() => buildAny(spec, { width: w, height, domain, hidden }), [spec, w, height, domain, hidden]);
  const cols = useMemo(() => hitColumns(model.hits), [model.hits]);
  const cx = useChartExport(spec, { width: exportWidth, height }, () => model.table.rows.length);
  const zoomed = !!domain && !sameDomain(domain, model.fullDomain);

  const local = (e: { clientX: number; clientY: number }) => {
    const r = svgRef.current?.getBoundingClientRect();
    return r ? { x: e.clientX - r.left, y: e.clientY - r.top } : { x: 0, y: 0 };
  };
  const inPlot = (p: { x: number; y: number }) => p.x >= model.plot.x && p.x <= model.plot.x + model.plot.w && p.y >= model.plot.y && p.y <= model.plot.y + model.plot.h;

  const onMove = (e: React.PointerEvent) => {
    const p = local(e);
    if (brush) { setBrush({ a: brush.a, b: Math.max(model.plot.x, Math.min(model.plot.x + model.plot.w, p.x)) }); return; }
    setKbCol(null);
    setHover(model.hitMode === 'x' ? (inPlot(p) ? hoverByX(model.hits, p.x) : null) : hoverByXY(model.hits, p.x, p.y));
  };
  const onDown = (e: React.PointerEvent) => {
    if (e.button !== 0 || !model.brushable || !model.xScale) return;
    const p = local(e);
    if (!inPlot(p)) return;
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    setBrush({ a: p.x, b: p.x });
  };
  const onUp = () => {
    if (!brush) return;
    const d = model.xScale ? brushDomain(model.xScale, brush.a, brush.b) : null;
    setBrush(null);
    if (d) { setDomain(d); setHover(null); }
    else if (hover && onSelect) onSelect(hover.hits[0]);
  };
  const onClickBars = () => { if (!model.brushable && hover && onSelect) onSelect(hover.hits[0]); };

  const zoomBy = useCallback((f: number) => {
    if (!model.fullDomain || !model.domain) return;
    const next = zoomDomain(model.domain, model.fullDomain, f);
    setDomain(sameDomain(next, model.fullDomain) ? null : next);
  }, [model.fullDomain, model.domain]);
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) { cx.onKeyDown(e); return; }
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault();
      const next = stepColumn(cols, kbCol ?? hover?.crosshairX ?? hover?.anchorX ?? null, e.key === 'ArrowRight' ? 1 : -1);
      if (next == null) return;
      setKbCol(next);
      setHover(model.hitMode === 'x' ? hoverByX(model.hits, next, 2) : (() => {
        const hit = model.hits.find((h) => Math.abs(h.x - next) < 0.75);
        return hit ? { hits: [hit], anchorX: hit.x, anchorY: hit.y, crosshairX: null } : null;
      })());
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (model.hitMode === 'xy') {
        e.preventDefault();
        const order = [...model.hits].sort((a, b) => a.y - b.y || a.x - b.x);
        const i = order.findIndex((h) => hover?.hits[0] === h);
        const hit = order[Math.min(order.length - 1, Math.max(0, (i < 0 ? -1 : i) + (e.key === 'ArrowDown' ? 1 : -1)))];
        if (hit) setHover({ hits: [hit], anchorX: hit.x, anchorY: hit.y, crosshairX: null });
      }
    } else if (e.key === 'Enter' && hover && onSelect) { e.preventDefault(); onSelect(hover.hits[0]); }
    else if ((e.key === '+' || e.key === '=') && model.brushable) { e.preventDefault(); zoomBy(0.5); }
    else if (e.key === '-' && model.brushable) { e.preventDefault(); zoomBy(2); }
    else if (e.key === '0' || e.key === 'Escape') { setDomain(null); setHover(null); setKbCol(null); }
  };

  const toggle = (key: string) => setHidden((cur) => {
    const n = new Set(cur);
    if (n.has(key)) n.delete(key); else if (n.size < model.legend.length - 1) n.add(key);
    return n;
  });

  const getLink = (): string | null => (typeof link === 'function' ? link() : link ?? null);
  const run = (kind: ExportKind, skin: ExportSkin) => runExport(kind, { spec, frame: { width: exportWidth, chartHeight: height, domain, hidden }, skin }, getLink(), serverExport);

  const hl = new Set(highlightPlaces ?? []);
  const ringHits = hl.size ? model.hits.filter((h) => h.placeId && hl.has(h.placeId)) : [];
  const tip = hover ? (() => {
    const rows = hover.hits.flatMap((h) => h.rows.map((r) => ({ ...r, color: h.color })));
    const sz = { w: Math.min(260, Math.max(130, Math.max(...rows.map((r) => (r.k.length + r.v.length) * 6.4 + 34), hover.hits[0].title.length * 6.4 + 20))), h: 22 + rows.length * 15 };
    return { rows, sz, pos: placeTooltip(hover.anchorX, hover.anchorY, { w: model.width, h: model.height }, sz) };
  })() : null;

  return (
    <figure className={`cx-frame${bare ? ' cx-bare' : ''}`} data-chart={spec.id} data-chart-type={spec.type} onContextMenu={cx.onContextMenu}>
      {cx.menu}
      {!bare && (
        <figcaption className="cx-head">
          <span className="cx-title" title={spec.title}>{spec.title}</span>
          <Chip chip={spec.provenance.chip} />
          <span className="cx-spacer" />
          {zoomed && <button type="button" className="cx-btn" onClick={() => setDomain(null)}>Reset zoom</button>}
          <button type="button" className="cx-btn" aria-pressed={table} onClick={() => setTable((t) => !t)}>Table</button>
          <ExportMenu onRun={run} hasLink={!!getLink()} server={serverExport} />
          {cx.word && <span className={`cx-status ${cx.word.ok ? 'ok' : 'bad'}`} role="status">{cx.word.text}</span>}
        </figcaption>
      )}
      {!bare && spec.subtitle && <div className="cx-sub">{spec.subtitle}</div>}
      {!bare && model.legend.length > 1 && spec.type !== 'spark' && (
        <div className="cx-legend" role="group" aria-label="Series">
          {model.legend.map((l) => (
            <button key={l.key} type="button" className={`cx-key${hidden.has(l.key) ? ' off' : ''}`} aria-pressed={!hidden.has(l.key)} onClick={() => toggle(l.key)}>
              <i style={{ background: l.color }} aria-hidden="true" />{l.label}
            </button>
          ))}
        </div>
      )}
      <div ref={host} className="cx-host">
        {width > 0 && !table && (
          <div className="cx-stage" style={{ width: model.width, height: model.height }}>
            <svg ref={svgRef} className="cx-svg" width={model.width} height={model.height} viewBox={`0 0 ${model.width} ${model.height}`}
              role="application" aria-label={`${spec.title}. ${spec.provenance.chip}`} tabIndex={0}
              onPointerMove={onMove} onPointerDown={onDown} onPointerUp={onUp} onPointerLeave={() => { if (!brush) setHover(null); }}
              onClick={onClickBars} onDoubleClick={() => setDomain(null)} onKeyDown={onKey} onBlur={() => setKbCol(null)}>
              <VNodes nodes={model.nodes} />
              <g className="cx-over" pointerEvents="none">
                {hover?.crosshairX != null && <line x1={hover.crosshairX} x2={hover.crosshairX} y1={model.plot.y} y2={model.plot.y + model.plot.h} className="cx-cross" />}
                {hover?.hits.filter((h) => h.seriesKey !== '__event').map((h, i) => <circle key={i} cx={h.x} cy={h.y} r={5.5} className="cx-ring" style={{ stroke: h.color }} />)}
                {ringHits.map((h, i) => <circle key={`s${i}`} cx={h.x} cy={h.y} r={8} className="cx-sel" />)}
                {brush && <rect x={Math.min(brush.a, brush.b)} y={model.plot.y} width={Math.abs(brush.b - brush.a)} height={model.plot.h} className="cx-brush" />}
              </g>
            </svg>
            {tip && hover && (
              <div className="cx-tip" role="status" style={{ left: tip.pos.left, top: tip.pos.top, width: tip.sz.w }}>
                <b>{hover.hits[0].title}</b>
                {tip.rows.map((r, i) => (
                  <div key={i} className="cx-trow"><i style={{ background: r.color }} aria-hidden="true" /><span>{r.k}</span><span className="cx-tv">{r.v}</span></div>
                ))}
              </div>
            )}
          </div>
        )}
        {table && (
          <div className="cx-table-wrap" tabIndex={0} role="region" aria-label={`${spec.title}, plotted values`}>
            <table className="cx-table">
              <thead><tr>{model.table.columns.map((c) => <th key={c} scope="col">{c}</th>)}</tr></thead>
              <tbody>
                {model.table.rows.slice(0, 300).map((r, i) => (
                  <tr key={i}>{r.map((v, j) => <td key={j} className={typeof v === 'number' ? 'num' : undefined}>{v == null ? <span className="cx-ns">Not served</span> : String(v)}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {!bare && (
        <div className="cx-caption mono">
          {captionParts(spec.provenance).map((p, i) => <span key={i}>{p}</span>)}
        </div>
      )}
    </figure>
  );
}
