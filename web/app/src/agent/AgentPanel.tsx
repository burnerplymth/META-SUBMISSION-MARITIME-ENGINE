/**
 * AGENT window (code AGT): Meta AI plans and runs a task with tools, in view.
 *
 *   header   mode chip (Live | Recorded), state label, caps as chips
 *   tasks    demo tasks from the server (RECORDED scripts), as chips
 *   input    typed task, Send, Stop
 *   confirm  one card per state-changing action: Confirm | Reject (confirmed automatically unless set to Ask first)
 *   answer   the final answer, labelled
 *   steps    the live timeline: tool, arguments, result, cost, duration, mode
 *
 * No explanatory prose: chips and state labels only. The user-visible name is
 * Meta AI; a RECORDED run is always labelled Recorded and is never shown as live.
 */
import React, { useEffect, useRef, useState } from 'react';
import { ModeMark } from '../meta/AiBadge';
import { humaniseAgentText } from '../format';
import { Button, Status, Toggle } from '../ui';
import { safeStorage } from '../store';
import { useInWindow, windowMeta } from '../windows/context';
import { MAX_TASK_CHARS } from './controller';
import { capChips, finalAnswer, isWorking } from './reducer';
import { getAgent, useAgent } from './singleton';
import { MODE_WORD, confirmCard, errorLabel, statusLabel, stepRow, type StepRow } from './timeline';
import type { AgentTransport } from './types';
import './agent.css';

function ModeChip({ mode, className = '' }: { mode: AgentTransport; className?: string }) {
  const m = mode === 'LIVE' ? 'live' : 'recorded';
  return (
    <span className={`ag-mode ag-mode--${m}${className ? ` ${className}` : ''}`} data-transport={mode} title={`Meta AI, ${MODE_WORD[mode].toLowerCase()}`}>
      <ModeMark mode={m} /> {MODE_WORD[mode]}
    </span>
  );
}

function StepItem({ row }: { row: StepRow }) {
  return (
    <li className={`ag-step ag-step--${row.state.tone}`} data-step={row.id} data-status={row.state.word} data-kind={row.kind} data-where={row.where}>
      <div className="ag-step-top">
        <span className="ag-step-n">{row.seq}</span>
        <b className="ag-step-t">{row.title}</b>
        {row.where === 'browser' && <Status state="meta" word="Browser" />}
        <Status state={row.state.kind} word={row.state.word} />
        <ModeChip mode={row.mode} className="ag-step-mode" />
        <span className="ag-step-num">{row.duration}</span>
        {row.cost !== null && <span className="ag-step-num" data-cost>{row.cost}</span>}
      </div>
      {row.args && <div className="ag-step-args">{row.args}</div>}
      {row.result && <div className="ag-step-res">{row.result}</div>}
      {row.flags.length > 0 && <div className="ag-step-flags">{row.flags.map((f) => <Status key={f} state="snapshot" word={f.replace(/_/g, ' ')} />)}</div>}
    </li>
  );
}

const AUTO_KEY = 'supermap.agent.auto';

