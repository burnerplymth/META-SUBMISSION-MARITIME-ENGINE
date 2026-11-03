/**
 * CHAT CARDS: the tool-call cards, the confirmation cards and the sources list.
 * Every string is rendered as a text node. Links come from citations that the
 * server built from URLs it checked (http or https only) and open with
 * rel="noopener noreferrer", beside their domain.
 */
import { humaniseAgentText } from '../format';
import React, { useState } from 'react';
import { ModeMark } from '../meta/AiBadge';
import { Button, Icon, Status } from '../ui';
import { MODE_WORD, argsSummary, confirmCard, fmtCost, fmtDuration, isUiTool, stepState, toolLabel } from '../agent/timeline';
import type { AgentAction, AgentTransport } from '../agent/types';
import { safeHref } from './markdown';
import type { ChatStep, Citation, WebSource } from './types';

export function ModeChip({ mode, className = '' }: { mode: AgentTransport; className?: string }) {
  const m = mode === 'LIVE' ? 'live' : 'recorded';
  return (
    <span className={`ag-mode ag-mode--${m}${className ? ` ${className}` : ''}`} data-transport={mode} title={`Meta AI, ${MODE_WORD[mode].toLowerCase()}`}>
      <ModeMark mode={m} /> {MODE_WORD[mode]}
    </span>
  );
}

const FLAG_WORD: Readonly<Record<string, string>> = {
  instruction_like_text_in_web_result: 'Instruction-like web text',
  instruction_like_text_in_result: 'Instruction-like text',
  result_truncated: 'Result cut',
};
const flagWord = (f: string) => FLAG_WORD[f] ?? f.replace(/_/g, ' ');

function prettyArgs(a: Record<string, unknown> | null | undefined): string {
  try {
    const s = JSON.stringify(a ?? {}, null, 1);
    return s.length > 1200 ? `${s.slice(0, 1199)}…` : s;
  } catch {
    return '';
  }
}

function SourceLine({ s }: { s: WebSource }) {
  const link = safeHref(s.url);
  return (
    <li className="ch-src-line">
      {link
        ? <a className="ch-link" href={link.href} target="_blank" rel="noopener noreferrer">{s.title}</a>
        : <span>{s.title}</span>}
      <span className="ch-src-d"> {s.domain}{s.date ? `, ${s.date}` : ''}</span>
    </li>
  );
}

export interface ToolCardProps { step: ChatStep; open: boolean; onToggle: () => void; mode: AgentTransport }

/** One tool call: a header row (always shown) and its details (arguments, result, flags, sources). */
export function ToolCard({ step, open, onToggle, mode }: ToolCardProps) {
  const st = stepState(step);
  const live = mode === 'LIVE';
  const cost = live && step.cost_usd > 0 ? fmtCost(step.cost_usd) : null;
  const where = step.executed_in === 'browser' || isUiTool(step.tool) ? 'browser' : 'server';
  const id = `chs-${step.id}`;
  return (
    <li className={`ag-step ch-step ag-step--${st.tone}`} data-step={step.id} data-status={st.word} data-kind={step.kind} data-where={where} data-tool={step.tool ?? step.name}>
      <button type="button" className="ch-step-head" aria-expanded={open} aria-controls={id} onClick={onToggle}>
        <Icon name={open ? 'chevron-down' : 'chevron-right'} size="xs" />
        <span className="ag-step-n">{step.seq}</span>
        <b className="ag-step-t">{toolLabel(step.tool ?? step.name)}</b>
        {where === 'browser' && <Status state="meta" word="Browser" />}
        <Status state={st.kind} word={st.word} />
        <ModeChip mode={mode} className="ag-step-mode" />
        <span className="ag-step-num">{fmtDuration(step.duration_ms)}</span>
        {cost !== null && <span className="ag-step-num" data-cost>{cost}</span>}
      </button>
      {step.result_summary && <div className="ag-step-res ch-step-res">{humaniseAgentText(step.result_summary)}</div>}
      {open && (
        <div className="ch-step-body" id={id}>
          <div className="ch-kv"><span className="ch-k">Arguments</span><pre className="ch-pre ch-args">{prettyArgs(step.arguments) || argsSummary(step.arguments)}</pre></div>
          {(step.sources?.length ?? 0) > 0 && (
            <div className="ch-kv"><span className="ch-k">Sources found</span>
              <ul className="ch-src-lines">{step.sources!.map((s) => <SourceLine key={s.url} s={s} />)}</ul>
            </div>
          )}
          {(step.flags?.length ?? 0) > 0 && (
            <div className="ag-step-flags">{step.flags.map((f) => <Status key={f} state="snapshot" word={flagWord(f)} />)}</div>
          )}
        </div>
      )}
    </li>
  );
}

export function ToolCards({ steps, mode, label }: { steps: readonly ChatStep[]; mode: AgentTransport; label: string }) {
  const [openIds, setOpen] = useState<ReadonlySet<string>>(new Set());
  if (!steps.length) return null;
  const toggle = (id: string) => setOpen((cur) => { const n = new Set(cur); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  return (
    <ol className="ag-steps ch-steps" aria-label={label} data-count={steps.length}>
      {steps.map((s) => <ToolCard key={s.id} step={s} mode={s.transport ?? mode} open={openIds.has(s.id)} onToggle={() => toggle(s.id)} />)}
    </ol>
  );
}

/** The confirmation card for a state-changing action: nothing runs before Confirm. */
export function ConfirmCards({ actions, busy, onConfirm, onReject }: {
  actions: readonly AgentAction[]; busy: boolean; onConfirm: (id: string) => void; onReject: (id: string) => void;
}) {
  if (!actions.length) return null;
  return (
    <div className="ag-confirms ch-confirms" role="group" aria-label="Needs confirmation">
      {actions.map((a) => {
        const c = confirmCard(a);
        return (
          <div key={c.id} className="ag-confirm" data-action={c.id} data-tool={c.tool}>
            <div className="ag-confirm-top">
              <b>{c.title}</b>
              <Status state="snapshot" word="Needs confirmation" />
              <ModeChip mode={c.mode} />
              {c.estimate && <Status state="meta" word="Up to" meta={c.estimate} />}
            </div>
            <div className="ag-confirm-sum">{humaniseAgentText(c.summary)}</div>
            {c.args && <div className="ag-step-args">{c.args}</div>}
            <div className="ag-confirm-btns">
              <Button variant="primary" size="sm" disabled={busy} onClick={() => onConfirm(c.id)} className="ag-confirm-yes">Confirm</Button>
              <Button size="sm" disabled={busy} onClick={() => onReject(c.id)} className="ag-confirm-no">Reject</Button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** The numbered sources of one answer. */
export function SourcesList({ citations, active, recorded }: { citations: readonly Citation[]; active: number | null; recorded: boolean }) {
  return (
    <ol className="ch-sources" aria-label="Sources">
      {citations.map((c) => {
        const link = safeHref(c.url);
        const when = c.date ? c.date : `Retrieved ${c.retrieved_at}`;
        return (
          <li key={c.n} id={`ch-src-${c.n}`} value={c.n} className={`ch-source${active === c.n ? ' on' : ''}`} data-source={c.n}>
            <span className="ch-src-n">{c.n}</span>
            <span className="ch-src-main">
              {link
                ? <a className="ch-link" href={link.href} target="_blank" rel="noopener noreferrer">{c.title}</a>
                : <span>{c.title}</span>}
              <span className="ch-src-d">{c.domain} · {when}{recorded ? ' · Recorded' : ''}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
