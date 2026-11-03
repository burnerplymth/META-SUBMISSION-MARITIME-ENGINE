import type { Theme } from '../types';

/* Appearance model.
 *
 * The store's `Theme` union stays exactly as it was (dark, light,
 * retro-dark, retro-light): the map package, the basemap pairing and
 * every persisted value keep working. Two things sit on top:
 *   scheme  LIGHT | DARK, the top-right toggle
 *   skin    institutional | paper | retro, in the appearance menu
 * and maps the pair onto a Theme. Paper is a light-only skin: it is a
 * data-skin attribute over theme 'light', and choosing DARK while on
 * Paper returns to institutional dark while REMEMBERING Paper for the
 * next LIGHT (the preference persists separately from the theme).
 * Density is an independent attribute. Pure module: no DOM, no storage
 * of its own; callers pass the storage and the node. */

export type Scheme = 'light' | 'dark';
export type Skin = 'institutional' | 'paper' | 'retro';
export type Density = 'compact' | 'comfortable';

export const SKIN_KEY = 'supermap.skin';
export const DENSITY_KEY = 'supermap.density';
export const FOLLOW_KEY = 'supermap.theme.follow';

export const SKINS: readonly Skin[] = ['institutional', 'paper', 'retro'];
export const DENSITIES: readonly Density[] = ['compact', 'comfortable'];
export const SKIN_LABEL: Record<Skin, string> = { institutional: 'Institutional', paper: 'Paper', retro: 'Retro' };
export const DENSITY_LABEL: Record<Density, string> = { compact: 'Compact', comfortable: 'Comfortable' };

export const schemeOf = (t: Theme): Scheme => (t === 'light' || t === 'retro-light' ? 'light' : 'dark');
export const isRetro = (t: Theme): boolean => t === 'retro-dark' || t === 'retro-light';

/** The (scheme, skin) pair as a store Theme. Paper has no dark form. */
export function themeFor(scheme: Scheme, skin: Skin): Theme {
  if (skin === 'retro') return scheme === 'light' ? 'retro-light' : 'retro-dark';
  return scheme;
}

/** Validated reads: a stored value outside the union is dropped. */
export function parseSkin(raw: unknown): Skin | null {
  return raw === 'institutional' || raw === 'paper' || raw === 'retro' ? raw : null;
}
export function parseDensity(raw: unknown): Density | null {
  return raw === 'compact' || raw === 'comfortable' ? raw : null;
}
export function readSkinPref(storage: { get(k: string): string | null }): Skin {
  return parseSkin(storage.get(SKIN_KEY)) ?? 'institutional';
}
export function readDensity(storage: { get(k: string): string | null }): Density {
  return parseDensity(storage.get(DENSITY_KEY)) ?? 'compact';
}

/** The skin actually in force for a theme and a remembered preference.
 *  A retro theme is always retro; paper only exists over light. */
export function effectiveSkin(theme: Theme, pref: Skin): Skin {
  if (isRetro(theme)) return 'retro';
  if (pref === 'paper' && theme === 'light') return 'paper';
  return 'institutional';
}

/** What the preference becomes when the theme changes by a path other
 *  than the appearance menu (a persisted theme, the #light link, the
 *  map's own basemap toggle): a retro theme means retro; otherwise the
 *  remembered preference stays, except that retro is not kept. */
export function prefAfterTheme(theme: Theme, pref: Skin): Skin {
  if (isRetro(theme)) return 'retro';
  return pref === 'retro' ? 'institutional' : pref;
}

export interface AppearanceNode { dataset: Record<string, string | undefined> }
/** Write the three attributes the stylesheets key on. */
export function applyAppearance(node: AppearanceNode, theme: Theme, pref: Skin, density: Density): void {
  node.dataset.theme = theme;
  node.dataset.skin = effectiveSkin(theme, pref);
  node.dataset.density = density;
}
