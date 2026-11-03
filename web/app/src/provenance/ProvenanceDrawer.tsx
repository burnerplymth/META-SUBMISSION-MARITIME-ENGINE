import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Chip } from '../panels/reports/chips';
import { Icon } from '../ui/Icon';
import { closeProvenance, NOT_EXPOSED, useProvenance, WORD_TEXT, type ProvSection } from './registry';
import { provenanceSourceLine } from './registry';
import './provenance.css';

/* One drawer for the whole app, mounted once. It shows the open reference's
 * sections; a value the document does not carry reads "Not exposed". */
export function ProvenanceDrawer() {
  const ref = useProvenance();
  const shell = useRef<HTMLElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const [late, setLate] = useState<{ id: string; sections: ProvSection[] } | null>(null);
  const id = ref?.id ?? null;

  useEffect(() => {
    if (!id) return;
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    shell.current?.querySelector<HTMLElement>('[data-prov-close]')?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); closeProvenance(); } };
    window.addEventListener('keydown', key, true);
    return () => {
      window.removeEventListener('keydown', key, true);
      const o = opener.current; if (o && document.contains(o)) o.focus();
    };
  }, [id]);

  useEffect(() => {
    setLate(null);
    if (!ref?.load) return;
    let live = true;
    ref.load().then((s) => { if (live && s.length) setLate({ id: ref.id, sections: s }); }).catch(() => { /* journal unavailable: the rows stay as listed */ });
    return () => { live = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  if (!ref) return null;
  const sections = late && late.id === ref.id ? [...ref.sections, ...late.sections] : ref.sections;
  return createPortal(
    <aside ref={shell} className="prov-drawer" role="dialog" aria-label="Provenance" data-testid="prov-drawer">
      <header className="prov-head">
        <span className="prov-kicker">Where from</span>
        <button type="button" className="icon-btn" data-prov-close aria-label="Close provenance" onClick={closeProvenance}><Icon name="close" /></button>
      </header>
      <div className="prov-body">
        <h2 className="prov-title">{ref.title}</h2>
        <div className="prov-value mono" data-testid="prov-value">{ref.value}</div>
        <div className="prov-chips">
          {ref.word ? <Chip chip={ref.word} word={WORD_TEXT[ref.word]} /> : <span className="prov-ne mono">{NOT_EXPOSED}</span>}
          {ref.asOf && <span className="prov-asof mono">{`As of ${ref.asOf}`}</span>}
        </div>
        <section className="prov-sec" aria-label="Formula">
          <h3>Formula</h3>
          {ref.formula ? <code className="prov-formula mono">{ref.formula}</code> : <span className="prov-ne mono">{NOT_EXPOSED}</span>}
        </section>
        {sections.map((s) => (
          <section key={s.title} className="prov-sec" aria-label={s.title}>
            <h3>{s.title}</h3>
            <dl className="prov-rows">
              {s.rows.map((r, i) => (
                <div key={`${r.label}-${i}`} className="prov-row">
                  <dt>{r.label}</dt>
                  <dd className={r.value == null ? 'prov-ne mono' : r.mono ? 'mono prov-hash' : undefined}>{r.value ?? NOT_EXPOSED}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
        <p className="prov-source mono" data-testid="prov-source" role="group" aria-label="Source line">{provenanceSourceLine(ref, sections)}</p>
      </div>
    </aside>, document.body);
}
