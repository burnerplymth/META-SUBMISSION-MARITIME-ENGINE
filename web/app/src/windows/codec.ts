/**
 * Share link codec: `#preset=<code>` is base64url of the
 * preset JSON, deflate-raw compressed through CompressionStream where the
 * browser has it (prefix `z`), plain base64url otherwise (prefix `p`). At
 * most 2,000 characters; a larger preset is shared as a file. A link carries
 * geometry and window ids, never data. Decoding output is untrusted: callers
 * run it through validatePreset.
 */
import { PRESET_SCHEMA, type Preset } from './presets';

export const MAX_CODE_CHARS = 2000;

const toB64Url = (bytes: Uint8Array): string => {
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
};
const fromB64Url = (t: string): Uint8Array => {
  const b = atob(t.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((t.length + 3) % 4));
  const out = new Uint8Array(b.length);
  for (let i = 0; i < b.length; i += 1) out[i] = b.charCodeAt(i);
  return out;
};

async function pipe(bytes: Uint8Array, stream: { writable: WritableStream<Uint8Array>; readable: ReadableStream<Uint8Array> }): Promise<Uint8Array> {
  const w = stream.writable.getWriter();
  w.write(bytes as unknown as Uint8Array).catch(() => undefined);
  w.close().catch(() => undefined);
  const chunks: Uint8Array[] = [];
  const r = stream.readable.getReader();
  for (;;) { const { done, value } = await r.read(); if (done) break; if (value) chunks.push(value); }
  const out = new Uint8Array(chunks.reduce((a, c) => a + c.length, 0));
  let o = 0;
  for (const c of chunks) { out.set(c, o); o += c.length; }
  return out;
}

/** The slim payload a link carries (no ids, no timestamps). */
export function sharePayload(p: Preset): string {
  return JSON.stringify({ schema: PRESET_SCHEMA, name: p.name, legacy: p.legacy, mode: p.mode, flags: p.flags, dock: p.dock, windows: p.windows });
}

export async function encodePreset(p: Preset): Promise<string | null> {
  const bytes = new TextEncoder().encode(sharePayload(p));
  let code: string;
  if (typeof CompressionStream === 'function') {
    try { code = `z${toB64Url(await pipe(bytes, new CompressionStream('deflate-raw') as never))}`; }
    catch { code = `p${toB64Url(bytes)}`; }
  } else code = `p${toB64Url(bytes)}`;
  return code.length <= MAX_CODE_CHARS ? code : null;
}

/** Decode to the raw (untrusted) JSON value, or null. */
export async function decodePreset(code: string): Promise<unknown | null> {
  try {
    if (!/^[zp][A-Za-z0-9_-]{1,2000}$/.test(code)) return null;
    let bytes = fromB64Url(code.slice(1));
    if (code[0] === 'z') {
      if (typeof DecompressionStream !== 'function') return null;
      bytes = await pipe(bytes, new DecompressionStream('deflate-raw') as never);
    }
    if (bytes.length > 64 * 1024) return null;
    return JSON.parse(new TextDecoder().decode(bytes)) as unknown;
  } catch { return null; }
}

/** The preset= grammar value check (the URL parser's guard). */
export const isPresetCode = (v: string): boolean => /^[zp][A-Za-z0-9_-]{1,2000}$/.test(v);
