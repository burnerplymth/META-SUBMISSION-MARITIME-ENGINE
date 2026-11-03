import React, { useEffect, useRef, useState } from 'react';
import { Button, Icon, InspectorSection, ScrubField, Toggle } from '../../ui';
import { Tag, Tags } from './chips';
import { LAYER_DEFS, layerCount, type LayerKey, type LayerVis } from './geo';
import {
  FIELDS, SCENARIO_LABEL, SCENARIO_TYPES, checkForm, unavailable,
  type ScenarioCtx, type ScenarioForm, type ScenarioType,
} from './scenario';
import { searchSeaPlaces } from './client';
import type { ChipSpec } from './labels';
import type { GeoIndexPort, PlaceHit } from './types';
import { labelWord } from '../../copy';

/* ── port picker ───────────────────────────────────────────────────── */
export function PortPicker({ placeId, ports, onPick, chips, name }: { placeId: string; ports: readonly GeoIndexPort[]; onPick: (id: string) => void; chips: readonly ChipSpec[]; name?: string }) {
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<PlaceHit[]>([]);
  const known = ports.some((p) => p.place_id === placeId);
  const timer = useRef(0);
  useEffect(() => {
    window.clearTimeout(timer.current);
    if (q.trim().length < 2) { setHits([]); return undefined; }
    const ctrl = new AbortController();
    timer.current = window.setTimeout(() => {
      searchSeaPlaces(q.trim(), ctrl.signal).then(setHits, () => setHits([]));
    }, 220);
    return () => { window.clearTimeout(timer.current); ctrl.abort(); };
  }, [q]);
  return (
    <div className="ptw-picker">
      <label className="ptw-field">
        <span className="ptw-field-l">Port</span>
        <select className="ptw-select" value={known ? placeId : ''} onChange={(e) => { if (e.target.value) onPick(e.target.value); }} aria-label="Port">
          {!known && <option value="">{name ?? placeId}</option>}
          {ports.map((p) => <option key={p.place_id} value={p.place_id}>{p.name}</option>)}
        </select>
      </label>
      <label className="ptw-field">
        <span className="ptw-field-l">Other place</span>
        <span className="ptw-search">
          <Icon name="search" size="xs" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search" aria-label="Search places" spellCheck={false} />
        </span>
      </label>
      {hits.length > 0 && (
        <ul className="ptw-hits" role="listbox" aria-label="Places">
          {hits.map((h) => (
            <li key={h.id}><button type="button" role="option" aria-selected={h.id === placeId} onClick={() => { onPick(h.id); setQ(''); setHits([]); }}>{h.name}</button></li>
          ))}
        </ul>
      )}
      <Tags chips={chips} />
    </div>
  );
}

/* ── layers ────────────────────────────────────────────────────────── */
export interface LookState { colour: boolean; glow: boolean; labels: boolean; vessels: boolean }

/** On a short viewport (1280x720) the Layers group starts closed so the Scenario controls below it are reachable without scrolling. */
export const layersDefaultOpen = (viewportH: number): boolean => !(viewportH > 0 && viewportH < 800);

export function LayersSection({ vis, onVis, counts, hasCoast, look, onLook, imagery }: {
  vis: LayerVis; onVis: (k: LayerKey) => void; counts: Record<string, number>; hasCoast: boolean;
  look: LookState; onLook: (k: keyof LookState, v: boolean) => void;
  imagery: { available: boolean; on: boolean; onToggle: (v: boolean) => void; opacity: number; onOpacity: (n: number) => void; chips: readonly ChipSpec[] } | null;
}) {
  return (
    <InspectorSection title="Layers" defaultOpen={layersDefaultOpen(typeof window === 'undefined' ? 0 : window.innerHeight)} actions={<Tag state="snapshot" word="Sourced" />}>
      <div className="ptw-lyrs" role="group" aria-label="Map layers">
        {LAYER_DEFS.map((d) => {
          const n = layerCount(d, counts, hasCoast);
          const off = n === 0;
          return (
            <button key={d.key} type="button" className="ptw-lyr" aria-pressed={vis[d.key] && !off} disabled={off} data-layer={d.key}
              title={off ? 'None mapped' : undefined} onClick={() => onVis(d.key)}>
              <i className={`ptw-sw ptw-sw--${d.swatch}`} aria-hidden="true" />
              <span>{d.label}</span>
              <em>{off ? '0' : n}</em>
            </button>
          );
        })}
      </div>
      <div className="ptw-looks">
        <Toggle label="Colour" checked={look.colour} onChange={(v) => onLook('colour', v)} />
        <Toggle label="Glow" checked={look.glow} onChange={(v) => onLook('glow', v)} />
        <Toggle label="Labels" checked={look.labels} onChange={(v) => onLook('labels', v)} />
        <Toggle label="Vessels" checked={look.vessels} onChange={(v) => onLook('vessels', v)} />
      </div>
      {imagery && imagery.available && (
        <div className="ptw-imagery">
          <Toggle label="Sentinel-2 underlay" checked={imagery.on} onChange={imagery.onToggle} />
          <Tags chips={imagery.chips} />
          {imagery.on && (
            <label className="ptw-opacity"><span>Opacity</span>
              <input type="range" min={0.1} max={1} step={0.05} value={imagery.opacity} onChange={(e) => imagery.onOpacity(Number(e.target.value))} aria-label="Underlay opacity" />
            </label>
          )}
        </div>
      )}
    </InspectorSection>
  );
}

