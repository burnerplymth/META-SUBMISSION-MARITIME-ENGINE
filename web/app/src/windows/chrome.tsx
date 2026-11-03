import React, { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import type { IDockviewHeaderActionsProps, IDockviewPanelHeaderProps } from 'dockview-react';
import { StampText } from '../components/Panel';
import { WindowExportButton } from '../exports/PanelExportMenu';
import { useDeskStore } from '../store';
import { WindowAiBadge } from '../meta/WindowAiBadge';
import { Icon } from '../ui/Icon';
import { hint } from '../ui/hint';
import { useMenuKeys } from '../ui/menuKeys';
import { useWindowMeta } from './context';
import { wm } from './controller';
import { linkOf, linkStore, nextLink, type LinkOf } from './linkGroups';
import { WINDOWS, isWindowId, type WindowId } from './registry';

/** The link mark on a title bar: the group letter and colour (never colour alone). */
export function LinkMark({ group, onClick }: { group: LinkOf; onClick?: () => void }) {
  const label = group === null ? 'Not linked' : `Link group ${group}`;
  const n = group === 'A' ? 1 : group === 'B' ? 2 : 'none';
  /* without an action (the legend) the mark is a picture of the group, not a control */
  if (!onClick) return <span className="ui-link wm-link" data-group={n} role="img" aria-label={label}><span className="ui-link-n" aria-hidden="true">{group ?? ''}</span></span>;
  return (
    <button type="button" className="ui-link wm-link" data-group={n} aria-label={label} title={`${label}  ${hint('Link', '')}`.trim()} onClick={onClick}>
      <span className="ui-link-n" aria-hidden="true">{group ?? ''}</span>
    </button>
  );
}

export function useWmState() { return useSyncExternalStore(wm.subscribe, wm.getState, wm.getState); }

/** Tab: icon, title, close on hover or focus. The map has no close button. The tab is a role=tab element (dockview),
 *  whose children are presentational: the close mark is a pointer convenience, hidden from the tab order and from
 *  assistive technology. Keyboard: Delete on a focused tab, Alt+Shift+X, or Close in the window menu. */
export function WindowTab(props: IDockviewPanelHeaderProps) {
  const { api } = props;
  const id = api.id;
  const def = isWindowId(id) ? WINDOWS[id] : null;
  const [title, setTitle] = useState(api.title ?? def?.title ?? id);
  useEffect(() => { const d = api.onDidTitleChange?.((e: { title: string }) => setTitle(e.title)); return () => d?.dispose(); }, [api]);
  return (
    <div className="wm-tab" data-wid={id} onAuxClick={(e) => { if (e.button === 1 && id !== 'map' && isWindowId(id)) { e.preventDefault(); wm.closeWindow(id); } }}>
      {def && <Icon name={def.icon} size="xs" className="wm-tab-ico" />}
      <span className="wm-tab-title" title={title}>{title}</span>
      {id !== 'map' && isWindowId(id) && (
        <button type="button" className="wm-tab-x" aria-hidden="true" tabIndex={-1} aria-label={`Close ${title}`} title={`Close  ${hint('Close', 'Alt+Shift+X')}`}
          onPointerDown={(e) => e.stopPropagation()} onClick={(e) => { e.stopPropagation(); wm.closeWindow(id); }}>
          <Icon name="close" size="xs" />
        </button>
      )}
    </div>
  );
}

/** Popover menu, portalled so a floating window's clip never cuts it. */
function Menu({ anchor, onClose, opener, children }: { anchor: DOMRect; onClose: () => void; opener: React.RefObject<HTMLElement | null>; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const onMenuKey = useMenuKeys(true, ref, opener, onClose);
  useEffect(() => {
    const down = (e: PointerEvent) => { if (!ref.current?.contains(e.target as Node)) onClose(); };
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); opener.current?.focus(); } };
    window.addEventListener('pointerdown', down, true); window.addEventListener('keydown', key, true);
    return () => { window.removeEventListener('pointerdown', down, true); window.removeEventListener('keydown', key, true); };
  }, [onClose, opener]);
  const left = Math.max(8, Math.min(anchor.right - 200, window.innerWidth - 208));
  return createPortal(
    <div className="wm-menu pop" role="menu" aria-label="Window menu" ref={ref} onKeyDown={onMenuKey} style={{ left, top: anchor.bottom + 4 }}>{children}</div>, document.body);
}
const Item = ({ icon, label, keys, onClick, disabled }: { icon: Parameters<typeof Icon>[0]['name']; label: string; keys?: string; onClick: () => void; disabled?: boolean }) => (
  <button type="button" role="menuitem" className="wm-menu-item" disabled={disabled} onClick={onClick}>
    <Icon name={icon} size="sm" /><span>{label}</span>{keys && <kbd className="wm-menu-keys">{keys}</kbd>}
  </button>
);

