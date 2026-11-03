/**
 * AI BADGE. The one quiet mark every model
 * output carries: a sparkles icon and `Meta AI`, no border. A small mark
 * beside it says the mode without opening anything and never by colour
 * alone: a solid dot is Live, a ring is Recorded, a slashed ring is
 * Unavailable. The accessible name reads `Meta AI, recorded`.
 *
 * The popover lists Model, Mode, Prompt id, Cost, Cached, Time and a
 * one-line Did (`Classified 1 headline`). Enter/Space toggles it, Esc
 * closes it and returns focus to the badge.
 *
 * Built from a served provenance block alone (provenance.ts). Absent,
 * malformed or no-model provenance renders NOTHING, never a fake badge, and
 * a recorded fixture is never worded, marked or coloured as live.
 */
import React, { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from '../ui/Icon';
import { StateMark } from '../ui/Status';
import { clockUtc } from './aiCalls';
import { metaChipView, NA, parseProvenance, type MetaChipView, type MetaRow } from './provenance';
import './meta.css';

export type AiMode = 'live' | 'recorded' | 'unavailable';
const MODE_MARK = { live: 'dot', recorded: 'ring', unavailable: 'slash' } as const;
export const MODE_WORD: Record<AiMode, string> = { live: 'Live', recorded: 'Recorded', unavailable: 'Unavailable' };

const MODE_BY_WORD: Record<string, AiMode> = { Live: 'live', Recorded: 'recorded', Unavailable: 'unavailable' };

/** The drawn mode mark: solid dot (Live), ring (Recorded), slashed ring (Unavailable). */
export function ModeMark({ mode, size = 10 }: { mode: AiMode; size?: number }) {
  return <span className={`ai-mark ai-mark--${mode}`} data-mode={mode}><StateMark name={MODE_MARK[mode]} size={size} /></span>;
}

/** Rows as a definition list; a Mode row carries its mark. */
export function PopRows({ rows }: { rows: MetaRow[] }) {
  return (
    <dl className="metachip-rows ai-rows">
      {rows.map((r) => (
        <React.Fragment key={r.k}>
          <dt>{r.k}</dt>
          <dd>
            {r.k === 'Mode' && MODE_BY_WORD[r.v] && <ModeMark mode={MODE_BY_WORD[r.v]} />}
            {r.v}
          </dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

/** A button that opens a small keyboard-operable details popover, portalled
 *  so no window clip can cut it, flipped above the button when it sits low. */
export function PopButton({ className = '', wrapClass = '', ariaLabel, title, head, body, wide = false, defaultOpen = false, data, children }: {
  className?: string;
  wrapClass?: string;
  ariaLabel: string;
  title?: string;
  head: string;
  body: React.ReactNode | ((close: () => void) => React.ReactNode);
  wide?: boolean;
  defaultOpen?: boolean;
  data?: Record<string, string>;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement | null>(null);
  const popRef = useRef<HTMLDivElement | null>(null);
  const popId = useId();

  useEffect(() => {
    if (!open) return;
    const place = () => {
      const r = btnRef.current?.getBoundingClientRect();
      if (!r) return;
      const w = wide ? 300 : 248;
      const left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8));
      const popH = popRef.current?.offsetHeight ?? 160;
      const top = r.bottom + 4 + popH > window.innerHeight ? Math.max(8, r.top - popH - 4) : r.bottom + 4;
      setPos({ left, top });
    };
    place();
    const raf = requestAnimationFrame(place);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); setOpen(false); btnRef.current?.focus(); }
    };
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (btnRef.current?.contains(t) || popRef.current?.contains(t)) return;
      setOpen(false);
    };
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    window.addEventListener('keydown', onKey, true);
    window.addEventListener('pointerdown', onDown);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('keydown', onKey, true);
      window.removeEventListener('pointerdown', onDown);
    };
  }, [open, wide]);

  return (
    <span className={`metachip-wrap ${wrapClass}`.trim()}>
      <button ref={btnRef} type="button" className={className} {...data} title={title} aria-expanded={open}
        aria-controls={open ? popId : undefined} aria-haspopup="dialog" aria-label={ariaLabel}
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}>
        {children}
      </button>
      {open && typeof document !== 'undefined' && createPortal(
        <div id={popId} ref={popRef} role="dialog" aria-label={head} className={`metachip-pop${wide ? ' metachip-pop--wide' : ''}`}
          style={{ left: pos?.left ?? -9999, top: pos?.top ?? 0 }}>
          <div className="metachip-pop-head">{head}</div>
          {typeof body === 'function' ? body(() => setOpen(false)) : body}
        </div>, document.body)}
    </span>
  );
}

/** The rows a badge popover shows: the view's five, then Time and Did. */
export function badgeRows(view: MetaChipView, did: string, at: number): MetaRow[] {
  return [...view.rows, { k: 'Time', v: `${clockUtc(at)} UTC` }, { k: 'Did', v: did || NA }];
}

/** The badge face: icon, the words `Meta AI`, the mode mark. */
export function BadgeFace({ mode }: { mode: AiMode }) {
  return (
    <>
      <Icon name="ai" size="xs" className="ai-badge-ico" />
      <span className="ai-badge-w">Meta AI</span>
      <ModeMark mode={mode} />
    </>
  );
}

export function AiBadge({ provenance, did, at, defaultOpen = false, className = '' }: {
  provenance: unknown;
  /** One line of what the call did: `Classified 1 headline`. */
  did: string;
  /** Epoch ms when the answer reached this page (default: now, at first render). */
  at?: number;
  defaultOpen?: boolean;
  className?: string;
}) {
  const [received] = useState(() => at ?? Date.now());
  const view = metaChipView(parseProvenance(provenance));
  if (!view) return null;
  return (
    <PopButton className={`ai-badge ai-badge--${view.tone}`} wrapClass={className} ariaLabel={view.aria} head="Meta AI"
      data={{ 'data-transport': view.transport, 'data-ai-badge': view.tone }} defaultOpen={defaultOpen}
      body={<PopRows rows={badgeRows(view, did, at ?? received)} />}>
      <BadgeFace mode={view.tone} />
    </PopButton>
  );
}

/** Server-render form: the popover rows inline, no popover. */
export function AiBadgeExpanded({ provenance, did, at = 0 }: { provenance: unknown; did: string; at?: number }) {
  const view = metaChipView(parseProvenance(provenance));
  if (!view) return null;
  return (
    <div className={`metachip-card metachip-card--${view.tone}`}>
      <div className="metachip-pop-head">Meta AI</div>
      <PopRows rows={badgeRows(view, did, at)} />
    </div>
  );
}
