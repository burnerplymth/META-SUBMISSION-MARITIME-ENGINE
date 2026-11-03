import React from 'react';
import { CREDITS, GROUPS, GROUP_LABEL, creditText, type Credit } from './credits';
import { SafeLink } from '../components/SafeLink';

/** The registry as a list: one row per source, grouped; no prose. Used by the Sources dialog. */
export function SourcesList({ credits = CREDITS }: { credits?: readonly Credit[] }) {
  return (
    <div className="src-list" data-testid="sources-list">
      {GROUPS.map((g) => {
        const rows = credits.filter((c) => c.group === g);
        if (!rows.length) return null;
        return (
          <section key={g} className="src-group" aria-label={GROUP_LABEL[g]}>
            <h3>{GROUP_LABEL[g]}</h3>
            <ul>
              {rows.map((c) => (
                <li key={c.id} className="src-row" data-source-id={c.id} data-required={c.required ? '1' : '0'}>
                  <div className="src-head">
                    <b>{c.name}</b>
                    <span className="src-chip mono">{c.licence_short}</span>
                    {c.required && <span className="src-chip src-chip--req mono">Attribution required</span>}
                  </div>
                  <p className="src-line mono" data-credit-line={c.id}>{creditText(c)}</p>
                  <p className="src-links mono small"><SafeLink href={c.url}>Source</SafeLink> · <SafeLink href={c.terms_url}>Terms</SafeLink></p>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
