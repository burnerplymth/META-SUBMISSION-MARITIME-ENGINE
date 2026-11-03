import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Panel } from '../components/Panel';
import { MethodDisclosure } from './MethodDisclosure';
import { Icon } from '../ui/Icon';
import { Status } from '../ui/Status';
import { SimLineageChip } from './history/lineageChip';
import { KINDS, nodeName } from '../seed';
import { fmtDayTime, nodeLabel, nodeNameOptions, prettyKind, resolveNodeInput } from '../format';
import { anyFeedLive, runWord } from '../shell/truthLabel';
import { deskTabView } from '../deskMode';
import { defaultSimRequest, useDeskStore, useSimStore, type SimPanelKey } from '../store';
import type { SimState } from '../../../map/src/sim/types';
import type { SimVehicleDetail, SimVehiclePriors } from '../api';
import {
  GRANULARITIES, MARKER_KIND_LABEL, TICK_SPECS, computeVolumeAtRisk,
  deriveStatusBoard, effectivePanelView, fmtLate, fmtSimDayTime, markerFraction,
  parseLaneKey, placeLabel, queueChartRows, simClockReadout, tierSplit,
  type SimGranularity, type SimPlaceInfo,
} from '../../../map/src/sim/phaseD';
import type { SimMarker, SimTickSize } from '../../../map/src/sim/types';

/* The SIM control surface: mode switch,
 * run controls, timeline, layer stack, provenance,
 * volume at risk and the linkable per-place status boards.
 * Vocabulary: user-readable strings say places / legs /
 * routes / network — never the data model's own words. Everything
 * simulated is labelled SIMULATED; every derived figure is MODELLED. */

export const placesOf = (graph: { nodes: unknown[] }): Map<string, SimPlaceInfo> => {
  const m = new Map<string, SimPlaceInfo>();
  for (const n of graph.nodes as SimPlaceInfo[]) if (n && typeof n.id === 'string') m.set(n.id, n);
  return m;
};

const SPEEDS = [0.5, 1, 2, 4];

/* ── Mode switch: Live or Recorded / Replay / SIM ──────────── */

export function SimModeSwitch() {
  const s = useSimStore();
  const d = useDeskStore(); /* desk-slice fields only */
  const now = Date.now();
  /* LIVE only when the displayed chain earns it now; a recorded stack reads Recorded */
  const liveNow = d.runMeta && d.runMeta.mode === 'LIVE'
    ? runWord(d.runMeta, d.health, d.snapshotMode, now) === 'LIVE'
    : anyFeedLive(d.health, d.snapshotMode);
  const view = deskTabView(s.simActive, d.runMeta?.mode, liveNow);
  const mode = view.selected;
  return (
    <span className="seg sim-modes" role="group" aria-label="Desk mode">
      <button type="button" className={`seg-b${mode === 'LIVE' ? ' on' : ''}`} aria-pressed={mode === 'LIVE'} onClick={() => void s.showDeskMode('LIVE')}
        title={view.liveWord === 'Live' ? 'Live desk' : 'Latest recorded disruption'}>{view.liveWord}</button>
      <button type="button" className={`seg-b${mode === 'CANNED' ? ' on' : ''}`} aria-pressed={mode === 'CANNED'} onClick={() => void s.showDeskMode('CANNED')}
        title="Stored replay">Replay</button>
      <button type="button" className={`seg-b sim-mode-sim${mode === 'SIM' ? ' on' : ''}`} aria-pressed={mode === 'SIM'}
        onClick={() => { if (!s.simActive) s.enterSimShell(); }}
        title="Simulation">SIM</button>
      {mode === 'SIM' && <span className="simulated-chip">Simulated</span>}
      {view.scenario && <span className="simulated-chip" title="Stated scenario">Scenario</span>}
    </span>
  );
}

/* ── Transport (shared by the SIM panel; the timeline carries its own) */

function Transport() {
  const s = useSimStore();
  if (!s.simRun) return null;
  return (
    <div className="sim-transport mono">
      <button onClick={() => s.setSimPlayhead(0)} title="Start" aria-label="Start"><Icon name="skip-back" /></button>
      <button onClick={() => s.simStepGranularity(-1)} title="Step back" aria-label="Step back"><Icon name="step-back" /></button>
      <button className="sim-play" onClick={s.simPlaying ? s.simPause : s.simPlay} aria-label={s.simPlaying ? 'Pause' : 'Play'}>
        <Icon name={s.simPlaying ? 'pause' : 'play'} /> {s.simPlaying ? 'Pause' : 'Play'}</button>
      <button onClick={() => s.simStepGranularity(1)} title="Step forward" aria-label="Step forward"><Icon name="step-forward" /></button>
      <span className="dim">Speed</span>
      {SPEEDS.map((v) => (
        <button key={v} className={s.simSpeed === v ? 'on' : ''} onClick={() => s.setSimSpeed(v)}>{v}×</button>
      ))}
      <span className="dim">Step</span>
      {GRANULARITIES.map((g) => (
        <button key={g} className={s.simGranularity === g ? 'on' : ''} onClick={() => s.setSimGranularity(g)}>{g}</button>
      ))}
    </div>
  );
}

/* ── Timeline bar (map foot, SIM mode only) ────────────────────── */

/** Timeline marker shapes, drawn (never a font glyph): a solid diamond is a disruption onset, an open diamond its expiry,
 *  a triangle the queue peak, a dot a congested arrival, a hexagon a chain event. Shape, not colour alone, tells them apart. */
