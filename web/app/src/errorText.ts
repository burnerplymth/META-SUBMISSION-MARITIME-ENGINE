import { ApiError } from './api';

/* The only text an error may put on screen: a short title plus one fixed
 * sentence. The raw exception message, a server sentence, a path or an id is
 * never printed. Codes come from ApiError.code (client codes and the server's
 * short error codes); anything unmapped falls back to a generic pair. */

export interface ErrorText { title: string; sentence: string }

const CODE_TEXT: Record<string, ErrorText> = {
  timeout: { title: 'Request timed out', sentence: 'The service did not answer in time.' },
  aborted: { title: 'Request cancelled', sentence: 'The call was stopped before it finished.' },
  network: { title: 'Service unreachable', sentence: 'The request did not reach the service.' },
  bad_sim_run: { title: 'Run not followed', sentence: 'The service named no run to follow.' },
  http_400: { title: 'Request refused', sentence: 'The request was not accepted.' },
  http_401: { title: 'Request refused', sentence: 'Access is not granted.' },
  http_403: { title: 'Request refused', sentence: 'Access is not granted.' },
  http_404: { title: 'Not served', sentence: 'The item is not available.' },
  http_413: { title: 'Request refused', sentence: 'The request is too large.' },
  http_429: { title: 'Request refused', sentence: 'The call limit is reached.' },
};

const FALLBACK: ErrorText = { title: 'Request failed', sentence: 'The service did not complete the request.' };

/** Title and sentence for any thrown value. */
export function errorText(e: unknown): ErrorText {
  const code = e instanceof ApiError ? e.code : '';
  if (e instanceof ApiError && e.tag === 'build') return { title: 'Run still building', sentence: 'It can be reopened from search when ready.' };
  if (CODE_TEXT[code]) return CODE_TEXT[code];
  if (/^http_5\d\d$/.test(code)) return { title: 'Service error', sentence: 'The service could not complete the request.' };
  if (/^http_4\d\d$/.test(code)) return { title: 'Request refused', sentence: 'The request was not accepted.' };
  return FALLBACK;
}

/** `Title. Sentence.` as one line for the error bar. */
export function errorLine(e: unknown): string {
  const t = errorText(e);
  return `${t.title}. ${t.sentence}`;
}