/* ── scenario ──────────────────────────────────────────────────────── */
export interface ScenarioPanelProps {
  ctx: ScenarioCtx;
  form: ScenarioForm;
  onForm: (f: ScenarioForm) => void;
  days: number;
  onDays: (n: number) => void;
  withRange: boolean;
  onRange: (v: boolean) => void;
  busy: boolean;
  error: { word: string; reason: string } | null;
  onRun: () => void;
  onBaseline: () => void;
  canRun: boolean;
}
export function ScenarioSection({ ctx, form, onForm, days, onDays, withRange, onRange, busy, error, onRun, onBaseline, canRun }: ScenarioPanelProps) {
  const spec = FIELDS[form.type];
  const issues = checkForm(form, ctx);
  const bad = (f: string) => issues.some((i) => i.field === f);
  const group = ctx.groups.find((g) => g.id === form.group);
  const set = (patch: Partial<ScenarioForm>) => onForm({ ...form, ...patch });
  return (
    <InspectorSection title="Scenario" actions={<Tag state="sim" word="Simulated" />}>
      <div className="ptw-scn" role="group" aria-label="Scenario type">
        {SCENARIO_TYPES.map((t) => {
          const why = unavailable(t, ctx);
          return (
            <button key={t} type="button" className="ptw-scn-b" aria-pressed={form.type === t} disabled={!!why} title={why ?? undefined} data-type={t}
              onClick={() => onForm({ ...form, type: t, ...defaultsFor(t, form, ctx) })}>{SCENARIO_LABEL[t]}</button>
          );
        })}
      </div>
      <div className="ptw-params">
        {spec.factor && <ScrubField label={form.type === 'surge' ? 'Arrivals' : form.type === 'crane_scale' ? 'Crane rate' : form.type === 'strike' ? 'Service rate' : 'Evacuation'} field="factor"
          value={form.factor} onChange={(v) => set({ factor: v })} step={spec.factor.step} min={spec.factor.min} max={spec.factor.max} unit={'×'} digits={1} />}
        {spec.group !== 'none' && (
          <label className="ptw-field ptw-field--row">
            <span className="ptw-field-l">Group</span>
            <select className="ptw-select" value={form.group} onChange={(e) => set({ group: e.target.value, berths: spec.berths ? 1 : 0 })} aria-label="Berth group" data-bad={bad('group') || undefined}>
              {spec.group === 'optional' && <option value="">All groups</option>}
              {spec.group === 'one' && !form.group && <option value="">Pick</option>}
              {ctx.groups.map((g) => <option key={g.id} value={g.id}>{g.name} ({g.berths})</option>)}
            </select>
          </label>
        )}
        {spec.berths && <ScrubField label="Berths shut" value={form.berths} onChange={(v) => set({ berths: Math.round(v) })} step={1} min={0} max={group?.berths ?? 1} digits={0} unit={group ? `of ${group.berths}` : undefined} />}
        <ScrubField label="Start" value={form.startH} onChange={(v) => set({ startH: v })} step={1} min={0} max={ctx.days * 24} digits={0} unit="h" />
        <ScrubField label="Duration" value={form.durationH} onChange={(v) => set({ durationH: v })} step={1} min={1} max={ctx.days * 24} digits={0} unit="h" />
        <ScrubField label="Run length" value={days} onChange={(v) => onDays(Math.round(v))} step={1} min={1} max={30} digits={0} unit="days" />
      </div>
      {issues.length > 0 && <div className="ptw-kn">{[...new Set(issues.map((i) => i.word))].map((w) => <Tag key={w} state="notserved" word={labelWord(w)} />)}</div>}
      <div className="ptw-run">
        <Toggle label="Range" checked={withRange} onChange={onRange} />
        <span className="ptw-tr-spacer" />
        <Button variant="secondary" size="sm" onClick={onBaseline} disabled={busy || !canRun} icon="history">Baseline</Button>
        <Button variant="primary" size="sm" onClick={onRun} disabled={busy || !canRun || issues.length > 0} icon="play" data-testid="run">{busy ? 'Running' : 'Run'}</Button>
      </div>
      {error && <div className="ptw-kn" role="alert"><Tag state="down" word={error.word}/><span className="ptw-reason">{error.reason}</span></div>}
    </InspectorSection>
  );
}

/** When the scenario type changes, keep the window and swap the factor / group defaults. */
function defaultsFor(t: ScenarioType, f: ScenarioForm, ctx: ScenarioCtx): Partial<ScenarioForm> {
  const biggest = [...ctx.groups].sort((a, b) => b.berths - a.berths || (a.id < b.id ? -1 : 1))[0];
  const factor = { surge: 2, berth_closure: 1, crane_scale: 0.5, strike: 0, channel_closure: 1, yard_outage: 0.3 }[t];
  return { factor, group: t === 'berth_closure' ? (ctx.groups.some((g) => g.id === f.group) ? f.group : biggest?.id ?? '') : '', berths: t === 'berth_closure' ? Math.max(1, Math.ceil((biggest?.berths ?? 1) / 2)) : 0 };
}
