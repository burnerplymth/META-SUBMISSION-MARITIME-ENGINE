/* The bundled recorded fixtures: a Suez closure of 14 d and of 28 d (engine rev 2, n 200, seed 7, paired) and the served comparison
 * document. Loaded on demand; a result read from here is always shown as Recorded, never as a served run. */
import { parseCompare, type CompareDoc } from './compare';
import type { McFixture } from './types';

export const RECORDED_IDS = { d14: 'mc_5b3163e9338eaaa4', d28: 'mc_ae9b64f9e82d52f4' } as const;

export async function loadRecorded(which: 'd14' | 'd28' = 'd14'): Promise<McFixture> {
  const m = which === 'd14' ? await import('./fixtures/suez-14d-n200.json') : await import('./fixtures/suez-28d-n200.json');
  return (m.default ?? m) as unknown as McFixture;
}

export async function loadRecordedCompare(): Promise<{ a: McFixture; b: McFixture; doc: CompareDoc }> {
  const [a, b, c] = await Promise.all([loadRecorded('d14'), loadRecorded('d28'), import('./fixtures/suez-compare-14v28.json')]);
  const doc = parseCompare(((c.default ?? c) as unknown as { compare: unknown }).compare);
  if (!doc) throw new Error('recorded_compare_unreadable');
  return { a, b, doc };
}