export function AgentPanel() {
  const { state: s, meta } = useAgent();
  const agent = getAgent();
  const [draft, setDraft] = useState('');
  const win = useInWindow();
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => { if (!meta.loaded) void agent.loadMeta(); }, [agent, meta.loaded]);

  const working = isWorking(s);
  const label = statusLabel(s.status, s.stopReason);
  const answer = finalAnswer(s);
  const mode: AgentTransport | null = s.transport ?? (meta.status?.transport_label ?? null);
  const agentOff = meta.status?.enabled === false;
  const unserved = meta.loaded && meta.status === null;
  const caps = capChips(s);
  const rows = s.steps.map(stepRow);
  const cards = s.pending.map(confirmCard);
  // Auto-run (default on): an action the person asked for is confirmed on their behalf. Switch to Ask to confirm each one by hand.
  const [auto, setAuto] = useState<boolean>(() => safeStorage.get(AUTO_KEY) !== '0');
  const nextCard = cards.length > 0 ? cards[0].id : '';
  useEffect(() => { if (auto && !s.busy && nextCard) void agent.confirm(nextCard); }, [auto, s.busy, nextCard, agent]);

  useEffect(() => {
    if (!win) return;
    windowMeta.set('agent', { updated: label.word, code: 'AGT' });
  });
  useEffect(() => { if (!win) return; return () => windowMeta.set('agent', null); }, [win]);
  // While working the newest step stays in view; a finished task opens at its answer.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (working && cards.length === 0) el.scrollTop = el.scrollHeight;
    else if (!working) el.scrollTop = 0;
  }, [rows.length, rows[rows.length - 1]?.state.word, working, cards.length]);

  const canSend = !working && !s.busy && !agentOff && !unserved && draft.trim().length > 0;
  const send = () => {
    if (!canSend) return;
    const text = draft.trim();
    void agent.send(text, 'typed').then((started) => { if (started) setDraft(''); });
  };

  return (
    <section className="panel ag" data-panel="agent" data-agent-status={s.status} data-transport={mode ?? 'NONE'}>
      <div className="panel-body ag-body">
        <div className="ag-head" data-region="head">
          {mode && <ModeChip mode={mode} />}
          {agentOff ? <Status state="notserved" word="Meta AI off" />
            : unserved ? <Status state="notserved" word={errorLabel(meta.error ?? 'unreachable')} />
            : <Status state={label.kind} word={label.word} className="ag-state" />}
          {s.error && s.status !== 'failed' && <Status state="down" word={errorLabel(s.error)} className="ag-error" />}
          {caps.map((c) => <Status key={c.key} state={c.hot ? 'snapshot' : 'meta'} word={c.label} meta={c.text} className={`ag-cap ag-cap--${c.key}`} />)}
        </div>

        {meta.tasks.length > 0 && (
          <div className="ag-tasks" role="group" aria-label="Suggested tasks">
            <Status state={mode === 'LIVE' ? 'meta' : 'snapshot'} word={mode === 'LIVE' ? 'Examples' : 'Recorded'} className="ag-tasks-tag" />
            {meta.tasks.map((t) => (
              <Button key={t} size="sm" className="ag-task" title={t} disabled={working || s.busy || agentOff} onClick={() => setDraft(t)}>{t}</Button>
            ))}
          </div>
        )}

        <div className="ag-compose">
          <textarea className="ag-input" aria-label="Task" placeholder="Task" rows={2} maxLength={MAX_TASK_CHARS}
            value={draft} disabled={working || agentOff}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} />
          <div className="ag-actions">
            <Button variant="primary" onClick={send} disabled={!canSend} className="ag-send">Send</Button>
            {working && <Button variant="danger" icon="square" onClick={() => void agent.stop()} className="ag-stop">Stop</Button>}
          </div>
        </div>

        <div className="ag-flags" data-region="auto">
          <Toggle label="Actions" checked={auto} words={['Ask first', 'Run automatically']} className="ag-auto"
            onChange={(v) => { setAuto(v); safeStorage.set(AUTO_KEY, v ? '1' : '0'); }} />
        </div>

        <div className="ag-scroll" ref={scrollRef} data-region="results">
        {cards.length > 0 && (
          <div className="ag-confirms" role="group" aria-label="Needs confirmation">
            {cards.map((c) => (
              <div key={c.id} className="ag-confirm" data-action={c.id} data-tool={c.tool}>
                <div className="ag-confirm-top">
                  <b>{c.title}</b>
                  <Status state="snapshot" word="Needs confirmation" />
                  <ModeChip mode={c.mode} />
                  {c.estimate && <Status state="meta" word="Up to" meta={c.estimate} />}
                </div>
                <div className="ag-confirm-sum">{c.summary}</div>
                {c.args && <div className="ag-step-args">{c.args}</div>}
                <div className="ag-confirm-btns">
                  <Button variant="primary" size="sm" disabled={s.busy} onClick={() => void agent.confirm(c.id)} className="ag-confirm-yes">Confirm</Button>
                  <Button size="sm" disabled={s.busy} onClick={() => void agent.reject(c.id)} className="ag-confirm-no">Reject</Button>
                </div>
              </div>
            ))}
          </div>
        )}

        {answer && (
          <div className={`ag-answer${answer.system ? ' ag-answer--system' : ''}`} data-region="answer" data-transport={answer.transport ?? s.transport ?? 'NONE'}>
            <div className="ag-answer-top">
              <b>Answer</b>
              {(answer.transport ?? s.transport) && <ModeChip mode={(answer.transport ?? s.transport) as AgentTransport} />}
            </div>
            <div className="ag-answer-text">{humaniseAgentText(answer.text)}</div>
          </div>
        )}

        <ol className="ag-steps" aria-label="Steps" data-count={rows.length}>
          {rows.map((r) => <StepItem key={r.id} row={r} />)}
        </ol>
        </div>
      </div>
    </section>
  );
}
