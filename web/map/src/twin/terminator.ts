// TWIN day/night terminator (pure, optional layer). Solar position from
// the standard low-precision almanac formulas (good to ~1 degree, ample
// for a shading overlay). Time comes from the SIM CLOCK of the state in
// view (sim_time), so the shading follows the playhead. A display aid,
// not an observation.

const D2R = Math.PI / 180;

export interface SubSolar {
  lon: number;
  lat: number;
}

/** Sub-solar point (lon, lat in degrees) for a UTC instant. */
export function subSolarPoint(ms: number): SubSolar {
  const d = ms / 86400000 + 2440587.5 - 2451545.0; // days since J2000.0
  const g = (357.529 + 0.98560028 * d) * D2R;
  const q = 280.459 + 0.98564736 * d;
  const L = (q + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * D2R;
  const e = (23.439 - 0.00000036 * d) * D2R;
  const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L));
  const dec = Math.asin(Math.sin(e) * Math.sin(L));
  // Greenwich mean sidereal time (degrees)
  const gmst = (280.46061837 + 360.98564736629 * d) % 360;
  let lon = ra / D2R - gmst;
  lon = ((((lon + 180) % 360) + 360) % 360) - 180;
  return { lon, lat: dec / D2R };
}

/**
 * Night-side polygon ([lon, lat] ring, closed over the dark pole) for a
 * UTC instant. `steps` longitude samples across the globe.
 */
export function nightPolygon(ms: number, steps = 120): [number, number][] {
  const s = subSolarPoint(ms);
  const dec = s.lat * D2R;
  const pts: [number, number][] = [];
  for (let i = 0; i <= steps; i++) {
    const lon = -180 + (360 * i) / steps;
    const H = (lon - s.lon) * D2R; // hour angle
    // terminator latitude where solar altitude = 0
    const t = Math.abs(dec) < 1e-6 ? 1e-6 : Math.tan(dec);
    const lat = Math.atan(-Math.cos(H) / t) / D2R;
    pts.push([lon, lat]);
  }
  // the dark pole is the one opposite the sub-solar latitude's sign
  const poleLat = s.lat >= 0 ? -90 : 90;
  return [...pts, [180, poleLat], [-180, poleLat], pts[0]];
}
