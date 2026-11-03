import React from 'react';

/** The chain narrative (POST /llm/explain) is served model or template text.
 *  It sits in a closed disclosure under its provenance chips so the panel
 *  itself carries chips and tables only; the step record stays in view. Marked
 *  `data-methodology` so the prose guard treats it as drawer text. */
export function NarrativeDisclosure({ text }: { text: string }) {
  return (
    <details className="method-disclosure" data-methodology>
      <summary className="mono">Narrative</summary>
      <p className="explain-text">{text}</p>
    </details>
  );
}
