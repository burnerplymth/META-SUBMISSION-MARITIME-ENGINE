/**
 * ASK THE CHAIN: a question box under the Explain narrative.
 * The loaded causal chain is the whole universe of the question:
 *
 *   ask   -> POST /llm/ask/preview (same body as the ask)
 *     refused by the server (a place outside the chain, ...)  -> Refused chip, no call
 *     recorded transport (free)       -> runs straight away
 *     any other transport (paid)      -> Estimate $… and Confirm paid call first
 *   reply -> Meta AI badge + Post-check pass + the sentence (labelled by the
 *            badge), or Template (model text discarded), or Refused · <code>.
 *
 * Nothing the model says reaches the engine, the ranking or the grade:
 * the answer is content under a provenance chip, nothing else.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { humaniseIds } from '../format';
import { nodeName } from '../seed';
import { useDeskStore } from '../store';
import { Chip } from '../shell/Chip';
import { Icon } from '../ui/Icon';
import type { Chain } from '../types';
import { AiBadge } from './AiBadge';
import { noteMetaCall } from './aiStatus';
import { recordAiCall } from './aiCalls';
import { askChainCall, askChainPreview, MetaCallError } from './client';
import {
  askCodeWord, askPreviewView, askView, buildAskRequest, chainScope, suggestQuestions,
  ASK_MAX_CHARS, type AskPreviewView, type AskRequest, type AskView,
} from './askChain';

export const ASK_COPY = {
  title: 'Ask the chain',
  placeholder: 'Ask about this chain',
  button: 'Ask',
  confirm: 'Confirm paid call',
  cancel: 'Cancel',
  working: 'Asking',
  refused: 'Refused',
  failed: 'Ask failed',
  invalid: 'Reply not usable',
  template: 'Rule-based answer',
  templateMeta: 'No model text',
  did: 'Answered 1 question',
  groupLabel: 'Ask the loaded causal chain',
  inputLabel: 'Question about the loaded chain',
} as const;

export type AskPhase =
  | { p: 'idle' }
  | { p: 'previewing'; q: string }
  | { p: 'confirm'; q: string; req: AskRequest; estimate: string | null }
  | { p: 'running'; q: string; estimate: string | null }
  | { p: 'done'; q: string; view: AskView; estimate: string | null }
  | { p: 'refused'; q: string; code: string }
  | { p: 'error'; q: string; code: string };

export function AskChain({ chain }: { chain: Chain | null }) {
  // The wrapper keys on the run: a different chain starts a clean box, and a
  // late reply for the previous run can never render under a newer one.
  if (!chain) return null;
  return <AskChainBox key={chain.run_id} chain={chain} />;
}

function AskChainBox({ chain }: { chain: Chain }) {
  const s = useDeskStore();
  const [text, setText] = useState('');
  const [st, setSt] = useState<AskPhase>({ p: 'idle' });
  const abort = useRef<AbortController | null>(null);
  const catalogue = useMemo(
    () => (s.graph?.nodes ?? []).map((n) => n as { id?: unknown; name?: unknown })
      .filter((n): n is { id: string; name?: string } => typeof n.id === 'string')
      .map((n) => ({ id: n.id, name: typeof n.name === 'string' ? n.name : undefined })),
    [s.graph]);
  useEffect(() => () => abort.current?.abort(), []);
  const selectedName = s.selectedNode && chainScope(chain).includes(s.selectedNode) ? nodeName(s.selectedNode) : null;
  const suggestions = suggestQuestions(chain, selectedName);

  const run = async (q: string, req: AskRequest, estimate: string | null) => {
    setSt({ p: 'running', q, estimate });
    try {
      const raw = await askChainCall(req, abort.current?.signal);
      const view = askView(raw, chain, catalogue);
      if ('provenance' in view) recordAiCall({ surface: 'ask', did: ASK_COPY.did, provenance: view.provenance });
      noteMetaCall();
      setSt({ p: 'done', q, view, estimate });
    } catch (e) {
      setSt({ p: 'error', q, code: e instanceof MetaCallError ? e.code : 'failed' });
    }
  };
  const start = async (question: string) => {
    const req = buildAskRequest(chain, question);
    if (!req) return;
    abort.current?.abort();
    abort.current = new AbortController();
    setSt({ p: 'previewing', q: req.question });
    let pv: AskPreviewView | null = null;
    try { pv = askPreviewView(await askChainPreview(req, abort.current.signal)); } catch (e) {
      // A refusal can also come back as a typed error (e.g. run_not_found).
      if (e instanceof MetaCallError && !['unreachable', 'timeout', 'aborted', 'http_404', 'http_405'].includes(e.code)) {
        setSt({ p: 'error', q: req.question, code: e.code });
        return;
      }
      pv = null;
    }
    if (pv?.kind === 'refused') { setSt({ p: 'refused', q: req.question, code: pv.code }); return; }
    // A paid call, or one whose cost the API would not preview, asks first.
    if (!pv || pv.paid) { setSt({ p: 'confirm', q: req.question, req, estimate: pv ? pv.estimate : null }); return; }
    await run(req.question, req, pv.estimate);
  };
  const busy = st.p === 'previewing' || st.p === 'running';

  return (
    <div className="askc" role="group" aria-label={ASK_COPY.groupLabel} data-phase={st.p}>
      <div className="askc-head">
        <span>{ASK_COPY.title}</span>
        <Chip kind="meta" word={`Scope · ${chainScope(chain).length} places`} />
      </div>
      <form className="askc-form" onSubmit={(e) => { e.preventDefault(); if (!busy) void start(text); }}>
        <input className="askc-in" value={text} maxLength={ASK_MAX_CHARS} placeholder={ASK_COPY.placeholder}
          aria-label={ASK_COPY.inputLabel} onChange={(e) => setText(e.target.value)} disabled={busy} />
        <button type="submit" className="fclass-btn" disabled={busy || !text.trim()}><Icon name="ai" size="xs" /> {ASK_COPY.button}</button>
      </form>
      {st.p === 'idle' && suggestions.length > 0 && (
        <div className="askc-sugg">
          {suggestions.map((q) => (
            <button key={q} type="button" className="nlq-chip nlq-chip--term" onClick={() => { setText(q); void start(q); }}>{q}</button>))}
        </div>)}
      <AskResult st={st}
        onConfirm={st.p === 'confirm' ? () => void run(st.q, st.req, st.estimate) : undefined}
        onCancel={() => setSt({ p: 'idle' })} onPlace={(id) => s.selectNode(id)} />
    </div>
  );
}

/** The state under the form: exported for static-markup rendering. */
export function AskResult({ st, onConfirm, onCancel, onPlace }: {
  st: AskPhase; onConfirm?: () => void; onCancel?: () => void; onPlace?: (id: string) => void;
}) {
  if (st.p === 'idle') return null;
  return (
    <div className="askc-out" aria-live="polite">
      <p className="askc-q"><b>Q</b> {st.q}</p>
      {(st.p === 'previewing' || st.p === 'running') && (
        <div className="askc-row">
          <Chip kind="meta" word={ASK_COPY.working} />
          {st.p === 'running' && st.estimate && <Chip kind="meta" word={st.estimate} />}
        </div>)}
      {st.p === 'confirm' && (
        <div className="askc-row">
          {st.estimate && <Chip kind="meta" word={st.estimate} />}
          <button type="button" className="fclass-btn fclass-btn--go" onClick={onConfirm}>{ASK_COPY.confirm}</button>
          <button type="button" className="fclass-btn" onClick={onCancel}>{ASK_COPY.cancel}</button>
        </div>)}
      {st.p === 'refused' && (
        <div className="askc-row"><Chip kind="notserved" word={ASK_COPY.refused} meta={askCodeWord(st.code)} /></div>)}
      {st.p === 'error' && (
        <div className="askc-row"><Chip kind="down" word={ASK_COPY.failed} meta={askCodeWord(st.code)} /></div>)}
      {st.p === 'done' && <AskDone view={st.view} estimate={st.estimate} onPlace={onPlace} />}
    </div>
  );
}

