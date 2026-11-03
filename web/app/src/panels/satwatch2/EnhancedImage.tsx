import React, { useEffect, useRef, useState } from 'react';
import { Chip } from '../reports/chips';
import { cropSrc } from './client';
import { enhanceRgba, presetFor, upscalePlan } from './enhance';
import { NoDataOverlay } from './NoData';

/* ENHANCED VIEW. Presentation only: the stored crop is fetched, upscaled on
 * a canvas and sharpened / denoised in the browser. The result is synthetic
 * and never leaves this component: no detection, no count and no request
 * reads it. The frame carries the SYNTHETIC ENHANCEMENT chip for as long as
 * enhanced pixels are on screen. Detection layers are drawn by the parent
 * from the server's coordinates (norm1000), which are resolution-free. */

export function SynthChip({ inline = false }: { inline?: boolean }) {
  return (
    <span className={inline ? 'sw2-synth sw2-synth--inline' : 'sw2-synth'} data-chip="synthetic-enhancement">
      <Chip chip="FLAG" word="Synthetic enhancement" title="Presentation only" />
    </span>
  );
}

type St = 'loading' | 'ok' | 'err';

/** `url` is a served crop path ("/satwatch/crops/x.png"); `kind` picks the preset. */
export function EnhancedImage({ url, alt, kind, showChip = true }: { url: string; alt: string; kind: string; showChip?: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [st, setSt] = useState<St>('loading');
  useEffect(() => {
    const ctrl = new AbortController();
    let alive = true;
    setSt('loading');
    (async () => {
      try {
        const res = await fetch(cropSrc(url), { signal: ctrl.signal });
        if (!res.ok) throw new Error(`http_${res.status}`);
        const bmp = await createImageBitmap(await res.blob());
        const plan = upscalePlan(bmp.width, bmp.height);
        const c = ref.current;
        if (!alive || !c) return;
        c.width = plan.width; c.height = plan.height;
        const ctx = c.getContext('2d', { willReadFrequently: true });
        if (!ctx) throw new Error('no_canvas');
        ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(bmp, 0, 0, plan.width, plan.height);
        const src = ctx.getImageData(0, 0, plan.width, plan.height);
        const out = enhanceRgba(src.data, plan.width, plan.height, presetFor(kind));
        ctx.putImageData(new ImageData(out, plan.width, plan.height), 0, 0);
        bmp.close?.();
        if (alive) setSt('ok');
      } catch {
        if (alive && !ctrl.signal.aborted) setSt('err');
      }
    })();
    return () => { alive = false; ctrl.abort(); };
  }, [url, kind]);
  return (
    <>
      {st !== 'ok' && <img src={cropSrc(url)} alt={alt} loading="lazy" />}
      <canvas ref={ref} className="sw2-enh" role="img" aria-label={`${alt}, enhanced view`} data-enhanced="synthetic" hidden={st !== 'ok'} />
      {st === 'ok' && showChip && <SynthChip />}
      {st === 'loading' && <span className="sw2-synth"><Chip chip="INFO" word="Enhancing" /></span>}
      {st === 'err' && <span className="sw2-synth"><Chip chip="NOT SERVED" word="Enhanced not served" /></span>}
    </>
  );
}

/** The crop as the viewer shows it: the stored image, or its enhanced
 *  presentation when the toggle is on. */
export function FrameImage({ url, alt, kind, enhanced, showChip = true }: { url: string; alt: string; kind: string; enhanced: boolean; showChip?: boolean }) {
  return (
    <>
      {enhanced ? <EnhancedImage url={url} alt={alt} kind={kind} showChip={showChip} /> : <img src={cropSrc(url)} alt={alt} loading="lazy" />}
      <NoDataOverlay url={url} />
    </>
  );
}