const MARKER_SHAPE: Record<SimMarker['kind'], React.ReactNode> = {
  disruption_onset: <path d="M5 1l4 4-4 4-4-4z" fill="currentColor" />,
  disruption_expiry: <path d="M5 1.6l3.4 3.4L5 8.4 1.6 5z" fill="none" stroke="currentColor" strokeWidth="1.2" />,
  queue_peak: <path d="M5 1.5l4 7H1z" fill="currentColor" />,
  congested_arrival: <circle cx="5" cy="5" r="3.2" fill="currentColor" />,
  chain_event: <path d="M5 1l3.6 2v4L5 9 1.4 7V3z" fill="none" stroke="currentColor" strokeWidth="1.2" />,
};
const MarkerMark = ({ kind }: { kind: SimMarker['kind'] }) => (
  <svg className="sim-marker-shape" width="10" height="10" viewBox="0 0 10 10" aria-hidden="true" focusable="false" data-marker={kind}>{MARKER_SHAPE[kind]}</svg>
);

export function SimTimeline() {
  const s = useSimStore();
  const d = useDeskStore(); /* desk-slice fields only */
  const trackRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const lastSeek = useRef(0);

  /* chain sync: OFF by default (markers only). When ON, the
   * referenced chain is loaded through the existing path and the
   * selection follows the latest chain step at or before the playhead
   * — the existing read-only chain reveal follows the selection.
   * the synced chain lives in the SIM slice (openSimSyncedChain
   * → simSyncedChain) — the desk chain slice is never written by sync,
   * so the desk's chain survives SIM untouched at the source. */
  const chainRef = useMemo(
    () => (s.simMarkers ?? []).find((m) => m.kind === 'chain_event' && m.ref)?.ref ?? null,
    [s.simMarkers]);
  /* Every hook sits above the no-run early return below: a hook under
   * the return would make the no-run → run transition render more hooks
   * than the previous render and React would tear down the whole app
   * (error #310) the moment a run loaded while the map panel was mounted. */
  const places = useMemo(() => placesOf(d.graph), [d.graph]);
  useEffect(() => {
    if (!s.simChainSync || !chainRef || !s.simRun) return;
    if (!s.simSyncedChain || s.simSyncedChain.run_id !== chainRef) { void s.openSimSyncedChain(chainRef); return; }
    const t = Date.parse(s.simRun.journal.start_at) + s.simPlayhead * (s.simRun.tick_seconds ?? 86400) * 1000;
    const evs = s.simSyncedChain.events.filter((e) => Date.parse(e.at) <= t);
    const last = evs[evs.length - 1];
    if (last && d.selectedSeq !== last.seq) d.selectNode(last.node_id, last.seq);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s.simChainSync, chainRef, s.simPlayhead, s.simSyncedChain?.run_id]);

  if (!s.simRun) {
    return (
      <div className="sim-timeline sim-timeline--empty mono">
        <span className="simulated-chip">Simulated</span>
        <Status state="notserved" word="No run" />
      </div>
    );
  }
  const run = s.simRun;
  const ticks = run.ticks;
  const ro = simClockReadout(run, s.simPlayhead);
  const seekFromEvent = (e: React.PointerEvent) => {
    const el = trackRef.current; if (!el) return;
    const r = el.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    s.setSimPlayhead(frac * (ticks - 1));
  };
  const onMove = (e: React.PointerEvent) => {
    if (!dragging.current) return;
    const now = performance.now();
    if (now - lastSeek.current < 90) return;
    lastSeek.current = now;
    seekFromEvent(e);
  };
  const divisions = Math.min(ticks - 1, 48);
  return (
    <div className="sim-timeline mono">
      <div className="sim-tl-row">
        <button onClick={() => s.setSimPlayhead(0)} title="Start" aria-label="Start"><Icon name="skip-back" /></button>
        <button onClick={() => s.simStepGranularity(-1)} title="Step back" aria-label="Step back"><Icon name="step-back" /></button>
        <button className="sim-play" onClick={s.simPlaying ? s.simPause : s.simPlay} aria-label={s.simPlaying ? 'Pause' : 'Play'}>
          <Icon name={s.simPlaying ? 'pause' : 'play'} /> {s.simPlaying ? 'Pause' : 'Play'}</button>
        <button onClick={() => s.simStepGranularity(1)} title="Step forward" aria-label="Step forward"><Icon name="step-forward" /></button>
        <span className="sim-clock">
          <b>{ro.dateTime}</b> · {ro.tickLabel} · Tick {ro.tickIndex + 1} / {ticks}
          {ro.interpolated && <span className="interp-tag">Interpolated</span>}
        </span>
        <span className="spacer" />
        <span className="dim">Speed</span>
        {SPEEDS.map((v) => (
          <button key={v} className={s.simSpeed === v ? 'on' : ''} onClick={() => s.setSimSpeed(v)}>{v}×</button>
        ))}
        <span className="dim">Step</span>
        {GRANULARITIES.map((g) => (
          <button key={g} className={s.simGranularity === g ? 'on' : ''} onClick={() => s.setSimGranularity(g)}>{g}</button>
        ))}
        <label className="sim-sync" title="Follows playhead">
          <input type="checkbox" checked={s.simChainSync} onChange={(e) => s.setSimChainSync(e.target.checked)} />
          Sync chain</label>
      </div>
      <div className="sim-ruler" ref={trackRef}
        onPointerDown={(e) => { dragging.current = true; (e.target as Element).setPointerCapture?.(e.pointerId); seekFromEvent(e); }}
        onPointerMove={onMove}
        onPointerUp={(e) => { dragging.current = false; seekFromEvent(e); }}>
        <div className="sim-ruler-ticks" aria-hidden>
          {Array.from({ length: divisions + 1 }, (_, i) => (
            <span key={i} className={i % 6 === 0 ? 'tick-major' : undefined}
              style={{ left: `${(i / divisions) * 100}%` }} />
          ))}
        </div>
        {(s.simMarkers ?? []).map((m, i) => (
          <button key={`${m.kind}-${m.tick}-${i}`}
            className={`sim-marker sim-marker--${m.kind}`}
            style={{ left: `${markerFraction(m, ticks) * 100}%` }}
            title={`${MARKER_KIND_LABEL[m.kind]}${m.node_id ? ` — ${placeLabel(m.node_id, places)}` : ''} · ${fmtDayTime(m.at)}`}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => { s.setSimPlayhead(m.tick); if (m.node_id) d.selectNode(m.node_id); }}>
            <MarkerMark kind={m.kind} /></button>
        ))}
        <div className="sim-playhead" style={{ left: `${(s.simPlayhead / Math.max(1, ticks - 1)) * 100}%` }} aria-hidden />
      </div>
      <div className="sim-ruler-labels">
        <span>{fmtSimDayTime(Date.parse(run.journal.start_at))}</span>
        <span />
        <span>{run.end_at ? fmtSimDayTime(Date.parse(run.end_at)) : ''}</span>
      </div>
    </div>
  );
}

