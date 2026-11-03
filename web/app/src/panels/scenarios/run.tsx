import React from 'react';
import { Status } from '../../ui/Status';
import { Button, SegmentedControl } from '../../ui';
import { fmtD, plural } from './logic';
import { Section, type NameOf } from './sections';
import type { ChainRunDoc, RulesDoc, SimRunDoc } from './types';

/* The Run section: engine choice, the two run buttons, and the result. A
 * chain result is one exact run per shock plus a labelled combined table; a
 * simulation result is one run with every shock a window. */

export type RunState =
  | { kind: 'idle' }
  | { kind: 'running'; target: 'chain' | 'sim' }
  | { kind: 'error'; line: string }
  | { kind: 'chain'; doc: ChainRunDoc }
  | { kind: 'sim'; doc: SimRunDoc; status: 'building' | 'ready' | 'failed' };

export interface CmpRow { place_id: string; name: string | null; a: number | null; b: number | null; delta: number }

/** Per place maximum delay in two combined tables, largest change first. a = this scenario, b = the saved one. */
export function compareCombined(a: ChainRunDoc, b: ChainRunDoc, limit = 10): CmpRow[] {
  const ma = new Map(a.combined.places.map((p) => [p.place_id, p]));
  const mb = new Map(b.combined.places.map((p) => [p.place_id, p]));
  const rows: CmpRow[] = [];
  for (const id of new Set([...ma.keys(), ...mb.keys()])) {
    const x = ma.get(id), y = mb.get(id);
    rows.push({ place_id: id, name: x?.name ?? y?.name ?? null, a: x ? x.max_delay_d : null, b: y ? y.max_delay_d : null, delta: Math.round(((y?.max_delay_d ?? 0) - (x?.max_delay_d ?? 0)) * 10000) / 10000 });
  }
  rows.sort((p, q) => Math.abs(q.delta) - Math.abs(p.delta) || p.place_id.localeCompare(q.place_id));
  return rows.slice(0, limit);
}

const dash = '–';
const cell = (v: number | null): string => (v === null ? dash : fmtD(v));

