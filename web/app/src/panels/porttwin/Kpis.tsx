import React from 'react';
import { InspectorSection } from '../../ui';
import { Tag, Tags } from './chips';
import {
  compareLines, directionCounts, fmtPct, fmtSigned, kpiSections, overallUtil, utilRows, DASH,
  type KpiLine, type VariantSet,
} from './kpi';
import { SIMULATED, baselineStateChip } from './labels';
import type { CompareDoc, SimGroup, SimSummary } from './types';

const cap = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);

function Range({ f }: { f: KpiLine['rangeFrac'] }) {
  if (!f) return null;
  return (
    <span className="ptw-rng" aria-hidden="true">
      <i style={{ left: `${f[0] * 100}%`, width: `${Math.max(1.5, (f[1] - f[0]) * 100)}%` }} />
      {f[2] !== null && <u style={{ left: `${f[2] * 100}%` }} />}
    </span>
  );
}

export function KpiRow({ l }: { l: KpiLine }) {
  return (
    <div className="ptw-kr" data-key={l.key}>
      <span className="ptw-kr-l">{l.label}</span>
      <span className="ptw-kr-v"><b>{l.value}</b>{l.unit && <small>{l.unit}</small>}</span>
      {l.range ? <span className="ptw-kr-r"><Range f={l.rangeFrac} /><em>{l.range}</em></span> : <span className="ptw-kr-r" />}
    </div>
  );
}

export interface KpiProps {
  summary: SimSummary;
  variants: VariantSet;
  groups: readonly SimGroup[];
  compare: CompareDoc | null;
  rangeDown: boolean;
}

export function KpiSection({ summary, variants, groups, compare, rangeDown }: KpiProps) {
  const k = kpiSections(summary, variants);
  const util = utilRows(summary, groups, compare);
  const all = overallUtil(util);
  const allBase = overallUtil(util, 'baseline');
  const hasRange = k.waiting.some((l) => l.range);
  return (
    <div className="ptw-kpis" data-section="kpis">
      <InspectorSection title="Waiting" note={hasRange ? 'Range low to high' : undefined} actions={<Tag {...SIMULATED} />}>
        <div className="ptw-kgrid">{k.waiting.map((l) => <KpiRow key={l.key} l={l} />)}</div>
        {k.censored ? <div className="ptw-kn"><Tag state="meta" word="Censored" meta={`${k.censored} still waiting`} /></div> : null}
        {rangeDown && <div className="ptw-kn"><Tag state="down" word="Down" meta="range" /></div>}
      </InspectorSection>
      <InspectorSection title="Berth utilisation" note={all === null ? undefined : `Overall ${fmtPct(all)}`} actions={<Tag {...SIMULATED} />}>
        <div className="ptw-ubars">
          {util.map((u) => (
            <div className="ptw-ub" key={u.id} title={u.id}>
              <span className="ptw-ub-n">{u.name}</span>
              <span className="ptw-ub-t" role="img" aria-label={`${u.name} ${fmtPct(u.util)}`}>
                <i style={{ width: `${Math.max(0, Math.min(1, u.util)) * 100}%` }} data-kind={u.kind} />
                {u.baseline !== null && <u style={{ left: `${Math.max(0, Math.min(1, u.baseline)) * 100}%` }} />}
              </span>
              <span className="ptw-ub-v">{fmtPct(u.util)}</span>
              {u.delta !== null && u.delta !== 0 && <span className="ptw-ub-d">{fmtSigned(u.delta * 100, 0)} pt</span>}
            </div>
          ))}
        </div>
        {allBase !== null && all !== null && (
          <div className="ptw-kn"><Tag state="meta" word="Baseline" meta={`overall ${fmtPct(allBase)}`} />{compare && <Tag {...baselineStateChip(compare.baseline_state)} />}</div>
        )}
      </InspectorSection>
      {k.yard && (
        <InspectorSection title="Yard" actions={<Tag {...SIMULATED} />}>
          <div className="ptw-kgrid">{k.yard.map((l) => <KpiRow key={l.key} l={l} />)}</div>
        </InspectorSection>
      )}
      <InspectorSection title="Throughput" note="Units kept apart" actions={<Tag {...SIMULATED} />}>
        <div className="ptw-kgrid">
          {k.throughput.length === 0 && <span className="ptw-kn">{DASH}</span>}
          {k.throughput.map((t) => (
            <div className="ptw-kr" key={t.label}><span className="ptw-kr-l">{cap(t.label)}</span><span className="ptw-kr-v"><b>{t.value}</b><small>{t.unit}</small></span><span className="ptw-kr-r" /></div>
          ))}
          {k.calls.map((t) => (
            <div className="ptw-kr" key={t.label}><span className="ptw-kr-l">{t.label}</span><span className="ptw-kr-v"><b>{t.value}</b><small>calls</small></span><span className="ptw-kr-r" /></div>
          ))}
        </div>
      </InspectorSection>
    </div>
  );
}

export function ComparePanel({ compare }: { compare: CompareDoc }) {
  const lines = compareLines(compare.table);
  const dc = directionCounts(lines);
  return (
    <InspectorSection title="Against baseline" note={compare.identical ? 'Identical' : `${dc.worse} worse · ${dc.better} better`} actions={<Tags chips={[baselineStateChip(compare.baseline_state), SIMULATED]} />}>
      <div className="ptw-cmp" data-tour="compare" role="table" aria-label="Baseline comparison">
        <div className="ptw-cr ptw-cr--h" role="row"><span>Measure</span><span>Baseline</span><span>Scenario</span><span>Change</span></div>
        {lines.map((l) => (
          <div className="ptw-cr" role="row" key={l.measure} data-dir={l.dir}>
            <span className="ptw-cr-l">{l.label}{l.unit && <small>{l.unit}</small>}</span>
            <span className="ptw-cr-n">{l.baseline}</span>
            <span className="ptw-cr-n">{l.scenario}</span>
            <span className={`ptw-cr-d ptw-dir--${l.dir}`}>{l.delta}{l.pct !== DASH && <em>{l.pct}</em>}</span>
          </div>
        ))}
      </div>
    </InspectorSection>
  );
}
