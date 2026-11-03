/**
 * FEED CLASSIFIER. There is no per-item button any
 * more. Classify is a News toolbar action (`Classify visible`) and an item
 * context-menu entry (`Classify with Meta AI`). The classifier is the
 * model's EDGE-IN role (event text -> a structured candidate); its result is
 * plain tag text plus one `AiBadge`. It changes nothing else: no event is
 * minted, ranked or re-graded from here.
 *
 *   start -> POST /llm/classify/preview (the API's pre-call estimate)
 *     recorded transport (free): runs straight away
 *     any other transport: shows the estimate and waits for Confirm
 *   result -> Weather · Sev 4 · Savannah · Can start a chain, or
 *             Unclassified / Place not in catalogue when the closed-schema
 *             or catalogue check fails. The free-text reason is not shown.
 *
 * The preview and confirm flow for paid calls is unchanged: this module only
 * moves where the request starts.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { nodeName } from '../seed';
import { useDeskStore } from '../store';
import { Status } from '../ui/Status';
import { AiBadge } from './AiBadge';
import { noteMetaCall } from './aiStatus';
import { recordAiCall } from './aiCalls';
import { classifyPreview, classifyText, MetaCallError, type ClassifyBody } from './client';
import { classifyPreviewView, classifyView, kindWord, type ClassifyPreviewView, type ClassifyView } from './classify';
import { refusalWord } from './nlQuery';

export type ClassifyPhase =
  | { p: 'idle' }
  | { p: 'previewing' }
  | { p: 'confirm'; preview: ClassifyPreviewView }
  | { p: 'running'; preview: ClassifyPreviewView | null }
  | { p: 'done'; preview: ClassifyPreviewView | null; view: ClassifyView }
  | { p: 'error'; code: string };

export const CLASSIFY_COPY = {
  /** The News toolbar action and the item context-menu entry. */
  toolbar: 'Classify visible',
  menu: 'Classify with Meta AI',
  confirm: 'Confirm paid call',
  cancel: 'Cancel',
  running: 'Classifying',
  unclassified: 'Unclassified',
  unknownPlace: 'Place not in catalogue',
  failed: 'Classify failed',
  notRelevant: 'Not relevant',
  did: 'Classified 1 headline',
} as const;

/** At most this many items per `Classify visible` press. */
export const CLASSIFY_BATCH_MAX = 10;

export interface ClassifyItem { key: string; body: ClassifyBody }

const IDLE: ClassifyPhase = { p: 'idle' };

