import React, { useState } from 'react';
import { formatBytes } from '../../charts/format';
import { Chip, MetaChip } from './chips';
import { buildPack, buildReport, saveBlob } from './client';
import { replay } from './exportActions';
import { stampShort } from './exportPlan';
import type { ExportDataset } from './model';
import type { RecentItem } from './recent';

/* RECENT: what this browser exported lately, with the request behind
 * each file, so it can be fetched again. Local to this browser. A pack
 * request carries its pinned as_of, so the same zip is rebuilt. */

const KIND: Record<RecentItem['req']['t'], string> = { csv: 'CSV', json: 'JSON', xlsx: 'XLSX', pack: 'ZIP', report: 'REPORT', snapshot: 'SNAPSHOT', figure: 'FIGURE', figpost: 'FIGURE' };

export function RecentList({ items, catalogue, onClear, onGoto }: {
  items: RecentItem[]; catalogue: ExportDataset[]; onClear: () => void; onGoto: (tab: 'report' | 'snapshot') => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [fail, setFail] = useState<Record<string, string>>({});
  const again = async (it: RecentItem) => {
    setBusy(it.key); setFail((f) => { const n = { ...f }; delete n[it.key]; return n; });
    try {
      const r = it.req;
      if (r.t === 'pack') { const dl = await buildPack(r.body); saveBlob(dl.filename, dl.blob); }
      else if (r.t === 'report') {
        const env = await buildReport(r.body as unknown as Parameters<typeof buildReport>[0]);
        saveBlob(env.filename || it.file, new Blob([env.content], { type: r.ext === 'md' ? 'text/markdown' : 'text/html' }));
      } else if (!(await replay(r, catalogue))) throw new Error('unavailable');
    } catch (e) { setFail((f) => ({ ...f, [it.key]: (e as { code?: string }).code ?? 'FAILED' })); } finally { setBusy(null); }
  };
  if (items.length === 0) return <div className="rk-state"><Chip chip="NOT SERVED" word="Nothing exported yet" /></div>;
  return (
    <div className="xc-recent">
      <div className="rk-row"><MetaChip>This browser only</MetaChip><MetaChip>{items.length} kept</MetaChip><span className="rk-spacer" /><button type="button" className="rk-btn" onClick={onClear}>Clear list</button></div>
      <ul className="xc-list" aria-label="Recent exports">
        {items.map((it) => {
          const needsPage = it.req.t === 'snapshot' || (it.req.t === 'report' && it.req.figures);
          return (
            <li key={it.key} className="xc-rrow">
              <MetaChip>{KIND[it.req.t]}</MetaChip>
              <div className="xc-name"><span className="xc-title">{it.label}</span><span className="mono rk-code">{it.file}</span></div>
              <span className="mono rk-num">{stampShort(it.at)}</span>
              <span className="mono rk-num">{formatBytes(it.bytes) ?? <Chip chip="NOT SERVED" />}</span>
              <span className="mono rk-num">{it.rows != null ? `${it.rows.toLocaleString('en-US')} rows` : ''}</span>
              {it.runId ? <MetaChip title={it.runId}>Run {it.runId.slice(4, 14)}</MetaChip> : <span />}
              {needsPage
                ? <button type="button" className="rk-btn" onClick={() => onGoto(it.req.t === 'snapshot' ? 'snapshot' : 'report')}>Open page</button>
                : <button type="button" className="rk-btn" disabled={busy === it.key} onClick={() => again(it)}>{busy === it.key ? 'Fetching…' : 'Re-download'}</button>}
              {fail[it.key] && <Chip chip="DOWN" word={fail[it.key]} />}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
