import React from 'react';
import { useTabKeys } from '../../ui/Controls';
import { ChartFrame } from '../../charts/ChartFrame';
import type { ServerExport } from '../../charts/exportDom';
import type { ChartSpec, Hit } from '../../charts/types';
import { Chip, MetaChip } from '../reports/chips';
import type { Dataset } from './columns';
import {
  TOP_CHOICES, axisColumns, isGoodsClassColumn, labelColumns, numericColumns, viewTypes,
  type ChartView, type ViewType,
} from './chartSpec';

/* CHART VIEW: pick the type, x, y, series, split; defaults come from
 * the dataset. The chart plots the rows the grid shows (filtered and
 * sorted), or only the selected rows. */

const TYPE_LABEL: Record<ViewType, string> = { cascade: 'CHAIN', bar: 'BAR', line: 'LINE', scatter: 'SCATTER' };

function Pick({ id, label, value, options, onChange, none }: {
  id: string; label: string; value: number | null; options: Array<{ i: number; name: string }>; onChange: (v: number | null) => void; none?: string;
}) {
  return (
    <label className="dv-pick" htmlFor={id}>
      <span className="rk-lbl">{label}</span>
      <select id={id} className="rk-input" value={value ?? ''} onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}>
        {none !== undefined && <option value="">{none}</option>}
        {options.map((o) => <option key={o.i} value={o.i}>{o.name}</option>)}
      </select>
    </label>
  );
}

export function ChartPane({ ds, rows, view, onView, built, selectionOnly, onSelectionOnly, hasSelection, link, onHit, highlightPlaces, serverExport }: {
  ds: Dataset; rows: number[]; view: ChartView; onView: (v: ChartView) => void; built: { spec: ChartSpec | null; reason: string | null };
  selectionOnly: boolean; onSelectionOnly: (b: boolean) => void; hasSelection: boolean;
  link: () => string | null; onHit: (h: Hit) => void; highlightPlaces: readonly string[]; serverExport?: ServerExport | null;
}) {
  const types = viewTypes(ds);
  const tk = useTabKeys(types.length, types.indexOf(view.type), (i) => onView({ ...view, type: types[i] }));
  const nums = numericColumns(ds).filter((c) => !c.aux).map((c) => ({ i: c.index, name: c.name }));
  const xs = [...axisColumns(ds), ...labelColumns(ds).filter((c) => !c.aux)].map((c) => ({ i: c.index, name: c.name }));
  const cats = labelColumns(ds).filter((c) => !c.aux).map((c) => ({ i: c.index, name: c.name }));
  const stackable = view.type === 'bar' && view.series != null && !isGoodsClassColumn(ds.columns[view.series]);
  const set = (p: Partial<ChartView>) => onView({ ...view, ...p });

  if (types.length === 0) {
    return <div className="dv-pane"><Chip chip="NOT SERVED" word="NO NUMERIC COLUMN" /></div>;
  }
  return (
    <div className="dv-pane dv-chartpane">
      <div className="dv-ctl">
        <div className="rk-tabs" role="tablist" aria-label="Chart type">
          {types.map((t, i) => (
            <button key={t} type="button" role="tab" {...tk(i)} aria-selected={view.type === t} className={`rk-tab${view.type === t ? ' on' : ''}`} onClick={() => set({ type: t })}>{TYPE_LABEL[t]}</button>
          ))}
        </div>
        {view.type !== 'cascade' && (
          <>
            <Pick id="dv-x" label="X" value={view.x} options={view.type === 'bar' ? xs : axisColumns(ds).map((c) => ({ i: c.index, name: c.name }))} onChange={(v) => set({ x: v })} />
            <Pick id="dv-y" label="Y" value={view.y} options={nums} onChange={(v) => set({ y: v })} />
            <Pick id="dv-s" label="Series" value={view.series} options={cats} none="None" onChange={(v) => set({ series: v, stacked: v == null ? false : view.stacked })} />
            <Pick id="dv-p" label="Split" value={view.split} options={cats} none="None" onChange={(v) => set({ split: v })} />
            {view.type === 'bar' && (
              <>
                <label className="rk-check rk-row" title={stackable ? undefined : 'STACK NEEDS A NON-GOODS SERIES'}>
                  <input type="checkbox" checked={view.stacked && stackable} disabled={!stackable} onChange={(e) => set({ stacked: e.target.checked })} />
                  <span>Stack</span>
                </label>
                <label className="dv-pick" htmlFor="dv-top">
                  <span className="rk-lbl">Top</span>
                  <select id="dv-top" className="rk-input rk-input--num" value={view.top} onChange={(e) => set({ top: Number(e.target.value) })}>
                    {TOP_CHOICES.map((n) => <option key={n} value={n}>{n}</option>)}
                  </select>
                </label>
              </>
            )}
          </>
        )}
        <label className="rk-check rk-row">
          <input type="checkbox" checked={selectionOnly && hasSelection} disabled={!hasSelection} onChange={(e) => onSelectionOnly(e.target.checked)} />
          <span>Selected rows</span>
        </label>
        <MetaChip>{rows.length.toLocaleString('en-US')} ROWS</MetaChip>
      </div>
      {built.spec
        ? <ChartFrame key={built.spec.id} spec={built.spec} height={view.type === 'cascade' ? 300 : 260} link={link} onSelect={onHit} highlightPlaces={highlightPlaces} serverExport={serverExport} />
        : <div className="rk-state"><Chip chip="NOT SERVED" word={built.reason ?? 'NOTHING TO PLOT'} /></div>}
    </div>
  );
}
