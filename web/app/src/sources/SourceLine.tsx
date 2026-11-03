import React from 'react';
import { creditById, creditText } from './credits';

/** The registry's credit line for one or more sources, as a short source line under a figure.
 *  Renders nothing for an id the registry does not hold. */
export function SourceLine({ ids, className = 'fineprint mono' }: { ids: readonly string[]; className?: string }) {
  const rows = ids.map((id) => creditById(id)).filter((c): c is NonNullable<typeof c> => !!c);
  if (!rows.length) return null;
  return (
    <>
      {rows.map((c) => (
        <p key={c.id} className={`${className} src-credit`} data-source-line={c.id}>{creditText(c)}</p>
      ))}
    </>
  );
}
