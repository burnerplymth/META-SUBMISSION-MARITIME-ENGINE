/* PORT TWIN map palettes: flat Apple / Google Maps register (quiet land,
 * calm water, thin roads, tinted terminals), one per theme, with a mono
 * variant for "colour off". Plain hex so label contrast can be checked.
 * Vessel kinds reuse the map package's glowing-point language: colour by
 * kind, state is a ring or a trail, never a fill. */
import type { CargoKind } from './types';

export type ThemeName = 'dark' | 'light';

export interface Palette {
  theme: ThemeName;
  void: string; sea: string; land: string; coast: string; extent: string;
  building: string; roadMinor: string; roadMajor: string; roadCase: string; rail: string;
  yard: string; storage: string;
  terminalLine: string; terminalFill: Record<CargoKind, string>;
  pier: string; pierLine: string; quay: string; breakwater: string; berth: string; crane: string;
  fairway: string; fairwayShut: string; lock: string; anchorage: string; anchorageFill: string;
  outage: string; strike: string;
  label: string; labelHalo: string; labelDim: string;
  kind: Record<CargoKind, string>;
  hullEdge: string; additive: boolean;
}

const KIND_DARK: Record<CargoKind, string> = {
  container: '#5b9bff', bulk: '#d6c07a', tanker: '#fb923c', 'ro-ro': '#2dd4bf', general: '#b69cff', unknown: '#aab0b8',
};
const KIND_LIGHT: Record<CargoKind, string> = {
  container: '#1d5fe0', bulk: '#8a6d1f', tanker: '#c2410c', 'ro-ro': '#0f766e', general: '#6d3fd6', unknown: '#5f6670',
};
const tint = (c: Record<CargoKind, string>, a: string): Record<CargoKind, string> =>
  Object.fromEntries((Object.keys(c) as CargoKind[]).map((k) => [k, `${c[k]}${a}`])) as Record<CargoKind, string>;

export const DARK: Palette = {
  theme: 'dark',
  void: '#0b0b0b', sea: '#0f2433', land: '#1b1e22', coast: '#35516a', extent: '#2c2c2c',
  building: '#262a30', roadMinor: '#2c3138', roadMajor: '#3d444d', roadCase: '#15171a', rail: '#59626d',
  yard: '#30353c', storage: '#2c3036',
  terminalLine: '#7d8895', terminalFill: tint(KIND_DARK, '2b'),
  pier: '#3a4048', pierLine: '#8a96a3', quay: '#e6edf3', breakwater: '#7d8895', berth: '#9fb4c8', crane: '#f5c542',
  fairway: '#3fb6d9', fairwayShut: '#ff6b6b', lock: '#e8930c', anchorage: '#b69cff', anchorageFill: '#b69cff14',
  outage: '#e8930c', strike: '#ff6b6b',
  label: '#e6e6e6', labelHalo: '#0b0b0bd9', labelDim: '#a3a3a3',
  kind: KIND_DARK, hullEdge: '#0b0b0b', additive: true,
};
export const LIGHT: Palette = {
  theme: 'light',
  void: '#e9e9ec', sea: '#b7d6e6', land: '#f1efe9', coast: '#7fa3b8', extent: '#c9c9ce',
  building: '#e4e0d6', roadMinor: '#ffffff', roadMajor: '#fbe9b0', roadCase: '#d6d2c6', rail: '#9a9aa2',
  yard: '#e0dccf', storage: '#d9d5c8',
  terminalLine: '#7a7f87', terminalFill: tint(KIND_LIGHT, '26'),
  pier: '#e1dccf', pierLine: '#8b8f96', quay: '#2a3138', breakwater: '#6f757d', berth: '#52606d', crane: '#946800',
  fairway: '#0369a1', fairwayShut: '#b23a37', lock: '#a34a06', anchorage: '#6d3fd6', anchorageFill: '#6d3fd614',
  outage: '#a34a06', strike: '#b23a37',
  label: '#1d1d1f', labelHalo: '#ffffffe0', labelDim: '#3d4852',
  kind: KIND_LIGHT, hullEdge: '#ffffff', additive: false,
};

/** Greys for "colour off": the layers keep their shapes and lose their hues. */
function mono(p: Palette): Palette {
  const dark = p.theme === 'dark';
  const g = dark
    ? { fill: '#2a2d31', line: '#8c9399', kind: '#c9ced3', tint: '#9aa0a6' }
    : { fill: '#dcdad4', line: '#6c7279', kind: '#3b4148', tint: '#6c7279' };
  const kinds = Object.fromEntries((Object.keys(p.kind) as CargoKind[]).map((k) => [k, g.kind])) as Record<CargoKind, string>;
  return {
    ...p, sea: dark ? '#0d1013' : '#cfd3d7', land: dark ? '#25292e' : p.land, coast: g.line, terminalFill: tint(kinds, dark ? '22' : '30'), terminalLine: g.line,
    fairway: g.line, anchorage: g.line, anchorageFill: `${g.line}14`, lock: g.line, crane: g.kind, kind: kinds,
    roadMajor: dark ? '#363b41' : '#f7f7f5',
  };
}

export function paletteFor(theme: ThemeName, colour: boolean): Palette {
  const base = theme === 'light' ? LIGHT : DARK;
  return colour ? base : mono(base);
}
/** The document theme as one of the two map palettes (retro and paper skins follow their family). */
export function themeFromAttr(attr: string | null | undefined): ThemeName {
  return attr === 'light' || attr === 'retro-light' ? 'light' : 'dark';
}
