import React, { useEffect, useMemo, useRef, useState } from 'react';
import { VNodes } from '../../charts/VNodeView';
import { sentenceCase } from '../../copy';
import { SegmentedControl, Button } from '../../ui';
import { Chip, MetaChip, StateLine } from '../reports/chips';
import { buildModel, buildObserved, buildSim, type ChartOut } from './chart';
import {
  DAY_MS, eventsOn, fmtDate, fmtNum, isoDay, reasonWord, safeText, seriesAt, simAt, snapDay, stepCursor, stepsOn,
  type Axis, type Step,
} from './logic';
import type { EngineDoc, EpisodeDoc, ObsEvent, ObsSeries, SimSeries } from './types';

/** Width of a host element, tracked with a ResizeObserver. */
export function useWidth(ref: React.RefObject<HTMLElement>, fallback = 420): number {
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

const hostOf = (url: string | null): string | null => {
  if (!url || !/^https?:\/\//i.test(url)) return null;
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return null; }
};

/** One drawing with the shared cursor: the pointer and the arrow keys move it a day at a time on every drawing. */
function CursorChart({ chart, axis, cursor, onCursor, label, id }: { chart: ChartOut; axis: Axis; cursor: number | null; onCursor: (ms: number | null) => void; label: string; id: string }) {
  const days = Math.round((axis.t1 - axis.t0) / DAY_MS);
  const onMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - r.left;
    if (px < chart.plot.x - 2 || px > chart.plot.x + chart.plot.w + 2) { onCursor(null); return; }
    onCursor(snapDay(axis, chart.fromX(px)));
  };
  const onKey = (e: React.KeyboardEvent) => {
    const big = e.shiftKey ? 7 : 1;
    if (e.key === 'ArrowRight' || e.key === 'ArrowUp') { e.preventDefault(); onCursor(stepCursor(axis, cursor, 1, big)); }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') { e.preventDefault(); onCursor(stepCursor(axis, cursor, -1, big)); }
    else if (e.key === 'Home') { e.preventDefault(); onCursor(stepCursor(axis, cursor, 'start')); }
    else if (e.key === 'End') { e.preventDefault(); onCursor(stepCursor(axis, cursor, 'end')); }
    else if (e.key === 'Escape') { e.preventDefault(); onCursor(null); }
  };
  return (
    <div className="rp-chart" tabIndex={0} role="slider" aria-label={label} aria-orientation="horizontal" aria-valuemin={0} aria-valuemax={days}
      aria-valuenow={cursor == null ? 0 : Math.round((cursor - axis.t0) / DAY_MS)} aria-valuetext={cursor == null ? 'No date selected' : fmtDate(isoDay(cursor))}
      onPointerMove={onMove} onPointerLeave={() => onCursor(null)} onKeyDown={onKey} data-testid={id}>
      <svg className="rp-svg" width={chart.width} height={chart.height} viewBox={`0 0 ${chart.width} ${chart.height}`} aria-hidden="true" focusable="false">
        <VNodes nodes={chart.nodes} />
        {cursor != null && <line className="rp-cursor" x1={chart.xOf(cursor)} x2={chart.xOf(cursor)} y1={chart.plot.y} y2={chart.plot.y + chart.plot.h} />}
      </svg>
    </div>
  );
}

function Readout({ children, date }: { children: React.ReactNode; date: number | null }) {
  return (
    <div className="rp-readout mono" data-testid="rp-readout">
      {/* no cursor: the readout keeps its height but states nothing (an orphan "No date" caption read as a fault) */}
      <b>{date == null ? '\u00a0' : fmtDate(isoDay(date))}</b>
      {children}
    </div>
  );
}

export function SourceLink({ url, publisher }: { url: string | null; publisher: string }) {
  const host = hostOf(url);
  if (!host) return null;
  return <a className="rp-link" href={url as string} target="_blank" rel="noopener noreferrer" aria-label={`Open source, ${publisher}`}>{host}</a>;
}

