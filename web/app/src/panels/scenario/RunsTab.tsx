import React, { useMemo, useState } from 'react';
import { Chip } from '../../shell/Chip';
import { useScn } from './ctx';
import { catalogueRows, plainLine, shortId, type CatalogueRowView } from './logic';
import { Empty, Seg, Tag } from './ui';
import { labelWord } from '../../copy';

type KindFilter = 'all' | 'chain' | 'sim';
const SHOWN = 120;

/** Stored chain and sim runs (GET /runs). Pin and rename are not served by
 *  the API, so there is no control for them here. */
export function RunsTab() {
  const c = useScn();
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState<KindFilter>('all');
  const rows = useMemo(
    () => catalogueRows(c.catalogue.runs, { kind: kind === 'all' ? null : kind, query, nameOf: c.nameOf }),
    [c.catalogue.runs, kind, query, c.nameOf]);
  const activeIds = new Set([c.chainId, c.simId].filter(Boolean) as string[]);

  const open = async (r: CatalogueRowView, thenShock: boolean) => {
    if (r.kind === 'chain') { c.setLoop('chain'); await c.openChainRun(r.id); } else { c.setLoop('sim'); await c.openSimRunId(r.id); }
    if (thenShock) c.go('SHOCK');
  };
  const pick = (r: CatalogueRowView, side: 'a' | 'b') => {
    c.setLoop(r.kind);
    c.setCmp({ ...c.cmp, [side]: r.id });
  };

  return (
    <div className="scn-tab" data-tab="RUNS">
      <div className="scn-row scn-row--wrap">
        <input className="mono scn-filter" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter" aria-label="Filter runs" />
        <Seg<KindFilter> label="Run kind" value={kind} onChange={setKind}
          options={[{ v: 'all', label: 'All' }, { v: 'chain', label: 'Chain' }, { v: 'sim', label: 'Simulation', disabled: !c.simServed }]} />
        <button type="button" className="btn scn-btn" onClick={c.refreshCatalogue} disabled={c.catalogue.status === 'loading'}>Refresh</button>
        {c.catalogue.status === 'loading' && <Tag tone="busy" word="Loading" />}
        <Chip kind="meta" word={`${rows.length} runs`} />
      </div>
      {c.catalogue.status === 'error' && (
        <div className="scn-refusal mono" role="alert"><Tag tone="bad" word="Down" meta="Runs" /><span className="scn-reason">{plainLine(c.catalogue.error ?? '')}</span></div>
      )}
      {c.catalogue.runs === null && c.catalogue.status !== 'error' && <div className="empty-state"><Tag tone="busy" word="Loading" /></div>}
      {c.catalogue.runs !== null && rows.length === 0 && <Empty word="No runs" />}
      {rows.length > 0 && (
        <div className="scn-scroll">
          <table className="data-table scn-table">
            <thead><tr><th>Run</th><th>State</th><th>Actions</th></tr></thead>
            <tbody>
              {rows.slice(0, SHOWN).map((r) => {
                const active = activeIds.has(r.id);
                return (
                  <tr key={r.id} className={active ? 'row--sel' : undefined}>
                    <td>
                      <b>{r.title}</b>
                      <div className="mono scn-id">{`${labelWord(r.kind)} · ${shortId(r.id)}`}</div>
                      <div className="mono scn-id">{[r.created, r.facts].filter(Boolean).join(' · ')}</div>
                    </td>
                    <td className="scn-states">{active && <Tag tone="meta" word="Active" />}<Tag tone={r.state === 'COMPLETE' || r.state === 'READY' ? 'ok' : r.state === 'FAILED' ? 'bad' : 'busy'} word={r.state} /></td>
                    <td className="scn-actions">
                      <button type="button" className="btn scn-btn" onClick={() => void open(r, false)}>Open</button>
                      <button type="button" className="btn scn-btn" onClick={() => void open(r, true)}>Fork</button>
                      <button type="button" className={`btn scn-btn${c.cmp.a === r.id ? ' on' : ''}`} onClick={() => pick(r, 'a')} aria-pressed={c.cmp.a === r.id} aria-label="Compare as A">A</button>
                      <button type="button" className={`btn scn-btn${c.cmp.b === r.id ? ' on' : ''}`} onClick={() => pick(r, 'b')} aria-pressed={c.cmp.b === r.id} aria-label="Compare as B">B</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {rows.length > SHOWN && <div className="mono scn-more">{`Showing ${SHOWN} of ${rows.length}`}</div>}
        </div>
      )}
    </div>
  );
}
