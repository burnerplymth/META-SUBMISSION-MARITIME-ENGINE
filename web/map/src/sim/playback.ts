// Sim playback driver logic. Pure
// step arithmetic + a small hook. The tick is a pure index into the
// run's sampled states (state(tick) is a server-side pure function);
// the driver only chooses WHICH tick is in view. Playback cadence:
// one tick per `tickMs` (default 900 ms at 1x — readable motion for
// day-tick runs; the host's speed control drives
// `speed` here). The FLOWS step clock is separate and faster:
// the map advances it at the cascade's 300 ms/step language.

import { useEffect, useRef, useState } from "react";

export const FLOW_STEP_MS = 300;

export function clampTick(tick: number, ticks: number): number {
  if (ticks <= 0) return 0;
  return Math.max(0, Math.min(ticks - 1, Math.floor(tick)));
}

export function nextTick(tick: number, ticks: number): number {
  return clampTick(tick + 1, ticks);
}

export function prevTick(tick: number, ticks: number): number {
  return clampTick(tick - 1, ticks);
}

export function isAtEnd(tick: number, ticks: number): boolean {
  return ticks > 0 && tick >= ticks - 1;
}

export interface SimPlayback {
  tick: number;
  playing: boolean;
  atEnd: boolean;
  play: () => void;
  pause: () => void;
  toggle: () => void;
  stepForward: () => void;
  stepBack: () => void;
  seek: (tick: number) => void;
  reset: () => void;
}

/**
 * Uncontrolled playback state for `ticks` ticks. When `controlledTick`
 * is provided the hook reports through `onTickChange` instead of
 * holding the tick itself (the app store drives it that way — one code
 * path, same semantics as useCascade's controlled mode).
 */
export function useSimPlayback(
  ticks: number,
  opts: {
    tickMs?: number;
    controlledTick?: number;
    onTickChange?: (tick: number) => void;
    autoPlay?: boolean;
  } = {},
): SimPlayback {
  const tickMs = opts.tickMs ?? 900;
  const controlled = opts.controlledTick !== undefined;
  const [internalTick, setInternalTick] = useState(0);
  const tick = controlled ? (opts.controlledTick as number) : internalTick;
  const [playing, setPlaying] = useState(!!opts.autoPlay && ticks > 1);
  const timer = useRef<number | null>(null);

  const setTick = (t: number) => {
    const c = clampTick(t, ticks);
    if (!controlled) setInternalTick(c);
    opts.onTickChange?.(c);
  };

  useEffect(() => {
    if (!playing) return;
    if (isAtEnd(tick, ticks)) {
      setPlaying(false);
      return;
    }
    timer.current = window.setTimeout(() => setTick(tick + 1), tickMs);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, tick, ticks, tickMs]);

  return {
    tick,
    playing,
    atEnd: isAtEnd(tick, ticks),
    play: () => {
      if (isAtEnd(tick, ticks)) setTick(0);
      setPlaying(true);
    },
    pause: () => setPlaying(false),
    toggle: () => {
      if (playing) setPlaying(false);
      else {
        if (isAtEnd(tick, ticks)) setTick(0);
        setPlaying(true);
      }
    },
    stepForward: () => {
      setPlaying(false);
      setTick(tick + 1);
    },
    stepBack: () => {
      setPlaying(false);
      setTick(tick - 1);
    },
    seek: (t: number) => setTick(t),
    reset: () => {
      setPlaying(false);
      setTick(0);
    },
  };
}
