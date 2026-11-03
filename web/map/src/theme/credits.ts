/* Map credits from the shared registry (data/sources/credits.json): the text each source needs on the map,
 * and the same text as links for the attribution control. */
import registry from "../../../../data/sources/credits.json";

interface MapCredit {
  id: string;
  line: string;
  transformed: string | null;
  url: string;
  required: boolean;
  surfaces: string[];
  map_text: string | null;
}

const ALL = registry.sources as unknown as MapCredit[];
const byId = (id: string): MapCredit => {
  const c = ALL.find((x) => x.id === id);
  if (!c) throw new Error(`credit ${id} is not in the registry`);
  return c;
};

/** The registry sources that must show on the map's attribution control. */
export const MAP_CREDIT_IDS: readonly string[] = ALL.filter((c) => c.surfaces.includes("map")).map((c) => c.id);

/** Plain text of a map credit (the registry's `map_text`, with the transformation note where one is stated). */
export function mapCreditText(id: string, withNote = false): string {
  const c = byId(id);
  const t = c.map_text ?? c.line;
  return withNote && c.transformed ? `${t} · ${c.transformed}` : t;
}

const esc = (s: string): string => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** The credit as a link to its source (the attribution control accepts markup; every part is escaped). */
export function mapCreditHtml(id: string, withNote = false): string {
  const c = byId(id);
  const t = esc(mapCreditText(id, false));
  const a = /^https:\/\//.test(c.url) ? `<a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${t}</a>` : t;
  return withNote && c.transformed ? `${a} · ${esc(c.transformed)}` : a;
}