/* ── left: what happened ─────────────────────────────────────────────── */
export function ObservedColumn({ doc, axis, cursor, onCursor }: { doc: EpisodeDoc; axis: Axis; cursor: number | null; onCursor: (ms: number | null) => void }) {
  const host = useRef<HTMLDivElement>(null);
  const w = useWidth(host);
  const [sid, setSid] = useState(doc.observed.series[0]?.id ?? '');
  useEffect(() => { setSid(doc.observed.series[0]?.id ?? ''); }, [doc.id]);
  const series: ObsSeries | undefined = doc.observed.series.find((s) => s.id === sid) ?? doc.observed.series[0];
  const chart = useMemo(() => (series ? buildObserved(doc, series, axis, w) : null), [doc, series, axis, w]);
  const at = series && cursor != null ? seriesAt(series.points, cursor) : null;
  const evs = cursor != null ? eventsOn(doc.observed.events, cursor) : [];
  return (
    <section className="rp-col" aria-label="What happened" data-testid="rp-observed">
      <div className="rp-colh">
        <h3 className="rp-ct">What happened</h3>
        <Chip chip="SOURCED" word="Reported" />
        {doc.observed.series.length > 1 && (
          <SegmentedControl label="Series" size="sm" value={series?.id ?? ''} onChange={setSid}
            options={doc.observed.series.map((s) => ({ value: s.id, label: safeText(s.label).replace(/^Port calls, /, '') }))} />
        )}
      </div>
      <div ref={host} className="rp-chartwrap">
        {chart && series ? (
          <>
            <CursorChart chart={chart} axis={axis} cursor={cursor} onCursor={onCursor} label={`${series.label}, observed`} id="rp-chart-observed" />
            <Readout date={cursor}>
              {series && <span>{at ? `${fmtNum(at.value, 0)} ${series.unit.replace('per day', 'a day')}` : cursor != null ? 'No row' : ''}</span>}
              {evs.length > 0 && <span className="rp-ro-ev">{evs.map((e) => safeText(e.label)).join(' · ')}</span>}
            </Readout>
          </>
        ) : <StateLine chip="NO DATA" word="No series" />}
      </div>
      <EventList events={doc.observed.events} cursor={cursor} />
    </section>
  );
}

