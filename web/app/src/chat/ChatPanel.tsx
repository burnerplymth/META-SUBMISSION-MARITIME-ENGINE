/**
 * CHAT window (id agent, code AGT): a multi-turn conversation with Meta AI.
 *
 *   rail      chats: search, rename, delete (a drawer when the window is narrow)
 *   header    title, mode chip (Live | Recorded), state label, spend, Export chat
 *   log       messages: tool cards (collapsible), answers with numbered
 *             citations, sources, Uncited flags, confirm cards, Stop
 *   composer  attached context chips, Send, web search toggle, actions (auto-run or ask first)
 *
 * No explanatory prose: chips and state labels only. The user-visible name is
 * Meta AI; a RECORDED chat is always labelled Recorded and is never shown as
 * live. Model and web text is rendered as text nodes only (markdown.ts).
 */
import { humaniseAgentText } from '../format';
import React, { useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Button, IconButton, Status, Toggle } from '../ui';
import { safeStorage } from '../store';
import { Ctx } from '../store';
import { useInWindow, windowMeta } from '../windows/context';
import { ConfirmCards, ModeChip, SourcesList, ToolCards } from './Cards';
import { ExportMenu } from './ExportMenu';
import { Markdown } from './Markdown';
import { Rail } from './Rail';
import { MAX_MESSAGE_CHARS } from './controller';
import { attachedFrom, chipKey, collectContext, pruneDismissed, storeContext } from './contextSources';
import { clockOf, errorWord, money, threadLabel, webLabel } from './labels';
import { liveSteps, working as isWorking } from './reducer';
import { getChat, useChat } from './singleton';
import type { ChatMessage, ContextChip, ExportFormat } from './types';
import './chat.css';

function ContextTag({ c, onRemove }: { c: ContextChip; onRemove?: () => void }) {
  const nums = Object.entries(c.summary).slice(0, 2).map(([k, v]) => `${k.replace(/_/g, ' ')} ${v}`).join(' · ');
  return (
    <span className="ch-ctx" data-kind={c.kind} data-ctx={chipKey(c)}>
      <span className="ch-ctx-k">{c.kind === 'montecarlo' ? 'Monte Carlo' : c.kind.charAt(0).toUpperCase() + c.kind.slice(1)}</span>
      <span className="ch-ctx-l">{c.label}</span>
      {nums && <span className="ch-ctx-n">{nums}</span>}
      {onRemove && (
        <button type="button" className="ch-ctx-x" aria-label={`Remove context: ${c.label}`} onClick={onRemove}>×</button>
      )}
    </span>
  );
}

