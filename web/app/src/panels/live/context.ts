/* EIA / Comtrade context — pure view models for GET /feeds/eia and
 * GET /feeds/comtrade. Doctrine: every figure is one sourced value in
 * its own unit; nothing is summed (not across origins, partners or
 * goods classes); no change-since or forward figure is derived; each
 * HS chapter is its own group and never merged. */

import type { Provenance } from './model';
import { liveChipKind } from '../../shell/truthLabel';

export interface ContextRecordBase { record_type: string; provenance: Provenance; figure_label?: string; [k: string]: unknown }
export interface ContextDoc {
  feed: string; label: string; state: string; mode: string | null; last_success_at?: string | null; note?: string | null;
  provenance: Provenance | null; figure_label: string; context_only: boolean; count: number; record_types: Record<string, number>;
  records: ContextRecordBase[];
}

export interface EiaSpot { id: string; label: string; units: string; period: string; value: number; retrievedAt: string; mode: string; source: string }
/** `aggregate`: a row EIA itself serves as a grouping (World, OPEC, a region) — shown tagged, never added to the country rows. */
export interface EiaOrigin { id: string; name: string; aggregate: boolean; quantityKbbl: number; units: string; period: string; retrievedAt: string; mode: string; source: string }
export interface EiaView { spot: EiaSpot[]; originPeriod: string | null; origins: EiaOrigin[] }

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

export function eiaView(doc: ContextDoc | null | undefined): EiaView {
  const spot: EiaSpot[] = [];
  const origins: EiaOrigin[] = [];
  for (const r of doc?.records ?? []) {
    const pr = r.provenance;
    if (!pr?.source || !pr.retrieved_at) continue; // a figure that cannot show its chip is not shown
    if (r.record_type === 'eia_spot_price') {
      const v = num(r.latest_value);
      if (v === null) continue; // absent is not a zero
      spot.push({ id: String(r.series_id), label: String(r.label ?? r.series_id), units: String(r.units ?? ''), period: String(r.latest_period ?? ''), value: v, retrievedAt: pr.retrieved_at, mode: pr.mode, source: pr.source });
    } else if (r.record_type === 'eia_crude_imports_by_origin') {
      const q = num(r.quantity_kbbl);
      if (q === null) continue;
      origins.push({ id: String(r.origin_id), aggregate: !String(r.origin_id).startsWith('CTY_'), name: String(r.origin_name ?? r.origin_id), quantityKbbl: q, units: String(r.units ?? ''), period: String(r.period ?? ''), retrievedAt: pr.retrieved_at, mode: pr.mode, source: pr.source });
    }
  }
  spot.sort((a, b) => a.label.localeCompare(b.label));
  /* one period only: the latest served; origins ordered by their own quantity */
  const latest = origins.reduce<string | null>((m, o) => (m === null || o.period > m ? o.period : m), null);
  const cur = origins.filter((o) => o.period === latest).sort((a, b) => Number(a.aggregate) - Number(b.aggregate) || b.quantityKbbl - a.quantityKbbl || a.name.localeCompare(b.name));
  return { spot, originPeriod: latest, origins: cur };
}

export interface ComtradeRow {
  key: string; reporter: string; partner: string; flow: string; period: string; /** share of the basis, 0 to 1 */ share: number; measure: 'chapter_share' | 'partner_share'; basis: string;
  retrievedAt: string; mode: string; source: string;
}
export interface ComtradeGroup { chapter: string; name: string; rows: ComtradeRow[] }

/** One group per HS chapter (a goods class), each ordered by its own
 *  shares; chapters in code order. The feed carries derived shares only
 *  (UN Comtrade terms): a chapter's share of the reporter's five-chapter
 *  total, or a partner's share of the reporter's world value. Nothing is added across rows. */
export function comtradeGroups(doc: ContextDoc | null | undefined): ComtradeGroup[] {
  const by = new Map<string, ComtradeGroup>();
  for (const r of doc?.records ?? []) {
    if (r.record_type !== 'comtrade_derived') continue;
    const pr = r.provenance;
    const v = num(r.share);
    if (!pr?.source || !pr.retrieved_at || v === null) continue;
    const chapter = String(r.hs_chapter ?? '');
    const g = by.get(chapter) ?? { chapter, name: String(r.hs_chapter_name ?? ''), rows: [] };
    const rep = r.reporter as { name?: string; iso?: string } | undefined;
    const par = r.partner as { name?: string; iso?: string } | undefined;
    g.rows.push({
      key: String(r.dedupe_key ?? `${chapter}${rep?.iso}${par?.iso}${r.flow}${r.period}`), reporter: rep?.name ?? rep?.iso ?? '', partner: par?.name ?? par?.iso ?? '',
      flow: String(r.flow ?? ''), period: String(r.period ?? ''), share: v, measure: r.measure === 'partner_share' ? 'partner_share' : 'chapter_share', basis: String(r.share_field ?? ''),
      retrievedAt: pr.retrieved_at, mode: pr.mode, source: pr.source,
    });
    by.set(chapter, g);
  }
  const groups = [...by.values()].sort((a, b) => a.chapter.localeCompare(b.chapter, undefined, { numeric: true }));
  for (const g of groups) g.rows.sort((a, b) => b.period.localeCompare(a.period) || a.measure.localeCompare(b.measure) || b.share - a.share || a.key.localeCompare(b.key));
  return groups;
}

export function fmtShare(v: number): string { return `${(v * 100).toFixed(1)} %`; }

export function fmtUsd(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  return `$${Math.round(v).toLocaleString('en-US')}`;
}
export function fmtKbbl(v: number): string { return Math.round(v).toLocaleString('en-US'); }
export function fmtPrice(v: number): string { return v.toFixed(2); }

/** Chip for a context record: SOURCED, with the feed's served mode as the
 *  as-of qualifier (a LIVE pack reads LIVE, a stored copy SNAPSHOT). */
export function contextMode(mode: string | null | undefined, retrievedAt?: string | null, now: number = Date.now(), source?: string | null): 'LIVE' | 'SNAPSHOT' {
  return liveChipKind(mode, retrievedAt, source, now) === 'LIVE' ? 'LIVE' : 'SNAPSHOT';
}
