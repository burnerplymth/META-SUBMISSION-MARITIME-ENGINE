import React, { useCallback, useId, useRef } from 'react';
import {
  applyKeyEdit, dateLabel, dayLabel, endOf, keyEdit, moveStart, overlapSegments, resizeEnd, resizeStart, spanOf, tickStep, ticks,
  viewDays, type Snap,
} from './timeline';
import type { UiShock } from './types';
import { KIND_LABEL } from './doc';

/* The Gantt-style timeline: a row per shock on a day axis (or a date axis),
 * bars you drag to move and resize at either end, snapping, keyboard control
 * and the overlap and span marks. Presentation over timeline.ts, which owns
 * every number. Edits go out through onEdit with a gesture key, so a whole
 * drag is one undo step. */

const LABEL_W = 156;
const ROW_H = 30;
const AXIS_H = 30;
const BAR_H = 18;
const HANDLE_W = 7;
const PAD_R = 24;

export interface TimelineProps {
  shocks: readonly UiShock[];
  names: Readonly<Record<string, string>>;
  /** Proposed starts of an arrangement waiting for Accept. */
  pending: Readonly<Record<string, number>> | null;
  selectedId: string | null;
  /** Shocks the shock book refuses are drawn dashed. */
  refused: ReadonlySet<string>;
  snap: Snap;
  axis: 'days' | 'dates';
  startDate: string;
  pxPerDay: number;
  onSelect(id: string | null): void;
  onEdit(id: string, patch: { startD?: number; durationD?: number }, key: string): void;
  onGestureEnd(): void;
}

type Drag = { id: string; mode: 'move' | 'end' | 'start'; x0: number; start0: number; dur0: number; key: string; moved: boolean };

