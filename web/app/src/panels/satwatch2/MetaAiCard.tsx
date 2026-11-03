import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Chip, MetaChip } from '../reports/chips';
import { CentreError } from '../reports/client';
import { AiBadge } from '../../meta/AiBadge';
import { recordAiCall } from '../../meta/aiCalls';
import {
  cropSrc, fetchSamPlan, fetchVisionPlan, hasOperatorToken, runSam, runVision, type CropQuery,
} from './client';
import {
  aiFailure, foundWord, geomCensus, liveGate, objectChips, qualityWord, samChips, samConfirm, scoreWord, visionConfirm, visionFields,
  type AiFailure, type ConfirmView, type SamPlan, type SamResult, type VisionPlan, type VisionResult,
} from './metaAi';
import { bboxRect, ringsToPath } from './model';

/* META AI on ONE NATIVE crop: SAM 3.1 boxes and mask outlines
 * drawn over the stored crop, and a Muse Spark vision read of the same crop.
 * Chips and state labels; the model's own words appear only as its read.
 *
 * The crop shown here is always the stored native image: the ENHANCED VIEW
 * toggle of the desk never reaches this card and nothing here reads a
 * canvas. A RECORDED result is a stored real reply for this exact crop, shown
 * for free; a LIVE call needs the operator token and a confirmation of the
 * estimate and the remaining daily cap, and only then is anything sent. */

type Run<T> = { s: 'idle' } | { s: 'loading' } | { s: 'ok'; v: T } | { s: 'err'; f: AiFailure; code: string };
type Plan<T> = { s: 'loading' } | { s: 'ok'; v: T } | { s: 'err'; f: AiFailure };
type Pending = { kind: 'sam' | 'vision'; confirm: ConfirmView };

const failOf = (e: unknown): { f: AiFailure; code: string } => {
  const code = (e as CentreError).code ?? 'error';
  return { f: aiFailure(code), code };
};

/** Boxes, mask outlines and an index tag per SAM object, in norm1000 over the crop. */
export function SamLayer({ result, hot, onHot }: { result: SamResult; hot: string | null; onHot: (id: string | null) => void }) {
  return (
    <>
      <svg className="sw2-ov sw2-sam" viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true" focusable="false">
        {result.objects.map((o) => {
          const hit = hot === o.id ? ' hot' : '';
          return (
            <g key={o.id} onMouseEnter={() => onHot(o.id)} onMouseLeave={() => onHot(null)}>
              {o.mask && o.mask.format === 'polygon' && <path className={`sw2-sam-mask${hit}`} d={ringsToPath(o.mask.rings)} />}
              {Array.isArray(o.bbox) && o.bbox.length === 4 && (() => { const r = bboxRect(o.bbox as [number, number, number, number]); return <rect className={`sw2-sam-bx${hit}`} x={r.x} y={r.y} width={r.w} height={r.h} />; })()}
              <title>{`${o.label} · ${scoreWord(o.score)}`}</title>
            </g>
          );
        })}
      </svg>
      {result.objects.map((o, i) => (Array.isArray(o.bbox) && o.bbox.length === 4
        ? <span key={o.id} className={`sw2-sam-tag${hot === o.id ? ' hot' : ''}`} style={{ left: `${o.bbox[0] / 10}%`, top: `${o.bbox[1] / 10}%` }} aria-hidden="true">{i + 1}</span> : null))}
    </>
  );
}

function ConfirmBox({ pending, label, onConfirm, onCancel }: { pending: Pending; label: string; onConfirm: () => void; onCancel: () => void }) {
  const c = pending.confirm;
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { ref.current?.querySelector<HTMLButtonElement>('button[data-confirm]')?.focus(); }, []);
  return (
    <div ref={ref} className="sw2-confirm" role="alertdialog" aria-label={`Confirm ${label}`} data-confirm-kind={pending.kind}
      onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); onCancel(); } }}>
      <Chip chip="FLAG" word="Spends credits" />
      <MetaChip title="Estimate, not billed">{`Est. ${c.est}`}</MetaChip>
      {c.price && <MetaChip>{c.price}</MetaChip>}
      {c.remaining && <MetaChip>{c.remaining}</MetaChip>}
      <button type="button" className="rk-btn rk-btn--primary" data-confirm onClick={onConfirm}>{`Confirm ${label}`}</button>
      <button type="button" className="rk-btn" onClick={onCancel}>Cancel</button>
    </div>
  );
}

