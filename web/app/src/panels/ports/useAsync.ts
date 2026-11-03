import { useEffect, useRef, useState } from 'react';
import { HttpError, isAbort } from './http';

export interface AsyncState<T> { data: T | null; error: HttpError | null; loading: boolean }

/** Run `load` whenever `key` changes (and on `tick` bumps). The previous
 *  data stays on screen while the next request is in flight; a stale
 *  answer (superseded request) is dropped; an abort is not an error.
 *  `enabled=false` clears the state and issues nothing. */
export function useAsync<T>(load: (signal: AbortSignal) => Promise<T>, key: string, enabled = true, tick = 0): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({ data: null, error: null, loading: enabled });
  const loadRef = useRef(load);
  loadRef.current = load;
  useEffect(() => {
    if (!enabled) { setState({ data: null, error: null, loading: false }); return undefined; }
    const ctrl = new AbortController();
    setState((p) => ({ ...p, loading: true }));
    loadRef.current(ctrl.signal).then(
      (data) => { if (!ctrl.signal.aborted) setState({ data, error: null, loading: false }); },
      (e: unknown) => {
        if (ctrl.signal.aborted || isAbort(e)) return;
        setState((p) => ({ data: p.data, error: e instanceof HttpError ? e : new HttpError(0, 'error', String(e)), loading: false }));
      },
    );
    return () => ctrl.abort();
  }, [key, enabled, tick]);
  return state;
}

/** A counter that bumps every `ms` while the page is visible. */
export function useVisibleTick(ms: number): [number, () => void] {
  const [n, setN] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => { if (!document.hidden) setN((x) => x + 1); }, ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return [n, () => setN((x) => x + 1)];
}
