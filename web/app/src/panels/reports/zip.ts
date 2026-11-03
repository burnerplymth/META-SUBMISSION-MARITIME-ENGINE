/* A minimal read-only zip reader, enough to pull manifest.json
 * out of the DATA PACK the API returns (POST /exports/pack answers an
 * application/zip with stored or deflated entries, 1980-01-01 stamps,
 * no zip64, no encryption). Pure bytes in, bytes out.
 *
 * Deflated entries inflate through DecompressionStream('deflate-raw'),
 * present in every current browser and in Node 18+. */

export interface ZipEntry { name: string; method: number; compressedSize: number; size: number; offset: number }

const u16 = (b: Uint8Array, o: number): number => b[o] | (b[o + 1] << 8);
const u32 = (b: Uint8Array, o: number): number => (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0;

/** Locate the end-of-central-directory record and list the entries.
 *  Throws a short code string when the bytes are not a readable zip. */
export function listZipEntries(bytes: Uint8Array): ZipEntry[] {
  const minEocd = 22;
  if (bytes.length < minEocd) throw new Error('zip_too_short');
  let eocd = -1;
  for (let i = bytes.length - minEocd; i >= Math.max(0, bytes.length - minEocd - 0xffff); i--) {
    if (u32(bytes, i) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error('zip_no_eocd');
  const count = u16(bytes, eocd + 10);
  let p = u32(bytes, eocd + 16);
  const entries: ZipEntry[] = [];
  const dec = new TextDecoder();
  for (let n = 0; n < count; n++) {
    if (p + 46 > bytes.length || u32(bytes, p) !== 0x02014b50) throw new Error('zip_bad_central_directory');
    const flags = u16(bytes, p + 8);
    if (flags & 1) throw new Error('zip_encrypted');
    const nameLen = u16(bytes, p + 28); const extraLen = u16(bytes, p + 30); const commentLen = u16(bytes, p + 32);
    entries.push({
      name: dec.decode(bytes.subarray(p + 46, p + 46 + nameLen)),
      method: u16(bytes, p + 10), compressedSize: u32(bytes, p + 20), size: u32(bytes, p + 24), offset: u32(bytes, p + 42),
    });
    p += 46 + nameLen + extraLen + commentLen;
  }
  return entries;
}

async function inflateRaw(data: Uint8Array): Promise<Uint8Array> {
  const DS = (globalThis as { DecompressionStream?: new (f: string) => TransformStream<Uint8Array, Uint8Array> }).DecompressionStream;
  if (!DS) throw new Error('zip_no_inflate');
  const stream = new Blob([data as BlobPart]).stream().pipeThrough(new DS('deflate-raw'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

/** Bytes of one named entry (stored or deflated). */
export async function readZipEntry(bytes: Uint8Array, name: string): Promise<Uint8Array | null> {
  const e = listZipEntries(bytes).find((x) => x.name === name);
  if (!e) return null;
  if (u32(bytes, e.offset) !== 0x04034b50) throw new Error('zip_bad_local_header');
  const start = e.offset + 30 + u16(bytes, e.offset + 26) + u16(bytes, e.offset + 28);
  const raw = bytes.subarray(start, start + e.compressedSize);
  if (e.method === 0) return raw.slice();
  if (e.method === 8) return inflateRaw(raw);
  throw new Error(`zip_method_${e.method}`);
}

/** Parse one JSON entry; null when the entry is absent. */
export async function readZipJson<T = unknown>(bytes: Uint8Array, name: string): Promise<T | null> {
  const raw = await readZipEntry(bytes, name);
  if (!raw) return null;
  return JSON.parse(new TextDecoder().decode(raw)) as T;
}