/* ── linkable sub-panel plumbing ─────────────────────────────── */

function usePanelView(key: SimPanelKey) {
  const s = useSimStore();
  const d = useDeskStore(); /* desk-slice fields only */
  const master = useMemo(
    () => ({ runId: s.simRun?.sim_run_id ?? '', playhead: s.simPlayhead, nodeId: d.selectedNode }),
    [s.simRun?.sim_run_id, s.simPlayhead, d.selectedNode]);
  const view = effectivePanelView(s.simLinks[key], master);
  const [state, setState] = useState<SimState | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!s.simRun) { setState(null); return; }
    if (!view.frozen) { setState(s.simStateExact); setLoading(false); return; }
    /* Frozen: the SAME bus source at the frozen tick (the store's
     * cached /sim/state path) — never a private timing loop. While the
     * frozen state is in flight the panel shows its loading state,
     * never the master's current state dressed as frozen. */
    setLoading(true);
    let dead = false;
    void s.fetchSimStateAt(view.tick).then((st) => { if (!dead) { setState(st); setLoading(false); } });
    return () => { dead = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view.frozen, view.tick, view.nodeId, s.simRun?.sim_run_id, s.simStateExact]);
  return { view, state, loading };
}

function LinkToggle({ panelKey, view }: { panelKey: SimPanelKey; view: { frozen: boolean; frozenAtLabel: string | null } }) {
  const s = useSimStore();
  return (
    <span className="link-wrap">
      {view.frozen && <span className="frozen-stamp mono">Frozen at {view.frozenAtLabel}</span>}
      <button className={`link-toggle mono ${view.frozen ? 'link-toggle--off' : ''}`}
        onClick={() => s.toggleSimLink(panelKey)}
        title={view.frozen
          ? 'Re-link'
          : 'Freeze'}>
        {view.frozen ? 'Unlinked' : 'Linked'}</button>
    </span>
  );
}

/* ── vehicle inspector ──────────────────────────────────
 * The map-picked vessel / aircraft, rendered from the vehicle route's
 * served detail (identity MODELLED throughout; state is the state
 * route's entry verbatim). It follows the MASTER timeline like the
 * boards: the detail is fetched at the floor tick (simStateExact's
 * tick — the source usePanelView reads) and refetched when the
 * selection, the floor tick or the run changes. While a refetch is in
 * flight the previous detail keeps showing ONLY when it is the same
 * vehicle's, stamped with ITS OWN sim_time — another tick's state is
 * never dressed as current (the boards' honesty rule). */

const VESSEL_CLASS_WORDS: Record<string, string> = {
  container: 'container vessel', bulk: 'bulk carrier', tanker: 'tanker',
  roro: 'ro-ro vessel', passenger: 'passenger vessel',
};
export const vesselClassWords = (cls: string): string => VESSEL_CLASS_WORDS[cls] ?? `${cls} vessel`;

const fmtInt = (n: number): string => n.toLocaleString('en-US');
const fmtSpeed = (v: number): string => String(Math.round(v * 10) / 10);
const fmtWxNum = (v: number): string => (Number.isInteger(v) ? String(v) : v.toFixed(1));

/** Class priors as RANGES — the server serves ranges only, so the
 *  line never collapses one to a point figure: "LOA 250–400 m · beam
 *  48–61 m · GT 95,000–235,000 · capacity 10,000–24,000 TEU" (the
 *  capacity segment is absent for passenger vessels). */
export function vesselPriorsLine(p: SimVehiclePriors): string {
  const parts = [
    `LOA ${fmtInt(p.loa_m[0])}–${fmtInt(p.loa_m[1])} m`,
    `beam ${fmtInt(p.beam_m[0])}–${fmtInt(p.beam_m[1])} m`,
    `GT ${fmtInt(p.gt[0])}–${fmtInt(p.gt[1])}`,
  ];
  if (p.capacity) parts.push(`capacity ${fmtInt(p.capacity.range[0])}–${fmtInt(p.capacity.range[1])} ${p.capacity.unit}`);
  return parts.join(' · ');
}

