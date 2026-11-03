import React, { useEffect, useRef, useState } from 'react';
import { useDeskStore, useSimStore } from '../../store';
import { nodeLabel } from '../../format';
import { Chip } from '../../shell/Chip';
import { backplay } from './backplay';
import { historyApi, type Fetched } from './client';
import { buildEventForkBody, countNoun, forkView, lineageChipMeta, lineageEntry, reasonLabel, type ForkView } from './logic';
import { ForkedFromChip } from './lineageChip';
import { ErrChip, NsChip, Rows } from './parts';
import type { ForkResponse } from './types';
import { Icon } from '../../ui/Icon';

type Step = { s: 'idle' } | { s: 'busy'; since: number } | { s: 'done'; runId: string } | { s: 'fail'; code: string };

/** The returned chain body and sim body as state labels, with the two
 *  create buttons kept SEPARATE: the chain run is instant, the sim run is
 *  asynchronous (BUILDING with elapsed time). Whatever runs from a fork is
 *  SIMULATED; its inputs are the recorded (SNAPSHOT) or inferred
 *  (MODELLED) event. */
export function ForkSection({ resp, win, onClose }: { resp: ForkResponse; win?: { from: string; to: string; places?: string[] } | null; onClose: () => void }) {
  const d = useDeskStore();
  const [rev, setRev] = useState<1 | 2>(1);
  const name = (id: string) => nodeLabel(id, d.graph);
  const view: ForkView = forkView(resp, rev, name);
  const [chainStep, setChainStep] = useState<Record<string, Step>>({});
  const setStep = (id: string, st: Step) => setChainStep((m) => ({ ...m, [id]: st }));
  const eventIds = view.eventIds;

  const createChain = async (eventId: string, place: string) => {
    setStep(eventId, { s: 'busy', since: Date.now() });
    let runId: string | null = null; let failure: string | null = null;
    if (rev === 1) {
      /* ENGINE REV 1: the fork route creates the run through the real POST
       * /run and logs the lineage server-side. */
      const body = buildEventForkBody([eventId], win ?? resp.forked_from.window ?? null, { chainCreate: true });
      if (!body.ok) { setStep(eventId, { s: 'fail', code: body.code }); return; }
      const r: Fetched<ForkResponse> = await historyApi.fork(body.body);
      if (r.kind === 'ok') {
        const c = r.body.created_chain_runs.find((x) => x.event_id === eventId);
        if (c?.created && c.run_id) runId = c.run_id; else failure = c?.code ?? c?.reason ?? 'not_created';
      } else failure = r.kind === 'error' ? r.code : r.reason;
    } else {
      /* ENGINE REV 2: the fork's own request body plus engine_rev, posted to
       * the real POST /run. */
      const cr = resp.chain_requests.find((x) => x.event_id === eventId);
      if (!cr) { setStep(eventId, { s: 'fail', code: 'unknown_event' }); return; }
      const r = await historyApi.runChain({ ...cr.request, engine_rev: 2 });
      if (r.kind === 'ok') runId = r.body.run_id; else failure = r.kind === 'error' ? r.code : r.reason;
    }
    if (!runId) { setStep(eventId, { s: 'fail', code: failure ?? 'not_created' }); return; }
    const ids = [...new Set([...(backplay.get().lineage.find((e) => e.lineage_id === resp.forked_from.lineage_id)?.chain_run_ids ?? []), runId])];
    backplay.recordFork(resp, ids);
    setStep(eventId, { s: 'done', runId });
    const ok = await d.openChain(runId);
    if (ok) d.selectNode(place);
  };

  const entry = lineageEntry(resp, [], 0);
  return (
    <section className="hx-card" data-fork={resp.forked_from.lineage_id} aria-label="Fork">
      <div className="hx-card-h">
        <ForkedFromChip meta={lineageChipMeta(entry, name)} lineageId={entry.lineage_id} />
        <Chip kind="meta" word={entry.lineage_id} />
        <span className="spacer" />
        <button type="button" className="hx-btn" onClick={onClose} title="Close" aria-label="Close fork"><Icon name="close" size="xs" /></button>
      </div>
      <div className="hx-chips">
        {resp.forked_from.events.map((e) => <Chip key={e.event_id} kind={String(e.mode).toUpperCase() === 'MODELED' ? 'model' : 'snapshot'} word={String(e.mode).toUpperCase() === 'MODELED' ? 'Modelled' : undefined} meta={e.node_id} />)}
        <Chip kind="sim" meta="Outputs" />
      </div>

      {view.chains.map((c) => {
        const st = chainStep[c.eventId] ?? { s: 'idle' as const };
        return (
          <div key={c.eventId} className="hx-card" data-chain={c.eventId}>
            <div className="hx-card-h">
              <span>Chain body</span><Chip kind="meta" word={name(c.place)} />
              <span className="spacer" />
              <span className="seg" role="group" aria-label="Engine revision">
                {([1, 2] as const).map((r) => (
                  <button key={r} type="button" className={`seg-b${rev === r ? ' on' : ''}`} aria-pressed={rev === r} onClick={() => setRev(r)}>Rev {r}</button>
                ))}
              </span>
            </div>
            <Rows rows={c.labels} />
            <div className="hx-state">
              <button type="button" className="hx-btn hx-btn--go" disabled={!c.ok || st.s === 'busy'} onClick={() => void createChain(c.eventId, c.place)}>Create chain run</button>
              {st.s === 'busy' && <Chip kind="meta" word="Creating" />}
              {st.s === 'done' && <><Chip kind="sim" /><Chip kind="meta" word="Run" meta={st.runId} /></>}
              {st.s === 'fail' && <ErrChip code={st.code} />}
              {!c.ok && <Chip kind="down" word="Invalid" />}
            </div>
          </div>
        );
      })}

      {view.sim && <SimBody view={view} resp={resp} eventIds={eventIds} />}
      {!view.sim && <div className="hx-state"><NsChip reason="no_sim_request" /></div>}
    </section>
  );
}

