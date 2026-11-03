/**
 * ENSEMBLE EXPLORATIONS — the interactive-deepening surface:
 * saved explorations whose members are ordinary flat
 * jobs. Create / rename / reopen from the saved record (pointers
 * only — the detail payload is rendered as served; aggregates come
 * from each member job's own routes through the shared blocks),
 * members listed with siblings from the same parent together, the
 * breadcrumb path to the selected member, and the deepening form:
 * pick a variant of a member, sweep the next axis over supplied
 * values, and read the child member's own quote — its count note
 * verbatim — when it lands. A deepening the server refuses (a
 * closure's capacity axis, a path with no axis left) renders the
 * server's message as-is, with nothing appended.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, pollBackoffMs } from '../api';
import { useDeskStore } from '../store';
import { fmtDayTime, humaniseProse, nodeLabel, prettyKind } from '../format';
import type {
  EnsExpandQuote, EnsExplorationDetail, EnsExplorationEntry, EnsExplorationMember,
  EnsStatsJobSummary, EnsVariantData,
} from '../types';
import { AXIS_LABELS, fmtInt, statusWord, variantChain } from './ensstats';
import { JobAggregates, cleanErr } from './enaggregate';
import {
  ENX_COPY, axisLabel, crumbLine, memberRows, memberTitle, nextAxisLine,
  parseValues, valuesCount,
} from './enexplorer';

export function ExplorationsSection({ flatJobs, currentFlatJobId, inert, onError, openExplorationId, onExplorationOpened }: {
  flatJobs: EnsStatsJobSummary[];
  currentFlatJobId: string | null;
  inert: boolean;
  onError: (msg: string) => void;
  /** Search nav bridge: an exploration id handed down by
   *  the panel; opened below through the existing load path. */
  openExplorationId?: string | null;
  onExplorationOpened?: () => void;
}) {
  const s = useDeskStore(); // desk-slice consumer
  const [entries, setEntries] = useState<EnsExplorationEntry[]>([]);
  const [detail, setDetail] = useState<EnsExplorationDetail | null>(null);
  const [createName, setCreateName] = useState('');
  const [createRootId, setCreateRootId] = useState('');
  const [renameText, setRenameText] = useState('');
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [expandTarget, setExpandTarget] = useState<{ memberJobId: string; variantIndex: number } | null>(null);
  const [valuesText, setValuesText] = useState('');
  const [expandError, setExpandError] = useState<string | null>(null);
  const [lastQuote, setLastQuote] = useState<EnsExpandQuote | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const refreshEntries = useCallback(async () => {
    try {
      const { explorations } = await api.ensExplorations();
      setEntries(explorations);
    } catch { /* the list is a convenience; its absence asserts nothing */ }
  }, []);

  useEffect(() => { void refreshEntries(); }, [refreshEntries]);

  /* The root picker defaults to the panel's active flat job, else
   * the newest stored one. */
  useEffect(() => {
    if (createRootId) return;
    const preferred = currentFlatJobId ?? flatJobs[0]?.id ?? '';
    if (preferred) setCreateRootId(preferred);
  }, [createRootId, currentFlatJobId, flatJobs]);

  const loadDetail = useCallback(async (id: string, selectJobId?: string, quiet = false) => {
    try {
      const d = await api.ensExploration(id);
      setDetail(d);
      setRenameText(d.name);
      setSelectedJobId((prev) => selectJobId ?? (prev && d.nodes.some((n) => n.job_id === prev) ? prev : d.root_job_id));
      return d;
    } catch (e) { if (!quiet) onError(cleanErr(e)); return null; }
  }, [onError]);

  /* Search nav bridge: open the handed-down exploration
   * through loadDetail; an id that no longer exists is ignored
   * quietly (quiet load, no error line). */
  useEffect(() => {
    if (!openExplorationId) return;
    void loadDetail(openExplorationId, undefined, true).then(() => onExplorationOpened?.());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openExplorationId]);

  /* While any member runs, refresh the saved record — member states
   * and summaries move with the flat jobs underneath. */
  const anyRunning = detail?.nodes.some((n) => n.status === 'running') ?? false;
  /* backoff on consecutive failures (pollBackoffMs: 1200 ms
   *  base, doubling to a 30 s cap, reset on success). loadDetail
   *  reports failure by returning null rather than throwing. */
  useEffect(() => {
    if (!detail || !anyRunning) return;
    const id = detail.id;
    let dead = false;
    let failures = 0;
    let timer: number | null = null;
    const poll = async () => {
      const d = await loadDetail(id);
      if (dead) return;
      failures = d ? 0 : failures + 1;
      timer = window.setTimeout(() => void poll(), pollBackoffMs(failures));
    };
    timer = window.setTimeout(() => void poll(), pollBackoffMs(0));
    pollRef.current = timer;
    return () => { dead = true; if (timer) window.clearTimeout(timer); };
  }, [detail, anyRunning, loadDetail]);

  const doCreate = async () => {
    if (!createRootId) return;
    setBusy('create');
    try {
      const d = await api.ensExplorationCreate(createName.trim() ? createName.trim() : null, createRootId);
      setDetail(d);
      setRenameText(d.name);
      setSelectedJobId(d.root_job_id);
      setExpandTarget(null); setLastQuote(null); setExpandError(null);
      void refreshEntries();
    } catch (e) { onError(cleanErr(e)); } finally { setBusy(null); }
  };

  const doRename = async () => {
    if (!detail || !renameText.trim()) return;
    setBusy('rename');
    try {
      const d = await api.ensExplorationRename(detail.id, renameText.trim());
      setDetail(d);
      void refreshEntries();
    } catch (e) { onError(cleanErr(e)); } finally { setBusy(null); }
  };

  const openEntry = (entry: EnsExplorationEntry) => {
    setExpandTarget(null); setLastQuote(null); setExpandError(null);
    void loadDetail(entry.id);
  };

  const member: EnsExplorationMember | null =
    detail?.nodes.find((n) => n.job_id === selectedJobId) ?? detail?.nodes[0] ?? null;

  const openMemberVariant = useCallback((m: EnsExplorationMember) => (data: EnsVariantData, index: number) => {
    const place = m.summary?.disruption.node_id;
    const label = `ENSEMBLE VARIANT ${index} — ${place ? nodeLabel(place, s.graph) : ''}`;
    s.openVariantChain(variantChain(data), label);
    s.setFocus('chain');
  }, [s]);

  const doExpand = async () => {
    if (!detail || !member || !expandTarget || !member.next_axis) return;
    const parsed = parseValues(valuesText, member.next_axis);
    if ('error' in parsed) { setExpandError(parsed.error); return; }
    setExpandError(null); setBusy('expand');
    try {
      const out = await api.ensExplorationExpand(detail.id, expandTarget.memberJobId, expandTarget.variantIndex, parsed.values);
      setLastQuote(out.quote);
      setExpandTarget(null);
      await loadDetail(detail.id, out.child.id);
      void refreshEntries();
    } catch (e) {
      /* The server's refusal (a closure's capacity axis, no axis
       * left on the path, a bad value) renders as served — nothing
       * is appended to it. */
      setExpandError(cleanErr(e));
    } finally { setBusy(null); }
  };

  const expandMember: EnsExplorationMember | null =
    expandTarget && detail ? detail.nodes.find((n) => n.job_id === expandTarget.memberJobId) ?? null : null;
  const nextAxisDesc = expandMember?.summary?.axes.find((a) => a.axis === expandMember.next_axis)?.description;

  return (
    <>
      <h5>{ENX_COPY.expHead}</h5>
      <details className="sim-method" data-methodology><summary className="mono">Methodology</summary><p className="fineprint">{ENX_COPY.expIntro}</p></details>

      {/* ── Create ── */}
      <div className="ens-controls">
        <label className="fineprint">{ENX_COPY.expNameLabel}
          <input className="enx-wide mono" value={createName} disabled={inert}
            onChange={(e) => setCreateName(e.target.value)} aria-label={ENX_COPY.expNameLabel} />
        </label>
      </div>
      <div className="ens-controls">
        <label className="fineprint">{ENX_COPY.expRootLabel}
          <select className="enx-wide mono" value={createRootId} disabled={inert}
            onChange={(e) => setCreateRootId(e.target.value)} aria-label={ENX_COPY.expRootLabel}>
            {flatJobs.length === 0 && <option value="">—</option>}
            {flatJobs.map((j) => (
              <option key={j.id} value={j.id}>
                {nodeLabel(j.disruption.node_id, s.graph)} · {prettyKind(j.disruption.kind)} · {fmtDayTime(j.created_at)} · {statusWord(j.status)}
              </option>
            ))}
          </select>
        </label>
        <button className="btn btn--amber" onClick={() => void doCreate()} disabled={inert || busy === 'create' || !createRootId}>
          {busy === 'create' ? ENX_COPY.expCreating : ENX_COPY.expCreate}
        </button>
      </div>
      {flatJobs.length === 0 && <p className="fineprint">{ENX_COPY.expNoRoot}</p>}

      {/* ── Saved explorations ── */}
      <h5>{ENX_COPY.expListHead}</h5>
      {entries.length === 0 && <p className="dim">{ENX_COPY.expNone}</p>}
      <table className="data-table">
        <tbody>{entries.map((e) => (
          <tr key={e.id} className={detail?.id === e.id ? 'row--sel' : ''} onClick={() => openEntry(e)} title={ENX_COPY.expOpen}>
            <td>{e.name}
              <div className="cell-sub">{fmtInt(e.members)} {e.members === 1 ? ENX_COPY.memberWord : ENX_COPY.membersWord} · {fmtInt(e.expansions)} deepenings · {fmtDayTime(e.updated_at)}</div></td>
          </tr>
        ))}</tbody>
      </table>

      {/* ── Open exploration ── */}
      {detail && (
        <>
          <div className="ens-controls">
            <label className="fineprint">{ENX_COPY.expNameLabel}
              <input className="enx-wide mono" value={renameText} disabled={inert}
                onChange={(e) => setRenameText(e.target.value)} aria-label={ENX_COPY.expNameLabel} />
            </label>
            <button className="btn" onClick={() => void doRename()} disabled={inert || busy === 'rename' || !renameText.trim()}>
              {busy === 'rename' ? ENX_COPY.expRenaming : ENX_COPY.expRename}
            </button>
          </div>

          <h5>{ENX_COPY.expMembersHead}</h5>
          <div className="enx-members">
            {memberRows(detail).map(({ member: m, depth }) => (
              <button key={m.job_id} className={`enx-member ${member?.job_id === m.job_id ? 'enx-member--sel' : ''}`}
                style={{ marginLeft: depth * 14 }} onClick={() => { setSelectedJobId(m.job_id); setExpandTarget(null); }}>
                <span className="enx-member-main">
                  <span>{memberTitle(m)}</span>
                  <span className="enx-state">{m.status ? statusWord(m.status) : '—'}</span>
                </span>
                <span className="enx-member-sub">
                  {m.summary ? <>{nodeLabel(m.summary.disruption.node_id, s.graph)} · {fmtInt(m.summary.variants_planned)} variants</> : ENX_COPY.memberMissing}
                </span>
              </button>
            ))}
          </div>

          {member && (
            <>
              <p className="fineprint">{nextAxisLine(member)}</p>
              {member.varied_axes.length > 0 && (
                <p className="fineprint">{ENX_COPY.expVariedLine}: {member.varied_axes.map((a) => AXIS_LABELS[a] ?? a).join(' · ')}</p>
              )}
              <h5>{ENX_COPY.expCrumbHead}</h5>
              {member.breadcrumb.length === 0
                ? <p className="fineprint">{ENX_COPY.expCrumbRoot}</p>
                : member.breadcrumb.map((step, i) => (
                  <p key={i} className="fineprint">{i + 1}. {crumbLine(step)}</p>
                ))}
              {member.summary === null && <p className="honest">{ENX_COPY.memberMissing}</p>}
              {member.status === 'running' && <p className="fineprint">{ENX_COPY.memberRunning}</p>}
              {member.summary && member.status && (
                <JobAggregates
                  key={member.job_id}
                  jobId={member.job_id}
                  status={member.status}
                  onOpenVariant={openMemberVariant(member)}
                  onError={onError}
                  deepen={member.status === 'complete' && member.next_axis !== null
                    ? { disabled: inert, onDeepen: (index) => { setExpandTarget({ memberJobId: member.job_id, variantIndex: index }); setExpandError(null); setLastQuote(null); } }
                    : undefined}
                />
              )}

              {/* ── Deepening form ── */}
              {expandTarget && expandMember && expandMember.next_axis && (
                <div className="enx-expand">
                  <h5>{ENX_COPY.expandHead} — variant {expandTarget.variantIndex}</h5>
                  <p className="fineprint">{ENX_COPY.expandAxisLine}: <b>{axisLabel(expandMember.next_axis)}</b>
                    {nextAxisDesc ? <> — {humaniseProse(nextAxisDesc, s.graph)}</> : ''}</p>
                  <p className="fineprint">{ENX_COPY.expandHeldLine}.</p>
                  <div className="ens-controls">
                    <label className="fineprint">{ENX_COPY.expandValuesLabel}
                      <input className="enx-wide mono" value={valuesText} disabled={inert}
                        onChange={(e) => setValuesText(e.target.value)} aria-label={ENX_COPY.expandValuesLabel} />
                    </label>
                    <button className="btn btn--amber" onClick={() => void doExpand()} disabled={inert || busy === 'expand'}>
                      {busy === 'expand' ? ENX_COPY.expandRunning : ENX_COPY.expandButton}
                    </button>
                  </div>
                  <p className="fineprint">{ENX_COPY.expandValuesHint} {valuesCount(valuesText)} {ENX_COPY.expandQuoteValues}.</p>
                  {expandError && <p className="honest">{expandError}</p>}
                </div>
              )}
              {lastQuote && (
                <div className="honest">
                  <b>{ENX_COPY.expandQuoteHead}.</b>{' '}
                  {axisLabel(lastQuote.axis)} · {fmtInt(lastQuote.values_count)} {ENX_COPY.expandQuoteValues} ·{' '}
                  {fmtInt(lastQuote.grid_size)} {ENX_COPY.expandQuoteGrid}.
                  {lastQuote.count_note && <> {humaniseProse(lastQuote.count_note, s.graph)}</>}
                  <p>{ENX_COPY.expandDone}</p>
                </div>
              )}
              {expandError && !expandTarget && <p className="honest">{expandError}</p>}
            </>
          )}
        </>
      )}
    </>
  );
}

export default ExplorationsSection;
