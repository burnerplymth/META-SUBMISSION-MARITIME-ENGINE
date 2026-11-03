/* Fetch layer of the watchlists window: read-only requests to routes that are
 * already served. A 404, or a reply that is not the expected JSON, is NOT
 * SERVED; a network error, a rate refusal or a 5xx is DOWN. Nothing here
 * throws into a panel and no server text reaches the screen. */
import { getJson, HttpError, isAbort } from '../ports/http';
import { postEvaluate } from '../shipments/client';
import type { Shipment } from '../shipments/types';
import { levelOf, type TransitGroup } from './logic';
import type { FeedWord, PlaceResult, TransitObs } from './types';

/** The cells whose feed state speaks for a place, in order of preference. */
const FEED_CELLS = ['activity', 'activity_ratio', 'hub_aircraft', 'border_wait', 'ais_samples'] as const;
/** The feed behind each state cell: its freshness window decides whether Live may be worn. */
const CELL_SOURCE: Record<(typeof FEED_CELLS)[number], string> = { activity: 'IMF PortWatch', activity_ratio: 'IMF PortWatch', hub_aircraft: 'OpenSky', border_wait: 'CBP', ais_samples: 'AISStream' };

/** Reads one place off the ports board (GET /ports/{id}). */
export function parsePlace(j: unknown): PlaceResult {
  if (!j || typeof j !== 'object') return { kind: 'notserved' };
  const o = j as { status?: { label?: unknown }; cells?: Record<string, { feed_state?: unknown; value?: unknown; as_of?: unknown } | undefined> };
  if (!o.status || typeof o.status.label !== 'string' || !o.cells || typeof o.cells !== 'object') return { kind: 'notserved' };
  let feed: FeedWord = 'notserved'; let asOf: string | null = null; let source: string | undefined;
  for (const k of FEED_CELLS) {
    const c = o.cells[k];
    if (!c || c.value == null) continue;
    const fs = String(c.feed_state ?? '').toUpperCase();
    if (fs === 'LIVE' || fs === 'SNAPSHOT') { feed = fs === 'LIVE' ? 'live' : 'snapshot'; asOf = typeof c.as_of === 'string' ? c.as_of : null; source = CELL_SOURCE[k]; break; }
  }
  return { kind: 'ok', obs: { feed, deviation: levelOf(o.status.label), asOf, ...(source ? { source } : {}) } };
}

export async function fetchPlace(id: string, signal?: AbortSignal): Promise<PlaceResult> {
  try {
    return parsePlace(await getJson<unknown>(`/ports/${encodeURIComponent(id)}`, { signal, timeoutMs: 20000 }));
  } catch (e) {
    if (e instanceof HttpError && (e.status === 404 || e.status === 405 || e.status === 501)) return { kind: 'notserved' };
    return { kind: 'down', code: isAbort(e) ? 'aborted' : e instanceof HttpError ? e.code : 'network' };
  }
}

export type TransitResult = { kind: 'ok'; byKey: Record<string, TransitObs>; label: string } | { kind: 'notserved' } | { kind: 'down'; code: string } | { kind: 'refused'; code: string };

/** Reads the legs and lanes of one group against its library scenario (POST /exposure/evaluate). */
export async function fetchTransit(g: TransitGroup, signal?: AbortSignal): Promise<TransitResult> {
  const ships: Shipment[] = g.items.map((i, n) => ({ id: `W${String(n + 1).padStart(3, '0')}`, origin: i.a, destination: i.b ?? i.a, mode: 'sea' }));
  const r = await postEvaluate({ shipments: ships, scenario: { scenario_id: g.scenarioId, duration_d: g.durationD } }, signal);
  if (r.kind !== 'ok') return r.kind === 'notserved' ? { kind: 'notserved' } : { kind: r.kind, code: r.code };
  const byKey: Record<string, TransitObs> = {};
  r.doc.shipments.forEach((s) => {
    const idx = ships.findIndex((x) => x.id === s.id);
    const item = g.items[idx];
    if (!item) return;
    const served = s.status === 'delayed' || s.status === 'rerouted' || s.status === 'unaffected';
    const med = served ? (s.status === 'unaffected' ? 0 : s.added_days?.median ?? null) : null;
    const obs: TransitObs = { median: med, low: served ? s.added_days?.low ?? (med === 0 ? 0 : null) : null, high: served ? s.added_days?.high ?? (med === 0 ? 0 : null) : null, label: r.doc.label };
    for (const k of g.keys) if (k.endsWith(`|${item.id}`)) byKey[k] = obs;
  });
  return { kind: 'ok', byKey, label: r.doc.label };
}
