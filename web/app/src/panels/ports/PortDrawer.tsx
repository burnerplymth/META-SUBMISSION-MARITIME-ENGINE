import { failureSentence } from '../failureText';
import React, { useEffect, useMemo, useRef } from 'react';
import { CellBox, MetaTag, ProvChip } from './chips';
import { Citation } from '../../components/Citation';
import { TIER_WORD, buildDetail, overlayQuery, type DetailRow, type Overlay, type PortDetail } from './model';
import { getJson } from './http';
import { useAsync } from './useAsync';
import { labelWord } from '../../copy';
import { Icon } from '../../ui/Icon';
import { Status, StateMark } from '../../ui/Status';

/* Detail drawer for one place — GET /ports/{place_id}. Every series is a
 * cell with its provenance chip and as-of; a NOT SERVED cell is the
 * hatched chip; a section whose feed is down is not rendered (its name
 * stays as one NOT SERVED chip so the absence is visible, not hidden). */

function Rows({ rows }: { rows: DetailRow[] }) {
  return (
    <div className="pt-kv" role="table">
      {rows.map((r, i) => (
        <div className="pt-kv-row" role="row" key={`${r.label}-${i}`}>
          <span className="pt-kv-label" role="rowheader">{r.label}</span>
          <span className="pt-kv-val" role="cell"><CellBox v={r.cell} />{r.extra ? <small className="pt-kv-extra">{r.extra}</small> : null}</span>
        </div>
      ))}
    </div>
  );
}

