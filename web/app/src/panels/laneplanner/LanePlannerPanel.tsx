import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { LiveStatus } from '../../ui/LiveStatus';
import { ExportMenu } from '../../charts/ExportMenu';
import { VNodes } from '../../charts/VNodeView';
import { Panel } from '../../components/Panel';
import { useStore } from '../../store';
import { Button, IconButton, Toggle } from '../../ui';
import { Chip, MetaChip, StateLine } from '../reports/chips';
import { MapPreview } from './MapPreview';
import { PlacePicker } from './PlacePicker';
import { fetchScenarios, postPlan } from './client';
import { laneServerExport, runLaneExport } from './exportChart';
import {
  buildRangeChart, buildTimeline, chipFor, clampDuration, csvRows, planBody, variantWord, dPlus, fmtD, fmtEnd, fmtKm, fmtPct, fmtRange, fmtSigned, NOT_SERVED,
  approxLegCount, hasLaneBasis, laneView, placeChoices, planTitle, refusalWord, stateChip, tableRows, type RangeChart,
} from './logic';
import type { PlanDoc, PlanResult, Scenario } from './types';
import '../../charts/charts.css';
import './lane.css';
import { labelWord } from '../../copy';
import { ProvenanceCell } from '../../provenance/ProvenanceCell';
import { laneCheckProvenance, lanePlanProvenance, laneWord, type CheckField, type LaneField } from '../../provenance/registry';
import { useLaneExportables } from '../../exports/adapters';
import { useLaneReport } from './exportReport';
import { ExportButton, useExportContextMenu } from '../../exports/PanelExportMenu';

/* LANE PLANNER (LNE). Origin and destination, a disruption (none or a library
 * scenario with its duration), and the candidate routes as RANGES: base sea-lane
 * days from leg geometry plus the queue wait ENGINE REV 2 returns under the
 * scenario, each bound built from named deterministic variants. Every number
 * wears its label; no accuracy is claimed. Sections whose feed is down are
 * hidden; what the server cannot serve reads NOT SERVED. */

const DEFAULT_ORIGIN = 'sea_shanghai';
const DEFAULT_DEST = 'sea_rotterdam';
const DEFAULT_SCENARIO = 'suez-closure';

