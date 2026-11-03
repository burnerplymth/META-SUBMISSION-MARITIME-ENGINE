// PLACE CLUSTERING (pure, deterministic). At low zoom, places whose
// glowing points would overlap collapse into one count badge that
// expands as the user zooms in. A fixed screen-pixel grid over the
// Web-Mercator world at the (quantised) zoom: every place falls in
// exactly one cell, a cell with two or more places is a cluster, and
// pinned places (selection, chain members) are never absorbed. No
// randomness, no iteration-order dependence: input order does not
// change the result.

export interface ClusterItem {
  id: string;
  lon: number;
  lat: number;
  /** capacity (or any positive weight): the heaviest member leads */
  weight: number;
  /** never absorbed into a cluster */
  pinned?: boolean;
}

export interface PlaceCluster {
  /** `cluster:<leader id>` — stable while the leader stays the heaviest */
  id: string;
  leader: string;
  lon: number;
  lat: number;
  count: number;
  /** member ids, heaviest first */
  members: string[];
}

export interface ClusterResult {
  singles: string[];
  clusters: PlaceCluster[];
}

const TILE = 512;

/** Web-Mercator world pixel (origin top-left) at a zoom. */
export function worldPx(lon: number, lat: number, zoom: number): [number, number] {
  const scale = TILE * Math.pow(2, zoom);
  const x = ((lon + 180) / 360) * scale;
  const s = Math.sin((Math.max(-85.0511, Math.min(85.0511, lat)) * Math.PI) / 180);
  const y = (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * scale;
  return [x, y];
}

/** Inverse of worldPx. */
export function worldLonLat(x: number, y: number, zoom: number): [number, number] {
  const scale = TILE * Math.pow(2, zoom);
  const lon = (x / scale) * 360 - 180;
  const n = Math.PI - (2 * Math.PI * y) / scale;
  const lat = (180 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
  return [lon, lat];
}

const byWeightThenId = (a: ClusterItem, b: ClusterItem): number =>
  b.weight - a.weight || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

export function clusterPlaces(
  items: ReadonlyArray<ClusterItem>,
  zoom: number,
  cellPx: number,
): ClusterResult {
  const sorted = [...items].sort(byWeightThenId);
  const cells = new Map<string, ClusterItem[]>();
  const singles: string[] = [];
  for (const it of sorted) {
    if (it.pinned) {
      singles.push(it.id);
      continue;
    }
    const [x, y] = worldPx(it.lon, it.lat, zoom);
    const key = `${Math.floor(x / cellPx)}:${Math.floor(y / cellPx)}`;
    const arr = cells.get(key);
    if (arr) arr.push(it);
    else cells.set(key, [it]);
  }
  const clusters: PlaceCluster[] = [];
  for (const arr of cells.values()) {
    if (arr.length === 1) {
      singles.push(arr[0].id);
      continue;
    }
    /* weighted centroid in world pixels (log weight keeps one giant
     * from pinning the badge on top of itself) */
    let sx = 0;
    let sy = 0;
    let sw = 0;
    for (const m of arr) {
      const [x, y] = worldPx(m.lon, m.lat, zoom);
      const w = Math.log10(Math.max(1, m.weight)) + 1;
      sx += x * w;
      sy += y * w;
      sw += w;
    }
    const [lon, lat] = worldLonLat(sx / sw, sy / sw, zoom);
    const leader = arr[0].id; // heaviest (arr is in sorted order)
    clusters.push({
      id: `cluster:${leader}`,
      leader,
      lon,
      lat,
      count: arr.length,
      members: arr.map((m) => m.id),
    });
  }
  singles.sort();
  clusters.sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  return { singles, clusters };
}

/** The zoom a cluster click flies to: the first step (0.5) at or above
 *  `fromZoom + 1` where the members no longer share any cluster cell, so
 *  the badge dissolves into its places; never more than `maxStep` levels
 *  in one click. Pure and deterministic. */
export function zoomToExpand(
  members: ReadonlyArray<ClusterItem>,
  fromZoom: number,
  cellPx: number,
  maxStep = 4,
): number {
  const lo = fromZoom + 1;
  const hi = fromZoom + maxStep;
  for (let z = lo; z <= hi + 1e-9; z += 0.5) {
    if (clusterPlaces(members, z, cellPx).clusters.length === 0) return z;
  }
  return hi;
}