/** The Muse Spark read: chips and labelled fields only. The Meta AI badge sits
 *  in the chip row; the model's own words (scene sentence, full phrases) sit in
 *  a closed Read disclosure. */
export function VisionReadView({ read: res }: { read: VisionResult }) {
  const read = res.read;
  const live = res.mode === 'LIVE' && res.provenance.transport === 'LIVE' && res.provenance.live === true && res.provenance.transport_raw !== 'fixture';
  const fields = visionFields(read);
  const full: Array<{ key: string; label: string; items: string[] }> = [
    { key: 'features', label: 'Visible features', items: read.visible_features.map((f) => (f.detail ? `${f.label}: ${f.detail}` : f.label)) },
    { key: 'cues', label: 'Congestion cues', items: read.congestion_cues },
    { key: 'cannot', label: 'Cannot see', items: read.cannot_see },
    { key: 'limits', label: 'Limits', items: read.limits },
  ];
  return (
    <div className="sw2-ai-result sw2-read" data-vision-mode={res.mode}>
      <div className="sw2-row">
        <Chip chip="MODELLED" word="Modelled" />
        <Chip chip={live ? 'LIVE' : 'SNAPSHOT'} word={live ? 'Live' : 'Recorded'} title={live ? 'Call made now' : 'Stored reply'} />
        <AiBadge provenance={res.provenance} did="Read 1 crop" />
        <MetaChip>{`Quality ${qualityWord(read.image_quality)}`}</MetaChip>
        <MetaChip title="Stated by model">{`Score ${read.confidence.toFixed(2)}`}</MetaChip>
      </div>
      <div className="sw2-fields">
        <div className="sw2-field" data-field="level">
          <span className="rk-lbl">Level</span>
          <MetaChip>{qualityWord(read.congestion_level)}</MetaChip>
        </div>
        {fields.filter((f) => f.chips.length > 0).map((f) => (
          <div key={f.key} className="sw2-field" data-field={f.key} role="group" aria-label={f.label}>
            <span className="rk-lbl">{f.label}</span>
            {f.chips.map((c) => <MetaChip key={c.text + (c.full ?? '')} title={c.full ?? undefined}>{c.text}</MetaChip>)}
            {f.more > 0 && <MetaChip>{`+${f.more}`}</MetaChip>}
          </div>
        ))}
      </div>
      {read.post_check.length > 0 && <div className="sw2-row">{read.post_check.map((p) => <Chip key={p} chip="FLAG" word="Checked" title={p} />)}</div>}
      <details className="sw2-readbox">
        <summary>Read</summary>
        <p className="sw2-scene">{read.scene.replace(/^./, (c) => c.toUpperCase())}</p>
        {full.filter((f) => f.items.length > 0).map((f) => (
          <div key={f.key} className="sw2-list" role="group" aria-label={f.label}>
            <span className="rk-lbl">{f.label}</span>
            <ul>{f.items.map((t) => <li key={t}><span>{t}</span></li>)}</ul>
          </div>
        ))}
      </details>
    </div>
  );
}