function Message({ m, recorded, last, canRegen, onRegen, onExport }: {
  m: ChatMessage; recorded: boolean; last: boolean; canRegen: boolean; onRegen: () => void; onExport: (f: ExportFormat, id: string) => void;
}) {
  const [showSources, setShowSources] = useState(false);
  const [active, setActive] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);
  const cites = m.citations ?? [];
  const mode = m.transport ?? 'RECORDED';

  if (m.role === 'user') {
    return (
      <article className="ch-msg ch-msg--u" data-role="user" data-msg={m.id}>
        <div className="ch-msg-h"><b>You</b><span className="ch-time">{clockOf(m.ts)}</span></div>
        <div className="ch-text">{m.text}</div>
        {(m.context?.length ?? 0) > 0 && <div className="ch-ctxs">{m.context!.map((c) => <ContextTag key={chipKey(c)} c={c} />)}</div>}
      </article>
    );
  }
  if (m.system) {
    return <div className="ch-sys" data-role="system" data-msg={m.id}><Status state="notserved" word={m.text.replace(/\.$/, '')} /></div>;
  }
  const copy = () => {
    void navigator.clipboard?.writeText(m.text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }, () => {});
  };
  return (
    <article className="ch-msg ch-msg--a" data-role="assistant" data-region="answer" data-transport={mode} data-msg={m.id}>
      <div className="ch-msg-h">
        <b>Meta AI</b>
        <ModeChip mode={mode} />
        <span className="ch-time">{clockOf(m.ts)}</span>
        {mode === 'LIVE' && typeof m.cost_usd === 'number' && <span className="ag-step-num" data-cost>{money(m.cost_usd)}</span>}
        {m.web?.flagged && <Status state="snapshot" word="Instruction-like web text" />}
      </div>
      {(m.tool_steps?.length ?? 0) > 0 && <ToolCards steps={m.tool_steps!} mode={mode} label="Tool calls" />}
      <Markdown text={humaniseAgentText(m.text)} nCites={cites.length} uncited={m.uncited}
        onCite={(n) => { setShowSources(true); setActive(n); document.getElementById(`ch-src-${n}`)?.scrollIntoView?.({ block: 'nearest' }); }} />
      {cites.length > 0 && (
        <div className="ch-srcs">
          <Button size="sm" icon={showSources ? 'chevron-down' : 'chevron-right'} aria-expanded={showSources} onClick={() => setShowSources((o) => !o)} className="ch-srcs-btn">
            {`Sources ${cites.length}`}
          </Button>
          {showSources && <SourcesList citations={cites} active={active} recorded={recorded || mode === 'RECORDED'} />}
        </div>
      )}
      <div className="ch-msg-f">
        <IconButton icon="copy" label="Copy message" size="sm" onClick={copy} className="ch-copy" />
        {copied && <Status state="meta" word="Copied" />}
        <ExportMenu label="Export answer" onPick={(f) => onExport(f, m.id)} className="ch-export-answer" />
        {last && canRegen && <IconButton icon="refresh-cw" label="Regenerate" size="sm" onClick={onRegen} className="ch-regen" />}
      </div>
    </article>
  );
}

const AUTO_KEY = 'supermap.agent.auto';

