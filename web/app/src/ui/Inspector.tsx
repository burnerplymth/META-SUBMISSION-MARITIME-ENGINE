import React, { useCallback, useId, useRef, useState } from 'react';
import { Icon } from './Icon';

/* Row and disclosure primitives for detail windows: property rows
 * with the label left and the value right, disclosure groups with a chevron, scrubbable numeric
 * fields and timecode-style readouts. Sentence-case labels; numbers are
 * tabular with slashed zero. Styled in ui/ui.css with the --insp-* tokens. */

/** A property list: wraps InspectorRow children in a <dl>. */
export function InspectorList({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <dl className={`ui-ilist${className ? ` ${className}` : ''}`}>{children}</dl>;
}

/** One property: `label` left, value right (children), optional unit after the value. */
export function InspectorRow({ label, children, unit, title, className = '' }:
  { label: string; children?: React.ReactNode; unit?: string; title?: string; className?: string }) {
  return (
    <div className={`ui-irow${className ? ` ${className}` : ''}`} title={title}>
      <dt className="ui-irow-l">{label}</dt>
      <dd className="ui-irow-v"><span className="ui-irow-n">{children ?? '–'}</span>{unit && <small className="ui-irow-u">{unit}</small>}</dd>
    </div>
  );
}

export interface SectionHeaderProps {
  title: string;
  open: boolean;
  onToggle: () => void;
  /** Id of the region the header controls (aria-controls). */
  controls?: string;
  /** Plain count or note at the right of the title (`4`, `3 of 9`). */
  note?: string;
  /** Right-aligned actions (IconButtons). They sit outside the toggle button. */
  actions?: React.ReactNode;
  className?: string;
}
/** Disclosure header: chevron, title, optional note and actions. */
export function SectionHeader({ title, open, onToggle, controls, note, actions, className = '' }: SectionHeaderProps) {
  return (
    <div className={`ui-sec-h${className ? ` ${className}` : ''}`} data-open={open}>
      <button type="button" className="ui-sec-btn" aria-expanded={open} aria-controls={controls} onClick={onToggle}>
        <Icon name="chevron-right" size="xs" className="ui-sec-chev" />
        <span className="ui-sec-t">{title}</span>
        {note && <span className="ui-sec-n">{note}</span>}
      </button>
      {actions && <span className="ui-sec-a">{actions}</span>}
    </div>
  );
}

/** A disclosure group: SectionHeader plus a body that mounts only while open. */
export function InspectorSection({ title, defaultOpen = true, note, actions, children, className = '' }:
  { title: string; defaultOpen?: boolean; note?: string; actions?: React.ReactNode; children: React.ReactNode; className?: string }) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <section className={`ui-sec${className ? ` ${className}` : ''}`}>
      <SectionHeader title={title} open={open} onToggle={() => setOpen((o) => !o)} controls={id} note={note} actions={actions} />
      {open && <div id={id} className="ui-sec-body">{children}</div>}
    </section>
  );
}

/** Pure scrub maths: the value after dragging `dx` pixels (or pressing an
 *  arrow key), stepped, clamped and rounded to the step's precision. */
export function scrubValue(start: number, dx: number, opts: { step?: number; min?: number; max?: number; fine?: boolean; coarse?: boolean } = {}): number {
  const step = opts.step ?? 1;
  const mult = opts.coarse ? 10 : opts.fine ? 0.1 : 1;
  const decimals = Math.max(0, (String(step).split('.')[1] ?? '').length + (opts.fine ? 1 : 0));
  let v = start + dx * step * mult;
  if (opts.min !== undefined) v = Math.max(opts.min, v);
  if (opts.max !== undefined) v = Math.min(opts.max, v);
  return Number(v.toFixed(decimals));
}