/** Right side of a group's header: stamp, link mark, Meta AI badge, float,
 *  maximize and the overflow menu, for the group's active window. */
export function GroupActions(props: IDockviewHeaderActionsProps) {
  const { activePanel, group } = props;
  const wid = activePanel?.id;
  const id: WindowId | null = wid && isWindowId(wid) ? wid : null;
  const meta = useWindowMeta(id ?? '');
  const s = useDeskStore();
  const st = useWmState();
  const ls = useSyncExternalStore(linkStore.subscribe, linkStore.get, linkStore.get);
  const [menu, setMenu] = useState<DOMRect | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  if (!id) return null;
  const def = WINDOWS[id];
  const floating = group.api.location.type === 'floating';
  const maximized = st.maximized === id;
  const link = linkOf(ls, id);
  return (
    <div className="wm-actions" data-wid={id}>
      {meta?.action && <span className="wm-action">{meta.action}</span>}
      {meta?.updated && <span className="wm-stamp mono" title={meta.updated}><StampText text={meta.updated} /></span>}
      {def.ai && <WindowAiBadge windowId={id} />}
      {id !== 'expt' && <WindowExportButton windowId={id} />}
      <LinkMark group={link} onClick={() => wm.setLink(id, nextLink(link), { place: s.selectedNode, seq: s.selectedSeq })} />
      <button type="button" className="wm-btn" aria-label={floating ? 'Dock' : 'Float'} title={floating ? `Dock  ${hint('Dock', 'Alt+Shift+F')}` : `Float  ${hint('Float', 'Alt+Shift+F')}`}
        onClick={() => wm.toggleFloat(id)}><Icon name={floating ? 'panel-top' : 'square-stack'} size="sm" /></button>
      <button type="button" className="wm-btn" aria-label={maximized ? 'Restore' : 'Maximize'} title={`${maximized ? 'Restore' : 'Maximize'}  ${hint('Maximize', 'Alt+Shift+M')}`}
        onClick={() => wm.toggleMaximize(id)}><Icon name={maximized ? 'restore' : 'maximize'} size="sm" /></button>
      <button ref={btn} type="button" className="wm-btn" aria-label="Window menu" aria-haspopup="menu" aria-expanded={menu !== null} title="Menu"
        onClick={() => setMenu(btn.current?.getBoundingClientRect() ?? null)}><Icon name="more" size="sm" /></button>
      {menu && (
        <Menu anchor={menu} opener={btn} onClose={() => setMenu(null)}>
          <Item icon={floating ? 'panel-top' : 'square-stack'} label={floating ? 'Dock' : 'Float'} keys="Alt+Shift+F" onClick={() => { setMenu(null); wm.toggleFloat(id); }} />
          <Item icon={maximized ? 'restore' : 'maximize'} label={maximized ? 'Restore' : 'Maximize'} keys="Alt+Shift+M" onClick={() => { setMenu(null); wm.toggleMaximize(id); }} />
          {id === 'map'
            ? <Item icon="eye-off" label="Hide map" onClick={() => { setMenu(null); wm.hideMap(); }} />
            : <Item icon="close" label="Close" keys="Alt+Shift+X" onClick={() => { setMenu(null); wm.closeWindow(id); }} />}
        </Menu>
      )}
    </div>
  );
}
