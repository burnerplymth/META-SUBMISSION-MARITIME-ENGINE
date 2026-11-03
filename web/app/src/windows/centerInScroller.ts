/**
 * Bring `el` to the middle of its horizontal scroller by setting scrollLeft.
 *
 * Element.scrollIntoView() also makes the element the sequential focus navigation starting point (Chromium), so
 * calling it on mount moved the first Tab press past the skip link and the top bar. Setting scrollLeft moves the
 * scroller and nothing else. Returns the new scrollLeft.
 */
export function centerInScroller(scroller: HTMLElement, el: HTMLElement): number {
  const s = scroller.getBoundingClientRect();
  const e = el.getBoundingClientRect();
  const delta = e.left + e.width / 2 - (s.left + s.width / 2);
  const max = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
  const next = Math.max(0, Math.min(max, scroller.scrollLeft + delta));
  scroller.scrollLeft = next;
  return next;
}
