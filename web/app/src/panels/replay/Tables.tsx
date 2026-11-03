import React, { useMemo, useState } from 'react';
import { Button, SegmentedControl } from '../../ui';
import { Chip, MetaChip, StateLine } from '../reports/chips';
import { SourceLink } from './Columns';
import { BASIS_LABEL, STATUS_LABEL, fmtDate, safeText, signed, type Counts } from './logic';
import type { Basis, Row, Status } from './types';

const COUNT_ORDER: Status[] = ['matched', 'not_flagged', 'not_observed', 'opposite', 'not_produced', 'outside', 'within', 'quiet', 'not_compared', 'ungraded'];

/** Counts per relation, never a total or a rate; always beside the Descriptive chip. */
export function CountChips({ counts }: { counts: Counts }) {
  return (
    <span className="rp-counts" data-testid="rp-counts">
      <Chip chip="INFO" word="Descriptive" />
      {COUNT_ORDER.filter((s) => counts[s] > 0).map((s) => <MetaChip key={s}>{`${STATUS_LABEL[s]} ${counts[s]}`}</MetaChip>)}
    </span>
  );
}

function Relation({ status }: { status: Status }) {
  return <span className={`rp-st rp-st--${status}`} data-status={status}>{STATUS_LABEL[status]}</span>;
}

const diffText = (r: Row): string => (r.difference ? `${signed(r.difference.value)} ${r.difference.unit}` : '');

function RowsTable({ rows, label, testId }: { rows: readonly Row[]; label: string; testId: string }) {
  return (
    <div className="rp-tablewrap">
      <table className="rp-table rp-table--rows" aria-label={label} data-testid={testId}>
        <thead>
          <tr>
            <th scope="col">Subject</th><th scope="col">Basis</th><th scope="col">Observed</th><th scope="col">Model</th>
            <th scope="col" className="num">Difference</th><th scope="col" className="num">Timing d</th><th scope="col">Magnitude</th><th scope="col">Relation</th><th scope="col">Source</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.basis}:${r.key}`} data-status={r.status}>
              <th scope="row">{safeText(r.subject)}</th>
              <td>{BASIS_LABEL[r.basis]}</td>
              <td>{safeText(r.observed)}</td>
              <td>{safeText(r.model)}</td>
              <td className="num">{diffText(r)}</td>
              <td className="num">{r.timingD == null ? '' : signed(r.timingD)}</td>
              <td>{r.magnitude ?? ''}</td>
              <td><Relation status={r.status} /></td>
              <td className="rp-src">{r.publisher ? <span>{safeText(r.publisher).slice(0, 48)}</span> : null}{r.url ? <SourceLink url={r.url} publisher={safeText(r.publisher)} /> : null}{r.observedDate && !r.publisher ? <span className="mono">{fmtDate(r.observedDate)}</span> : null}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const LIMIT = 12;

/** The published misses: every place or effect where the model and the record differ. Open by default, never folded away. */
export function MissesSection({ misses, counts, ready }: { misses: readonly Row[]; counts: Counts; ready: boolean }) {
  const [all, setAll] = useState(false);
  const shown = all ? misses : misses.slice(0, LIMIT);
  return (
    <section className="rp-sec rp-sec--misses" aria-label="Published misses" data-testid="rp-misses">
      <div className="rp-sech">
        <h3 className="rp-st-h">Published misses</h3>
        <CountChips counts={counts} />
      </div>
      {!ready ? <StateLine chip="NO DATA" word="No comparison" /> : misses.length === 0 ? <StateLine chip="INFO" word="No misses listed" /> : (
        <>
          <RowsTable rows={shown} label="Published misses" testId="rp-misses-table" />
          {misses.length > LIMIT && (
            <Button variant="ghost" size="sm" onClick={() => setAll((v) => !v)} aria-expanded={all}>{all ? 'Show fewer rows' : `Show ${misses.length - LIMIT} more rows`}</Button>
          )}
        </>
      )}
    </section>
  );
}

/** Every compared pair, with a basis filter. */
export function ComparisonSection({ rows }: { rows: readonly Row[] }) {
  const [basis, setBasis] = useState<'all' | Basis>('all');
  const present = useMemo(() => (['portwatch', 'dossier', 'simulation', 'reported'] as Basis[]).filter((b) => rows.some((r) => r.basis === b)), [rows]);
  const shown = basis === 'all' ? rows : rows.filter((r) => r.basis === basis);
  return (
    <section className="rp-sec" aria-label="Comparison" data-testid="rp-comparison">
      <div className="rp-sech">
        <h3 className="rp-st-h">Comparison</h3>
        <MetaChip>{`${shown.length} rows`}</MetaChip>
        <span className="rp-spacer" />
        <SegmentedControl label="Basis" size="sm" value={basis} onChange={setBasis}
          options={[{ value: 'all' as const, label: 'All' }, ...present.map((b) => ({ value: b, label: BASIS_LABEL[b] }))]} />
      </div>
      {shown.length === 0 ? <StateLine chip="NO DATA" word="No rows" /> : <RowsTable rows={shown} label="Comparison" testId="rp-comparison-table" />}
    </section>
  );
}
