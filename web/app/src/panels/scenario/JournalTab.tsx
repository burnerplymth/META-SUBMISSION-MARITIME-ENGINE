import React from 'react';
import { Chip } from '../../shell/Chip';
import { useScn, type Loaded } from './ctx';
import { plainLine, chainJournalRows, chainLineageRows, engineLabel, shortId, simJournalRows } from './logic';
import { CopyButton, Empty, Tag } from './ui';
import { labelWord } from '../../copy';

/** The loading / error / empty states of a fetched document. */
export function LoadGate<T>({ loaded, subject, children }: { loaded: Loaded<T>; subject: string | null; children: (doc: T) => React.ReactNode }) {
  if (!subject) return <Empty word="No run" />;
  if (loaded.status === 'loading' || (loaded.status === 'idle')) return <div className="empty-state"><Tag tone="busy" word="Loading" /></div>;
  if (loaded.status === 'error' || !loaded.doc) return <div className="scn-refusal mono" role="alert"><Tag tone="bad" word="Down" /><span className="scn-reason">{loaded.error ? plainLine(loaded.error) : 'Not served'}</span></div>;
  return <>{children(loaded.doc)}</>;
}

export function JournalTab() {
  const c = useScn();
  return (
    <div className="scn-tab" data-tab="JRNL">
      {c.loop === 'chain' ? (
        <LoadGate loaded={c.chainJournal} subject={c.chainId}>
          {(doc) => {
            const rows = chainJournalRows(doc, c.nameOf);
            const lineage = chainLineageRows(doc);
            return (
              <>
                <div className="scn-row scn-row--wrap mono">
                  <Chip kind="meta" word={doc.kind === 'fork' ? 'Fork' : 'Run'} />
                  <Chip kind="meta" word={engineLabel(doc.engine_rev)} />
                  <Chip kind="meta" word={shortId(doc.run_id)} />
                  <CopyButton text={doc.run_id} label="Copy ID" />
                  <button type="button" className="btn scn-btn" onClick={() => c.refreshJournal()}>Refresh</button>
                </div>
                <JournalTable rows={rows} />
                <div className="scn-subhead mono">Lineage</div>
                <table className="data-table scn-table">
                  <thead><tr><th>Run</th><th>Parent</th><th>Forked at</th><th>Engine</th></tr></thead>
                  <tbody>
                    {lineage.map((l) => (
                      <tr key={l.id} className={l.current ? 'row--sel' : undefined}>
                        <td className="mono">{shortId(l.id)}{l.current && <> <Chip kind="meta" word="Loaded" /></>}</td>
                        <td className="mono">{l.parent ? shortId(l.parent) : '—'}</td>
                        <td className="mono">{l.at}</td><td className="mono">{l.rev}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            );
          }}
        </LoadGate>
      ) : (
        <LoadGate loaded={c.simJournal} subject={c.simId}>
          {(doc) => {
            const rows = simJournalRows(doc, c.nameOf);
            return (
              <>
                <div className="scn-row scn-row--wrap mono">
                  <Chip kind="sim" />
                  <Chip kind="meta" word={shortId(doc.sim_run_id)} />
                  <CopyButton text={doc.sim_run_id} label="Copy ID" />
                  {doc.lineage?.parent_id
                    ? <Chip kind="meta" word="Fork of" meta={`${shortId(doc.lineage.parent_id)} · Tick ${doc.lineage.forked_at_tick ?? '—'}`} />
                    : <Chip kind="meta" word="Root" />}
                  {doc.replay_body && <CopyButton text={JSON.stringify(doc.replay_body)} label="Copy replay body" />}
                  <button type="button" className="btn scn-btn" onClick={() => c.refreshJournal()}>Refresh</button>
                </div>
                <JournalTable rows={rows} />
              </>
            );
          }}
        </LoadGate>
      )}
    </div>
  );
}

function JournalTable({ rows }: { rows: ReturnType<typeof chainJournalRows> }) {
  return (
    <table className="data-table scn-table">
      <thead><tr><th className="num">#</th><th>At</th><th>Kind</th><th>What was done</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.n}>
            <td className="num mono">{r.n}</td><td className="mono">{r.at}</td>
            <td><span className={`chip mono scn-kind scn-kind--${r.kind.toLowerCase().replace(/\s+/g, '-')}`}><b className="chip-w">{labelWord(r.kind)}</b></span></td>
            <td className="mono">{r.what}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
