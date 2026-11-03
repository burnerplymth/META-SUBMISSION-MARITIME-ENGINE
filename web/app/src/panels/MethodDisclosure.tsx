import React from 'react';

/** The one place a panel may explain itself: a closed "Method" disclosure
 *  holding at most three short lines. Call it with `data-methodology` so the
 *  prose guard (guard/scan.ts) treats the children as drawer text. */
export function MethodDisclosure({ children }: { children: React.ReactNode; 'data-methodology'?: boolean }) {
  return (
    <details className="method-disclosure" data-methodology>
      <summary className="mono">Method</summary>
      {children}
    </details>
  );
}
