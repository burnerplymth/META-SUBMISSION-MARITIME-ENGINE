/* A small XLSX writer for client-held tables: one data sheet (bold frozen
 * header row, auto-filter, typed cells) and one Provenance sheet (the
 * header block). Strings are inline strings, never formulas, so a cell
 * that starts with = + - @ is data and stays inert. Fixed zip timestamps:
 * equal inputs give equal bytes. The server's workbook writer
 * (server/reports/xlsx.py) stays the source for run datasets. */
import { escXml, type HeaderEntry } from './header';
import { writeZip } from './zipWrite';

const enc = new TextEncoder();

export function colName(i: number): string {
  let n = i + 1; let s = '';
  while (n > 0) { const r = (n - 1) % 26; s = String.fromCharCode(65 + r) + s; n = Math.floor((n - 1) / 26); }
  return s;
}

/** Excel sheet names: at most 31 characters, none of : \ / ? * [ ], not empty. */
export function sheetName(raw: string, taken: ReadonlySet<string> = new Set()): string {
  let base = raw.replace(/[:\\/?*[\]]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 31) || 'Sheet';
  let name = base; let i = 2;
  while (taken.has(name.toLowerCase())) { const suf = ` ${i++}`; name = base.slice(0, 31 - suf.length) + suf; }
  return name;
}

type Cell = string | number | null | undefined;

function cellXml(ref: string, v: Cell, style = 0): string {
  const s = style ? ` s="${style}"` : '';
  if (v == null || (typeof v === 'number' && !Number.isFinite(v))) return '';
  if (typeof v === 'number') return `<c r="${ref}"${s}><v>${v}</v></c>`;
  const t = String(v);
  if (t === '') return '';
  return `<c r="${ref}"${s} t="inlineStr"><is><t xml:space="preserve">${escXml(t)}</t></is></c>`;
}

function dataSheet(columns: readonly string[], rows: ReadonlyArray<ReadonlyArray<Cell>>): string {
  const widths = columns.map((c, i) => {
    let w = c.length;
    for (let r = 0; r < Math.min(rows.length, 200); r++) { const v = rows[r][i]; if (v != null) w = Math.max(w, String(v).length); }
    return Math.min(60, Math.max(8, w + 2));
  });
  const last = `${colName(Math.max(0, columns.length - 1))}${rows.length + 1}`;
  const body: string[] = [];
  body.push(`<row r="1">${columns.map((c, i) => cellXml(`${colName(i)}1`, c, 1)).join('')}</row>`);
  rows.forEach((row, r) => {
    body.push(`<row r="${r + 2}">${columns.map((_, i) => cellXml(`${colName(i)}${r + 2}`, row[i])).join('')}</row>`);
  });
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">`
    + `<dimension ref="A1:${last}"/>`
    + `<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>`
    + `<sheetFormatPr defaultRowHeight="15"/>`
    + `<cols>${widths.map((w, i) => `<col min="${i + 1}" max="${i + 1}" width="${w}" customWidth="1"/>`).join('')}</cols>`
    + `<sheetData>${body.join('')}</sheetData>`
    + (columns.length ? `<autoFilter ref="A1:${last}"/>` : '')
    + `</worksheet>`;
}

function keyValueSheet(entries: readonly HeaderEntry[]): string {
  const body = entries.map(([k, v], i) => `<row r="${i + 1}">${cellXml(`A${i + 1}`, k, 1)}${cellXml(`B${i + 1}`, v)}</row>`).join('');
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">`
    + `<cols><col min="1" max="1" width="18" customWidth="1"/><col min="2" max="2" width="70" customWidth="1"/></cols><sheetData>${body}</sheetData></worksheet>`;
}

export interface XlsxInput {
  columns: readonly string[];
  rows: ReadonlyArray<ReadonlyArray<Cell>>;
  /** sheet name for the data (default "Data") */
  name?: string;
  /** the header block; written to a second sheet "Provenance" when present */
  header?: readonly HeaderEntry[];
}

export function buildXlsx(input: XlsxInput): Uint8Array {
  const data = sheetName(input.name ?? 'Data');
  const prov = input.header?.length ? sheetName('Provenance', new Set([data.toLowerCase()])) : null;
  const sheets = [{ name: data, xml: dataSheet(input.columns, input.rows) }, ...(prov ? [{ name: prov, xml: keyValueSheet(input.header!) }] : [])];
  const files: Array<{ name: string; bytes: Uint8Array }> = [];
  const add = (name: string, xml: string): void => { files.push({ name, bytes: enc.encode(xml) }); };
  const XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n';
  add('[Content_Types].xml', `${XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">`
    + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
    + '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    + sheets.map((_, i) => `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join('')
    + '</Types>');
  add('_rels/.rels', `${XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`);
  add('xl/workbook.xml', `${XML}<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>`
    + sheets.map((s, i) => `<sheet name="${escXml(s.name)}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`).join('') + '</sheets></workbook>');
  add('xl/_rels/workbook.xml.rels', `${XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">`
    + sheets.map((_, i) => `<Relationship Id="rId${i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`).join('')
    + `<Relationship Id="rId${sheets.length + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`);
  add('xl/styles.xml', `${XML}<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">`
    + '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
    + '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
    + '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    + '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    + '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
    + '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>');
  sheets.forEach((s, i) => add(`xl/worksheets/sheet${i + 1}.xml`, s.xml));
  return writeZip(files);
}
