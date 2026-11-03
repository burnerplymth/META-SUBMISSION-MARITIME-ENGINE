import { fmtDateLong } from '../../format';
import React, { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Icon } from '../../ui/Icon';
import { Status } from '../../ui/Status';
import { Button, IconButton, SegmentedControl, Toggle } from '../../ui';
import { placeTag, searchPlaces, type PlaceChoice } from '../laneplanner/logic';
import { KIND_LABEL, MAX_SHOCKS, effectiveLoss, type ShockPatch, type ShockTemplate } from './doc';
import { diffRows, fmtD, fromPct, plural, rowChips, savedLine, toPct } from './logic';
import { SNAPS, type Snap } from './timeline';
import { KINDS, type PendingArrangement, type RulesDoc, type SavedRow, type ShockKind, type ShockVerdictRow, type UiShock } from './types';

/* Sections of the Scenario builder panel. Presentation only: the numbers and
 * the rules live in doc.ts, timeline.ts, logic.ts and the server. */

export type NameOf = (placeId: string) => string;

/* ── small inputs ───────────────────────────────────────────────────── */
/** A number field that commits on every valid keystroke and shows its unit. */
export function NumField({ label, value, unit, min, max, step = 'any', onCommit, className = '', id }: {
  label: string; value: number; unit?: string; min?: number; max?: number; step?: number | 'any'; onCommit: (v: number) => void; className?: string; id?: string;
}) {
  const [text, setText] = useState<string | null>(null);
  const shown = text ?? String(value);
  return (
    <label className={`scb-f ${className}`}>
      <span className="scb-lab">{label}</span>
      <span className="scb-unit">
        <input id={id} className="scb-input" type="number" inputMode="decimal" min={min} max={max} step={step} value={shown}
          onChange={(e) => { setText(e.target.value); const v = e.target.valueAsNumber; if (Number.isFinite(v)) onCommit(v); }}
          onBlur={() => setText(null)} />
        {unit && <i>{unit}</i>}
      </span>
    </label>
  );
}

