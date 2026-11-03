/**
 * One factual sentence for a failed call, from its short code and HTTP status.
 * The server's own message text (which can carry paths, ids or stack wording)
 * is never shown; the code stays available as a short mono token elsewhere.
 */
export function failureSentence(code?: string | null, status?: number | null): string {
  const c = (code ?? '').toLowerCase();
  const fromCode = /^http_(\d{3})$/.exec(c);
  const s = status && status > 0 ? status : fromCode ? Number(fromCode[1]) : 0;
  if (c === 'timeout') return 'Request timed out.';
  if (c === 'network' || (s === 0 && (c === '' || c === 'error'))) return 'Service unreachable.';
  if (c === 'rate_limited' || c === 'cap_reached' || s === 429) return 'Request refused. The call limit is reached.';
  if (s === 401 || s === 403 || /auth|forbidden|token/.test(c)) return 'Request refused. Access is not granted.';
  if (s === 404 || c === 'not_found' || c === 'route_missing') return 'Not served. The route is not available.';
  if (s >= 500) return 'Service error. The request was not completed.';
  if (s >= 400) return 'Request refused.';
  return 'Request not completed.';
}
