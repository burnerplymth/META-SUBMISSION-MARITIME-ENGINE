import React from 'react';
import { chipTone, type ChipTone } from './model';
import { Status } from '../../ui/Status';
import { sentenceCase } from '../../copy';
import type { StateKind } from '../../shell/stateChips';
import './reports.css';

/** The tone onto the one `Status` grammar: a drawn mark and a word, never colour alone. */
const TONE_STATE: Record<ChipTone, StateKind> = {
  live: 'live', snapshot: 'snapshot', sourced: 'meta', model: 'model', sim: 'sim', down: 'down', notserved: 'notserved', meta: 'meta', test: 'sim', flag: 'meta',
};

/** State / provenance chip. `chip` picks the tone; `word`
 *  overrides the printed text. Words print in sentence case. */
export function Chip({ chip, word, title, tone }: { chip: string; word?: string; title?: string; tone?: ChipTone }) {
  const t = tone ?? chipTone(chip);
  return <span className={`rk-chip rk-chip--${t}`}><Status state={TONE_STATE[t]} word={sentenceCase(word ?? chip)} title={title} /></span>;
}

/** Plain neutral chip (source, run id, units, hash). */
export function MetaChip({ children, title }: { children: React.ReactNode; title?: string }) {
  return <span className="rk-chip rk-chip--meta" title={title}><span>{typeof children === 'string' ? sentenceCase(children) : children}</span></span>;
}

/** The no-prose empty / failure state: one hatched chip and its code. */
export function StateLine({ chip, word, code, onRetry }: { chip: string; word: string; code?: string; onRetry?: () => void }) {
  return (
    <div className="rk-state" role="status">
      <Chip chip={chip} word={word} />
      {code && <span className="rk-code mono">{code}</span>}
      {onRetry && <button type="button" className="rk-btn" onClick={onRetry}>Retry</button>}
    </div>
  );
}
