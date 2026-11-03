/* VERIFY a built data pack: re-hash every file inside the zip and
 * compare with the sha256 the manifest states. Runs in the browser from
 * the bytes already in memory; nothing is sent anywhere. */
import { listZipEntries, readZipEntry } from './zip';
import type { PackManifest } from './model';

export type VerifyState = 'MATCH' | 'MISMATCH' | 'MISSING';
export interface VerifyRow { file: string; state: VerifyState; expected: string; actual: string | null }

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new Error('no_subtle_crypto');
  const d = new Uint8Array(await subtle.digest('SHA-256', bytes as BufferSource));
  return Array.from(d, (b) => b.toString(16).padStart(2, '0')).join('');
}

/** Files the manifest hashes: its `files` map plus each dataset entry. */
export function expectedHashes(m: PackManifest): Array<{ file: string; sha256: string }> {
  const out = new Map<string, string>();
  for (const [name, f] of Object.entries(m.files ?? {})) if (f?.sha256) out.set(name, f.sha256);
  for (const d of m.datasets ?? []) if (d.file && d.sha256) out.set(d.file, d.sha256);
  return [...out.entries()].sort(([a], [b]) => (a < b ? -1 : 1)).map(([file, sha256]) => ({ file, sha256 }));
}

export async function verifyPack(zip: Uint8Array, m: PackManifest): Promise<VerifyRow[]> {
  const names = new Set(listZipEntries(zip).map((e) => e.name));
  const rows: VerifyRow[] = [];
  for (const e of expectedHashes(m)) {
    if (!names.has(e.file)) { rows.push({ file: e.file, state: 'MISSING', expected: e.sha256, actual: null }); continue; }
    const raw = await readZipEntry(zip, e.file);
    const actual = raw ? await sha256Hex(raw) : null;
    rows.push({ file: e.file, state: actual === e.sha256 ? 'MATCH' : actual == null ? 'MISSING' : 'MISMATCH', expected: e.sha256, actual });
  }
  return rows;
}
export const allMatch = (rows: readonly VerifyRow[]): boolean => rows.length > 0 && rows.every((r) => r.state === 'MATCH');
