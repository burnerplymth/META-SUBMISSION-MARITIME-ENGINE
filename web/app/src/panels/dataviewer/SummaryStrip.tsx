import React from 'react';
import { formatValue } from '../../charts/format';
import { formatTimeFull } from '../../charts/scale';
import { MetaChip } from '../reports/chips';
import type { ColumnSummary, Stat } from './summary';

/* SUMMARY strip: count, min, median, max per numeric column, computed
 * here from the rows loaded (and filtered). A per-row-unit column gets
 * one block per unit; nothing is added across units. */

function fmt(type: 'number' | 'time', v: number | null): string {
  if (v == null) return 'NOT SERVED';
  return type === 'time' ? formatTimeFull(v) : formatValue(v, null);
}

function Block({ type, unit, stat }: { type: 'number' | 'time'; unit: string | null; stat: Stat }) {
  return (
    <dl className="dv-sblock">
      {unit && <dt className="dv-sunit">{unit}</dt>}
      <div><dt>N</dt><dd>{stat.n.toLocaleString('en-US')}{stat.notServed > 0 && <span className="dv-sns"> +{stat.notServed} NOT SERVED</span>}</dd></div>
      <div><dt>Min</dt><dd>{fmt(type, stat.min)}</dd></div>
      <div><dt>Median</dt><dd>{fmt(type, stat.median)}</dd></div>
      <div><dt>Max</dt><dd>{fmt(type, stat.max)}</dd></div>
    </dl>
  );
}

export function SummaryStrip({ summaries, rowCount }: { summaries: ColumnSummary[]; rowCount: number }) {
  return (
    <section className="dv-summary" aria-label="Summary of loaded rows">
      <div className="dv-sbar">
        <MetaChip title="Computed in the browser from the rows loaded">Computed · loaded rows</MetaChip>
        <MetaChip>{rowCount.toLocaleString('en-US')} ROWS</MetaChip>
      </div>
      {summaries.length === 0 && <MetaChip>No numeric column</MetaChip>}
      <div className="dv-scards">
        {summaries.map((s) => (
          <article key={s.col} className="dv-scard">
            <h4 title={s.header}>{s.name}{s.notServed > 0 && <span className="dv-sns"> · {s.notServed.toLocaleString('en-US')} NOT SERVED</span>}</h4>
            {s.groups.length === 0 ? <MetaChip>NOT SERVED</MetaChip> : s.groups.map((g, i) => <Block key={i} type={s.type} unit={g.unit} stat={g.stat} />)}
          </article>
        ))}
      </div>
    </section>
  );
}