export function PortDrawer({ placeId, overlay, tick, onClose, onSelect }: {
  placeId: string; overlay: Overlay; tick: number; onClose: () => void; onSelect: (id: string) => void;
}) {
  const q = overlayQuery(overlay);
  const { data, error, loading } = useAsync<PortDetail>(
    (signal) => getJson<PortDetail>(`/ports/${encodeURIComponent(placeId)}${q ? `?${q}` : ''}`, { signal }),
    `${placeId}|${q}`, true, tick);
  const view = useMemo(() => (data && data.place_id === placeId ? buildDetail(data) : null), [data, placeId]);
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { closeRef.current?.focus({ preventScroll: true }); }, [placeId]);

  return (
    <aside className="pt-drawer" aria-label={`Place detail ${view?.name ?? ''}`.trim()}
      onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } }}>
      <header className="pt-drawer-head">
        <div className="pt-drawer-title">
          <b>{view?.name ?? 'Place'}</b>
        </div>
        <button ref={closeRef} className="pt-btn" onClick={onClose} aria-label="Close place detail">Close <Icon name="close" size="xs" /></button>
      </header>
      <div className="pt-drawer-body">
        {error && view && <p><ProvChip kind="DOWN" label="Detail stale" title="Showing last copy" /></p>}
        {error && !view && <p><ProvChip kind="DOWN" title={failureSentence(error.code, error.status)} /></p>}
        {!view && !error && loading && <p className="mono small dim">Loading</p>}
        {view && (
          <>
            <div className="pt-chips">
              <MetaTag title={view.tierTitle} strong={view.tierWord === TIER_WORD.core}>{view.tierWord}</MetaTag>
              <MetaTag>{view.kind}</MetaTag>
              <MetaTag>{labelWord(view.mode)}</MetaTag>
              {view.region && <MetaTag>{labelWord(view.region)}</MetaTag>}
              {view.overlayLabel && <MetaTag title="Overlay run">{view.overlayLabel}</MetaTag>}
              {loading && <span className="mono small dim">Refreshing</span>}
            </div>
            <section className="pt-sec" aria-label="Status">
              <h5>Status</h5>
              <div className="pt-chips">
                <span className={`pt-status pt-status--${view.status.key}`} title={view.status.title}>
                  <StateMark name={view.status.mark} size={9} />{labelWord(view.status.label)}
                </span>
                <MetaTag title="Published status rule">{view.status.ruleId} v{view.status.ruleVersion}</MetaTag>
              </div>
              {view.basis.length > 0 && (
                <ul className="pt-basis">
                  {view.basis.map((b) => (
                    <li key={`${b.component}${b.metric}`}><MetaTag>{b.component}</MetaTag> <span>{labelWord(b.metric)}</span> <b className="mono">{typeof b.value === 'number' ? b.value : String(b.value)}</b> <span className="mono dim">Level {b.level}</span></li>
                  ))}
                </ul>
              )}
              {view.notEvaluated.length > 0 && (
                <div className="pt-chips">
                  {view.notEvaluated.map((n) => <span key={n.component} className="pv-prov" title={labelWord(n.reason)}><Status state="notserved" word={`${n.component} not evaluated`} /></span>)}
                </div>
              )}
            </section>
            <section className="pt-sec" aria-label="Capacity and cells">
              <h5>Cells</h5>
              <Rows rows={[{ label: 'Capacity', cell: view.capacity }, ...view.cells]} />
            </section>
            {view.events.length > 0 && (
              <section className="pt-sec" aria-label="Active events">
                <h5>Active events <span className="mono dim">{view.events.length}</span></h5>
                <ul className="pt-list">
                  {view.events.map((e) => (
                    <li key={e.id}>
                      <div className="pt-li-top"><span className="mono pt-sev" title="Out of 5">Sev {e.severity ?? '—'}</span><span className="pt-li-head">{e.headline}</span></div>
                      <div className="pt-li-sub"><CellBox v={e.cell} />
                        <span className="mono dim small">{e.source}</span>
                      </div>
                      <Citation c={e.citation} titleMax={72} />
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {view.sections.map((sec) => (
              <details className="pt-sec pt-det" key={sec.id} open={sec.id !== 'goods_composition'}>
                <summary><h5>{sec.title}{sec.meta ? <span className="mono dim"> {sec.meta}</span> : null}{sec.id === 'portwatch' && view.window ? <span className="mono dim"> {view.window}</span> : null}</h5></summary>
                {sec.groups.map((g, i) => (
                  <div key={`${g.title}-${i}`} className="pt-group">
                    {g.title && <div className="pt-group-title mono">{g.title}</div>}
                    <Rows rows={g.rows} />
                  </div>
                ))}
              </details>
            ))}
            {view.legs.length > 0 && (
              <details className="pt-sec pt-det" open>
                <summary><h5>Nearby legs <span className="mono dim">{view.legs.length}</span></h5></summary>
                <ul className="pt-list">
                  {view.legs.map((l) => (
                    <li key={`${l.direction}${l.otherId}`}>
                      <div className="pt-li-top">
                        <span className="mono dim small">{l.direction === 'inbound' ? 'From' : 'To'}</span>
                        <button className="pt-link" onClick={() => onSelect(l.otherId)}>{l.otherName}</button>
                        <MetaTag>{labelWord(l.tier)}</MetaTag>
                        {l.transitD !== null && <span className="mono dim small">{l.transitD} d</span>}
                      </div>
                      <div className="pt-li-sub"><CellBox v={l.share} /><MetaTag title="Leg basis">{labelWord(l.basis)}</MetaTag></div>
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {view.money.length > 0 && (
              <details className="pt-sec pt-det" open>
                <summary><h5>Money exposure <span className="mono dim">{view.money.length}</span></h5></summary>
                <ul className="pt-list">
                  {view.money.map((m, i) => (
                    <li key={`${m.ticker}${i}`}>
                      <div className="pt-li-top"><b className="mono">{m.ticker}</b><span className="pt-li-head">{m.company}</span><MetaTag>{m.kind}</MetaTag></div>
                      {m.excerpt ? <blockquote className="pt-quote">{m.excerpt}</blockquote> : <div className="pt-li-sub"><MetaTag title="Filing excerpt">Excerpt withheld</MetaTag></div>}
                      <div className="pt-li-sub"><CellBox v={m.filing} /><CellBox v={m.price} /></div>
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {view.hidden.length > 0 && (
              <div className="pt-chips pt-hidden" role="group" aria-label="Not served sections">
                {view.hidden.map((h) => <span key={h.id} className="pv-prov" title={labelWord(h.reason)}><Status state="notserved" word={`Not served ${h.title}`} /></span>)}
              </div>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
