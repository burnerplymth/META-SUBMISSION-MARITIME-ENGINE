/** The reduced-motion preference, read live at call time.
 *  Under it, timed animation (the cascade pulse ticker, the cascade
 *  stagger) does not run — state still lands, instantly and still. */
export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}
