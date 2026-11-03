import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { IconButton } from '../../ui';
import { buildPaths, drawMap, pickVessel, scaleBar, HULL_MID, type DrawStats, type Hover, type PathSet, type Underlay } from './render';
import {
  clampView, fitScale, fitView, terminalAt, toScreen, toWorld, zoomAt, type LabelCand, type LayerVis, type Prepared, type View,
} from './geo';
import { paletteFor, themeFromAttr } from './palette';
import { effectsAt } from './scenario';
import { frameAt, placeFrame, classLoa, type PlacedVessel, type VesselSet } from './vessels';
import type { Layout } from './berths';
import type { FramesDoc, ScenarioItem } from './types';
import { usePlayhead, type Playhead } from './playhead';
import { createPinchTracker } from './gesture';
import { Tags } from './chips';
import { sourceChips, type ChipSpec } from './labels';

const MAX_SCALE = 3;

export interface MapViewProps {
  placeId: string;
  prep: Prepared;
  layout: Layout | null;
  labelCands: LabelCand[];
  vis: LayerVis;
  colour: boolean;
  glow: boolean;
  labels: boolean;
  imageUrl: string | null;
  imageBounds?: [number, number, number, number];
  imageOn: boolean;
  imageOpacity: number;
  frames: FramesDoc | null;
  vesselsOn: boolean;
  loaScale: number;
  scenario: readonly ScenarioItem[];
  playhead: Playhead;
  chips: readonly ChipSpec[];
  onStats?: (s: DrawStats & { ms: number }) => void;
}

const STATE_WORD: Record<string, string> = {
  approach: 'Approach', waiting_outside: 'Waiting outside', anchored: 'Anchored', moving_in: 'Moving in', working: 'Working', ready_to_depart: 'Ready to depart', moving_out: 'Moving out',
};
export const stateWord = (s: string): string => STATE_WORD[s] ?? s;

type Card = { x: number; y: number; title: string; lines: string[] } | null;

