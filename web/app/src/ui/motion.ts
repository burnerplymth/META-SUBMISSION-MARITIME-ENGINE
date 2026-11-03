/** The reduced-motion preference, read at call time (never cached: the setting can change while the page is open). */
export function reducedMotion(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Scroll behaviour for script-driven scrolling. The CSS rule for reduced motion does not reach an explicit
 *  `behavior: 'smooth'` option, so every smooth scroll in the app takes its value from here. */
export function scrollBehavior(): ScrollBehavior {
  return reducedMotion() ? 'auto' : 'smooth';
}
