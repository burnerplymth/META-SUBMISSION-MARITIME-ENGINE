// Per-panel export: CSV / JSON / PNG (where meaningful) / data-pack.
// Data-pack = JSON payload + provenance manifest (source + retrieved_at per file).
function download(name: string, blob: Blob) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}
export function exportJSON(name: string, data: unknown) {
  download(`${name}.json`, new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
}
/** Spreadsheet formula-cell neutralisation — a cell whose text
 *  starts with =, +, - or @ (after optional leading whitespace) is
 *  prefixed with a single quote so a spreadsheet opens it as text. */
export function neutraliseCsvCell(text: string): string {
  return /^(\s*[=+\-@]|[\t\r])/.test(text) ? `'${text}` : text;
}
export function csvCell(v: unknown): string {
  return `"${neutraliseCsvCell(String(v ?? '')).replace(/"/g, '""')}"`;
}
export function exportCSV(name: string, rows: Array<Record<string, unknown>>) {
  if (!rows.length) return;
  const cols = Object.keys(rows[0]);
  const csv = [cols.map((c) => csvCell(c)).join(','), ...rows.map((r) => cols.map((c) => csvCell(r[c])).join(','))].join('\n');
  download(`${name}.csv`, new Blob([csv], { type: 'text/csv' }));
}
export function exportDataPack(name: string, data: unknown, provenance: Array<{ file: string; source: string; retrieved_at?: string; mode: string }>) {
  const pack = { pack: name, exported_at: new Date().toISOString(), provenance_manifest: provenance, data };
  download(`${name}.datapack.json`, new Blob([JSON.stringify(pack, null, 2)], { type: 'application/json' }));
}
/** Prepare an SVG for serialisation by inlining
 *  COMPUTED styles onto a clone. The panels' chart SVG paint is
 *  CSS-variable-based (styles.css custom properties + classes), and a
 *  serialised SVG carries neither the stylesheet nor the variables —
 *  rasterised as an <img> it would render unstyled (default black).
 *  Walking the source and the clone together in document order and
 *  copying each element's computed style onto the clone's style
 *  attribute bakes the resolved paint into the markup itself. The
 *  live element is never mutated. When no computed-style source
 *  exists (non-DOM environment) the clone is returned with only the
 *  xmlns attribute — callers in the browser always have one. */
export function inlineSvgComputedStyles(svgEl: SVGSVGElement): SVGSVGElement {
  const clone = svgEl.cloneNode(true) as SVGSVGElement;
  if (!clone.getAttribute('xmlns')) clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  if (typeof getComputedStyle !== 'function') return clone;
  const srcEls: Element[] = [svgEl, ...Array.from(svgEl.querySelectorAll('*'))];
  const dstEls: Element[] = [clone, ...Array.from(clone.querySelectorAll('*'))];
  srcEls.forEach((src, i) => {
    const dst = dstEls[i];
    if (!dst) return;
    const cs = getComputedStyle(src);
    let css = '';
    for (let p = 0; p < cs.length; p++) {
      const prop = cs[p];
      const val = cs.getPropertyValue(prop);
      if (prop && val) css += `${prop}:${val};`;
    }
    if (css) dst.setAttribute('style', css);
  });
  return clone;
}

/** Exports ALWAYS carry institutional styling. The retro themes
 *  are a screen-only register: while a retro theme is active, the PNG
 *  path pins the document theme to the paired institutional variant
 *  (retro-dark → dark, retro-light → light) for the duration of the
 *  computed-style serialisation, then restores the live theme. The
 *  clone's styles are baked inline at serialise time, so the restore
 *  cannot leak retro paint into the file. JSON/CSV/data-pack exports
 *  carry no styling at all and need no pin; server-generated reports
 *  never read the app theme. */
export function institutionalThemeFor(theme: string | undefined): 'dark' | 'light' {
  return theme === 'light' || theme === 'retro-light' ? 'light' : 'dark';
}

/** PNG export: serialise the panel's inline SVG (map / DAG / chart) to a PNG. */
export function exportPNG(name: string, svgEl: SVGSVGElement | null) {
  if (!svgEl) return;
  const rootEl = typeof document !== 'undefined' ? document.documentElement : undefined;
  const prevTheme = rootEl?.dataset.theme;
  const pinned = prevTheme === 'retro-dark' || prevTheme === 'retro-light';
  if (rootEl && pinned) rootEl.dataset.theme = institutionalThemeFor(prevTheme);
  let xml: string;
  try {
    xml = new XMLSerializer().serializeToString(inlineSvgComputedStyles(svgEl));
  } finally {
    if (rootEl && pinned) rootEl.dataset.theme = prevTheme as string;
  }
  const img = new Image();
  img.onload = () => {
    const c = document.createElement('canvas'); c.width = img.width || 960; c.height = img.height || 540;
    c.getContext('2d')?.drawImage(img, 0, 0);
    c.toBlob((b) => b && download(`${name}.png`, b));
  };
  img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(xml)}`;
}