export interface ScrubFieldProps {
  label: string;
  value: number;
  onChange: (v: number) => void;
  step?: number; min?: number; max?: number;
  unit?: string;
  /** Fractional digits shown. Default follows the step. */
  digits?: number;
  disabled?: boolean;
  /** A stable hook name (data-field) for the tour. */
  field?: string;
}
/** A numeric field you can drag: press and drag the label sideways to scrub
 *  (Shift = coarse x10, Alt = fine x0.1), or focus the field and use the arrow
 *  keys (same modifiers), or type. Escape while dragging restores the value; Escape in the field drops an uncommitted edit. */
export function ScrubField({ label, value, onChange, step = 1, min, max, unit, digits, disabled, field }: ScrubFieldProps) {
  const id = useId();
  const drag = useRef<{ x: number; v: number } | null>(null);
  const dragEsc = useRef<((k: KeyboardEvent) => void) | null>(null);
  const stopEsc = () => { if (dragEsc.current) window.removeEventListener('keydown', dragEsc.current, true); dragEsc.current = null; };
  const shown = (v: number) => v.toFixed(digits ?? (String(step).split('.')[1] ?? '').length);
  const [draft, setDraft] = useState<string | null>(null);
  const commit = useCallback((s: string) => {
    const n = Number(s);
    if (s.trim() !== '' && Number.isFinite(n)) onChange(scrubValue(n, 0, { step, min, max }));
    setDraft(null);
  }, [onChange, step, min, max]);
  return (
    <div className="ui-scrub" data-disabled={disabled || undefined} data-field={field}>
      <label className="ui-scrub-l" htmlFor={id}
        onPointerDown={(e) => {
          if (disabled) return;
          drag.current = { x: e.clientX, v: value };
          const esc = (k: KeyboardEvent) => {
            if (k.key !== 'Escape' || !drag.current) return;
            onChange(drag.current.v); drag.current = null;
            window.removeEventListener('keydown', esc, true);
          };
          window.addEventListener('keydown', esc, true);
          dragEsc.current = esc;
          (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
        }}
        onPointerMove={(e) => {
          const d = drag.current; if (!d) return;
          onChange(scrubValue(d.v, Math.round((e.clientX - d.x) / 4), { step, min, max, fine: e.altKey, coarse: e.shiftKey }));
        }}
        onPointerUp={() => { drag.current = null; stopEsc(); }}
        onPointerCancel={() => { if (drag.current) onChange(drag.current.v); drag.current = null; stopEsc(); }}>{label}</label>
      <input id={id} className="ui-scrub-i" type="text" inputMode="decimal" disabled={disabled} value={draft ?? shown(value)}
        onChange={(e) => setDraft(e.target.value)} onBlur={(e) => { if (draft !== null) commit(e.target.value); }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { commit((e.target as HTMLInputElement).value); return; }
          if (e.key === 'Escape') { setDraft(null); return; }
          if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            e.preventDefault();
            onChange(scrubValue(value, e.key === 'ArrowUp' ? 1 : -1, { step, min, max, fine: e.altKey, coarse: e.shiftKey }));
          }
        }} />
      {unit && <span className="ui-scrub-u">{unit}</span>}
    </div>
  );
}

/** `D+03 14:00` for a count of hours (day, hour, optional minute). Pure. */
export function formatTimecode(totalHours: number, opts: { minutes?: boolean } = {}): string {
  const neg = totalHours < 0;
  const mins = Math.round(Math.abs(totalHours) * 60);
  const d = Math.floor(mins / 1440); const h = Math.floor((mins % 1440) / 60); const m = mins % 60;
  const p = (n: number) => String(n).padStart(2, '0');
  return `D${neg ? '−' : '+'}${p(d)} ${p(h)}${opts.minutes ? `:${p(m)}` : ':00'}`;
}

/** Timecode-style readout: tabular, slashed zero, no jitter as digits change. */
export function Timecode({ hours, minutes, className = '' }: { hours: number; minutes?: boolean; className?: string }) {
  return <span className={`ui-timecode${className ? ` ${className}` : ''}`}>{formatTimecode(hours, { minutes })}</span>;
}

/** Preferred names: a detail window is composed from rows. */
export const DetailList = InspectorList;
export const DetailRow = InspectorRow;