function useWidth(ref: React.RefObject<HTMLElement>, fallback = 520): number {
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => { const x = Math.floor(el.getBoundingClientRect().width); if (x > 0) setW(x); };
    read();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

type Phase = { s: 'idle' } | { s: 'loading' } | { s: 'done'; r: PlanResult };

export function LanePlannerPanel() {
  const st = useStore();
  const places = useMemo(() => placeChoices(st.graph.nodes), [st.graph.nodes]);
  const [scenarios, setScenarios] = useState<Scenario[] | null | undefined>(undefined);
  const [origin, setOrigin] = useState<string | null>(DEFAULT_ORIGIN);
  const [dest, setDest] = useState<string | null>(DEFAULT_DEST);
  const [scenarioId, setScenarioId] = useState<string | null>(DEFAULT_SCENARIO);
  const [durText, setDurText] = useState('14');
  const duration = clampDuration(durText, 14);
  const [loss, setLoss] = useState<number | null>(null);
  const [extra, setExtra] = useState(true);
  const [includeEstimated, setIncludeEstimated] = useState(true);
  const [phase, setPhase] = useState<Phase>({ s: 'idle' });
  const [selected, setSelected] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const started = useRef(false);

  useEffect(() => {
    const ctrl = new AbortController();
    void fetchScenarios(ctrl.signal).then((s) => setScenarios(s));
    return () => ctrl.abort();
  }, []);
  const scn = scenarios?.find((s) => s.id === scenarioId) ?? null;
  const library = scenarios ?? [];
  const showDisruption = scenarios != null && scenarios.length > 0;

  const run = useCallback(async (o: string | null, d: string | null, sid: string | null, dur: number, ls: number | null, ex: boolean, est: boolean, lib: readonly Scenario[]) => {
    if (!o || !d) return;
    abort.current?.abort();
    const ctrl = new AbortController();
    abort.current = ctrl;
    setPhase({ s: 'loading' });
    const body = planBody({ origin: o, destination: d, extra: ex, includeEstimated: est, choice: { scenarioId: sid, duration: dur, loss: ls } }, lib);
    const r = await postPlan(body, ctrl.signal);
    if (ctrl.signal.aborted) return;
    setSelected(null);
    setPhase({ s: 'done', r });
  }, []);

  /* the first plan runs once the scenario library has answered (or failed) */
  useEffect(() => {
    if (started.current || scenarios === undefined) return;
    started.current = true;
    const sid = scenarios && scenarios.some((s) => s.id === DEFAULT_SCENARIO) ? DEFAULT_SCENARIO : null;
    setScenarioId(sid);
    void run(DEFAULT_ORIGIN, DEFAULT_DEST, sid, 14, null, true, true, scenarios ?? []);
  }, [scenarios, run]);
  useEffect(() => () => abort.current?.abort(), []);

  const onScenario = (id: string) => {
    const next = id || null;
    setScenarioId(next);
    const s = library.find((x) => x.id === next);
    if (s) { setDurText(String(s.default_duration_d)); setLoss(s.kind === 'closure' ? null : s.default_capacity_loss ?? null); }
  };
  const submit = () => void run(origin, dest, scenarioId, duration, loss, extra, includeEstimated, library);
  const swap = () => { setOrigin(dest); setDest(origin); };

  const doc = phase.s === 'done' && phase.r.kind === 'ok' ? phase.r.doc : null;
  const stamp = doc ? doc.engine_label : phase.s === 'loading' ? 'Planning' : 'No plan';

  return (
    <Panel id="laneplanner" code="LNE" title="Lane planner" updated={stamp} className="lp-panel"
      exportSpec={{ csv: doc ? () => csvRows(laneView(doc)) : undefined, json: doc ? () => doc : undefined }}>
      <div className="lp-root" onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(); } }}>
        <LiveStatus text={phase.s === 'loading' ? 'Planning' : phase.s === 'done' ? (phase.r.kind === 'ok' ? 'Plan ready' : phase.r.kind === 'notserved' ? 'Not served' : phase.r.kind === 'down' ? 'Down' : 'Request refused') : ''} />
        <div className="lp-controls" role="group" aria-label="Plan">
          <PlacePicker label="Origin" value={origin} choices={places} onChange={setOrigin} />
          <IconButton icon="arrow-left-right" label="Swap" size="sm" onClick={swap} className="lp-swap" />
          <PlacePicker label="Destination" value={dest} choices={places} onChange={setDest} />
          {showDisruption && (
            <div className="lp-field lp-field--sel">
              <label htmlFor="lp-scn" className="lp-lab">Disruption</label>
              <select id="lp-scn" className="lp-input" value={scenarioId ?? ''} onChange={(e) => onScenario(e.target.value)}>
                <option value="">None</option>
                {library.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
              </select>
            </div>
          )}
          {showDisruption && scn && (
            <div className="lp-field lp-field--num">
              <label htmlFor="lp-dur" className="lp-lab">Duration</label>
              <span className="lp-unit"><input id="lp-dur" className="lp-input" type="number" min={1} max={3650} step={1} value={durText} list="lp-dur-opts"
                onChange={(e) => setDurText(e.target.value)} onBlur={() => setDurText(String(duration))} /><i>d</i></span>
              {scn.duration_options_d && <datalist id="lp-dur-opts">{scn.duration_options_d.map((d) => <option key={d} value={d} />)}</datalist>}
            </div>
          )}
          {showDisruption && scn && scn.kind !== 'closure' && scn.capacity_loss_options && (
            <div className="lp-field lp-field--num">
              <label htmlFor="lp-loss" className="lp-lab">Capacity loss</label>
              <select id="lp-loss" className="lp-input" value={loss ?? scn.default_capacity_loss ?? ''} onChange={(e) => setLoss(Number(e.target.value))}>
                {scn.capacity_loss_options.map((l) => <option key={l} value={l}>{Math.round(l * 100)}%</option>)}
              </select>
            </div>
          )}
          <div className="lp-field lp-field--tog"><Toggle checked={extra} onChange={setExtra} label="Extra legs" /></div>
          <div className="lp-field lp-field--tog" title="Nearest core place"><Toggle checked={includeEstimated} onChange={setIncludeEstimated} label="Include estimated places" /></div>
          <Button variant="primary" icon="route" keys="Ctrl+Enter" onClick={submit} disabled={!origin || !dest || phase.s === 'loading'}>Plan lane</Button>
        </div>
        <Result phase={phase} places={places} selected={selected} onSelect={setSelected} origin={origin}
          onSubstitute={(which, id) => { if (which === 'origin') setOrigin(id); else setDest(id); }} onRetry={submit} />
      </div>
    </Panel>
  );
}

