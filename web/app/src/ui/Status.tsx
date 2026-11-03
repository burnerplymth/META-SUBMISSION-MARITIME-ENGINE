import React from 'react';
import { STATE_MARK, chipWord, isProgressWord, type StateKind, type StateMarkName } from '../shell/stateChips';

/**
 * The state mark: a small drawn shape, never a font glyph. Each state has
 * its own shape so state is not carried by colour alone.
 *   dot     filled disc          Live
 *   ring    open ring            Snapshot
 *   half    ring, left half full Modelled
 *   target  ring with a centre   Simulated
 *   cross   two strokes          Down
 *   slash   ring with a slash    Not served
 */
export function StateMark({ name, size = 10 }: { name: StateMarkName; size?: number }) {
  const common = { width: size, height: size, viewBox: '0 0 10 10', 'aria-hidden': true as const, focusable: false as const, className: 'ui-mark', 'data-mark': name };
  switch (name) {
    case 'dot': return <svg {...common}><circle cx="5" cy="5" r="3.2" fill="currentColor" stroke="none" /></svg>;
    case 'ring': return <svg {...common}><circle cx="5" cy="5" r="2.7" fill="none" stroke="currentColor" strokeWidth="1.5" /></svg>;
    case 'half': return (
      <svg {...common}><circle cx="5" cy="5" r="2.7" fill="none" stroke="currentColor" strokeWidth="1.5" />
        <path d="M5 2.3a2.7 2.7 0 0 0 0 5.4z" fill="currentColor" stroke="none" /></svg>);
    case 'target': return (
      <svg {...common}><circle cx="5" cy="5" r="3.2" fill="none" stroke="currentColor" strokeWidth="1.25" />
        <circle cx="5" cy="5" r="1.1" fill="currentColor" stroke="none" /></svg>);
    case 'cross': return <svg {...common}><path d="M2.6 2.6l4.8 4.8M7.4 2.6L2.6 7.4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>;
    case 'slash': return (
      <svg {...common}><circle cx="5" cy="5" r="3.1" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <path d="M2.9 7.1l4.2-4.2" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" /></svg>);
  }
}

export interface StatusProps {
  state: StateKind;
  /** Overrides the default word. Omit to get the sentence-case word of the state. */
  word?: string;
  /** Plain text after the word (date, source, id). Never boxed. */
  meta?: string;
  title?: string;
  className?: string;
}

/**
 * ONE chip: a drawn mark plus one word, no border, no fill. The same
 * component serves every state (live, snapshot, simulated, modelled, down,
 * not served) and the neutral form (no mark: a source, id or code).
 * A window title bar shows at most one Status; rows inherit it and show
 * their own only when it differs.
 */
export function Status({ state, word, meta, title, className = '' }: StatusProps) {
  const mark = STATE_MARK[state];
  const w = chipWord(state, word);
  return (
    <span className={`chip chip--${state} ui-status${className ? ` ${className}` : ''}`} title={title} data-state={state} role={isProgressWord(w) ? 'status' : undefined}>
      {mark && <i className="chip-g" aria-hidden="true"><StateMark name={mark} /></i>}
      {w && <b className="chip-w">{w}</b>}
      {meta && <em className="chip-m">{meta}</em>}
    </span>
  );
}
