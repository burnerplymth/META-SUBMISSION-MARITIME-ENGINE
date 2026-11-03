import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDeskStore } from '../../store';
import { nodeLabel } from '../../format';
import { Chip } from '../../shell/Chip';
import { backplay, useBackplay } from './backplay';
import { historyApi, type Fetched } from './client';
import { PlaceChart } from './charts';
import { ForkSection } from './ForkSection';
import {
  activeEventsAt, alignDays, buildEventForkBody, buildForkBody, chartPlaces, clampWindow, eventStartDay, eventTicks, eventsForPlace,
  exampleWindow, focusEventAt, forkWindowGate, missingDays, placesInvolved, reasonLabel, sourceBoard, timelineRows, todayIso, topEventIds, windowLimits,
  type DateRange,
} from './logic';
import { ErrChip, NsChip, ProvChip, Rows, Sec } from './parts';
import type {
  ForkResponse, HistEvent, HistEventsDoc, HistExample, HistExamplesDoc, HistObservations, HistSourcesDoc, HistTimelineDoc,
} from './types';
import './history.css';
import { labelWord } from '../../copy';
import { Icon } from '../../ui/Icon';

const HIST_STATE_WORD: Record<string, string> = { 'HISTORY-CAPABLE': 'History', 'CURRENT-ONLY': 'Current only', BLOCKED: 'Blocked' };
const EVENT_COLORS = { seed: '#e8930c', watch: '#3fb6d9', control: '#8a8d93' } as const;