/** Per-item classify state for one list. The News panel owns one of these. */
export function useFeedClassify() {
  const s = useDeskStore();
  const [phases, setPhases] = useState<Record<string, ClassifyPhase>>({});
  const aborts = useRef(new Map<string, AbortController>());
  const phaseRef = useRef(phases);
  phaseRef.current = phases;
  const known = useMemo(() => {
    const ids = new Set<string>();
    for (const n of s.graph?.nodes ?? []) {
      const id = (n as { id?: unknown })?.id;
      if (typeof id === 'string') ids.add(id);
    }
    return ids;
  }, [s.graph]);
  useEffect(() => () => { for (const a of aborts.current.values()) a.abort(); }, []);
  const put = useCallback((key: string, ph: ClassifyPhase) => setPhases((m) => ({ ...m, [key]: ph })), []);

  const call = useCallback(async (key: string, body: ClassifyBody, preview: ClassifyPreviewView | null) => {
    put(key, { p: 'running', preview });
    try {
      const raw = await classifyText(body, aborts.current.get(key)?.signal);
      const view = classifyView(raw, known);
      if (view) {
        recordAiCall({ surface: 'classify', did: CLASSIFY_COPY.did, provenance: view.provenance });
        noteMetaCall();
      }
      put(key, view ? { p: 'done', preview, view } : { p: 'error', code: 'bad_reply' });
    } catch (e) {
      put(key, { p: 'error', code: e instanceof MetaCallError ? e.code : 'failed' });
    }
  }, [known, put]);

  const start = useCallback(async (item: ClassifyItem) => {
    const cur = phaseRef.current[item.key]?.p;
    if (cur === 'previewing' || cur === 'running' || cur === 'confirm' || cur === 'done') return;
    aborts.current.get(item.key)?.abort();
    const ctrl = new AbortController();
    aborts.current.set(item.key, ctrl);
    put(item.key, { p: 'previewing' });
    let preview: ClassifyPreviewView | null = null;
    try { preview = classifyPreviewView(await classifyPreview(item.body, ctrl.signal)); } catch { preview = null; }
    // A paid call, or one whose cost the API would not preview, asks first.
    if (!preview || preview.paid) { put(item.key, { p: 'confirm', preview: preview ?? { estimate: null, paid: true } }); return; }
    await call(item.key, item.body, preview);
  }, [call, put]);

  const startMany = useCallback(async (items: ClassifyItem[]) => {
    for (const it of items.slice(0, CLASSIFY_BATCH_MAX)) await start(it);
  }, [start]);

  const confirm = useCallback((item: ClassifyItem) => {
    const ph = phaseRef.current[item.key];
    if (ph?.p === 'confirm') void call(item.key, item.body, ph.preview);
  }, [call]);
  const cancel = useCallback((key: string) => { aborts.current.get(key)?.abort(); put(key, IDLE); }, [put]);
  const busy = Object.values(phases).some((p) => p.p === 'previewing' || p.p === 'running');
  return { phases, start, startMany, confirm, cancel, busy };
}

/** The state under one news item: nothing while idle. */
export function ClassifyBlock({ phase, onConfirm, onCancel }: { phase: ClassifyPhase | undefined; onConfirm?: () => void; onCancel?: () => void }) {
  const ph = phase ?? IDLE;
  if (ph.p === 'idle') return null;
  return (
    <div className="fclass" onClick={(e) => e.stopPropagation()} data-phase={ph.p}>
      {(ph.p === 'previewing' || ph.p === 'running') && <Status state="meta" word={CLASSIFY_COPY.running} />}
      {ph.p === 'confirm' && (
        <>
          {ph.preview.estimate && <Status state="meta" word={ph.preview.estimate} />}
          <button type="button" className="fclass-btn fclass-btn--go" onClick={onConfirm}>{CLASSIFY_COPY.confirm}</button>
          <button type="button" className="fclass-btn" onClick={onCancel}>{CLASSIFY_COPY.cancel}</button>
        </>)}
      {ph.p === 'error' && <Status state="down" word={CLASSIFY_COPY.failed} meta={refusalWord(ph.code)} />}
      {ph.p === 'done' && <ClassifyChips view={ph.view} preview={ph.preview} />}
    </div>
  );
}

/** The result row: exported for static-markup rendering. */
export function ClassifyChips({ view, preview }: { view: ClassifyView; preview?: ClassifyPreviewView | null }) {
  const tags = view.state === 'unclassified' ? [] : [
    kindWord(view.kind!), `Sev ${view.severity}`,
    ...(view.placeId ? [nodeName(view.placeId)] : []),
    ...(view.mintable !== null ? [view.mintable ? 'Can start a chain' : 'Cannot start a chain'] : []),
  ];
  return (
    <div className="fclass-row">
      {view.state === 'unclassified'
        ? <Status state="notserved" word={CLASSIFY_COPY.unclassified} />
        : <span className="fclass-tags">{tags.join(' · ')}</span>}
      {view.unknownPlace && <Status state="notserved" word={CLASSIFY_COPY.unknownPlace} />}
      {view.relevant === false && <Status state="notserved" word={CLASSIFY_COPY.notRelevant} />}
      <AiBadge provenance={view.provenance} did={CLASSIFY_COPY.did} />
    </div>
  );
}