export function MetaAiCard({ source, place, hasNow = true, hasBaseline = false }: { source: 'sentinel-2' | 'naip' | 'landsat'; place: string; hasNow?: boolean; hasBaseline?: boolean }) {
  const [role, setRole] = useState<'now' | 'baseline'>('now');
  const roles = source === 'sentinel-2' ? ([hasNow && 'now', hasBaseline && 'baseline'].filter(Boolean) as ('now' | 'baseline')[]) : [];
  const r: 'now' | 'baseline' | null = source === 'sentinel-2' ? (roles.includes(role) ? role : roles[0] ?? 'now') : null;
  const q: CropQuery = useMemo(() => ({ source, place, role: r }), [source, place, r]);
  const samOffered = source !== 'landsat';

  const [prompt, setPrompt] = useState<string | null>(null);
  const [samPlan, setSamPlan] = useState<Plan<SamPlan>>({ s: 'loading' });
  const [visPlan, setVisPlan] = useState<Plan<VisionPlan>>({ s: 'loading' });
  const [sam, setSam] = useState<Run<SamResult>>({ s: 'idle' });
  const [vis, setVis] = useState<Run<VisionResult>>({ s: 'idle' });
  const [pending, setPending] = useState<Pending | null>(null);
  const [hot, setHot] = useState<string | null>(null);
  const operator = hasOperatorToken();

  useEffect(() => { setPrompt(null); }, [source, place]);
  /* plans follow the crop; a recorded reply for it is replayed at once (free) */
  useEffect(() => {
    const ctrl = new AbortController();
    setSam({ s: 'idle' }); setVis({ s: 'idle' }); setPending(null); setHot(null);
    setSamPlan({ s: 'loading' }); setVisPlan({ s: 'loading' });
    if (samOffered) {
      fetchSamPlan(q, prompt, ctrl.signal).then((p) => {
        setSamPlan({ s: 'ok', v: p });
        if (p.recorded.available) {
          setSam({ s: 'loading' });
          runSam(q, p.prompt, null, ctrl.signal).then((v) => setSam({ s: 'ok', v })).catch((e) => { if ((e as CentreError).code !== 'aborted') setSam({ s: 'err', ...failOf(e) }); });
        }
      }).catch((e) => { if ((e as CentreError).code !== 'aborted') setSamPlan({ s: 'err', f: failOf(e).f }); });
    }
    fetchVisionPlan(q, ctrl.signal).then((p) => {
      setVisPlan({ s: 'ok', v: p });
      if (p.recorded.available) {
        setVis({ s: 'loading' });
        runVision(q, null, ctrl.signal).then((v) => setVis({ s: 'ok', v })).catch((e) => { if ((e as CentreError).code !== 'aborted') setVis({ s: 'err', ...failOf(e) }); });
      }
    }).catch((e) => { if ((e as CentreError).code !== 'aborted') setVisPlan({ s: 'err', f: failOf(e).f }); });
    return () => ctrl.abort();
  }, [q, prompt, samOffered]);

  useEffect(() => {
    if (sam.s === 'ok') recordAiCall({ surface: 'sam', did: foundWord(sam.v.found), provenance: sam.v.provenance, key: `sam:${sam.v.crop_id}:${sam.v.prompt}:${sam.v.mode}:${sam.v.provenance.response_id ?? ''}` });
  }, [sam]);
  useEffect(() => {
    if (vis.s === 'ok') recordAiCall({ surface: 'vision', did: 'Read 1 crop', provenance: vis.v.provenance, key: `vision:${vis.v.crop_id}:${vis.v.mode}:${vis.v.provenance.cost_usd ?? ''}` });
  }, [vis]);

  const replaySam = useCallback(() => {
    if (samPlan.s !== 'ok') return;
    setSam({ s: 'loading' });
    runSam(q, samPlan.v.prompt, null).then((v) => setSam({ s: 'ok', v })).catch((e) => setSam({ s: 'err', ...failOf(e) }));
  }, [q, samPlan]);
  const replayVis = useCallback(() => {
    setVis({ s: 'loading' });
    runVision(q, null).then((v) => setVis({ s: 'ok', v })).catch((e) => setVis({ s: 'err', ...failOf(e) }));
  }, [q]);
  const confirmRun = useCallback(() => {
    const p = pending; setPending(null);
    if (!p) return;
    if (p.kind === 'sam' && samPlan.s === 'ok') {
      setSam({ s: 'loading' });
      runSam(q, samPlan.v.prompt, { confirmCostUsd: samPlan.v.est_cost_usd }).then((v) => { setSam({ s: 'ok', v }); })
        .catch((e) => setSam({ s: 'err', ...failOf(e) }));
    }
    if (p.kind === 'vision' && visPlan.s === 'ok') {
      setVis({ s: 'loading' });
      runVision(q, { confirmCostUsd: visPlan.v.est_cost_usd }).then((v) => setVis({ s: 'ok', v }))
        .catch((e) => setVis({ s: 'err', ...failOf(e) }));
    }
  }, [pending, q, samPlan, visPlan]);

  const cropPlan = samPlan.s === 'ok' ? samPlan.v : visPlan.s === 'ok' ? visPlan.v : null;
  const samGate = samPlan.s === 'ok' ? liveGate(samPlan.v.live, { operator, capReached: samPlan.v.usage.cap_reached || samPlan.v.usage.disabled }) : null;
  const visGate = visPlan.s === 'ok' ? liveGate(visPlan.v.live, { operator, capReached: visPlan.v.usage.cap_reached || visPlan.v.usage.disabled }) : null;
  const samResult = sam.s === 'ok' ? sam.v : null;
  const chips = samResult ? samChips(samResult) : null;
  const census = samResult ? geomCensus(samResult.objects) : null;
  const objs = samResult ? objectChips(samResult.objects) : [];
  const read = vis.s === 'ok' ? vis.v : null;

  /* no stored crop for this source and place: nothing to read, nothing to show */
  if (visPlan.s === 'err' && visPlan.f.word === 'Crop not served') return null;

  return (
    <section className="sw2-ai" aria-label="Meta AI on this crop" data-ai-source={source}>
      <div className="sw2-row">
        <span className="rk-lbl">Meta AI</span>
        <Chip chip="MODELLED" word="Modelled" title="Not ground truth" />
        <Chip chip="INFO" word="Native pixels" title="Stored crop only" />
        {cropPlan && <MetaChip>{`${cropPlan.crop_px[0]} × ${cropPlan.crop_px[1]} · ${cropPlan.resolution_m} m/px`}</MetaChip>}
        {roles.length > 1 && (
          <span className="rk-tabs" role="group" aria-label="Crop">
            {roles.map((x) => (
              <button key={x} type="button" className={`rk-tab${r === x ? ' on' : ''}`} aria-pressed={r === x} onClick={() => setRole(x)}>{x === 'now' ? 'Now' : 'Baseline'}</button>
            ))}
          </span>
        )}
      </div>

      <div className="sw2-ai-grid">
        <div className="sw2-ai-crop">
          {cropPlan ? (
            <div className="sw2-frame sw2-ai-frame" style={{ aspectRatio: `${cropPlan.crop_px[0]} / ${cropPlan.crop_px[1]}` }} data-native="true">
              <img src={cropSrc(cropPlan.crop_url)} alt={`${cropPlan.name ?? place} native crop${cropPlan.scene_date ? `, ${cropPlan.scene_date}` : ''}`} loading="lazy" />
              {samResult && <SamLayer result={samResult} hot={hot} onHot={setHot} />}
            </div>
          ) : <div className="sw2-frame sw2-ai-frame sw2-ai-empty" aria-hidden="true" />}
        </div>

        <div className="sw2-ai-side">
          {samOffered && (
            <div className="sw2-ai-block" data-block="sam">
              <div className="sw2-row">
                <b className="sw2-ai-h">SAM 3.1</b>
                {samPlan.s === 'ok' && samPlan.v.prompts.length > 1 && (
                  <select className="rk-input" aria-label="SAM phrase" value={samPlan.v.prompt} onChange={(e) => setPrompt(e.target.value)}>
                    {samPlan.v.prompts.map((p) => <option key={p} value={p}>{p}</option>)}
                  </select>
                )}
                {samPlan.s === 'ok' && samPlan.v.prompts.length === 1 && <MetaChip>{samPlan.v.prompt}</MetaChip>}
                {samPlan.s === 'ok' && samPlan.v.recorded.available && (
                  <button type="button" className="rk-btn" onClick={replaySam} disabled={sam.s === 'loading'}>Replay recorded</button>
                )}
                {samPlan.s === 'ok' && samGate && (
                  <>
                    <button type="button" className="rk-btn rk-btn--primary" disabled={!samGate.canRun || sam.s === 'loading' || pending !== null}
                      onClick={() => setPending({ kind: 'sam', confirm: samConfirm(samPlan.v) })}>Run SAM</button>
                    <Chip chip={samGate.chip.chip} word={samGate.chip.word} title={samGate.chip.title} />
                    {samPlan.v.live.state === 'READY' && <MetaChip>{`Est. ${samConfirm(samPlan.v).est}`}</MetaChip>}
                  </>
                )}
                {samPlan.s === 'err' && <Chip chip={samPlan.f.chip} word={samPlan.f.word} />}
                {samPlan.s === 'ok' && !samPlan.v.recorded.available && sam.s === 'idle' && <Chip chip="NOT SERVED" word="No recorded reply" />}
              </div>
              {pending?.kind === 'sam' && <ConfirmBox pending={pending} label="SAM run" onConfirm={confirmRun} onCancel={() => setPending(null)} />}
              {sam.s === 'loading' && <div className="sw2-row"><Chip chip="INFO" word="Running" /></div>}
              {sam.s === 'err' && <div className="sw2-row"><Chip chip={sam.f.chip} word={sam.f.word} title={sam.code} /></div>}
              {samResult && chips && (
                <div className="sw2-ai-result" data-sam-found={samResult.found} data-sam-mode={chips.mode.word.toUpperCase()}>
                  <div className="sw2-row">
                    <span className="sw2-found">{chips.found}</span>
                    <Chip chip="MODELLED" word={chips.label} />
                    <Chip chip={chips.mode.chip} word={chips.mode.word} title={chips.mode.word === 'Recorded' ? 'Stored reply' : 'Call made now'} />
                    <AiBadge provenance={samResult.provenance} did={foundWord(samResult.found)} />
                    <MetaChip>{chips.prompt}</MetaChip>
                    {chips.below && <MetaChip>{chips.below}</MetaChip>}
                    {census && census.masks > 0 && <MetaChip>{`${census.masks} outlines`}</MetaChip>}
                  </div>
                  {objs.length > 0 && (
                    <ul className="sw2-objs" aria-label="SAM objects">
                      {objs.map((o) => (
                        <li key={o.id}>
                          <span className={`rk-chip rk-chip--meta sw2-obj${hot === o.id ? ' hot' : ''}${o.belowFloor ? ' dim' : ''}`}
                            onMouseEnter={() => setHot(o.id)} onMouseLeave={() => setHot(null)} title={o.belowFloor ? 'Below the size floor' : undefined}>
                            <span className="sw2-obj-i">{o.index}</span><span>{o.word}</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          )}

          <div className="sw2-ai-block" data-block="vision">
            <div className="sw2-row">
              <b className="sw2-ai-h">Muse Spark read</b>
              {visPlan.s === 'ok' && visPlan.v.recorded.available && (
                <button type="button" className="rk-btn" onClick={replayVis} disabled={vis.s === 'loading'}>Replay recorded</button>
              )}
              {visPlan.s === 'ok' && visGate && (
                <>
                  <button type="button" className="rk-btn rk-btn--primary" disabled={!visGate.canRun || vis.s === 'loading' || pending !== null}
                    onClick={() => setPending({ kind: 'vision', confirm: visionConfirm(visPlan.v) })}>Read crop</button>
                  <Chip chip={visGate.chip.chip} word={visGate.chip.word} title={visGate.chip.title} />
                  {visPlan.v.live.state === 'READY' && <MetaChip>{`Est. ${visionConfirm(visPlan.v).est}`}</MetaChip>}
                </>
              )}
              {visPlan.s === 'err' && <Chip chip={visPlan.f.chip} word={visPlan.f.word} />}
              {visPlan.s === 'ok' && !visPlan.v.recorded.available && vis.s === 'idle' && <Chip chip="NOT SERVED" word="No recorded read" />}
            </div>
            {pending?.kind === 'vision' && <ConfirmBox pending={pending} label="read" onConfirm={confirmRun} onCancel={() => setPending(null)} />}
            {vis.s === 'loading' && <div className="sw2-row"><Chip chip="INFO" word="Reading" /></div>}
            {vis.s === 'err' && <div className="sw2-row"><Chip chip={vis.f.chip} word={vis.f.word} title={vis.code} /></div>}
            {read && <VisionReadView read={read} />}
          </div>
        </div>
      </div>
    </section>
  );
}
