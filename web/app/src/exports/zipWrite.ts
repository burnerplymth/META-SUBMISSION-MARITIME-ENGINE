/* A minimal ZIP writer for the export pack: stored entries, sorted names,
 * fixed 1980-01-01 timestamps, so equal inputs give equal bytes. Pairs with
 * panels/reports/zip.ts (the reader) and packVerify.ts (the hash check). */

let table: Uint32Array | null = null;
function crcTable(): Uint32Array {
  if (table) return table;
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  table = t;
  return t;
}
export function crc32(bytes: Uint8Array): number {
  const t = crcTable();
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = t[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

const u16 = (n: number): number[] => [n & 0xff, (n >>> 8) & 0xff];
const u32 = (n: number): number[] => [n & 0xff, (n >>> 8) & 0xff, (n >>> 16) & 0xff, (n >>> 24) & 0xff];

export interface ZipInput { name: string; bytes: Uint8Array }

/** Safe member name: no leading slash, no `..`, forward slashes only. */
export function zipName(name: string): string {
  return name.replace(/\\/g, '/').split('/').filter((p) => p && p !== '.' && p !== '..').join('/');
}

export function writeZip(files: readonly ZipInput[]): Uint8Array {
  const enc = new TextEncoder();
  const sorted = [...files].map((f) => ({ name: zipName(f.name), bytes: f.bytes })).sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  const seen = new Set<string>();
  for (const f of sorted) { if (!f.name || seen.has(f.name)) throw new Error(`zip_bad_name:${f.name}`); seen.add(f.name); }
  const chunks: Uint8Array[] = []; const central: Uint8Array[] = [];
  let offset = 0;
  const DOS_TIME = 0; const DOS_DATE = (0 << 9) | (1 << 5) | 1; // 1980-01-01
  for (const f of sorted) {
    const name = enc.encode(f.name);
    const crc = crc32(f.bytes);
    const local = Uint8Array.from([
      ...u32(0x04034b50), ...u16(20), ...u16(0x0800), ...u16(0), ...u16(DOS_TIME), ...u16(DOS_DATE),
      ...u32(crc), ...u32(f.bytes.length), ...u32(f.bytes.length), ...u16(name.length), ...u16(0),
    ]);
    chunks.push(local, name, f.bytes);
    central.push(Uint8Array.from([
      ...u32(0x02014b50), ...u16(20), ...u16(20), ...u16(0x0800), ...u16(0), ...u16(DOS_TIME), ...u16(DOS_DATE),
      ...u32(crc), ...u32(f.bytes.length), ...u32(f.bytes.length), ...u16(name.length), ...u16(0), ...u16(0), ...u16(0), ...u16(0),
      ...u32(0o100644 << 16), ...u32(offset),
    ]), name);
    offset += local.length + name.length + f.bytes.length;
  }
  const cdSize = central.reduce((a, c) => a + c.length, 0);
  const eocd = Uint8Array.from([...u32(0x06054b50), ...u16(0), ...u16(0), ...u16(sorted.length), ...u16(sorted.length), ...u32(cdSize), ...u32(offset), ...u16(0)]);
  const all = [...chunks, ...central, eocd];
  const out = new Uint8Array(all.reduce((a, c) => a + c.length, 0));
  let p = 0;
  for (const c of all) { out.set(c, p); p += c.length; }
  return out;
}
