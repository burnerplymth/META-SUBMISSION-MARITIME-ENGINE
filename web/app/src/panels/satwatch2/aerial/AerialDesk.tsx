import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Chip, MetaChip } from '../../reports/chips';
import { CentreError } from '../../reports/client';
import { AiBadge } from '../../../meta/AiBadge';
import { recordAiCall } from '../../../meta/aiCalls';
import { noteMetaCall } from '../../../meta/aiStatus';
import { bboxRect, ringsToPath } from '../model';
import { API_BASE, fetchNative, fetchPlan, fetchSources, hasToken, imageSrc, runLive, runRecorded } from './aerialClient';
import {
  acceptRecorded, confirmWord, exportHref, liveCallEntries, runStateChip, usd, type ResState,
  sceneForPlace, failureWord, foundChips, foundDid, imageChip, lengthWord, objectRows, scoreWord, sourceWord, sourcesWithScenes, VERIFICATION_WORD,
  type AerialPlan, type AerialResult, type AerialScene, type AerialSource, type ScenesDoc,
} from './aerialModel';
import './aerial.css';

/* AERIAL DESK: SAM 3.1 on sub-metre aerial survey scenes of ports. Chips and state words only.
 * Detections of ONE dated scene, "SAM found N", never a count. The picture is the stored native crop when the
 * operator fetched it, otherwise a reduced preview (labelled); detections were always computed on native pixels.
 * A recorded result is a stored real reply (free, public); a live run needs the operator token and a
 * confirmation of the estimate. Nothing here feeds signals. */

type Feed<T> = { s: 'loading' } | { s: 'ok'; v: T } | { s: 'err'; code: string };
const codeOf = (e: unknown): string => (e instanceof CentreError ? e.code : 'error');

function Layer({ result, rows, hot, onHot }: { result: AerialResult; rows: ReturnType<typeof objectRows>; hot: string | null; onHot: (id: string | null) => void }) {
  return (
    <>
      <svg className="sw2-ov sw2-av-ov" viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true" focusable="false">
        {rows.map((o) => {
          const r = bboxRect(o.box);
          const cls = `${hot === o.id ? ' hot' : ''} sw2-av-${o.phrase === 'crane' ? 'crane' : o.phrase === 'container stack' ? 'stack' : 'ship'}`;
          return (
            <g key={o.id} onMouseEnter={() => onHot(o.id)} onMouseLeave={() => onHot(null)}>
              {o.rings && o.rings.length > 0 && <path className={`sw2-sam-mask${cls}`} d={ringsToPath(o.rings)} />}
              <rect className={`sw2-sam-bx${cls}`} x={r.x} y={r.y} width={r.w} height={r.h} />
              <title>{`${o.cls} · ${lengthWord(o)} · ${scoreWord(o.score)}`}</title>
            </g>
          );
        })}
      </svg>
      {rows.map((o) => (
        <span key={`t${o.id}`} className={`sw2-sam-tag${hot === o.id ? ' hot' : ''}`} style={{ left: `${o.box[0] / 10}%`, top: `${o.box[1] / 10}%` }} aria-hidden="true">{o.index}</span>
      ))}
      <span className="sw2-av-size" aria-hidden="true">{`${result.size_px[0]} × ${result.size_px[1]} px`}</span>
    </>
  );
}

