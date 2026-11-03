/**
 * NL STRIP: the row the command bar shows for a
 * natural-language query. Chips and state labels only, no prose:
 *
 *   ready      Parse with Meta AI (Enter)     (a live call is explicit)
 *   loading    Parsing…
 *   parsed     Meta AI badge  +  removable filter chips
 *              (+ unmatched words, + category not served)
 *   fallback   Keyword match · <code>      (the model's reply was rejected)
 *   refused    Meta AI refused · <code>       (closed-schema check failed)
 *   timeout / unreachable                      state chips + retry
 *
 * No result here is the model's: the rows below come from the lexical
 * engine run on the validated filters.
 */
import React from 'react';
import { Chip } from '../shell/Chip';
import { Icon } from '../ui/Icon';
import { Kbd } from '../ui/Controls';
import { AiBadge } from './AiBadge';
import { refusalWord } from './nlQuery';
import { NL_DID, type NlView } from './useNlSearch';
import { CATEGORY_LABELS } from '../search/engine';

export const NL_COPY = {
  parse: 'Parse with Meta AI',
  retry: 'Retry',
  parsing: 'Parsing',
  fallback: 'Keyword match',
  refused: 'Meta AI refused',
  timeout: 'Meta AI timeout',
  unreachable: 'Meta AI unreachable',
  reset: 'Reset',
  removeHint: 'Remove last chip',
  groupLabel: 'Natural-language query',
} as const;

export function NlStrip({ nl }: { nl: NlView }) {
  if (nl.phase === 'off') return null;
  const o = nl.outcome;
  return (
    <div className="nlq" role="group" aria-label={NL_COPY.groupLabel} data-phase={nl.phase} data-status={o?.status}>
      {nl.phase === 'ready' && (
        <button type="button" className="nlq-btn" onClick={nl.run}><Icon name="ai" size="xs" /> {NL_COPY.parse} <Icon name="enter" size="xs" /></button>)}
      {nl.phase === 'loading' && <Chip kind="meta" word={NL_COPY.parsing} />}
      {o?.status === 'parsed' && (
        <>
          <AiBadge provenance={nl.provenance} did={NL_DID} />
          {nl.chips.map((c) => c.removable ? (
            <button key={c.id} type="button" className={`nlq-chip nlq-chip--${c.kind}`}
              aria-label={`Remove ${c.label}`} title="Remove" onClick={() => nl.remove(c.id)}>
              <span>{c.kind === 'overridden' ? `${c.label} overridden` : c.label}</span><Icon name="close" size="xs" />
            </button>
          ) : (
            <span key={c.id} className={`nlq-chip nlq-chip--${c.kind}`}>
              <span>No match · {c.label}</span>
            </span>
          ))}
          {nl.unserved.map((c) => <Chip key={c} kind="notserved" word={`${CATEGORY_LABELS[c]} not served`} />)}
          {nl.hasRemovals && <button type="button" className="nlq-btn" onClick={nl.reset}>{NL_COPY.reset}</button>}
        </>
      )}
      {o?.status === 'fallback' && (
        <>
          <Chip kind="meta" word={NL_COPY.fallback} meta={refusalWord(o.code)} />
          <AiBadge provenance={o.provenance} did={NL_DID} />
        </>
      )}
      {o?.status === 'refused' && <Chip kind="down" word={NL_COPY.refused} meta={refusalWord(o.code)} />}
      {o?.status === 'timeout' && (
        <><Chip kind="down" word={NL_COPY.timeout} />
          <button type="button" className="nlq-btn" onClick={nl.run}>{NL_COPY.retry}</button></>)}
      {o?.status === 'unreachable' && (
        <><Chip kind="notserved" word={NL_COPY.unreachable} />
          <button type="button" className="nlq-btn" onClick={nl.run}>{NL_COPY.retry}</button></>)}
      {o?.status === 'parsed' && nl.chips.some((c) => c.removable) && (
        <span className="nlq-hint"><Kbd keys="Alt+Backspace" /> {NL_COPY.removeHint}</span>)}
    </div>
  );
}
