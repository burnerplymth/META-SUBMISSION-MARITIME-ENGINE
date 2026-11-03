/* PORT TWIN canvas renderer. One 2D canvas, plain Path2D: the geometry is
 * turned into one Path2D per layer once per port (buildPaths) and each
 * frame is a handful of fill / stroke calls under a metre-to-pixel
 * transform, so a pan costs about the same on Rotterdam as on Singapore.
 * Vessels, hulls, glows and labels are drawn in pixel space on top.
 * No DOM reads here (the caller passes the theme, size and images), so the
 * module can run against a recording context. */
import {
  toScreen, type LabelCand, type LayerVis, type PFeature, type Prepared, type View, type XY,
} from './geo';
import type { CargoKind } from './types';
import type { Palette } from './palette';
import type { PlacedVessel, Tray } from './vessels';
import type { Layout } from './berths';
import { closedCount, type Effects } from './scenario';

export type Ctx2D = CanvasRenderingContext2D;
export type Path2DCtor = new () => Path2D;

export interface PathSet {
  land: Path2D | null; water: Path2D; coast: Path2D; building: Path2D;
  roadMajor: Path2D; roadMinor: Path2D; railMain: Path2D; railOther: Path2D;
  yard: Path2D; storage: Path2D; terminal: Map<CargoKind, Path2D>; terminalOutline: Path2D; anchorage: Path2D;
  fairway: Path2D; lock: Path2D; breakwaterLine: Path2D; breakwaterPoly: Path2D; pierLine: Path2D; pierPoly: Path2D; quay: Path2D;
}

function ring(p: Path2D, r: readonly XY[], close: boolean): void {
  if (r.length === 0) return;
  p.moveTo(r[0][0], r[0][1]);
  for (let i = 1; i < r.length; i += 1) p.lineTo(r[i][0], r[i][1]);
  if (close) p.closePath();
}
function polys(p: Path2D, f: PFeature): void { for (const pg of f.polys) for (const r of pg) ring(p, r, true); }
function lines(p: Path2D, f: PFeature): void { for (const l of f.lines) ring(p, l, false); }

const MAJOR_ROAD = new Set(['motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary', 'primary_link', 'secondary', 'secondary_link']);

export function buildPaths(prep: Prepared, P: Path2DCtor): PathSet {
  const mk = () => new P();
  const set: PathSet = {
    land: null, water: mk(), coast: mk(), building: mk(), roadMajor: mk(), roadMinor: mk(), railMain: mk(), railOther: mk(),
    yard: mk(), storage: mk(), terminal: new Map(), terminalOutline: mk(), anchorage: mk(), fairway: mk(), lock: mk(),
    breakwaterLine: mk(), breakwaterPoly: mk(), pierLine: mk(), pierPoly: mk(), quay: mk(),
  };
  if (prep.land.length) { const l = mk(); for (const r of prep.land) ring(l, r, true); set.land = l; }
  for (const [layer, feats] of prep.byLayer) {
    for (const f of feats) {
      switch (layer) {
        case 'water': polys(set.water, f); break;
        case 'coastline': lines(set.coast, f); break;
        case 'building': polys(set.building, f); break;
        case 'road': lines(MAJOR_ROAD.has(f.kind ?? '') ? set.roadMajor : set.roadMinor, f); break;
        case 'rail': lines(f.kind === 'main' ? set.railMain : set.railOther, f); break;
        case 'yard': polys(set.yard, f); break;
        case 'storage': polys(set.storage, f); break;
        case 'terminal': {
          const k = (f.kind as CargoKind) ?? 'unknown';
          let tp = set.terminal.get(k); if (!tp) { tp = mk(); set.terminal.set(k, tp); }
          polys(tp, f); polys(set.terminalOutline, f); break;
        }
        case 'anchorage': polys(set.anchorage, f); break;
        case 'fairway': lines(set.fairway, f); polys(set.fairway, f); break;
        case 'lock': lines(set.lock, f); polys(set.lock, f); break;
        case 'breakwater': lines(set.breakwaterLine, f); polys(set.breakwaterPoly, f); break;
        case 'pier': lines(set.pierLine, f); polys(set.pierPoly, f); break;
        case 'quay': lines(set.quay, f); break;
        default: break;
      }
    }
  }
  return set;
}

