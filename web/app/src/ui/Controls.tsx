import React from 'react';
import { Icon, type IconName } from './Icon';
import { ariaKeys, hint, keyCaps } from './hint';

/* The control set: one button
 * family, grouped icon clusters, a segmented tool selector, a switch, key
 * caps, a toolbar. Heights come from --ctl-h-sm/--ctl-h/--ctl-h-lg
 * (22/24/26 px compact), the focus ring is the pinned app-wide
 * :focus-visible line (never restyled here), and the hit area grows to
 * 44 px on touch (ui.css). Classes are styled in ui/ui.css. Rail
 * identity: flat outline buttons with an inset bottom line on press,
 * a rail-and-diamond switch, segmented cells and tabs over a rail. */

export type ControlSize = 'sm' | 'md' | 'lg';

/** Arrow-key travel along a row of options (segmented control, toggle group, tabs): Left and Up go back, Right and
 *  Down go forward, both wrap, Home and End jump to the ends; a disabled option is skipped. Returns the index to
 *  move to, or null for any other key or when nothing is enabled. Pure: no DOM needed. */
export function arrowTarget(key: string, from: number, enabled: readonly boolean[]): number | null {
  const ok = enabled.map((e, i) => (e ? i : -1)).filter((i) => i >= 0);
  if (!ok.length) return null;
  if (key === 'Home') return ok[0];
  if (key === 'End') return ok[ok.length - 1];
  const dir = key === 'ArrowRight' || key === 'ArrowDown' ? 1 : key === 'ArrowLeft' || key === 'ArrowUp' ? -1 : 0;
  if (!dir) return null;
  const at = ok.indexOf(from);
  if (at < 0) return dir === 1 ? ok.find((i) => i > from) ?? ok[0] : [...ok].reverse().find((i) => i < from) ?? ok[ok.length - 1];
  return ok[(at + dir + ok.length) % ok.length];
}

/** Roving keyboard model for a horizontal tablist whose tabs are plain buttons: Left / Right / Home / End select and focus
 *  the neighbour (Up / Down are left alone), the selected tab alone is in the Tab order. Use `props(i)` on each tab. */
export function useTabKeys(count: number, active: number, onSelect: (i: number) => void) {
  const refs = React.useRef<Array<HTMLElement | null>>([]);
  const props = (i: number) => ({
    ref: (el: HTMLElement | null) => { refs.current[i] = el; },
    tabIndex: i === (active < 0 ? 0 : active) ? 0 : -1,
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key === 'ArrowUp' || e.key === 'ArrowDown') return;
      const enabled = Array.from({ length: count }, (_, k) => !(refs.current[k] as HTMLButtonElement | null)?.disabled);
      const to = arrowTarget(e.key, i, enabled);
      if (to === null) return;
      e.preventDefault();
      onSelect(to);
      refs.current[to]?.focus();
    },
  });
  return props;
}

/** Roving focus for a row of buttons: the arrow keys move focus (they do not press), Tab leaves the row. */
function useRowArrows(count: number) {
  const refs = React.useRef<Array<HTMLButtonElement | null>>([]);
  const onKeyDown = (e: React.KeyboardEvent, from: number) => {
    const enabled = Array.from({ length: count }, (_, i) => !refs.current[i]?.disabled);
    const to = arrowTarget(e.key, from, enabled);
    if (to === null) return;
    e.preventDefault();
    refs.current[to]?.focus();
  };
  return { refs, onKeyDown };
}
const SIZE_CLASS: Record<ControlSize, string> = { sm: ' ui-ctl--sm', md: '', lg: ' ui-ctl--lg' };

/** Visible key caps: <Kbd keys="Alt+Shift+F" /> draws Alt, Shift and F as caps. */
export function Kbd({ keys, className = '' }: { keys: string; className?: string }) {
  return (
    <span className={`ui-kbd${className ? ` ${className}` : ''}`} aria-hidden="true">
      {keyCaps(keys).map((k, i) => <kbd key={`${k}${i}`}>{k}</kbd>)}
    </span>
  );
}

type ButtonNative = Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'children'>;

export interface ButtonProps extends ButtonNative {
  /** primary: the one accent-filled action; secondary: bordered; ghost: bare; danger: red text. */
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: ControlSize;
  icon?: IconName;
  /** Shortcut shown as key caps after the label, and set as aria-keyshortcuts. */
  keys?: string;
  children?: React.ReactNode;
}
export function Button({ variant = 'secondary', size = 'md', icon, keys, children, className = '', type = 'button', ...rest }: ButtonProps) {
  return (
    <button type={type} className={`ui-btn ui-btn--${variant}${SIZE_CLASS[size]}${className ? ` ${className}` : ''}`}
      aria-keyshortcuts={keys ? ariaKeys(keys) : undefined} {...rest}>
      {icon && <Icon name={icon} size={size === 'lg' ? 'md' : 'sm'} />}
      {children != null && <span className="ui-btn-l">{children}</span>}
      {keys && <Kbd keys={keys} />}
    </button>
  );
}

