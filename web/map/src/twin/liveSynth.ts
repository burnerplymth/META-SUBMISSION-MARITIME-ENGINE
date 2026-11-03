// A synthetic stand-in for GET /live/entities so the LIVE layer can be
// exercised at several-thousand scale without the live feed. It is not
// imported by the map component, and the twin bar labels it
// "SYNTHETIC LIVE STAND-IN". Points are scattered along real lane paths.
import type { SimLane } from "../sim/types";
import type { LiveEntitiesColumns } from "./live";

function rng(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function syntheticLiveColumns(lanes: SimLane[], n: number, tSec: number): LiveEntitiesColumns {
  const rand = rng(1234);
  const movers = lanes.filter((l) => l.mode !== "ground" && l.path.length >= 2);
  const ids: string[] = [];
  const lon = new Float32Array(n);
  const lat = new Float32Array(n);
  const heading = new Float32Array(n);
  const speed = new Float32Array(n);
  const kind: string[] = [];
  const age = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const lane = movers[Math.floor(rand() * movers.length)];
    const seg = Math.floor(rand() * (lane.path.length - 1));
    const u = (rand() + (tSec * (lane.mode === "air" ? 0.012 : 0.002)) * (0.5 + rand())) % 1;
    const [la1, lo1] = lane.path[seg];
    const [la2, lo2] = lane.path[seg + 1];
    let dLo = lo2 - lo1;
    if (dLo > 180) dLo -= 360;
    if (dLo < -180) dLo += 360;
    let lo = lo1 + dLo * u;
    if (lo > 180) lo -= 360;
    if (lo < -180) lo += 360;
    lon[i] = lo;
    lat[i] = la1 + (la2 - la1) * u;
    heading[i] = ((Math.atan2(dLo * Math.cos((la1 * Math.PI) / 180), la2 - la1) * 180) / Math.PI + 360) % 360;
    speed[i] = lane.mode === "air" ? 380 + rand() * 120 : 8 + rand() * 14;
    kind.push(lane.mode === "air" ? "aircraft" : "vessel");
    age[i] = Math.floor(rand() * 30);
    ids.push(`${lane.mode === "air" ? "ac" : "mmsi"}${100000 + i}`);
  }
  return { ids, lon, lat, heading, speed, kind, age_s: age };
}