export function RunSection(p: {
  state: RunState; rules: RulesDoc | null; nameOf: NameOf;
  can: { chain: boolean; sim: boolean }; why: string[]; busy: boolean;
  rev: 1 | 2; onRev: (r: 1 | 2) => void; revs: readonly number[];
  onRun: (t: 'chain' | 'sim') => void;
  onOpenRun: (runId: string) => void; onFocusPlace: (id: string) => void; onOpenSim: (simId: string) => void;
  handoffs: { monteCarlo: boolean; shipments: boolean }; onHandoff: (k: 'monte-carlo' | 'shipments') => void;
  cmp: { name: string; rows: CmpRow[] } | null; onCloseCmp: () => void;
}) {
  const s = p.state;
  const sem = p.rules?.semantics?.chain;
  return (
    <Section title="Run" id="run" aside={p.revs.includes(2) ? (
      <SegmentedControl label="Engine revision" size="sm" value={String(p.rev) as '1' | '2'} onChange={(v) => p.onRev(Number(v) as 1 | 2)}
        options={[{ value: '1', label: 'Rev 1' }, { value: '2', label: 'Rev 2' }]} />
    ) : undefined}>
      <div className="scb-row scb-runbar">
        <Button variant="primary" icon="play" disabled={!p.can.chain || p.busy} onClick={() => p.onRun('chain')} data-testid="scb-run-chain">Run chain</Button>
        <Button icon="play" disabled={!p.can.sim || p.busy} onClick={() => p.onRun('sim')} data-testid="scb-run-sim">Run simulation</Button>
        {p.handoffs.monteCarlo && <Button disabled={!p.can.chain} onClick={() => p.onHandoff('monte-carlo')} data-testid="scb-mc">Run Monte Carlo</Button>}
        {p.handoffs.shipments && <Button disabled={!p.can.chain} onClick={() => p.onHandoff('shipments')} data-testid="scb-ship">Evaluate shipments</Button>}
        {s.kind === 'running' && <Status state="meta" word="Running" />}
      </div>
      {p.why.length > 0 && <div className="scb-note" role="status">{p.why.join('. ')}</div>}
      {s.kind === 'error' && <div className="scb-err" role="alert" data-testid="scb-run-error">{s.line}</div>}

      {s.kind === 'chain' && (
        <div className="scb-result" data-testid="scb-result-chain">
          <div className="scb-resh">
            <b>{plural(s.doc.runs.length, 'run')}</b><Status state="model" /><span className="scb-chip2">Engine rev {s.doc.engine_rev}</span>
          </div>
          <ol className="scb-runs">
            {s.doc.runs.map((r) => (
              <li key={r.run_id} data-run={r.run_id}>
                <span className="scb-rn">{r.order}</span>
                <span className="scb-rp">{p.nameOf(r.place_id)}</span>
                <span className="scb-rk">{r.kind.replace('_', ' ')}</span>
                <span className="mono scb-rt">Day {r.start_offset_d} · {fmtD(r.duration_d)}</span>
                <span className="mono scb-rt">{r.event_count ?? dash} events</span>
                <span className="scb-inline">
                  <Button size="sm" onClick={() => p.onOpenRun(r.run_id)} data-testid={`scb-open-${r.shock_id}`}>Chain</Button>
                  <Button size="sm" variant="ghost" onClick={() => p.onFocusPlace(r.place_id)}>Map</Button>
                </span>
              </li>
            ))}
          </ol>
          <div className="scb-resh"><b>Combined delay by place</b><Status state="meta" word="Approximated" /><span className="mono scb-rt">{plural(s.doc.combined.places_reached, 'place')} reached</span></div>
          <table className="scb-table" data-testid="scb-combined">
            <thead><tr><th>Place</th><th className="num">Largest</th><th className="num">Sum</th><th className="num">Hit by</th></tr></thead>
            <tbody>
              {s.doc.combined.places.slice(0, 8).map((c) => (
                <tr key={c.place_id}><td>{c.name ?? p.nameOf(c.place_id)}</td><td className="num">{fmtD(c.max_delay_d)}</td><td className="num" data-overlap={c.overlapping_hit_by?.length ?? undefined}>{fmtD(c.sum_delay_d)}</td><td className="num">{c.hit_by.length}</td></tr>
              ))}
            </tbody>
          </table>
          <div className="scb-rt mono" data-testid="scb-sumrule">Sum counts overlapping shocks only</div>
          {sem && (
            <details className="scb-method"><summary>Method</summary>
              <p>{sem.unit}.</p><p>Exact: {sem.exact[0]}.</p><p>Approximated: {sem.approximated[0]}.</p>
            </details>
          )}
        </div>
      )}

      {s.kind === 'sim' && (
        <div className="scb-result" data-testid="scb-result-sim">
          <div className="scb-resh">
            <b>Simulation</b><Status state="sim" />
            <Status state={s.status === 'failed' ? 'down' : 'meta'} word={s.status === 'ready' ? 'Ready' : s.status === 'failed' ? 'Failed' : 'Building'} />
            <span className="mono scb-rt">{s.doc.sim_run_id}</span><span className="scb-spacer" />
            <Button size="sm" variant="primary" disabled={s.status !== 'ready'} onClick={() => p.onOpenSim(s.doc.sim_run_id)} data-testid="scb-open-sim">Open simulation</Button>
          </div>
          <ol className="scb-runs">
            {s.doc.windows.map((w) => (
              <li key={w.sim_id}><span className="scb-rn">{w.sim_id}</span><span className="scb-rp">{p.nameOf(w.place_id)}</span>
                <span className="mono scb-rt">{w.starts_at.slice(0, 10)} · {fmtD(w.duration_d)}</span><span className="mono scb-rt">Loss {Math.round(w.capacity_loss * 100)}%</span></li>
            ))}
          </ol>
          <div className="scb-note">{plural(s.doc.ticks, 'tick')} of 1 day, seed {s.doc.seed}, no weather feed</div>
        </div>
      )}

      {p.cmp && (
        <div className="scb-result" data-testid="scb-compare">
          <div className="scb-resh"><b>Compared with {p.cmp.name || 'saved scenario'}</b><Status state="meta" word="Approximated" /><span className="scb-spacer" />
            <Button size="sm" variant="ghost" onClick={p.onCloseCmp}>Close</Button></div>
          <table className="scb-table">
            <thead><tr><th>Place</th><th className="num">This</th><th className="num">Saved</th><th className="num">Change</th></tr></thead>
            <tbody>
              {p.cmp.rows.map((r) => (
                <tr key={r.place_id}><td>{r.name ?? p.nameOf(r.place_id)}</td><td className="num">{cell(r.a)}</td><td className="num">{cell(r.b)}</td>
                  <td className="num">{r.delta > 0 ? '+' : r.delta < 0 ? '−' : ''}{fmtD(Math.abs(r.delta))}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}
