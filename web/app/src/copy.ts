/**
 * Sentence case for display text that arrives in capitals from a view-model
 * key or served label: `NOT SERVED` -> `Not
 * served`, `REV 1` -> `Rev 1`; codes and units (CHN, TEU, UTC, CSV, ...) keep
 * their capitals; mixed-case text is returned untouched. Pure. Use it where a
 * caps token must stay a stable key in code but is shown to a person.
 */
export const CODES = new Set([
  'CHN', 'TEU', 'TEUS', 'UTC', 'GMT', 'CSV', 'JSON', 'PNG', 'SVG', 'API', 'SEC', 'EDGAR', 'NOAA', 'NWS', 'GDACS', 'GDELT', 'AIS', 'LNG', 'OK', 'ID', 'IDS', 'URL', 'HTML', 'PDF', 'XLSX', 'ZIP',
  'SHA256', 'SIM', 'UN', 'US', 'USD', 'EUR', 'ETA', 'KPI', 'CBP', 'EIA', 'LOA', 'AOI', 'HIS', 'RLS', 'SCN', 'EXP', 'PRT', 'MKT', 'SWT', 'HLT', 'LYR', 'TRC', 'WIF', 'ENS',
  'IMP', 'DSK', 'PRF', 'LDG', 'MNY', 'XPL', 'ALR', 'RPT', 'DAT', 'LNE', 'MCS', 'SHP', 'WCH', 'PTW', 'LTV', 'LVF', 'LVT', 'LVC', 'CHT', 'DST', 'AI', 'HS', 'SITC', 'FRED', 'IMF', 'PMI', 'BDI', 'WTI', 'SCL',
  'SAM', 'LLM', 'USGS', 'FAO', 'WTO', 'UNCTAD', 'OECD', 'CPPI', 'EU', 'UK', 'DB', 'OSM', 'CC', 'NC', 'IGO', 'NWS', 'HTTP', 'HTTPS', 'CRS', 'GPS', 'EEZ', 'NTAD', 'PDF', 'TSV',
  'XML', 'MD', 'CFAR', 'ESA', 'USDA', 'NAIP', 'NASA', 'EOSDIS', 'OSM', 'MAE', 'VV', 'VH', 'SHA', 'ICAO24', 'PNGS', 'XLSX', 'CORS', 'ID', 'UI', 'ISO', 'UUID', 'GB', 'KB', 'MB', 'TB', 'NM', 'KM', 'KN', 'DWT', 'GT', 'S&P', 'WB', 'LOCODE', 'UNLOCODE', 'IMO', 'MMSI', 'ENC', 'PNG', 'JPG',
  'JPEG', 'SQL', 'CPU', 'GPU', 'RAM', 'CI', 'QA', 'IP', 'DNS', 'TLS', 'SSL', 'SAR', 'RGB', 'NIR', 'WGS84', 'EPSG', 'L2A', 'L1C', 'SCL', 'EDT', 'EST', 'PST', 'PDT', 'CET', 'CEST', 'BST', 'JST',
  'TBD', 'N/A', 'ROI', 'P50', 'P90', 'P10', 'P95', 'P05', 'DOI', 'TSS',
]);

const PROPER: Record<string, string> = {
  PORTWATCH: 'PortWatch', OPENSKY: 'OpenSky', AISSTREAM: 'AISStream', SENTINEL: 'Sentinel', 'SENTINEL-2': 'Sentinel-2', MARINETRAFFIC: 'MarineTraffic',
  SUPERMAP: 'SupermAP', 'S&P': 'S&P', GITHUB: 'GitHub', MAPLIBRE: 'MapLibre',
};
const MASK = /\\u[0-9a-fA-F]{4}|\\x[0-9a-fA-F]{2}|&[a-z]+;|&#\d+;/g;
const WORD = /^[A-Z][A-Z0-9'’&]*$/;
const NEUTRAL = /^[0-9.,+%\-–—−:;()/#×<>=~≈≤≥?!*"'\[\]]+$/;

interface Tok { raw: string; pre: string; core: string; post: string }

function split(raw: string): Tok[] {
  const toks: Tok[] = [];
  const re = /\S+|\s+/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(raw))) {
    const t = m[0];
    if (/^\s/.test(t)) { toks.push({ raw: t, pre: '', core: '', post: '' }); continue; }
    const mm = t.match(/^([^A-Za-z0-9]*)(.*?)([^A-Za-z0-9]*)$/s)!;
    toks.push({ raw: t, pre: mm[1], core: mm[2], post: mm[3] });
  }
  return toks;
}

