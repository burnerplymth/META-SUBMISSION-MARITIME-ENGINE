import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ButtonGroup, IconButton, SegmentedControl, Timecode } from '../../ui';
import { advance, SPEEDS, usePlayhead, type Playhead, type Speed } from './playhead';
import { seriesOf, fmtNum, type Series } from './kpi';
import { windows as scenarioWindows } from './scenario';
import type { KpisDoc, ScenarioItem } from './types';

type ChartKey = 'queue' | 'outside' | 'berths' | 'yard';
const CHART_OPTS = [
  { value: 'queue' as const, label: 'Queue' },
  { value: 'outside' as const, label: 'Outside' },
  { value: 'berths' as const, label: 'At berth' },
  { value: 'yard' as const, label: 'Yard' },
];
const pick = (k: ChartKey, s: KpisDoc['timeline']['samples'][number]): number | null =>
  k === 'queue' ? s.queue : k === 'outside' ? s.outside : k === 'berths' ? s.at_berth : s.yard_fill;

export interface TransportProps {
  playhead: Playhead;
  endH: number;
  scenario: readonly ScenarioItem[];
  divergesH: number | null;
  kpis: KpisDoc | null;
  baseKpis: KpisDoc | null;
  showBaseline: boolean;
}

/** Path for a series in a 1000 x 100 box. */
export function seriesPath(s: Series, endH: number, max: number): string {
  if (s.t.length === 0 || max <= 0) return '';
  return s.t.map((t, i) => `${i === 0 ? 'M' : 'L'}${((t / endH) * 1000).toFixed(1)} ${(100 - (s.v[i] / max) * 92 - 4).toFixed(1)}`).join('');
}

export function Transport({ playhead, endH, scenario, divergesH, kpis, baseKpis, showBaseline }: TransportProps) {
  const h = usePlayhead(playhead);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<Speed>(24);
  const [chart, setChart] = useState<ChartKey>('queue');
  const last = useRef(0);
  const wins = useMemo(() => scenarioWindows(scenario, endH), [scenario, endH]);

  useEffect(() => {
    if (!playing) return undefined;
    let id = 0;
    last.current = performance.now();
    const step = (now: number) => {
      const dt = Math.min(100, now - last.current); last.current = now;
      const r = advance(playhead.get(), dt, speed, endH);
      playhead.set(r.h);
      if (r.end) { setPlaying(false); return; }
      id = requestAnimationFrame(step);
    };
    id = requestAnimationFrame(step);
    return () => cancelAnimationFrame(id);
  }, [playing, speed, endH, playhead]);

  const toggle = useCallback(() => {
    if (!playing && playhead.get() >= endH) playhead.set(0);
    setPlaying((p) => !p);
  }, [playing, playhead, endH]);

  const sc = useMemo(() => seriesOf(kpis?.timeline.samples ?? [], (s) => s.t, (s) => pick(chart, s)), [kpis, chart]);
  const bs = useMemo(() => (showBaseline ? seriesOf(baseKpis?.timeline.samples ?? [], (s) => s.t, (s) => pick(chart, s)) : { t: [], v: [] }), [baseKpis, chart, showBaseline]);
  const max = Math.max(1e-9, ...sc.v, ...bs.v) * 1.05;
  const pct = (x: number) => `${Math.max(0, Math.min(100, (x / endH) * 100))}%`;
  const seekFromEvent = (e: React.PointerEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    playhead.set(Math.max(0, Math.min(endH, ((e.clientX - r.left) / r.width) * endH)));
  };

  return (
    <div className="ptw-transport">
      <div className="ptw-tr-row">
        <ButtonGroup label="Transport">
          <IconButton icon="skip-back" label="To start" size="sm" onClick={() => { setPlaying(false); playhead.set(0); }} />
          <IconButton icon="step-back" label="Back 1 h" size="sm" onClick={() => playhead.set(Math.max(0, playhead.get() - 1))} />
          <IconButton icon={playing ? 'pause' : 'play'} label={playing ? 'Pause' : 'Play'} keys="Space" size="sm" pressed={playing} onClick={toggle} />
          <IconButton icon="step-forward" label="Forward 1 h" size="sm" onClick={() => playhead.set(Math.min(endH, playhead.get() + 1))} />
        </ButtonGroup>
        <Timecode hours={h} minutes className="ptw-tc" />
        <span className="ptw-tr-end">of <Timecode hours={endH} /></span>
        <SegmentedControl<string> label="Speed" size="sm" value={String(speed)} onChange={(v) => setSpeed(Number(v) as Speed)}
          options={SPEEDS.map((s) => ({ value: String(s), label: `${s} h/s` }))} />
        <span className="ptw-tr-spacer" />
        <SegmentedControl<ChartKey> label="Series" size="sm" value={chart} onChange={setChart} options={CHART_OPTS} />
      </div>
      <div className="ptw-scrub" onPointerDown={(e) => { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); seekFromEvent(e); }}
        onPointerMove={(e) => { if (e.buttons) seekFromEvent(e); }} role="slider" aria-label="Playhead" aria-valuemin={0} aria-valuemax={endH} aria-valuenow={Math.round(h * 10) / 10} tabIndex={0}
        onKeyDown={(e) => {
          const d = e.shiftKey ? 24 : 1;
          if (e.key === 'ArrowRight') playhead.set(Math.min(endH, playhead.get() + d));
          else if (e.key === 'ArrowLeft') playhead.set(Math.max(0, playhead.get() - d));
          else if (e.key === ' ') toggle();
          else return;
          e.preventDefault();
        }}>
        <svg className="ptw-chart" viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true">
          {wins.map((w, i) => <rect key={i} className="ptw-win" x={(w.start / endH) * 1000} width={Math.max(2, ((w.end - w.start) / endH) * 1000)} y="0" height="100" />)}
          {[0.25, 0.5, 0.75].map((f) => <line key={f} className="ptw-grid" x1={f * 1000} x2={f * 1000} y1="0" y2="100" />)}
          {bs.t.length > 0 && <path className="ptw-line ptw-line--base" d={seriesPath(bs, endH, max)} />}
          {sc.t.length > 0 && <path className="ptw-line" d={seriesPath(sc, endH, max)} />}
        </svg>
        {divergesH !== null && <span className="ptw-div" style={{ left: pct(divergesH) }}><i />Diverges</span>}
        <span className="ptw-head" style={{ left: pct(h) }} />
        <span className="ptw-axis ptw-axis--max">{chart === 'yard' ? `${fmtNum(max * 100, 0)}%` : fmtNum(max, 0)}</span>
      </div>
      <div className="ptw-legend">
        {sc.t.length > 0 && <span className="ptw-lg"><i className="ptw-lg-s" />Scenario</span>}
        {bs.t.length > 0 && <span className="ptw-lg"><i className="ptw-lg-b" />Baseline</span>}
        {wins.length > 0 && <span className="ptw-lg"><i className="ptw-lg-w" />Window</span>}
      </div>
    </div>
  );
}
