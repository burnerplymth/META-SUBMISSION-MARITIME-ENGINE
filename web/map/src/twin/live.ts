// TWIN / LIVE entities (pure). The live feed serves observed
// positions as COMPACT COLUMNAR arrays at GET /live/entities:
//   { ids: string[], lon: number[], lat: number[], heading: number[],
//     speed: number[], kind: (string|number)[], age_s: number[] }
// This module validates + normalises that payload and owns the chip
// state for the LIVE layer. LIVE entities are OBSERVATIONS, a layer of
// their own: they are never merged with the SIMULATED fleet, never
// relabelled, and the layer is hidden when its feed is down.

export type LiveFeedStatus = "live" | "snapshot" | "not_served" | "down";

export interface LiveEntitiesColumns {
  ids: ArrayLike<string>;
  lon: ArrayLike<number>;
  lat: ArrayLike<number>;
  heading: ArrayLike<number>;
  /** knots for vessels / aircraft ground speed as served; used for
   *  the speed colour ramp only (never to extrapolate positions). */
  speed: ArrayLike<number>;
  kind: ArrayLike<string | number>;
  age_s: ArrayLike<number>;
}

/** What the host passes to the map's `liveEntities` prop. */
export interface LiveFeed {
  status: LiveFeedStatus;
  columns?: LiveEntitiesColumns | null;
  /** ISO time the snapshot was observed / served (chip freshness). */
  asOf?: string | null;
  /** True when this is the last good feed held across a failed poll: shown as SNAPSHOT, never LIVE. */
  stale?: boolean;
}

export type LiveKind = 0 | 1; // 0 = vessel (sea), 1 = aircraft (air)

const AIR_RE = /air|plane|flight|jet|heli|opensky/i;

/** Map a served `kind` value to the renderer's two glyph families. */
export function liveKindCode(k: string | number | undefined): LiveKind {
  if (typeof k === "number") return k === 1 ? 1 : 0;
  return typeof k === "string" && AIR_RE.test(k) ? 1 : 0;
}

/** Columns are usable when every array has the id array's length. */
export function validLiveColumns(c: LiveEntitiesColumns | null | undefined): c is LiveEntitiesColumns {
  if (!c || !c.ids) return false;
  const n = c.ids.length;
  return (
    c.lon?.length === n &&
    c.lat?.length === n &&
    (c.heading?.length ?? n) === n &&
    (c.speed?.length ?? n) === n &&
    (c.kind?.length ?? n) === n &&
    (c.age_s?.length ?? n) === n
  );
}

/**
 * Normalise a raw GET /live/entities JSON body into a LiveFeed. Accepts
 * the columns either at the top level or under `columns`, and reads the
 * served mode (`mode`/`state`: LIVE | SNAPSHOT) when present. A body
 * with no columns, or malformed columns, is NOT a live feed: it maps to
 * `not_served` so the layer renders its honest empty state, never data.
 */
/** Served state word to feed status. Only an explicit LIVE reads live
 *  (case-insensitive); DOWN is down; every other word (SNAPSHOT, STALE,
 *  REPLAY, empty) reads snapshot, so a recorded state is never shown as live. */
function statusFromWord(word: unknown): LiveFeedStatus {
  const w = String(word ?? "").trim().toUpperCase();
  if (w === "LIVE") return "live";
  if (w === "DOWN") return "down";
  return "snapshot";
}

export function normaliseLiveResponse(body: unknown, kindHint?: string): LiveFeed {
  if (!body || typeof body !== "object") return { status: "not_served" };
  const b = body as Record<string, unknown>;
  /* The real GET /live/entities shape (server/live): parallel columns
   * {id, lat, lon, spd_kn, hdg, age_s, ...} under `columns`, `kind` as a
   * REQUIRED query param echoed in the body, `status` "LIVE"|"SNAPSHOT",
   * `served`, and `feed.retrieved_at`. Adapt it to the renderer's
   * columns; `hdg: null` (bearing unknown) MUST become NaN, never 0 —
   * the renderer reads Number(), and Number(null) is 0 (due north). */
  const sc = b.columns as Record<string, unknown> | undefined;
  if (sc && typeof sc === "object" && Array.isArray(sc.id)) {
    if (b.served === false) return { status: "not_served" };
    const ids = sc.id as string[];
    const kind = String(b.kind ?? kindHint ?? "vessel");
    const num = (a: unknown, i: number): number => {
      const v = Array.isArray(a) ? (a as unknown[])[i] : undefined;
      return typeof v === "number" ? v : Number.NaN;
    };
    const cols: LiveEntitiesColumns = {
      ids,
      lon: (sc.lon as number[]) ?? [],
      lat: (sc.lat as number[]) ?? [],
      heading: ids.map((_, i) => num(sc.hdg, i)),
      speed: ids.map((_, i) => num(sc.spd_kn, i)),
      kind: ids.map(() => kind),
      age_s: ids.map((_, i) => num(sc.age_s, i)),
    };
    if (!validLiveColumns(cols)) return { status: "not_served" };
    const feed = (b.feed ?? {}) as Record<string, unknown>;
    const asOf = (feed.retrieved_at ?? b.generated_at ?? null) as string | null;
    const status = statusFromWord(b.status ?? "LIVE");
    if (status === "down") return { status: "down" };
    return {
      status,
      columns: cols,
      asOf: typeof asOf === "string" ? asOf : null,
    };
  }
  const cols = (sc && typeof sc === "object" ? sc : b) as unknown as LiveEntitiesColumns;
  if (!validLiveColumns(cols)) return { status: "not_served" };
  const flatStatus = statusFromWord(b.mode ?? b.state ?? "LIVE");
  if (flatStatus === "down") return { status: "down" };
  const asOf = (b.as_of ?? b.served_at ?? b.retrieved_at ?? null) as string | null;
  return {
    status: flatStatus,
    columns: cols,
    asOf: typeof asOf === "string" ? asOf : null,
  };
}