export function VehicleInspectorView({ detail, places, onClose }: {
  detail: SimVehicleDetail; places: Map<string, SimPlaceInfo>; onClose: () => void;
}) {
  const idn = detail.identity;
  const st = detail.state;
  const sea = idn.mode === 'sea';
  const title = sea ? (idn.vessel_name ?? 'Vessel') : (idn.tail ?? 'Aircraft');
  const sub = sea
    ? (idn.vessel_class ? vesselClassWords(idn.vessel_class) : 'vessel')
    : `${idn.aircraft_type ?? 'aircraft'}${idn.cls ? ` · ${idn.cls}` : ''}`;
  const fromName = placeLabel(idn.from, places);
  const toName = placeLabel(idn.to, places);
  const late = st ? fmtLate(st.late_min) : null;
  /* The leg segment currently in force: en route it is the state
   * entry's own lane toward its served destination; at a place it is
   * the segment departing that place (the rotation's next segment,
   * or the sea leg's other end). Null when nothing is served. */
  const seg = ((): { from: string; to: string } | null => {
    if (!st) return null;
    if (st.state === 'EN_ROUTE' && st.dest) {
      const parsed = parseLaneKey(st.lane);
      if (parsed) return { from: st.dest === parsed.to ? parsed.from : parsed.to, to: st.dest };
      return { from: idn.from, to: st.dest };
    }
    if (st.node) {
      if (!sea && idn.rotation && idn.rotation.length > 1) {
        const rot = idn.rotation;
        let i = rot.indexOf(st.node);
        if (i === rot.length - 1) i = 0;
        if (i >= 0) return { from: rot[i], to: rot[i + 1] };
      }
      if (sea) return { from: st.node, to: st.node === idn.from ? idn.to : idn.from };
    }
    return null;
  })();
  const rotation = !sea && idn.rotation && idn.rotation.length > 1 ? idn.rotation : null;
  return (
    <>
      <div className="sim-sub-head">
        <h5>{title}</h5>
        <span className="simulated-chip">Simulated</span>
        <button className="btn veh-close" onClick={onClose}
          title="Clear" aria-label="Clear the vehicle selection"><Icon name="close" size="xs" /></button>
      </div>
      <p className="veh-sub mono dim">{sub} · Identity modelled</p>
      {st === null && (
        <>
          <p className="dim">{/not departed/i.test(detail.state_reason ?? '') ? 'Not departed' : 'No state'}</p>
          <p className="fineprint mono">At {fmtDayTime(detail.sim_time)}</p>
        </>
      )}
      {st !== null && (
        <>
          <dl className="kv">
            <dt>State</dt>
            <dd>{st.state.replace(/_/g, ' ').toLowerCase()}
              {st.diverted && <span className="divert-chip mono">Diverted · Modelled</span>}</dd>
            <dt>Position</dt>
            <dd className="mono">{st.node
              ? placeLabel(st.node, places)
              : typeof st.lat === 'number' && typeof st.lon === 'number'
                ? `${st.lat.toFixed(3)}, ${st.lon.toFixed(3)}`
                : '—'}</dd>
            <dt>Leg</dt><dd>{fromName} → {toName}</dd>
            {st.dest && <><dt>Destination</dt><dd>{placeLabel(st.dest, places)}</dd></>}
            {st.eta_at && <><dt>ETA</dt><dd className="mono">{fmtDayTime(st.eta_at)}</dd></>}
            {typeof st.bearing_deg === 'number' && (
              <><dt>Bearing</dt><dd className="mono">{Math.round(st.bearing_deg)}°</dd></>)}
            <dt>Speed</dt>
            <dd className="mono">{sea
              ? `${fmtSpeed(idn.speed_kmh / 1.852)} kn · MODELLED`
              : `${fmtSpeed(idn.speed_kmh)} km/h · MODELLED`}</dd>
            {typeof st.queue_wait_h === 'number' && st.queue_wait_h > 0 && (
              <><dt>Queue wait</dt><dd className="mono">{st.queue_wait_h.toFixed(1)} h</dd></>)}
            {late !== null && (
              <><dt>{sea ? 'Leg lateness' : 'Rotation lateness'}</dt><dd className="mono">{late}</dd></>)}
          </dl>
          <h5>Itinerary · Modelled</h5>
          {sea && (
            <p className="board-row"><span>{fromName} <Icon name="arrow-left-right" size="xs" label="to and from" /> {toName}</span>
              <span className="dim mono">out-and-back{st.dest ? ` · heading to ${placeLabel(st.dest, places)}` : ''}</span></p>)}
          {!sea && idn.home_hub && (
            <p className="board-row"><span>Home hub</span><span className="mono">{placeLabel(idn.home_hub, places)}</span></p>)}
          {!sea && rotation && (
            <p className="board-row"><span>Rotation</span>
              <span className="mono">{rotation.map((nodeId, i) => {
                const segHere = seg !== null && i > 0 && seg.from === rotation[i - 1] && seg.to === nodeId;
                const nodeHere = seg !== null
                  && ((i > 0 && seg.from === rotation[i - 1] && seg.to === nodeId)
                    || (i < rotation.length - 1 && seg.from === nodeId && seg.to === rotation[i + 1]));
                return (
                  <React.Fragment key={i}>
                    {i > 0 && <span className={segHere ? 'veh-cur' : undefined}> → </span>}
                    <span className={nodeHere ? 'veh-cur' : undefined}>{placeLabel(nodeId, places)}</span>
                  </React.Fragment>
                );
              })}</span></p>)}
          {sea && idn.vessel_priors && (
            <p className="fineprint">Class priors · Modelled · {vesselPriorsLine(idn.vessel_priors)}</p>)}
          {detail.weather
            ? <p className="fineprint">Wind {detail.weather.wind_kn !== null ? fmtWxNum(detail.weather.wind_kn) : '—'} kn
                {detail.weather.wave_m !== null ? ` · wave ${fmtWxNum(detail.weather.wave_m)} m` : ''}
                {' · '}{detail.weather_source ?? 'Snapshot'}</p>
            : <p className="fineprint">Weather not pinned</p>}
          <p className="fineprint mono">At {fmtDayTime(detail.sim_time)}</p>
        </>
      )}
    </>
  );
}