function EventList({ events, cursor }: { events: readonly ObsEvent[]; cursor: number | null }) {
  return (
    <ol className="rp-events" aria-label="Dated reports" data-testid="rp-events">
      {events.map((e, i) => {
        const on = cursor != null && e.date === isoDay(cursor);
        return (
          <li key={e.id} className={on ? 'on' : ''} data-event={i + 1}>
            <span className="rp-n" aria-hidden="true">{i + 1}</span>
            <span className="rp-ev-b">
              <span className="rp-ev-t"><b>{safeText(e.label)}</b><span className="mono">{fmtDate(e.date)}</span>{e.detail && <span className="rp-ev-d">{safeText(e.detail)}</span>}</span>
              <span className="rp-ev-c">
                <span>{safeText(e.publisher)}</span>
                {e.published !== e.date && <span className="mono">{fmtDate(e.published)}</span>}
                <SourceLink url={e.url} publisher={safeText(e.publisher)} />
              </span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/* ── right: what the model produced ──────────────────────────────────── */
const channelWord = (c: string): string => sentenceCase(c.replace(/[-_]/g, ' '));

export function ModelColumn({ doc, engine, steps, axis, cursor, onCursor, name }: {
  doc: EpisodeDoc; engine: EngineDoc | null; steps: readonly Step[]; axis: Axis; cursor: number | null; onCursor: (ms: number | null) => void; name: (id: string) => string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const w = useWidth(host);
  const [all, setAll] = useState(false);
  const [simId, setSimId] = useState(doc.simulation?.series[0]?.id ?? '');
  useEffect(() => { setAll(false); setSimId(doc.simulation?.series[0]?.id ?? ''); }, [doc.id, engine?.id]);
  const available = !!engine?.available;
  const chart = useMemo(() => (available ? buildModel(doc, steps, name, axis, w) : null), [doc, available, steps, name, axis, w]);
  const sim: SimSeries | undefined = doc.simulation?.series.find((s) => s.id === simId) ?? doc.simulation?.series[0];
  const simChart = useMemo(() => (sim ? buildSim(doc, sim, axis, w) : null), [doc, sim, axis, w]);
  const here = cursor != null ? stepsOn(steps, cursor) : [];
  const simVal = sim && cursor != null ? simAt(sim, cursor) : null;
  const shown = all ? steps : steps.slice(0, 8);
  return (
    <section className="rp-col" aria-label="What the model produced" data-testid="rp-model">
      <div className="rp-colh">
        <h3 className="rp-ct">What the model produced</h3>
        <Chip chip="MODELLED" />
        {engine && <MetaChip>{engine.label}</MetaChip>}
      </div>
      <div ref={host} className="rp-chartwrap">
        {available && chart ? (
          <>
            <CursorChart chart={chart} axis={axis} cursor={cursor} onCursor={onCursor} label={`${engine?.label} causal chain, added days`} id="rp-chart-model" />
            <Readout date={cursor}>
              <span>{here.length ? here.map((s) => `${name(s.placeId)} +${fmtNum(s.addedD)} d`).join(' · ') : cursor != null ? 'No step' : ''}</span>
            </Readout>
            {chart.beyond > 0 && <MetaChip>{`${chart.beyond} beyond the axis`}</MetaChip>}
          </>
        ) : engine ? <StateLine chip="NOT SERVED" word={reasonWord(engine.reason)} /> : <StateLine chip="NO DATA" word="No engine" />}
      </div>
      {available && (
        <div className="rp-tablewrap">
          <table className="rp-table" aria-label="Causal chain steps" data-testid="rp-steps">
            <thead><tr><th scope="col" className="num">Step</th><th scope="col">Place</th><th scope="col">Channel</th><th scope="col" className="num">Added days</th><th scope="col">Date</th></tr></thead>
            <tbody>
              {shown.map((s, i) => (
                <tr key={s.seq} className={cursor != null && s.date === isoDay(cursor) ? 'on' : ''}>
                  <td className="num">{i + 1}</td><th scope="row">{name(s.placeId)}</th><td>{channelWord(s.channel)}</td>
                  <td className="num">{fmtNum(s.addedD)}</td><td className="mono">{fmtDate(s.date)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {steps.length > 8 && (
            <Button variant="ghost" size="sm" onClick={() => setAll((v) => !v)} aria-expanded={all}>{all ? 'Show fewer steps' : `Show all ${steps.length} steps`}</Button>
          )}
        </div>
      )}
      {sim && simChart && (
        <div className="rp-sim" data-testid="rp-sim">
          <div className="rp-colh">
            <h4 className="rp-ct rp-ct--sub">Simulation layer</h4>
            <Chip chip="SIMULATED" />
            {(doc.simulation?.series.length ?? 0) > 1 && (
              <SegmentedControl label="Simulated series" size="sm" value={sim.id} onChange={setSimId}
                options={(doc.simulation?.series ?? []).map((s) => ({ value: s.id, label: s.label.replace(/^Berth calls, /, '') }))} />
            )}
          </div>
          <CursorChart chart={simChart} axis={axis} cursor={cursor} onCursor={onCursor} label={`${sim.label}, simulated`} id="rp-chart-sim" />
          <Readout date={cursor}>
            <span>{simVal ? `${fmtNum(simVal.disrupted, 0)} disrupted · ${fmtNum(simVal.twin, 0)} undisturbed` : cursor != null ? 'No row' : ''}</span>
          </Readout>
        </div>
      )}
    </section>
  );
}
