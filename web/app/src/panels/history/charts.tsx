import React from 'react';
import {
  dateAtFrac, daysBetween, eventStartDay, extentOf, lastAtOrBefore, linePaths, xAt, yAt,
  type Box, type DateRange, type DayPoint, type OverlayModel,
} from './logic';
import type { HistEvent } from './types';

export const CHART_BOX: Box = { w: 400, h: 88, l: 6, r: 52, t: 12, b: 12 };
/** The overlay keeps a caption band above the plot for the series names and the event. */
export const OVERLAY_BOX: Box = { w: 400, h: 112, l: 6, r: 52, t: 28, b: 12 };
const fmtV = (v: number): string => (Math.abs(v) >= 100 || Number.isInteger(v) ? String(Math.round(v * 10) / 10) : v.toFixed(1));

/** Map a pointer x inside the svg (viewBox units) to the window's day. */
export function seekFromPointer(e: React.MouseEvent<SVGSVGElement>, win: DateRange, box: Box): string {
  const r = e.currentTarget.getBoundingClientRect();
  const vx = ((e.clientX - r.left) / Math.max(1, r.width)) * box.w;
  const frac = (vx - box.l) / Math.max(1, box.w - box.l - box.r);
  return dateAtFrac(frac, win);
}

/** One place's observed series over the window with event markers and the
 *  backplay cursor. Days up to the cursor are solid in the SNAPSHOT
 *  colour; later days are faded (the as-of reading). A day the source did
 *  not serve is a gap, never a zero. */
export function PlaceChart({ place, points, win, cursor, events, focusId, onSeek, box = CHART_BOX }: {
  place: string; points: DayPoint[]; win: DateRange; cursor: string; events: readonly HistEvent[]; focusId: string | null;
  onSeek: (d: string) => void; box?: Box;
}) {
  const ext = extentOf(points);
  const n = points.length;
  const ci = Math.max(0, Math.min(n - 1, daysBetween(win.from, cursor)));
  const done = linePaths(points, ext, box, 0, ci);
  const rest = linePaths(points, ext, box, ci, n - 1);
  const last = lastAtOrBefore(points, ci);
  const cx = xAt(ci, n, box);
  const y0 = ext ? yAt(ext.min, ext, box) : box.h - box.b;
  const y1 = ext ? yAt(ext.max, ext, box) : box.t;
  return (
    <svg className="hx-svg" viewBox={`0 0 ${box.w} ${box.h}`} role="img" aria-label={`${place} observed series`}
      onClick={(e) => onSeek(seekFromPointer(e, win, box))}>
      <line className="grid" x1={box.l} x2={box.w - box.r} y1={y1} y2={y1} />
      <line className="grid" x1={box.l} x2={box.w - box.r} y1={y0} y2={y0} />
      {ext && <text x={box.w - box.r + 4} y={y1 + 3}>{fmtV(ext.max)}</text>}
      {ext && <text x={box.w - box.r + 4} y={y0 + 3}>{fmtV(ext.min)}</text>}
      {events.filter((e) => eventStartDay(e) >= win.from && eventStartDay(e) <= win.to).map((e) => {
        const x = xAt(daysBetween(win.from, eventStartDay(e)), n, box);
        const focus = e.id === focusId;
        return (
          <g key={e.id}>
            <line className={`evt${focus ? ' evt--focus' : ''}`} x1={x} x2={x} y1={box.t - 4} y2={box.h - box.b} />
            {(focus || focusId === null) && <text className={focus ? 'cap' : ''} x={Math.min(x + 3, box.w - box.r - 60)} y={box.t - 3}>{`${e.kind.replace(/_/g, ' ')}`.slice(0, 22)}</text>}
          </g>
        );
      })}
      {rest.map((d, i) => <path key={`r${i}`} className="obs obs--after" d={d} />)}
      {done.map((d, i) => <path key={`d${i}`} className="obs" d={d} />)}
      <line className="cur" x1={cx} x2={cx} y1={box.t - 4} y2={box.h - box.b} />
      {last && ext && (
        <g>
          <rect className="tag" x={box.w - box.r + 2} y={yAt(last.v, ext, box) - 7} width={box.r - 4} height={14} rx={2} />
          <text className="tag-t" x={box.w - box.r + 5} y={yAt(last.v, ext, box) + 3}>{fmtV(last.v)}</text>
        </g>
      )}
      <text x={box.l} y={box.h - 1}>{win.from.slice(5)}</text>
      <text x={box.w - box.r - 26} y={box.h - 1}>{win.to.slice(5)}</text>
    </svg>
  );
}

/** Observed vs simulated for one place, each on its OWN scale (the
 *  simulator counts berth calls, PortWatch counts transits: different
 *  units, so no shared axis). Baseline and evaluation spans are shaded;
 *  the event start is a vertical rule. Series are named at their ends. */
export function OverlayChart({ model, box = OVERLAY_BOX }: { model: OverlayModel; box?: Box }) {
  const n = model.days.length;
  const tag = (ext: typeof model.obsExt, pts: DayPoint[], cls: string) => {
    if (!ext) return null;
    const l = lastAtOrBefore(pts, pts.length - 1);
    if (!l) return null;
    return (
      <g>
        <rect className={cls} x={box.w - box.r + 2} y={yAt(l.v, ext, box) - 7} width={box.r - 4} height={14} rx={2} />
        <text className="tag-t" x={box.w - box.r + 5} y={yAt(l.v, ext, box) + 3}>{fmtV(l.v)}</text>
      </g>
    );
  };
  const bh = box.h - box.t - box.b;
  return (
    <svg className="hx-svg" viewBox={`0 0 ${box.w} ${box.h}`} role="img" aria-label={`${model.place} observed and simulated series`} style={{ cursor: 'default' }}>
      {model.band.baseline && <rect className="band" x={model.band.baseline[0]} y={box.t} width={Math.max(1, model.band.baseline[1] - model.band.baseline[0])} height={bh} />}
      {model.band.evaluation && <rect className="band band--eval" x={model.band.evaluation[0]} y={box.t} width={Math.max(1, model.band.evaluation[1] - model.band.evaluation[0])} height={bh} />}
      <line className="grid" x1={box.l} x2={box.w - box.r} y1={box.t} y2={box.t} />
      <line className="grid" x1={box.l} x2={box.w - box.r} y1={box.h - box.b} y2={box.h - box.b} />
      {model.eventX !== null && <line className="evt evt--focus" x1={model.eventX} x2={model.eventX} y1={box.t - 4} y2={box.h - box.b} />}
      {model.eventX !== null && <text className="cap" x={Math.min(model.eventX + 3, box.w - box.r - 40)} y={box.t - 3}>Event</text>}
      {model.obsPaths.map((d, i) => <path key={`o${i}`} className="obs" d={d} />)}
      {model.simPaths.map((d, i) => <path key={`s${i}`} className="sim" d={d} />)}
      {tag(model.obsExt, model.observed, 'tag')}
      {tag(model.simExt, model.simulated, 'tag tag--sim')}
      {model.obsExt && <text className="lbl-obs" x={box.l + 2} y={9}>Observed 0–{fmtV(model.obsExt.max)}</text>}
      {model.simExt && <text className="lbl-sim" x={box.l + 2} y={20}>Simulated 0–{fmtV(model.simExt.max)}</text>}
      <text x={box.l} y={box.h - 1}>{model.days[0]?.slice(5)}</text>
      <text x={box.w - box.r - 26} y={box.h - 1}>{model.days[n - 1]?.slice(5)}</text>
    </svg>
  );
}