export function KindSelect({ value, onChange, label = 'Kind', className = '' }: { value: ShockKind; onChange: (k: ShockKind) => void; label?: string; className?: string }) {
  return (
    <label className={`scb-f ${className}`}>
      <span className="scb-lab">{label}</span>
      <select className="scb-input" value={value} onChange={(e) => onChange(e.target.value as ShockKind)}>
        {KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
      </select>
    </label>
  );
}

export function Section({ title, children, aside, id }: { title: string; children: React.ReactNode; aside?: React.ReactNode; id?: string }) {
  const h = useId();
  return (
    <section className="scb-sec" aria-labelledby={h} data-section={id}>
      <header className="scb-sech"><h3 id={h} className="scb-st">{title}</h3><span className="scb-spacer" />{aside}</header>
      {children}
    </section>
  );
}

/* ── places: search, map selection, recent chips ─────────────────────── */
export function PlacePicker({ choices, onAdd, disabled }: { choices: readonly PlaceChoice[]; onAdd: (id: string) => void; disabled?: boolean }) {
  const id = useId();
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [idx, setIdx] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const hits = useMemo(() => (open ? searchPlaces(choices, q, 10) : []), [open, choices, q]);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', away);
    return () => document.removeEventListener('mousedown', away);
  }, [open]);
  const pick = (p: PlaceChoice) => { onAdd(p.id); setQ(''); setIdx(0); setOpen(false); };
  return (
    <div className="scb-f scb-f--grow" ref={box}>
      <label className="scb-lab" htmlFor={id}>Add place</label>
      <input id={id} className="scb-input" role="combobox" aria-expanded={open} aria-controls={`${id}-l`} aria-autocomplete="list" autoComplete="off" spellCheck={false}
        disabled={disabled} value={q} placeholder="Search places" data-testid="scb-place-search"
        onFocus={() => { setOpen(true); setIdx(0); }}
        onChange={(e) => { setQ(e.target.value); setOpen(true); setIdx(0); }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setIdx((i) => Math.min(hits.length - 1, i + 1)); }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)); }
          else if (e.key === 'Enter' && open && hits[idx]) { e.preventDefault(); e.stopPropagation(); pick(hits[idx]); }
          else if (e.key === 'Escape') setOpen(false);
        }} />
      {open && (
        <ul id={`${id}-l`} className="scb-list" role="listbox" aria-label="Places">
          {hits.length === 0 && <li className="scb-empty" role="option" aria-selected="false">No place</li>}
          {hits.map((p, i) => (
            <li key={p.id} role="option" aria-selected={i === idx} className={i === idx ? 'on' : ''}
              onMouseDown={(e) => { e.preventDefault(); pick(p); }} onMouseEnter={() => setIdx(i)}>
              <span className="scb-pn">{p.name}</span>
              <span className="scb-pc">{p.country ?? ''}</span>
              <span className="scb-tag">{placeTag(p)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function PlacesSection(p: {
  choices: readonly PlaceChoice[]; nameOf: NameOf; places: readonly string[]; recents: readonly string[];
  mapSelected: string | null; follow: boolean; onFollow: (v: boolean) => void;
  onAdd: (id: string) => void; onRemove: (id: string) => void; onClear: () => void;
  template: ShockTemplate; onTemplate: (t: ShockTemplate) => void; onAddShocks: () => void; room: number;
}) {
  const t = p.template;
  const n = p.places.length;
  const recent = p.recents.filter((r) => !p.places.includes(r)).slice(0, 6);
  return (
    <Section title="Places" id="places" aside={n > 0 ? <Button size="sm" variant="ghost" onClick={p.onClear}>Clear</Button> : undefined}>
      <div className="scb-row">
        <PlacePicker choices={p.choices} onAdd={p.onAdd} />
        <div className="scb-f">
          <span className="scb-lab">Map selection</span>
          <span className="scb-inline">
            <span className="scb-mapsel">{p.mapSelected ? p.nameOf(p.mapSelected) : 'None'}</span>
            <Button size="sm" disabled={!p.mapSelected || p.places.includes(p.mapSelected)} onClick={() => p.mapSelected && p.onAdd(p.mapSelected)}>Add</Button>
          </span>
        </div>
        <div className="scb-f scb-f--tog"><Toggle checked={p.follow} onChange={p.onFollow} label="Follow map" words={['Off', 'On']} /></div>
      </div>
      <ul className="scb-chips" aria-label="Chosen places" data-testid="scb-chosen">
        {n === 0 && <li className="scb-none">No places chosen</li>}
        {p.places.map((id) => (
          <li key={id} className="scb-chip"><span>{p.nameOf(id)}</span>
            <button type="button" aria-label={`Remove ${p.nameOf(id)}`} onClick={() => p.onRemove(id)}><Icon name="close" size="xs" /></button></li>
        ))}
      </ul>
      {recent.length > 0 && (
        <div className="scb-recent" role="group" aria-label="Recent places">
          <span className="scb-lab">Recent</span>
          {recent.map((id) => <button key={id} type="button" className="scb-rc" onClick={() => p.onAdd(id)}>{p.nameOf(id)}</button>)}
        </div>
      )}
      <div className="scb-row scb-row--tpl" role="group" aria-label="Shock for the chosen places">
        <KindSelect value={t.kind} label="Kind" onChange={(k) => p.onTemplate({ ...t, kind: k, loss: k === 'closure' ? 1 : t.loss })} />
        <NumField label="Start" unit="d" min={0} max={365} value={t.startD} onCommit={(v) => p.onTemplate({ ...t, startD: v })} className="scb-f--num" />
        <NumField label="Duration" unit="d" min={0.25} max={3650} value={t.durationD} onCommit={(v) => p.onTemplate({ ...t, durationD: v })} className="scb-f--num" />
        <NumField label="Capacity loss" unit="%" min={1} max={100} value={t.kind === 'closure' ? 100 : toPct(t.loss)} onCommit={(v) => p.onTemplate({ ...t, loss: fromPct(v) })} className="scb-f--num" />
        <Button variant="primary" icon="add" disabled={n === 0 || p.room === 0} onClick={p.onAddShocks} data-testid="scb-add-places">
          {n === 0 ? 'Add shocks' : `Add ${plural(Math.min(n, p.room), 'shock')}`}
        </Button>
      </div>
    </Section>
  );
}

/* ── shock rows ─────────────────────────────────────────────────────── */
export type BulkField = 'durationD' | 'loss' | 'startD' | 'kind';
export const BULK_FIELDS: ReadonlyArray<{ value: BulkField; label: string; unit: string }> = [
  { value: 'durationD', label: 'Duration', unit: 'd' }, { value: 'loss', label: 'Capacity loss', unit: '%' },
  { value: 'startD', label: 'Start', unit: 'd' }, { value: 'kind', label: 'Kind', unit: '' },
];

function BulkBar({ count, onApply }: { count: number; onApply: (patch: ShockPatch) => void }) {
  const [field, setField] = useState<BulkField>('durationD');
  const [num, setNum] = useState(7);
  const [kind, setKind] = useState<ShockKind>('strike');
  const f = BULK_FIELDS.find((x) => x.value === field)!;
  const apply = () => {
    if (field === 'kind') onApply({ kind });
    else if (field === 'loss') onApply({ loss: fromPct(num) });
    else onApply({ [field]: num } as ShockPatch);
  };
  return (
    <div className="scb-bulk" role="group" aria-label="Bulk edit" data-testid="scb-bulk">
      <span className="scb-count">{count} selected</span>
      <label className="scb-f"><span className="scb-lab">Field</span>
        <select className="scb-input" value={field} onChange={(e) => setField(e.target.value as BulkField)}>
          {BULK_FIELDS.map((x) => <option key={x.value} value={x.value}>{x.label}</option>)}
        </select></label>
      {field === 'kind'
        ? <KindSelect value={kind} onChange={setKind} label="Value" />
        : <NumField label="Value" unit={f.unit} value={num} onCommit={setNum} className="scb-f--num" min={0} />}
      <Button size="sm" variant="primary" onClick={apply} data-testid="scb-bulk-apply">Apply to all selected</Button>
    </div>
  );
}

export function ShocksSection(p: {
  shocks: readonly UiShock[]; selected: readonly string[]; focusId: string | null; nameOf: NameOf; placeIds: readonly string[];
  verdicts: ReadonlyMap<string, ShockVerdictRow>; rules: RulesDoc | null; checking: boolean;
  canUndo: boolean; canRedo: boolean;
  onAdd: () => void; onUndo: () => void; onRedo: () => void;
  onToggle: (id: string) => void; onToggleAll: () => void; onFocus: (id: string) => void;
  onPatch: (id: string, patch: ShockPatch, key: string) => void; onRemove: (id: string) => void; onDuplicate: (id: string) => void; onMove: (id: string, dir: -1 | 1) => void;
  onBulk: (patch: ShockPatch) => void;
}) {
  const n = p.shocks.length;
  const all = n > 0 && p.selected.length === n;
  const places = (cur: string) => [...new Set([cur, ...p.placeIds])];
  return (
    <Section title="Shocks" id="shocks" aside={(
      <span className="scb-inline">
        <IconButton icon="back" label="Undo" size="sm" disabled={!p.canUndo} onClick={p.onUndo} keys="Mod+Z" />
        <IconButton icon="forward" label="Redo" size="sm" disabled={!p.canRedo} onClick={p.onRedo} keys="Mod+Shift+Z" />
        <Button variant="primary" icon="add" size="sm" disabled={n >= MAX_SHOCKS} onClick={p.onAdd} data-testid="scb-add-shock">Add shock</Button>
      </span>
    )}>
      {n === 0 && <div className="scb-state" role="status"><Status state="meta" word="No shocks" /></div>}
      {n > 0 && (
        <div className="scb-rows" role="table" aria-label="Shocks" data-testid="scb-rows">
          <div className="scb-hrow" role="row">
            <span role="columnheader"><input type="checkbox" aria-label="Select all shocks" checked={all} onChange={p.onToggleAll} /></span>
            <span role="columnheader">Place</span><span role="columnheader">Kind</span><span role="columnheader">Start</span>
            <span role="columnheader">Duration</span><span role="columnheader">Loss</span><span role="columnheader">Checks</span><span role="columnheader" className="scb-vh">Actions</span>
          </div>
          {p.shocks.map((s, i) => {
            const v = p.verdicts.get(s.id);
            const chips = rowChips(v, p.rules);
            const refused = chips.filter((c) => !c.ok);
            return (
              <div key={s.id} role="row" className={`scb-srow${p.focusId === s.id ? ' on' : ''}${refused.length === 2 ? ' bad' : ''}`} data-shock-row={s.id} onFocusCapture={() => p.onFocus(s.id)}>
                <span role="cell"><input type="checkbox" aria-label={`Select shock at ${p.nameOf(s.placeId)}`} checked={p.selected.includes(s.id)} onChange={() => p.onToggle(s.id)} /></span>
                <span role="cell" className="scb-cell-place">
                  <select className="scb-input" aria-label={`Place of shock ${i + 1}`} value={s.placeId} onChange={(e) => p.onPatch(s.id, { placeId: e.target.value }, `place:${s.id}`)}>
                    {places(s.placeId).map((id) => <option key={id} value={id}>{p.nameOf(id)}</option>)}
                  </select>
                </span>
                <span role="cell"><select className="scb-input" aria-label={`Kind of shock ${i + 1}`} value={s.kind} onChange={(e) => p.onPatch(s.id, { kind: e.target.value as ShockKind }, `kind:${s.id}`)}>
                  {KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}</select></span>
                <span role="cell"><NumField label="Start" unit="d" min={0} max={365} value={s.startD} onCommit={(x) => p.onPatch(s.id, { startD: x }, `start:${s.id}`)} className="scb-f--cell" id={`scb-start-${s.id}`} /></span>
                <span role="cell"><NumField label="Duration" unit="d" min={0.01} max={3650} value={s.durationD} onCommit={(x) => p.onPatch(s.id, { durationD: x }, `dur:${s.id}`)} className="scb-f--cell" id={`scb-dur-${s.id}`} /></span>
                <span role="cell"><NumField label="Capacity loss" unit="%" min={1} max={100} value={toPct(effectiveLoss(s))} onCommit={(x) => p.onPatch(s.id, { loss: fromPct(x) }, `loss:${s.id}`)} className="scb-f--cell" id={`scb-loss-${s.id}`} /></span>
                <span role="cell" className="scb-checks">
                  {chips.length === 0 && <Status state="meta" word={p.checking ? 'Checking' : 'Unchecked'} />}
                  {chips.map((c) => (
                    <span key={c.target} className={`scb-v scb-v--${c.ok ? 'ok' : 'bad'}`} data-verdict={`${c.target}:${c.ok ? 'ok' : 'refused'}`} title={c.reason ?? undefined}>
                      <Icon name={c.ok ? 'ok' : 'error'} size="xs" />{c.target === 'chain' ? 'Chain' : 'Sim'} {c.word.toLowerCase()}
                    </span>
                  ))}
                  {refused.length > 0 && <span className="scb-why">{[...new Set(refused.map((c) => c.reason))].join('. ')}</span>}
                </span>
                <span role="cell" className="scb-acts">
                  <IconButton icon="arrow-up" label={`Move shock ${i + 1} up`} size="sm" disabled={i === 0} onClick={() => p.onMove(s.id, -1)} />
                  <IconButton icon="arrow-down" label={`Move shock ${i + 1} down`} size="sm" disabled={i === n - 1} onClick={() => p.onMove(s.id, 1)} />
                  <IconButton icon="copy" label={`Duplicate shock ${i + 1}`} size="sm" disabled={n >= MAX_SHOCKS} onClick={() => p.onDuplicate(s.id)} />
                  <IconButton icon="remove" label={`Remove shock ${i + 1}`} size="sm" onClick={() => p.onRemove(s.id)} />
                </span>
              </div>
            );
          })}
        </div>
      )}
      {p.selected.length > 0 && <BulkBar count={p.selected.length} onApply={p.onBulk} />}
    </Section>
  );
}

/* ── timeline toolbar and chips ─────────────────────────────────────── */
export function TimelineBar(p: {
  axis: 'days' | 'dates'; onAxis: (a: 'days' | 'dates') => void; snap: Snap; onSnap: (s: Snap) => void;
  onZoomIn: () => void; onZoomOut: () => void; onFit: () => void; startDate: string; onStartDate: (d: string) => void;
  span: number; overlaps: number; sameSite: number; lastEnd: number; empty: boolean;
}) {
  return (
    <div className="scb-tlbar">
      <SegmentedControl label="Axis" size="sm" value={p.axis} onChange={p.onAxis} options={[{ value: 'days', label: 'Days' }, { value: 'dates', label: 'Dates' }]} />
      <SegmentedControl label="Snap" size="sm" value={String(p.snap) as '0.25' | '1'} onChange={(v) => p.onSnap(Number(v) as Snap)}
        options={SNAPS.map((s) => ({ value: String(s) as '0.25' | '1', label: `${s} d` }))} />
      <span className="scb-inline" role="group" aria-label="Zoom">
        <IconButton icon="minus" label="Zoom out" size="sm" onClick={p.onZoomOut} />
        <IconButton icon="add" label="Zoom in" size="sm" onClick={p.onZoomIn} />
        <Button size="sm" variant="ghost" onClick={p.onFit}>Fit</Button>
      </span>
      <label className="scb-f scb-f--date"><span className="scb-lab">Start date</span>
        <input className="scb-input" type="date" value={p.startDate} onChange={(e) => e.target.value && p.onStartDate(e.target.value)} />
        <span className="scb-datefmt mono">{fmtDateLong(p.startDate)}</span></label>
      <span className="scb-spacer" />
      <span className="scb-chip2" data-testid="scb-span"><b>Total span</b> {fmtD(p.span)}</span>
      <span className={`scb-chip2${p.overlaps > 0 ? ' warn' : ''}`} data-testid="scb-overlaps">
        {p.empty ? 'No shocks' : p.overlaps === 0 ? 'No overlap' : `${plural(p.overlaps, 'overlap')}${p.sameSite ? ` · ${p.sameSite} at one place` : ''}`}
      </span>
    </div>
  );
}

/* ── arrangement: Meta AI box, rules, the diff ─────────────────────────── */
export function ArrangeSection(p: {
  busy: boolean; mode: 'Recorded' | 'Live' | null; status: string; disabled: boolean; aiOff: boolean;
  onAsk: (text: string) => void; onRule: (rule: { kind: 'stagger' | 'cluster' | 'spread_evenly' | 'align_start'; value?: number }) => void;
  pending: PendingArrangement | null; nameOf: (shockId: string) => string;
  onAccept: () => void; onDiscard: () => void;
  /** Why the last task produced no proposal (a short chip beside the status). */
  reason?: string | null;
  /** An accepted arrangement can be undone from here until the next edit. */
  applied?: boolean; onUndo?: () => void;
}) {
  const [text, setText] = useState('');
  const [within, setWithin] = useState(7);
  const [window_, setWindow] = useState(30);
  const [gap, setGap] = useState(0);
  const rows = p.pending ? diffRows(p.pending.diff) : [];
  return (
    <Section title="Arrange" id="arrange" aside={p.mode ? <Status state="meta" word={p.mode} title="Meta AI transport" /> : undefined}>
      <form className="scb-row scb-ask" onSubmit={(e) => { e.preventDefault(); if (text.trim() && !p.busy) { p.onAsk(text); } }}>
        <label className="scb-f scb-f--grow"><span className="scb-lab">Ask Meta AI to arrange</span>
          <input className="scb-input" value={text} maxLength={280} placeholder="Stagger these so they do not overlap" disabled={p.disabled || p.aiOff}
            onChange={(e) => setText(e.target.value)} data-testid="scb-ask-input" /></label>
        <Button type="submit" variant="primary" icon="ai" disabled={p.disabled || p.aiOff || p.busy || !text.trim()} data-testid="scb-ask-send">{p.busy ? 'Working' : 'Arrange'}</Button>
      </form>
      {p.status && (
        <div className="scb-note scb-inline" role="status" data-testid="scb-ask-status">
          <span>{p.status}</span>
          {p.reason && <span className="scb-chip2 warn" data-testid="scb-ask-reason">{p.reason}</span>}
          {p.applied && p.onUndo && <Button size="sm" icon="back" onClick={p.onUndo} data-testid="scb-undo-applied">Undo</Button>}
        </div>
      )}
      <div className="scb-row scb-rules" role="group" aria-label="Arrange by rule">
        <span className="scb-inline"><NumField label="Gap" unit="d" value={gap} onCommit={setGap} className="scb-f--num" />
          <Button size="sm" disabled={p.disabled} onClick={() => p.onRule({ kind: 'stagger', value: gap })} data-testid="scb-rule-stagger">Stagger</Button></span>
        <span className="scb-inline"><NumField label="Within" unit="d" value={within} onCommit={setWithin} className="scb-f--num" min={0} />
          <Button size="sm" disabled={p.disabled} onClick={() => p.onRule({ kind: 'cluster', value: within })} data-testid="scb-rule-cluster">Cluster</Button></span>
        <span className="scb-inline"><NumField label="Over" unit="d" value={window_} onCommit={setWindow} className="scb-f--num" min={0} />
          <Button size="sm" disabled={p.disabled} onClick={() => p.onRule({ kind: 'spread_evenly', value: window_ })} data-testid="scb-rule-spread">Spread evenly</Button></span>
        <Button size="sm" disabled={p.disabled} onClick={() => p.onRule({ kind: 'align_start' })} data-testid="scb-rule-align">Align starts</Button>
      </div>
      {p.pending && (
        <div className="scb-diff" role="group" aria-label="Proposed arrangement" data-testid="scb-diff">
          <div className="scb-diffh">
            <b>Proposed arrangement</b>
            <Status state="meta" word={p.pending.source === 'meta-ai' ? 'Meta AI' : 'Rule'} />
            <span className="scb-chip2">{p.pending.rule.kind.replace('_', ' ')}</span>
            <span className="scb-spacer" />
            <span className="scb-chip2" data-testid="scb-diff-overlaps">Overlaps {p.pending.overlapsBefore} to {p.pending.overlapsAfter}</span>
            <span className="scb-chip2" data-testid="scb-diff-span">Span {fmtD(p.pending.spanBefore)} to {fmtD(p.pending.spanAfter)}</span>
          </div>
          <ul className="scb-diffl">
            {rows.map((r) => <li key={r.id}><span>{p.nameOf(r.id)}</span><span className="mono">{fmtD(r.from)} to {fmtD(r.to)}</span></li>)}
          </ul>
          <div className="scb-inline">
            <Button variant="primary" icon="check" size="sm" onClick={p.onAccept} data-testid="scb-accept">Accept</Button>
            <Button size="sm" onClick={p.onDiscard} data-testid="scb-discard">Discard</Button>
          </div>
        </div>
      )}
    </Section>
  );
}

/* ── saved scenarios ────────────────────────────────────────────────── */
export function SavedSection(p: {
  name: string; onName: (n: string) => void; onSave: () => void; saving: boolean; canSave: boolean; saveNote: string | null;
  saved: SavedRow[] | null; savedState: 'idle' | 'loading' | 'error'; onLoad: (id: string) => void; onDelete: (id: string) => void; onCompare: (id: string) => void;
  comparing: string | null; owned?: Record<string, string>;
}) {
  return (
    <Section title="Saved" id="saved">
      <div className="scb-row">
        <label className="scb-f scb-f--grow"><span className="scb-lab">Name</span>
          <input className="scb-input" value={p.name} maxLength={60} placeholder="Untitled scenario" onChange={(e) => p.onName(e.target.value)} data-testid="scb-name" /></label>
        <Button icon="save" disabled={!p.canSave || p.saving} onClick={p.onSave} data-testid="scb-save">Save scenario</Button>
        {p.saveNote && <Status state="meta" word={p.saveNote} />}
      </div>
      {p.savedState === 'error' && <div className="scb-state"><Status state="down" word="Not served" /></div>}
      {p.saved && p.saved.length === 0 && <div className="scb-state" role="status"><Status state="meta" word="None saved" /></div>}
      {p.saved && p.saved.length > 0 && (
        <ul className="scb-saved" data-testid="scb-saved">
          {p.saved.map((r) => (
            <li key={r.id} data-saved={r.id}>
              <span className="scb-sn">{r.name || 'Untitled scenario'}</span>
              <span className="scb-ss mono">{savedLine(r)}</span>
              <span className="scb-inline">
                <Button size="sm" onClick={() => p.onLoad(r.id)}>Open</Button>
                <Button size="sm" disabled={p.comparing !== null} onClick={() => p.onCompare(r.id)}>{p.comparing === r.id ? 'Comparing' : 'Compare'}</Button>
                {p.owned?.[r.id]
                  ? <IconButton icon="remove" label={`Delete ${r.name || 'scenario'}`} size="sm" onClick={() => p.onDelete(r.id)} />
                  : <Status state="meta" word="Not yours" />}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