/* ── draw input ─────────────────────────────────────────────────────── */
export interface Underlay { image: CanvasImageSource; bounds: [number, number, number, number]; opacity: number }
export interface Hover { kind: 'vessel' | 'terminal'; id: string }
export interface DrawInput {
  prep: Prepared; paths: PathSet; view: View; dpr: number; pal: Palette; vis: LayerVis;
  glow: boolean; labels: boolean; labelCands: LabelCand[];
  underlay: Underlay | null; vesselsOn: boolean;
  layout: Layout | null; vessels: VesselSet2 | null; effects: Effects | null; berthCounts: Map<string, number>;
  hover: Hover | null; selectedVessel: number | null;
}
export interface VesselSet2 { vessels: PlacedVessel[]; tray: Tray }
export interface DrawStats { ms: number; vessels: number; hulls: number; labels: number }

const smooth = (a: number, b: number, x: number): number => { const t = Math.max(0, Math.min(1, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
const A = (hex: string, a: number): string => {
  const h = hex.length === 9 ? hex.slice(0, 7) : hex;
  return `${h}${Math.round(Math.max(0, Math.min(1, a)) * 255).toString(16).padStart(2, '0')}`;
};

const FONT = '600 11px "IBM Plex Sans", system-ui, sans-serif';

export function drawMap(ctx: Ctx2D, d: DrawInput): DrawStats {
  const t0 = typeof performance !== 'undefined' ? performance.now() : 0;
  const { view, pal, vis, paths, prep } = d;
  const s = view.scale; const dpr = d.dpr;
  const stats: DrawStats = { ms: 0, vessels: 0, hulls: 0, labels: 0 };
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, view.w, view.h);
  /* The canvas is painted in the base ground colour edge to edge: a fit that leaves a band around the
   * extent shows ground, never a blank strip. The extent is still outlined, so where the data ends stays visible. */
  ctx.fillStyle = vis.water && prep.hasCoast ? pal.sea : pal.land; ctx.fillRect(0, 0, view.w, view.h);

  // metre space: x_px = w/2 + (x - cx) s, y_px = h/2 - (y - cy) s
  const geo = () => ctx.setTransform(dpr * s, 0, 0, -dpr * s, dpr * (view.w / 2 - view.cx * s), dpr * (view.h / 2 + view.cy * s));
  const px = () => ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const lw = (n: number) => n / s;
  const [x0, y0, x1, y1] = prep.bboxM;
  const rect = (): void => { ctx.beginPath(); ctx.rect(x0, y0, x1 - x0, y1 - y0); };

  geo();
  ctx.lineJoin = 'round'; ctx.lineCap = 'round';
  // sea and land
  if (vis.water) { ctx.fillStyle = prep.hasCoast ? pal.sea : pal.land; rect(); ctx.fill(); }
  else { ctx.fillStyle = pal.land; rect(); ctx.fill(); }
  if (vis.land && prep.hasCoast && paths.land) { ctx.fillStyle = pal.land; ctx.fill(paths.land, 'evenodd'); }
  if (vis.water) {
    ctx.fillStyle = pal.sea; ctx.fill(paths.water, 'evenodd');
    ctx.strokeStyle = pal.coast; ctx.lineWidth = lw(1); ctx.stroke(paths.coast);
  }
  px();
  // Sentinel-2 underlay
  if (d.underlay) {
    const [b0, b1, b2, b3] = d.underlay.bounds;
    const tl = toScreen(view, b0, b3); const br = toScreen(view, b2, b1);
    ctx.save(); ctx.globalAlpha = d.underlay.opacity; ctx.imageSmoothingEnabled = true;
    try { ctx.drawImage(d.underlay.image, tl[0], tl[1], br[0] - tl[0], br[1] - tl[1]); } catch { /* image not decoded yet */ }
    ctx.restore();
  }
  geo();
  ctx.strokeStyle = pal.extent; ctx.lineWidth = lw(1); rect(); ctx.stroke();

  // land use and transport
  if (vis.land && s > 0.1) { ctx.fillStyle = pal.building; ctx.fill(paths.building, 'evenodd'); }
  if (vis.yards) {
    ctx.fillStyle = pal.storage; ctx.fill(paths.storage, 'evenodd');
    ctx.fillStyle = d.effects && d.effects.yardFactor < 1 ? A(pal.outage, 0.38) : pal.yard; ctx.fill(paths.yard, 'evenodd');
  }
  if (vis.transport) {
    if (s > 0.045) { ctx.strokeStyle = pal.roadMinor; ctx.lineWidth = lw(Math.max(0.7, 3 * s)); ctx.stroke(paths.roadMinor); }
    ctx.strokeStyle = pal.roadCase; ctx.lineWidth = lw(Math.max(1.6, 7 * s) + 1); ctx.stroke(paths.roadMajor);
    ctx.strokeStyle = pal.roadMajor; ctx.lineWidth = lw(Math.max(1.2, 7 * s)); ctx.stroke(paths.roadMajor);
    ctx.strokeStyle = pal.rail; ctx.lineWidth = lw(1); ctx.setLineDash([lw(5), lw(3)]); ctx.stroke(paths.railMain);
    if (s > 0.05) ctx.stroke(paths.railOther);
    ctx.setLineDash([]);
  }

  // terminals
  if (vis.terminals) {
    for (const [k, p] of paths.terminal) { ctx.fillStyle = pal.terminalFill[k] ?? pal.terminalFill.unknown; ctx.fill(p, 'evenodd'); }
    if (d.effects && d.effects.strikeFactor.size) {
      ctx.fillStyle = A(pal.strike, 0.1); for (const p of paths.terminal.values()) ctx.fill(p, 'evenodd');
    }
    ctx.strokeStyle = pal.terminalLine; ctx.lineWidth = lw(1); ctx.stroke(paths.terminalOutline);
    if (d.hover?.kind === 'terminal') {
      const f = (prep.byLayer.get('terminal') ?? []).find((t) => t.osm === d.hover!.id);
      if (f) { const hp = new Path2D(); for (const pg of f.polys) for (const r of pg) { hp.moveTo(r[0][0], r[0][1]); for (let i = 1; i < r.length; i += 1) hp.lineTo(r[i][0], r[i][1]); hp.closePath(); } ctx.strokeStyle = pal.label; ctx.lineWidth = lw(1.6); ctx.stroke(hp); }
    }
  }
  // anchorages
  if (vis.anchorages) {
    ctx.fillStyle = pal.anchorageFill; ctx.fill(paths.anchorage, 'evenodd');
    ctx.strokeStyle = A(pal.anchorage, 0.85); ctx.lineWidth = lw(1.2); ctx.setLineDash([lw(6), lw(4)]); ctx.stroke(paths.anchorage); ctx.setLineDash([]);
  }
  // piers, breakwaters, quays
  if (vis.quays) {
    ctx.fillStyle = pal.pier; ctx.fill(paths.pierPoly, 'evenodd');
    ctx.strokeStyle = pal.pierLine; ctx.lineWidth = lw(1); ctx.stroke(paths.pierPoly);
    ctx.lineWidth = lw(Math.max(1.4, 9 * s)); ctx.strokeStyle = pal.pier; ctx.stroke(paths.pierLine);
    ctx.lineWidth = lw(1); ctx.strokeStyle = pal.pierLine; ctx.stroke(paths.pierLine);
    ctx.fillStyle = pal.pier; ctx.fill(paths.breakwaterPoly, 'evenodd');
    ctx.strokeStyle = pal.breakwater; ctx.lineWidth = lw(Math.max(1.4, 6 * s)); ctx.stroke(paths.breakwaterLine); ctx.lineWidth = lw(1); ctx.stroke(paths.breakwaterPoly);
    if (d.glow) { ctx.strokeStyle = A(pal.quay, 0.14); ctx.lineWidth = lw(7); ctx.stroke(paths.quay); }
    ctx.strokeStyle = pal.quay; ctx.lineWidth = lw(2); ctx.stroke(paths.quay);
  }
  // fairways and locks
  if (vis.fairways) {
    const shut = !!d.effects?.channelClosed;
    const fc = shut ? pal.fairwayShut : pal.fairway;
    if (d.glow) { ctx.strokeStyle = A(fc, 0.16); ctx.lineWidth = lw(6); ctx.stroke(paths.fairway); }
    ctx.strokeStyle = A(fc, 0.9); ctx.lineWidth = lw(1.2); ctx.setLineDash([lw(8), lw(5)]); ctx.stroke(paths.fairway); ctx.setLineDash([]);
    ctx.strokeStyle = pal.lock; ctx.lineWidth = lw(1.5); ctx.stroke(paths.lock);
  }
  px();

  // points: berths, cranes
  if (vis.berths) {
    const r = Math.max(1.4, Math.min(3, s * 12));
    ctx.fillStyle = A(pal.berth, 0.9);
    for (const f of prep.byLayer.get('berth') ?? []) {
      for (const p of f.points) { const [sx, sy] = toScreen(view, p[0], p[1]); if (sx < -4 || sy < -4 || sx > view.w + 4 || sy > view.h + 4) continue; ctx.beginPath(); ctx.arc(sx, sy, r, 0, 6.2832); ctx.fill(); }
    }
    drawSlots(ctx, d);
  }
  if (vis.cranes) {
    const r = Math.max(1.2, Math.min(3.2, s * 14));
    for (const f of prep.byLayer.get('crane') ?? []) {
      for (const p of f.points) {
        const [sx, sy] = toScreen(view, p[0], p[1]);
        if (sx < -6 || sy < -6 || sx > view.w + 6 || sy > view.h + 6) continue;
        if (d.glow) { ctx.fillStyle = A(pal.crane, 0.16); ctx.beginPath(); ctx.arc(sx, sy, r * 2.6, 0, 6.2832); ctx.fill(); }
        ctx.fillStyle = pal.crane; ctx.fillRect(sx - r / 2, sy - r / 2, r, r);
      }
    }
  }

  // vessels
  if (d.vesselsOn && d.vessels) drawVessels(ctx, d, stats);

  // labels
  if (d.labels) stats.labels = drawLabels(ctx, d);
  stats.ms = (typeof performance !== 'undefined' ? performance.now() : 0) - t0;
  return stats;
}

/** Berth slot ticks, with a cross on the slots a closure has shut. */
function drawSlots(ctx: Ctx2D, d: DrawInput): void {
  const { layout, view, pal } = d;
  if (!layout || view.scale < 0.12) return;
  const closed = new Map<string, number>();
  if (d.effects) for (const [g, total] of d.berthCounts) closed.set(g, closedCount(d.effects, g, total));
  for (const b of layout.berths) {
    if (!b) continue;
    const [sx, sy] = toScreen(view, b.x + b.nx * 4, b.y + b.ny * 4);
    if (sx < -8 || sy < -8 || sx > view.w + 8 || sy > view.h + 8) continue;
    ctx.strokeStyle = A(pal.berth, 0.55); ctx.lineWidth = 1;
    const len = Math.min(b.lengthM * view.scale * 0.5, 40);
    const ax = Math.cos(b.heading) * len; const ay = -Math.sin(b.heading) * len;
    ctx.beginPath(); ctx.moveTo(sx - ax, sy - ay); ctx.lineTo(sx + ax, sy + ay); ctx.stroke();
  }
  if (closed.size) {
    const byGroup = new Map<string, Array<{ b: NonNullable<Layout['berths'][number]>; slot: number }>>();
    layout.berths.forEach((b, i) => { if (!b) return; const a = byGroup.get(b.group) ?? []; a.push({ b, slot: i }); byGroup.set(b.group, a); });
    for (const [g, list] of byGroup) {
      const n = closed.get(g) ?? 0; if (!n) continue;
      const ordered = list.sort((a, b2) => a.slot - b2.slot);
      for (const { b } of ordered.slice(Math.max(0, ordered.length - n))) {
        const [sx, sy] = toScreen(view, b.x + b.nx * 6, b.y + b.ny * 6);
        ctx.strokeStyle = pal.strike; ctx.lineWidth = 1.6;
        ctx.beginPath(); ctx.moveTo(sx - 4, sy - 4); ctx.lineTo(sx + 4, sy + 4); ctx.moveTo(sx + 4, sy - 4); ctx.lineTo(sx - 4, sy + 4); ctx.stroke();
      }
    }
  }
}

/** Hull outline in pixel space. */
function hullPath(ctx: Ctx2D, sx: number, sy: number, lenPx: number, beamPx: number, headingRad: number): void {
  const c = Math.cos(-headingRad); const sn = Math.sin(-headingRad);
  const pts: XY[] = [[-lenPx / 2, -beamPx / 2], [-lenPx / 2, beamPx / 2], [lenPx * 0.22, beamPx / 2], [lenPx / 2, 0], [lenPx * 0.22, -beamPx / 2]];
  ctx.beginPath();
  pts.forEach(([px, py], i) => {
    const x = sx + px * c - py * sn; const y = sy + px * sn + py * c;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.closePath();
}

/** Zoom thresholds (hull length in px): a glowing point below HULL_FROM, a hull above HULL_FULL, a blend between. */
export const HULL_FROM = 22;
export const HULL_FULL = 44;
export const HULL_MID = (HULL_FROM + HULL_FULL) / 2;

function drawVessels(ctx: Ctx2D, d: DrawInput, stats: DrawStats): void {
  const { view, pal } = d;
  const list = d.vessels!.vessels;
  const prevComp = ctx.globalCompositeOperation;
  for (const v of list) {
    const [sx, sy] = toScreen(view, v.x, v.y);
    if (sx < -30 || sy < -30 || sx > view.w + 30 || sy > view.h + 30) continue;
    stats.vessels += 1;
    const col = pal.kind[v.kind];
    const lenPx = v.loaM * view.scale; const beamPx = Math.max(1.6, v.beamM * view.scale);
    const hull = smooth(HULL_FROM, HULL_FULL, lenPx);
    const r = 2.2 * (v.state === 'working' ? 1.15 : 1);
    // glow: core disc plus halos at 2.2x and 4x, alpha .30 and .10 (additive on dark)
    if (d.glow) {
      const g = 1 - hull * 0.6;
      if (pal.additive) ctx.globalCompositeOperation = 'lighter';
      for (const [k, a] of [[4, 0.1], [2.2, 0.3]] as const) {
        const rr = r * k;
        const grad = ctx.createRadialGradient(sx, sy, 0, sx, sy, rr);
        grad.addColorStop(0, A(col, a * g * (pal.additive ? 1 : 0.7))); grad.addColorStop(1, A(col, 0));
        ctx.fillStyle = grad; ctx.beginPath(); ctx.arc(sx, sy, rr, 0, 6.2832); ctx.fill();
      }
      ctx.globalCompositeOperation = prevComp;
    }
    if (hull > 0.02) {
      stats.hulls += 1;
      ctx.globalAlpha = hull;
      hullPath(ctx, sx, sy, Math.max(lenPx, 4), beamPx, v.heading);
      ctx.fillStyle = v.state === 'anchored' ? A(col, 0.55) : col; ctx.fill();
      ctx.strokeStyle = pal.hullEdge; ctx.lineWidth = 0.8; ctx.stroke();
      ctx.globalAlpha = 1;
    }
    if (hull < 0.98) {
      ctx.globalAlpha = 1 - hull * 0.9;
      ctx.fillStyle = col; ctx.beginPath(); ctx.arc(sx, sy, r, 0, 6.2832); ctx.fill();
      if (!pal.additive) { ctx.strokeStyle = pal.hullEdge; ctx.lineWidth = 1; ctx.stroke(); }
      ctx.globalAlpha = 1;
    }
    if (v.state === 'anchored') { ctx.strokeStyle = A(col, 0.7); ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(sx, sy, r + 2.5, 0, 6.2832); ctx.stroke(); }
    if (v.state === 'moving_in' || v.state === 'moving_out') {
      ctx.strokeStyle = A(col, 0.5); ctx.lineWidth = 1; ctx.beginPath();
      const tx = Math.cos(v.heading) * 14; const ty = -Math.sin(v.heading) * 14;
      ctx.moveTo(sx, sy); ctx.lineTo(sx - tx, sy - ty); ctx.stroke();
    }
    if (d.selectedVessel === v.id || (d.hover?.kind === 'vessel' && d.hover.id === String(v.id))) {
      ctx.strokeStyle = pal.label; ctx.lineWidth = 1.4; ctx.beginPath(); ctx.arc(sx, sy, Math.max(7, lenPx / 2 + 3), 0, 6.2832); ctx.stroke();
    }
  }
}

/** Greedy label placement by priority with rectangle collision. Returns the number drawn. */
export function placeLabels(ctx: Ctx2D, cands: readonly LabelCand[], view: View, max = 60): Array<{ text: string; x: number; y: number; w: number; layer: string }> {
  ctx.font = FONT;
  const placed: Array<{ x0: number; y0: number; x1: number; y1: number }> = [];
  const out: Array<{ text: string; x: number; y: number; w: number; layer: string }> = [];
  for (const c of cands) {
    if (out.length >= max) break;
    const [sx, sy] = toScreen(view, c.x, c.y);
    if (sy < 8 || sy > view.h - 8) continue;
    const w = ctx.measureText(c.text).width;
    if (sx - w / 2 < 6 || sx + w / 2 > view.w - 6) continue;
    const r = { x0: sx - w / 2 - 3, y0: sy - 7, x1: sx + w / 2 + 3, y1: sy + 7 };
    if (placed.some((p) => r.x0 < p.x1 && r.x1 > p.x0 && r.y0 < p.y1 && r.y1 > p.y0)) continue;
    placed.push(r); out.push({ text: c.text, x: sx, y: sy, w, layer: c.layer });
  }
  return out;
}

function drawLabels(ctx: Ctx2D, d: DrawInput): number {
  const { view, pal, vis } = d;
  const cands = d.labelCands.filter((c) => (c.layer === 'terminal' ? vis.terminals : c.layer === 'anchorage' ? vis.anchorages : c.layer === 'pier' ? vis.quays : vis.water));
  const placed = placeLabels(ctx, cands, view);
  ctx.font = FONT; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.lineJoin = 'round';
  for (const l of placed) {
    ctx.strokeStyle = pal.labelHalo; ctx.lineWidth = 3.5; ctx.strokeText(l.text, l.x, l.y);
    ctx.fillStyle = l.layer === 'terminal' ? pal.label : pal.labelDim; ctx.fillText(l.text, l.x, l.y);
  }
  ctx.textAlign = 'start'; ctx.textBaseline = 'alphabetic';
  return placed.length;
}

/* ── picking ────────────────────────────────────────────────────────── */
export function pickVessel(vs: readonly PlacedVessel[], view: View, sx: number, sy: number): PlacedVessel | null {
  let best: PlacedVessel | null = null; let bd = Infinity;
  for (const v of vs) {
    const [x, y] = toScreen(view, v.x, v.y);
    const reach = Math.max(7, (v.loaM * view.scale) / 2 + 2);
    const dd = Math.hypot(x - sx, y - sy);
    if (dd <= reach && dd < bd) { best = v; bd = dd; }
  }
  return best;
}

/** A round scale-bar length (metres) for about `targetPx` pixels, and its pixel width. */
export function scaleBar(scale: number, targetPx = 90): { metres: number; px: number; label: string } {
  const raw = targetPx / scale;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const f = raw / pow;
  const m = (f >= 5 ? 5 : f >= 2 ? 2 : 1) * pow;
  return { metres: m, px: m * scale, label: m >= 1000 ? `${m / 1000} km` : `${m} m` };
}
