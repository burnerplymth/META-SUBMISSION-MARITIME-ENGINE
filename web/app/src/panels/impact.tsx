import { SourceLine } from '../sources/SourceLine';
import React, { useEffect, useMemo, useState } from 'react';
import { api, ApiError } from '../api';
import { Panel } from '../components/Panel';
import { SafeLink } from '../components/SafeLink';
import { nodeName } from '../seed';
import { fmtDay, humaniseProse, nodeLabel, prettyKind, runRef } from '../format';
import { displayValue } from './display';
import { useStore } from '../store';
import { runWord } from '../shell/truthLabel';
import { adapterFeedState, feedStamp, FEED_ADAPTERS } from '../shell/feedState';
import { ProvenanceCell } from '../provenance/ProvenanceCell';
import { impactFigureProvenance } from '../provenance/registry';
import { figChipClass, figState, figText, type FigSlot } from './fig';
import { MethodDisclosure } from './MethodDisclosure';
import type { GraphData, ImpactAnalogue, ImpactExposure, ImpactFigure, ImpactNode, ImpactRates, ImpactRun } from '../types';
import { Icon } from '../ui/Icon';
import { Status } from '../ui/Status';

/* ── IMPACT layer panel ─────────────────────────────────────
 * Every figure rendered here comes from the /impact payloads as
 * a {value, unit, class, source, as_of, derivation} object; the figure's
 * provenance class renders beside it as a chip (data-fig-class carries
 * the class so the chip↔payload match is machine-checkable), and the
 * derivation prints with the figure. ABSENT figures (value null) render
 * their served reason — never a zero, never a substitute. Some slots
 * are not served at all (the figure key itself is absent — share-only
 * tier-1 rows, ABSENT goods sub-profiles): those render NOT SERVED via
 * the fig.ts discipline, and no renderer reads through a missing
 * figure. Goods classes stay in their own unit classes end to end:
 * nothing is converted and nothing is merged into a single total, in
 * any unit. */

const fmtN = (n: number) => n.toLocaleString('en-US', { maximumFractionDigits: 2 });
const fmtInt = (n: number) => Math.round(n).toLocaleString('en-US');
const pct = (share: number) => `${(share * 100).toFixed(1)}%`;
const fmtUsd = (v: number) => (v >= 1e9 ? `$${(v / 1e9).toFixed(1)}B` : `$${fmtInt(v)}`);

/** Served derivation text → screen text: place names in place of ids,
 *  the run named in words. Accepts the long sentence or a short
 *  labelled field (figText). Facts are untouched. */
const deriv = (text: unknown, graph?: GraphData | null): string =>
  humaniseProse(figText(text), graph)
    .replace(/\brun_[a-z0-9]{6,}\b/g, 'this run')
    .replace(/\/impact\/run\/\{run_id\}/g, 'a loaded run');

/** Vessel-class labels, exactly the served taxonomy — a class is the
 *  ship type; the tanker class is never re-labelled as a cargo. */
const CLASS_LABELS: Record<string, string> = {
  container: 'Container', dry_bulk: 'Dry bulk', general_cargo: 'General cargo',
  roro: 'RoRo', tanker: 'Tanker',
};
const CLASS_ORDER = ['container', 'dry_bulk', 'general_cargo', 'roro', 'tanker'];

/** The provenance chip for one figure: the class text is the payload's
 *  own class (or, for a goods block, its verbatim figure_class string).
 *  A slot the payload does not serve at all has no class to claim, so
 *  the chip renders nothing — the NOT SERVED value beside it (FigVal)
 *  carries the fact. */
/** The class a goods block wears: the payload's own state (SOURCED, FALLBACK or ABSENT), never a fixed one. */
export const goodsChipClass = (g: { state: string }): string => g.state;

export function FigChip({ f, label }: { f: FigSlot; label?: string }) {
  const cls = figChipClass(f);
  if (cls == null) return null;
  return <span className="src-tag mono" data-fig-class={cls}>{label ?? cls}</span>;
}

function FigPct({ f, what, scope, runId }: { f: ImpactFigure; what: string; scope?: string | null; runId?: string | null }) {
  const text = pct(f.value as number);
  return <ProvenanceCell make={() => impactFigureProvenance({ fig: f, title: what, display: text, scope, runId, derivation: deriv(f.derivation) || null })}>{text}</ProvenanceCell>;
}

/** A figure value with its provenance drawer: the number opens "Where from" when the figure carries a value; absent and
 *  not-served slots read as before. */