export interface IconButtonProps extends ButtonNative {
  icon: IconName;
  /** Control name. Becomes aria-label and, with `keys`, the hint (`Float  Alt+Shift+F`). */
  label: string;
  keys?: string;
  size?: ControlSize;
  variant?: 'ghost' | 'secondary';
  /** Toggle state (aria-pressed). Omit for a plain command button. */
  pressed?: boolean;
  iconSize?: 'xs' | 'sm' | 'md' | 'lg';
}
export function IconButton({ icon, label, keys, size = 'md', variant = 'ghost', pressed, iconSize, className = '', type = 'button', ...rest }: IconButtonProps) {
  return (
    <button type={type} className={`ui-btn ui-btn--icon ui-btn--${variant}${SIZE_CLASS[size]}${pressed ? ' on' : ''}${className ? ` ${className}` : ''}`}
      aria-label={label} title={hint(label, keys)} aria-pressed={pressed} aria-keyshortcuts={keys ? ariaKeys(keys) : undefined} {...rest}>
      <Icon name={icon} size={iconSize ?? (size === 'lg' ? 'md' : 'sm')} />
    </button>
  );
}

/** Grouped cluster: adjacent buttons share one edge (icon clusters, transport). */
export function ButtonGroup({ label, children, className = '' }: { label: string; children: React.ReactNode; className?: string }) {
  return <span className={`ui-btn-group${className ? ` ${className}` : ''}`} role="group" aria-label={label}>{children}</span>;
}

export interface SegmentOption<T extends string> {
  value: T;
  label?: string;
  icon?: IconName;
  keys?: string;
  /** Accessible name when the option is icon-only. */
  name?: string;
  /** Hover text, when it should say more than the label. */
  title?: string;
  /** Rich content in place of the plain label (a state mark, a count). `label` stays the accessible name. */
  content?: React.ReactNode;
  className?: string;
}
/** The segmented control and the tool selector: a group of aria-pressed
 *  buttons on one rail. Icon-only options need `name`. Arrow keys move focus
 *  along the rail (Home and End jump); Enter or Space presses. */
export function SegmentedControl<T extends string>({ value, onChange, options, label, size = 'md', className = '' }:
  { value: T; onChange: (v: T) => void; options: readonly SegmentOption<T>[]; label: string; size?: ControlSize; className?: string }) {
  const { refs, onKeyDown } = useRowArrows(options.length);
  return (
    <span className={`seg ui-seg${SIZE_CLASS[size]}${className ? ` ${className}` : ''}`} role="group" aria-label={label}>
      {options.map((o, i) => {
        const on = o.value === value;
        const accessible = o.name ?? o.label ?? o.value;
        return (
          <button key={o.value} ref={(el) => { refs.current[i] = el; }} type="button" className={`seg-b${on ? ' on' : ''}${o.className ? ` ${o.className}` : ''}`} aria-pressed={on}
            aria-label={o.label && !o.content ? undefined : accessible} title={o.title ?? (o.label && !o.keys && !o.content ? undefined : hint(accessible, o.keys))} aria-keyshortcuts={o.keys ? ariaKeys(o.keys) : undefined}
            onKeyDown={(e) => onKeyDown(e, i)} onClick={() => { if (!on) onChange(o.value); }}>
            {o.icon && <Icon name={o.icon} size="sm" />}
            {o.content ?? (o.label && <span>{o.label}</span>)}
          </button>
        );
      })}
    </span>
  );
}

/** The multi-select sibling of the segmented control: the same flat text over a rail, but any number of options can
 *  be on (filters such as Sea, Air, Ground). Each option is an aria-pressed button; `onToggle` gets the clicked value. */
export function ToggleGroup<T extends string>({ values, onToggle, options, label, size = 'md', className = '' }:
  { values: readonly T[]; onToggle: (v: T) => void; options: readonly SegmentOption<T>[]; label: string; size?: ControlSize; className?: string }) {
  const { refs, onKeyDown } = useRowArrows(options.length);
  return (
    <span className={`seg ui-seg ui-seg--multi${SIZE_CLASS[size]}${className ? ` ${className}` : ''}`} role="group" aria-label={label}>
      {options.map((o, i) => {
        const on = values.includes(o.value);
        const accessible = o.name ?? o.label ?? o.value;
        return (
          <button key={o.value} ref={(el) => { refs.current[i] = el; }} type="button" className={`seg-b${on ? ' on' : ''}${o.className ? ` ${o.className}` : ''}`} aria-pressed={on}
            aria-label={o.label && !o.content ? undefined : accessible} title={o.title ?? (o.label && !o.keys && !o.content ? undefined : hint(accessible, o.keys))} aria-keyshortcuts={o.keys ? ariaKeys(o.keys) : undefined}
            onKeyDown={(e) => onKeyDown(e, i)} onClick={() => onToggle(o.value)}>
            {o.icon && <Icon name={o.icon} size="sm" />}
            {o.content ?? (o.label && <span>{o.label}</span>)}
          </button>
        );
      })}
    </span>
  );
}