export function MapView(p: MapViewProps) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const viewRef = useRef<View>({ cx: 0, cy: 0, scale: 0.05, w: 640, h: 420 });
  const fitRef = useRef(0.05);
  const raf = useRef(0);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const setRef = useRef<VesselSet | null>(null);
  const hoverRef = useRef<Hover | null>(null);
  const selRef = useRef<number | null>(null);
  const dragRef = useRef<{ x: number; y: number; cx: number; cy: number; moved: boolean } | null>(null);
  const pinch = useRef(createPinchTracker()); // touch: two-finger pinch zoom and pan
  const [size, setSize] = useState({ w: 640, h: 420 });
  const [card, setCard] = useState<Card>(null);
  const [tick, setTick] = useState(0); // bumps on view changes that the HUD shows (scale bar)
  const [hullView, setHullView] = useState(false);
  const hours = usePlayhead(p.playhead);

  const paths = useMemo<PathSet | null>(() => (typeof Path2D === 'undefined' ? null : buildPaths(p.prep, Path2D)), [p.prep]);
  const groupIds = useMemo(() => (p.frames?.groups ?? []).map((g) => g.id), [p.frames]);
  const berthCounts = useMemo(() => new Map((p.frames?.groups ?? []).map((g) => [g.id, g.berths])), [p.frames]);

  const draw = useCallback(() => {
    raf.current = 0;
    const cv = canvas.current; if (!cv || !paths) return;
    const ctx = cv.getContext('2d'); if (!ctx) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const v = viewRef.current;
    if (cv.width !== Math.round(v.w * dpr) || cv.height !== Math.round(v.h * dpr)) { cv.width = Math.round(v.w * dpr); cv.height = Math.round(v.h * dpr); }
    const pal = paletteFor(themeFromAttr(document.documentElement.getAttribute('data-theme')), p.colour);
    let vs: VesselSet | null = null;
    if (p.frames && p.layout && p.frames.frames.length) {
      const h = p.playhead.get();
      const { i, frac } = frameAt(p.frames, h);
      vs = placeFrame(p.layout, p.frames, p.frames.frames[i], { loaScale: p.loaScale, next: p.frames.frames[i + 1] ?? null, frac });
    }
    setRef.current = vs;
    const ef = p.scenario.length ? effectsAt(p.scenario, p.playhead.get(), groupIds) : null;
    const underlay: Underlay | null = p.imageOn && imgRef.current && imgRef.current.complete && imgRef.current.naturalWidth
      ? { image: imgRef.current, bounds: imageBoundsM(p.prep, p.imageBounds), opacity: p.imageOpacity } : null;
    const stats = drawMap(ctx, {
      prep: p.prep, paths, view: v, dpr, pal, vis: p.vis, glow: p.glow, labels: p.labels, labelCands: p.labelCands, underlay, vesselsOn: p.vesselsOn,
      layout: p.layout, vessels: vs, effects: ef, berthCounts, hover: hoverRef.current, selectedVessel: selRef.current,
    });
    const w = wrap.current;
    if (w) { w.dataset.vessels = String(stats.vessels); w.dataset.hulls = String(stats.hulls); w.dataset.labels = String(stats.labels); w.dataset.ms = stats.ms.toFixed(1); w.dataset.scale = v.scale.toFixed(5); w.dataset.cx = v.cx.toFixed(1); w.dataset.cy = v.cy.toFixed(1);
      const fv = vs?.vessels.find((x) => x.state === 'working');
      if (fv) { const [fx, fy] = toScreen(v, fv.x, fv.y); w.dataset.focus = `${(fx / v.w).toFixed(3)},${(fy / v.h).toFixed(3)}`; } else delete w.dataset.focus; }
    p.onStats?.(stats);
    const hv = v.scale * classLoa('container/neopanamax') >= HULL_MID;
    setHullView((o) => (o === hv ? o : hv));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.prep, paths, p.colour, p.glow, p.labels, p.labelCands, p.vis, p.imageOn, p.imageOpacity, p.vesselsOn, p.layout, p.frames, p.loaScale, p.scenario, groupIds, berthCounts, p.imageBounds]);

  const schedule = useCallback(() => { if (!raf.current) raf.current = requestAnimationFrame(draw); }, [draw]);
  useEffect(() => { schedule(); }, [schedule, hours, tick, size]);
  useEffect(() => () => { if (raf.current) cancelAnimationFrame(raf.current); raf.current = 0; }, []);

  // theme changes (the root attribute) redraw
  useEffect(() => {
    const mo = new MutationObserver(() => schedule());
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-skin'] });
    return () => mo.disconnect();
  }, [schedule]);

  // size
  useEffect(() => {
    const el = wrap.current; if (!el) return undefined;
    const ro = new ResizeObserver(() => {
      const r = el.getBoundingClientRect();
      const w = Math.max(200, Math.round(r.width)); const h = Math.max(160, Math.round(r.height));
      setSize((o) => (o.w === w && o.h === h ? o : { w, h }));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // fit on a new port or a resize that has not been touched
  const touched = useRef(false);
  useEffect(() => { touched.current = false; }, [p.placeId]);
  useEffect(() => {
    const prevFit = fitRef.current;
    const f = fitView(p.prep.bboxM, size.w, size.h);
    fitRef.current = f.scale;
    const cur = viewRef.current;
    if (!touched.current) viewRef.current = f;
    else viewRef.current = { ...cur, w: size.w, h: size.h, scale: Math.max(cur.scale * (f.scale / prevFit), f.scale * 0.6) };
    setTick((t) => t + 1);
  }, [p.prep, size.w, size.h, p.placeId]);

  // underlay image
  useEffect(() => {
    if (!p.imageUrl || !p.imageOn) { imgRef.current = null; return undefined; }
    const im = new Image();
    im.onload = () => schedule();
    im.src = p.imageUrl; imgRef.current = im;
    return () => { im.onload = null; };
  }, [p.imageUrl, p.imageOn, schedule]);

  // playhead drives redraws of vessels
  useEffect(() => p.playhead.subscribe(schedule), [p.playhead, schedule]);

  const setView = useCallback((v: View, user = true) => {
    if (user) touched.current = true;
    viewRef.current = clampView(v, p.prep.bboxM);
    setTick((t) => t + 1);
  }, [p.prep]);
  const zoomBy = useCallback((f: number, sx?: number, sy?: number) => {
    const v = viewRef.current;
    setView(zoomAt(v, sx ?? v.w / 2, sy ?? v.h / 2, f, fitRef.current * 0.6, MAX_SCALE));
  }, [setView]);
  const refit = useCallback(() => { touched.current = false; setView(fitView(p.prep.bboxM, viewRef.current.w, viewRef.current.h), false); }, [p.prep, setView]);

  // wheel needs a non-passive listener
  useEffect(() => {
    const cv = canvas.current; if (!cv) return undefined;
    const on = (e: WheelEvent) => {
      e.preventDefault();
      const r = cv.getBoundingClientRect();
      zoomBy(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0016)), e.clientX - r.left, e.clientY - r.top);
    };
    cv.addEventListener('wheel', on, { passive: false });
    return () => cv.removeEventListener('wheel', on);
  }, [zoomBy]);

  const pointerPos = (e: React.PointerEvent): [number, number] => {
    const r = canvas.current!.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  };
  const onDown = (e: React.PointerEvent) => {
    const [x, y] = pointerPos(e);
    const v = viewRef.current;
    pinch.current.down(e.pointerId, { x, y });
    /* a second finger turns the drag into a pinch; the pointer stays captured so the gesture follows it off the canvas */
    dragRef.current = pinch.current.active() ? null : { x, y, cx: v.cx, cy: v.cy, moved: false };
    if (pinch.current.active()) { setCard(null); hoverRef.current = null; }
    try { (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId); } catch { /* a pointer that is no longer active cannot be captured; the gesture still works on the canvas */ }
  };
  const updateHover = (x: number, y: number) => {
    const v = viewRef.current;
    let h: Hover | null = null; let c: Card = null;
    const vs = setRef.current;
    const pv = vs && p.vesselsOn ? pickVessel(vs.vessels, v, x, y) : null;
    if (pv) {
      h = { kind: 'vessel', id: String(pv.id) };
      c = { x, y, title: `Vessel ${pv.id}`, lines: vesselLines(pv, p.frames) };
    } else {
      const [wx, wy] = toWorld(v, x, y);
      const t = p.vis.terminals ? terminalAt(p.prep, wx, wy) : null;
      if (t) {
        h = { kind: 'terminal', id: t.osm };
        const gid = `bg_${t.osm}`;
        const grp = p.frames?.groups.find((g) => g.id === gid);
        const lines = [t.kind && t.kind !== 'unknown' ? t.kind : null, t.operator ?? null, grp ? `${grp.berths} berths` : null].filter(Boolean) as string[];
        c = { x, y, title: t.name ?? 'Terminal', lines };
      }
    }
    const prev = hoverRef.current;
    if (prev?.kind !== h?.kind || prev?.id !== h?.id) { hoverRef.current = h; schedule(); }
    setCard(c);
  };
  const onMove = (e: React.PointerEvent) => {
    const [x, y] = pointerPos(e);
    if (pinch.current.active()) {
      const nv = pinch.current.move(e.pointerId, { x, y }, viewRef.current, fitRef.current * 0.6, MAX_SCALE);
      if (nv) { setView(nv); setCard(null); }
      return;
    }
    const d = dragRef.current;
    if (d) {
      const dx = x - d.x; const dy = y - d.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true;
      if (d.moved) { const v = viewRef.current; setView({ ...v, cx: d.cx - dx / v.scale, cy: d.cy + dy / v.scale }); setCard(null); return; }
    }
    updateHover(x, y);
  };
  const onUp = (e: React.PointerEvent) => {
    const wasPinch = pinch.current.active();
    pinch.current.up(e.pointerId);
    const d = dragRef.current; dragRef.current = null;
    if (wasPinch) return; // lifting a finger ends the gesture; it is not a click
    if (d && !d.moved) {
      const [x, y] = pointerPos(e);
      const vs = setRef.current;
      const pv = vs && p.vesselsOn ? pickVessel(vs.vessels, viewRef.current, x, y) : null;
      selRef.current = pv ? pv.id : null; schedule();
    }
  };
  const onKey = (e: React.KeyboardEvent) => {
    const v = viewRef.current; const step = 60 / v.scale;
    if (e.key === '+' || e.key === '=') zoomBy(1.4);
    else if (e.key === '-') zoomBy(1 / 1.4);
    else if (e.key === '0') refit();
    else if (e.key === 'ArrowLeft') setView({ ...v, cx: v.cx - step });
    else if (e.key === 'ArrowRight') setView({ ...v, cx: v.cx + step });
    else if (e.key === 'ArrowUp') setView({ ...v, cy: v.cy + step });
    else if (e.key === 'ArrowDown') setView({ ...v, cy: v.cy - step });
    else return;
    e.preventDefault();
  };

  const sb = scaleBar(viewRef.current.scale);
  const tray = setRef.current?.tray;
  const zoomPct = Math.round((viewRef.current.scale / Math.max(1e-6, fitScale(p.prep.bboxM, size.w, size.h))) * 100);

  return (
    <div className="ptw-map" ref={wrap} data-place={p.placeId}>
      <canvas ref={canvas} className="ptw-canvas" tabIndex={0} role="application" aria-label="Port map"
        style={{ width: size.w, height: size.h }}
        onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={(e) => { pinch.current.up(e.pointerId); dragRef.current = null; }}
        onPointerLeave={() => { hoverRef.current = null; setCard(null); schedule(); }}
        onDoubleClick={(e) => { const r = canvas.current!.getBoundingClientRect(); zoomBy(2, e.clientX - r.left, e.clientY - r.top); }}
        onKeyDown={onKey} />
      <div className="ptw-hud ptw-hud--tl"><Tags chips={p.chips} /></div>
      <div className="ptw-hud ptw-hud--tr">
        <IconButton icon="plus" label="Zoom in" size="sm" variant="secondary" onClick={() => zoomBy(1.5)} />
        <IconButton icon="minus" label="Zoom out" size="sm" variant="secondary" onClick={() => zoomBy(1 / 1.5)} />
        <IconButton icon="maximize" label="Fit port" size="sm" variant="secondary" onClick={refit} />
      </div>
      <div className="ptw-hud ptw-hud--bl">
        <span className="ptw-scale" style={{ width: Math.max(24, sb.px) }}><i /><b>{sb.label}</b></span>
        <span className="ptw-zoom" data-hull={hullView}>{hullView ? 'Hulls' : 'Points'} {'·'} {zoomPct}%</span>
      </div>
      <div className="ptw-hud ptw-hud--br"><Tags chips={sourceChips(p.imageOn)} className="ptw-src" /></div>
      {tray && p.vesselsOn && (tray.approach + tray.outside + tray.anchoredUnmapped + tray.unplaced > 0) && (
        <div className="ptw-hud ptw-hud--tray" role="group" aria-label="Vessels not on the map">
          <span className="ptw-trayc" data-k="outside"><b>{tray.outside}</b>Outside</span>
          <span className="ptw-trayc" data-k="approach"><b>{tray.approach}</b>Approach</span>
          {tray.anchoredUnmapped > 0 && <span className="ptw-trayc" data-k="anchored"><b>{tray.anchoredUnmapped}</b>Anchored</span>}
          {tray.unplaced > 0 && <span className="ptw-trayc" data-k="unplaced"><b>{tray.unplaced}</b>Not placed</span>}
        </div>
      )}
      {card && (
        <div className="ptw-card" style={{ left: Math.min(card.x + 12, size.w - 180), top: Math.min(card.y + 12, size.h - 70) }} role="status">
          <b>{card.title}</b>
          {card.lines.map((l) => <span key={l}>{l}</span>)}
        </div>
      )}
    </div>
  );
}

function vesselLines(v: PlacedVessel, frames: FramesDoc | null): string[] {
  const g = v.group ? frames?.groups.find((x) => x.id === v.group) : null;
  return [v.cls, stateWord(v.state), g?.name ?? (v.state === 'anchored' ? 'Anchorage' : '')].filter(Boolean);
}

/** Underlay bounds in metres from the image's [lon0, lat0, lon1, lat1]. */
function imageBoundsM(prep: Prepared, b: [number, number, number, number] | undefined): [number, number, number, number] {
  if (!b) return prep.bboxM;
  const kx = prep.proj.kx; const ky = prep.proj.ky;
  return [(b[0] - prep.proj.lon0) * kx, (b[1] - prep.proj.lat0) * ky, (b[2] - prep.proj.lon0) * kx, (b[3] - prep.proj.lat0) * ky];
}
