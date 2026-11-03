/* CSV import of a shipment list. Pure: text in, shipments and counts out.
 * The delimiter is read from the header line (comma, semicolon or tab), quotes
 * follow RFC 4180, and headers are matched by name (case, spaces and
 * punctuation ignored). Text cells are cleaned the way the server cleans them:
 * control characters removed, whitespace collapsed, a leading = + - @ stripped. */
import type { Shipment, ShipMode } from './types';

export const MAX_ROWS = 500;
export const MAX_TEXT = 120;
export const MAX_ID = 64;
export const MAX_UNIT = 16;
const MAX_NUMBER = 1e12;

export function cleanText(v: unknown): string {
  // eslint-disable-next-line no-control-regex
  let t = String(v ?? '').replace(/[\u0000-\u001f\u007f-\u009f ​-‏‪-‮⁦-⁩﻿]/g, ' ');
  t = t.split(/\s+/).filter(Boolean).join(' ');
  while (t && '=+-@\t\r '.includes(t[0])) t = t.slice(1).trimStart();
  return t;
}

/** Records of a delimited text. Handles quoted fields with doubled quotes and line breaks inside quotes. */
export function parseCsv(text: string): string[][] {
  const src = text.replace(/^﻿/, '');
  const first = src.split(/\r\n|\n|\r/, 1)[0] ?? '';
  const count = (c: string) => first.split(c).length - 1;
  const delim = count('\t') > 0 && count('\t') >= count(',') ? '\t' : count(';') > count(',') ? ';' : ',';
  const rows: string[][] = [];
  let row: string[] = []; let cell = ''; let quoted = false; let any = false;
  const endCell = () => { row.push(cell); cell = ''; };
  const endRow = () => { endCell(); if (any || row.some((c) => c !== '')) rows.push(row); row = []; any = false; };
  for (let i = 0; i < src.length; i++) {
    const ch = src[i];
    if (quoted) {
      if (ch === '"') { if (src[i + 1] === '"') { cell += '"'; i++; } else quoted = false; } else cell += ch;
      continue;
    }
    if (ch === '"' && cell === '') { quoted = true; any = true; } else if (ch === delim) { endCell(); any = true; }
    else if (ch === '\n' || ch === '\r') { if (ch === '\r' && src[i + 1] === '\n') i++; endRow(); }
    else cell += ch;
  }
  if (cell !== '' || row.length) endRow();
  return rows;
}

type Field = 'id' | 'origin' | 'destination' | 'mode' | 'etd' | 'eta' | 'value' | 'teu' | 'quantity' | 'unit' | 'via' | 'route';
const HEADERS: Record<Field, string[]> = {
  id: ['id', 'shipmentid', 'shipment', 'reference', 'ref', 'booking', 'bookingref', 'bookingno', 'bol', 'billoflading', 'po', 'ponumber'],
  origin: ['origin', 'from', 'pol', 'portofloading', 'originport', 'loadport', 'origincity', 'originplace', 'departure place'],
  destination: ['destination', 'to', 'pod', 'portofdischarge', 'destinationport', 'dischargeport', 'destinationcity', 'destinationplace', 'dest'],
  mode: ['mode', 'transportmode', 'modeoftransport', 'transport'],
  etd: ['etd', 'departure', 'departuredate', 'shipdate', 'depart', 'sailing', 'sailingdate'],
  eta: ['eta', 'arrival', 'arrivaldate', 'deliverydate', 'arrive'],
  value: ['value', 'valueusd', 'cargovalue', 'cargovalueusd', 'goodsvalue', 'invoicevalue', 'usd', 'amount'],
  teu: ['teu', 'teus', 'containers'],
  quantity: ['quantity', 'qty'],
  unit: ['unit', 'uom'],
  via: ['via', 'viaplace', 'transshipment', 'transhipment', 'waypoint'],
  route: ['route', 'legs', 'routing', 'path', 'itinerary'],
};
const norm = (h: string) => h.toLowerCase().replace(/[^a-z0-9]+/g, '');
const LOOKUP = new Map<string, Field>();
for (const [f, names] of Object.entries(HEADERS) as Array<[Field, string[]]>) for (const n of names) LOOKUP.set(norm(n), f);

const MODE_WORDS: Record<string, ShipMode> = {
  sea: 'sea', ocean: 'sea', maritime: 'sea', vessel: 'sea', fcl: 'sea', lcl: 'sea', container: 'sea', port: 'sea',
  air: 'air', airfreight: 'air', flight: 'air',
  ground: 'ground', road: 'ground', truck: 'ground', rail: 'ground', land: 'ground', trucking: 'ground', intermodal: 'ground',
};

export function readMode(v: unknown): ShipMode | null {
  const k = String(v ?? '').toLowerCase().replace(/[^a-z]+/g, '');
  if (!k) return null;
  return MODE_WORDS[k] ?? null;
}

