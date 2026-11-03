/**
 * ENSEMBLE TREE — the precomputed mode's surface:
 * a per-level branching launcher that QUOTES before anything runs
 * (the quote persists nothing), a launch gate that cannot be
 * slipped past (over the default budget, the budget is raised only
 * by a control that names the figure; over the hard maximum, the
 * refusal renders from the served quote and no job is created),
 * the stored tree jobs, and the explorer: members grouped by level
 * with progressive disclosure, pruned members rendered as pruned
 * with their served reason, each member's subtree aggregates
 * through the shared blocks, and a member's full chain through the
 * tree's own replay route when the job serves one.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, pollBackoffMs } from '../api';
import { useDeskStore } from '../store';
import { fmtDayTime, humaniseProse, nodeLabel, prettyKind } from '../format';
import type {
  DisruptionSpec, EnsTreeJobSummary, EnsTreeMember, EnsTreeMemberDetail,
  EnsTreeQuoteResponse, EnsTreeView, EnsVariantData,
} from '../types';
import { combinationText, fmtInt, statusWord, variantChain } from './ensstats';
import { failureSentence } from './failureText';
import { BranchTables, OutcomeStatsBlock, cleanErr } from './enaggregate';
import {
  DEFAULT_BRANCHING_TEXT, ENX_COPY, axisLabel, branchingText, budgetLine, estimateLine,
  memberOutcomesLine, memberRefinement, memberState, memberStateWord,
  membersByLevel, parseBranching, quoteGate, quoteRows, refusalLines,
} from './enexplorer';
import { Icon } from '../ui/Icon';

const LEVEL_PAGE = 100;
const LEVEL_STEP = 500;

export function TreeSection({ spec, inert, onError }: {
  spec: DisruptionSpec;
  inert: boolean;
  onError: (msg: string) => void;
}) {
  const s = useDeskStore(); // desk-slice consumer
  const [branchingInput, setBranchingInput] = useState(DEFAULT_BRANCHING_TEXT);
  const [branchingError, setBranchingError] = useState<string | null>(null);
  const [quote, setQuote] = useState<EnsTreeQuoteResponse | null>(null);
  const [quoteBudget, setQuoteBudget] = useState<number | undefined>(undefined);
  const [raiseText, setRaiseText] = useState('');
  const [raiseError, setRaiseError] = useState<string | null>(null);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [jobs, setJobs] = useState<EnsTreeJobSummary[]>([]);
  const [job, setJob] = useState<EnsTreeJobSummary | null>(null);
  const [tree, setTree] = useState<EnsTreeView | null>(null);
  const [openLevels, setOpenLevels] = useState<Set<number>>(new Set([0]));
  const [shown, setShown] = useState<Record<number, number>>({});
  const [parentFilter, setParentFilter] = useState<number | null>(null);
  const [memberIdx, setMemberIdx] = useState<number | null>(null);
  const [memberDetail, setMemberDetail] = useState<EnsTreeMemberDetail | null>(null);
  const [chainNote, setChainNote] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const loadTree = useCallback(async (id: string) => {
    try {
      const t = await api.ensTreeView(id);
      setTree(t);
      setOpenLevels(new Set([0]));
      setShown({});
      setParentFilter(null);
      setMemberIdx(null);
      setMemberDetail(null);
      setChainNote(null);
    } catch (e) { onError(cleanErr(e)); }
  }, [onError]);

  const refreshList = useCallback(async () => {
    try {
      const { jobs: list } = await api.ensTreeList();
      setJobs(list);
    } catch { /* the list is a convenience; its absence asserts nothing */ }
  }, []);

  useEffect(() => { void refreshList(); }, [refreshList]);

  /* Poll the active tree job while the API says it is running; an
   * INTERRUPTED record stops the poll and renders its word. */
  /* backoff on consecutive failures (pollBackoffMs: 1200 ms
   *  base, doubling to a 30 s cap, reset on success). */
  useEffect(() => {
    if (!job || job.status !== 'running') return;
    let dead = false;
    let failures = 0;
    let timer: number | null = null;
    const poll = async () => {
      try {
        const fresh = await api.ensTreeJob(job.id);
        if (dead) return;
        failures = 0;
        setJob(fresh);
        if (fresh.status === 'complete') { void loadTree(fresh.id); void refreshList(); }
        if (fresh.status === 'failed' || fresh.status === 'interrupted') void refreshList();
      } catch (e) {
        if (dead) return;
        failures += 1;
        onError(cleanErr(e));
      }
      if (!dead) timer = window.setTimeout(() => void poll(), pollBackoffMs(failures));
    };
    timer = window.setTimeout(() => void poll(), pollBackoffMs(0));
    pollRef.current = timer;
    return () => { dead = true; if (timer) window.clearTimeout(timer); };
  }, [job, loadTree, refreshList, onError]);

  const doQuote = async (budget?: number) => {
    const parsed = parseBranching(branchingInput);
    if ('error' in parsed) { setBranchingError(parsed.error); return; }
    setBranchingError(null); setLaunchError(null); setBusy('quote');
    try {
      const q = await api.ensTreeQuote({ disruption: spec, branching: parsed.branching, budget });
      setQuote(q);
      setQuoteBudget(budget);
      setRaiseText(String(q.quoted_runs));
      setRaiseError(null);
    } catch (e) { onError(cleanErr(e)); } finally { setBusy(null); }
  };

  const doRaise = async () => {
    if (!quote) return;
    const t = raiseText.trim();
    const n = /^\d+$/.test(t) ? Number(t) : NaN;
    if (!Number.isInteger(n) || n < quote.quoted_runs) { setRaiseError(ENX_COPY.raiseInvalid); return; }
    setRaiseError(null);
    await doQuote(n);
  };

  const doLaunch = async () => {
    if (!quote) return;
    setBusy('launch'); setLaunchError(null);
    try {
      const summary = await api.ensTreeCreate({
        disruption: spec, branching: quote.branching, budget: quoteBudget,
      });
      setJob(summary);
      setTree(null); setMemberIdx(null); setMemberDetail(null);
      if (summary.status === 'complete') void loadTree(summary.id);
      void refreshList();
    } catch (e) {
      /* A refusal from create is the server's own text: it renders
       * verbatim (the per-level table is inside it) and no job was
       * persisted for it. */
      if (e instanceof ApiError && e.code === 'tree_too_large') setLaunchError('The tree is too large to launch.');
      else onError(cleanErr(e));
    } finally { setBusy(null); }
  };

  const openStoredJob = (j: EnsTreeJobSummary) => {
    setJob(j); setLaunchError(null); setChainNote(null);
    setTree(null); setMemberIdx(null); setMemberDetail(null);
    if (j.status === 'complete') void loadTree(j.id);
  };

  const selectMember = async (m: EnsTreeMember) => {
    if (!job) return;
    setMemberIdx(m.index); setMemberDetail(null); setChainNote(null); setBusy('member');
    try {
      const d = await api.ensTreeMember(job.id, m.index);
      setMemberDetail(d);
    } catch (e) { onError(cleanErr(e)); } finally { setBusy(null); }
  };

  const openMemberChain = async (m: EnsTreeMember) => {
    if (!job) return;
    setBusy('chain'); setChainNote(null);
    try {
      const data: EnsVariantData = await api.ensTreeVariant(job.id, m.index);
      const label = `${ENX_COPY.chainLabelPrefix} ${m.index} — ${nodeLabel(job.disruption.node_id, s.graph)}`;
      s.openVariantChain(variantChain(data), label);
      s.setFocus('chain');
    } catch (e) {
      /* The tree serves no chain for this member: say so plainly,
       * with the server's message, and leave the aggregates up. */
      setChainNote(`${ENX_COPY.chainNotServed} ${cleanErr(e)}`);
    } finally { setBusy(null); }
  };

  const gate = quote ? quoteGate(quote) : null;
  const running = job?.status === 'running';
  const stale = job !== null && job.disruption.node_id !== spec.node_id;
  const member: EnsTreeMember | null =
    tree && memberIdx !== null ? tree.nodes.find((n) => n.index === memberIdx) ?? null : null;
  const levelGroups = tree ? membersByLevel(
    parentFilter !== null ? tree.nodes.filter((n) => n.parent === parentFilter) : tree.nodes,
  ) : [];

  return (
    <>
      <h5>{ENX_COPY.treeHead}</h5>
      <details className="sim-method" data-methodology><summary className="mono">Methodology</summary><p className="fineprint">{ENX_COPY.treeIntro}</p></details>

      {/* ── Launcher: branching + quote before anything runs ── */}
      <div className="ens-controls">
        <label className="fineprint">{ENX_COPY.branchingLabel}
          <input className="enx-wide mono" value={branchingInput} disabled={inert}
            onChange={(e) => { setBranchingInput(e.target.value); setQuote(null); setQuoteBudget(undefined); }}
            aria-label={ENX_COPY.branchingLabel} />
        </label>
        <button className="btn btn--amber" onClick={() => void doQuote(quoteBudget)} disabled={inert || busy === 'quote'}>
          {busy === 'quote' ? ENX_COPY.quoting : ENX_COPY.quoteButton}
        </button>
      </div>
      <p className="fineprint">{ENX_COPY.branchingHint}</p>
      {branchingError && <p className="honest">{branchingError}</p>}

      {quote && (
        <div className="enx-quote">
          <h5>{ENX_COPY.quoteHead}</h5>
          <table className="data-table">
            <thead><tr><th>{ENX_COPY.levelWord}</th><th>Axis</th><th className="num">Branching</th><th className="num">Fanout</th><th className="num">Runs</th><th className="num">Cumulative</th></tr></thead>
            <tbody>{quoteRows(quote).map((r) => (
              <tr key={r.level}>
                <td className="mono">L{r.level}</td>
                <td>{r.axisLabel}</td>
                <td className="mono num">{r.branching}</td>
                <td className="mono num">{r.fanout}</td>
                <td className="mono num">{r.runs}</td>
                <td className="mono num">{r.cumulative}</td>
              </tr>
            ))}</tbody>
          </table>
          <p className="mono small">{quote.levels_table}</p>
          <p className="fineprint">{ENX_COPY.quotedTotal}: <b>{fmtInt(quote.quoted_runs)}</b> · {fmtInt(quote.total_runs_including_base)} {ENX_COPY.withBase} · {quote.arithmetic}</p>
          <p className="fineprint">{estimateLine(quote)}</p>
          <p className="fineprint">{budgetLine(quote)}</p>
          <p className="fineprint">{ENX_COPY.estimatorBasis}: {humaniseProse(quote.estimator.basis, s.graph)}</p>
          {quote.axes_notes.map((n, i) => <p key={i} className="fineprint">{humaniseProse(n, s.graph)}</p>)}

          {gate?.kind === 'ready' && (
            <>
              <p className="fineprint">{ENX_COPY.readyNote}</p>
              <button className="btn btn--amber" onClick={() => void doLaunch()} disabled={inert || busy === 'launch'}>
                {busy === 'launch' ? ENX_COPY.launching : ENX_COPY.launchButton}
              </button>
            </>
          )}
          {gate?.kind === 'raise' && (
            <div className="honest">
              <b>{ENX_COPY.raiseHead}.</b> {ENX_COPY.raiseNote}
              <div className="ens-controls">
                <label className="fineprint">{ENX_COPY.raiseLabel}
                  <input className="ens-count mono" value={raiseText} inputMode="numeric" disabled={inert}
                    onChange={(e) => setRaiseText(e.target.value)} aria-label={ENX_COPY.raiseLabel} />
                </label>
                <button className="btn" onClick={() => void doRaise()} disabled={inert || busy === 'quote'}>{ENX_COPY.raiseButton}</button>
              </div>
              {raiseError && <p className="honest">{raiseError}</p>}
            </div>
          )}
          {gate?.kind === 'refused' && (
            <div className="honest">
              <b>{ENX_COPY.refusedHead}.</b>
              {refusalLines(quote).map((line, i) => <p key={i}>{humaniseProse(line, s.graph)}</p>)}
            </div>
          )}
          {launchError && (
            <div className="honest">
              <b>{ENX_COPY.refusedHead}.</b>
              <p>{humaniseProse(launchError, s.graph)}</p>
            </div>
          )}
        </div>
      )}

      {/* ── Active job state ── */}
      {job && (
        <div className="ens-jobstate">
          <div className="mono small">
            {statusWord(job.status)} — {nodeLabel(job.disruption.node_id, s.graph)} · {prettyKind(job.disruption.kind)} ·{' '}
            branching {branchingText(job.branching)} · depth {job.depth}
            {running && <> · {fmtInt(job.progress.done)} of {fmtInt(job.progress.quoted)}</>}
          </div>
          {running && (
            <div className="ens-progress"><div style={{ width: `${job.progress.quoted ? (100 * job.progress.done) / job.progress.quoted : 0}%` }} /></div>
          )}
          {job.status === 'interrupted' && <p className="honest"><b>{statusWord(job.status)}.</b></p>}
          {job.status === 'failed' && job.error && <p className="honest"><b>{statusWord(job.status)}.</b> {failureSentence(job.error.code)}</p>}
          {stale && <p className="fineprint">{ENX_COPY.treeStaleNote}</p>}
        </div>
      )}

      {/* ── Explorer ── */}
      {tree && (
        <>
          <p className="fineprint">{ENX_COPY.accountingLine}: quoted {fmtInt(tree.accounting.quoted)} ({ENX_COPY.withBase}) ·{' '}
            run {fmtInt(tree.accounting.run)} · expanded {fmtInt(tree.accounting.expanded)} · pruned {fmtInt(tree.accounting.pruned)} ·{' '}
            final-level members {fmtInt(tree.accounting.depth_leaves)} · runs not taken under pruned members {fmtInt(tree.accounting.unexpanded_runs)}</p>
          <h5>{ENX_COPY.levelPlanHead}</h5>
          <table className="data-table">
            <tbody>{tree.axis_sequence.map((step) => (
              <tr key={step.level}>
                <td className="mono">L{step.level}</td>
                <td>{axisLabel(step.axis)}
                  <div className="cell-sub">{humaniseProse(step.description, s.graph)}</div></td>
                <td className="mono">{step.rule === 'interpolated' && step.span ? `across ${step.span[0]}–${step.span[1]}` : step.rule === 'levels' ? 'the declared levels' : 'held at one level'}</td>
                <td className="mono num">×{step.fanout}</td>
              </tr>
            ))}</tbody>
          </table>

          {parentFilter !== null && (
            <p className="fineprint">{ENX_COPY.filterActive}{' '}
              <button className="btn" onClick={() => setParentFilter(null)}>{ENX_COPY.filterShowAll}</button></p>
          )}
          {levelGroups.map(({ level, members }) => {
            const open = openLevels.has(level);
            const limit = shown[level] ?? LEVEL_PAGE;
            const axisStep = tree.axis_sequence[level - 1];
            return (
              <div key={level} className="enx-level">
                <button className="enx-levelhead mono"
                  onClick={() => setOpenLevels((prev) => { const next = new Set(prev); if (next.has(level)) next.delete(level); else next.add(level); return next; })}>
                  <Icon name={open ? 'chevron-down' : 'chevron-right'} size="xs" /> {ENX_COPY.levelWord} {level} — {level === 0 ? ENX_COPY.baseLevelName : (axisStep ? axisLabel(axisStep.axis) : '')} · {fmtInt(members.length)} {members.length === 1 ? ENX_COPY.memberWord : ENX_COPY.membersWord}
                </button>
                {open && (
                  <>
                    {members.slice(0, limit).map((m) => {
                      const state = memberState(m);
                      return (
                        <button key={m.index} className={`enx-member ${memberIdx === m.index ? 'enx-member--sel' : ''}`}
                          onClick={() => void selectMember(m)}>
                          <span className="enx-member-main">
                            <span>{memberRefinement(m)}</span>
                            <span className={`enx-state enx-state--${state}`}>{memberStateWord(state)}</span>
                          </span>
                          <span className="enx-member-sub">{memberOutcomesLine(m)}</span>
                          {m.pruned && m.prune_reason && (
                            <span className="enx-member-sub enx-pruned">{humaniseProse(m.prune_reason.message, s.graph)}</span>
                          )}
                        </button>
                      );
                    })}
                    {members.length > limit && (
                      <p className="fineprint">{ENX_COPY.showingOf} {fmtInt(limit)} of {fmtInt(members.length)} {ENX_COPY.membersWord}.{' '}
                        <button className="btn" onClick={() => setShown((prev) => ({ ...prev, [level]: limit + LEVEL_STEP }))}>{ENX_COPY.showMore}</button></p>
                    )}
                  </>
                )}
              </div>
            );
          })}

          {/* ── Selected member: its subtree aggregates ── */}
          {member && (
            <div className="enx-memberdetail">
              <h5>{ENX_COPY.memberHead} {member.index} — {memberRefinement(member)}</h5>
              <p className="mono small">{combinationText(member.combination)}</p>
              <p className="fineprint">{memberOutcomesLine(member)}</p>
              <div className="ens-controls">
                <button className="btn" onClick={() => void openMemberChain(member)} disabled={busy === 'chain'}>
                  {busy === 'chain' ? ENX_COPY.chainOpening : ENX_COPY.openChain}
                </button>
                {member.children.length > 0 && (
                  <button className="btn" onClick={() => { setParentFilter(member.index); setOpenLevels(new Set([member.level + 1])); }}>
                    {ENX_COPY.showChildren} ({fmtInt(member.children.length)})
                  </button>
                )}
              </div>
              {chainNote && <p className="honest">{chainNote}</p>}
              {busy === 'member' && <p className="fineprint">…</p>}
              {memberDetail && (
                <>
                  <p className="fineprint">{memberDetail.aggregates_basis === 'stored' ? ENX_COPY.basisStored : ENX_COPY.basisReplayed} ·{' '}
                    {fmtInt(memberDetail.variants_in_subtree)} variants {ENX_COPY.subtreeWord}.</p>
                  <p className="fineprint">{humaniseProse(memberDetail.subtree_note, s.graph)}</p>
                  <OutcomeStatsBlock outcomes={memberDetail.aggregates.outcomes} definitions={memberDetail.definitions} />
                  <BranchTables places={memberDetail.aggregates.places} legs={memberDetail.aggregates.legs} />
                </>
              )}
            </div>
          )}
        </>
      )}

      {/* ── Stored tree jobs ── */}
      <h5>{ENX_COPY.treeJobsHead}</h5>
      {jobs.length === 0 && <p className="dim">{ENX_COPY.noTreeJobs}</p>}
      <table className="data-table">
        <tbody>{jobs.map((j) => (
          <tr key={j.id} className={job?.id === j.id ? 'row--sel' : ''} onClick={() => openStoredJob(j)} title="Open tree job">
            <td>{nodeLabel(j.disruption.node_id, s.graph)}
              <div className="cell-sub">{prettyKind(j.disruption.kind)} · {fmtDayTime(j.created_at)}</div></td>
            <td className="mono">{branchingText(j.branching)}</td>
            <td className="mono">{statusWord(j.status)}</td>
          </tr>
        ))}</tbody>
      </table>
    </>
  );
}

export default TreeSection;