const isCaps = (core: string): boolean => {
  if (!core) return false;
  // a slashed or hyphenated compound is caps when every part is
  const parts = core.split(/[/\-]/).filter(Boolean);
  return parts.length > 0 && parts.some((p) => WORD.test(p)) && parts.every((p) => WORD.test(p) || /^\d+$/.test(p));
};
const isCode = (core: string): boolean => {
  if (CODES.has(core.toUpperCase()) && core === core.toUpperCase()) return true;
  const parts = core.split(/[/]/).filter(Boolean);
  return parts.length > 1 && parts.every((p) => CODES.has(p) || /^\d+$/.test(p));
};
const lowerCompound = (core: string): string => PROPER[core] ?? lowerCompound0(core);
const lowerCompound0 = (core: string): string => core.split('/').map((p) => (CODES.has(p) ? p : p.split('-').map((q) => (CODES.has(q) ? q : q.toLowerCase())).join('-'))).join('/');

/** Returns the converted text, or null when nothing changed. */
function convert(input: string, capFirst: boolean): string | null {
  if (/\bCC BY\b/.test(input)) return null;
  const masks: string[] = [];
  const text = input.replace(MASK, (m) => { masks.push(m); return `\u0001${masks.length - 1}\u0002`; });
  const toks = split(text);
  // classify tokens: caps-word (non-code), code, neutral, other
  type K = 'ws' | 'sep' | 'caps' | 'code' | 'neutral' | 'other';
  const kind: K[] = toks.map((t) => {
    if (!t.core && !t.pre && !t.post) return 'ws';
    if (/^[·|—–]$/.test(t.raw)) return 'sep';
    if (!t.core) return 'neutral';
    if (t.core.includes('\u0001')) return 'other';
    if (isCaps(t.core)) return isCode(t.core) ? 'code' : (t.core.length >= 2 || /^[A-Z]$/.test(t.core) ? 'caps' : 'other');
    if (NEUTRAL.test(t.core)) return 'neutral';
    if (/^\d/.test(t.core) && /^[0-9][0-9A-Za-z.,]*$/.test(t.core)) return 'neutral';
    return 'other';
  });
  // runs of caps (with neutral / code / ws between), broken by sep or other
  const out = toks.map((t) => t.raw);
  const lowered = new Set<number>();
  let i = 0;
  let changed = false;
  while (i < toks.length) {
    if (kind[i] !== 'caps' && kind[i] !== 'code') { i += 1; continue; }
    let j = i; let last = i;
    while (j < toks.length) {
      if (kind[j] === 'caps' || kind[j] === 'code') last = j;
      else if (kind[j] === 'ws' || kind[j] === 'neutral') { /* continue */ } else break;
      j += 1;
    }
    const idxs: number[] = [];
    for (let k = i; k <= last; k += 1) if (kind[k] === 'caps') idxs.push(k);
    const longest = Math.max(0, ...idxs.map((k) => toks[k].core.length));
    const eligible = idxs.length >= 2 ? idxs.some((k) => toks[k].core.length >= 2) : (idxs.length === 1 && toks[idxs[0]].core.length >= 3 && !kind.includes('other'));
    if (eligible && longest >= 2) {
      for (const k of idxs) {
        const t = toks[k];
        if (t.core.length === 1) {
          // "I" and labels such as "A / B" stay; the article A goes down before a word
          let nx = k + 1; while (nx < toks.length && kind[nx] === 'ws') nx += 1;
          if (!(t.core === 'A' && nx < toks.length && kind[nx] === 'caps' && toks[nx].core.length >= 2)) continue;
        }
        out[k] = t.pre + lowerCompound(t.core) + t.post;
        lowered.add(k);
        changed = true;
      }
    }
    i = last + 1;
  }
  if (!changed) return null;
  // capitalise: first word of the text (when capFirst) and the first word after a separator
  let startOfSegment = capFirst;
  for (let k = 0; k < toks.length; k += 1) {
    if (kind[k] === 'ws') continue;
    if (kind[k] === 'sep') { startOfSegment = true; continue; }
    if (startOfSegment) {
      if (lowered.has(k) || (kind[k] === 'other' && false)) {
        out[k] = out[k].replace(/^([^A-Za-z]*)([a-z])/, (_m, a, c) => a + c.toUpperCase());
      }
      if (kind[k] !== 'neutral' || /[0-9A-Za-z]/.test(toks[k].raw)) startOfSegment = false;
    }
    if (/[.!?]$/.test(toks[k].raw) && kind[k] !== 'neutral') startOfSegment = true;
  }
  let res = out.join('');
  res = res.replace(/\u0001(\d+)\u0002/g, (_, n) => masks[Number(n)]);
  return res === input ? null : res;
}

/** Sentence-case `text` for display; text with nothing to change comes back as is. */
export const sentenceCase = (text: string, capFirst = true): string => convert(text, capFirst) ?? text;

/**
 * A served identifier or enum read as a label: `capacity_cut` -> `Capacity cut`,
 * `sea` -> `Sea`, `NOT_SERVED` -> `Not served`. Already-readable mixed-case
 * text is returned as it is.
 */
export function labelWord(raw: unknown): string {
  const t = String(raw ?? '').replace(/_+/g, ' ').trim();
  if (!t) return t;
  if (t === t.toLowerCase()) return t.charAt(0).toUpperCase() + t.slice(1);
  if (t === t.toUpperCase()) return sentenceCase(t);
  return t;
}
