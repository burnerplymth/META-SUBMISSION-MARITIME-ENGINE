import React, { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { approxLegCount, landPath, mapModel, routeColors, type PlaceChoice } from './logic';
import type { PlanDoc } from './types';

/* Route preview: the places of every candidate route and the legs between
 * them, over a land outline, with the disrupted place marked. A leg is drawn
 * along its lane polyline (MODELLED lane geometry, checked against land) when
 * the plan carries one; a leg without lane geometry is its straight chord,
 * dashed, and counted by the Approx. chip.
 * Selecting a route here selects it in the chart, the table and the timeline. */

let landCache: unknown = null;
let landPending: Promise<unknown> | null = null;
function useLand(): unknown {
  const [land, setLand] = useState<unknown>(landCache);
  React.useEffect(() => {
    if (landCache) return;
    landPending ??= import('../../../../map/src/data/land-110m.geo.json').then((m) => (landCache = (m as { default?: unknown }).default ?? m)).catch(() => null);
    let live = true;
    void landPending.then((g) => { if (live && g) setLand(g); });
    return () => { live = false; };
  }, []);
  return land;
}

export function MapPreview({ doc, selected, onSelect, backdrop }: {
  doc: PlanDoc; selected: string | null; onSelect: (id: string | null) => void; backdrop: readonly PlaceChoice[];
}) {
  const host = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(520);
  const land = useLand();
  useLayoutEffect(() => {
    const el = host.current;
    if (!el) return;
    setW(Math.max(260, Math.floor(el.getBoundingClientRect().width)));
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver((es) => { const cr = es[0]?.contentRect; if (cr) setW(Math.max(260, Math.floor(cr.width))); });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const hgt = Math.round(Math.min(420, Math.max(240, w * 0.62)));
  const m = useMemo(() => mapModel(doc, w, hgt, selected), [doc, w, hgt, selected]);
  const colors = useMemo(() => routeColors(doc.routes), [doc.routes]);
  const land2 = useMemo(() => (land ? landPath(land, m) : ''), [land, m]);
  const dots = useMemo(
    () => backdrop.filter((p) => p.lon >= m.bbox.lon0 && p.lon <= m.bbox.lon1 && p.lat >= m.bbox.lat0 && p.lat <= m.bbox.lat1).slice(0, 700),
    [backdrop, m],
  );
  const approx = approxLegCount(doc);
  const ordered = [...m.lines].sort((a, b) => Number(a.selected) - Number(b.selected));
  return (
    <div className="lp-map" ref={host} data-testid="lp-map">
      <svg width={w} height={hgt} viewBox={`0 0 ${w} ${hgt}`} role="img" aria-label="Route preview" onClick={() => onSelect(null)}>
        <rect x={0} y={0} width={w} height={hgt} className="lp-ocean" />
        {land2 && <path d={land2} className="lp-land" />}
        {dots.map((p) => <circle key={p.id} cx={m.x(p.lon)} cy={m.y(p.lat)} r={1.3} className="lp-dot" />)}
        {ordered.map((l) => (
          <g key={l.routeId}>
            <path d={l.d} className="lp-halo" style={{ stroke: l.color }} />
            {l.d && <path d={l.d} className={`lp-line${l.selected ? ' sel' : ''}${l.dashed ? ' dashed' : ''}`} style={{ stroke: l.color }} />}
            {l.approx && <path d={l.approx} className={`lp-line approx${l.selected ? ' sel' : ''}`} style={{ stroke: l.color }} data-testid="lp-approx" />}
            <path d={`${l.d}${l.approx}`} className="lp-hit" onClick={(e) => { e.stopPropagation(); onSelect(l.routeId); }}><title>{l.label}</title></path>
          </g>
        ))}
        {m.points.map((p) => {
          const major = p.role !== 'waypoint';
          return (
            <g key={p.id} className={`lp-pt lp-pt--${p.role}`}>
              <circle cx={p.x} cy={p.y} r={major ? 4 : 2.2} className="lp-ptc" />
              {p.disrupted && <path d={`M${p.x} ${p.y - 8}L${p.x + 8} ${p.y}L${p.x} ${p.y + 8}L${p.x - 8} ${p.y}Z`} className="lp-closure" />}
              {major && <text x={p.x + (p.x > w * 0.7 ? -8 : 8)} y={p.y - 6} textAnchor={p.x > w * 0.7 ? 'end' : 'start'} className="lp-ptl">{p.name}</text>}
            </g>
          );
        })}
      </svg>
      <ul className="lp-legend" aria-label="Routes">
        {doc.routes.map((r) => {
          const dashed = !!doc.disruption && r.disrupted_place_on_route;
          return (
            <li key={r.id}>
              <button type="button" className={`lp-key${selected === r.id ? ' on' : ''}`} onClick={() => onSelect(selected === r.id ? null : r.id)}>
                <svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4" stroke={colors[r.id]} strokeWidth="2.5" strokeDasharray={dashed ? '4 3' : undefined} /></svg>
                <span>{r.label}</span>
              </button>
            </li>
          );
        })}
        {approx > 0 && <li className="lp-key lp-key--approx"><svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4" strokeWidth="2" strokeDasharray="3 3" /></svg><span>{`Approx. ${approx}`}</span></li>}
        {doc.disruption && <li className="lp-key lp-key--closure"><svg width="14" height="14" aria-hidden="true"><path d="M7 1L13 7L7 13L1 7Z" /></svg><span>{doc.disruption.name}</span></li>}
      </ul>
    </div>
  );
}