function FigNum({ f, fmt = fmtN, what, scope, runId, unit }: { f: FigSlot; fmt?: (n: number) => string; what: string; scope?: string | null; runId?: string | null; unit?: string }) {
  if (figState(f) !== 'value') return <FigVal f={f} fmt={fmt} />;
  const fig = f as ImpactFigure;
  const text = fmt(fig.value as number);
  const display = unit ? `${text} ${unit}` : text;
  return <ProvenanceCell make={() => impactFigureProvenance({ fig, title: what, display, scope, runId, derivation: deriv(fig.derivation) || null })}>{text}</ProvenanceCell>;
}

function FigVal({ f, fmt = fmtN }: { f: FigSlot; fmt?: (n: number) => string }) {
  /* Three served states, three renderings (fig.ts): a figure with a
   *  value; a figure served ABSENT (value null, reason carried); and a
   *  slot the payload does not serve at all → NOT SERVED. Reading
   *  `.value` through a missing figure crashed the whole app before
   *  this discipline landed (share-only tier-1 rows, ABSENT goods
   *  sub-profiles). */
  switch (figState(f)) {
    case 'not-served': return <span className="dim">Not served</span>;
    case 'absent': return <span className="dim">Absent</span>;
    default: return <>{fmt((f as ImpactFigure).value as number)}</>;
  }
}

/** One goods-mix block: a share row per vessel class in served order,
 *  each with its own figure's chip; a class whose figure is ABSENT
 *  renders its reason instead of a share. */
export function MixRows({ mix, caption }: { mix: Record<string, ImpactFigure>; caption: string }) {
  const keys = CLASS_ORDER.filter((k) => mix[k]);
  if (!keys.length) return null;
  return (
    <div className="imp-mix">
      <p className="fineprint">{caption}</p>
      {keys.map((k) => {
        const f = mix[k];
        return (
          <div className="imp-mixrow" key={k}>
            <span className="imp-mixname">{CLASS_LABELS[k] ?? k}</span>
            <span className="imp-bar" aria-hidden>{f.value != null && <i style={{ width: `${Math.min(100, f.value * 100)}%` }} />}</span>
            {f.value != null
              ? <span className="mono amber imp-mixval">{pct(f.value)}</span>
              : <span className="mono dim imp-mixval" title={figText(f.reason) || undefined}>Absent</span>}
            <FigChip f={f} />
          </div>
        );
      })}
      {keys.filter((k) => mix[k].value == null && mix[k].reason).map((k) => (
        <p key={k} className="fineprint">{CLASS_LABELS[k] ?? k}: {figText(mix[k].reason)}</p>
      ))}
    </div>
  );
}

/** GOODS block for the place inspector (Map Content rail): the
 *  selected place's capacity, country and goods composition from
 *  GET /impact/node/{id} — SOURCED mix, FALLBACK call mix, or the
 *  served ABSENT reason, exactly as the payload states it. */