export function Timeline(p: TimelineProps) {
  const { shocks, names, pending, selectedId, snap, axis, startDate, pxPerDay: px } = p;
  const uid = useId().replace(/[^A-Za-z0-9_-]/g, '');
  const drag = useRef<Drag | null>(null);
  const seq = useRef(0);
  const days = viewDays(shocks, 30);
  const width = Math.ceil(days * px) + PAD_R;
  const height = AXIS_H + Math.max(1, shocks.length) * ROW_H + 6;
  const step = tickStep(px);
  const overlaps = overlapSegments(shocks);
  const span = spanOf(shocks);
  const rowOf = (id: string) => shocks.findIndex((s) => s.id === id);
  const x = (d: number) => d * px;
  const label = (s: UiShock) => `${names[s.placeId] ?? s.placeId}, ${KIND_LABEL[s.kind]}, ${axis === 'dates' ? `${dateLabel(startDate, s.startD)} to ${dateLabel(startDate, endOf(s))}` : `day ${s.startD} to day ${endOf(s)}`}`;

  const down = (e: React.PointerEvent<SVGElement>, s: UiShock, mode: Drag['mode']) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as SVGElement).setPointerCapture?.(e.pointerId);
    seq.current += 1;
    drag.current = { id: s.id, mode, x0: e.clientX, start0: s.startD, dur0: s.durationD, key: `drag:${s.id}:${seq.current}`, moved: false };
    p.onSelect(s.id);
  };
  const move = (e: React.PointerEvent<SVGElement>) => {
    const d = drag.current;
    if (!d) return;
    const dD = (e.clientX - d.x0) / px;
    if (!d.moved && Math.abs(e.clientX - d.x0) < 2) return;
    d.moved = true;
    if (d.mode === 'move') p.onEdit(d.id, { startD: moveStart(d.start0, dD, snap) }, d.key);
    else if (d.mode === 'end') p.onEdit(d.id, { durationD: resizeEnd(d.start0, d.dur0, dD, snap) }, d.key);
    else p.onEdit(d.id, resizeStart(d.start0, d.dur0, dD, snap), d.key);
  };
  const up = (e: React.PointerEvent<SVGElement>) => {
    if (!drag.current) return;
    (e.currentTarget as SVGElement).releasePointerCapture?.(e.pointerId);
    drag.current = null;
    p.onGestureEnd();
  };
  const onKey = useCallback((e: React.KeyboardEvent<SVGGElement>, s: UiShock) => {
    const k = keyEdit(e, snap);
    if (!k) {
      if (e.key === 'Escape') p.onSelect(null);
      return;
    }
    e.preventDefault();
    p.onEdit(s.id, applyKeyEdit(s, k, snap), `key:${s.id}:${e.key}:${e.altKey ? 'a' : ''}`);
  }, [snap, p]);

  const ticksAt = ticks(days, step);
  const tickText = (d: number) => (axis === 'dates' ? dateLabel(startDate, d) : String(d));

  return (
    <div className="scb-tl" data-testid="scb-timeline">
      <div className="scb-tl-labels" style={{ width: LABEL_W }} aria-hidden="false">
        <div className="scb-tl-corner" style={{ height: AXIS_H }}>{axis === 'dates' ? 'Date' : 'Day'}</div>
        {shocks.length === 0 && <div className="scb-tl-lab scb-tl-lab--empty" style={{ height: ROW_H }}>No shocks</div>}
        {shocks.map((s) => (
          <button key={s.id} type="button" className={`scb-tl-lab${selectedId === s.id ? ' on' : ''}`} style={{ height: ROW_H }}
            onClick={() => p.onSelect(selectedId === s.id ? null : s.id)} aria-pressed={selectedId === s.id}
            title={`${names[s.placeId] ?? s.placeId}, ${KIND_LABEL[s.kind]}`}>
            <span className="scb-tl-n">{names[s.placeId] ?? s.placeId}</span>
            <span className="scb-tl-k">{KIND_LABEL[s.kind]}</span>
          </button>
        ))}
      </div>
      <div className="scb-tl-scroll">
        <svg className="scb-tl-svg" width={width} height={height} role="group" aria-label="Shock timeline" aria-describedby={`${uid}-d`}>
          <desc id={`${uid}-d`}>Arrows move, Alt resizes.</desc>
          <defs>
            <pattern id={`${uid}-h`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="6" height="6" className="scb-hatch-bg" />
              <line x1="0" y1="0" x2="0" y2="6" className="scb-hatch-ln" />
            </pattern>
          </defs>
          {/* axis */}
          <rect x={0} y={0} width={width} height={AXIS_H} className="scb-ax-bg" />
          {ticksAt.map((d) => (
            <g key={d}>
              <line x1={x(d)} x2={x(d)} y1={AXIS_H - 6} y2={height} className="scb-grid" />
              <text x={x(d) + 3} y={AXIS_H - 10} className="scb-ax-t">{tickText(d)}</text>
            </g>
          ))}
          {shocks.map((_, i) => <line key={i} x1={0} x2={width} y1={AXIS_H + (i + 1) * ROW_H} y2={AXIS_H + (i + 1) * ROW_H} className="scb-rowline" />)}
          {/* total span bracket */}
          {shocks.length > 0 && span.totalSpanD > 0 && (
            <g className="scb-span" aria-hidden="true">
              <line x1={x(span.firstStartD)} x2={x(span.lastEndD)} y1={AXIS_H - 3} y2={AXIS_H - 3} />
              <line x1={x(span.firstStartD)} x2={x(span.firstStartD)} y1={AXIS_H - 7} y2={AXIS_H} />
              <line x1={x(span.lastEndD)} x2={x(span.lastEndD)} y1={AXIS_H - 7} y2={AXIS_H} />
            </g>
          )}
          {/* proposed arrangement: outlines at the new starts */}
          {pending && shocks.map((s) => {
            const to = pending[s.id];
            if (to === undefined || to === s.startD) return null;
            const i = rowOf(s.id);
            const y = AXIS_H + i * ROW_H + (ROW_H - BAR_H) / 2;
            return (
              <g key={`g-${s.id}`} className="scb-ghost" aria-hidden="true">
                <line x1={x(s.startD)} x2={x(to)} y1={y + BAR_H / 2} y2={y + BAR_H / 2} />
                <rect x={x(to)} y={y} width={Math.max(3, s.durationD * px)} height={BAR_H} rx={2} />
              </g>
            );
          })}
          {/* bars */}
          {shocks.map((s, i) => {
            const y = AXIS_H + i * ROW_H + (ROW_H - BAR_H) / 2;
            const w = Math.max(3, s.durationD * px);
            const on = selectedId === s.id;
            return (
              <g key={s.id} className={`scb-bar scb-bar--${s.kind}${on ? ' on' : ''}${p.refused.has(s.id) ? ' refused' : ''}`}
                tabIndex={0} role="group" aria-roledescription="Shock bar" aria-label={label(s)} data-shock={s.id}
                onFocus={() => p.onSelect(s.id)} onKeyDown={(e) => onKey(e, s)}>
                <rect x={x(s.startD)} y={y} width={w} height={BAR_H} rx={2} className="scb-bar-body"
                  onPointerDown={(e) => down(e, s, 'move')} onPointerMove={move} onPointerUp={up} onPointerCancel={up} />
                <rect x={x(s.startD)} y={y} width={HANDLE_W} height={BAR_H} className="scb-handle scb-handle--start" data-handle="start"
                  onPointerDown={(e) => down(e, s, 'start')} onPointerMove={move} onPointerUp={up} onPointerCancel={up} />
                <rect x={x(s.startD) + w - HANDLE_W} y={y} width={HANDLE_W} height={BAR_H} className="scb-handle scb-handle--end" data-handle="end"
                  onPointerDown={(e) => down(e, s, 'end')} onPointerMove={move} onPointerUp={up} onPointerCancel={up} />
              </g>
            );
          })}
          {/* overlap highlight on both rows */}
          {overlaps.map((o) => [o.a, o.b].map((id) => {
            const i = rowOf(id);
            if (i < 0) return null;
            const y = AXIS_H + i * ROW_H + (ROW_H - BAR_H) / 2;
            return <rect key={`${o.a}-${o.b}-${id}`} x={x(o.fromD)} y={y} width={Math.max(2, (o.toD - o.fromD) * px)} height={BAR_H} rx={2}
              className={`scb-overlap${o.samePlace ? ' same' : ''}`} fill={`url(#${uid}-h)`} pointerEvents="none" data-overlap={`${o.a}:${o.b}`} />;
          }))}
          {/* duration labels last: the overlap hatch above must not run through the figures */}
          {shocks.map((s, i) => {
            const y = AXIS_H + i * ROW_H + (ROW_H - BAR_H) / 2;
            const w = Math.max(3, s.durationD * px);
            return w > 46 ? <text key={`t-${s.id}`} x={x(s.startD) + 6} y={y + BAR_H / 2 + 4} className="scb-bar-t" pointerEvents="none" aria-hidden="true">{dayLabel(s.durationD)}</text> : null;
          })}
        </svg>
      </div>
    </div>
  );
}