/** The sim body and its separate, asynchronous create button. The elapsed
 *  counter is local and starts at the click; the server's own run id shows
 *  as soon as the build is reported (simBuilding). */
function SimBody({ view, resp, eventIds }: { view: ForkView; resp: ForkResponse; eventIds: string[] }) {
  const sim = useSimStore();
  const d = useDeskStore();
  const [step, setStep] = useState<Step>({ s: 'idle' });
  const [now, setNow] = useState(() => Date.now());
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (step.s !== 'busy') return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [step.s]);
  const body = view.sim!.body;
  const create = async () => {
    const since = Date.now();
    setNow(since); setStep({ s: 'busy', since });
    backplay.recordFork(resp, backplay.get().lineage.find((e) => e.lineage_id === resp.forked_from.lineage_id)?.chain_run_ids ?? []);
    const err = await sim.createSimRun(body);
    if (!alive.current) return;
    if (err) setStep({ s: 'fail', code: (/^\[([^\]]+)\]/.exec(err)?.[1] ?? 'error') });
    else setStep({ s: 'done', runId: sim.simRun?.sim_run_id ?? '' });
  };
  const elapsed = step.s === 'busy' ? Math.max(0, Math.floor((now - step.since) / 1000)) : 0;
  return (
    <div className="hx-card hx-card--sim" data-sim-body>
      <div className="hx-card-h"><span>Simulation body</span><Chip kind="sim" /><span className="spacer" /><Chip kind="meta" word={countNoun(eventIds.length, 'event')} /></div>
      <Rows rows={view.sim!.labels} />
      {view.sim!.notes.length > 0 && (
        <details><summary>Notes {view.sim!.notes.length}</summary><ul>{view.sim!.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></details>
      )}
      <div className="hx-state">
        <button type="button" className="hx-btn hx-btn--sim" disabled={!view.sim!.ok || step.s === 'busy'} onClick={() => void create()}>Create simulation run</button>
        {step.s === 'busy' && (
          <>
            <Chip kind="sim" word="Building" />
            <b>{elapsed}s</b>
            {sim.simBuilding && <Chip kind="meta" word="Run" meta={sim.simBuilding.simRunId} />}
          </>
        )}
        {step.s === 'done' && <><Chip kind="sim" word="Opened" />{sim.simRun && <Chip kind="meta" word="Run" meta={sim.simRun.sim_run_id} />}</>}
        {step.s === 'fail' && <ErrChip code={step.code} />}
        {!view.sim!.ok && <Chip kind="down" word="Invalid" />}
      </div>
      {step.s === 'fail' && <div className="hx-state"><span>{reasonLabel(step.code)}</span></div>}
      {d.busy === 'sim' && step.s !== 'busy' && <Chip kind="meta" word="Simulation busy" />}
    </div>
  );
}