export function Result({ phase, places, selected, onSelect, onSubstitute, onRetry, origin }: {
  phase: Phase; places: ReturnType<typeof placeChoices>; selected: string | null; onSelect: (id: string | null) => void; origin: string | null;
  onSubstitute: (which: 'origin' | 'destination', id: string) => void; onRetry: () => void;
}) {
  if (phase.s === 'idle') return <StateLine chip="NO DATA" word="No plan" />;
  if (phase.s === 'loading') return <StateLine chip="INFO" word="Planning" />;
  const r = phase.r;
  if (r.kind === 'notserved') return <StateLine chip="NOT SERVED" word="Not served" code="/lanes/plan" />;
  if (r.kind === 'down') return <StateLine chip="DOWN" word="Down" code={r.code} onRetry={onRetry} />;
  if (r.kind === 'refused') {
    const nb = Array.isArray(r.refusal.core_neighbours) ? (r.refusal.core_neighbours as Array<{ id: string; name: string }>) : [];
    const pid = typeof r.refusal.place_id === 'string' ? r.refusal.place_id : null;
    const name = places.find((p) => p.id === pid)?.name ?? pid;
    return (
      <div className="lp-refusal" role="status" data-testid="lp-refusal">
        <Chip chip="FLAG" word={refusalWord(r.code)} />
        {name && <MetaChip>{name}</MetaChip>}
        {nb.length > 0 && <ul className="lp-nbrs" aria-label="Core places">{nb.map((n) => (
          <li key={n.id}><button type="button" className="rk-btn" onClick={() => onSubstitute(pid && pid === origin ? 'origin' : 'destination', n.id)}>{n.name}</button></li>
        ))}</ul>}
        <span className="rk-code mono">{r.code}</span>
      </div>
    );
  }
  return <PlanView doc={r.doc} places={places} selected={selected} onSelect={onSelect} />;
}