export function GoodsBlock() {
  const s = useStore();
  const node = s.selectedNode;
  useEffect(() => { if (node) void s.fetchImpactNode(node); /* eslint-disable-next-line */ }, [node]);
  const d = s.impactNodeData && node && s.impactNodeData.node_id === node ? s.impactNodeData : null;
  const g = d?.goods;
  return (
    <div className="sec"><div className="sec-t"><Icon name="chevron-down" size="xs" className="sec-chev" /> Goods</div>
      {!node && <p className="dim">Select a place</p>}
      {node && s.impactNodeSource === 'unreachable' && (
        <p className="honest">Unreachable · Goods</p>)}
      {node && s.impactNodeSource !== 'unreachable' && !d && (
        <p className="dim">Loading · Goods · {nodeName(node)}</p>)}
      {d && g && (
        <>
          <dl className="kv">
            <dt>Capacity</dt>
            <dd className="mono"><FigNum f={d.capacity} fmt={fmtInt} what="Capacity" scope={d.name} unit={d.capacity.unit ?? undefined} /> {d.capacity.unit ?? ''} <FigChip f={d.capacity} /></dd>
            <dt>Country</dt>
            <dd className="w">{d.country.name ?? <span className="dim" title={figText(d.country.reason) || undefined}>Not served</span>}</dd>
          </dl>
          {g.state === 'ABSENT' && g.goods && (
            <p className="dim"><FigChip f={g.goods} /> {figText(g.goods.reason)}</p>)}
          {(g.state === 'SOURCED' || g.state === 'FALLBACK') && (
            <>
              <p className="imp-figclass">
                <span className="src-tag mono" data-fig-class={goodsChipClass(g)}>{g.figure_class}</span>
                {g.window
                  ? <span className="dim small"> · window {g.window.start} → {g.window.end}</span>
                  : <span className="dim small"> · pinned snapshot</span>}
              </p>
              {g.total_tonnes_per_day && (
                <dl className="kv">
                  <dt>Throughput</dt>
                  <dd className="mono"><FigNum f={g.total_tonnes_per_day} fmt={fmtInt} what="Goods volume per day" scope={d.name} unit="t/day" /> t/day <FigChip f={g.total_tonnes_per_day} /></dd>
                  {g.portcalls_per_day && (<>
                    <dt>Port calls</dt>
                    <dd className="mono"><FigNum f={g.portcalls_per_day} what="Port calls per day" scope={d.name} unit="/day" /> /day <FigChip f={g.portcalls_per_day} /></dd>
                  </>)}
                </dl>)}
              {g.transits_per_day_total && g.transit_tonnes_per_day_total && (
                <dl className="kv">
                  <dt>Transits</dt>
                  <dd className="mono"><FigNum f={g.transits_per_day_total} what="Transits per day" scope={d.name} unit="/day" /> /day <FigChip f={g.transits_per_day_total} /></dd>
                  <dt>Transit tonnage</dt>
                  <dd className="mono"><FigNum f={g.transit_tonnes_per_day_total} fmt={fmtInt} what="Transit volume per day" scope={d.name} unit="t/day" /> t/day <FigChip f={g.transit_tonnes_per_day_total} /></dd>
                </dl>)}
              {g.combined_mix && <MixRows mix={g.combined_mix} caption={g.state === 'FALLBACK'
                ? 'Call mix · Fallback · Vessel-call shares'
                : 'Combined mix · Share of tonnes/day'} />}
              {g.import_mix && <MixRows mix={g.import_mix} caption="Import mix · Share of tonnes/day" />}
              {g.export_mix && <MixRows mix={g.export_mix} caption="Export mix · Share of tonnes/day" />}
              {g.transit_mix && <MixRows mix={g.transit_mix} caption="Transit mix · Share of tonnes/day" />}
              {g.state === 'FALLBACK' && g.tonnes_per_day && !('container' in g.tonnes_per_day) && (
                <p className="fineprint">Tonnage · Absent · {figText((g.tonnes_per_day as ImpactFigure).reason)}</p>)}
            </>
          )}
          {(d.country_exposure ?? []).map((row) => (
            <p key={row.iso3} className="fineprint" title={deriv(row.share.derivation, s.graph) || undefined}>
              National share · <b className="amber">{row.share.value != null ? pct(row.share.value) : '—'}</b> of {row.country} port tonnage <FigChip f={row.share} />
            </p>
          ))}
        </>
      )}
    </div>
  );
}

/* ── Panel sections ─────────────────────────────────────────────────── */