function VehicleInspector() {
  const s = useSimStore();
  const d = useDeskStore(); /* desk-slice fields only */
  const places = useMemo(() => placesOf(d.graph), [d.graph]);
  const vehicleId = s.simVehicleId;
  const runId = s.simRun?.sim_run_id ?? null;
  /* The MASTER floor tick — simStateExact's tick, the same source
   * usePanelView derives the boards from (simTick is its mirror). */
  const floorTick = s.simStateExact?.tick ?? s.simTick;
  const fetchAt = s.fetchSimVehicleAt;
  const [detail, setDetail] = useState<SimVehicleDetail | null>(null);
  useEffect(() => {
    if (!runId || !vehicleId) { setDetail(null); return; }
    let dead = false;
    void fetchAt(floorTick).then((det) => { if (!dead) setDetail(det); });
    return () => { dead = true; };
  }, [runId, vehicleId, floorTick, fetchAt]);
  if (!s.simRun) return null;
  /* A previous detail renders only while it is THIS run's and THIS
   * vehicle's — a different vehicle's detail never dresses as the
   * current selection while its fetch is in flight. */
  const shown = detail && detail.entity_id === vehicleId && detail.sim_run_id === runId ? detail : null;
  return (
    <div className="sim-sub veh">
      {!vehicleId && (
        <p className="dim">Select a vessel or aircraft</p>)}
      {vehicleId && !shown && <p className="dim">Loading · Vehicle state</p>}
      {vehicleId && shown && (
        <VehicleInspectorView detail={shown} places={places} onClose={() => s.selectSimVehicle(null)} />)}
    </div>
  );
}

/* ── status board ────────────────────────────────────────────── */

function StatusBoard() {
  const s = useSimStore();
  const d = useDeskStore(); /* desk-slice fields only */
  const { view, state, loading } = usePanelView('board');
  const places = useMemo(() => placesOf(d.graph), [d.graph]);
  if (!s.simRun) return null;
  const placeId = view.nodeId;
  const place = placeId ? places.get(placeId) : undefined;
  const board = state && placeId
    ? deriveStatusBoard({ state, run: s.simRun, placeId, places })
    : null;
  return (
    <div className="sim-sub">
      <div className="sim-sub-head"><h5>Status board</h5><LinkToggle panelKey="board" view={view} /></div>
      {!placeId && <p className="dim">Select a place</p>}
      {placeId && loading && <p className="dim">Loading · State</p>}
      {placeId && !loading && !state && <p className="dim">Not served</p>}
      {placeId && board && (
        <>
          <dl className="kv">
            <dt>Place</dt><dd className="amber">{nodeLabel(placeId, d.graph)}</dd>
            <dt>Time</dt><dd className="mono">{state ? fmtDayTime(state.sim_time) : '—'}{view.frozen ? ' · Frozen' : ''}</dd>
            <dt>Queue</dt><dd className="mono">{board.queue ?? '—'} waiting</dd>
            <dt>Berthed</dt><dd className="mono">{board.berthed ?? '—'}{board.berths !== null ? ` of ${board.berths} berths` : ''}</dd>
            {place?.mode === 'air' && <><dt>Departure queue</dt><dd className="mono">{board.departureQueue} on the ground awaiting departure</dd></>}
            <dt>Congestion</dt><dd>{board.congestion}</dd>
          </dl>
          {place?.kind === 'ground_node' && (
            <p className="fineprint">Ground · Corridor flow only</p>)}
          <h5>Present · Simulated{board.present.length > 12 ? ` · 12 of ${board.present.length}` : ''}</h5>
          {board.present.length ? board.present.slice(0, 12).map((p, i) => (
            <p key={i} className="board-row"><span>{p.label}{p.diverted && <span className="divert-chip mono">Diverted · Modelled</span>}</span><span className="dim mono">{p.clsLabel ? `${p.clsLabel} · ` : ''}{p.classLabel} · {p.state.replace(/_/g, ' ').toLowerCase()}{p.queueWaitH ? ` · waited ${p.queueWaitH.toFixed(1)} h` : ''}{fmtLate(p.lateMin) ? ` · rotation ${fmtLate(p.lateMin)}` : ''}</span></p>
          )) : <p className="dim">No vehicles present</p>}
          <h5>Inbound · Simulated{board.inbound.length > 12 ? ` · 12 of ${board.inbound.length}` : ''}</h5>
          {!board.inboundServed && <p className="dim">Arrivals not served</p>}
          {board.inboundServed && (board.inbound.length ? board.inbound.slice(0, 12).map((p, i) => (
            <p key={i} className="board-row"><span>{p.label}{p.diverted && <span className="divert-chip mono">Diverted · Modelled</span>}</span><span className="dim mono">{p.clsLabel ? `${p.clsLabel} · ` : ''}{p.classLabel} · arrives {p.etaAt ? fmtDayTime(p.etaAt) : '—'}{fmtLate(p.lateMin) ? ` · projected ${fmtLate(p.lateMin)}` : ''}{view.frozen ? ' · As frozen' : ''}</span></p>
          )) : <p className="dim">Nothing inbound</p>)}
          <h5>Goods at and inbound · Modelled</h5>
          {board.goods.length ? board.goods.map((g) => (
            <p key={`${g.unitClass}-${g.group}`} className="board-row"><span>{g.group} load</span>
              <span className="mono amber">{g.amount >= 100 ? Math.round(g.amount).toLocaleString('en-US') : g.amount.toFixed(1)} {g.unit}</span></p>
          )) : <p className="dim">No modelled load</p>}
          {board.goodsNotEstimated > 0 && (
            <p className="fineprint">{board.goodsNotEstimated} vehicle leg{board.goodsNotEstimated === 1 ? '' : 's'} · No throughput</p>)}
          <h5>Disruptions in force here</h5>
          {board.disruptions.length ? board.disruptions.map((d, i) => (
            <p key={i} className="board-row"><span>{prettyKind(d.kindLabel)}</span><span className="dim mono">Until {d.endsAt}</span></p>
          )) : <p className="dim">No disruption in force</p>}
        </>
      )}
    </div>
  );
}

/* ── Queue chart (linkable) ────────────────────────────────────────── */

