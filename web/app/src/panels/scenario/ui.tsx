import React, { useEffect, useState } from 'react';
import { Chip } from '../../shell/Chip';
import { Status } from '../../ui/Status';
import type { StateKind } from '../../shell/stateChips';
import { fmtElapsed, refusalSentence, type Refusal } from './logic';
import { labelWord } from '../../copy';

/* Small shared pieces of the scenario loop panel. State words, glyphs and
 * chips only: no explanatory prose anywhere in this folder. */

export type Tone = 'ok' | 'bad' | 'busy' | 'idle' | 'warn' | 'meta';
/** The drawn mark per outcome tone: a solid dot is a pass, a cross a failure, a ring with a centre work in progress. */
const TONE_STATE: Record<Tone, StateKind> = { ok: 'live', bad: 'down', busy: 'sim', idle: 'snapshot', warn: 'model', meta: 'meta' };

/** An outcome tag: a drawn mark + word (+ meta), one `Status`. Colour is never the only carrier. */
export function Tag({ tone, word, meta, title }: { tone: Tone; word: string; meta?: string; title?: string }) {
  return (
    <span className={`scn-tag scn-tag--${tone}`} data-tone={tone}>
      <Status state={TONE_STATE[tone]} word={labelWord(word)} meta={meta} title={title} />
    </span>
  );
}

/** Segmented control (radio group semantics, arrow keys inside). */
export function Seg<T extends string | number>({ value, options, onChange, label, disabled }:
  { value: T; options: ReadonlyArray<{ v: T; label: string; title?: string; disabled?: boolean }>; onChange: (v: T) => void; label: string; disabled?: boolean }) {
  const move = (dir: 1 | -1, from: number) => {
    const live = options.filter((o) => !o.disabled);
    if (!live.length) return;
    const cur = options[from];
    const i = live.findIndex((o) => o.v === cur.v);
    onChange(live[(i + dir + live.length) % live.length].v);
  };
  return (
    <span className="seg scn-seg" role="radiogroup" aria-label={label}>
      {options.map((o, i) => (
        <button key={String(o.v)} type="button" role="radio" aria-checked={o.v === value} title={o.title}
          disabled={disabled || o.disabled} tabIndex={o.v === value ? 0 : -1}
          className={`seg-b${o.v === value ? ' on' : ''}`} onClick={() => onChange(o.v)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); move(1, i); }
            else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { e.preventDefault(); move(-1, i); }
          }}>{o.label}</button>
      ))}
    </span>
  );
}

/** A chip row of choices (durations, sizes). */
export function ChoiceChips({ value, options, onChange, label, fmt }:
  { value: number; options: readonly number[]; onChange: (v: number) => void; label: string; fmt?: (v: number) => string }) {
  return (
    <span className="scn-choices" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button key={o} type="button" role="radio" aria-checked={o === value} className={`scn-choice mono${o === value ? ' on' : ''}`}
          onClick={() => onChange(o)}>{fmt ? fmt(o) : String(o)}</button>
      ))}
    </span>
  );
}

export function Field({ label, bad, children, wide }: { label: string; bad?: boolean; children: React.ReactNode; wide?: boolean }) {
  return <label className={`scn-field${bad ? ' scn-field--bad' : ''}${wide ? ' scn-field--wide' : ''}`}><span>{label}</span>{children}</label>;
}

/** Elapsed time since `since` (ms epoch), ticking each second. */
export function useElapsed(since: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (since === null) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [since]);
  return since === null ? 0 : Math.max(0, now - since);
}

/** Building, with honest elapsed time. */
export function Building({ since, word = 'Building', meta }: { since: number; word?: string; meta?: string }) {
  const ms = useElapsed(since);
  return <Tag tone="busy" word={word} meta={`${fmtElapsed(ms)}${meta ? ` · ${meta}` : ''}`} />;
}

/** A refusal: Refused, the short reason code and one plain sentence. */
export function RefusalLine({ r }: { r: Refusal }) {
  return (
    <div className="scn-refusal mono" role="alert">
      <Tag tone="bad" word="Refused" meta={r.code} />
      <span className="scn-reason">{refusalSentence(r)}</span>
      {r.origin === 'local' && <Chip kind="meta" word="Local check" />}
    </div>
  );
}

/** Copy to the clipboard; the button shows Copied for a moment. */
export function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [done, setDone] = useState<'idle' | 'ok' | 'fail'>('idle');
  useEffect(() => {
    if (done === 'idle') return;
    const id = window.setTimeout(() => setDone('idle'), 1600);
    return () => window.clearTimeout(id);
  }, [done]);
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); setDone('ok'); } catch { setDone('fail'); }
  };
  return <button type="button" className="btn scn-btn" onClick={() => void copy()}>{done === 'ok' ? 'Copied' : done === 'fail' ? 'Copy failed' : label}</button>;
}

export const Empty = ({ word, tone = 'idle' }: { word: string; tone?: Tone }) =>
  <div className="empty-state"><Tag tone={tone} word={word} /></div>;

/** Draws the sticky-head table the other panels use. */
export function Th({ children, num }: { children?: React.ReactNode; num?: boolean }) { return <th className={num ? 'num' : undefined}>{children}</th>; }