function VolumeSection({ run }: { run: ImpactRun }) {
  const s = useStore();
  const h = run.volume_at_risk.clearance_horizon;
  return (
    <>
      <h5>Volume at risk</h5>
      {run.volume_at_risk.by_unit.map((u) => (
        <div key={u.unit} className="imp-unit">
          <p className="imp-unit-head"><b>{u.unit}</b><span className="dim"> · {u.places.length} affected place{u.places.length === 1 ? '' : 's'}</span></p>
          <dl className="kv">
            <dt>Daily volume at risk</dt>
            <dd className="amber mono">≈ <FigNum f={u.daily_volume_at_risk} fmt={fmtInt} what="Daily volume at risk" scope={u.unit} runId={run.run_id} unit={u.unit} /> {u.unit} <FigChip f={u.daily_volume_at_risk} /></dd>
            <dt>Capacity-days impaired</dt>
            <dd className="mono"><FigNum f={u.capacity_days_impaired} fmt={fmtInt} what="Capacity-days impaired" scope={u.unit} runId={run.run_id} /> <FigChip f={u.capacity_days_impaired} /></dd>
          </dl>
          <table className="data-table">
            <thead><tr><th>Place</th><th className="num">Capacity</th><th className="num">At risk</th><th className="num">Worst share</th><th className="num">Max delay (d)</th></tr></thead>
            <tbody>{u.places.map((p) => (
              <tr key={p.node_id} className={s.selectedNode === p.node_id ? 'row--sel' : ''}
                onClick={() => s.selectNode(p.node_id)}>
                <td>{p.name}</td>
                <td className="mono num"><FigNum f={p.capacity} fmt={fmtInt} what="Capacity" scope={p.name} runId={run.run_id} /></td>
                <td className="mono num amber"><FigNum f={p.capacity_at_risk} fmt={fmtInt} what="Capacity at risk" scope={p.name} runId={run.run_id} /></td>
                <td className="mono num">{p.worst_magnitude.value != null ? <FigPct f={p.worst_magnitude} what="Worst share of capacity" scope={p.name} runId={run.run_id} /> : '—'}</td>
                <td className="mono num">{p.max_delay.value != null ? <FigNum f={p.max_delay} fmt={(n) => n.toFixed(1)} what="Maximum delay" scope={p.name} runId={run.run_id} unit="d" /> : '—'}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ))}
      <dl className="kv">
        <dt>Clearance horizon</dt>
        <dd className="amber mono">{h.value != null ? <><FigNum f={h} fmt={(n) => n.toFixed(1)} what="Clearance horizon" runId={run.run_id} unit="d" /> days from run start</> : 'Absent'} <FigChip f={h} /></dd>
      </dl>
    </>
  );
}

function GoodsSection({ run }: { run: ImpactRun }) {
  const s = useStore();
  const rowIds = useMemo(() => {
    const ids: string[] = [];
    const push = (id: string) => { if (!ids.includes(id)) ids.push(id); };
    run.goods_profile.port.places.forEach((p) => push(p.node_id));
    run.goods_profile.chokepoint.places.forEach((p) => push(p.node_id));
    run.volume_at_risk.by_unit.forEach((u) => u.places.forEach((p) => push(p.node_id)));
    return ids.slice(0, 30);
  }, [run]);
  const [nodes, setNodes] = useState<Record<string, ImpactNode | null>>({});
  useEffect(() => {
    let dead = false;
    setNodes({});
    void Promise.all(rowIds.map((id) => api.impactNode(id)
      .then((p) => [id, p] as const).catch(() => [id, null] as const)))
      .then((pairs) => { if (!dead) setNodes(Object.fromEntries(pairs)); });
    return () => { dead = true; };
  }, [rowIds]);
  const profileOf = (id: string) =>
    run.goods_profile.port.places.find((p) => p.node_id === id)
    ?? run.goods_profile.chokepoint.places.find((p) => p.node_id === id);
  const mixSummary = (nd: ImpactNode | null | undefined): React.ReactNode => {
    if (nd === undefined) return <span className="dim">Loading</span>;
    if (!nd) return <span className="dim">Not served</span>;
    const g = nd.goods;
    if (g.state === 'ABSENT') return <span className="dim">{figText(g.goods?.reason)}</span>;
    const mix = g.combined_mix ?? g.transit_mix;
    if (!mix) return <span className="dim">—</span>;
    const top = CLASS_ORDER.filter((k) => mix[k]?.value != null)
      .sort((a, b) => (mix[b].value ?? 0) - (mix[a].value ?? 0)).slice(0, 2);
    return top.map((k) => `${CLASS_LABELS[k]} ${pct(mix[k].value ?? 0)}`).join(' · ') || '—';
  };
  const sub = (key: 'port' | 'chokepoint', title: string) => {
    const prof = run.goods_profile[key];
    if (!prof.places.length) return null;
    if (prof.state === 'ABSENT') {
      /* ABSENT sub-profile: the payload serves no total_impaired_volume
       * and the mix slot is the single ABSENT figure itself, carrying
       * the reason — render exactly that (the pre-fix code read
       * `.unit` through the missing total and crashed the app). */
      const absFig = prof.mix as ImpactFigure;
      return (
        <div key={key} className="imp-unit">
          <p className="imp-unit-head"><b>{title}</b><span className="dim"> · {key === 'port' ? 'Ports' : 'Straits and canals'}</span></p>
          <p className="dim"><FigChip f={absFig} /> {figText(absFig.reason)}</p>
        </div>
      );
    }
    const mix = prof.mix as Record<string, ImpactFigure>;
    const firstMix = Object.values(mix)[0];
    return (
      <div key={key} className="imp-unit">
        <p className="imp-unit-head"><b>{title}</b><span className="dim"> · {key === 'port' ? 'Ports' : 'Straits and canals'}</span></p>
        <dl className="kv">
          <dt>Impaired volume</dt>
          <dd className="mono"><FigNum f={prof.total_impaired_volume} fmt={fmtInt} what="Impaired volume" scope={key === 'port' ? 'Ports' : 'Straits and canals'} runId={run.run_id} unit={prof.total_impaired_volume?.unit ?? undefined} /> {prof.total_impaired_volume?.unit ?? ''} <FigChip f={prof.total_impaired_volume} /></dd>
        </dl>
        <MixRows mix={mix} caption={`Goods mix · ${key === 'port' ? 'Port' : 'Strait or canal'}`} />
      </div>
    );
  };
  return (
    <>
      <h5>Goods at risk</h5>
      {sub('port', 'Ports')}
      {sub('chokepoint', 'Straits & canals')}
      <table className="data-table">
        <thead><tr><th>Place</th><th>Goods</th><th>Mix (top classes)</th><th>Window</th></tr></thead>
        <tbody>{rowIds.map((id) => {
          const prof = profileOf(id);
          const nd = nodes[id];
          const g = nd?.goods;
          const win = g?.window ?? prof?.window ?? null;
          return (
            <tr key={id} className={s.selectedNode === id ? 'row--sel' : ''}
              onClick={() => s.selectNode(id)}>
              <td>{prof?.name ?? nd?.name ?? nodeLabel(id, s.graph)}</td>
              <td>{g
                ? (g.state === 'ABSENT'
                  ? <span className="src-tag mono" data-fig-class="ABSENT">Absent</span>
                  : <span className="src-tag mono" data-fig-class={goodsChipClass(g)}>{g.figure_class}</span>)
                : <span className="dim">{nd === null ? 'Not served' : 'Loading'}</span>}</td>
              <td className="small">{mixSummary(nd)}</td>
              <td className="mono small">{win ? `${win.start} → ${win.end}` : '—'}</td>
            </tr>
          );
        })}</tbody>
      </table>
      {run.places_affected > rowIds.length && (
        <p className="fineprint">First {rowIds.length} affected places</p>)}
      <p className="fineprint">Air and ground places · Absent</p>
    </>
  );
}

function ExposureSection({ run, standalone }: { run: ImpactRun | null; standalone: { iso3: string; data: ImpactExposure } | { iso3: string; error: true } | null }) {
  const s = useStore();
  if (run) {
    const ee = run.economy_exposure;
    const cov = ee.tier2_coverage;
    const tier2 = [...ee.tier2_trade_share].sort((a, b) => (b.share.value ?? 0) - (a.share.value ?? 0));
    return (
      <>
        <h5>Economy exposure</h5>
        <p className="fineprint">Tier 1 · Port share of national tonnage · PortWatch AIS estimate</p>
        <table className="data-table">
          <thead><tr><th>Country</th><th>Affected places</th><th className="num">Affected (t)</th><th className="num">National (t)</th><th className="num">Share</th></tr></thead>
          <tbody>{ee.tier1_port_share.map((row) => (
            <tr key={row.iso3} title={deriv(row.share.derivation, s.graph) || undefined}>
              {/* Share-only rows serve no country name: the served ISO
                  code names the country rather than a blank cell. */}
              <td>{row.country ?? row.iso3}</td>
              <td className="small">{row.places.map((id) => nodeLabel(id, s.graph)).join(' · ')}</td>
              <td className="mono num"><FigNum f={row.affected_tonnage} fmt={fmtInt} what="Affected tonnage" scope={row.country ?? row.iso3} runId={run?.run_id} unit="t" /></td>
              <td className="mono num"><FigNum f={row.national_tonnage} fmt={fmtInt} what="National tonnage" scope={row.country ?? row.iso3} runId={run?.run_id} unit="t" /></td>
              <td className="mono num amber">{row.share.value != null ? <FigPct f={row.share} what="Share of national tonnage" scope={row.country ?? row.iso3} runId={run?.run_id} /> : '—'} <FigChip f={row.share} /></td>
            </tr>
          ))}</tbody>
        </table>
        {ee.tier1_port_share.length === 0 && <p className="dim">No country share</p>}
        <p className="fineprint">Tier 2 · Structural · UN Comtrade {tier2[0]?.period ?? ''} · routing sets {cov.routing_sets_version}
          {cov.extracts_retrieved_at
            ? <> · extracts {fmtDay(cov.extracts_retrieved_at)} · {(cov.extracts_empty ?? []).length} pairs absent</>
            : <> · No extracts</>}
          {cov.affected_without_sets.length > 0 && <> · No routing set · {cov.affected_without_sets.map((id) => nodeLabel(id, s.graph)).join(' · ')}</>}</p>
        <table className="data-table">
          <thead><tr><th>Reporter</th><th>Good</th><th>Basis</th><th className="num">Share via affected</th><th>Via</th></tr></thead>
          <tbody>{tier2.slice(0, 12).map((row, i) => (
            <tr key={`${row.reporter_iso3}-${row.good}-${i}`} title={deriv(row.share.derivation, s.graph) || undefined}>
              <td>{row.reporter}</td>
              <td className="small">{row.good_label}</td>
              <td className="small">{row.basis} · {row.period}</td>
              <td className="mono num amber">{row.share.value != null ? pct(row.share.value) : '—'}</td>
              <td className="small">{row.via.join(' · ')}</td>
            </tr>
          ))}</tbody>
        </table>
        {tier2.length > 12 && <p className="fineprint">+{tier2.length - 12} more rows in the export</p>}
        {tier2.length === 0 && <p className="dim">No tier 2 rows</p>}
      </>
    );
  }
  if (!standalone) {
    return (
      <>
        <h5>Economy exposure</h5>
        <p className="dim">Select a place</p>
      </>
    );
  }
  if ('error' in standalone) {
    return (
      <>
        <h5>Economy exposure</h5>
        <p className="honest">Unreachable · Exposure</p>
      </>
    );
  }
  const d = standalone.data;
  return (
    <>
      <h5>Economy exposure · {d.country}</h5>
      <p className="fineprint">Tier 1 · Structural · {d.window.start} to {d.window.end} · {d.country_tonnage.ports_in_layer} ports in layer · {d.country_tonnage.ports_in_build} in network</p>
      <dl className="kv">
        <dt>Imports (window)</dt>
        <dd className="mono"><FigNum f={d.country_tonnage.imports} fmt={fmtInt} what="Imports in window" scope={d.iso3} unit="t" /> t <FigChip f={d.country_tonnage.imports} /></dd>
        <dt>Exports (window)</dt>
        <dd className="mono"><FigNum f={d.country_tonnage.exports} fmt={fmtInt} what="Exports in window" scope={d.iso3} unit="t" /> t <FigChip f={d.country_tonnage.exports} /></dd>
      </dl>
      <table className="data-table">
        <thead><tr><th>Port</th><th className="num">Tonnage (t)</th><th className="num">Share of national</th></tr></thead>
        <tbody>{d.ports.map((p) => (
          <tr key={p.node_id} className={s.selectedNode === p.node_id ? 'row--sel' : ''}
            onClick={() => s.selectNode(p.node_id)} title={deriv(p.share.derivation, s.graph) || undefined}>
            <td>{p.name}</td>
            <td className="mono num"><FigNum f={p.tonnage} fmt={fmtInt} what="Port tonnage" scope={p.name} unit="t" /></td>
            <td className="mono num amber">{p.share.value != null ? <FigPct f={p.share} what="Share of national tonnage" scope={p.name} /> : '—'}</td>
          </tr>
        ))}</tbody>
      </table>
      <p className="fineprint">Tier 2: <FigChip f={d.tier2} /></p>
    </>
  );
}

function obsValue(o: ImpactAnalogue['observations'][number]): string {
  if (o.value_usd_per_40ft != null) return `${fmtUsd(o.value_usd_per_40ft)}/40ft`;
  if (o.value_usd_per_feu != null) return `${fmtUsd(o.value_usd_per_feu)}/FEU`;
  if (o.value_usd_per_day != null) return `${fmtUsd(o.value_usd_per_day)}/day`;
  return '—';
}

function RateSection({ run, rates, analogues }:
  { run: ImpactRun | null; rates: ImpactRates | null; analogues: ImpactAnalogue[] | null }) {
  const s = useStore();
  const wl = run?.economy_exposure.watchlist;
  return (
    <>
      <h5>Rate context</h5>
      {rates ? (
        <>
          <p className="imp-unit-head"><b>Observed index values</b>{rates.citations.length > 0 && <span className="dim"> · cited</span>}
            {' '}<span className="src-tag mono">{rates.citations.length > 0 ? rates.state : 'Not served'}</span></p>
          {(rates.index_links ?? []).map((l) => (
            <p key={l.url} className="fineprint">{l.index} · <SafeLink href={l.url}>{l.publisher}</SafeLink></p>
          ))}
          {rates.citations.map((c, i) => (
            <div key={i} className="imp-cite">
              <p className="mono small">
                <b className="amber">{c.value.value != null ? fmtUsd(c.value.value) : '—'}</b>
                <span className="dim"> {c.value.unit ?? ''}</span>
                {c.stale && <span className="src-tag mono">{c.stale_stamp ?? 'Stale'}</span>}
              </p>
              <p className="small">{c.index}{c.route ? ` · ${c.route}` : ''} · {c.value.as_of ? fmtDay(c.value.as_of) : '—'}
                {c.age_days != null ? ` · ${c.age_days} days old` : ''}</p>
              <p className="fineprint">Reported by <SafeLink href={c.url}>{c.publisher}</SafeLink>
                {c.corroborating_publisher && c.corroborating_url && <> · corroborated by <SafeLink href={c.corroborating_url}>{c.corroborating_publisher}</SafeLink></>}
                {' '}<FigChip f={c.value} /></p>
            </div>
          ))}
          {Object.entries(rates.licence_states).map(([k]) => (
            <p key={k} className="fineprint"><b>{k === 'drewry_wci' ? 'Drewry WCI' : k === 'freightos_fbx' ? 'Freightos FBX' : k === 'scfi' ? 'SCFI' : k}</b> <Status state="notserved" word="Needs licence" /></p>
          ))}
        </>
      ) : <p className="dim">Not served · Rate citations</p>}
      <p className="imp-unit-head"><b>Historical analogues</b>
        {' '}<span className="src-tag mono" data-fig-class="HISTORICAL">Historical</span></p>
      {(analogues ?? []).map((a) => (
        <article key={a.id} className="imp-analogue">
          <p><b>{a.title}</b><span className="dim small"> · {a.episode_dates.start} → {a.episode_dates.end} · {a.channels.map((c) => displayValue(c)).join(' · ')}</span></p>
          <table className="data-table">
            <thead><tr><th>Series</th><th>Date</th><th className="num">Observed</th></tr></thead>
            <tbody>{a.observations.map((o, i) => (
              <tr key={i}><td className="small">{o.series}</td><td className="mono small">{o.as_of}</td>
                <td className="mono num amber">{obsValue(o)}</td></tr>
            ))}</tbody>
          </table>
          {a.observations.some((o) => o.note) && (
            <details className="method-disclosure" data-methodology><summary className="mono">Source notes</summary>
              {a.observations.map((o, i) => o.note ? <p key={i} className="fineprint">{o.series} · {o.as_of}: {humaniseProse(o.note, s.graph)} <span className="dim">({o.source})</span></p> : null)}
            </details>)}
        </article>
      ))}
      {analogues && analogues.length === 0 && <p className="dim">No analogues</p>}
      <p className="imp-unit-head"><b>Watchlist</b></p>
      {run && wl ? (
        <>
          <p className="fineprint">Threshold ≥ {pct(wl.threshold)} of reference flow</p>
          <table className="data-table">
            <thead><tr><th>Reporter</th><th>Good</th><th className="num">Exposed share</th><th>Via</th></tr></thead>
            <tbody>{wl.entries.map((e, i) => (
              <tr key={`${e.reporter_iso3}-${e.good}-${i}`} title={deriv(e.share.derivation, s.graph) || undefined}>
                <td>{e.reporter}</td><td className="small">{e.good_label}</td>
                <td className="mono num amber">{e.share.value != null ? pct(e.share.value) : '—'}</td>
                <td className="small">{e.via.join(' · ')}</td>
              </tr>
            ))}</tbody>
          </table>
          {wl.entries.length === 0 && <p className="dim">No good clears {pct(wl.threshold)}</p>}
        </>
      ) : (
        <p className="dim">No run loaded</p>
      )}
    </>
  );
}

function ImpactMethod() {
  return (
    <MethodDisclosure data-methodology>
      <p className="fineprint">Volume at risk: capacity times worst affected share, per unit class.</p>
      <p className="fineprint">Goods mix: vessel-class shares of trailing 30-day tonnage.</p>
      <p className="fineprint">Tier 2: modelled routing sets; share basis as printed per row.</p>
    </MethodDisclosure>
  );
}

/* ── The panel ──────────────────────────────────────────────────────── */

export function ImpactPanel() {
  const s = useStore();
  const runId = s.chain?.run_id ?? null;
  useEffect(() => { if (runId) void s.fetchImpactRun(runId); /* eslint-disable-next-line */ }, [runId]);
  const run = s.impactRun && runId && s.impactRun.run_id === runId ? s.impactRun : null;
  // Standalone structural fetches (rates + analogues serve with or
  // without a run; the run payload embeds the same blocks when loaded).
  const [ratesDoc, setRatesDoc] = useState<ImpactRates | null>(null);
  const [anaDoc, setAnaDoc] = useState<{ matching_rule: string; entries: ImpactAnalogue[] } | null>(null);
  useEffect(() => {
    let dead = false;
    api.impactRates().then((d) => { if (!dead) setRatesDoc(d); }).catch(() => { /* the section renders its unavailable line */ });
    api.impactAnalogues().then((d) => { if (!dead) setAnaDoc({ matching_rule: d.matching_rule, entries: d.entries }); }).catch(() => { /* same */ });
    return () => { dead = true; };
  }, []);
  // No-run economy exposure: the selected place's country structure.
  const selNode = s.selectedNode;
  useEffect(() => {
    if (!runId && selNode && s.impactNodeData?.node_id !== selNode) void s.fetchImpactNode(selNode);
    /* eslint-disable-next-line */
  }, [runId, selNode, s.impactNodeData?.node_id]);
  const iso3 = !run ? (s.impactNodeData?.country.iso3 ?? null) : null;
  const [expState, setExpState] = useState<{ iso3: string; data: ImpactExposure } | { iso3: string; error: true } | null>(null);
  useEffect(() => {
    if (!iso3) { setExpState(null); return; }
    let dead = false;
    api.impactExposure(iso3)
      .then((d) => { if (!dead) setExpState({ iso3, data: d }); })
      .catch(() => { if (!dead) setExpState({ iso3, error: true }); });
    return () => { dead = true; };
  }, [iso3]);
  const rates = run?.price_context.rates ?? ratesDoc;
  const analogues = run?.price_context.analogues ?? anaDoc?.entries ?? null;
  const updated = run ? feedStamp(adapterFeedState(s.health, FEED_ADAPTERS.portwatchDaily, s.snapshotMode), 'IMF PortWatch daily')
    : runId && s.impactRunSource === 'unreachable' ? 'Unreachable'
    : runId ? 'Loading'
    : 'No run loaded';
  /* Provenance stamps come from served records only: the PortWatch daily layer's own last
   * successful retrieval from the health board; the analogues carry no retrieved_at, so none is stamped. */
  const pwDaily = (s.health?.adapters ?? []).find((a) => a.adapter.toLowerCase().includes('portwatch') && a.adapter.toLowerCase().includes('daily'));
  const pwRetrieved = pwDaily?.last_success_at ?? undefined;
  const standalone = expState && iso3 && expState.iso3 === iso3 ? expState : null;
  return (
    <Panel id="impact" code="IMP" title="Impact — Volume, goods & exposure" updated={updated}
      exportSpec={{
        source: 'IMF PortWatch; UN Comtrade (derived shares)',
        json: () => ({ run, rates, exposure: standalone && 'data' in standalone ? standalone.data : null }),
        pack: () => ({
          data: { run, rates, exposure: standalone && 'data' in standalone ? standalone.data : null },
          /* the impact-run payload serves no retrieved_at (its
           * figures carry as_of, and the extracts entry below carries
           * the served extracts_retrieved_at) — the field is OMITTED
           * rather than stamped with export time; the exporter's
           * exported_at is the honest export stamp. The remaining
           * entries keep their served / pinned-snapshot stamps.
           * ExportSpec (components/Panel.tsx) still types
           * retrieved_at as required, so the array is cast through at
           * this boundary. */
          provenance: [
            { file: 'impact-run.json', source: 'SupermAP impact derivation (chain engine × pinned snapshots)', mode: s.runMeta ? runWord(s.runMeta, s.health, s.snapshotMode, Date.now()) : 'MODELLED' },
            { file: 'portwatch_daily.json', source: 'IMF PortWatch daily layers (AIS-based estimates, snapshot)', ...(pwRetrieved ? { retrieved_at: pwRetrieved } : {}), mode: 'SNAPSHOT' },
            ...(run?.economy_exposure.tier2_coverage.extracts_retrieved_at ? [{ file: 'comtrade_aggregates.json', source: 'UN Comtrade (derived shares, cached at build)', retrieved_at: run.economy_exposure.tier2_coverage.extracts_retrieved_at, mode: 'SNAPSHOT' }] : []),
            { file: 'analogues.json', source: 'SupermAP impact analogues — HISTORICAL, cited episodes', mode: 'SNAPSHOT' },
          ] as unknown as Array<{ file: string; source: string; retrieved_at: string; mode: string }>,
        }),
      }}>
      {runId && s.impactRunSource === 'unreachable' && (
        <p className="honest">Unreachable · Impact</p>)}
      {run && (
        <>
          <p className="fineprint">{runRef(s.chain, s.graph)} · {run.seed.name} ({prettyKind(run.seed.kind)}) · {run.places_affected} places affected</p>
          <VolumeSection run={run} />
          <hr className="rule" />
          <GoodsSection run={run} />
          <hr className="rule" />
        </>
      )}
      {!runId && (
        <>
          <p className="honest"><b>No run loaded</b></p>
          <hr className="rule" />
        </>
      )}
      {runId && !run && s.impactRunSource !== 'unreachable' && (
        <p className="honest">Loading · Impact</p>)}
      <ExposureSection run={run} standalone={standalone} />
      <hr className="rule" />
      <RateSection run={run} rates={rates} analogues={analogues} />
      <hr className="rule" />
      <SourceLine ids={['imf-portwatch', 'un-comtrade']} />
      <ImpactMethod />
    </Panel>
  );
}
