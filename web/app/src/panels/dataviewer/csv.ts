/* RFC 4180 CSV reader for the exports the server writes. Pure. */

/** Parse CSV text into rows of strings. Handles quoted fields with
 *  embedded commas, quotes ("") and newlines, CRLF, a BOM and a missing
 *  final newline. A blank final line is not a row. */
export function parseCsv(text: string): string[][] {
  const src = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
  const rows: string[][] = [];
  let row: string[] = []; let field = ''; let q = false; let i = 0;
  const n = src.length;
  while (i < n) {
    const c = src[i];
    if (q) {
      if (c === '"') {
        if (src[i + 1] === '"') { field += '"'; i += 2; continue; }
        q = false; i++; continue;
      }
      field += c; i++; continue;
    }
    if (c === '"' && field === '') { q = true; i++; continue; }
    if (c === ',') { row.push(field); field = ''; i++; continue; }
    if (c === '\r') { i++; continue; }
    if (c === '\n') { row.push(field); rows.push(row); row = []; field = ''; i++; continue; }
    field += c; i++;
  }
  if (field !== '' || row.length > 0) { row.push(field); rows.push(row); }
  return rows;
}

/** The server prefixes a quote to strings that start with = + - @ (the
 *  spreadsheet formula guard). The viewer shows the text as written. */
export function unguard(cell: string): string {
  return cell.length > 1 && cell[0] === "'" && '=+-@'.includes(cell[1]) ? cell.slice(1) : cell;
}
