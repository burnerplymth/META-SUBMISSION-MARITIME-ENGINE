import React from 'react';

const HIDDEN: React.CSSProperties = {
  position: 'absolute', width: 1, height: 1, margin: -1, padding: 0, overflow: 'hidden', clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap', border: 0,
};

/** A polite live region for an async result or error: the region stays mounted, only its text changes, so a screen
 *  reader announces "Evaluating", "Result ready" or "Down" without any visual change. Pass a short state word. */
export function LiveStatus({ text }: { text: string | null | undefined }) {
  return <span role="status" aria-live="polite" aria-atomic="true" style={HIDDEN} data-live-status>{text ?? ''}</span>;
}
