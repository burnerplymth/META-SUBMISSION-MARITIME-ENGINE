import { useEffect, useState } from 'react';
import { cropSrc } from './client';
import { analyseNoData, type NoDataState, type NoDataStats } from './noDataGate';

/* No-data measure. The stored crop is read once into a private canvas to
 * measure its pure-black or transparent share, which gates the change figure.
 * The hatch itself is drawn by NoData.tsx. Pixels that feed detection are
 * untouched. Cached per crop url. */

const MAX_SIDE = 1600;
const cache = new Map<string, Promise<NoDataStats>>();

function measure(url: string): Promise<NoDataStats> {
  const hit = cache.get(url);
  if (hit) return hit;
  const p = (async () => {
    const res = await fetch(cropSrc(url));
    if (!res.ok) throw new Error(`http_${res.status}`);
    const bmp = await createImageBitmap(await res.blob());
    const k = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
    const w = Math.max(1, Math.round(bmp.width * k)); const h = Math.max(1, Math.round(bmp.height * k));
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    if (!ctx) throw new Error('no_canvas');
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(bmp, 0, 0, w, h);
    bmp.close?.();
    return analyseNoData(ctx.getImageData(0, 0, w, h).data, w, h);
  })();
  cache.set(url, p);
  p.catch(() => cache.delete(url));
  return p;
}

/** The no-data measurement of one served crop. */
export function useNoData(url: string | null | undefined): NoDataState {
  const [st, setSt] = useState<NoDataState>({ s: 'loading' });
  useEffect(() => {
    if (!url) { setSt({ s: 'err' }); return; }
    let alive = true;
    setSt({ s: 'loading' });
    measure(url).then((stats) => { if (alive) setSt({ s: 'ok', stats }); }).catch(() => { if (alive) setSt({ s: 'err' }); });
    return () => { alive = false; };
  }, [url]);
  return st;
}
