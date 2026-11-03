/**
 * THREAD RAIL: the list of chats with search, rename and delete. A delete asks
 * once in place (Delete | Keep); a rename is an inline field (Enter saves,
 * Escape cancels). Rows are buttons in a list; the open chat is aria-current.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Button, IconButton, Status } from '../ui';
import { MODE_WORD } from '../agent/timeline';
import { dayOf, errorWord } from './labels';
import { filterThreads } from './reducer';
import type { ThreadSummary } from './types';

export interface RailProps {
  threads: readonly ThreadSummary[];
  activeId: string | null;
  loaded: boolean;
  error: string | null;
  onNew: () => void;
  onOpen: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  busy: boolean;
}

function Row({ t, active, onOpen, onRename, onDelete }: {
  t: ThreadSummary; active: boolean; onOpen: () => void; onRename: (title: string) => void; onDelete: () => void;
}) {
  const [mode, setMode] = useState<'idle' | 'rename' | 'delete'>('idle');
  const [draft, setDraft] = useState(t.title);
  const field = useRef<HTMLInputElement | null>(null);
  useEffect(() => { if (mode === 'rename') { setDraft(t.title); field.current?.focus(); field.current?.select(); } }, [mode, t.title]);
  const save = () => { const v = draft.trim(); if (v && v !== t.title) onRename(v); setMode('idle'); };
  const working = t.status === 'running' || t.status === 'awaiting_confirmation' || t.status === 'awaiting_client';

  return (
    <li className={`ch-row${active ? ' on' : ''}`} data-thread={t.id} data-transport={t.transport}>
      {mode === 'rename' ? (
        <input ref={field} className="ch-rename" aria-label="Chat name" value={draft} maxLength={80}
          onChange={(e) => setDraft(e.target.value)} onBlur={save}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); save(); } else if (e.key === 'Escape') { e.preventDefault(); setMode('idle'); } }} />
      ) : (
        <button type="button" className="ch-row-main" aria-current={active ? 'true' : undefined} onClick={onOpen}>
          <span className="ch-row-t">{t.title}</span>
          <span className="ch-row-p">{t.preview || (t.messages ? '' : 'Empty')}</span>
          <span className="ch-row-m">
            <span className="ch-row-mode">{MODE_WORD[t.transport]}</span>
            <span>{dayOf(t.updated_at)}</span>
            {working && <Status state="live" word="Working" />}
          </span>
        </button>
      )}
      {mode === 'delete' ? (
        <span className="ch-row-ask" role="group" aria-label="Delete chat">
          <Button size="sm" variant="danger" onClick={() => { setMode('idle'); onDelete(); }} className="ch-row-del-yes">Delete</Button>
          <Button size="sm" onClick={() => setMode('idle')}>Keep</Button>
        </span>
      ) : mode === 'idle' ? (
        <span className="ch-row-acts">
          <IconButton icon="pencil" label="Rename chat" size="sm" onClick={() => setMode('rename')} className="ch-row-ren" />
          <IconButton icon="trash-2" label="Delete chat" size="sm" onClick={() => setMode('delete')} className="ch-row-del" />
        </span>
      ) : null}
    </li>
  );
}

export function Rail({ threads, activeId, loaded, error, onNew, onOpen, onRename, onDelete, busy }: RailProps) {
  const [q, setQ] = useState('');
  const rows = filterThreads(threads, q);
  return (
    <nav className="ch-rail" aria-label="Chats">
      <div className="ch-rail-top">
        <Button variant="primary" size="sm" icon="plus" onClick={onNew} disabled={busy} className="ch-new">New chat</Button>
      </div>
      <div className="ch-search">
        <input type="search" className="ch-search-in" aria-label="Search chats" placeholder="Search" value={q}
          onChange={(e) => setQ(e.target.value)} />
      </div>
      <ul className="ch-list" aria-label="Chat list">
        {rows.map((t) => (
          <Row key={t.id} t={t} active={t.id === activeId} onOpen={() => onOpen(t.id)}
            onRename={(title) => onRename(t.id, title)} onDelete={() => onDelete(t.id)} />
        ))}
      </ul>
      <div className="ch-rail-foot">
        {error ? <Status state="notserved" word={errorWord(error)} />
          : loaded && threads.length === 0 ? <Status state="meta" word="No chats" />
          : loaded && rows.length === 0 ? <Status state="meta" word="No match" /> : null}
      </div>
    </nav>
  );
}