export function AerialDesk({ doc, place, onRetry }: { doc: Feed<ScenesDoc>; place: string | null; onRetry?: () => void }) {
  const [sel, setSel] = useState<string | null>(null);
  const [sources, setSources] = useState<AerialSource[]>([]);
  const [srcSel, setSrcSel] = useState<string>('all');
  const [shown, setShown] = useState<ReadonlySet<string>>(new Set());
  const [res, setRes] = useState<ResState>({ s: 'idle' });
  const [plan, setPlan] = useState<Feed<AerialPlan>>({ s: 'loading' });
  const [hot, setHot] = useState<string | null>(null);
  const [pending, setPending] = useState<AerialPlan | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [planTick, setPlanTick] = useState(0);   // plan refresh only: a live result is never re-replayed
  const runSeq = useRef(0);
  const operator = hasToken();
  const confirmRef = useRef<HTMLDivElement>(null);

  /* The scene follows the anchorage: its own scene when it has one; otherwise none (a Not scanned chip), and the scene tabs stay for browsing. */
  useEffect(() => {
    if (doc.s !== 'ok') return;
    setSel(sceneForPlace(doc.v.scenes, place)?.scene_id ?? null);
    setSrcSel('all');
  }, [doc, place]);

  useEffect(() => {                                    // the registry only labels and filters: a down registry changes nothing else
    const ctrl = new AbortController();
    fetchSources(ctrl.signal).then(setSources).catch(() => { if (!ctrl.signal.aborted) setSources([]); });
    return () => ctrl.abort();
  }, []);

  const srcOf = (id: string): AerialSource | undefined => sources.find((s) => s.id === id);
  const srcTabs = doc.s === 'ok' ? sourcesWithScenes(sources, doc.v.scenes) : [];
  const listed = doc.s === 'ok' ? doc.v.scenes.filter((x) => srcSel === 'all' || x.source === srcSel) : [];
  const pickSource = (id: string) => {
    setSrcSel(id);
    if (doc.s !== 'ok') return;
    const inSrc = doc.v.scenes.filter((x) => id === 'all' || x.source === id);
    if (!inSrc.some((x) => x.scene_id === sel)) setSel((inSrc.find((x) => x.recorded_phrases.length > 0) ?? inSrc[0])?.scene_id ?? null);
  };

  const scene: AerialScene | null = doc.s === 'ok' ? doc.v.scenes.find((x) => x.scene_id === sel) ?? null : null;
  const labelOf = useCallback((p: string): string => (doc.s === 'ok' ? doc.v.phrases.find((x) => x.phrase === p)?.label : undefined) ?? p, [doc]);
  const wanted = useMemo(() => {
    if (doc.s !== 'ok' || !scene) return [] as string[];
    const rec = new Set(scene.recorded_phrases);
    const def = doc.v.default_phrases.filter((p) => rec.has(p));
    return def.length ? def : scene.recorded_phrases.slice(0, 2);
  }, [doc, scene]);

  useEffect(() => {                                    // plan and usage follow the scene and every spend
    if (!scene) return;
    const ctrl = new AbortController();
    setPending(null); setHot(null);
    fetchPlan(scene.scene_id, wanted.length ? wanted : ['ship'], ctrl.signal).then((v) => setPlan({ s: 'ok', v })).catch((e) => { if (!ctrl.signal.aborted) setPlan({ s: 'err', code: codeOf(e) }); });
    return () => ctrl.abort();
  }, [scene?.scene_id, wanted.join('|'), planTick]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {                                    // the stored replay: only on a scene change, never after a live run
    if (!scene) return;
    const ctrl = new AbortController();
    if (wanted.length === 0) { setRes({ s: 'err', code: 'not_recorded' }); return () => ctrl.abort(); }
    setRes({ s: 'loading' });
    runRecorded(scene.scene_id, wanted, ctrl.signal).then((v) => { setRes((cur) => acceptRecorded(cur, v)); setShown(new Set(v.phrases)); })
      .catch((e) => { if (!ctrl.signal.aborted) setRes({ s: 'err', code: codeOf(e) }); });
    return () => ctrl.abort();
  }, [scene?.scene_id, wanted.join('|')]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { confirmRef.current?.querySelector<HTMLButtonElement>('button[data-confirm]')?.focus(); }, [pending]);

  const result = res.s === 'ok' ? res.v : null;
  const rows = useMemo(() => (result ? objectRows(result, shown) : []), [result, shown]);
  const toggle = (p: string) => setShown((cur) => { const n = new Set(cur); if (n.has(p)) n.delete(p); else n.add(p); return n; });

  const doFetch = () => {
    if (!scene) return;
    setBusy('fetch');
    fetchNative(scene.scene_id).then(() => setPlanTick((t) => t + 1)).catch((e) => setRes({ s: 'err', code: codeOf(e) })).finally(() => setBusy(null));
  };
  const doLive = () => {
    const p = pending; setPending(null);
    if (!p || !scene) return;
    setBusy('live'); setRes({ s: 'loading' });
    runLive(scene.scene_id, wanted, p.est_cost_usd).then((v) => {
      setRes({ s: 'ok', v }); setShown(new Set(v.phrases)); setPlanTick((t) => t + 1);
      const run = `${scene.scene_id}:${Date.now()}:${++runSeq.current}`;
      for (const e of liveCallEntries(v, run)) recordAiCall({ surface: 'sam', did: e.did, provenance: e.provenance, key: e.key });
      noteMetaCall();
    })
      .catch((e) => setRes({ s: 'err', code: codeOf(e) })).finally(() => setBusy(null));
  };

  if (doc.s === 'loading') return null;
  if (doc.s === 'err') {
    const f = failureWord(doc.code);
    return <section className="sw2-ai sw2-av" aria-label="Aerial scenes"><div className="sw2-row"><span className="rk-lbl">Aerial scenes</span><Chip chip={f.chip} word={f.word} title={doc.code} /></div></section>;
  }
  const p = plan.s === 'ok' ? plan.v : null;
  const liveReady = !!p && p.live_state === 'READY';

  return (
    <section className="sw2-ai sw2-av" aria-label="Aerial scenes" data-aerial-scene={sel ?? ''}>
      <div className="sw2-row">
        <span className="rk-lbl">Aerial scenes</span>
        <Chip chip="MODELLED" word="Modelled" title="Not ground truth" />
        <MetaChip>{doc.v.framing}</MetaChip>
        {srcTabs.length > 1 && (
          <span className="rk-tabs" role="group" aria-label="Aerial source">
            <button type="button" className={`rk-tab${srcSel === 'all' ? ' on' : ''}`} aria-pressed={srcSel === 'all'} onClick={() => pickSource('all')}>All</button>
            {srcTabs.map((s) => (
              <button key={s.id} type="button" className={`rk-tab${srcSel === s.id ? ' on' : ''}`} aria-pressed={srcSel === s.id} data-source={s.id}
                title={`${s.coverage}${s.year ? ` · ${s.year}` : ''} · ${s.licence}`} onClick={() => pickSource(s.id)}>{sourceWord(s)}</button>
            ))}
          </span>
        )}
        <span className="rk-tabs" role="group" aria-label="Scene">
          {listed.map((s) => (
            <button key={s.scene_id} type="button" className={`rk-tab${sel === s.scene_id ? ' on' : ''}`} aria-pressed={sel === s.scene_id} onClick={() => setSel(s.scene_id)}>{s.name}</button>
          ))}
        </span>
      </div>

      {!scene && <div className="sw2-row" data-aerial-state="none"><Chip chip="NOT SERVED" word="Not scanned" /></div>}

      {scene && (
        <div className="sw2-ai-grid">
          <div className="sw2-ai-crop">
            <div className="sw2-row sw2-av-badges">
              <Chip chip="SNAPSHOT" word={scene.badge} title={scene.acquisition_label ?? undefined} />
              {scene.acquisition_label && !scene.badge.includes(', ') && <MetaChip>{scene.acquisition_label}</MetaChip>}
              {(() => { const c = imageChip(scene.image); return <Chip chip={c.chip} word={c.word} title={c.title} />; })()}
              {srcOf(scene.source) && <MetaChip title={`${srcOf(scene.source)!.coverage} · ${srcOf(scene.source)!.licence}`}>{sourceWord(srcOf(scene.source)!)}</MetaChip>}
              {scene.attribution && <MetaChip title={`${scene.attribution.text} · ${scene.attribution.licence}`}>{scene.attribution.chip}</MetaChip>}
            </div>
            {scene.attribution && <p className="fineprint mono src-credit" data-aerial-credit>{scene.attribution.text}</p>}
            <div className="sw2-frame sw2-ai-frame sw2-av-frame" style={{ aspectRatio: scene.size_px ? `${scene.size_px[0]} / ${scene.size_px[1]}` : '1 / 1' }} data-native={scene.image === 'native'}>
              {scene.image && <img src={imageSrc(scene.scene_id)} alt={`${scene.name}, ${scene.badge}`} loading="lazy" />}
              {result && <Layer result={result} rows={rows} hot={hot} onHot={setHot} />}
            </div>
          </div>

          <div className="sw2-ai-side">
            <div className="sw2-ai-block" data-block="aerial-sam">
              <div className="sw2-row">
                <b className="sw2-ai-h">SAM 3.1</b>
                {res.s === 'loading' && <Chip chip="INFO" word="Loading" />}
                {res.s === 'err' && (() => { const f = failureWord(res.code); return <Chip chip={f.chip} word={f.word} title={res.code} />; })()}
                {result && <Chip chip={result.mode === 'LIVE' ? 'FLAG' : 'SNAPSHOT'} word={result.mode === 'LIVE' ? 'Live' : 'Recorded'} title={result.mode === 'LIVE' ? 'Call made now' : 'Stored real reply'} />}
                {result && result.mode === 'LIVE' && <MetaChip title="Model">{result.model ?? 'sam-3.1'}</MetaChip>}
                {result && result.mode === 'LIVE' && <MetaChip title="Estimated cost">{`${usd(result.cost_usd)} · ${result.images} ${result.images === 1 ? 'call' : 'calls'}`}</MetaChip>}
                {result && result.tile_count > 1 && <MetaChip>{`${result.tile_count} tiles`}</MetaChip>}
                {result && result.skipped_tiles > 0 && <Chip chip="NOT SERVED" word={`${result.skipped_tiles} no-data tiles skipped`} />}
                {result && <AiBadge provenance={{ source: 'Meta Model API / sam-3.1', mode: 'MODELED', retrieved_at: '' }} did={foundDid(result)} />}
              </div>

              {result && (
                <div className="sw2-row sw2-av-found" role="group" aria-label="SAM found">
                  {foundChips(result, labelOf).map((c) => (
                    <button key={c.phrase} type="button" className={`rk-tab sw2-found-chip${shown.has(c.phrase) ? ' on' : ''}`} aria-pressed={shown.has(c.phrase)} onClick={() => toggle(c.phrase)} data-phrase={c.phrase}>
                      <span className="sw2-found">{c.word}</span>
                    </button>
                  ))}
                  <Chip chip="FLAG" word={VERIFICATION_WORD} title="Not reviewed" />
                  {result.missing.length > 0 && <Chip chip="NOT SERVED" word={`No recorded reply · ${result.missing.map((m) => labelOf(m).toLowerCase()).join(', ')}`} />}
                </div>
              )}

              {rows.length > 0 && (
                <table className="rk-table sw2-av-table" aria-label="SAM objects">
                  <thead><tr><th scope="col">#</th><th scope="col">Class</th><th scope="col">Length</th><th scope="col">Score</th></tr></thead>
                  <tbody>
                    {rows.map((o) => (
                      <tr key={o.id} className={hot === o.id ? 'on' : ''} onMouseEnter={() => setHot(o.id)} onMouseLeave={() => setHot(null)} data-object={o.id}>
                        <td className="mono">{o.index}</td>
                        <td>{o.cls}{o.stitched ? ' · stitched' : ''}</td>
                        <td className="mono" title={o.floor ? 'Scene border floor' : o.basis === 'mask' ? 'From the outline' : 'From the box'}>{lengthWord(o)}</td>
                        <td className="mono">{scoreWord(o.score)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {result && rows.length === 0 && shown.size > 0 && <div className="sw2-row"><Chip chip="INFO" word="SAM found none" /></div>}

              <div className="sw2-row sw2-av-actions">
                {result && (
                  <>
                    <a className="rk-btn" href={exportHref(API_BASE, scene.scene_id, [...shown], 'geojson')} download>Export GeoJSON</a>
                    <a className="rk-btn" href={exportHref(API_BASE, scene.scene_id, [...shown], 'csv')} download>Export CSV</a>
                  </>
                )}
                {operator && scene.image !== 'native' && !p?.crop_stored && <button type="button" className="rk-btn" onClick={doFetch} disabled={busy !== null}>Fetch native crop</button>}
                {operator && p && (
                  <button type="button" className="rk-btn rk-btn--primary" disabled={!liveReady || busy !== null || pending !== null || p.cap_reached || p.disabled} onClick={() => setPending(p)}>Run SAM</button>
                )}
                {p && (() => { const c = runStateChip({ operator, cropStored: liveReady, capReached: p.cap_reached, disabled: p.disabled }); return <Chip chip={c.chip} word={c.word} />; })()}
                {p && liveReady && <MetaChip>{confirmWord(p)}</MetaChip>}
              </div>
              {pending && (
                <div ref={confirmRef} className="sw2-confirm" role="alertdialog" aria-label="Confirm SAM run" data-confirm-kind="aerial"
                  onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); setPending(null); } }}>
                  <Chip chip="FLAG" word="Spends credits" />
                  <MetaChip title="Not billed yet">{confirmWord(pending)}</MetaChip>
                  {pending.remaining_calls != null && <MetaChip>{`${pending.remaining_calls} calls left today`}</MetaChip>}
                  <button type="button" className="rk-btn rk-btn--primary" data-confirm onClick={doLive}>Confirm SAM run</button>
                  <button type="button" className="rk-btn" onClick={() => setPending(null)}>Cancel</button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
