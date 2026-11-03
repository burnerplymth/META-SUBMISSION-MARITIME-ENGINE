import React, { useEffect, useMemo, useRef } from 'react';
import { bboxRect, decodeRle, detectionGeometry, paintMask, ringsToPath, type OverlayDetection } from './model';

/* Detection layers per satwatch-overlay/1: points, boxes, polygons as SVG
 * in norm1000 space, COCO-RLE masks painted into one canvas. Moved out of
 * SatelliteDeskPanel unchanged so the single-source viewer draws on the
 * same layers. Geometry a detector did not produce is never drawn. */
export function SvgLayer({ dets }: { dets: OverlayDetection[] }) {
  return (
    <svg className="sw2-ov" viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true" focusable="false">
      {dets.map((d) => {
        const g = detectionGeometry(d);
        const tip = <title>{[d.label, d.zone, d.score != null ? `score ${d.score}` : null].filter(Boolean).join(' · ')}</title>;
        if (g.kind === 'point') return <circle key={d.id} className="sw2-pt" cx={g.x} cy={g.y} r={7}>{tip}</circle>;
        if (g.kind === 'bbox') { const r = bboxRect(g.box); return <rect key={d.id} className="sw2-bx" x={r.x} y={r.y} width={r.w} height={r.h}>{tip}</rect>; }
        if (g.kind === 'polygon') return <path key={d.id} className="sw2-pg" d={ringsToPath(g.rings)}>{tip}</path>;
        return null;
      })}
    </svg>
  );
}

/** COCO-RLE masks are pixel-space: painted into one canvas the size of
 *  the first mask, then stretched over the crop. */
export function RleLayer({ dets }: { dets: OverlayDetection[] }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const masks = useMemo(() => dets.map((d) => detectionGeometry(d)).filter((g): g is Extract<ReturnType<typeof detectionGeometry>, { kind: 'rle' }> => g.kind === 'rle'), [dets]);
  useEffect(() => {
    const c = ref.current;
    if (!c || masks.length === 0) return;
    const [h, w] = masks[0].size;
    c.width = w; c.height = h;
    const ctx = c.getContext('2d');
    if (!ctx) return;
    const img = ctx.createImageData(w, h);
    for (const m of masks) {
      try { paintMask(img.data, w, h, decodeRle(m.counts, m.size), m.size, [232, 147, 12, 120]); } catch { /* an undecodable mask is skipped, never faked */ }
    }
    ctx.putImageData(img, 0, 0);
  }, [masks]);
  if (masks.length === 0) return null;
  return <canvas ref={ref} className="sw2-rle" aria-hidden="true" />;
}

export function Layers({ dets, show }: { dets: OverlayDetection[]; show: boolean }) {
  if (!show || dets.length === 0) return null;
  return <><RleLayer dets={dets} /><SvgLayer dets={dets} /></>;
}
