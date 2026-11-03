import type { MouseEvent, ReactNode } from 'react';
import { safeHref } from '../../../map/src/safeHref';

/**
 * An anchor only when safeHref accepts the data-supplied URL;
 * otherwise the label renders as plain text — no anchor element at all.
 */
export function SafeLink({ href, children, onClick }: {
  href: string | null | undefined;
  children: ReactNode;
  onClick?: (e: MouseEvent) => void;
}) {
  const safe = safeHref(href);
  if (safe === null) return <span>{children}</span>;
  return <a href={safe} target="_blank" rel="noreferrer" onClick={onClick}>{children}</a>;
}
