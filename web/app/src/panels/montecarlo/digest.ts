/* The runs digest is the sha256 of the concatenated stream hashes in row order
 * (see the Monte Carlo API), so a client can check a loaded sample set against it. */
import type { McSample } from './types';

export async function sha256Hex(s: string): Promise<string | null> {
  try {
    const subtle = globalThis.crypto?.subtle;
    if (!subtle) return null;
    const buf = await subtle.digest('SHA-256', new TextEncoder().encode(s));
    return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, '0')).join('');
  } catch { return null; }
}

export type DigestCheck = 'match' | 'differs' | 'unchecked';

/** Does the digest of the loaded samples equal the result's runs digest? 'unchecked' when the check cannot run. */
export async function checkDigest(samples: readonly McSample[] | null, n: number, digest: string | null | undefined): Promise<DigestCheck> {
  if (!samples || !digest || samples.length !== n) return 'unchecked';
  const rows = [...samples].sort((a, b) => a.i - b.i);
  const d = await sha256Hex(rows.map((r) => r.stream_hash).join(''));
  if (d === null) return 'unchecked';
  return d === digest ? 'match' : 'differs';
}