function PlanView({ doc: served, places, selected, onSelect }: { doc: PlanDoc; places: ReturnType<typeof placeChoices>; selected: string | null; onSelect: (id: string | null) => void }) {
  /* the lane-based figures (when the server has the lane geometry) replace the chord-based ones in the view */
  const doc = useMemo(() => laneView(served), [served]);
  const approxLegs = approxLegCount(doc);
  const rangeHost = useRef<HTMLDivElement>(null);
  const tlHost = useRef<HTMLDivElement>(null);
  const rw = useWidth(rangeHost); const tw = useWidth(tlHost, 760);
  const range = useMemo(() => buildRangeChart(doc, rw, selected), [doc, rw, selected]);
  const timeline = useMemo(() => buildTimeline(doc, tw, selected), [doc, tw, selected]);
  useLaneExportables(doc, range, doc.routes.some((r) => r.timeline.length > 0) ? timeline : null);
  useLaneReport(doc);
  const tableMenu = useExportContextMenu('laneplanner:table');
  const rows = useMemo(() => tableRows(doc), [doc]);
  const hasQueue = !!doc.disruption;
  const routeById = useMemo(() => new Map(doc.routes.map((x) => [x.id, x])), [doc]);
  const prov = (r: ReturnType<typeof tableRows>[number], field: LaneField, display: string) => () => lanePlanProvenance({
    doc, route: routeById.get(r.key) ?? null, label: r.label, field, display,
    word: field === 'queue' || field === 'wait' ? 'SIMULATED' : laneWord(String(r.chip)),
  });
  const overall = doc.routes.some((r) => r.queue && r.queue.high > 0) ? 'SIMULATED' : 'MODELLED';
  const flagged = doc.checks.filter((c) => c.flag);
  const unserved = Object.entries(doc.states).filter(([, v]) => v !== 'SERVED' && v !== 'OFF');
  const server = useMemo(() => laneServerExport(doc), [doc]);
  const exportRange = useCallback((kind: Parameters<typeof runLaneExport>[0], skin: Parameters<typeof runLaneExport>[1]) =>
    runLaneExport(kind, skin, { doc, title: `${planTitle(doc)} transit days`, kind: 'range', chart: range }, server), [doc, range, server]);
  const exportTimeline = useCallback((kind: Parameters<typeof runLaneExport>[0], skin: Parameters<typeof runLaneExport>[1]) =>
    runLaneExport(kind, skin, { doc, title: `${planTitle(doc)} voyage`, kind: 'timeline', chart: timeline }, null), [doc, timeline]);
  return (
    <div className="lp-result" data-testid="lp-result">
      <header className="lp-head">
        <h2 className="lp-title">{planTitle(doc)}</h2>
        <Chip chip={overall} />
        <MetaChip>{doc.engine_label}</MetaChip>
        {doc.disruption && <MetaChip>{`${doc.disruption.name} · ${doc.disruption.duration_d} d`}</MetaChip>}
        {hasLaneBasis(doc) && <MetaChip>Lane geometry</MetaChip>}
        {approxLegs > 0 && <MetaChip>{`Approximate legs ${approxLegs}`}</MetaChip>}
        {doc.shell_access && <Chip chip="MODELLED" word="Access leg" title="Nearest core place" />}
        {doc.shell_access && <Chip chip="NOT SERVED" word="Estimated-place wait" />}
        {flagged.map((c) => <Chip key={c.id} chip="FLAG" word={c.state} title={c.id} />)}
        {unserved.map(([k]) => <Chip key={k} chip="NOT SERVED" word={labelWord(k)} />)}
      </header>
      <div className="lp-grid">
        <section className="lp-sec lp-sec--map" aria-label="Routes">
          <MapPreview doc={doc} selected={selected} onSelect={onSelect} backdrop={places} />
        </section>
        <section className="lp-sec lp-sec--range" aria-label="Transit time range">
          <div className="lp-sech">
            <span className="lp-st">Transit time</span><Chip chip="INFO" word="Range" />
            <span className="lp-spacer" />
            <ExportMenu onRun={exportRange} hasLink={false} server={server} />
            <ExportButton itemId="laneplanner:table" label="Export table" />
          </div>
          <div ref={rangeHost} className="lp-chart" data-testid="lp-range">
            <ChartSvg chart={range} label="Transit time range chart" />
          </div>
          <div className="lp-tablewrap" onContextMenu={tableMenu.onContextMenu}>{tableMenu.menu}
            <table className="lp-table" data-testid="lp-table">
              <thead><tr>
                <th scope="col">Option</th><th scope="col" className="num">Median (d)</th><th scope="col" className="num">Range (d)</th><th scope="col" className="num">Against baseline (d)</th>
                {hasQueue && <th scope="col" className="num">Queue peak (d)</th>}{hasQueue && <th scope="col" className="num">Wait at arrival (d)</th>}<th scope="col" className="num">Length (km)</th><th scope="col">Basis</th>
              </tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.key} className={`${selected === r.key ? 'on' : ''}${r.baseline ? ' base' : ''}`} tabIndex={0}
                    onClick={() => onSelect(r.key === 'baseline' ? null : r.key)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(r.key === 'baseline' ? null : r.key); } }}>
                    <th scope="row"><i className="lp-sw" style={{ background: r.color }} />{r.label}</th>
                    <td className="num"><ProvenanceCell make={prov(r, 'median', `${fmtD(r.median)} d`)}>{fmtD(r.median)}</ProvenanceCell></td>
                    <td className="num"><ProvenanceCell make={prov(r, 'range', `${fmtRange(r.range)} d`)}>{fmtRange(r.range)}</ProvenanceCell></td>
                    <td className="num">{r.vs == null ? '—' : <ProvenanceCell make={prov(r, 'vs', `${fmtSigned(r.vs)} d`)}>{fmtSigned(r.vs)}</ProvenanceCell>}</td>
                    {hasQueue && <td className="num">{r.baseline ? '—' : r.queue ? <ProvenanceCell make={prov(r, 'queue', `${fmtD(r.queue.median)} (${fmtRange(r.queue)}) d`)}>{`${fmtD(r.queue.median)} (${fmtRange(r.queue)})`}</ProvenanceCell> : r.queueState === 'not_served' ? NOT_SERVED : '0.0'}</td>}
                    {hasQueue && <td className="num">{r.baseline ? '—' : r.arrival ? (r.arrival.high > 0 ? `${fmtD(r.arrival.median)} (${fmtRange(r.arrival)})` : '0.0') : NOT_SERVED}</td>}
                    <td className="num"><ProvenanceCell make={prov(r, 'km', `${fmtKm(r.km)} km`)}>{fmtKm(r.km)}</ProvenanceCell></td>
                    <td><Chip chip={r.chip} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
      {doc.routes.some((r) => r.timeline.length > 0) && (
        <section className="lp-sec" aria-label="Voyage">
          <div className="lp-sech">
            <span className="lp-st">Voyage</span><Chip chip="INFO" word="Timeline" />
            {doc.disruption && <MetaChip>{`Closure ${dPlus(0)} to ${dPlus(doc.disruption.end_d.median)}`}</MetaChip>}
            {doc.disruption && <MetaChip>{`Range ${dPlus(doc.disruption.end_d.low)} to ${dPlus(doc.disruption.end_d.high)}`}</MetaChip>}
            <span className="lp-spacer" />
            <ExportMenu onRun={exportTimeline} hasLink={false} />
          </div>
          <div ref={tlHost} className="lp-chart" data-testid="lp-timeline"><ChartSvg chart={timeline} label="Voyage timeline chart" /></div>
        </section>
      )}
      <Checks doc={doc} />
      <Variants doc={doc} />
      <Sources doc={doc} />
    </div>
  );
}