function QueueChart() {
  const s = useSimStore();
  const d = useDeskStore(); /* desk-slice fields only */
  const { view, state, loading } = usePanelView('queue');
  const places = useMemo(() => placesOf(d.graph), [d.graph]);
  if (!s.simRun) return null;
  const rows = state ? queueChartRows(state, places) : [];
  const max = Math.max(1, ...rows.map((r) => r.queue));
  /* rule 5: on a tiered network this ranking covers
   * calibration-grade places only — the fineprint says so in words. */
  const tiered = [...places.values()].some((p) => typeof p.tier === 'string' && p.tier !== '');
  return (
    <div className="sim-sub">
      <div className="sim-sub-head"><h5>Queues · Simulated</h5><LinkToggle panelKey="queue" view={view} /></div>
      {loading && <p className="dim">Loading · State</p>}
      {!loading && state && (rows.length ? rows.map((r) => (
        <div key={r.placeId} className="qrow" onClick={() => d.selectNode(r.placeId)}>
          <span className="qrow-name">{r.label}</span>
          <span className="qrow-bar"><i style={{ width: `${(r.queue / max) * 100}%` }} /></span>
          <span className="mono amber">{r.queue}</span>
        </div>
      )) : <p className="dim">No queues</p>)}
      {!loading && state && <p className="fineprint mono">At {fmtDayTime(state.sim_time)}{view.frozen ? ' · Frozen' : ''} · longest queues first{tiered ? ' · Core places only' : ''}</p>}
    </div>
  );
}

/* ── Chain sub-panel (linkable, where applicable) ────────── */

function ChainSyncPanel() {
  const s = useSimStore();
  const d = useDeskStore(); /* desk-slice fields only */
  const { view } = usePanelView('chain');
  if (!s.simRun) return null;
  const chainMarkers = (s.simMarkers ?? []).filter((m) => m.kind === 'chain_event');
  const ref = chainMarkers.find((m) => m.ref)?.ref ?? null;
  const panelMs = Date.parse(s.simRun.journal.start_at) + view.tick * (s.simRun.tick_seconds ?? 86400) * 1000;
  /* the panel reads the SIM slice's synced chain — the desk
   * chain is a different run's property and never consulted here. */
  const events = s.simSyncedChain && ref && s.simSyncedChain.run_id === ref
    ? s.simSyncedChain.events.filter((e) => Date.parse(e.at) <= panelMs)
    : [];
  return (
    <div className="sim-sub">
      <div className="sim-sub-head"><h5>Chain</h5><LinkToggle panelKey="chain" view={view} /></div>
      {!ref && <p className="dim">No linked chain</p>}
      {ref && !s.simChainSync && (
        <p className="dim">{chainMarkers.length} chain marker{chainMarkers.length === 1 ? '' : 's'}</p>)}
      {ref && s.simChainSync && (!s.simSyncedChain || s.simSyncedChain.run_id !== ref) && (
        <p className="dim">Loading · Chain</p>)}
      {ref && s.simChainSync && s.simSyncedChain?.run_id === ref && (
        events.length ? events.slice(-6).map((e) => (
          <p key={e.seq} className="board-row"><span>{nodeLabel(e.node_id, d.graph)}</span>
            <span className="dim mono">{e.channel} · +{e.delay_d.toFixed(1)} d · {fmtDayTime(e.at)}</span></p>
        )) : <p className="dim">No chain steps yet</p>)}
    </div>
  );
}

/* ── volume at risk ────────────────────────────────────────────── */

function VolumeAtRisk() {
  const s = useSimStore();
  const d = useDeskStore(); /* desk-slice fields only */
  const places = useMemo(() => placesOf(d.graph), [d.graph]);
  const v = computeVolumeAtRisk({ state: s.simStateExact, baselineState: s.simBaselineState, places });
  if (!s.simRun) return null;
  return (
    <div className="sim-sub">
      <div className="sim-sub-head"><h5>Volume at risk · Modelled</h5></div>
      {s.simVarNote && <p className="fineprint">No baseline comparison</p>}
      {!v.available && (
        <p className="dim">Not available · {v.missing ?? 'Required input'}</p>)}
      {v.available && (v.lines.length ? v.lines.map((l) => {
        const unitWord = l.unit.replace(/\/day$/, '');
        return (
          <p key={l.unit} className="board-row"><span>{unitWord} at risk</span>
            <span className="mono amber">{Math.abs(l.value) >= 100 ? Math.round(l.value).toLocaleString('en-US') : l.value.toFixed(1)} {unitWord}</span></p>
        );
      }) : <p className="dim">None</p>)}
      {v.available && v.skippedNoThroughput > 0 && (
        <p className="fineprint">{v.skippedNoThroughput} core place{v.skippedNoThroughput === 1 ? '' : 's'} excluded · No throughput</p>)}
    </div>
  );
}

/* ── the building block ──────────────────────────────────────
 * Shown while a create-then-poll build is in flight on the server
 * (simBuilding set, no run loaded yet): the .honest idiom, the run id
 * in the short form used for hashes elsewhere (first 16 chars + …),
 * and an elapsed-seconds count ticking on a local 1 s interval —
 * visual only, no state leaves this component. */

export function SimBuildingNotice({ building }: { building: { simRunId: string; since: number } }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const elapsed = Math.max(0, Math.floor((now - building.since) / 1000));
  const short = building.simRunId.length > 16 ? `${building.simRunId.slice(0, 16)}…` : building.simRunId;
  return (
    <p className="honest">Building run <span className="mono">{short}</span> · {elapsed} s</p>
  );
}

/* ── The SIM panel ─────────────────────────────────────────────────── */

type DraftDisruption = { node_id: string; kind: string; capacity_loss: number; duration_d: number };