export function ChatPanel() {
  const { state: s, meta } = useChat();
  const chat = getChat();
  const store = useContext(Ctx);
  const win = useInWindow();
  const [draft, setDraft] = useState('');
  const [railOpen, setRailOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const sectionRef = useRef<HTMLElement | null>(null);
  const composerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => { if (!meta.loaded) void chat.loadMeta(); }, [chat, meta.loaded]);

  // The narrow drawer stops above the composer (its height is published as a variable) and closes once a chat is selected or created.
  useEffect(() => {
    const sec = sectionRef.current, comp = composerRef.current;
    if (!sec || !comp) return;
    const set = () => sec.style.setProperty('--ch-comp-h', `${Math.ceil(comp.getBoundingClientRect().height)}px`);
    set();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(set);
    ro.observe(comp);
    return () => ro.disconnect();
  }, []);
  useEffect(() => { setRailOpen(false); }, [s.activeId]);
  useEffect(() => {
    if (!railOpen) return;
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') setRailOpen(false); };
    const down = (e: PointerEvent) => {
      const t = e.target as Element | null;
      if (t && !t.closest('.ch-rail-wrap') && !t.closest('.ch-rail-toggle')) setRailOpen(false);
    };
    document.addEventListener('keydown', key);
    document.addEventListener('pointerdown', down, true);
    return () => { document.removeEventListener('keydown', key); document.removeEventListener('pointerdown', down, true); };
  }, [railOpen]);

  const thread = s.thread;
  const working = isWorking(s);
  const label = threadLabel(thread, working);
  const mode = thread?.transport ?? meta.status?.transport_label ?? null;
  const agentOff = meta.status?.enabled === false;
  const unserved = meta.loaded && meta.status === null;
  const messages = thread?.messages ?? [];
  const live = liveSteps(s);
  const cards = thread?.pending ?? [];
  // Auto-run (default on): an action the person asked for is confirmed on their behalf. Switch to Ask first to confirm each one by hand.
  const [auto, setAuto] = useState<boolean>(() => safeStorage.get(AUTO_KEY) !== '0');
  const nextCard = cards.length > 0 ? cards[0].id : '';
  useEffect(() => { if (auto && !s.busy && nextCard) void chat.confirm(nextCard); }, [auto, s.busy, nextCard, chat]);
  const webOn = thread ? thread.web_search : (s.draftWeb ?? (meta.status?.transport_label === 'LIVE'));
  const webState = thread ? thread.web.state : webOn ? (mode === 'LIVE' ? 'on' : 'recorded') : 'off';
  const daily = thread?.web.daily ?? meta.status?.chat?.web_search ?? null;

  // context chips: the candidates now, minus the ones the person removed
  const selected = store?.selectedNode ?? null;
  const chain = store?.chain ?? null;
  const runLabel = store?.runMeta?.label;
  const graph = store?.graph as unknown as { nodes: ReadonlyArray<{ id?: unknown; name?: unknown }> } | null | undefined;
  const candidates = useMemo(
    () => collectContext(storeContext({ selectedNode: selected, graph: graph ?? null, chain, runMeta: runLabel ? { label: runLabel } : null })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selected, graph, chain?.run_id, chain?.event_count, runLabel],
  );
  useEffect(() => {
    const pruned = pruneDismissed(candidates, s.dismissed);
    if (pruned.length !== s.dismissed.length) chat.dismissContext(pruned);
  }, [candidates, s.dismissed, chat]);
  const attached = attachedFrom(candidates, s.dismissed);
  const removed = candidates.filter((c) => s.dismissed.includes(chipKey(c)));

  useEffect(() => {
    if (!win) return;
    windowMeta.set('agent', { updated: label.word, code: 'AGT' });
  });
  useEffect(() => { if (!win) return; return () => windowMeta.set('agent', null); }, [win]);
  // The newest message or step stays in view while a turn runs; an opened chat starts at its end.
  const tail = `${s.activeId}:${messages.length}:${live.length}:${cards.length}:${working}`;
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [tail]);

  const canSend = !working && !s.busy && !agentOff && !unserved && draft.trim().length > 0;
  const send = () => {
    if (!canSend) return;
    const text = draft.trim();
    void chat.send(text, { origin: 'typed', context: attached })
      .then((started) => { if (started) setDraft(''); });
  };
  const empty = messages.length === 0 && !working;
  const suggestions = meta.suggestions;
  const lastAssistantId = [...messages].reverse().find((m) => m.role === 'assistant' && !m.system)?.id;

  return (
    <section ref={sectionRef} className="panel ag ch" data-panel="agent" data-chat="1" data-agent-status={s.busy && thread && thread.status === 'idle' ? 'running' : (thread?.status ?? 'none')}
      data-transport={mode ?? 'NONE'} data-rail={railOpen ? 'open' : 'closed'}>
      <div className="panel-body ag-body ch-body">
        <div className="ch-rail-wrap">
          <Rail threads={s.threads} activeId={s.activeId} loaded={s.listLoaded} error={s.listError} busy={s.busy}
            onNew={() => { chat.newChat(); setDraft(''); setRailOpen(false); }}
            onOpen={(id) => { setRailOpen(false); void chat.open(id); }}
            onRename={(id, t) => void chat.rename(id, t)} onDelete={(id) => void chat.remove(id)} />
        </div>
        <div className="ch-main">
          <div className="ag-head ch-head" data-region="head">
            <IconButton icon="panel-left" label="Chats" pressed={railOpen} size="sm" onClick={() => setRailOpen((o) => !o)} className="ch-rail-toggle" />
            <b className="ch-title" title={thread?.title}>{thread?.title ?? 'New chat'}</b>
            {mode && <ModeChip mode={mode} />}
            {agentOff ? <Status state="notserved" word="Meta AI off" />
              : unserved ? <Status state="notserved" word={errorWord(meta.error ?? 'unreachable')} />
              : <Status state={label.kind} word={label.word} className="ag-state" />}
            {s.error && <Status state="down" word={errorWord(s.error)} className="ag-error" />}
            {thread && mode === 'LIVE' && (
              <Status state="meta" word="Spend" meta={`${money(thread.cost_usd)}/${money(thread.limits.max_spend_usd)}`} className="ag-cap ag-cap--spend" />
            )}
            <span className="ch-head-sp" />
            <ExportMenu label="Export chat" disabled={!thread || messages.length === 0} onPick={(f) => void chat.exportChat(f)} className="ch-export-chat" />
          </div>

          <div className="ag-scroll ch-scroll" ref={scrollRef} data-region="results">
            {empty ? (
              <div className="ch-empty">
                {suggestions.length > 0 && (
                  <div className="ch-suggest" role="group" aria-label="Suggestions">
                    {suggestions.map((t) => (
                      <Button key={t} size="sm" className="ch-sugg" disabled={agentOff} onClick={() => setDraft(t)}>{t}</Button>
                    ))}
                  </div>
                )}
                {meta.tasks.length > 0 && (
                  <div className="ag-tasks" role="group" aria-label="Suggested tasks">
                    <Status state={mode === 'LIVE' ? 'meta' : 'snapshot'} word={mode === 'LIVE' ? 'Examples' : 'Recorded'} className="ag-tasks-tag" />
                    {meta.tasks.map((t) => (
                      <Button key={t} size="sm" className="ag-task" title={t} disabled={working || s.busy || agentOff} onClick={() => setDraft(t)}>{t}</Button>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="ch-log" role="log" aria-live="polite" aria-relevant="additions" aria-label="Conversation">
                {messages.map((m) => (
                  <Message key={m.id} m={m} recorded={thread?.transport === 'RECORDED'} last={m.id === lastAssistantId}
                    canRegen={!working && !s.busy} onRegen={() => void chat.regenerate()} onExport={(f, id) => void chat.exportChat(f, id)} />
                ))}
                {working && (
                  <div className="ch-turn" data-region="working">
                    <ToolCards steps={live} mode={thread?.transport ?? 'RECORDED'} label="Steps" />
                    <ConfirmCards actions={cards} busy={s.busy} onConfirm={(id) => void chat.confirm(id)} onReject={(id) => void chat.reject(id)} />
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="ch-composer" data-wm-keep ref={composerRef}>
            {(attached.length > 0 || removed.length > 0) && (
              <div className="ch-ctxs ch-ctxs--c" role="group" aria-label="Attached context">
                {attached.map((c) => <ContextTag key={chipKey(c)} c={c} onRemove={() => chat.dismissContext([...s.dismissed, chipKey(c)])} />)}
                {removed.length > 0 && (
                  <Button size="sm" icon="plus" className="ch-attach" onClick={() => chat.dismissContext(s.dismissed.filter((k) => k !== chipKey(removed[0])))}>
                    {`Attach ${removed[0].label}`}
                  </Button>
                )}
              </div>
            )}
            <div className="ag-compose ch-compose">
              <textarea className="ag-input" aria-label="Message" placeholder="Message" rows={2} maxLength={MAX_MESSAGE_CHARS}
                value={draft} disabled={agentOff}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} />
              <div className="ag-actions">
                <Button variant="primary" onClick={send} disabled={!canSend} className="ag-send">Send</Button>
                {working && <Button variant="danger" icon="square" onClick={() => void chat.stop()} className="ag-stop">Stop</Button>}
              </div>
            </div>
            <div className="ag-flags ch-flags" data-region="voice">
              <Toggle label="Actions" checked={auto} words={['Ask first', 'Run automatically']} className="ch-auto"
                onChange={(v) => { setAuto(v); safeStorage.set(AUTO_KEY, v ? '1' : '0'); }} />
              <Toggle label="Web search" checked={webOn} onChange={(v) => void chat.setWeb(v)} words={['Off', 'On']} disabled={agentOff || working} className="ch-web" />
              {webOn && <Status state={webLabel(webState).kind} word={webLabel(webState).word} className="ch-web-state" />}
              {webOn && mode === 'LIVE' && daily && <Status state="meta" word="Web today" meta={`${daily.used_today}/${daily.cap}`} className="ch-web-day" />}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
