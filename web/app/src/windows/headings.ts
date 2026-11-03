/**
 * Heading levels inside a window. Each window is its own document region under the page heading (h1), but the
 * panels were written with the level their style needed (h5 for a section head, h3 for a card), so a screen
 * reader that lists headings met h1 then h5. The levels are therefore set per window, from the headings it holds:
 * the shallowest becomes level 2, each deeper distinct level the next one down, so the outline never skips a level
 * and the look (which belongs to the element, not the level) does not change.
 */

/** Level of each heading given the levels present in the window (ascending ranks from 2, capped at 6). */
export function levelMap(levels: readonly number[], base = 2): Map<number, number> {
  const distinct = [...new Set(levels)].sort((a, b) => a - b);
  return new Map(distinct.map((l, i) => [l, Math.min(6, base + i)]));
}

interface HeadingEl {
  tagName: string;
  getAttribute(n: string): string | null;
  setAttribute(n: string, v: string): void;
}
interface RootLike { querySelectorAll(sel: string): ArrayLike<HeadingEl> }

const tagLevel = (e: HeadingEl): number => {
  const m = /^H([1-6])$/.exec(e.tagName);
  return m ? Number(m[1]) : Number(e.getAttribute('aria-level')) || 2;
};

/** Set aria-level on every heading under `root`. Returns the number changed. */
export function applyHeadingLevels(root: RootLike): number {
  const hs = Array.from(root.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"]'));
  if (!hs.length) return 0;
  const map = levelMap(hs.map(tagLevel));
  let n = 0;
  for (const h of hs) {
    const to = String(map.get(tagLevel(h)));
    if (h.getAttribute('aria-level') !== to) { h.setAttribute('aria-level', to); n += 1; }
  }
  return n;
}
