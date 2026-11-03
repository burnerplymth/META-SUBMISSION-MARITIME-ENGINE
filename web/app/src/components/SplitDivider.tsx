import React, { useEffect, useRef } from 'react';
import { DEFAULT_COLW, widthFromDrag, widthFromKey } from '../layoutGeometry';

/**
 * A draggable grid divider between two workspace columns.
 * Pointer drags write through rAF (one store update per animation
 * frame — MapLibre's container ResizeObserver reflows at that rate,
 * never at raw pointer-event rate); the final pointer-up writes the
 * settled width once more. Keyboard: arrows step, shift widens the
 * step, Home resets, double-click resets. Rendered only in the
 * normal window state (no maximize, map visible) by the shell.
 */
export function SplitDivider({ side, value, onChange, onDragState }: {
  side: 'left' | 'right';
  value: number;
  onChange: (side: 'left' | 'right', w: number) => void;
  onDragState?: (dragging: boolean) => void;
}) {
  const drag = useRef<{ startX: number; startW: number; last: number; raf: number } | null>(null);

  /* A divider unmounted mid-drag (a maximize lands) must not leave
   * the shell in the dragging state or a rAF pending. */
  useEffect(() => () => {
    if (drag.current?.raf) cancelAnimationFrame(drag.current.raf);
    drag.current = null;
  }, []);

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = value;
    drag.current = { startX, startW, last: value, raf: 0 };
    onDragState?.(true);
    const move = (ev: PointerEvent) => {
      const d = drag.current;
      if (!d) return;
      d.last = widthFromDrag(side, startW, startX, ev.clientX);
      if (!d.raf) {
        d.raf = requestAnimationFrame(() => {
          if (drag.current) { drag.current.raf = 0; onChange(side, drag.current.last); }
        });
      }
    };
    const up = (ev: PointerEvent) => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      const d = drag.current;
      drag.current = null;
      if (d?.raf) cancelAnimationFrame(d.raf);
      onChange(side, d ? widthFromDrag(side, startW, startX, ev.clientX) : value);
      onDragState?.(false);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    const next = widthFromKey(side, value, e.key, e.shiftKey);
    if (next === null) return;
    e.preventDefault();
    onChange(side, next);
  };

  return (
    <div
      className={`split-divider split-divider--${side}`}
      style={side === 'left' ? { left: 34 + value } : { right: value }}
      role="separator"
      aria-orientation="vertical"
      aria-label={side === 'left' ? 'Resize left column' : 'Resize right column'}
      aria-valuenow={value}
      aria-valuemin={side === 'left' ? 200 : 300}
      aria-valuemax={side === 'left' ? 480 : 640}
      tabIndex={0}
      title="Resize"
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      onDoubleClick={() => onChange(side, DEFAULT_COLW[side])}
    />
  );
}
