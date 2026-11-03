/**
 * META AI ACTIVITY: the `aiactivity` window, code AIA, command bar
 * `> meta ai`. Every model call this page made, newest first, from the
 * in-memory session log (aiCalls.ts). Table of facts only: no prose.
 *
 *   - mode comes from GET /llm/status (Recorded | Live | Unavailable), the
 *     same read as the status-bar indicator;
 *   - a row's mode is derived from its own provenance, so a recorded
 *     fixture is never worded or marked as live;
 *   - an empty log is one quiet word, never a made-up call; an unreachable
 *     status is `Unavailable` in the not-served form, never a fake zero.
 */
import React, { useEffect } from 'react';
import { Status } from '../ui/Status';
import { useInWindow, windowMeta } from '../windows/context';
import { SURFACE_WORD, clockUtc, sessionStats, useAiCalls, type AiCall } from './aiCalls';
import { useAiStatus } from './aiStatus';
import { ModeMark } from './AiBadge';
import { NA, fmtUsd } from './provenance';

const costWord = (c: AiCall): string => (c.cost === null ? NA : fmtUsd(c.cost));

export function AiCallTable({ calls }: { calls: readonly AiCall[] }) {
  return (
    <table className="ai-table" data-rows={calls.length}>
      <thead>
        <tr>
          <th scope="col">Time</th><th scope="col">Surface</th><th scope="col">Did</th><th scope="col">Mode</th>
          <th scope="col">Model</th><th scope="col">Prompt id</th><th scope="col" className="ai-num">Cost</th><th scope="col">Cached</th>
        </tr>
      </thead>
      <tbody>
        {calls.map((c) => (
          <tr key={c.id} data-call-mode={c.mode.toLowerCase()}>
            <td className="ai-num">{clockUtc(c.at)}</td>
            <td>{SURFACE_WORD[c.surface]}</td>
            <td className="ai-did" title={c.did}>{c.did}</td>
            <td><ModeMark mode={c.mode === 'Live' ? 'live' : 'recorded'} /> {c.mode}</td>
            <td className="ai-model" title={c.model ?? undefined}>{c.model ?? NA}</td>
            <td>{c.promptId ?? NA}</td>
            <td className="ai-num">{costWord(c)}</td>
            <td>{c.cached === null ? NA : c.cached ? 'Yes' : 'No'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function AiActivityPanel() {
  const calls = useAiCalls();
  const { doc, view } = useAiStatus();
  const win = useInWindow();
  const stats = sessionStats(calls);
  const stamp = view?.kind === 'model' ? `${view.word} · this session` : view?.kind === 'na' ? 'Unavailable' : undefined;
  useEffect(() => {
    if (!win) return;
    windowMeta.set('aiactivity', { updated: stamp, code: 'AIA' });
  });
  useEffect(() => { if (!win) return; return () => windowMeta.set('aiactivity', null); }, [win]);
  const spent = doc && typeof doc.spent_usd === 'number' && Number.isFinite(doc.spent_usd)
    ? (typeof doc.cap_usd === 'number' ? `${fmtUsd(doc.spent_usd)} of ${fmtUsd(doc.cap_usd)}` : `${fmtUsd(doc.spent_usd)} · No cap`) : NA;
  return (
    <section className="panel ai-activity" data-panel="aiactivity">
      <div className="panel-body">
        <div className="ai-sum">
          {view?.kind === 'na'
            ? <Status state="notserved" word="Unavailable" meta="llm/status" />
            : view ? <span className={`ai-sum-mode ai-sum-mode--${view.tone}`}><ModeMark mode={view.tone} /> {view.word}</span> : null}
          <dl className="metachip-rows ai-sum-rows">
            <dt>Calls this session</dt><dd>{stats.total}</dd>
            <dt>Live</dt><dd>{stats.live}</dd>
            <dt>Recorded</dt><dd>{stats.recorded}</dd>
            <dt>Spend</dt><dd>{spent}</dd>
            {doc && typeof doc.calls_today === 'number' && <><dt>Calls today</dt><dd>{doc.calls_today}</dd></>}
            {doc && typeof doc.sam_calls_today === 'number' && <><dt>SAM calls today</dt><dd>{doc.sam_calls_today}</dd></>}
          </dl>
        </div>
        {calls.length === 0
          ? <div className="empty-state"><Status state="meta" word="No calls yet" /></div>
          : <AiCallTable calls={calls} />}
      </div>
    </section>
  );
}
