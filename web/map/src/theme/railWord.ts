/* Rail wording for the map's popups and tooltips: sentence case. Words the data layer hands over in capitals
 * (a channel `QUEUED`, a fleet chip `EN ROUTE`, a place kind `AIR HUB`) read as one sentence-case phrase; codes
 * and units that are acronyms keep their capitals. Pure. */
const KEEP = new Set(["AIS", "UTC", "TEU", "LNG", "SAR", "ETA", "ID", "UN", "EU", "US", "UK", "USD", "EIA", "OSM"]);

export function railWord(s: string): string {
  const t = s.trim();
  if (!t) return t;
  // not all capitals (a place name, a served sentence) or no letters (a code with digits only): leave it alone
  if (t !== t.toUpperCase() || !/[A-Z]/.test(t) || /\d/.test(t)) return t;
  let first = true;
  return t
    .toLowerCase()
    .split(/(\s+|-)/)
    .map((w) => {
      if (!/[a-z]/.test(w)) return w;
      const up = w.toUpperCase();
      if (KEEP.has(up)) { first = false; return up; }
      if (first) { first = false; return w.charAt(0).toUpperCase() + w.slice(1); }
      return w;
    })
    .join("");
}

/** First letter up, the rest as given: a served lower-case word (`sea`, `port`, `congestion`) as a label. */
export const capFirst = (s: string): string => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
