import React from 'react';

/** Target id of the workspace landmark (<main>) that the skip link moves focus to. */
export const MAIN_ID = 'main-content';

/** Move focus to the workspace landmark. The address hash carries app state (run=, layout=), so the link never
 *  navigates: it is an anchor for assistive technology and a click handler for everyone else. */
export function focusMain(doc: Document = document): boolean {
  const el = doc.getElementById(MAIN_ID);
  if (!el) return false;
  el.focus({ preventScroll: false });
  return doc.activeElement === el;
}

/** First focusable element on the page: hidden until it has focus (a11y.css). */
export function SkipLink() {
  return (
    <a className="skip-link" href={`#${MAIN_ID}`} onClick={(e) => { e.preventDefault(); focusMain(); }}>
      Skip to content
    </a>
  );
}
