/**
 * Shared href guard — the single place both web packages route
 * data-supplied URLs through before rendering them as anchors.
 *
 * Returns the URL unchanged only when it parses as an absolute URL and
 * its protocol is http: or https:. Anything else — javascript:, data:,
 * vbscript:, protocol-relative, relative, malformed, empty, or missing —
 * returns null, and callers render plain text instead of an anchor.
 */
export function safeHref(raw: string | null | undefined): string | null {
  if (typeof raw !== 'string' || !raw.trim()) return null;
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return null;
  }
  return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? raw : null;
}