/** Merge per-kind feeds (the API serves one kind per request) into one
 *  LiveFeed. Columns concatenate; the merged state is the WEAKEST state of
 *  the feeds that contributed columns (any snapshot half makes the whole
 *  layer SNAPSHOT, so recorded entities are never labelled live). With no
 *  contributing feed: not_served beats down. Nothing is invented. */
export function mergeLiveFeeds(feeds: LiveFeed[]): LiveFeed {
  const withCols = feeds.filter((f) => (f.status === "live" || f.status === "snapshot") && f.columns);
  if (!withCols.length) {
    return feeds.some((f) => f.status === "not_served") ? { status: "not_served" } : { status: "down" };
  }
  const cat = <T,>(pick: (c: LiveEntitiesColumns) => ArrayLike<T>): T[] =>
    withCols.flatMap((f) => Array.from(pick(f.columns as LiveEntitiesColumns)));
  const status: LiveFeedStatus = withCols.some((f) => f.status !== "live") ? "snapshot" : "live";
  return {
    status,
    columns: {
      ids: cat((c) => c.ids), lon: cat((c) => c.lon), lat: cat((c) => c.lat),
      heading: cat((c) => c.heading), speed: cat((c) => c.speed),
      kind: cat((c) => c.kind), age_s: cat((c) => c.age_s),
    },
    asOf: withCols.map((f) => f.asOf ?? "").sort().pop() || null,
  };
}

export interface LiveChip {
  /** visible text, chips and state labels only */
  label: "LIVE" | "SNAPSHOT" | "NOT SERVED" | "DOWN";
  count: number | null;
  /** median observation age in seconds, null when unknown */
  medianAgeS: number | null;
  /** the layer draws entities */
  visible: boolean;
}

/** Median observation age beyond which a "live" feed no longer earns the LIVE label. */
export const LIVE_STALE_AFTER_S = 900;

/** Chip state for the LIVE layer. A down feed HIDES the layer; a
 *  not-served feed shows the NOT SERVED chip and no entities. */
export function liveChip(feed: LiveFeed | null | undefined): LiveChip | null {
  if (!feed) return null;
  switch (feed.status) {
    case "not_served":
      return { label: "NOT SERVED", count: null, medianAgeS: null, visible: false };
    case "down":
      return { label: "DOWN", count: null, medianAgeS: null, visible: false };
    default: {
      const ok = validLiveColumns(feed.columns);
      const n = ok ? (feed.columns as LiveEntitiesColumns).ids.length : 0;
      const medianAgeS = ok ? medianOf((feed.columns as LiveEntitiesColumns).age_s) : null;
      // LIVE only when the feed is live now AND its observations are fresh;
      // a served-live feed whose median observation is stale reads SNAPSHOT.
      const stale = medianAgeS !== null && medianAgeS > LIVE_STALE_AFTER_S;
      return {
        label: feed.status === "snapshot" || stale ? "SNAPSHOT" : "LIVE",
        count: n,
        medianAgeS,
        visible: n > 0,
      };
    }
  }
}

/** The fleet-key word for the live layer. The swatch reads Live only when the layer's own chip does
 *  (feed live now and fresh observations); a snapshot feed reads Snapshot, an absent one reads nothing. */
export function liveKeyLabel(feed: LiveFeed | null | undefined): { word: string; live: boolean } | null {
  const chip = liveChip(feed);
  if (!chip) return null;
  if (chip.label === "NOT SERVED") return { word: "Not served", live: false };
  if (chip.label === "DOWN") return { word: "Down", live: false };
  if (!chip.visible) return null;
  return chip.label === "LIVE" ? { word: "Live", live: true } : { word: "Snapshot", live: false };
}

function medianOf(a: ArrayLike<number> | undefined): number | null {
  if (!a || !a.length) return null;
  const c = Array.from(a as ArrayLike<number>).filter((v) => Number.isFinite(v)).sort((x, y) => x - y);
  return c.length ? c[Math.floor(c.length / 2)] : null;
}
