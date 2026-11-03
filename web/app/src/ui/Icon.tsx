import React from 'react';
import { ICON_DATA, type IconName as LucideName } from './iconData';

/**
 * The SupermAP icon set. A Lucide subset (ISC licence,
 * vendored in ./vendor/lucide, licence text in THIRD-PARTY.md) turned into
 * data by scripts/build-icons.mjs. No icon font, no runtime package, no
 * sprite to mount: <Icon> draws the vendored paths as inline SVG, in
 * currentColor, with a constant 1.5 px stroke at every size
 * (vector-effect: non-scaling-stroke, see ui.css).
 *
 *   <Icon name="close" />                          decorative (aria-hidden)
 *   <Icon name="sun" size="md" label="Light" />    labelled (role="img")
 *   <Icon name="chevron-down" size={12} />
 *
 * Never write a unicode glyph as an icon.
 */

/** Semantic names the app uses, mapped onto the vendored Lucide name. */
export const ICON_ALIAS = {
  close: 'x', more: 'ellipsis', maximize: 'maximize-2', restore: 'minimize-2', ai: 'sparkles', appearance: 'sliders-horizontal',
  home: 'house', enter: 'corner-down-left', backspace: 'delete', back: 'chevron-left', forward: 'chevron-right',
  open: 'external-link', float: 'layers', dock: 'panel-left', add: 'plus', remove: 'minus', filter: 'funnel', sort: 'chevrons-up-down',
  refresh: 'refresh-cw', reset: 'rotate-ccw', light: 'sun', dark: 'moon', select: 'mouse-pointer-2', measure: 'ruler', drag: 'grip-vertical',
  live: 'radio', snapshot: 'history', simulation: 'flask-conical', ledger: 'scale', proof: 'shield-check', news: 'newspaper',
  chain: 'network', route: 'waypoints', place: 'map', fork: 'git-fork', warn: 'triangle-alert', error: 'circle-x', ok: 'circle-check',
  unavailable: 'ban', offline: 'wifi-off', spend: 'coins', alert: 'bell', play: 'play', pause: 'pause',
} as const satisfies Record<string, LucideName>;
export type IconAlias = keyof typeof ICON_ALIAS;
export type IconName = LucideName | IconAlias;

/** Size tokens (px). `--icon-xs/sm/md/lg` in tokens.css carry the same numbers. */
export const ICON_SIZE = { xs: 12, sm: 14, md: 16, lg: 20 } as const;
export type IconSize = keyof typeof ICON_SIZE | number;

export const resolveIcon = (name: IconName): LucideName =>
  (name in ICON_ALIAS ? ICON_ALIAS[name as IconAlias] : name) as LucideName;

export interface IconProps {
  name: IconName;
  /** xs 12, sm 14 (default), md 16, lg 20, or a pixel number. */
  size?: IconSize;
  /** Accessible name. Without it the icon is decorative and aria-hidden. */
  label?: string;
  className?: string;
  /** Stroke in px; default is the --icon-stroke token (1.5), constant at every size. */
  stroke?: number;
}

export function Icon({ name, size = 'sm', label, className = '', stroke }: IconProps) {
  const els = ICON_DATA[resolveIcon(name)];
  const px = typeof size === 'number' ? size : ICON_SIZE[size];
  return (
    <svg
      className={`ui-icon${className ? ` ${className}` : ''}`}
      width={px} height={px} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.5} style={stroke === undefined ? undefined : { strokeWidth: stroke }} strokeLinecap="round" strokeLinejoin="round" data-icon={resolveIcon(name)}
      {...(label ? { role: 'img', 'aria-label': label } : { 'aria-hidden': true, focusable: false })}
    >
      {els.map(([tag, attrs], i) => React.createElement(tag, { key: i, ...attrs }))}
    </svg>
  );
}
