import { useCallback, useEffect, useRef, useState } from 'react';
import { safeStorage } from '../store';
import type { Theme } from '../types';
import {
  DENSITY_KEY, FOLLOW_KEY, SKIN_KEY, applyAppearance, prefAfterTheme, readDensity, readSkinPref, schemeOf, themeFor,
  type Density, type Scheme, type Skin,
} from './appearance';

/** Appearance state for the top bar: scheme (LIGHT | DARK), skin and
 *  density, persisted through safeStorage (which never throws) and
 *  reflected in html[data-theme], html[data-skin], html[data-density].
 *  The store's Theme stays the single source of the colour family; this
 *  hook only maps the (scheme, skin) pair onto it. FOLLOW SYSTEM is
 *  opt-in: it is OFF until the user turns it on, so a fresh visit is
 *  always the institutional dark default. */
export function useAppearance(theme: Theme, setTheme: (t: Theme) => void) {
  const [pref, setPref] = useState<Skin>(() => prefAfterTheme(theme, readSkinPref(safeStorage)));
  const [density, setDensityState] = useState<Density>(() => readDensity(safeStorage));
  const [follow, setFollowState] = useState<boolean>(() => safeStorage.get(FOLLOW_KEY) === '1');
  const themeRef = useRef(theme); themeRef.current = theme;
  const prefRef = useRef(pref); prefRef.current = pref;

  // The theme can change by another path (#light, the map's basemap toggle).
  useEffect(() => { setPref((p) => prefAfterTheme(theme, p)); }, [theme]);
  useEffect(() => {
    if (typeof document !== 'undefined') applyAppearance(document.documentElement, theme, pref, density);
  }, [theme, pref, density]);
  useEffect(() => { safeStorage.set(SKIN_KEY, pref); }, [pref]);
  useEffect(() => { safeStorage.set(DENSITY_KEY, density); }, [density]);
  useEffect(() => { safeStorage.set(FOLLOW_KEY, follow ? '1' : '0'); }, [follow]);

  const setScheme = useCallback((scheme: Scheme) => {
    setFollowState(false); // an explicit choice ends system-following
    setTheme(themeFor(scheme, prefRef.current));
  }, [setTheme]);
  const setSkin = useCallback((skin: Skin) => {
    setPref(skin);
    setTheme(themeFor(schemeOf(themeRef.current), skin));
  }, [setTheme]);
  const setDensity = useCallback((d: Density) => setDensityState(d), []);
  const setFollow = useCallback((on: boolean) => setFollowState(on), []);

  // FOLLOW SYSTEM: track prefers-color-scheme while it is on.
  useEffect(() => {
    if (!follow || typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const mq = window.matchMedia('(prefers-color-scheme: light)');
    const apply = () => {
      const next = themeFor(mq.matches ? 'light' : 'dark', prefRef.current);
      if (next !== themeRef.current) setTheme(next);
    };
    apply();
    mq.addEventListener?.('change', apply);
    return () => mq.removeEventListener?.('change', apply);
  }, [follow, setTheme]);

  return { scheme: schemeOf(theme), skin: pref, density, follow, setScheme, setSkin, setDensity, setFollow };
}
export type Appearance = ReturnType<typeof useAppearance>;
