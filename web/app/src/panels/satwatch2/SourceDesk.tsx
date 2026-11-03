import React, { useCallback, useEffect, useState } from 'react';
import { Chip, MetaChip, StateLine } from '../reports/chips';
import { labelWord, sentenceCase } from '../../copy';
import { CentreError } from '../reports/client';
import { failureState } from '../reports/model';
import { fetchSourceRecord, runSourceFetch } from './client';
import { FrameImage } from './EnhancedImage';
import { Layers } from './overlayLayers';
import {
  canFetch, fetchWord, recordChips,
  type AvailabilityDoc, type SourceChipView, type SourceId, type SourceRecord, type SourceSpec, type SourcesDoc,
} from './sources';

/* Multi-source strip and single-source viewer. Chips and state
 * labels only. The strip names the sources (S2 / S1 SAR / LANDSAT / NAIP)
 * with a state per place; the viewer shows one stored crop with the
 * Sentinel-1 targets as points on it. Counts and points are the server's:
 * enhanced pixels (EnhancedImage) are presentation and feed nothing. */

export function SourceStrip({ chips, specs, active, onSelect }: { chips: SourceChipView[]; specs: SourcesDoc; active: SourceId; onSelect: (id: SourceId) => void }) {
  const spec = specs.sources.find((s) => s.id === active) ?? null;
  const cur = chips.find((c) => c.id === active) ?? null;
  return (
    <div className="sw2-sources" role="group" aria-label="Imagery source">
      <div className="sw2-row">
        <span className="rk-lbl">Source</span>
        <span className="rk-tabs">
          {chips.map((c) => (
            <button key={c.id} type="button" className={`rk-tab sw2-src${active === c.id ? ' on' : ''}`} aria-pressed={active === c.id}
              disabled={!c.selectable} onClick={() => onSelect(c.id)}>
              <span>{c.label}</span>
              <Chip chip={c.state.chip} word={c.state.word} title={c.state.title} />
            </button>
          ))}
        </span>
      </div>
      {spec && cur && (
        <div className="sw2-row">
          <Chip chip={cur.grade.chip} word={cur.grade.word} title="Ship-detection grade" />
          <MetaChip>{`${spec.resolution_m} m/px`}</MetaChip>
          <MetaChip>Revisit {spec.revisit_label}</MetaChip>
          {spec.coverage !== 'global' && <MetaChip>{spec.coverage} only</MetaChip>}
          {spec.attribution_chip && <MetaChip title={spec.licence}>{spec.attribution_chip}</MetaChip>}
        </div>
      )}
      {spec && spec.attribution && <p className="fineprint mono src-credit" data-source-credit>{spec.attribution}</p>}
    </div>
  );
}

type RecState = { s: 'loading' } | { s: 'ok'; rec: SourceRecord | null } | { s: 'err'; code: string };

export function SourceViewer({ source, place, spec, avail, canScan, markers, enhanced, onChanged }: {
  source: Exclude<SourceId, 'sentinel-2'>; place: string; spec: SourceSpec | null; avail: AvailabilityDoc | 'loading' | 'err' | null;
  canScan: boolean; markers: boolean; enhanced: boolean; onChanged: () => void;
}) {
  const [st, setSt] = useState<RecState>({ s: 'loading' });
  const [busy, setBusy] = useState(false);
  const [fail, setFail] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const ctrl = new AbortController();
    setSt({ s: 'loading' }); setFail(null);
    fetchSourceRecord(source, place, ctrl.signal).then((rec) => setSt({ s: 'ok', rec }))
      .catch((e: unknown) => { if ((e as CentreError).code !== 'aborted') setSt({ s: 'err', code: (e as CentreError).code ?? 'error' }); });
    return () => ctrl.abort();
  }, [source, place, tick]);

  const row = avail && avail !== 'loading' && avail !== 'err' ? avail.sources.find((r) => r.id === source) ?? null : null;
  const run = useCallback(async () => {
    setBusy(true); setFail(null);
    try { const rec = await runSourceFetch(source, place); setSt({ s: 'ok', rec }); onChanged(); }
    catch (e) { setFail((e as CentreError).code ?? 'error'); }
    finally { setBusy(false); }
  }, [source, place, onChanged]);

  if (st.s === 'loading') return <StateLine chip="INFO" word="Loading" />;
  if (st.s === 'err') return <StateLine chip={failureState(st.code).chip} word={failureState(st.code).word} code={st.code} onRetry={() => setTick((t) => t + 1)} />;
  const rec = st.rec;
  const failed = fail ? failureState(fail) : null;
  if (!rec) {
    const word = source === 'sentinel-1' ? 'Not scanned' : 'Not fetched';
    return (
      <div className="sw2-srcview">
        <div className="sw2-row">
          <Chip chip="NOT SERVED" word={word} />
          {row && row.state !== 'AVAILABLE' && <Chip chip="NOT SERVED" word={sentenceCase(row.state)} />}
          {canScan && canFetch(row) && (
            <button type="button" className="rk-btn rk-btn--primary" onClick={run} disabled={busy}>{fetchWord(source)}</button>
          )}
        </div>
        {failed && <StateLine chip={failed.chip} word={failed.word} code={failed.code} />}
      </div>
    );
  }
  const chips = recordChips(rec, spec);
  const [cw, ch] = rec.crop.px;
  const alt = `${rec.name || rec.place}, ${spec?.label ?? rec.source} ${rec.scene.date}`;
  return (
    <div className="sw2-srcview">
      <div className="sw2-row">
        {chips.map((c) => <Chip key={c.key} chip={c.chip} word={c.word} title={c.title} />)}
        <span className="rk-spacer" />
        {canScan && canFetch(row) && (
          <button type="button" className="rk-btn" onClick={run} disabled={busy}>{busy ? fetchWord(source) : `Re${fetchWord(source).toLowerCase()}`}</button>
        )}
      </div>
      {failed && <StateLine chip={failed.chip} word={failed.word} code={failed.code} />}
      {rec.source === 'sentinel-1' && rec.detector.status !== 'OK' && <StateLine chip="NOT SERVED" word={labelWord(rec.detector.status)} />}
      <div className="sw2-frame" style={{ aspectRatio: `${cw} / ${ch}` }} data-source={rec.source}>
        <FrameImage url={rec.crop.url} alt={alt} kind={spec?.kind ?? 'optical'} enhanced={enhanced} />
        <Layers dets={rec.detections} show={markers && rec.source === 'sentinel-1'} />
      </div>
    </div>
  );
}