interface NodeLite { id: string; name?: string }
/** The source's own stated window, first clause only, in sentence case. */
const statedWindow = (w: string | undefined): string => labelWord((w ?? '').split(/[(;:]/)[0].trim().slice(0, 30));

/** HISTORY: go back to a date, pull the recorded APIs for it, backplay the
 *  events, and fork any event or window into the simulator. Every datum
 *  carries its provenance chip (SNAPSHOT for recorded pulls, MODELLED for
 *  inferred events); a source that is CURRENT-ONLY or BLOCKED shows its
 *  true state and a NOT SERVED range, never backfilled. */
export function HistoryPanel({ onClose }: { onClose: () => void }) {
  const d = useDeskStore();
  const bp = useBackplay();
  const today = useMemo(() => todayIso(new Date()), []);
  const [sources, setSources] = useState<Fetched<HistSourcesDoc> | null>(null);
  const [examples, setExamples] = useState<Fetched<HistExamplesDoc> | null>(null);
  const [events, setEvents] = useState<Fetched<HistEventsDoc> | null>(null);
  const [obs, setObs] = useState<Record<string, Fetched<HistObservations>>>({});
  const [timeline, setTimeline] = useState<{ date: string; res: Fetched<HistTimelineDoc> } | null>(null);
  const [userPlaces, setUserPlaces] = useState<string[]>([]);
  const [placeDraft, setPlaceDraft] = useState('');
  const [fromDraft, setFromDraft] = useState('');
  const [toDraft, setToDraft] = useState('');
  const [fork, setFork] = useState<{ resp: ForkResponse } | { err: string } | null>(null);
  const [forking, setForking] = useState(false);
  const paintedRef = useRef(false);

  /* mounted = HISTORY backplay is on: the shell transport shows the cursor */
  useEffect(() => {
    backplay.setActive(true);
    return () => { backplay.setActive(false); };
  }, []);
  /* the backplay clock: one beat per speed interval while playing */
  useEffect(() => {
    if (!bp.playing) return;
    const id = window.setInterval(() => backplay.beat(), backplay.intervalMs());
    return () => window.clearInterval(id);
  }, [bp.playing, bp.speed]);

  /* sources and examples */
  useEffect(() => {
    let live = true;
    void historyApi.sources().then((r) => { if (live) setSources(r); });
    void historyApi.examples().then((r) => { if (live) setExamples(r); });
    return () => { live = false; };
  }, []);
  const srcList = sources?.kind === 'ok' ? sources.body.sources : null;
  const limits = useMemo(() => (srcList ? windowLimits(srcList, today) : null), [srcList, today]);
  useEffect(() => { backplay.setLimits(limits); }, [limits]);
  const exList: HistExample[] = examples?.kind === 'ok' ? examples.body.examples : [];

  /* default window: the first curated example (else the last 30 days of the limit) */
  useEffect(() => {
    if (bp.win || sources === null || examples === null) return;
    const ex = exList[0];
    const w = ex ? exampleWindow(ex.started_at, null, limits) : limits ? clampWindow(limits.to, limits.to, limits, 31) : null;
    if (w) backplay.setWindow(w, ex ? eventStartDay(ex) : w.from);
  }, [bp.win, sources, examples, limits, exList]);
  useEffect(() => { if (bp.win) { setFromDraft(bp.win.from); setToDraft(bp.win.to); } }, [bp.win?.from, bp.win?.to]); // eslint-disable-line react-hooks/exhaustive-deps

  const win: DateRange | null = bp.win;
  const winKey = win ? `${win.from}|${win.to}` : '';
  const placesKey = userPlaces.join(',');

  /* events for the window (PortWatch-inferred events appear once places are named) */
  useEffect(() => {
    if (!win) return;
    let live = true;
    setEvents(null);
    void historyApi.events({ from: win.from, to: win.to, places: userPlaces }).then((r) => { if (live) setEvents(r); });
    return () => { live = false; };
  }, [winKey, placesKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const evList: HistEvent[] = events?.kind === 'ok' ? events.body.events : [];
  useEffect(() => { if (win) backplay.setTicks(eventTicks(evList, win)); }, [events, winKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const cursor = bp.cursor ?? win?.from ?? '';
  const focus = useMemo(() => (cursor ? focusEventAt(evList, cursor) : null), [evList, cursor]);
  const places = useMemo(() => {
    const out = [...userPlaces];
    chartPlaces(evList, focus).forEach((p) => { if (!out.includes(p)) out.push(p); });
    return out.slice(0, 8);
  }, [evList, focus, userPlaces]);
  const chartKey = `${winKey}|${places.join(',')}`;

  /* one observations series per chart place, per window */
  const have = useRef<Set<string>>(new Set());
  const winRef = useRef(winKey);
  useEffect(() => { winRef.current = winKey; have.current = new Set(); setObs({}); }, [winKey]);
  useEffect(() => {
    if (!win) return;
    /* A reply is kept while its WINDOW is still current (a change in the
     * place list must not drop a reply another effect run is waiting on). */
    for (const p of places) {
      const k = `${winKey}|${p}`;
      if (have.current.has(k)) continue;
      have.current.add(k);
      void historyApi.observations({ place: p, from: win.from, to: win.to }).then((r) => { if (winRef.current === winKey) setObs((m) => ({ ...m, [p]: r })); });
    }
  }, [chartKey]); // eslint-disable-line react-hooks/exhaustive-deps

  /* as-of reading, debounced so a fast backplay does not flood the route */
  const tlPlaces = places.slice(0, 4);
  useEffect(() => {
    if (!cursor || !win) return;
    let live = true;
    const t = window.setTimeout(() => {
      void historyApi.timeline({ date: cursor, places: tlPlaces }).then((r) => { if (live) setTimeline({ date: cursor, res: r }); });
    }, 280);
    return () => { live = false; window.clearTimeout(t); };
  }, [cursor, winKey, tlPlaces.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

  /* backplay selects the places involved on the map (the selectNode bus) */
  const focusId = focus?.id ?? null;
  const setPaint = d.setMapPaint;
  const selectNode = d.selectNode;
  useEffect(() => {
    if (!focus) {
      if (paintedRef.current) { setPaint(null); paintedRef.current = false; }
      return;
    }
    const inv = placesInvolved(focus);
    const entries: Record<string, { color: string; opacity?: number }> = { [inv.seed]: { color: EVENT_COLORS.seed } };
    inv.watch.forEach((p) => { entries[p] = { color: EVENT_COLORS.watch }; });
    inv.control.forEach((p) => { entries[p] = { color: EVENT_COLORS.control, opacity: 0.8 }; });
    setPaint({ entries, legend: [{ label: 'Event place', color: EVENT_COLORS.seed }, { label: 'Watch', color: EVENT_COLORS.watch }, { label: 'Control', color: EVENT_COLORS.control }] });
    paintedRef.current = true;
    selectNode(inv.seed);
  }, [focusId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => { if (paintedRef.current) setPaint(null); }, [setPaint]);

  /* `,` and `.` step the cursor (Shift: 10 days); Space plays or pauses */
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || t?.isContentEditable) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === ',' || e.key === '<') { e.preventDefault(); backplay.step(e.shiftKey ? -10 : -1); }
      else if (e.key === '.' || e.key === '>') { e.preventDefault(); backplay.step(e.shiftKey ? 10 : 1); }
      else if (e.key === ' ' && !t?.closest('a,summary,button,[role="slider"],[role="tab"],[role="option"],[role="listbox"]')) { e.preventDefault(); backplay.toggle(); }
    };
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, []);

  const applyWindow = () => {
    const w = clampWindow(fromDraft, toDraft, limits);
    backplay.setWindow(w);
  };
  const goExample = (ex: HistExample) => {
    const w = exampleWindow(ex.started_at, null, limits);
    backplay.setWindow(w, clampWindow(eventStartDay(ex), eventStartDay(ex), w).from);
    setUserPlaces([]);
  };
  const seekEvent = (e: HistEvent) => { backplay.setCursor(eventStartDay(e)); d.selectNode(e.node_id); };
  const doFork = useCallback(async (ids: string[] | null) => {
    if (!win) return;
    const b = ids ? buildEventForkBody(ids, { from: win.from, to: win.to, places: userPlaces }) : buildForkBody({ window: { from: win.from, to: win.to, places: userPlaces } });
    if (!b.ok) { setFork({ err: b.code }); return; }
    setForking(true);
    const r = await historyApi.fork(b.body);
    setForking(false);
    if (r.kind === 'ok') {
      backplay.recordFork(r.body, r.body.created_chain_runs.filter((c) => c.created && c.run_id).map((c) => c.run_id as string));
      setFork({ resp: r.body });
    } else setFork({ err: r.kind === 'error' ? r.code : r.reason });
  }, [win, userPlaces]);

  const forkGate = forkWindowGate(events?.kind === 'ok' ? evList.length : null, !!win);
  const board = srcList && win ? sourceBoard(srcList, win.from, win.to, today) : [];
  const nodes = (d.graph?.nodes as unknown as NodeLite[] | undefined) ?? [];
  const addPlace = () => {
    const q = placeDraft.trim().toLowerCase();
    if (!q) return;
    const hit = nodes.find((n) => n.id.toLowerCase() === q || (n.name ?? '').toLowerCase() === q);
    if (hit && !userPlaces.includes(hit.id)) setUserPlaces((p) => [...p, hit.id]);
    setPlaceDraft('');
  };
  const tl = timeline && timeline.res.kind === 'ok' ? timelineRows(timeline.res.body) : null;
  const inForce = new Set(activeEventsAt(evList, cursor).map((e) => e.id));

  return (
    <section className="panel tool-slot hx" data-panel="tool-history">
      <header className="panel-head">
        <span className="code-chip">HIS</span>
        <span className="panel-title">History</span>
        <span className="panel-updated mono"><Chip kind="snapshot" meta={cursor || undefined} /></span>
        <span className="panel-controls"><button type="button" className="icon-btn" title="Close" aria-label="Close" onClick={onClose}><Icon name="close" size="sm" /></button></span>
      </header>
      <div className="panel-body"><div className="hx-body">

        <Sec title="Sources" id="sources" chips={sources === null ? <Chip kind="meta" word="Checking" /> : sources.kind === 'ok' ? <Chip kind="meta" word={sources.body.fetch_mode ?? ''} /> : null}>
          {sources?.kind === 'notserved' && <NsChip reason={sources.reason} meta="Sources" />}
          {sources?.kind === 'error' && <ErrChip code={sources.code} />}
          {board.map((r) => (
            <div key={r.source} className="hx-src" data-source={r.source} title={`${r.label}${r.cacheEntries !== null ? ` · cache ${r.cacheEntries}` : ''}${r.verified ? ` · ${r.verified}` : ''}`}>
              <b>{labelWord(r.source.replace(/_/g, ' '))}</b>
              <Chip kind={r.stateKind} word={HIST_STATE_WORD[r.stateWord] ?? labelWord(r.stateWord.toLowerCase().replace(/-/g, ' '))} />
              {r.served && r.range && r.avail !== 'out' && <Chip kind="meta" word={r.rangeLabel} />}
              {r.served && r.range && r.avail === 'out' && <NsChip meta={`Out of range ${r.rangeLabel}`} />}
              {r.served && !r.range && <Chip kind="meta" word={statedWindow(srcList?.find((s) => s.source === r.source)?.history_window)} />}
              {!r.served && <NsChip meta="Date range" />}
            </div>
          ))}
        </Sec>

        <Sec title="Window" id="window" chips={limits ? <Chip kind="meta" word={`${limits.from} → ${limits.to}`} /> : <NsChip meta="Limits" />}>
          <div className="hx-ctl">
            <label>From<input type="date" value={fromDraft} min={limits?.from} max={limits?.to} onChange={(e) => setFromDraft(e.target.value)} aria-label="From date" /></label>
            <label>To<input type="date" value={toDraft} min={limits?.from} max={limits?.to} onChange={(e) => setToDraft(e.target.value)} aria-label="To date" /></label>
            <button type="button" className="hx-btn hx-btn--go" onClick={applyWindow} disabled={!limits}>Load</button>
          </div>
          {examples?.kind === 'notserved' && <NsChip reason={examples.reason} meta="Examples" />}
          <div className="hx-ex" role="group" aria-label="Seeded examples">
            {exList.map((ex) => (
              <button key={ex.event_id} type="button" className={`hx-btn${win && eventStartDay(ex) >= win.from && eventStartDay(ex) <= win.to ? ' on' : ''}`}
                title={ex.title} onClick={() => goExample(ex)}>{ex.replay_id ?? ex.node_id}</button>
            ))}
          </div>
        </Sec>

        <Sec title="Events" id="events" chips={<>
          {events === null && win && <Chip kind="meta" word="Checking" />}
          {events?.kind === 'ok' && <Chip kind="meta" word={`${evList.length}`} />}
          <span className="spacer" />
          {events?.kind === 'ok' && forkGate.over && <Chip kind="down" word="Over cap" meta={`${forkGate.count} > ${forkGate.cap}`} title="Limit 8 events" />}
          {forkGate.over && <button type="button" className="hx-btn hx-btn--sim" disabled={forking} data-fork="top" onClick={() => void doFork(topEventIds(evList))}>Fork top {forkGate.cap} by severity</button>}
          <button type="button" className="hx-btn hx-btn--sim" data-fork="window" disabled={!forkGate.enabled || forking}
            title={forkGate.over ? `${forkGate.count} events in the window, at most ${forkGate.cap} per fork` : undefined} onClick={() => void doFork(null)}>Fork window</button>
        </>}>
          {events?.kind === 'notserved' && <NsChip reason={events.reason} />}
          {events?.kind === 'error' && <ErrChip code={events.code} />}
          {events?.kind === 'ok' && (events.body.sources ?? []).filter((s) => !s.served).map((s) => (
            <div key={s.source} className="hx-src"><b>{labelWord(s.source)}</b><NsChip reason={s.reason} /></div>
          ))}
          {events?.kind === 'ok' && evList.length === 0 && <div className="hx-empty"><Chip kind="meta" word="No events" /></div>}
          {evList.length > 0 && (
            <div className="hx-tbl-wrap">
              <table className="hx-tbl hx-tbl--ev mono">
                <thead><tr><th>Date</th><th>Place</th><th>Kind</th><th className="num">Severity</th><th>Source</th><th /></tr></thead>
                <tbody>
                  {evList.map((e) => (
                    <tr key={e.id} className={inForce.has(e.id) ? 'sel' : ''} data-event={e.id}>
                      <td><button type="button" className="hx-row-btn" onClick={() => seekEvent(e)} title="Go to event">{eventStartDay(e)}</button></td>
                      <td title={e.node_id}>{nodeLabel(e.node_id, d.graph)}</td>
                      <td className="dim" title={e.kind.replace(/_/g, " ")}>{e.kind.replace(/_/g, ' ')}</td>
                      <td className="num">{e.severity}</td>
                      <td><ProvChip prov={e.provenance ?? { mode: e.mode, source: e.source }} /></td>
                      <td><button type="button" className="hx-btn hx-btn--sim" disabled={forking} onClick={() => void doFork([e.id])}>Fork</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {forking && <Chip kind="meta" word="Forking" />}
          {fork && 'err' in fork && <div className="hx-state"><ErrChip code={fork.err} /></div>}
        </Sec>

        {fork && 'resp' in fork && <ForkSection resp={fork.resp} win={win ? { from: win.from, to: win.to, places: userPlaces } : null} onClose={() => setFork(null)} />}

        <Sec title="As of" id="asof" chips={<Chip kind="snapshot" word="Snapshot as of" meta={cursor} />}>
          {timeline === null && <Chip kind="meta" word="Checking" />}
          {timeline?.res.kind === 'notserved' && <NsChip reason={timeline.res.reason} />}
          {timeline?.res.kind === 'error' && <ErrChip code={timeline.res.code} />}
          {tl && (
            <>
              <div className="hx-asof mono"><span>Timeline</span><b>{timeline!.date}</b>{timeline!.date !== cursor && <Chip kind="meta" word="Checking" />}</div>
              {tl.events.length === 0 && <Chip kind="meta" word="No event in force" />}
              {tl.events.length > 0 && (
                <table className="hx-tbl mono"><tbody>
                  {tl.events.map((e) => (
                    <tr key={e.id}>
                      <td title={e.place}>{nodeLabel(e.place, d.graph)}</td>
                      <td className="dim">{e.kind.replace(/_/g, ' ')}</td>
                      <td>{labelWord(e.statusAsOf)}</td>
                      <td className="num">{e.day !== null ? `Day ${e.day}` : ''}</td>
                      <td><ProvChip prov={{ mode: e.mode, source: e.source }} /></td>
                    </tr>
                  ))}
                </tbody></table>
              )}
              {tl.obs.length > 0 && (
                <table className="hx-tbl mono"><thead><tr><th>Place</th><th>Metric</th><th className="num">On date</th><th>Unit</th><th /></tr></thead><tbody>
                  {tl.obs.map((o) => (
                    <tr key={`${o.place}-${o.metric}`}>
                      <td title={o.place}>{nodeLabel(o.place, d.graph)}</td>
                      {o.served ? (
                        <>
                          <td className="dim">{labelWord(o.metric)}</td>
                          <td className="num">{o.onDate !== null ? o.onDate : ''}</td>
                          <td className="dim">{o.unit}</td>
                          <td><ProvChip prov={o.prov} /></td>
                        </>
                      ) : <td colSpan={4}><NsChip reason={o.reason || 'no_series_for_place'} /></td>}
                    </tr>
                  ))}
                </tbody></table>
              )}
              {tl.market.length > 0 && (
                <table className="hx-tbl mono"><tbody>
                  {tl.market.map((m) => (
                    <tr key={m.metric}>
                      <td>{m.label}</td>
                      <td className="num">{m.value !== null ? m.value : ''}</td>
                      <td className="dim">{m.unit}</td>
                      <td className="dim">{m.date}</td>
                      <td>{m.value !== null ? <ProvChip prov={m.prov} /> : <NsChip reason="no_data" />}</td>
                    </tr>
                  ))}
                </tbody></table>
              )}
            </>
          )}
        </Sec>

        <Sec title="Places" id="charts" chips={<Chip kind="snapshot" word="Observed" />}>
          <div className="hx-ctl">
            <label>Add place<input type="text" list="hx-places" value={placeDraft} onChange={(e) => setPlaceDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') addPlace(); }} aria-label="Add place" /></label>
            <datalist id="hx-places">{nodes.slice(0, 600).map((n) => <option key={n.id} value={n.id}>{n.name ?? n.id}</option>)}</datalist>
            <button type="button" className="hx-btn" onClick={addPlace}>Add</button>
          </div>
          {win && places.length === 0 && events !== null && <div className="hx-empty"><Chip kind="meta" word="No places" /></div>}
          {win && places.map((p) => {
            const r = obs[p];
            const pts = r?.kind === 'ok' ? alignDays(r.body.series, win.from, win.to) : null;
            const miss = pts ? missingDays(pts) : 0;
            return (
              <div key={p} className="hx-chart" data-place={p}>
                <div className="hx-chart-h mono">
                  <b>{nodeLabel(p, d.graph)}</b>
                  {r?.kind === 'ok' && <span className="u">{r.body.metric} · {r.body.unit}</span>}
                  <span className="spacer" />
                  {r === undefined && <Chip kind="meta" word="Checking" />}
                  {r?.kind === 'ok' && <ProvChip prov={r.body.provenance} />}
                  {r?.kind === 'ok' && miss > 0 && <NsChip meta={`${miss} d missing`} />}
                  {r?.kind === 'notserved' && <NsChip meta={`${reasonLabel(r.reason)} · ${win.from} → ${win.to}`} />}
                  {r?.kind === 'error' && <ErrChip code={r.code} />}
                  {userPlaces.includes(p) && <button type="button" className="hx-btn" onClick={() => setUserPlaces((u) => u.filter((x) => x !== p))} title="Remove" aria-label="Remove place"><Icon name="close" size="xs" /></button>}
                </div>
                {pts && <PlaceChart place={p} points={pts} win={win} cursor={cursor} events={eventsForPlace(evList, p)} focusId={focusId}
                  onSeek={(dd) => { backplay.setCursor(dd); d.selectNode(p); }} />}
              </div>
            );
          })}
        </Sec>

      </div></div>
    </section>
  );
}
