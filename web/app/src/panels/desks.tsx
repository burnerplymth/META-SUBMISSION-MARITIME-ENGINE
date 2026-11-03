import React, { useEffect, useMemo, useState } from 'react';
import { api, pollBackoffMs } from '../api';
import { Panel } from '../components/Panel';
import { nodeName } from '../seed';
import { prettyKey } from '../format';
import { displayValue, friendlyError } from './display';
import { useDeskStore, type MapPaint } from '../store';
import { consumeDesk, consumeReplay, registerDeskConsumer, registerReplayConsumer } from '../search/navBridge';
import { DESK_FEATURES } from '../search/registry';
import type { ChannelsDoc, CostDoc, CoverageDoc, CritDoc, EvalDoc, LaneDoc, LensDoc, NotHitEntry, ReplayDoc, ReplaySummary, RerouteDoc, VerifyDoc, WxDoc } from '../types';
import { labelWord } from '../copy';

/* Analysis Desks: WHYNOT, VERIFY, CHAN, CRIT,
 * LANE/REROUTE, REPLAY, COVER, LENS, REPORT, COST, WX, EVAL — one
 * panel, served figures only. Labels and values only; method lines are
 * rendered verbatim from the served payloads. */

type GNode = { id: string; name?: string; kind?: string; tier?: string };

const GOODS_CLASSES: Array<{ id: string; label: string }> = [
  { id: 'container', label: 'Container' }, { id: 'dry_bulk', label: 'Dry bulk' },
  { id: 'general_cargo', label: 'General cargo' }, { id: 'roro', label: 'RoRo' },
  { id: 'tanker', label: 'Tanker' },
];

function heatColor(t: number): string {
  const a = [122, 77, 0], b = [212, 58, 47];
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}


function FigTable({ figures }: { figures: Record<string, unknown> }) {
  const { graph } = useDeskStore();
  const rows = Object.entries(figures).filter(([, v]) => v !== null && typeof v !== 'object');
  if (!rows.length) return <p className="mono">—</p>;
  return (
    <table className="tbl"><tbody>
      {rows.map(([k, v]) => <tr key={k}><td className="mono">{prettyKey(k)}</td><td className="mono">{k.endsWith('_id') || k === 'node_id' ? nodeName(String(v)) : displayValue(v, graph)}</td></tr>)}
    </tbody></table>
  );
}

/** VERIFY status line: an errored verification (the server answers
 *  200 with {match:false, error:"replay failed: …"}) is a failed state;
 *  'Hash mismatch' is reserved for a clean match:false with no error, a
 *  genuinely recomputed differing hash. The server's error text is not shown. */
export function verifyStatusText(v: VerifyDoc): string {
  if (v.error) return 'Verify failed';
  return v.match ? 'Hash match' : 'Hash mismatch';
}

/** WX seed default: the server seed serves capacity_loss: null, so the
 *  loss the what-if runs with is the operator's choice, presented as a
 *  visible editable input pre-filled with this labelled default, never
 *  a hidden constant substituted at run time. */
export const WX_DEFAULT_CAPACITY_LOSS = 0.5;

export function CritSweepHeader({ crit }: { crit: CritDoc }) {
  return <p className="mono">{crit.places_swept} places · {crit.elapsed_s} s · Version {crit.version ?? '—'}</p>;
}

