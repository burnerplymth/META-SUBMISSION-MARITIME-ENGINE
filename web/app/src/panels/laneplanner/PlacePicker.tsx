import React, { useEffect, useId, useMemo, useRef, useState } from 'react';
import { placeTag, placeTagKey, searchPlaces, type PlaceChoice } from './logic';

/* A place field: type to search the served places (all modes, both tiers),
 * pick with the mouse or the arrow keys. The tag says what the planner will
 * do with the place (calibrated places plan; estimated places plan when the switch is on; air places are refused with their reason). */
export function PlacePicker({ label, value, choices, onChange, disabled }: {
  label: string; value: string | null; choices: readonly PlaceChoice[]; onChange: (id: string) => void; disabled?: boolean;
}) {
  const id = useId();
  const [q, setQ] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [idx, setIdx] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const cur = useMemo(() => choices.find((c) => c.id === value) ?? null, [choices, value]);
  const hits = useMemo(() => (open ? searchPlaces(choices, q ?? '', 12) : []), [open, choices, q]);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) { setOpen(false); setQ(null); } };
    document.addEventListener('mousedown', away);
    return () => document.removeEventListener('mousedown', away);
  }, [open]);
  const pick = (p: PlaceChoice) => { onChange(p.id); setOpen(false); setQ(null); };
  return (
    <div className="lp-field" ref={box}>
      <label htmlFor={id} className="lp-lab">{label}</label>
      <input id={id} className="lp-input" role="combobox" aria-expanded={open} aria-controls={`${id}-l`} aria-autocomplete="list" aria-activedescendant={open && hits[idx] ? `${id}-o${idx}` : undefined} autoComplete="off" spellCheck={false}
        disabled={disabled} value={q ?? cur?.name ?? value ?? ''} placeholder="Search places"
        onFocus={(e) => { setOpen(true); setIdx(0); e.currentTarget.select(); }}
        onBlur={(e) => { if (!box.current?.contains(e.relatedTarget as Node | null)) { setOpen(false); setQ(null); } }}
        onChange={(e) => { setQ(e.target.value); setOpen(true); setIdx(0); }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setIdx((i) => Math.min(hits.length - 1, i + 1)); }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)); }
          else if (e.key === 'Enter' && open && hits[idx]) { e.preventDefault(); e.stopPropagation(); pick(hits[idx]); }
          else if (e.key === 'Escape') { setOpen(false); setQ(null); }
        }} />
      {cur && q === null && <span className="lp-sub">{[cur.country, placeTag(cur)].filter(Boolean).join(' · ')}</span>}
      {open && (
        <ul id={`${id}-l`} className="lp-list" role="listbox" aria-label={label}>
          {hits.length === 0 && <li className="lp-empty" role="option" aria-selected="false">No match</li>}
          {hits.map((p, i) => (
            <li key={p.id} id={`${id}-o${i}`} role="option" aria-selected={i === idx} className={i === idx ? 'on' : ''}
              onMouseDown={(e) => { e.preventDefault(); pick(p); }} onMouseEnter={() => setIdx(i)}>
              <span className="lp-pn">{p.name}</span>
              <span className="lp-pc">{p.country ?? ''}</span>
              <span className={`lp-tag lp-tag--${placeTagKey(p)}`}>{placeTag(p)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
