import React from "react";

/** A drawn close mark: two strokes, never a typed symbol. The button carries its own aria-label. */
export function CloseMark() {
  return (
    <svg viewBox="0 0 10 10" width="10" height="10" aria-hidden focusable="false">
      <path d="M1.5 1.5l7 7M8.5 1.5l-7 7" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}
