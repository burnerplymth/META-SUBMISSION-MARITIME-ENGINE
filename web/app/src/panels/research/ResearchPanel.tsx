import React, { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { LiveStatus } from '../../ui/LiveStatus';
import { useTabKeys } from '../../ui/Controls';
import { ChartFrame } from '../../charts/ChartFrame';
import { Panel } from '../../components/Panel';
import { Chip, MetaChip, StateLine } from '../reports/chips';
import { Tickets } from './tickets';
import { chartSpecFor, seriesSpec } from './chartSpecs';
import { failureOf, loadAssembly, loadEpisodes, loadScan, runAnalysis, type Origin } from './client';
import { fitTracks } from './geo';
import { analysisSpec, datasetLabel, defaultCtx, defaultPlan, MAX_MAP_TRACKS, type AnalysisCtx } from './plan';
import { displayDay } from './time';
import { tableCsv, exportUrl, download } from './exportTable';
import { decodeSel, encodeSel, researchHash } from './urlHash';
import { caveatWord, errorWord, overlayLabel, SCENE_REASON, stateWord, summaryRows } from './vocab';
import { ReportTab } from './ReportTab';
import { useResearchReport } from './exportReport';
import { getReport } from './reportSingleton';
import { resolveEpisode, type EvidenceTarget } from './reportModel';
import { researchNav, type ResearchTab } from './researchNav';
import { toMs } from './time';
import {
  ANALYSIS_KINDS, DESCRIPTIVE_LABEL, KIND_LABEL, type AnalysisKind, type AnalysisResult, type Assembly, type Episode, type ScanDoc,
} from './types';
import '../../charts/charts.css';
import './research.css';
import { scrollBehavior } from '../../ui/motion';

/* RESEARCH WORKBENCH (RSH). An episode from the archive, its aligned series (port calls, weather, EIA), descriptive
 * analyses with sample size and caveat chips, recorded SAM scene results and an AIS sample. Descriptive statistics
 * on historical data: no forecast, no claim of cause. A recorded copy is always labelled Recorded. */

type Phase<T> = { s: 'loading' } | { s: 'ready'; v: T } | { s: 'failed'; code: string };
const SERIES_SHOWN = 4;

function OriginChip({ origin }: { origin: Origin }) {
  return origin === 'recorded' ? <Chip chip="SNAPSHOT" word="Recorded" /> : <Chip chip="SNAPSHOT" word="Cached" />;
}

/** Scroll the window's own scroller (never the page) so the element sits at its top edge. */
function scrollWithin(el: HTMLElement | null | undefined): void {
  if (!el) return;
  let p = el.parentElement;
  while (p && !(p.scrollHeight > p.clientHeight + 1 && /(auto|scroll)/.test(getComputedStyle(p).overflowY))) p = p.parentElement;
  if (!p) return;
  const top = p.scrollTop + el.getBoundingClientRect().top - p.getBoundingClientRect().top - 4;
  p.scrollTo?.({ top, behavior: scrollBehavior() });
}

export function ResearchPanel() {
  const [eps, setEps] = useState<Phase<{ list: Episode[]; origin: Origin }>>({ s: 'loading' });
  const [epId, setEpId] = useState<string | null>(null);
  const [asm, setAsm] = useState<Phase<{ a: Assembly; origin: Origin }>>({ s: 'loading' });
  const [scan, setScan] = useState<ScanDoc | null>(null);
  const [kind, setKind] = useState<AnalysisKind>('event_study');
  const [ctx, setCtx] = useState<AnalysisCtx>({ series: null, x: null, eventId: null, runId: null });
  const [results, setResults] = useState<Partial<Record<AnalysisKind, Phase<AnalysisResult>>>>({});
  const token = useRef(0);
  const navVersion = useSyncExternalStore(researchNav.subscribe, researchNav.version, researchNav.version);
  const view: ResearchTab = researchNav.tab();
  const VIEWS = ['workbench', 'report'] as const;
  const tkView = useTabKeys(VIEWS.length, VIEWS.indexOf(view), (i) => researchNav.setTab(VIEWS[i]));
  const tkKind = useTabKeys(ANALYSIS_KINDS.length, ANALYSIS_KINDS.indexOf(kind), (i) => pick(ANALYSIS_KINDS[i]));
  const [flash, setFlash] = useState<string | null>(null);
  const secRefs = useRef<Record<string, HTMLElement | null>>({});
  const tickets = useRef(new Tickets());

  useEffect(() => {
    let live = true;
    loadEpisodes().then((r) => {
      if (!live) return;
      setEps({ s: 'ready', v: { list: r.value, origin: r.origin } });
      const want = decodeSel(researchHash.get())?.episode;
      setEpId(r.value.find((e) => e.id === want)?.id ?? r.value[0]?.id ?? null);
    }).catch((e) => { if (live) setEps({ s: 'failed', code: failureOf(e).code }); });
    return () => { live = false; };
  }, []);

  const episode = useMemo(() => (eps.s === 'ready' ? eps.v.list.find((e) => e.id === epId) ?? null : null), [eps, epId]);

  const run = useCallback(async (a: Assembly, origin: Origin, k: AnalysisKind, c: AnalysisCtx, tk: number) => {
    const spec = analysisSpec(k, c);
    if (!spec) { setResults((r) => ({ ...r, [k]: { s: 'failed', code: 'invalid_request' } })); tickets.current.next(k); return; }
    const mine = tickets.current.next(k);
    const fresh = () => tk === token.current && tickets.current.isCurrent(k, mine);
    setResults((r) => ({ ...r, [k]: { s: 'loading' } }));
    try {
      const res = await runAnalysis(a, origin, spec);
      if (fresh()) setResults((r) => ({ ...r, [k]: { s: 'ready', v: res } }));
    } catch (e) { if (fresh()) setResults((r) => ({ ...r, [k]: { s: 'failed', code: failureOf(e).code } })); }
  }, []);

  useEffect(() => {
    if (!episode) return;
    const tk = ++token.current;
    setAsm({ s: 'loading' }); setScan(null); setResults({});
    researchHash.set(encodeSel({ episode: episode.id, places: [], from: null, to: null }));
    loadAssembly(defaultPlan(episode)).then(async (r) => {
      if (tk !== token.current) return;
      const c = defaultCtx(r.value, episode);
      setAsm({ s: 'ready', v: { a: r.value, origin: r.origin } });
      setCtx(c);
      void run(r.value, r.origin, kind, c, tk);
      const sc = await loadScan(r.value, r.origin);
      if (tk === token.current) setScan(sc);
    }).catch((e) => { if (tk === token.current) setAsm({ s: 'failed', code: failureOf(e).code }); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [episode]);

  const a = asm.s === 'ready' ? asm.v.a : null;
  const origin: Origin = asm.s === 'ready' ? asm.v.origin : eps.s === 'ready' ? eps.v.origin : 'server';
  const recorded = origin === 'recorded';

  const pick = (k: AnalysisKind) => {
    setKind(k);
    /* the result sits below the charts: bring the Analysis section to the top of the window */
    setTimeout(() => scrollWithin(secRefs.current.analysis), 0);
    if (a && !results[k]) void run(a, origin, k, ctx, token.current);
  };
  const change = (patch: Partial<AnalysisCtx>) => {
    const c = { ...ctx, ...patch };
    setCtx(c);
    setResults((r) => ({ ...r, [kind]: undefined }));
    if (a) void run(a, origin, kind, c, token.current);
  };

  /* Requests from outside: open_research (episode and tab) and an evidence chip of the report (what it cites). */
  useEffect(() => {
    if (eps.s !== 'ready') return;
    const ask = researchNav.takeOpen();
    if (!ask) return;
    const hit = resolveEpisode(eps.v.list, { episodeId: ask.episodeId, places: ask.places, from: ask.from, to: ask.to });
    if (hit) setEpId(hit.id);
    if (ask.tab === 'report' && ask.sessionId) void getReport().attach(ask.sessionId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [navVersion, eps]);

  useEffect(() => {
    if (!a) return;
    const f = researchNav.takeFocus();
    if (!f) return;
    let c = ctx;
    if (f.series && a.series.some((x) => x.key === f.series)) c = { ...ctx, series: f.series };
    if (f.kind) {
      setKind(f.kind);
      if (c !== ctx) setCtx(c);
      if (c !== ctx || !results[f.kind]) void run(a, origin, f.kind, c, token.current);
    } else if (c !== ctx) {
      setCtx(c);
      setResults({});
    }
    setFlash(f.section);
    window.setTimeout(() => {
      secRefs.current[f.section]?.scrollIntoView?.({ block: 'start', behavior: scrollBehavior() });
    }, 30);
    window.setTimeout(() => setFlash(null), 2600);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [navVersion, a]);

  const seriesOptions = a ? a.series.filter((s) => s.values.some((v) => v !== null)) : [];
  const label = (key: string) => { const s = a?.series.find((x) => x.key === key); return s ? `${datasetLabel(s.dataset)}, ${a?.places.find((p) => p.id === s.place)?.name ?? (s.place === 'global' ? 'Global' : s.place)}` : key; };

  const exportSpec = useMemo(() => a ? {
    csv: () => a.t.map((d, i) => Object.fromEntries([['date', d], ...a.series.map((s) => [s.key, s.values[i]])])),
    json: () => ({ label: DESCRIPTIVE_LABEL, assembly_id: a.id, window: { from: a.from, to: a.to }, series: a.series.map((s) => ({ key: s.key, unit: s.unit, state: s.state, source: s.source, values: s.values })), t: a.t, events: a.events }),
  } : undefined, [a]);

  const loaded = useMemo(() => Object.fromEntries(Object.entries(results).map(([k, v]) => [k, v && v.s === 'ready' && !v.v.error ? v.v : null])) as Partial<Record<AnalysisKind, AnalysisResult | null>>, [results]);
  useResearchReport(a && asm.s === 'ready' ? { assembly: a, origin, episode, ctx, loaded, scan } : null);
  const res = results[kind];
  const spec = res && res.s === 'ready' ? chartSpecFor(res.v, recorded) : null;
  const tracks = a ? a.tracks.slice(0, MAX_MAP_TRACKS) : [];
  const map = a ? fitTracks(tracks, a.places) : null;

  return (
    <Panel id="research" code="RSH" title="Research workbench" updated={a ? `${origin === 'recorded' ? 'RECORDED' : 'CACHED'} · ${a.from} to ${a.to}` : undefined} exportSpec={exportSpec} className="rk-panel">
      <div className="rsh-root" data-testid="research-root" data-view={view}>
        <LiveStatus text={eps.s === 'failed' ? 'Not served' : asm.s === 'failed' ? errorWord(asm.code) : res?.s === 'loading' ? 'Running' : res?.s === 'failed' ? errorWord(res.code) : res?.s === 'ready' ? 'Result ready' : ''} />
        <div className="rk-tabs" role="tablist" aria-label="Research view">
          {VIEWS.map((t, i) => (
            <button key={t} type="button" role="tab" {...tkView(i)} aria-selected={view === t} className={`rk-tab${view === t ? ' on' : ''}`} data-testid={`research-view-${t}`} onClick={() => researchNav.setTab(t)}>{t === 'workbench' ? 'Workbench' : 'Report'}</button>
          ))}
        </div>
        {eps.s === 'loading' && <StateLine chip="INFO" word="Loading" />}
        {eps.s === 'failed' && <StateLine chip="NOT SERVED" word="Not served" code={eps.code} />}
        {eps.s === 'ready' && (
          <section className="rsh-sec" aria-label="Episode">
            <div className="rk-row">
              <label className="rk-lbl" htmlFor="rsh-episode">Episode</label>
              <select id="rsh-episode" className="rsh-select" value={epId ?? ''} onChange={(e) => setEpId(e.target.value)} data-testid="research-episode">
                {eps.v.list.map((e) => <option key={e.id} value={e.id}>{e.title}</option>)}
              </select>
              <OriginChip origin={origin} />
              {episode && <MetaChip>{`${displayDay(toMs(episode.from) as number)} to ${displayDay(toMs(episode.to) as number)}`}</MetaChip>}
              <Chip chip="SOURCED" word="Descriptive" />
            </div>
            {a && view === 'workbench' && (
              <ul className="rsh-events" aria-label="Events" data-testid="research-events" ref={(el) => { secRefs.current.events = el; }} data-flash={flash === 'events' ? '' : undefined}>
                {a.events.map((ev, evi) => (
                  <li key={ev.id}>
                    <span className="rsh-evn mono" aria-hidden>{evi + 1}</span>
                    <MetaChip>{ev.t.slice(0, 10)}</MetaChip>
                    <MetaChip>{ev.category.replace(/_/g, ' ')}</MetaChip>
                    <span>{ev.title}</span>
                    {ev.source.url ? <a href={ev.source.url} target="_blank" rel="noopener noreferrer">{ev.source.publisher ?? 'Source'}</a> : <span className="rk-count">{ev.source.publisher ?? 'Source'}</span>}
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        {view === 'report' && eps.s === 'ready' && (
          <ReportTab ctx={{ episode, kind, series: ctx.series, eventId: ctx.eventId, copyRecorded: recorded }} onShow={(t: EvidenceTarget) => researchNav.requestFocus(t)} />
        )}

        {view === 'workbench' && asm.s === 'loading' && eps.s === 'ready' && <StateLine chip="INFO" word="Loading" />}
        {view === 'workbench' && asm.s === 'failed' && <StateLine chip="NOT SERVED" word={errorWord(asm.code)} code={asm.code} />}

        {a && view === 'workbench' && (
          <>
            <section className="rsh-sec" aria-label="Aligned series" data-testid="research-series" ref={(el) => { secRefs.current.series = el; }} data-flash={flash === 'series' ? '' : undefined}>
              <h3 className="rsh-h">Aligned series <MetaChip>{`${a.days} d`}</MetaChip></h3>
              <div className="rsh-grid rsh-grid--2">
                {seriesOptions.slice(0, SERIES_SHOWN).map((s) => (
                  <ChartFrame key={s.key} height={150} exportWidth={900}
                    spec={seriesSpec(s.key, label(s.key), s.unit, a.t, s.values, a.events.filter((e) => e.place === s.place || s.place === 'global'), recorded)} />
                ))}
              </div>
              <div className="rk-row rk-wrap">
                {a.series.filter((s) => s.state === 'not_served').map((s) => <Chip key={s.key} chip="NOT SERVED" word={`${label(s.key)}: not served`} />)}
                {a.coverage.filter((c) => c.chip === 'gaps_in_series').map((c, i) => <MetaChip key={i}>{`${caveatWord(c.chip)}: ${datasetLabel(c.dataset ?? '')}`}</MetaChip>)}
              </div>
            </section>

            <section className="rsh-sec" aria-label="Analysis" data-testid="research-analysis" ref={(el) => { secRefs.current.analysis = el; }} data-flash={flash === 'analysis' ? '' : undefined}>
              <h3 className="rsh-h">Analysis</h3>
              <div className="rk-tabs" role="tablist" aria-label="Analysis kind">
                {ANALYSIS_KINDS.map((k, i) => (
                  <button key={k} type="button" role="tab" {...tkKind(i)} aria-selected={kind === k} className={`rk-tab${kind === k ? ' on' : ''}`} data-testid={`research-tab-${k}`} onClick={() => pick(k)}>{KIND_LABEL[k]}</button>
                ))}
              </div>
              <div className="rk-row rk-wrap">
                <label className="rk-lbl" htmlFor="rsh-series">{kind === 'lagged_correlation' ? 'Series Y' : 'Series'}</label>
                <select id="rsh-series" className="rsh-select" value={ctx.series ?? ''} onChange={(e) => change({ series: e.target.value })}>
                  {seriesOptions.map((s) => <option key={s.key} value={s.key}>{label(s.key)}</option>)}
                </select>
                {kind === 'lagged_correlation' && <>
                  <label className="rk-lbl" htmlFor="rsh-x">Series X</label>
                  <select id="rsh-x" className="rsh-select" value={ctx.x ?? ''} onChange={(e) => change({ x: e.target.value })}>
                    {seriesOptions.filter((s) => s.key !== ctx.series).map((s) => <option key={s.key} value={s.key}>{label(s.key)}</option>)}
                  </select></>}
                {(kind === 'event_study' || kind === 'before_after') && <>
                  <label className="rk-lbl" htmlFor="rsh-event">Event</label>
                  <select id="rsh-event" className="rsh-select" value={ctx.eventId ?? ''} onChange={(e) => change({ eventId: e.target.value })}>
                    {a.events.map((ev) => <option key={ev.id} value={ev.id}>{ev.title}</option>)}
                  </select></>}
                {kind === 'model_overlay' && episode && episode.simRuns.length > 0 && <>
                  <label className="rk-lbl" htmlFor="rsh-run">Run</label>
                  <select id="rsh-run" className="rsh-select" value={ctx.runId ?? ''} onChange={(e) => change({ runId: e.target.value })}>
                    {episode.simRuns.map((id, i) => <option key={id} value={id}>{`Run ${i + 1}`}</option>)}
                  </select></>}
              </div>
              <div data-testid="research-result" aria-live="polite">
                {!res && <StateLine chip="INFO" word="No result" />}
                {res?.s === 'loading' && <StateLine chip="INFO" word="Running" />}
                {res?.s === 'failed' && <StateLine chip="NOT SERVED" word={errorWord(res.code)} code={res.code} />}
                {res?.s === 'ready' && <ResultView r={res.v} recorded={recorded} spec={spec} />}
              </div>
            </section>

            <section className="rsh-sec" aria-label="Scenes" data-testid="research-scenes" ref={(el) => { secRefs.current.scenes = el; }} data-flash={flash === 'scenes' ? '' : undefined}>
              <h3 className="rsh-h">Scenes <MetaChip>{`${a.scenes.length}`}</MetaChip><Chip chip="MODELLED" word="Detected, not verified" /></h3>
              {a.scenes.length === 0 ? <StateLine chip="NOT SERVED" word="No scenes" /> : (
                <div className="rsh-scroll">
                  <table className="rk-table">
                    <thead><tr><th>Date</th><th>Source</th><th className="rk-r">Cloud %</th><th>Scene</th><th>SAM result</th></tr></thead>
                    <tbody>
                      {a.scenes.map((s) => {
                        const sam = scan?.results[s.sceneId];
                        const nc = scan?.notCounted[s.sceneId];
                        return (
                          <tr key={s.sceneId}>
                            <td className="rk-num">{s.t}</td><td>{s.source}</td>
                            <td className="rk-r rk-num">{s.cloudPct === null ? 'Not served' : s.cloudPct.toFixed(1)}</td>
                            <td>{s.usable ? <Chip chip="SOURCED" word="Usable" /> : <Chip chip="NOT SERVED" word="Not counted" />}</td>
                            <td>{sam ? <span className="rk-row"><span className="rk-num">{sam.text}</span><Chip chip="MODELLED" word="Modelled" /><Chip chip="SNAPSHOT" word="Recorded" /></span>
                              : !s.usable ? <MetaChip>Cloud above limit</MetaChip>
                              : <MetaChip>{nc ? SCENE_REASON[nc] ?? 'Not counted' : scan ? 'No recorded reply' : 'Not served'}</MetaChip>}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <section className="rsh-sec" aria-label="Vessel tracks" data-testid="research-tracks">
              <h3 className="rsh-h">AIS sample <MetaChip>{`${tracks.length} of ${a.tracks.length} vessels`}</MetaChip></h3>
              {map ? (
                <svg className="rsh-map" viewBox={`0 0 ${map.w} ${map.h}`} width={map.w} role="img" aria-label="Vessel track sample">
                  {map.lines.map((l) => <path key={l.id} d={l.d} />)}
                  {map.dots.map((d) => <g key={d.id}><circle cx={d.x} cy={d.y} r={3} /><text x={d.x + 5} y={d.y + 3}>{d.name}</text></g>)}
                </svg>
              ) : <StateLine chip="NOT SERVED" word="No open track data" />}
            </section>

            <section className="rsh-sec" aria-label="Export">
              <h3 className="rsh-h">Export</h3>
              <div className="rk-row rk-wrap">
                {!recorded && (['csv', 'json', 'xlsx'] as const).map((f) => (
                  <a key={f} className="rk-btn" href={exportUrl(a.id, f)} download data-testid={`research-export-${f}`}>{`Server ${f.toUpperCase()}`}</a>
                ))}
                <button type="button" className="rk-btn" data-testid="research-export-table" onClick={() => download(`research-${a.id}.csv`, 'text/csv', tableCsv({ columns: ['date', ...a.series.map((s) => s.key)], rows: a.t.map((d, i) => [d, ...a.series.map((s) => s.values[i])]) }, [`assembly ${a.id}`, `window ${a.from} to ${a.to}`]))}>Table CSV</button>
              </div>
            </section>
          </>
        )}
        <p className="rsh-foot" data-testid="research-label">{DESCRIPTIVE_LABEL}</p>
      </div>
    </Panel>
  );
}

function ResultView({ r, recorded, spec }: { r: AnalysisResult; recorded: boolean; spec: ReturnType<typeof chartSpecFor> }) {
  if (r.error) return <StateLine chip="NOT SERVED" word={errorWord(r.error.code)} code={r.error.code} />;
  const rows = summaryRows(r);
  const ovl = overlayLabel(r);
  return (
    <div className="rsh-sec">
      <div className="rk-row rk-wrap" data-testid="research-chips">
        <MetaChip>{`n ${r.n ?? 'not served'}${r.nUnit ? ` ${r.nUnit}` : ''}`}</MetaChip>
        {r.kind === 'model_overlay' && <Chip chip="SIMULATED" word="Simulated beside observed" />}
        {r.caveats.map((c) => <MetaChip key={c}>{caveatWord(c)}</MetaChip>)}
        {r.ci && r.ci.low !== null && r.ci.high !== null && <MetaChip>{`${Math.round((r.ci.level ?? 0.95) * 100)}% interval ${r.ci.low.toFixed(1)} to ${r.ci.high.toFixed(1)}`}</MetaChip>}
        {r.hasPointwiseCi && <MetaChip>Interval band</MetaChip>}
        {recorded && <Chip chip="SNAPSHOT" word="Recorded" />}
      </div>
      {rows.length > 0 && <dl className="rsh-kv">{rows.map((x) => <div key={x.label}><dt>{x.label}</dt><dd>{x.value}</dd></div>)}</dl>}
      {spec ? <ChartFrame spec={spec} height={240} exportWidth={900} /> : <StateLine chip="NOT SERVED" word="No chart" />}
      {ovl && <p className="rsh-foot">{ovl}</p>}
      <p className="rsh-foot">{r.label}</p>
    </div>
  );
}
