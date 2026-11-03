// LIVE entities client: GET /live/entities (compact columnar arrays)
// and a polling hook. Hosts that want the layer pass the hook's result to
// <SupermapMap liveEntities={...}>. Status mapping (hide, never fake):
//   200 + valid columns  -> "live" | "snapshot" (as the body states)
//   404 / 501 / bad body -> "not_served"   (NOT SERVED chip, no entities)
//   network error / 5xx  -> "down"         (layer hidden)

import { useEffect, useState } from "react";
import { LIVE_STALE_AFTER_S, mergeLiveFeeds, normaliseLiveResponse, type LiveFeed } from "./live";

/** One GET /live/entities request. `kind` is REQUIRED by the API
 *  (vessel | aircraft); without it the server answers 422. */
export async function fetchLiveEntities(
  base: string,
  init?: { signal?: AbortSignal; path?: string; kind?: string; limit?: number },
): Promise<LiveFeed> {
  try {
    const path = init?.path ?? `/live/entities?kind=${init?.kind ?? "vessel"}&limit=${init?.limit ?? 5000}`;
    const res = await fetch(`${base}${path}`, { signal: init?.signal });
    if (res.status === 404 || res.status === 501) return { status: "not_served" };
    if (!res.ok) return { status: "down" };
    return normaliseLiveResponse(await res.json(), init?.kind);
  } catch {
    return { status: "down" };
  }
}

/** Vessels and aircraft are separate requests; merge them into one feed. */
export async function fetchLiveEntitiesAll(base: string, signal?: AbortSignal): Promise<LiveFeed> {
  const kinds = ["vessel", "aircraft"];
  return mergeLiveFeeds(await Promise.all(kinds.map((kind) => fetchLiveEntities(base, { signal, kind }))));
}

/** The feed to show after a poll. One failed poll (down) keeps the last good columns on screen, marked stale and
 *  never LIVE, so the layer does not flicker; a second failure in a row, or a held feed older than the freshness
 *  window, reads Down. A route that is not served is shown at once (nothing is held for it). Pure. */
export function holdLastGood(prev: LiveFeed | null, next: LiveFeed, failures: number, nowMs: number): LiveFeed {
  if (next.status !== "down") return next;
  if (failures > 1 || !prev || !prev.columns || (prev.status !== "live" && prev.status !== "snapshot")) return next;
  const t = prev.asOf ? Date.parse(prev.asOf) : NaN;
  if (Number.isFinite(t) && (nowMs - t) / 1000 > LIVE_STALE_AFTER_S) return next;
  return { ...prev, status: "snapshot", stale: true };
}

/** Poll GET /live/entities (vessel + aircraft) every `pollMs` (paused while the tab is hidden). */
export function useLiveEntities(base: string | null, pollMs = 5000): LiveFeed | null {
  const [feed, setFeed] = useState<LiveFeed | null>(null);
  useEffect(() => {
    if (base === null) { // null = layer off; '' is a valid same-origin base
      setFeed(null);
      return;
    }
    let stop = false;
    let timer = 0;
    let failures = 0;
    let good: LiveFeed | null = null; // the last feed that arrived with columns
    const ac = new AbortController();
    const tick = async () => {
      if (typeof document === "undefined" || !document.hidden) {
        const f = await fetchLiveEntitiesAll(base, ac.signal);
        if (!stop) {
          failures = f.status === "down" ? failures + 1 : 0;
          const shown = holdLastGood(good, f, failures, Date.now());
          if (!shown.stale && f.columns) good = f;
          setFeed(shown);
        }
      }
      if (!stop) timer = window.setTimeout(tick, pollMs);
    };
    void tick();
    return () => {
      stop = true;
      ac.abort();
      window.clearTimeout(timer);
    };
  }, [base, pollMs]);
  return feed;
}