function ChartSvg({ chart, label }: { chart: Pick<RangeChart, 'nodes' | 'width' | 'height'>; label: string }) {
  return <svg className="lp-svg" width={chart.width} height={chart.height} viewBox={`0 0 ${chart.width} ${chart.height}`} role="img" aria-label={label}><VNodes nodes={chart.nodes} /></svg>;
}

function Checks({ doc }: { doc: PlanDoc }) {
  if (doc.checks.length === 0) return null;
  const pair = doc.checks.find((c) => c.id === 'cape_vs_suez');
  const floor = doc.checks.find((c) => c.id === 'great_circle_floor');
  const cell = (check: typeof doc.checks[number], field: CheckField, display: string, lanePair = false) => (
    <ProvenanceCell make={() => laneCheckProvenance({ doc, check, field, display, lanePair })}>{display}</ProvenanceCell>
  );
  return (
    <section className="lp-sec" aria-label="Distance check" data-testid="lp-checks">
      <div className="lp-sech"><span className="lp-st">Distance check</span><Chip chip="SOURCED" word="Reference" /></div>
      <table className="lp-table lp-table--checks">
        <thead><tr><th scope="col">Check</th><th scope="col" className="num">Suez (km)</th><th scope="col" className="num">Cape (km)</th><th scope="col" className="num">Detour (km)</th><th scope="col" className="num">Cut (%)</th><th scope="col" className="num">Cited (%)</th><th scope="col">State</th></tr></thead>
        <tbody>
          {pair && (
            <tr className={pair.flag ? 'flag' : ''}>
              <th scope="row">Cape vs Suez</th>
              <td className="num">{pair.engine?.suez_km == null ? fmtKm(undefined) : cell(pair, 'suez', `${fmtKm(pair.engine.suez_km)} km`)}</td>
              <td className="num">{pair.engine?.cape_km == null ? fmtKm(undefined) : cell(pair, 'cape', `${fmtKm(pair.engine.cape_km)} km`)}</td>
              <td className="num">{pair.engine?.detour_km == null ? fmtKm(undefined) : cell(pair, 'detour', `${fmtKm(pair.engine.detour_km)} km`)}</td>
              <td className="num">{pair.engine?.cut_pct == null ? fmtPct(undefined) : cell(pair, 'cut', fmtPct(pair.engine.cut_pct))}</td>
              <td className="num">{pair.reference ? cell(pair, 'cited', `${pair.reference.cut_pct.lo.toFixed(1)} to ${pair.reference.cut_pct.hi.toFixed(1)} %`) : NOT_SERVED}</td>
              <td><Chip chip={stateChip(pair.state)} word={pair.state} /></td>
            </tr>
          )}
          {pair?.lane?.sea_routes_pair && (
            <tr>
              <th scope="row">Cape vs Suez, lane pair</th>
              <td className="num">{cell(pair, 'suez', `${fmtKm(pair.lane.sea_routes_pair.suez_nm * 1.852)} km`, true)}</td><td className="num">{cell(pair, 'cape', `${fmtKm(pair.lane.sea_routes_pair.cape_nm * 1.852)} km`, true)}</td>
              <td className="num">{cell(pair, 'detour', `${fmtKm((pair.lane.sea_routes_pair.cape_nm - pair.lane.sea_routes_pair.suez_nm) * 1.852)} km`, true)}</td>
              <td className="num">{cell(pair, 'cut', fmtPct(pair.lane.sea_routes_pair.cut_pct), true)}</td><td className="num">—</td>
              <td><Chip chip="MODELLED" /></td>
            </tr>
          )}
          {floor && (
            <tr className={floor.flag ? 'flag' : ''}>
              <th scope="row">Great circle floor</th>
              <td className="num" colSpan={4}>{floor.engine?.great_circle_km == null ? fmtKm(undefined) : cell(floor, 'floor', `${fmtKm(floor.engine.great_circle_km)} km`)}</td><td className="num">—</td>
              <td><Chip chip={stateChip(floor.state)} word={floor.state} /></td>
            </tr>
          )}
        </tbody>
      </table>
    </section>
  );
}

