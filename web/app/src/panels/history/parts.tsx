import React from 'react';
import { Chip } from '../../shell/Chip';
import type { StateKind } from '../../shell/stateChips';
import { provKind, provWord, reasonLabel, retrievedDay, type LabelRow } from './logic';
import type { HistProvenance } from './types';
import './history.css';

/** Provenance chip of a served historical record: SNAPSHOT (recorded
 *  fetch, with its retrieval day) or MODELLED (inferred). A record with
 *  no mode is a refusal and gets the NOT SERVED chip, not a blank. */
export function ProvChip({ prov, className }: { prov?: HistProvenance | null; className?: string }) {
  const k = provKind(prov);
  return <Chip kind={k} word={provWord(prov)} meta={k === 'notserved' ? undefined : retrievedDay(prov) || undefined}
    title={prov ? `${prov.source ?? ''} ${prov.retrieved_at ?? ''}`.trim() : undefined} className={className} />;
}

/** NOT SERVED with the stable reason code as the meta: the date range or
 *  series the layer could not answer, never filled in. */
export function NsChip({ reason, meta, className }: { reason?: string; meta?: string; className?: string }) {
  return <Chip kind="notserved" meta={meta ?? (reason ? reasonLabel(reason) : undefined)} title={reason} className={className} />;
}

export function Sec({ title, chips, children, id }: { title: string; chips?: React.ReactNode; children?: React.ReactNode; id?: string }) {
  return (
    <section className="hx-sec" data-sec={id}>
      <div className="hx-sec-h"><h5>{title}</h5>{chips}</div>
      {children}
    </section>
  );
}

/** Label rows: a key and its value, with a state chip where a row has one. */
export function Rows({ rows }: { rows: LabelRow[] }) {
  return (
    <dl className="hx-kv mono">
      {rows.map((r, i) => (
        <React.Fragment key={`${r.key}-${i}`}>
          <dt>{r.key}</dt>
          <dd>{r.kind ? <Chip kind={r.kind as StateKind} word={r.value} /> : r.value}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

/** A one-word failure state for a call that errored (not a NOT SERVED). */
export function ErrChip({ code }: { code: string }) {
  return <Chip kind="down" word={reasonLabel(code)} />;
}
