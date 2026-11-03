/**
 * SEARCH SLICE RECOVERY. The search slices (all five: the run
 * catalogue, the IMPACT goods taxonomy, the saved explorations, the
 * watchlists, the stored replays) are boot-fetched, and a fetch that
 * fails — or, for the ~14 s catalogue build, merely outruns the
 * client's 15 s request timeout — would leave the slice null for the
 * WHOLE page life, so whole categories would silently vanish from the
 * search catalogue (no GOODS facet, exact class names answering
 * "No results"; stored runs unreachable by id fragment). Each load
 * therefore gets a second chance.
 *
 * The recovery contract: each slice load runs as a bounded CYCLE —
 * up to `maxAttempts` attempts spaced by the backoff
 * (pollBackoffMs) — and `ensure()` starts a cycle only when the slice
 * is neither loaded nor already loading. A cycle that exhausts its
 * attempts settles the slice FAILED: it stays null (the index spans
 * less; nothing is invented — the honest degraded state), and a later
 * ensure() — the store calls it at boot and again whenever search
 * opens with the slice absent — starts one fresh bounded cycle. No
 * path retries unboundedly and no path polls: between cycles the app
 * is silent.
 */
import { pollBackoffMs } from '../api';

export interface SliceRetryOptions {
  /** Total attempts per cycle, the first included. Default 4. */
  maxAttempts?: number;
  /** Delay before the next attempt, given the count of consecutive
   *  failures so far. Default: the poll backoff. */
  backoff?: (failures: number) => number;
  /** Injectable wait (defaults to a timer). */
  sleep?: (ms: number) => Promise<void>;
}

export const SLICE_MAX_ATTEMPTS = 4;

const realSleep = (ms: number): Promise<void> =>
  new Promise<void>((resolve) => { setTimeout(resolve, ms); });

/** One bounded load cycle: attempt, back off, retry; the last error
 *  is thrown once `maxAttempts` attempts have failed. */
export async function loadSliceWithRetry<T>(
  load: () => Promise<T>,
  opts: SliceRetryOptions = {},
): Promise<T> {
  const maxAttempts = Math.max(1, opts.maxAttempts ?? SLICE_MAX_ATTEMPTS);
  const backoff = opts.backoff ?? ((failures: number) => pollBackoffMs(failures));
  const sleep = opts.sleep ?? realSleep;
  let failures = 0;
  for (;;) {
    try {
      return await load();
    } catch (err) {
      failures += 1;
      if (failures >= maxAttempts) throw err;
      await sleep(backoff(failures));
    }
  }
}

export type SliceStatus = 'idle' | 'loading' | 'ready' | 'failed';

export interface SliceRecovery<T> {
  /** Start a bounded load cycle unless the slice is loaded or a
   *  cycle is already running. Fire-and-forget by design: outcomes
   *  land via onLoad / the status, never as a rejection here. */
  ensure: () => void;
  status: () => SliceStatus;
}

/**
 * The per-slice recovery machine the store wires to a state setter.
 * `onLoad` fires exactly once per successful cycle, with the served
 * value verbatim. After a failed cycle the machine rests at FAILED
 * until the next ensure() — which is what makes "re-request when
 * search opens with the slice absent" a bounded behaviour rather
 * than a retry storm.
 */
export function createSliceRecovery<T>(
  load: () => Promise<T>,
  onLoad: (value: T) => void,
  opts: SliceRetryOptions = {},
): SliceRecovery<T> {
  let status: SliceStatus = 'idle';
  return {
    status: () => status,
    ensure: () => {
      if (status === 'loading' || status === 'ready') return;
      status = 'loading';
      loadSliceWithRetry(load, opts).then(
        (value) => { status = 'ready'; onLoad(value); },
        () => { status = 'failed'; },
      );
    },
  };
}
