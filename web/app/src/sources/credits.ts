/* SOURCE CREDITS. One registry of every external source the product shows or exports:
 * display line, licence, URL and whether attribution is required in the product. The data lives in
 * data/sources/credits.json (the server reads the same file); this module types it and matches free
 * source strings against it. No prose: a credit is a line, a short transformation note and a licence word. */
import registry from '../../../../data/sources/credits.json';

export type CreditSurface = 'sources' | 'map' | 'footer' | 'export' | 'provenance';
export type CreditGroup = 'data' | 'map' | 'imagery' | 'model' | 'font';

export interface Credit {
  id: string;
  name: string;
  group: CreditGroup;
  /** The exact display line. */
  line: string;
  /** A statement of the transformation applied, shown next to the line (IMF terms). */
  transformed: string | null;
  licence: string;
  /** Two or three words for the list: "CC BY 4.0", "Public domain". */
  licence_short: string;
  url: string;
  terms_url: string;
  /** True when the publisher's terms require attribution in the product. */
  required: boolean;
  /** Where the credit is rendered. */
  surfaces: CreditSurface[];
  /** Lower-case words that identify the source in a free source string. */
  match: string[];
  /** Short text for the map's attribution control (the line itself when absent). */
  map_text: string | null;
  note: string | null;
}

export const CREDITS_VERSION: string = registry.version;
export const CREDITS: readonly Credit[] = registry.sources as unknown as Credit[];
export const GROUP_LABEL: Readonly<Record<CreditGroup, string>> = { data: 'Data', map: 'Map', imagery: 'Imagery', model: 'Models', font: 'Fonts' };
export const GROUPS: readonly CreditGroup[] = ['data', 'map', 'imagery', 'model', 'font'];

const esc = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const PATTERNS: ReadonlyArray<{ c: Credit; re: RegExp }> = CREDITS.map((c) => ({ c, re: new RegExp(`(?:^|[^A-Za-z0-9])(?:${c.match.map(esc).join('|')})(?:$|[^A-Za-z0-9])`, 'i') }));

export const creditById = (id: string): Credit | undefined => CREDITS.find((c) => c.id === id);
export const requiredCredits = (): Credit[] => CREDITS.filter((c) => c.required);
export const creditsOn = (surface: CreditSurface): Credit[] => CREDITS.filter((c) => c.surfaces.includes(surface));

/** The display line with its transformation note, one string. */
export const creditText = (c: Credit): string => (c.transformed ? `${c.line} · ${c.transformed}` : c.line);

/** Credits whose words occur in any of the given free source strings (registry order, no repeats). */
export function creditsFor(...texts: ReadonlyArray<string | null | undefined>): Credit[] {
  const hay = texts.filter((t): t is string => typeof t === 'string' && t !== '').join(' \n ');
  if (!hay) return [];
  return PATTERNS.filter(({ re }) => re.test(hay)).map(({ c }) => c);
}

/** One footer line for an export: the matched credits, else the stated source. Never empty. */
export function sourceLineFor(...texts: ReadonlyArray<string | null | undefined>): string {
  const hit = creditsFor(...texts);
  if (hit.length) return hit.map(creditText).join(' | ');
  const own = texts.find((t) => typeof t === 'string' && t.trim() !== '');
  return own ? `Source: ${own.trim().replace(/^source:\s*/i, '')}` : 'Source: SupermAP stored data';
}

/** A stated source as it should read in a caption or footer: the registry credit lines when the string
 *  names a registered source, else the string itself. */
export function captionSource(source: string | null | undefined): string | null {
  if (!source) return null;
  const hit = creditsFor(source);
  return hit.length ? hit.map(creditText).join(' | ') : source;
}
