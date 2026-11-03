import React, { useEffect, useRef, useState } from 'react';
import { Button } from '../../ui';
import { Chip, MetaChip } from '../reports/chips';
import { postResolve } from './client';
import { QUALITY_WORD } from './logic';
import type { ResolveRow, ShipMode, Shipment, Suggestion } from './types';
import { SHIP_MODES } from './types';
import { cleanText, MAX_TEXT, readDate, readNumber } from './csv';

/* A hand-entered shipment. Each place field shows how its text resolves
 * (POST /exposure/resolve): the place and the match quality, or the closest
 * places to pick. Nothing is stored until the row is added. */

function useResolved(text: string, mode: ShipMode | 'via'): ResolveRow | null | 'pending' {
  const [row, setRow] = useState<ResolveRow | null | 'pending'>(null);
  useEffect(() => {
    const t = text.trim();
    if (t.length < 2) { setRow(null); return; }
    setRow('pending');
    const ctrl = new AbortController();
    const timer = setTimeout(() => { void postResolve([{ text: t, mode }], ctrl.signal).then((r) => { if (!ctrl.signal.aborted) setRow(r?.[0] ?? null); }); }, 280);
    return () => { clearTimeout(timer); ctrl.abort(); };
  }, [text, mode]);
  return row;
}

function Resolution({ row, onPick }: { row: ResolveRow | null | 'pending'; onPick: (name: string) => void }) {
  if (row === null) return <span className="sh-sub">&nbsp;</span>;
  if (row === 'pending') return <span className="sh-sub">Resolving</span>;
  if (row.status === 'resolved' && row.place) {
    return <span className="sh-sub sh-res"><span className="sh-pn">{row.place.name}</span>{row.place.country ? <i>{row.place.country}</i> : null}<MetaChip>{QUALITY_WORD[row.quality ?? ''] ?? 'Match'}</MetaChip></span>;
  }
  return (
    <span className="sh-sub sh-res">
      <Chip chip="NO DATA" word="Unresolved" />
      {row.suggestions.slice(0, 3).map((s: Suggestion) => (
        <button key={s.id} type="button" className="rk-btn" aria-label={`Use ${s.name}`} onClick={() => onPick(s.name)}>{s.name}</button>
      ))}
    </span>
  );
}

export function AddForm({ nextId, onAdd, onClose }: { nextId: string; onAdd: (s: Shipment) => void; onClose: () => void }) {
  const [origin, setOrigin] = useState(''); const [dest, setDest] = useState(''); const [via, setVia] = useState('');
  const [mode, setMode] = useState<ShipMode>('sea');
  const [qty, setQty] = useState(''); const [value, setValue] = useState(''); const [etd, setEtd] = useState(''); const [eta, setEta] = useState('');
  const first = useRef<HTMLInputElement>(null);
  useEffect(() => { first.current?.focus(); }, []);
  const ro = useResolved(origin, mode); const rd = useResolved(dest, mode); const rv = useResolved(via, 'via');
  const ok = cleanText(origin).length > 0 && cleanText(dest).length > 0;
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!ok) return;
    const s: Shipment = { id: nextId, origin: cleanText(origin).slice(0, MAX_TEXT), destination: cleanText(dest).slice(0, MAX_TEXT), mode };
    const q = readNumber(qty); if (q !== null) { s.quantity = q; s.unit = 'TEU'; }
    const v = readNumber(value); if (v !== null) s.value_usd = v;
    const d1 = readDate(etd); if (d1) s.etd = d1;
    const d2 = readDate(eta); if (d2) s.eta = d2;
    const vi = cleanText(via).slice(0, MAX_TEXT); if (vi) s.via = vi;
    onAdd(s);
    setOrigin(''); setDest(''); setVia(''); setQty(''); setValue(''); setEtd(''); setEta('');
    first.current?.focus();
  };
  const field = (label: string, node: React.ReactNode, sub?: React.ReactNode, cls = '') => (
    <div className={`sh-field ${cls}`}><label className="sh-fl"><span className="sh-lab">{label}</span>{node}</label>{sub}</div>
  );
  return (
    <form className="sh-form" onSubmit={submit} aria-label="Add shipment" data-testid="sh-form">
      {field('Origin', <input ref={first} className="sh-input" value={origin} maxLength={MAX_TEXT} autoComplete="off" spellCheck={false} onChange={(e) => setOrigin(e.target.value)} />, <Resolution row={ro} onPick={setOrigin} />, 'sh-field--place')}
      {field('Destination', <input className="sh-input" value={dest} maxLength={MAX_TEXT} autoComplete="off" spellCheck={false} onChange={(e) => setDest(e.target.value)} />, <Resolution row={rd} onPick={setDest} />, 'sh-field--place')}
      {field('Via', <input className="sh-input" value={via} maxLength={MAX_TEXT} autoComplete="off" spellCheck={false} onChange={(e) => setVia(e.target.value)} />, <Resolution row={rv} onPick={setVia} />, 'sh-field--place')}
      {field('Mode', <select className="sh-input" value={mode} onChange={(e) => setMode(e.target.value as ShipMode)}>{SHIP_MODES.map((m) => <option key={m} value={m}>{m === 'sea' ? 'Sea' : m === 'air' ? 'Air' : 'Ground'}</option>)}</select>, undefined, 'sh-field--num')}
      {field('TEU', <input className="sh-input" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} />, undefined, 'sh-field--num')}
      {field('Value USD', <input className="sh-input" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} />, undefined, 'sh-field--num')}
      {field('Departure', <input className="sh-input" type="date" value={etd} onChange={(e) => setEtd(e.target.value)} />, undefined, 'sh-field--num')}
      {field('Arrival', <input className="sh-input" type="date" value={eta} onChange={(e) => setEta(e.target.value)} />, undefined, 'sh-field--num')}
      <div className="sh-form-actions">
        <Button variant="primary" icon="plus" type="submit" disabled={!ok}>Add</Button>
        <Button onClick={onClose}>Close</Button>
      </div>
    </form>
  );
}