export function readNumber(v: unknown): number | null {
  const s = String(v ?? '').trim();
  if (!s) return null;
  let t = s.replace(/[A-Za-z$€£\s]/g, '');
  if (/^\d{1,3}(\.\d{3})+(,\d+)?$/.test(t)) t = t.replace(/\./g, '').replace(',', '.');      // 1.234.567,89
  else if (/^\d+,\d{1,2}$/.test(t)) t = t.replace(',', '.');                                       // 12,5
  else t = t.replace(/,/g, '');
  if (!/^\d+(\.\d+)?$/.test(t)) return null;
  const n = Number(t);
  return Number.isFinite(n) && n >= 0 && n <= MAX_NUMBER ? n : null;
}

export function readDate(v: unknown): string | null {
  const s = String(v ?? '').trim();
  const m = /^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[T ].*)?$/.exec(s);
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const dt = new Date(Date.UTC(y, mo - 1, d));
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return null;
  return `${String(y).padStart(4, '0')}-${String(mo).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}

/** "Shanghai > Singapore > Rotterdam": the legs of a route as place texts. */
export function splitRoute(v: string): string[] {
  return v.split(/\s*(?:->|=>|→|>|\||;)\s*/).map(cleanText).filter(Boolean);
}

export interface ImportRejection { line: number; reason: 'missing_origin' | 'missing_destination' | 'too_many' | 'empty_row' }
export interface ImportResult {
  shipments: Shipment[]; rejected: ImportRejection[];
  columns: Field[]; unknownColumns: number;
  /** cells that held text the importer could not read (dates, values, modes) */
  unread: { dates: number; values: number; modes: number };
  truncated: boolean;
  fileError: 'no_rows' | 'no_columns' | null;
}

export function importShipments(text: string, existingIds: Iterable<string> = []): ImportResult {
  const out: ImportResult = { shipments: [], rejected: [], columns: [], unknownColumns: 0, unread: { dates: 0, values: 0, modes: 0 }, truncated: false, fileError: null };
  const recs = parseCsv(text);
  if (recs.length < 1) { out.fileError = 'no_rows'; return out; }
  const head = recs[0].map((h) => LOOKUP.get(norm(h)) ?? null);
  out.unknownColumns = head.filter((h) => h === null).length;
  const seenField = new Set(head.filter((h): h is Field => h !== null));
  out.columns = [...seenField];
  const hasEnds = (seenField.has('origin') && seenField.has('destination')) || seenField.has('route');
  if (!hasEnds) { out.fileError = 'no_columns'; return out; }
  if (recs.length < 2) { out.fileError = 'no_rows'; return out; }
  const used = new Set<string>(existingIds);
  let seq = 0;
  const nextId = () => { let id: string; do { seq += 1; id = `S-${String(seq).padStart(3, '0')}`; } while (used.has(id)); return id; };
  for (let r = 1; r < recs.length; r++) {
    const rec = recs[r];
    const cell: Partial<Record<Field, string>> = {};
    head.forEach((f, i) => { if (f && cell[f] === undefined && rec[i] !== undefined) cell[f] = rec[i]; });
    if (Object.values(cell).every((c) => !String(c ?? '').trim())) continue;
    if (out.shipments.length >= MAX_ROWS) { out.truncated = true; out.rejected.push({ line: r + 1, reason: 'too_many' }); continue; }
    let origin = cleanText(cell.origin);
    let destination = cleanText(cell.destination);
    let via = cleanText(cell.via);
    const legs = cell.route ? splitRoute(cell.route) : [];
    if (legs.length >= 2) {
      if (!origin) origin = legs[0];
      if (!destination) destination = legs[legs.length - 1];
      if (!via && legs.length >= 3) via = legs[1];
    }
    if (!origin) { out.rejected.push({ line: r + 1, reason: 'missing_origin' }); continue; }
    if (!destination) { out.rejected.push({ line: r + 1, reason: 'missing_destination' }); continue; }
    let id = cleanText(cell.id).slice(0, MAX_ID);
    if (!id || used.has(id)) { const base = id; id = nextId(); if (base) id = `${base.slice(0, MAX_ID - 8)}#${id.slice(2)}`; }
    used.add(id);
    const s: Shipment = { id, origin: origin.slice(0, MAX_TEXT), destination: destination.slice(0, MAX_TEXT), mode: 'sea' };
    if (cell.mode?.trim()) { const m = readMode(cell.mode); if (m) s.mode = m; else out.unread.modes += 1; }
    for (const k of ['etd', 'eta'] as const) {
      const raw = cell[k]; if (!raw?.trim()) continue;
      const d = readDate(raw); if (d) s[k] = d; else out.unread.dates += 1;
    }
    if (cell.value?.trim()) { const n = readNumber(cell.value); if (n !== null) s.value_usd = n; else out.unread.values += 1; }
    const qRaw = cell.teu?.trim() ? cell.teu : cell.quantity;
    if (qRaw?.trim()) {
      const n = readNumber(qRaw);
      if (n !== null) { s.quantity = n; s.unit = cell.teu?.trim() ? 'TEU' : (cleanText(cell.unit).slice(0, MAX_UNIT) || 'TEU'); } else out.unread.values += 1;
    }
    if (via) s.via = via.slice(0, MAX_TEXT);
    out.shipments.push(s);
  }
  if (out.shipments.length === 0 && out.fileError === null) out.fileError = 'no_rows';
  return out;
}
