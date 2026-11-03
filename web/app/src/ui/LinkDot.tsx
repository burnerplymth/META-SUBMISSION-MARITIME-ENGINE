import React from 'react';

/** Link groups (Bloomberg / LSEG style): windows that share a group follow
 *  the same selection. The group is a number 1..6 AND a colour (--lnk-1..6),
 *  so it is never colour alone. `null` is unlinked (a plain ring). The window
 *  manager owns the behaviour; this is only the mark. */
export const LINK_GROUPS = [1, 2, 3, 4, 5, 6] as const;
export type LinkGroup = (typeof LINK_GROUPS)[number];

export function LinkDot({ group, onClick, title }: { group: LinkGroup | null; onClick?: () => void; title?: string }) {
  const label = group === null ? 'Not linked' : `Link group ${group}`;
  const body = <span className="ui-link-n" aria-hidden="true">{group ?? ''}</span>;
  return onClick
    ? <button type="button" className="ui-link" data-group={group ?? 'none'} aria-label={label} title={title ?? label} onClick={onClick}>{body}</button>
    : <span className="ui-link" data-group={group ?? 'none'} role="img" aria-label={label} title={title ?? label}>{body}</span>;
}
