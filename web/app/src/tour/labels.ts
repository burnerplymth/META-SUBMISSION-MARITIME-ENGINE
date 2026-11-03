/**
 * The state labels a step shows under its caption. Computed from the real
 * state at display time, never from the caption: a step names a feed or a
 * model as Live only when the health board and the served transport say so
 * now (shell/feedState.ts, shell/truthLabel.ts). Pure.
 */
import type { Health } from '../types';
import { adapterFeedState, feedView } from '../shell/feedState';
import { runWord } from '../shell/truthLabel';
import type { TourLabel, TourLabelKey } from './types';

export interface RunMetaLike { label: string; mode: string; liveRef?: { source: string; retrieved_at?: string; mode?: string; what: string } }
export interface LabelCtx {
  health: Health | null;
  snapshotMode: boolean;
  runMeta: RunMetaLike | null;
  /** Meta AI transport as the status route served it. */
  llm: 'LIVE' | 'RECORDED' | null;
  /** The Agent session's own transport (falls back to the served status). */
  agent: 'LIVE' | 'RECORDED' | null;
  ledger: { locked: number; graded: number } | null;
  now: number;
}

const feeds = (c: LabelCtx): TourLabel[] => {
  if (!c.health) return [{ kind: 'down', word: 'Source health down' }];
  const n = { live: 0, snapshot: 0, down: 0, calm: 0 };
  for (const a of c.health.adapters) {
    const f = adapterFeedState(c.health, a.adapter.toLowerCase(), c.snapshotMode);
    const v = feedView(f);
    if (v.calm || f.word === 'NOT SERVED' || f.word === 'CHECKING' || f.word === 'RECORDED') n.calm += 1;
    else if (f.word === 'LIVE') n.live += 1;
    else if (f.word === 'SNAPSHOT') n.snapshot += 1;
    else n.down += 1;
  }
  const out: TourLabel[] = [];
  if (n.live) out.push({ kind: 'live', word: 'Live', meta: `${n.live} feeds` });
  if (n.snapshot) out.push({ kind: 'snapshot', word: 'Snapshot', meta: `${n.snapshot} feeds` });
  if (n.down) out.push({ kind: 'down', word: 'Down', meta: `${n.down} feeds` });
  /* not connected or offline copy: calm, as in the status bar (never counted as down) */
  if (n.calm) out.push({ kind: 'notserved', word: 'Not served', meta: `${n.calm} feeds` });
  return out;
};

const chain = (c: LabelCtx): TourLabel[] => {
  if (!c.runMeta) return [];
  const w = runWord(c.runMeta, c.health, c.snapshotMode, c.now);
  const out: TourLabel[] = [];
  if (w === 'LIVE') out.push({ kind: 'live', word: 'Live' });
  else if (w === 'SNAPSHOT') out.push({ kind: 'snapshot', word: 'Snapshot' });
  else if (w === 'SCENARIO') out.push({ kind: 'sim', word: 'Scenario' });
  else out.push({ kind: 'snapshot', word: 'Recorded' });
  out.push({ kind: 'meta', word: 'Deterministic' });
  return out;
};

const transport = (t: 'LIVE' | 'RECORDED' | null): TourLabel[] =>
  t === 'LIVE' ? [{ kind: 'live', word: 'Live', meta: 'Meta AI' }]
    : t === 'RECORDED' ? [{ kind: 'snapshot', word: 'Recorded', meta: 'Meta AI' }]
      : [{ kind: 'notserved', word: 'Not served', meta: 'Meta AI' }];

export function labelsFor(keys: readonly TourLabelKey[] | undefined, c: LabelCtx): TourLabel[] {
  const out: TourLabel[] = [];
  for (const k of keys ?? []) {
    switch (k) {
      case 'feeds': out.push(...feeds(c)); break;
      case 'chain': out.push(...chain(c)); break;
      case 'metaai': out.push(...transport(c.llm)); break;
      case 'agent': out.push(...transport(c.agent ?? c.llm)); break;
      case 'simulated': out.push({ kind: 'sim', word: 'Simulated' }); break;
      case 'modelled': out.push({ kind: 'model', word: 'Modelled' }); break;
      case 'recorded': out.push({ kind: 'snapshot', word: 'Recorded' }); break;
      case 'snapshot': out.push({ kind: 'snapshot', word: 'Snapshot' }); break;
      case 'ledger': out.push(c.ledger ? { kind: 'meta', word: 'Ledger', meta: `${c.ledger.locked} locked · ${c.ledger.graded} graded` } : { kind: 'down', word: 'Ledger down' }); break;
    }
  }
  return out;
}

/** The Meta AI transport word from the served LLM status (never assumed). */
export function transportOf(st: { transport?: string; transport_label?: string; live?: boolean } | null | undefined): 'LIVE' | 'RECORDED' | null {
  if (!st) return null;
  const word = (st.transport_label ?? st.transport ?? '').toUpperCase();
  if (st.live === true || word === 'LIVE') return 'LIVE';
  if (word === 'RECORDED' || word.includes('FIXTURE')) return 'RECORDED';
  return null;
}
