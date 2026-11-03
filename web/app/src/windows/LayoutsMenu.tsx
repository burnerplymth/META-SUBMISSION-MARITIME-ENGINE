import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useDeskStore } from '../store';
import { MODE_LABEL, familyFor } from '../shell/mode';
import { Icon, type IconName } from '../ui/Icon';
import { Kbd } from '../ui/Controls';
import { useMenuKeys } from '../ui/menuKeys';
import { useWmState } from './chrome';
import { wm } from './controller';
import { BUILTIN_PRESETS, MAX_USER_PRESETS, NAME_MAX, presetAvailable, type Preset } from './presets';
import { WINDOWS, type WindowId } from './registry';
import type { LayoutOp } from '../search/command';

/** Window event the command bar uses to reach this menu (`layout save`, `layout rename X`). */
export const LAYOUT_OP_EVENT = 'sm:layout-op';
export const requestLayoutOp = (op: LayoutOp, name?: string): void => {
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(LAYOUT_OP_EVENT, { detail: { op, name } }));
};

const codesOf = (p: Preset): string => {
  const ids = [...new Set(p.phone)].filter((id) => id !== 'map').slice(0, 5);
  return (['map', ...ids] as WindowId[]).map((id) => WINDOWS[id].code).join(' ');
};

function download(name: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
  const a = document.createElement('a');
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

type Row = { icon: IconName; label: string; keys?: string; run: () => void; disabled?: boolean };

/** The Layouts menu: built-in presets, user presets, Save
 *  current, Rename, Duplicate, Export file, Import file, Copy link, Reset. */
export function LayoutsMenu() {
  const s = useDeskStore();
  const st = useWmState();
  const [open, setOpen] = useState(false);
  const [edit, setEdit] = useState<null | 'save' | 'rename'>(null);
  const [text, setText] = useState('');
  const [note, setNote] = useState<string | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const file = useRef<HTMLInputElement>(null);
  const [anchor, setAnchor] = useState<DOMRect | null>(null);

  const toggle = useCallback((on: boolean) => { setOpen(on); if (on) setAnchor(btn.current?.getBoundingClientRect() ?? null); else { setEdit(null); setNote(null); } }, []);
  const onMenuKey = useMenuKeys(open && anchor !== null, pop, btn, useCallback(() => toggle(false), [toggle]));
  const flash = (m: string) => { setNote(m); window.setTimeout(() => setNote(null), 1800); };
  const active = st.userPresets.find((p) => p.id === st.presetId);

  useEffect(() => {
    if (!open) return;
    const down = (e: PointerEvent) => { const t = e.target as Node; if (!pop.current?.contains(t) && !btn.current?.contains(t)) toggle(false); };
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); toggle(false); btn.current?.focus(); } };
    window.addEventListener('pointerdown', down, true); window.addEventListener('keydown', key, true);
    return () => { window.removeEventListener('pointerdown', down, true); window.removeEventListener('keydown', key, true); };
  }, [open, toggle]);

  // Alt+L opens the menu; the command bar's layout verbs arrive as an event.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.altKey && !e.shiftKey && !e.ctrlKey && !e.metaKey && e.code === 'KeyL') { e.preventDefault(); toggle(true); } };
    const onOp = (e: Event) => {
      const { op, name } = (e as CustomEvent<{ op: LayoutOp; name?: string }>).detail;
      if (op === 'save') { if (name) wm.saveCurrent(name); else { toggle(true); setEdit('save'); setText(''); } }
      else if (op === 'rename') { if (name && active) wm.rename(active.id, name); else if (active) { toggle(true); setEdit('rename'); setText(active.name); } }
      else if (op === 'reset') wm.reset();
      else if (op === 'duplicate') wm.duplicate();
      else if (op === 'export') { const f = wm.exportFile(); download(f.name, f.text); }
      else if (op === 'link') void copyLink();
      else if (op === 'import') file.current?.click();
    };
    window.addEventListener('keydown', onKey); window.addEventListener(LAYOUT_OP_EVENT, onOp);
    return () => { window.removeEventListener('keydown', onKey); window.removeEventListener(LAYOUT_OP_EVENT, onOp); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.id, toggle]);

  async function copyLink(): Promise<void> {
    const code = await wm.shareCode();
    if (!code) { flash('Too large'); return; }
    const base = typeof location !== 'undefined' ? location.origin + location.pathname : '';
    const url = `${base}#layout=${encodeURIComponent(wm.state.legacy)}&preset=${code}`;
    try { await navigator.clipboard.writeText(url); flash('Copied'); } catch { flash('Copy blocked'); }
  }

  const fam = familyFor(s.mode, s.liveTwinEnabled);
  const gate = { liveTwin: s.liveTwinEnabled };
  const builtins = BUILTIN_PRESETS.filter((p) => presetAvailable(p, gate));
  const primary = builtins.filter((p) => fam.includes(p.legacy)), other = builtins.filter((p) => !fam.includes(p.legacy));
  const pick = (p: Preset) => { wm.apply(p); toggle(false); };

  const presetRow = (p: Preset, user: boolean) => {
    const on = st.presetId === p.id;
    return (
      <div key={p.id} className={`lm-row${on ? ' on' : ''}`} role="none">
        <button type="button" role="menuitemradio" aria-checked={on} className="lm-pick" onClick={() => (user ? (wm.applyUser(p.id), toggle(false)) : pick(p))}>
          <Icon name="layout-dashboard" size="md" className="lm-ico" />
          <span className="lm-name">{p.name}</span>
          <span className="lm-codes mono">{codesOf(p)}</span>
          {on && st.modified && <span className="lm-mod">Modified</span>}
        </button>
        {user && <button type="button" role="menuitem" className="lm-del" aria-label={`Delete ${p.name}`} title="Delete" onClick={() => wm.remove(p.id)}><Icon name="trash-2" size="sm" /></button>}
      </div>
    );
  };
  const rows: Row[] = [
    { icon: 'plus', label: 'Save current', run: () => { setEdit('save'); setText(''); }, disabled: st.userPresets.length >= MAX_USER_PRESETS },
    { icon: 'pencil', label: 'Rename', run: () => { setEdit('rename'); setText(active?.name ?? ''); }, disabled: !active },
    { icon: 'copy', label: 'Duplicate', run: () => { wm.duplicate(); }, disabled: st.userPresets.length >= MAX_USER_PRESETS },
    { icon: 'download', label: 'Export file', run: () => { const f = wm.exportFile(); download(f.name, f.text); } },
    { icon: 'upload', label: 'Import file', run: () => file.current?.click() },
    { icon: 'link', label: 'Copy link', run: () => { void copyLink(); } },
    { icon: 'rotate-ccw', label: 'Reset', run: () => { wm.reset(); toggle(false); } },
  ];
  const submit = () => {
    const n = text.trim();
    if (!n) return;
    if (edit === 'save') { if (active) { /* keep the active user preset, add another */ } wm.saveCurrent(n); }
    else if (edit === 'rename' && active) wm.rename(active.id, n);
    setEdit(null); setText('');
  };

  return (
    <span className="layouts-wrap lm">
      <button ref={btn} type="button" className="layouts-btn mono lm-btn" aria-haspopup="menu" aria-expanded={open} title="Layouts  Alt+L"
        onClick={() => toggle(!open)}>
        <Icon name="layout-dashboard" size="sm" /><span className="lm-btn-w">Layouts</span><Icon name="chevron-down" size="xs" />
      </button>
      <input ref={file} type="file" accept="application/json,.json" hidden aria-label="Import layout file"
        onChange={async (e) => { const f = e.target.files?.[0]; e.target.value = ''; if (!f) return; const ok = wm.importText(await f.text()); flash(ok ? 'Imported' : 'Not a layout'); }} />
      {open && anchor && createPortal(
        <div ref={pop} className="lm-pop pop" role="menu" aria-label="Layouts" onKeyDown={onMenuKey} style={{ top: anchor.bottom + 4, left: Math.max(8, Math.min(anchor.right - 360, window.innerWidth - 368)) }}>
          <div className="lm-h">Layout presets <span className="lm-h-mode">{MODE_LABEL[s.mode]}</span></div>
          {primary.map((p) => presetRow(p, false))}
          {other.length > 0 && <div className="lm-sep" />}
          {other.map((p) => presetRow(p, false))}
          <div className="lm-h lm-sep-h">My presets</div>
          {st.userPresets.length === 0 && <div className="lm-empty"><span className="lm-empty-w">None saved</span></div>}
          {st.userPresets.map((p) => presetRow(p, true))}
          <div className="lm-sep" />
          {edit && (
            <form className="lm-edit" onSubmit={(e) => { e.preventDefault(); submit(); }}>
              <input autoFocus value={text} maxLength={NAME_MAX} aria-label={edit === 'save' ? 'Layout name' : 'New name'} placeholder="Name" onChange={(e) => setText(e.target.value)} />
              <button type="submit" className="lm-ok" disabled={!text.trim()}>{edit === 'save' ? 'Save' : 'Rename'}</button>
            </form>
          )}
          {rows.map((r) => (
            <button key={r.label} type="button" role="menuitem" className="lm-act" disabled={r.disabled} onClick={r.run}>
              <Icon name={r.icon} size="md" /><span>{r.label}</span>{r.keys && <Kbd keys={r.keys} />}
            </button>
          ))}
          {note && <div className="lm-note" role="status">{note}</div>}
        </div>, document.body)}
    </span>
  );
}
