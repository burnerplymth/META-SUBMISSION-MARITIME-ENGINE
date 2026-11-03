import React from 'react';
import { Status } from '../ui/Status';
import type { StateKind } from './stateChips';

/** Adapter kept for the callers that already import it (ProvChip and the
 *  panels): `Chip` is now the one `Status` (ui/Status.tsx). New
 *  code should import `Status` from `../ui`. */
export function Chip({ kind, word, meta, title, className = '' }:
  { kind: StateKind; word?: string; meta?: string; title?: string; className?: string }) {
  return <Status state={kind} word={word} meta={meta} title={title} className={className} />;
}

/** An empty or unreachable section: one status, centred. No prose. */
export function EmptyState({ word, kind = 'meta' }: { word: string; kind?: StateKind }) {
  return <div className="empty-state"><Status state={kind} word={word} /></div>;
}
