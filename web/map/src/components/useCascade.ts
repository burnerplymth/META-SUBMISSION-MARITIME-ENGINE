import { useEffect, useMemo, useRef, useState } from "react";
import type { CausalEvent } from "../types";
import { prefersReducedMotion } from "./motion";

/**
 * Cascade playback. Steps through events in EXACT seq order —
 * the animation order is the DAG order by construction: events are sorted
 * by `seq` once, and step N reveals precisely events with seq <= N and
 * chain edges with to_seq <= N. Stagger defaults to 300ms per step.
 * Supports controlled use (step + onStepChange) so
 * The chain explorer / director can drive the same playback (one code
 * path), and uncontrolled use with its own transport state.
 */
export interface CascadeState {
  /** events sorted by seq ascending — animation order ≡ DAG order */
  ordered: CausalEvent[];
  /** highest seq currently revealed; -1 = nothing revealed yet */
  step: number;
  playing: boolean;
  total: number;
  play: () => void;
  pause: () => void;
  toggle: () => void;
  reset: () => void;
  seek: (seq: number) => void;
  stepForward: () => void;
  stepBack: () => void;
  atEnd: boolean;
}

export interface CascadeOptions {
  staggerMs?: number;
  autoPlay?: boolean;
  /** controlled step (if provided, internal stepping reports via onStepChange) */
  step?: number;
  onStepChange?: (seq: number) => void;
}

export function useCascade(
  events: CausalEvent[] | undefined,
  opts: CascadeOptions = {},
): CascadeState {
  const staggerMs = opts.staggerMs ?? 300;
  const ordered = useMemo(
    () => [...(events ?? [])].sort((a, b) => a.seq - b.seq),
    [events],
  );
  const total = ordered.length;
  const maxStep = total - 1;

  const controlled = opts.step !== undefined;
  const [internalStep, setInternalStep] = useState(-1);
  const step = controlled ? (opts.step as number) : internalStep;
  const [playing, setPlaying] = useState(false);
  const timer = useRef<number | null>(null);

  const setStep = (s: number) => {
    const clamped = Math.max(-1, Math.min(maxStep, s));
    if (!controlled) setInternalStep(clamped);
    opts.onStepChange?.(clamped);
  };

  // New run loaded: reset (and autoplay if asked).
  const runKey = ordered.length ? `${ordered[0].run_id}:${total}` : "";
  const lastKey = useRef("");
  useEffect(() => {
    if (runKey !== lastKey.current) {
      lastKey.current = runKey;
      setStep(-1);
      setPlaying(!!opts.autoPlay && total > 0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runKey]);

  useEffect(() => {
    if (!playing) return;
    if (step >= maxStep) {
      setPlaying(false);
      return;
    }
    /* reduced motion — no stagger animation; the cascade
     * lands on its final step in one still frame and stops. */
    if (prefersReducedMotion()) {
      setStep(maxStep);
      setPlaying(false);
      return;
    }
    timer.current = window.setTimeout(() => setStep(step + 1), staggerMs);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, step, staggerMs, maxStep]);

  return {
    ordered,
    step,
    playing,
    total,
    play: () => {
      if (step >= maxStep) setStep(-1);
      setPlaying(true);
    },
    pause: () => setPlaying(false),
    toggle: () => {
      if (playing) {
        setPlaying(false);
      } else {
        if (step >= maxStep) setStep(-1);
        setPlaying(true);
      }
    },
    reset: () => {
      setPlaying(false);
      setStep(-1);
    },
    seek: (s: number) => setStep(s),
    stepForward: () => setStep(step + 1),
    stepBack: () => setStep(step - 1),
    atEnd: total > 0 && step >= maxStep,
  };
}
