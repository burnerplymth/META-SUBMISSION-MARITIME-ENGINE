import React, { useState } from 'react';
import { Chip, MetaChip, StateLine } from './chips';
import { rowsLabel, shortHash, summariseManifest, type PackManifest } from './model';
import { allMatch, verifyPack, type VerifyRow } from './packVerify';
import { labelWord } from '../../copy';

/* MANIFEST viewer for a built pack: schema, as-of, caps, every file with
 * rows written / available / cut, bytes and the full sha256, what was
 * skipped, and a VERIFY that re-hashes each file from the zip bytes. */

export function ManifestView({ manifest, zip, onOpen }: { manifest: PackManifest; zip: Uint8Array; onOpen?: (datasetId: string) => void }) {
  const m = summariseManifest(manifest);
  const [rows, setRows] = useState<VerifyRow[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const verify = async () => {
    setBusy(true); setErr(null);
    try { setRows(await verifyPack(zip, manifest)); } catch (e) { setErr('verify_failed'); } finally { setBusy(false); }
  };
  const state = (file: string) => rows?.find((r) => r.file === file)?.state ?? null;
  const copy = (t: string) => { void navigator.clipboard?.writeText(t).catch(() => undefined); };
  return (
    <section className="xc-manifest" aria-label="Manifest">
      <div className="rk-toolbar">
        <span className="rk-lbl">MANIFEST.JSON</span>
        {m.schema && <MetaChip>{m.schema}</MetaChip>}
        {m.asOf && <Chip chip="SNAPSHOT" word={`As of ${m.asOf.slice(0, 16).replace('T', ' ')}Z`} />}
        {m.rowsCap != null && <MetaChip>Rows cap {rowsLabel(m.rowsCap)}</MetaChip>}
        {m.runId && <MetaChip title={m.runId}>Run {m.runId}</MetaChip>}
        {m.streamHash && <MetaChip title={m.streamHash}>Stream {shortHash(m.streamHash)}</MetaChip>}
        {m.mapHash && <MetaChip title={m.mapHash}>Map {shortHash(m.mapHash)}</MetaChip>}
        <span className="rk-spacer" />
        <button type="button" className="rk-btn" disabled={busy} onClick={verify}>{busy ? 'Hashing…' : 'Verify SHA256'}</button>
        {rows && <Chip chip={allMatch(rows) ? 'HAS DATA' : 'DOWN'} word={allMatch(rows) ? 'All match' : `${rows.filter((r) => r.state !== 'MATCH').length} differ`} />}
      </div>
      {err && <StateLine chip="DOWN" word="Verify failed" code={err} />}
      <div className="xc-scroll">
        <table className="rk-table">
          <thead>
            <tr><th scope="col">File</th><th scope="col" className="rk-r">Written</th><th scope="col" className="rk-r">Available</th><th scope="col" className="rk-r">Cut</th>
              <th scope="col" className="rk-r">Bytes</th><th scope="col">Provenance</th><th scope="col">SHA256</th><th scope="col"><span className="xc-sr">Open</span></th></tr>
          </thead>
          <tbody>
            {m.datasets.map((d) => (
              <tr key={d.id}>
                <td><span className="mono">{d.file}</span></td>
                <td className="rk-r mono rk-num">{d.rowsWritten}</td>
                <td className="rk-r mono rk-num">{d.rowsAvailable}</td>
                <td className="rk-r">{d.truncated ? <Chip chip="CUT" word={`Cut ${d.cut}`} /> : <span className="mono rk-num">0</span>}</td>
                <td className="rk-r mono rk-num">{d.bytes ?? <Chip chip="NOT SERVED" />}</td>
                <td>{d.provenance ? <Chip chip={/^mixed/i.test(d.provenance) ? 'MIXED' : d.provenance.toUpperCase()} /> : <Chip chip="NOT SERVED" />}</td>
                <td>
                  <button type="button" className="xc-hash mono" title="Copy" onClick={() => copy(d.sha256)}>{d.sha256.slice(0, 16)}…</button>
                  {state(d.file) && <Chip chip={state(d.file) === 'MATCH' ? 'HAS DATA' : 'DOWN'} word={state(d.file) as string} />}
                </td>
                <td>{onOpen && <button type="button" className="rk-btn" onClick={() => onOpen(d.id)}>Open</button>}</td>
              </tr>
            ))}
            {m.files.filter((f) => !m.datasets.some((d) => d.file === f.name)).map((f) => (
              <tr key={f.name}>
                <td><span className="mono">{f.name}</span></td>
                <td className="rk-r" colSpan={3} />
                <td className="rk-r mono rk-num">{f.bytes ?? <Chip chip="NOT SERVED" />}</td>
                <td />
                <td>
                  <button type="button" className="xc-hash mono" title="Copy" onClick={() => copy(f.sha256)}>{f.sha256.slice(0, 16)}…</button>
                  {state(f.name) && <Chip chip={state(f.name) === 'MATCH' ? 'HAS DATA' : 'DOWN'} word={state(f.name) as string} />}
                </td>
                <td />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(m.charts.length > 0 || m.workbook || m.report?.mdFile) && (
        <div className="rk-row rk-wrap" role="group" aria-label="Pack v2 contents">
          <span className="rk-lbl">Contents</span>
          {m.charts.length > 0 && <MetaChip>{m.charts.length} figures</MetaChip>}
          {m.png !== 'NOT REQUESTED' && <MetaChip>PNG {m.png}</MetaChip>}
          {m.workbook && <MetaChip title={m.workbook.sheets.join(', ')}>Workbook {m.workbook.sheets.length} sheets{m.workbook.bytes ? ` · ${m.workbook.bytes}` : ''}</MetaChip>}
          {m.report?.mdFile && <MetaChip>Report MD</MetaChip>}
        </div>
      )}
      {m.charts.length > 0 && (
        <ul className="xc-chartlist" aria-label="Figures in the pack">
          {m.charts.map((c) => (
            <li key={c.id}><span className="mono">{c.id}</span><Chip chip={c.chip} /><MetaChip>{labelWord(c.type).replace('_', ' ')}</MetaChip>{c.points != null && <span className="mono rk-num">{c.points} points</span>}</li>
          ))}
        </ul>
      )}
      <div className="rk-row rk-wrap">
        <span className="rk-lbl">Caps</span>
        {m.cutDatasets.length === 0
          ? <MetaChip>Nothing cut</MetaChip>
          : m.cutDatasets.map((c) => <Chip key={c.id} chip="CUT" word={`${c.id} −${rowsLabel(c.rowsCut)} at cap ${rowsLabel(c.rowsCap)}`} />)}
        {m.skipped.length > 0 && <span className="rk-lbl">Skipped</span>}
        {m.skipped.map((s) => <Chip key={s.id} chip="NOT SERVED" word={`${s.id}${s.reason ? ` · ${s.reason}` : ''}`} />)}
        {m.report && <MetaChip>Report {labelWord(m.report.template)} · {m.report.sections} sections</MetaChip>}
      </div>
    </section>
  );
}