/** A switch (role="switch"): a short rail with ticks and a diamond marker
 *  (never a pill), the state in words beside it. `words` renames the two
 *  ends (`['Light', 'Dark']` for the theme: the marker sits at the right
 *  end when checked). The words are decorative; aria-checked carries state. */
export function Toggle({ checked, onChange, label, className = '', disabled, words = ['Off', 'On'], labelHidden }:
  { checked: boolean; onChange: (v: boolean) => void; label: string; className?: string; disabled?: boolean; words?: readonly [string, string]; labelHidden?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={checked} disabled={disabled} className={`ui-toggle${className ? ` ${className}` : ''}`}
      onClick={() => onChange(!checked)}>
      <span className={`ui-toggle-l${labelHidden ? ' ui-vh' : ''}`}>{label}</span>
      <span className="ui-sw" aria-hidden="true">
        <span className="ui-sw-w ui-sw-w--off">{words[0]}</span>
        <span className="ui-sw-trk"><i className="ui-sw-thm" /></span>
        <span className="ui-sw-w ui-sw-w--on">{words[1]}</span>
      </span>
    </button>
  );
}

export interface TabOption<T extends string> {
  value: T;
  label: string;
  /** Plain count or note after the label. */
  note?: string | number;
  disabled?: boolean;
  icon?: IconName;
  /** Hover text. */
  title?: string;
  /** Element ids for aria-controls / the tab itself, when a tabpanel needs them. */
  id?: string;
  controls?: string;
}
/** Tabs: flat text over a bottom rail, the selected tab carries the marker
 *  line with end ticks. WAI-ARIA tablist with roving tabindex; Left / Right /
 *  Home / End move and select. */
export function Tabs<T extends string>({ value, onChange, options, label, className = '' }:
  { value: T; onChange: (v: T) => void; options: readonly TabOption<T>[]; label: string; className?: string }) {
  const refs = React.useRef<Array<HTMLButtonElement | null>>([]);
  const onKey = (e: React.KeyboardEvent, i: number) => {
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') return; // a horizontal tablist: Left / Right / Home / End only
    const to = arrowTarget(e.key, i, options.map((o) => !o.disabled));
    if (to === null) return;
    e.preventDefault();
    onChange(options[to].value);
    refs.current[to]?.focus();
  };
  return (
    <div className={`ui-tabs${className ? ` ${className}` : ''}`} role="tablist" aria-label={label}>
      {options.map((o, i) => {
        const on = o.value === value;
        return (
          <button key={o.value} ref={(el) => { refs.current[i] = el; }} type="button" role="tab" id={o.id} aria-selected={on} aria-controls={o.controls}
            tabIndex={on ? 0 : -1} disabled={o.disabled} title={o.title} className={`ui-tab${on ? ' on' : ''}`} onClick={() => { if (!on) onChange(o.value); }} onKeyDown={(e) => onKey(e, i)}>
            {o.icon && <Icon name={o.icon} size="sm" />}
            <span>{o.label}</span>
            {o.note !== undefined && <span className="ui-tab-n">{o.note}</span>}
          </button>
        );
      })}
    </div>
  );
}

/** A range bar: a rail with ticks, whisker caps at low and high and a
 *  diamond at the median. Positions are fractions of the axis (0..1); the
 *  caller scales. Modelled or estimated ranges are always labelled by the
 *  caller (a Status chip beside it); the bar itself draws no claim. */
export function RangeBar({ low, high, median, tone = 'range', label, className = '' }:
  { low: number; high: number; median?: number; tone?: 'range' | 'model' | 'ref'; label: string; className?: string }) {
  const f = (v: number) => `${Math.max(0, Math.min(1, v)) * 100}%`;
  const a = Math.min(low, high); const b = Math.max(low, high);
  return (
    <span className={`ui-range${tone === 'range' ? '' : ` ui-range--${tone}`}${className ? ` ${className}` : ''}`} role="img" aria-label={label}>
      <i className="ui-range-rail" /><i className="ui-range-ticks" />
      <i className="ui-range-band" style={{ left: f(a), width: `${Math.max(0, Math.min(1, b) - Math.max(0, a)) * 100}%` }} />
      <i className="ui-range-cap" style={{ left: f(a) }} /><i className="ui-range-cap" style={{ left: f(b) }} />
      {median !== undefined && <i className="ui-range-med" style={{ left: f(median) }} />}
    </span>
  );
}

/** A toolbar: a flat graphite bar with a hairline edge.
 *  Children are IconButton / ButtonGroup / SegmentedControl / ToolbarSeparator. */
export function Toolbar({ label, edge = 'top', children, className = '' }:
  { label: string; edge?: 'top' | 'bottom'; children: React.ReactNode; className?: string }) {
  return <div className={`ui-toolbar ui-toolbar--${edge}${className ? ` ${className}` : ''}`} role="toolbar" aria-label={label}>{children}</div>;
}
export const ToolbarSeparator = () => <span className="ui-toolbar-sep" role="separator" aria-orientation="vertical" />;
export const ToolbarSpacer = () => <span className="ui-toolbar-spacer" />;