export function DesksPanel({ demoActive = false }: { demoActive?: boolean }) {
  const s = useDeskStore(); /* desk slice only — stable across playback ticks */
  const nodes = useMemo(() => ((s.graph?.nodes ?? []) as unknown as GNode[]), [s.graph]);
  const runId = s.chain?.run_id ?? null;

  /* WHYNOT — served on its own route (not part of the chain payload), fetched per loaded run. */
  const [notHit, setNotHit] = useState<NotHitEntry[]>([]);
  const [nhPlace, setNhPlace] = useState<string>('');
  useEffect(() => {
    if (!runId) { setNotHit([]); return; }
    let live = true;
    void api.desksWhynot(runId).then((d) => { if (live) setNotHit(d.not_hit); }).catch(() => { if (live) setNotHit([]); });
    return () => { live = false; };
  }, [runId]);
  const nh = notHit.find((n) => n.place_id === nhPlace) ?? notHit[0];

  /* VERIFY */
  const [verify, setVerify] = useState<VerifyDoc | null>(null);
  const [verifyBusy, setVerifyBusy] = useState(false);
  const doVerify = async () => {
    if (!runId) return;
    setVerifyBusy(true);
    try { setVerify(await api.desksVerify(runId)); }
    catch { setVerify(null); }
    setVerifyBusy(false);
  };

  /* CHAN */
  const [chanTick, setChanTick] = useState(0);
  /* The tick is per-run state: reset it when the run changes — the
   * server 422s out-of-range ticks, so a tick carried over from a
   * longer run would error this panel on the new run. */
  useEffect(() => { setChanTick(0); }, [runId]);
  const [chan, setChan] = useState<ChannelsDoc | null>(null);
  useEffect(() => {
    if (!runId) { setChan(null); return; }
    let live = true;
    void api.desksChannels(runId, chanTick).then((d) => { if (live) setChan(d); }).catch(() => { if (live) setChan(null); });
    return () => { live = false; };
  }, [runId, chanTick]);

  /* CRIT */
  const [crit, setCrit] = useState<CritDoc | null>(null);
  const [critPaint, setCritPaint] = useState(false);
  const loadCrit = () => { void api.desksCrit().then(setCrit).catch(() => setCrit(null)); };
  useEffect(loadCrit, []);
  /* while the sweep runs, poll with the ensemble panels'
   * backoff (pollBackoffMs: base interval while healthy, doubling per
   * consecutive failure to the cap, reset on success) over a
   * setTimeout chain — a failing service is not hammered at a fixed
   * 1500 ms forever. */
  useEffect(() => {
    if (crit?.job?.state !== 'running') return;
    let dead = false;
    let failures = 0;
    let timer: number | null = null;
    const poll = async () => {
      try {
        const doc = await api.desksCrit();
        if (dead) return;
        failures = 0;
        setCrit(doc);
      } catch {
        if (dead) return;
        failures += 1;
      }
      if (!dead) timer = window.setTimeout(() => void poll(), pollBackoffMs(failures));
    };
    timer = window.setTimeout(() => void poll(), pollBackoffMs(0));
    return () => { dead = true; if (timer) window.clearTimeout(timer); };
  }, [crit?.job?.state]);
  const toggleCritPaint = () => {
    if (critPaint) { s.setMapPaint(null); setCritPaint(false); return; }
    if (!crit?.rows.length) return;
    const max = Math.max(...crit.rows.map((r) => r.places_hit), 1);
    const entries: MapPaint['entries'] = {};
    for (const r of crit.rows) entries[r.place_id] = { color: heatColor(r.places_hit / max), opacity: 0.55 };
    s.setMapPaint({ entries, legend: [{ label: 'Most places hit', color: heatColor(1) }, { label: 'Fewer places hit', color: heatColor(0.15) }] });
    setCritPaint(true);
  };

  /* LANE / REROUTE */
  const [laneOrigin, setLaneOrigin] = useState('sea_rotterdam');
  const [laneDest, setLaneDest] = useState('sea_singapore');
  const [lane, setLane] = useState<LaneDoc | null>(null);
  const [laneErr, setLaneErr] = useState<string | null>(null);
  const runLane = async () => {
    setLaneErr(null);
    try { setLane(await api.desksLane(laneOrigin, laneDest)); }
    catch (e) { setLane(null); setLaneErr(friendlyError(e)); }
  };
  const [rrPlace, setRrPlace] = useState('chk_suez');
  const [rrDest, setRrDest] = useState('sea_portsaid');
  const [reroute, setReroute] = useState<RerouteDoc | null>(null);
  const runReroute = async () => {
    try { setReroute(await api.desksReroute(rrPlace, rrDest || undefined)); }
    catch { setReroute(null); }
  };
  const closeInWhatIf = (placeId: string) => {
    void s.runWhatIf({ node_id: placeId, kind: 'closure', capacity_loss: 1.0, duration_d: 30, shape: 'step', started_at: new Date().toISOString().slice(0, 19) + 'Z' }, `Close ${nodeName(placeId)}`);
  };

  /* REPLAY */
  const [replays, setReplays] = useState<ReplaySummary[]>([]);
  const [replaysLoaded, setReplaysLoaded] = useState(false);
  const [replay, setReplay] = useState<ReplayDoc | null>(null);
  const [replayStep, setReplayStep] = useState(0);
  useEffect(() => {
    void api.desksReplays().then((d) => setReplays(d.replays)).catch(() => setReplays([]))
      .finally(() => setReplaysLoaded(true));
  }, []);
  const openReplay = (eventId: string) => {
    setReplayStep(0);
    void api.desksReplay(eventId).then(setReplay).catch(() => setReplay(null));
  };

  /* Search nav bridge: a desk request scrolls its section
   * into view; a replay request selects that replay in the REPLAY
   * section through openReplay. A replay id that arrives before the
   * stored list lands waits in navReplayId; once the list is known,
   * an id that is not in it is dropped quietly (no error theatre). */
  const [navReplayId, setNavReplayId] = useState<string | null>(null);
  useEffect(() => {
    const onDesk = () => {
      const key = consumeDesk();
      if (key) {
        const anchor = DESK_FEATURES.find((d) => d.desk === key)?.anchor;
        if (anchor) document.getElementById(anchor)?.scrollIntoView({ block: 'start' });
      }
    };
    const onReplay = () => {
      const eventId = consumeReplay();
      if (eventId) {
        setNavReplayId(eventId);
        document.getElementById('desk-replay')?.scrollIntoView({ block: 'start' });
      }
    };
    registerDeskConsumer(onDesk);
    registerReplayConsumer(onReplay);
    return () => { registerDeskConsumer(null); registerReplayConsumer(null); };
  }, []);
  useEffect(() => {
    if (!navReplayId) return;
    if (replays.some((r) => r.event_id === navReplayId)) {
      openReplay(navReplayId);
      setNavReplayId(null);
    } else if (replaysLoaded) {
      setNavReplayId(null); // no longer stored — ignore quietly
    }
    // openReplay is a stable in-component helper over setState + api.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [navReplayId, replays, replaysLoaded]);

  /* COVER */
  const [cover, setCover] = useState<CoverageDoc | null>(null);
  const [coverPaint, setCoverPaint] = useState(false);
  const toggleCover = async () => {
    if (coverPaint) { s.setMapPaint(null); setCoverPaint(false); return; }
    const doc = cover ?? await api.desksCoverage().catch(() => null);
    if (!doc) return;
    setCover(doc);
    const entries: MapPaint['entries'] = {};
    for (const p of doc.places) {
      if (p.coverage_class === 'CORE') entries[p.place_id] = { color: '#2f7fd0', opacity: 0.5 };
      else if (p.coverage_class === 'SHELL') entries[p.place_id] = { color: '#6b6f76', opacity: 0.35 };
    }
    s.setMapPaint({ entries, legend: [{ label: 'Core', color: '#2f7fd0' }, { label: 'Shell', color: '#6b6f76' }] });
    setCoverPaint(true);
  };

  /* LENS */
  const [lensClass, setLensClass] = useState('tanker');
  const [lens, setLens] = useState<LensDoc | null>(null);
  const applyLens = async () => {
    if (!runId) return;
    const doc = await api.desksLens(runId, lensClass).catch(() => null);
    setLens(doc);
    if (!doc) return;
    const entries: MapPaint['entries'] = {};
    for (const p of doc.places) {
      if (p.state === 'carries') entries[p.place_id] = { color: '#d4a017', opacity: 0.65 };
      else if (p.state === 'unknown') entries[p.place_id] = { color: '#555a61', opacity: 0.18 };
    }
    s.setMapPaint({ entries, legend: [{ label: `Carries ${doc.goods_label.toLowerCase()}`, color: '#d4a017' }, { label: 'Mix not served', color: '#555a61' }] });
  };

  /* COST + EVAL */
  const [cost, setCost] = useState<CostDoc | null>(null);
  const [evalDoc, setEvalDoc] = useState<EvalDoc | null>(null);
  useEffect(() => {
    void api.desksCost().then(setCost).catch(() => setCost(null));
    void api.desksEval().then(setEvalDoc).catch(() => setEvalDoc(null));
  }, []);

  /* WX */
  const wxFeed = useMemo(() => s.feed.filter((f) => f.source === 'NOAA/NWS'), [s.feed]);
  const [wx, setWx] = useState<WxDoc | null>(null);
  /* The seed's capacity loss, as an editable operator input: pre-filled
   * with the labelled default when the seed serves none (null), or with
   * the served value when it serves one. The run below uses THIS value. */
  const [wxLoss, setWxLoss] = useState(WX_DEFAULT_CAPACITY_LOSS);
  const openWx = (eventId: string) => {
    void api.desksWx(eventId).then((doc) => {
      setWx(doc);
      setWxLoss(doc.seed?.scenario.disruption.capacity_loss ?? WX_DEFAULT_CAPACITY_LOSS);
    }).catch(() => setWx(null));
  };
  const seedWx = () => {
    if (!wx?.seed) return;
    const d = wx.seed.scenario.disruption;
    void s.runWhatIf({ node_id: d.node_id, kind: 'weather', capacity_loss: wxLoss, duration_d: d.duration_d ?? 7, shape: 'step', started_at: d.started_at }, 'Weather what-if');
  };

  const placeOptions = (value: string, onChange: (v: string) => void) => (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {nodes.map((n) => <option key={n.id} value={n.id}>{n.name ?? n.id}</option>)}
    </select>
  );

  return (
    <Panel id="desks" code="DSK" title="Analysis desks" updated="Served">
      <h5 id="desk-whynot">Why not hit</h5>
      {!s.chain && <p className="mono">No chain loaded</p>}
      {s.chain && (notHit.length === 0
        ? <p className="mono">Not served</p>
        : <>
          <div className="form-grid"><label>Place
            <select value={nh?.place_id ?? ''} onChange={(e) => setNhPlace(e.target.value)}>
              {notHit.map((n) => <option key={n.place_id} value={n.place_id}>{nodeName(n.place_id)}</option>)}
            </select></label></div>
          {nh && <>
            <p className="mono">{nodeName(nh.place_id)} · {displayValue(nh.reason)}</p>
            <FigTable figures={nh.figures} />
          </>}
        </>)}

      <hr className="rule" />
      <h5 id="desk-verify">Verify chain</h5>
      <button className="btn" disabled={!runId || demoActive || verifyBusy} onClick={() => void doVerify()}>Verify</button>
      {verify && <>
        <p className="mono">{verifyStatusText(verify)}</p>
        <table className="tbl"><tbody>
          <tr><td className="mono">Stored hash</td><td className="mono">{verify.stored_stream_hash.slice(0, 16)}…</td></tr>
          <tr><td className="mono">Recomputed hash</td><td className="mono">{verify.recomputed_stream_hash ? verify.recomputed_stream_hash.slice(0, 16) + '…' : 'Not served'}</td></tr>
          <tr><td className="mono">Version</td><td className="mono">{verify.graph_version ?? '—'}</td></tr>
          <tr><td className="mono">Build hash, journal</td><td className="mono">{verify.journal_graph_hash ? verify.journal_graph_hash.slice(0, 16) + '…' : '—'}</td></tr>
          <tr><td className="mono">Build hash, current</td><td className="mono">{verify.current_graph_hash ? verify.current_graph_hash.slice(0, 16) + '…' : '—'}</td></tr>
        </tbody></table>
        <FigTable figures={(verify.journal?.disruption ?? {}) as Record<string, unknown>} />
      </>}

      <hr className="rule" />
      <h5 id="desk-chan">Channels</h5>
      {!runId && <p className="mono">No chain loaded</p>}
      {runId && (chan
        ? <>
          <div className="form-grid"><label>Tick
            <select value={chanTick} onChange={(e) => setChanTick(Number(e.target.value))}>
              {chan.ticks.map((t) => <option key={t} value={t}>{t}</option>)}
            </select></label></div>
          <table className="tbl"><tbody>
            {chan.channels.map((c) => <tr key={c.channel}><td className="mono">{labelWord(c.channel)}</td><td className="mono">{displayValue(c.state)}</td><td className="mono">{c.events_at_tick} / {c.events_total}</td></tr>)}
          </tbody></table>
        </>
        : <p className="mono">Not served</p>)}

      <hr className="rule" />
      <h5 id="desk-crit">Criticality</h5>
      {!crit && <p className="mono">Not served</p>}
      {crit && crit.status === 'not_computed' && <>
        <p className="mono">Not computed · Version {crit.graph_version ?? '—'}</p>
        <button className="btn" disabled={demoActive} onClick={() => { void api.desksCritRun().then(loadCrit); }}>Run sweep</button>
      </>}
      {crit?.job?.state === 'running' && <p className="mono">Sweep {crit.job.done}/{crit.job.total}</p>}
      {crit?.job?.state === 'failed' && <p className="mono">Sweep failed</p>}
      {crit && crit.rows.length > 0 && <>
        <CritSweepHeader crit={crit} />
        <button className="btn" onClick={toggleCritPaint}>{critPaint ? 'Heat off' : 'Heat on map'}</button>
        <table className="tbl"><thead><tr><th>#</th><th>Place</th><th>Hit</th><th>Delay (d)</th></tr></thead><tbody>
          {crit.rows.slice(0, 15).map((r) => <tr key={r.place_id}><td className="mono">{r.rank}</td><td>{r.name ?? nodeName(r.place_id)}</td><td className="mono">{r.places_hit}</td><td className="mono">{r.total_delay_d.toFixed(1)}</td></tr>)}
        </tbody></table>
      </>}

      <hr className="rule" />
      <h5 id="desk-lane">Lane</h5>
      <div className="form-grid">
        <label>Origin{placeOptions(laneOrigin, setLaneOrigin)}</label>
        <label>Destination{placeOptions(laneDest, setLaneDest)}</label>
      </div>
      <button className="btn" disabled={demoActive} onClick={() => void runLane()}>Stress test</button>
      {laneErr && <p className="mono">{laneErr}</p>}
      {lane && <>
        {lane.primary
          ? <>
            <p className="mono">{lane.primary.places.map((p) => nodeName(p)).join(' → ')}</p>
            <p className="mono">Transit {lane.primary.total_transit_d} d · Spare {lane.primary.spare_capacity ?? 'Not served'}{lane.primary.spare_capacity != null ? ` ${lane.primary.spare_capacity_unit ?? ''}` : ''}</p>
            <table className="tbl"><tbody>
              {lane.removals.map((r) => <tr key={r.place_id}><td>{nodeName(r.place_id)}</td><td className="mono">{r.result === 'no_path' ? 'No path' : `+${r.added_transit_d} d`}</td>
                <td><button className="btn" disabled={demoActive} onClick={() => closeInWhatIf(r.place_id)}>Close in what-if</button></td></tr>)}
            </tbody></table>
            <p className="mono">Single points of failure: {lane.single_points_of_failure.length ? lane.single_points_of_failure.map((p) => nodeName(p)).join(' · ') : 'None'}</p>
            {lane.alternates.map((a, i) => <p className="mono" key={i}>Alternate {a.places.map((p) => nodeName(p)).join(' → ')} · +{a.added_transit_d} d</p>)}
          </>
          : <p className="mono">No path</p>}
      </>}

      <hr className="rule" />
      <h5 id="desk-reroute">Reroute</h5>
      <div className="form-grid">
        <label>Place{placeOptions(rrPlace, setRrPlace)}</label>
        <label>Destination{placeOptions(rrDest, setRrDest)}</label>
      </div>
      <button className="btn" disabled={demoActive} onClick={() => void runReroute()}>Explore bypass</button>
      {reroute && <>
        {reroute.bypass_legs.map((l, i) => <p className="mono" key={i}>{nodeName(l.from)} → {nodeName(l.to)} · {l.transit_d} d · Spare {l.spare_capacity ?? 'Not served'}</p>)}
        {reroute.alternates.map((a, i) => <p className="mono" key={i}>Alternate {a.places.map((p) => nodeName(p)).join(' → ')} · {a.total_transit_d} d · Spare {a.spare_capacity ?? 'Not served'}</p>)}
        {reroute.bypass_legs.length === 0 && reroute.alternates.length === 0 && <p className="mono">No bypass served</p>}
      </>}

      <hr className="rule" />
      <h5 id="desk-replay">Replay</h5>
      {replays.length === 0 && <p className="mono">Not stored</p>}
      <div className="form-grid"><label>Event
        <select value={replay?.id ?? ''} onChange={(e) => openReplay(e.target.value)}>
          <option value="">—</option>
          {replays.map((r) => <option key={r.event_id} value={r.event_id}>{r.title ?? r.event_id}</option>)}
        </select></label></div>
      {replay && <>
        <p className="mono">{replay.title}</p>
        <p className="mono">Daily run state · {replay.per_day_run_state ? 'Stored' : 'Not stored'}</p>
        <div className="form-grid"><label>Step {replayStep + 1} / {replay.observed_steps.length}
          <input type="range" min={0} max={Math.max(0, replay.observed_steps.length - 1)} value={replayStep} onChange={(e) => setReplayStep(Number(e.target.value))} /></label></div>
        {replay.observed_steps[replayStep] && <FigTable figures={replay.observed_steps[replayStep]} />}
      </>}

      <hr className="rule" />
      <h5 id="desk-cover">Coverage</h5>
      <button className="btn" onClick={() => void toggleCover()}>{coverPaint ? 'Coverage off' : 'Coverage on map'}</button>
      {cover && <p className="mono">{cover.legend.map((l) => `${l.class} ${l.places}`).join(' · ')} · Unclassed {cover.unclassed}</p>}

      <hr className="rule" />
      <h5 id="desk-lens">Lens</h5>
      <div className="form-grid"><label>Goods class
        <select value={lensClass} onChange={(e) => setLensClass(e.target.value)}>
          {GOODS_CLASSES.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
        </select></label></div>
      <button className="btn" disabled={!runId || demoActive} onClick={() => void applyLens()}>Apply to chain</button>
      {lens && <>
        <table className="tbl"><tbody>
          {lens.places.map((p) => <tr key={p.place_id}><td>{nodeName(p.place_id)}</td><td className="mono">{p.state === 'carries' ? `Carries ${p.share != null ? (p.share * 100).toFixed(1) + '%' : ''}` : p.state === 'not_carried' ? 'Not carried' : 'Unknown'}</td></tr>)}
        </tbody></table>
        <p className="mono">Legs carrying · {lens.legs.filter((l) => l.state === 'carries').length} / {lens.legs.length}</p>
      </>}

      <hr className="rule" />
      <h5 id="desk-report">Report</h5>
      {!runId && <p className="mono">No chain loaded</p>}
      {runId && <p className="mono">
        <a href={api.reportUrl(runId, 'md')} download>Report MD</a>{' · '}
        <a href={api.reportUrl(runId, 'html')} download>Report HTML</a>{' · '}
        <a href={api.packUrl(runId)} download>Data pack</a>
      </p>}

      <hr className="rule" />
      <h5 id="desk-cost">Cost</h5>
      {!cost && <p className="mono">Not served</p>}
      {cost && <>
        <table className="tbl"><thead><tr><th>Action</th><th>Calls</th><th>Estimated (USD)</th><th>Actual (USD)</th></tr></thead><tbody>
          {cost.per_action.map((a) => <tr key={a.action}><td className="mono">{labelWord(a.action)}</td><td className="mono">{a.calls}</td><td className="mono">{a.estimated_usd.toFixed(4)}</td><td className="mono">{a.actual_usd.toFixed(4)}</td></tr>)}
        </tbody></table>
        <p className="mono">Spend ${cost.totals.accrued_usd.toFixed(4)} · {cost.entries} entries</p>
        {cost.kill_switch
          ? <p className="mono">Spend cap · {cost.kill_switch.cap_usd != null ? `$${cost.kill_switch.cap_usd}` : 'No cap'} · Spent ${cost.kill_switch.spent_usd ?? '—'} · {cost.kill_switch.cap_reached ? 'Reached' : 'Not reached'} · {labelWord(cost.kill_switch.transport)}</p>
          : <p className="mono">Spend cap · Not served</p>}
      </>}

      <hr className="rule" />
      <h5 id="desk-wx">Weather</h5>
      {wxFeed.length === 0 && <p className="mono">No NOAA alerts in feed</p>}
      <div className="form-grid"><label>Alert
        <select value={wx?.event.id ?? ''} onChange={(e) => openWx(e.target.value)}>
          <option value="">—</option>
          {wxFeed.map((f) => <option key={f.id} value={f.id}>{f.nws?.event ?? f.kind} — {nodeName(f.node_id)}</option>)}
        </select></label></div>
      {wx && <>
        <p className="mono">Primary · {wx.mapping.primary_place ? nodeName(wx.mapping.primary_place.place_id) : 'Not served'}</p>
        <p className="mono">Connected · {wx.mapping.connected_places.slice(0, 8).map((p) => nodeName(p.place_id)).join(' · ') || '—'}</p>
                {wx.seed && <div className="form-grid"><label>Capacity loss (fraction) · operator default {WX_DEFAULT_CAPACITY_LOSS}
          <input type="number" step="0.05" min="0.05" max="1" value={wxLoss} onChange={(e) => setWxLoss(Number(e.target.value))} /></label></div>}
        <button className="btn" disabled={demoActive || !wx.seed} onClick={seedWx}>Run what-if from alert</button>
      </>}

      <hr className="rule" />
      <h5 id="desk-eval">Eval</h5>
      {!evalDoc && <p className="mono">Not served</p>}
      {evalDoc && <>
        <p className="mono">{displayValue(evalDoc.status)}</p>
        {evalDoc.labelled_set && <>
          <p className="mono">Labelled set · {evalDoc.labelled_set.size} items · {evalDoc.labelled_set.sources.map((x) => displayValue(x)).join(' · ')}</p>
          <p className="mono">Fields · {evalDoc.labelled_set.label_fields.map((x) => displayValue(x)).join(' · ')}</p>
        </>}
        <p className="mono">Grades · Not served</p>
      </>}
    </Panel>
  );
}
