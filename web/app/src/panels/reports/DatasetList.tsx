import React from 'react';
import { SparkView } from '../../charts/SparkView';
import { formatBytes } from '../../charts/format';
import { Chip, MetaChip } from './chips';
import { stampShort, type PreviewInfo } from './exportPlan';
import { hasData, rowsLabel, type ExportDataset } from './model';
import type { RouteState } from '../dataviewer/dataClient';
import { Icon } from '../../ui/Icon';

/* The dataset catalogue as a list: state, rows, size, updated, the
 * provenance chip, a mini-preview, and one-click OPEN, CHART and
 * downloads. A size is a measured sample or an ESTIMATE; an update stamp
 * is shown only when the server serves one. */

export interface DatasetActions {
  open: (d: ExportDataset, tab: 'grid' | 'chart') => void;
  csv: (d: ExportDataset) => void;
  json: (d: ExportDataset) => void;
  xlsx: (d: ExportDataset) => void;
}

function ProvChip({ p }: { p?: string }) {
  if (!p) return <Chip chip="NOT SERVED" />;
  return /^mixed/i.test(p) ? <Chip chip="MIXED" word="Per cell" /> : <Chip chip={p.toUpperCase()} />;
}

export function DatasetList({ items, previews, rowsCap, xlsx, busy, actions, pick, picked, onPick, showUpdated = false }: {
  items: ExportDataset[]; previews: Record<string, PreviewInfo | undefined>; rowsCap: number; xlsx: RouteState;
  busy: string | null; actions: DatasetActions; showUpdated?: boolean;
  /** checkbox mode (data pack) */
  pick?: boolean; picked?: readonly string[]; onPick?: (id: string) => void;
}) {
  if (items.length === 0) return <div className="rk-state"><Chip chip="NOT SERVED" word="No match" /></div>;
  return (
    <ul className="xc-list" aria-label="Datasets">
      {items.map((d) => {
        const ok = hasData(d);
        const pv = previews[d.id];
        const rows = d.rows == null ? null : Math.min(d.rows, rowsCap);
        const est = ok && pv?.bytesPerRow != null && rows != null ? Math.round(rows * pv.bytesPerRow) : null;
        const sized = d.size_bytes != null ? formatBytes(d.size_bytes) : est != null ? `≈ ${formatBytes(est)}` : null;
        const isBusy = busy?.startsWith(`${d.id}:`) ?? false;
        return (
          <li key={d.id} className={`xc-row${ok ? '' : ' off'}${pick ? ' xc-row--pick' : ''}${showUpdated ? '' : ' xc-noupd'}`} data-dataset={d.id}>
            {pick && (
              <label className="xc-pick">
                <input type="checkbox" checked={!!picked?.includes(d.id)} disabled={!ok} onChange={() => onPick?.(d.id)} aria-label={`Include ${d.label}`} />
              </label>
            )}
            <div className="xc-name" title={d.description}>
              <span className="xc-title">{d.label}</span>
              <span className="mono rk-code">{d.id}</span>
            </div>
            <div className="xc-state">{ok ? <Chip chip="HAS DATA" /> : <Chip chip={d.state} />}</div>
            <div className="xc-rows mono rk-num" title="Rows available">{rowsLabel(d.rows) ?? <span className="xc-nospark">Not served</span>}</div>
            <div className="xc-size mono rk-num" title={d.size_bytes != null ? 'Size served' : 'Raw CSV · From a sample'}>
              {ok ? (sized ?? <MetaChip>Sampling</MetaChip>) : <span className="xc-nospark">Not served</span>}
            </div>
            {showUpdated && <div className="xc-upd mono">{stampShort(d.updated_at) ?? <Chip chip="NOT SERVED" />}</div>}
            <div className="xc-prov"><ProvChip p={d.provenance} /></div>
            <div className="xc-spark">
              {pv && pv.spark.some((v) => v != null)
                ? <SparkView values={pv.spark} kind={pv.sparkKind} />
                : <span className="xc-nospark">{ok ? (pv ? 'No numeric' : '…') : ''}</span>}
            </div>
            {!pick && (
              <div className="xc-acts" role="group" aria-label={`${d.label} actions`}>
                <button type="button" className="rk-btn rk-btn--primary" disabled={!ok} onClick={() => actions.open(d, 'grid')}>Open</button>
                <button type="button" className="rk-btn" disabled={!ok} onClick={() => actions.open(d, 'chart')}>Chart</button>
                <button type="button" className="rk-btn" disabled={!ok || isBusy} onClick={() => actions.csv(d)}>CSV</button>
                <button type="button" className="rk-btn" disabled={!ok || isBusy} onClick={() => actions.json(d)}>JSON</button>
                <button type="button" className="rk-btn" disabled={!ok || isBusy || xlsx !== 'served'} onClick={() => actions.xlsx(d)}
                  title={xlsx === 'served' ? undefined : 'XLSX not served'}>XLSX{xlsx === 'notserved' || xlsx === 'down' ? <Icon name="ban" size="xs" label="Not served" /> : null}</button>
              </div>
            )}
            {ok && pv && (
              <div className="xc-card" role="note" aria-label={`${d.label} preview`}>
                <div className="rk-row"><MetaChip>Preview · First {pv.sampleRows} rows</MetaChip><MetaChip>{pv.columns} columns</MetaChip>
                  {pv.bytesPerRow != null && <MetaChip>{Math.round(pv.bytesPerRow)} B / row</MetaChip>}</div>
                {pv.spark.some((v) => v != null)
                  ? <><SparkView values={pv.spark} kind={pv.sparkKind} width={300} height={56} /><span className="rk-lbl">{pv.sparkLabel}</span></>
                  : <Chip chip="NOT SERVED" word="No numeric column" />}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