export function SimPanel() {
  const s = useSimStore();
  const d = useDeskStore(); /* desk-slice fields only */
  const run = s.simRun;
  const places = useMemo(() => placesOf(d.graph), [d.graph]);
  const [tickSize, setTickSize] = useState<SimTickSize>('day');
  /* The form opens on the demo configuration (day × 30); switching
   * tick size resets ticks to the default for that size. */
  const [ticks, setTicks] = useState(30);
  const [seed, setSeed] = useState(42);
  const [weather, setWeather] = useState(true);
  const [startLocal, setStartLocal] = useState(() => {
    const d = new Date(); d.setUTCMinutes(0, 0, 0);
    return d.toISOString().slice(0, 16);
  });
  const [drafts, setDrafts] = useState<DraftDisruption[]>([
    { node_id: 'chk_suez', kind: 'closure', capacity_loss: 1, duration_d: 10 },
  ]);
  const [dPlace, setDPlace] = useState('Suez Canal');
  const [dKind, setDKind] = useState('closure');
  const [dLoss, setDLoss] = useState(1);
  const [dDur, setDDur] = useState(10);
  const [liveId, setLiveId] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  const startIso = `${startLocal}:00Z`;
  const create = async () => {
    /* A run created from this panel's own form replaces any bridged
     * run — its live-feed seed note goes with the old run. */
    d.setSimSeedNote(null);
    setFormError(null);
    const spec = TICK_SPECS[tickSize];
    if (!Number.isInteger(ticks) || ticks < 1 || ticks > spec.maxTicks) {
      setFormError(`Ticks: whole number 1 to ${spec.maxTicks} for ${spec.label} ticks`);
      return;
    }
    const disruptions: Array<DraftDisruption | { disruption_id: string }> = [...drafts];
    const req = {
      disruptions: disruptions.map((d) => 'disruption_id' in d
        ? { disruption_id: (d as { disruption_id: string }).disruption_id }
        : { node_id: (d as DraftDisruption).node_id, kind: (d as DraftDisruption).kind, capacity_loss: (d as DraftDisruption).kind === 'closure' ? 1 : (d as DraftDisruption).capacity_loss, duration_d: (d as DraftDisruption).duration_d, started_at: startIso, shape: 'step' as const }),
      tick: tickSize, ticks, seed, start_at: startIso, weather,
    };
    const err = await s.createSimRun(req);
    if (err) setFormError(err);
  };
  const addScenario = () => {
    const nodeId = resolveNodeInput(dPlace, d.graph);
    setDrafts((d) => [...d, { node_id: nodeId, kind: dKind, capacity_loss: dKind === 'closure' ? 1 : dLoss, duration_d: dDur }]);
  };
  const addLive = () => {
    if (!liveId) return;
    setDrafts((d) => [...d, { disruption_id: liveId } as unknown as DraftDisruption]);
  };

  const split = run?.provenance.split;
  const tier = run ? tierSplit(run, places) : null;
  const weatherLine = !run ? null
    : run.weather
      ? `Weather data by Open-Meteo.com · ${run.weather.retrieved_at} · CC BY 4.0`
      : 'Weather · Not served';
  const updated = !s.simActive ? 'Desk mode'
    : run ? `Simulated · ${TICK_SPECS[run.journal.tick].label} ticks · ${run.ticks}`
    : 'No run';

  return (
    <Panel id="sim" code="SIM" title="Simulation — Living network" updated={updated}
      exportSpec={{ json: () => s.simRun }}>
      <SimModeSwitch />
      {run && <SimLineageChip journal={run.journal} />}
      {s.simBuilding && !run && <SimBuildingNotice building={s.simBuilding} />}
      {!s.simActive && (
        <div className="empty-state empty-state--act">
          <Status state="meta" word="SIM not open" meta={d.runMeta ? runWord(d.runMeta, d.health, d.snapshotMode, Date.now()) : undefined} />
          <button type="button" className="btn" onClick={() => s.enterSimShell()}>Open run</button>
        </div>)}
      {s.simActive && !run && (
        <>
          <p className="honest">No run</p>
          <button className="btn btn--amber" disabled={d.busy === 'sim'}
            onClick={() => { d.setSimSeedNote(null); void s.enterSim(defaultSimRequest()); }}>
            {d.busy === 'sim' ? 'Creating run' : 'Load default configuration'}</button>
        </>)}
      {s.simActive && (
        <>
          <h5>New run</h5>
          <div className="form-grid">
            <label>Tick size
              <span className="seg">
                {(Object.keys(TICK_SPECS) as SimTickSize[]).map((t) => (
                  <button key={t} type="button" className={`seg-b${tickSize === t ? ' on' : ''}`} aria-pressed={tickSize === t}
                    onClick={() => { setTickSize(t); setTicks(TICK_SPECS[t].defaultTicks); }}>{TICK_SPECS[t].letter}</button>
                ))}
              </span></label>
            <label>Ticks (max {TICK_SPECS[tickSize].maxTicks})<input type="number" min={1} max={TICK_SPECS[tickSize].maxTicks} value={ticks} onChange={(e) => setTicks(Number(e.target.value))} /></label>
            <label>Seed<input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} /></label>
            <label>Start (UTC)<input type="datetime-local" value={startLocal} onChange={(e) => setStartLocal(e.target.value)} /></label>
            <label>Weather snapshot<input type="checkbox" checked={weather} onChange={(e) => setWeather(e.target.checked)} /></label>
          </div>
          <p className="fineprint">H hour · D day · W week · M 30 days · Default {TICK_SPECS[tickSize].defaultTicks} ticks</p>
          <h5>Disruptions in this run</h5>
          {drafts.map((draft, i) => {
            const dd = draft as unknown as { disruption_id?: string; node_id?: string; kind?: string; duration_d?: number };
            const liveEv = dd.disruption_id ? d.feed.find((f) => f.id === dd.disruption_id) : undefined;
            return (
              <p key={i} className="board-row">
                <span>{dd.disruption_id
                  ? `${liveEv ? liveEv.headline : 'Feed disruption'} · Feed`
                  : `${nodeLabel(dd.node_id ?? '', d.graph)} · ${prettyKind(dd.kind ?? '').toLowerCase()} · ${dd.duration_d} d`}</span>
                <button className="btn" onClick={() => setDrafts((x) => x.filter((_, j) => j !== i))}>Remove</button>
              </p>
            );
          })}
          {!drafts.length && <p className="dim">No disruptions</p>}
          <div className="form-grid">
            <label>Place<input className="mono" value={dPlace} onChange={(e) => setDPlace(e.target.value)} list="sim-place-names" /></label>
            <label>Kind<select value={dKind} onChange={(e) => setDKind(e.target.value)}>{KINDS.map((k) => <option key={k} value={k}>{prettyKind(k)}</option>)}</select></label>
            <label>Capacity loss (fraction)<input type="number" step="0.05" min="0.05" max="1" value={dLoss} disabled={dKind === 'closure'} onChange={(e) => setDLoss(Number(e.target.value))} /></label>
            <label>Duration (days)<input type="number" step="1" min="1" value={dDur} onChange={(e) => setDDur(Number(e.target.value))} /></label>
          </div>
          <datalist id="sim-place-names">{nodeNameOptions(d.graph).map((n) => <option key={n} value={n} />)}</datalist>
          <button className="btn" onClick={addScenario}>Add scenario disruption</button>{' '}
          <div className="form-grid" style={{ marginTop: 7 }}>
            <label>Feed disruption
              <select value={liveId} onChange={(e) => setLiveId(e.target.value)}>
                <option value="">Select from feed</option>
                {d.feed.map((f) => <option key={f.id} value={f.id}>{f.headline} — {nodeName(f.node_id)}</option>)}
              </select></label>
          </div>
          <button className="btn" disabled={!liveId} onClick={addLive}>Add feed disruption</button>
          <button className="btn btn--amber" disabled={d.busy === 'sim'} onClick={() => void create()}>
            {d.busy === 'sim' ? 'Creating run' : 'Create run'}</button>
          {formError && <p className="alr-err mono">{formError}</p>}
        </>)}
      {s.simActive && run && (
        <>
          {d.simSeedNote && <p className="fineprint">{d.simSeedNote}</p>}
          <hr className="rule" />
          <h5>Transport</h5>
          <Transport />
          <h5>Provenance</h5>
          <p className="fineprint sim-prov">
            <span className="exposure-chip mono">Traffic model v{run.journal.graph_version}</span>{' '}
            <span className="exposure-chip mono">Legs {split?.sourced_edges ?? '—'} sourced · {split?.modeled_edges ?? '—'} modelled</span>{' '}
            <span className="exposure-chip mono">Times, speeds and weather · Modelled</span>{' '}
            <span className="exposure-chip mono">{weatherLine}</span>{' '}
            <span className="exposure-chip mono">Traffic simulated</span>
            {run.journal.lane_geometry && <>{' '}<span className="exposure-chip mono" data-chip="lane-geometry">Lane geometry</span>{' '}<span className="exposure-chip mono">Modelled</span></>}
          </p>
          <p className="fineprint">
            <span className="exposure-chip mono">Roster {run.entity_count.toLocaleString('en-US')} vehicles</span>{' '}
            <span className="exposure-chip mono">{run.vessel_count.toLocaleString('en-US')} vessels</span>{' '}
            <span className="exposure-chip mono">{run.aircraft_count.toLocaleString('en-US')} aircraft</span>{' '}
            <span className="exposure-chip mono">Simulated</span>
          </p>
          {tier && (tier.tiered ? (
            <p className="fineprint">
              <span className="exposure-chip mono">Places core {tier.coreNodes} · Shell {tier.shellNodes}</span>{' '}
              <span className="exposure-chip mono">Legs core {tier.coreLanes} · Shell-touching {tier.shellTouchingLanes}</span>{' '}
              <span className="exposure-chip mono">Stats over core only</span>
            </p>
          ) : (
            <p className="fineprint"><span className="exposure-chip mono">All core</span></p>))}
          {run.provenance.headway_coarsening.factor > 1 && (
            <p className="fineprint"><span className="exposure-chip mono">Frequencies coarsened ×{run.provenance.headway_coarsening.factor}</span></p>)}
          <MethodDisclosure data-methodology>
            <p className="fineprint">Vehicle load: leg throughput times headway. Modelled.</p>
            <p className="fineprint">Volume at risk: core-place throughput times added queue wait against the undisrupted run.</p>
            <p className="fineprint">Traffic is modelled; no timetable is claimed. Weather data by Open-Meteo.com (CC BY 4.0).</p>
          </MethodDisclosure>
          <h5>Layers</h5>
          <div className="lyr mono">
            {([
              ['SEA', 'Sea · Vessels and port states'], ['AIR', 'Air · Aircraft and airport states'],
              ['GROUND', 'Ground · Corridor flow'], ['LANES', 'Legs · Lines'],
              ['FLOWS', 'Flows · Direction'], ['WEATHER', 'Weather · Snapshot field'],
              ['DISRUPTIONS', 'Disruptions · Markers and affected places'], ['SATELLITE', 'Satellite · Imagery basemap'],
            ] as Array<[keyof typeof s.simLayers, string]>).map(([key, label]) => (
              <label key={key}><input type="checkbox" checked={s.simLayers[key]}
                onChange={(e) => s.setSimLayer(key, e.target.checked)} /> {label}</label>
            ))}
          </div>
          <h5>Timeline markers</h5>
          <p className="fineprint">
            {(Object.keys(MARKER_KIND_LABEL) as Array<keyof typeof MARKER_KIND_LABEL>).map((k) => (
              <span key={k} style={{ marginRight: 10 }}><MarkerMark kind={k} /> {MARKER_KIND_LABEL[k]}</span>
            ))}
            {s.simMarkers === null ? ' · Not served' : ` · ${s.simMarkers.length} markers`}
            {s.simMarkersTruncated ? ' · Largest 50' : ''}
          </p>
          <VolumeAtRisk />
          <VehicleInspector />
          <StatusBoard />
          <QueueChart />
          <ChainSyncPanel />
          <button className="btn" onClick={() => s.exitSim()}>Leave simulation</button>
        </>)}
    </Panel>
  );
}