/** Cosmetic only (the post-check already ran on the raw text): the
 *  deterministic template's machine prefix comes off (the TEMPLATE chip says
 *  it) and place ids read as place names. Steps, channels and delays are
 *  untouched. */
export function displayText(text: string, graph: Parameters<typeof humaniseIds>[1]): string {
  const cleaned = text
    .replace(/^\s*TEMPLATE\.\s*/i, '')
    .replace(/^\s*Run\s+run_[a-z0-9]+\s*:\s*/i, '')
    .replace(/\brun_[a-z0-9]{8,}\b/g, 'the run');
  return humaniseIds(cleaned, graph);
}

function AskDone({ view, estimate, onPlace }: { view: AskView; estimate: string | null; onPlace?: (id: string) => void }) {
  const { graph } = useDeskStore();
  if (view.kind === 'invalid') {
    return <div className="askc-row"><Chip kind="down" word={ASK_COPY.invalid} meta={askCodeWord(view.code)} /></div>;
  }
  if (view.kind === 'refused') {
    return (
      <div className="askc-row">
        <AiBadge provenance={view.provenance} did={ASK_COPY.did} />
        <Chip kind="notserved" word={ASK_COPY.refused} meta={askCodeWord(view.code)} />
        {view.cost && <Chip kind="meta" word={view.cost} />}
      </div>);
  }
  const tpl = view.kind === 'template';
  return (
    <>
      <div className="askc-row">
        {tpl
          ? <Chip kind="meta" word={ASK_COPY.template} meta={ASK_COPY.templateMeta} />
          : <AiBadge provenance={view.provenance} did={ASK_COPY.did} />}
        <Chip kind="meta" word={`Post-check ${view.postCheck.replace(/_/g, ' ').toLowerCase()}`} meta={tpl ? askCodeWord(view.reason) : undefined} />
        {tpl && view.cost && <Chip kind="meta" word={view.cost} />}
        {!tpl && estimate && !view.cost && <Chip kind="meta" word={estimate} />}
      </div>
      <p className="askc-a">{displayText(view.text, graph)}</p>
      {view.cited.length > 0 && (
        <div className="askc-row" role="group" aria-label="Places cited">
          {view.cited.map((id) => (
            <button key={id} type="button" className="nlq-chip nlq-chip--filter" onClick={() => onPlace?.(id)}>{nodeName(id)}</button>))}
        </div>)}
    </>
  );
}