function Variants({ doc }: { doc: PlanDoc }) {
  return (
    <section className="lp-sec lp-sec--variants" aria-label="Variants" data-testid="lp-variants">
      <div className="lp-sech"><span className="lp-st">Variants</span><Chip chip="INFO" word="Named" /></div>
      <ul className="lp-chips">
        {doc.variants.sail.map((v) => <li key={v.id}><MetaChip title={v.id}>{`${variantWord(v.id)} ${v.speed_kn} kn`}</MetaChip></li>)}
        {doc.variants.queue.map((v) => <li key={v.id}><MetaChip title={`${labelWord(v.profile)} duration ×${v.duration_mult}`}>{variantWord(v.id)}</MetaChip></li>)}
        {doc.time_basis.leg_days != null && <li><MetaChip>{`Basis ${labelWord(doc.time_basis.kind)} ${fmtD(doc.time_basis.leg_days)} d`}</MetaChip></li>}
        {hasLaneBasis(doc) && <li><MetaChip>Lane nautical miles</MetaChip></li>}
      </ul>
    </section>
  );
}

function Sources({ doc }: { doc: PlanDoc }) {
  if (doc.sources.length === 0) return null;
  return (
    <section className="lp-sec lp-sec--sources" aria-label="Sources" data-testid="lp-sources">
      <div className="lp-sech"><span className="lp-st">Sources</span></div>
      <ul className="lp-chips">
        {doc.sources.map((s) => <li key={s.id}><Chip chip={chipFor(s.state)} word={s.label} title={s.detail ?? s.id} /></li>)}
      </ul>
    </section>
  );
}
