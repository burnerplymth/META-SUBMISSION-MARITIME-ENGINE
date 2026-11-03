import React from 'react';
import { StateMark } from '../../ui';
import { STATE_MARK } from '../../shell/stateChips';
import type { ChipSpec } from './labels';
import { sentenceCase } from '../../copy';

/** The house chip (a drawn mark plus one word, no border). The doctrine words
 *  and the API's own words print in sentence case; codes
 *  and units (CHN, TEU, UTC) keep their capitals. */
export function Tag({ state, word, meta, title }: ChipSpec) {
  const mark = STATE_MARK[state];
  return (
    <span className={`chip chip--${state} ui-status ptw-tag`} title={title} data-state={state}>
      {mark && <i className="chip-g" aria-hidden="true"><StateMark name={mark} /></i>}
      <b className="chip-w">{sentenceCase(word)}</b>
      {meta && <em className="chip-m">{meta}</em>}
    </span>
  );
}

export function Tags({ chips, className = '' }: { chips: readonly ChipSpec[]; className?: string }) {
  if (chips.length === 0) return null;
  return <span className={`ptw-tags${className ? ` ${className}` : ''}`}>{chips.map((c, i) => <Tag key={`${c.word}${i}`} {...c} />)}</span>;
}
