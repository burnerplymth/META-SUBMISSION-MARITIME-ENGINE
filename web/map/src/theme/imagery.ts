/**
 * SATELLITE imagery sources. Pure: URL builders,
 * date handling and chip logic. No network here; the one probe is injected.
 *
 * Imagery is CONTEXT under the map, never the product's own detections.
 *
 *  STATIC  EOX Sentinel-2 cloudless, the 2016 layer (`s2cloudless_3857`),
 *          licence CC BY 4.0 (attribution only, commercial use allowed).
 *          Per EOX's WMTS GetCapabilities, the
 *          layer abstract says "for 2016 ... Creative Commons Attribution 4.0".
 *          EOX's 2018 to 2025 layers are CC BY-NC-SA 4.0 (non-commercial,
 *          share-alike) and MUST NOT be used: no `s2cloudless-20NN`
 *          layer id with NN >= 18 may be requested (2017 is CC BY 4.0).
 *          Tiles are 256 px Web Mercator (matrix set `g`, GoogleMapsCompatible);
 *          10 m source, native detail to about z14 (higher zooms over-zoom).
 *
 *  DAILY   NASA GIBS true-colour VIIRS (NOAA-21), requested for an explicit
 *          acquisition day (the TIME dimension, as a path segment in the
 *          REST pattern). NASA's Earthdata data policy: full and open
 *          sharing, no exclusive access, non-discriminatory. 250 m, native
 *          detail to z9 (GoogleMapsCompatible_Level9; z10 answers HTTP 400).
 *          NOAA-21 rather than Suomi NPP: NASA is ending
 *          delivery of new Suomi NPP data.
 *          The chip shows the ACQUISITION DATE ("Imagery YYYY-MM-DD"); a
 *          missing or stale day shows NOT SERVED. The word LIVE is never used.
 */

export type SatSource = "static" | "daily";

/* ---------------------------------------------------------------- EOX 2016 */
export const EOX_LAYER_ID = "s2cloudless_3857";
export const EOX_TILES =
  "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg";
export const EOX_MAXZOOM = 14;
/** The exact attribution text EOX requires for the CC BY 4.0 (2016) layer. */
export const EOX_ATTRIBUTION =
  "EOxCloudless https://cloudless.eox.at by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2016 & 2017)";
/** Chip text for the static layer (a layer year, not an acquisition day). */
export const STATIC_CHIP_TEXT = "Imagery 2016";

/* ---------------------------------------------------------------- NASA GIBS */
export const GIBS_HOST = "https://gibs.earthdata.nasa.gov";
export const GIBS_LAYER_ID = "VIIRS_NOAA21_CorrectedReflectance_TrueColor";
export const GIBS_MATRIX_SET = "GoogleMapsCompatible_Level9";
export const GIBS_MAXZOOM = 9;
export const GIBS_ATTRIBUTION = "NASA GIBS / Earthdata";
/** A day older than this many days (UTC) is stale: NOT SERVED. */
export const GIBS_MAX_AGE_DAYS = 2;

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const DAY_MS = 86_400_000;

export function isDay(s: unknown): s is string {
  if (typeof s !== "string" || !DAY_RE.test(s)) return false;
  const t = Date.parse(`${s}T00:00:00Z`);
  return Number.isFinite(t) && new Date(t).toISOString().slice(0, 10) === s;
}

/** UTC calendar day of an instant, YYYY-MM-DD. */
export function utcDay(now: Date): string {
  return now.toISOString().slice(0, 10);
}

export function addDays(day: string, n: number): string {
  if (!isDay(day)) throw new Error(`not a day: ${String(day)}`);
  return new Date(Date.parse(`${day}T00:00:00Z`) + n * DAY_MS).toISOString().slice(0, 10);
}

/** Whole UTC days from `day` to the day of `now` (positive = day is in the past). */
export function ageDays(day: string, now: Date): number {
  return Math.round((Date.parse(`${utcDay(now)}T00:00:00Z`) - Date.parse(`${day}T00:00:00Z`)) / DAY_MS);
}

/** The default requested day: today (UTC) minus one day (today's pass is often not yet processed). */
export function defaultDailyDate(now: Date): string {
  return addDays(utcDay(now), -1);
}

/** MapLibre tile template for one acquisition day. Throws on a malformed day (never builds a URL from junk). */
export function gibsTileUrl(day: string): string {
  if (!isDay(day)) throw new Error(`not a day: ${String(day)}`);
  return `${GIBS_HOST}/wmts/epsg3857/best/${GIBS_LAYER_ID}/default/${day}/${GIBS_MATRIX_SET}/{z}/{y}/{x}.jpeg`;
}

/** One real low-zoom tile (z3, x4, y2: Atlantic and Europe) used to ask whether a day exists. */
export function gibsProbeUrl(day: string): string {
  return gibsTileUrl(day).replace("{z}", "3").replace("{y}", "2").replace("{x}", "4");
}

export interface DailyResolution {
  /** the day whose tiles answered, or null when none did (NOT SERVED) */
  date: string | null;
  /** the days asked, in order */
  tried: string[];
}

/**
 * Today (UTC) minus one day; if that day has no tiles, the day before it.
 * `probe(url)` resolves true when the tile answered 2xx. Never throws: a probe
 * that rejects counts as "not served" for that day.
 */
export async function resolveDailyDate(
  now: Date,
  probe: (url: string) => Promise<boolean>,
): Promise<DailyResolution> {
  const first = defaultDailyDate(now);
  const days = [first, addDays(first, -1)];
  const tried: string[] = [];
  for (const d of days) {
    tried.push(d);
    let ok = false;
    try {
      ok = await probe(gibsProbeUrl(d));
    } catch {
      ok = false;
    }
    if (ok) return { date: d, tried };
  }
  return { date: null, tried };
}

/** Browser probe: a real tile GET. Used only at runtime. */
export const PROBE_TIMEOUT_MS = 8000;
export async function fetchProbe(url: string, timeoutMs: number = PROBE_TIMEOUT_MS): Promise<boolean> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { method: "GET", cache: "no-store", signal: ctl.signal });
    return r.ok;
  } finally {
    clearTimeout(timer);
  }
}

export interface ImageryChip {
  /** the chip text; state labels only */
  text: string;
  state: "ok" | "not-served" | "pending";
}

/**
 * The imagery state chip. Static: the layer year. Daily: the acquisition day,
 * or NOT SERVED when no day answered, the day is malformed, in the future, or
 * older than GIBS_MAX_AGE_DAYS. `date` undefined = still resolving.
 */
export function imageryChip(args: {
  source: SatSource;
  date: string | null | undefined;
  now: Date;
}): ImageryChip {
  if (args.source === "static") return { text: STATIC_CHIP_TEXT, state: "ok" };
  if (args.date === undefined) return { text: "Imagery pending", state: "pending" };
  if (args.date === null || !isDay(args.date)) return { text: "Imagery NOT SERVED", state: "not-served" };
  const age = ageDays(args.date, args.now);
  if (age < 0 || age > GIBS_MAX_AGE_DAYS) return { text: "Imagery NOT SERVED", state: "not-served" };
  return { text: `Imagery ${args.date}`, state: "ok" };
}

/** The one attribution line per source (rendered only in the map's attribution control). */
export function imageryAttribution(source: SatSource): string {
  return source === "daily" ? GIBS_ATTRIBUTION : EOX_ATTRIBUTION;
}
