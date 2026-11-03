import React, { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Chip } from '../reports/chips';
import { nearestPlace, projector, type PlacePoint } from './miniMap';

/* MAP VIEW for place datasets: every place as a faint dot, the places
 * in the selected rows ringed and named. Clicking a place selects it
 * through the shell's place bus (selectNode), so the real map, the
 * chain and the inspector follow. */

export function MapView({ places, highlighted, current, onPick }: {
  places: PlacePoint[]; highlighted: readonly string[]; current: string | null; onPick: (id: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(640);
  const [hover, setHover] = useState<PlacePoint | null>(null);
  useLayoutEffect(() => {
    const el = host.current;
    if (!el) return;
    setW(Math.max(260, Math.floor(el.getBoundingClientRect().width)));
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver((es) => { const cr = es[0]?.contentRect; if (cr) setW(Math.max(260, Math.floor(cr.width))); });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const h = Math.round(w * 0.5);
  const pr = useMemo(() => projector(w, h), [w, h]);
  const hl = useMemo(() => new Set(highlighted), [highlighted]);
  const byId = useMemo(() => new Map(places.map((p) => [p.id, p] as const)), [places]);
  const hot = highlighted.map((id) => byId.get(id)).filter((p): p is PlacePoint => !!p);
  const missing = highlighted.filter((id) => !byId.has(id));

  const local = (e: { clientX: number; clientY: number; currentTarget: Element }) => { const r = (e.currentTarget as Element).getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
  const meridians = [-150, -120, -90, -60, -30, 0, 30, 60, 90, 120, 150];
  const parallels = [-60, -30, 0, 30, 60];

  return (
    <div className="dv-map" ref={host}>
      <div className="dv-mapbar">
        <Chip chip="SOURCED" word="PLACES" />
        <span className="dv-mapn mono">{places.length.toLocaleString('en-US')} PLACES · {hot.length} IN SELECTION</span>
        {missing.length > 0 && <Chip chip="NOT SERVED" word={`${missing.length} WITHOUT COORDINATES`} />}
      </div>
      <svg className="dv-mapsvg" width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label="Places in the selected rows"
        onPointerMove={(e) => { const p = local(e); setHover(nearestPlace(places, pr, p.x, p.y, 9)); }}
        onPointerLeave={() => setHover(null)}
        onClick={(e) => { const p = local(e); const n = nearestPlace(places, pr, p.x, p.y, 9); if (n) onPick(n.id); }}>
        {meridians.map((m) => <line key={`m${m}`} x1={pr.x(m)} x2={pr.x(m)} y1={pr.y(80)} y2={pr.y(-60)} className="dv-grat" />)}
        {parallels.map((p) => <line key={`p${p}`} x1={pr.x(-180)} x2={pr.x(180)} y1={pr.y(p)} y2={pr.y(p)} className="dv-grat" />)}
        {places.map((p) => !hl.has(p.id) && <circle key={p.id} cx={pr.x(p.lon)} cy={pr.y(p.lat)} r={1.6} className="dv-dot" />)}
        {hot.map((p) => (
          <g key={p.id}>
            <circle cx={pr.x(p.lon)} cy={pr.y(p.lat)} r={p.id === current ? 8 : 6} className={`dv-hot${p.id === current ? ' cur' : ''}`} />
            {hot.length <= 14 && <text x={pr.x(p.lon) + 9} y={pr.y(p.lat) + 3} className="dv-maplbl">{p.name}</text>}
          </g>
        ))}
        {hover && !hl.has(hover.id) && <g><circle cx={pr.x(hover.lon)} cy={pr.y(hover.lat)} r={4} className="dv-hover" /><text x={pr.x(hover.lon) + 8} y={pr.y(hover.lat) - 6} className="dv-maplbl">{hover.name}</text></g>}
      </svg>
      <ul className="dv-maplist" aria-label="Places in selection">
        {hot.map((p) => (
          <li key={p.id}><button type="button" className={`dv-pill${p.id === current ? ' on' : ''}`} onClick={() => onPick(p.id)}>{p.name}<span className="dv-pk">{p.kind}</span></button></li>
        ))}
        {hot.length === 0 && <li><Chip chip="NOT SERVED" word="NO PLACE SELECTED" /></li>}
      </ul>
    </div>
  );
}
