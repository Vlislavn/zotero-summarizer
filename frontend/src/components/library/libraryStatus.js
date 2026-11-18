// The Library's ONE status message and its empty states, chosen by pure functions (F-final-plan §3.5,
// B §3.2 «Состояния»). The first matching priority wins, so the page never stacks banners:
//   1 offline · 2 load error · 3 scoring failed · 4 connecting / slow · 5 scoring runs ·
//   6 receipt of an action · 7 summary · 8 up to date.
// Each result is {id, tone, text, action}: one sentence and at most one action. `tone` is
// 'info' | 'warning' | 'error'; the component spells warning and error out in words.
import { humanizeError } from '../../utils/humanizeError.js';
import { timeAgo } from './shared.jsx';

const say = (id, tone, text, action = null, extra = {}) => ({ id, tone, text, action, ...extra });
const SYNC = { id: 'sync', label: 'Sync now' };
const RETRY = { id: 'retry', label: 'Retry' };
const count = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// The state of the scores in one phrase (the summary line and the jobs panel's Scores section).
export const scoresText = (c) => (!c.modelReady ? 'Ranked by recency — the model is not ready'
  : !c.computedAt ? 'Papers are not scored yet'
    : `Scores updated ${timeAgo(c.computedAt)}${c.scoresStale ? ' (older model)' : ''}`);

function summary(c) {
  const scores = scoresText(c);
  const unscored = c.modelReady && c.unscored > 0 ? `${c.unscored} not scored` : '';
  const pending = c.pending > 0 ? count(c.pending, 'Zotero change') + ' to apply' : '';
  if (c.modelReady && c.computedAt && !c.scoresStale && !unscored && !pending) {
    return say('up_to_date', 'info', 'Scores up to date');
  }
  return say('summary', 'info', [scores, unscored, pending].filter(Boolean).join(' · '),
    pending ? { id: 'pending', label: 'Review changes ›', to: '/ops?tab=pending' } : null);
}

// c: {connection 'ok'|'busy'|'mac'|'checking', slow, loading, offline, rows, err, scoringError, computing,
//     receipt {text, error, warning, busy} (a busy one is a running job's progress: a spinner, nothing to dismiss), modelReady, computedAt, scoresStale, unscored (over the WHOLE queue), pending}
// → the message, or null while the first answer is still out (nothing true to say yet).
export function libraryStatus(c) {
  const rows = c.rows || 0;
  if (!c.loading && (c.offline || c.connection === 'mac')) {
    return say('offline', 'warning', rows > 0
      ? `Mac not reachable — showing ${count(rows, 'paper')} saved on this device. Your verdicts and notes are kept here and sent later.`
      : 'Mac not reachable — no papers are saved on this device yet. Your verdicts and notes are kept here and sent later.', SYNC);
  }
  if (c.err) {
    if (c.err.status === 503) return say('error_busy', 'warning', 'Zotero is busy. Close Zotero, then retry.', RETRY);
    if (c.err.status === 422) {
      return say('error_stale_app', 'error', 'This page needs a newer version of the app. Restart the app, then reload.', { id: 'reload', label: 'Reload' });
    }
    return say('error_load', 'error', `Couldn’t load your list: ${humanizeError(c.err)}${rows > 0 ? ' Showing the last loaded list.' : ''}`, RETRY);
  }
  if (c.scoringError) return say('scoring_failed', 'error', `Scoring failed: ${c.scoringError}`, { id: 'rescore', label: 'Rescore' });
  // The device copy is on screen (or nothing yet) while the first request is out: not «offline», not «model not ready».
  const waiting = c.loading ? c.offline || c.connection !== 'ok' : c.connection === 'checking';
  if (waiting && !c.slow) return say('connecting', 'info', 'Connecting to your Mac…', null, { busy: true });
  if (waiting || c.connection === 'busy') return say('slow', 'warning', 'The app is slow to answer — still trying.', SYNC);
  if (c.loading && rows === 0) return null;
  if (c.computing) return say('computing', 'info', 'Scoring your library… the order updates when it finishes.', null, { busy: true });
  if (c.receipt?.text && c.receipt.busy) return say('receipt', 'info', c.receipt.text, null, { busy: true });
  if (c.receipt?.text) return say('receipt', c.receipt.error ? 'error' : c.receipt.warning ? 'warning' : 'info', c.receipt.text, { id: 'dismiss', label: 'Dismiss' });
  return summary(c);
}

// The six empty lists. Never while the list is loading, failed, offline (the status line speaks) or scoring.
// c: {loading, err, offline, computing, scoringError, rows, rawCount, hasActiveFilters, search, scopeName,
//     includeRead, readHidden, modelReady, computedAt}
export function emptyState(c) {
  if (c.loading || c.err || c.offline || c.computing || c.scoringError || c.rows > 0) return null;
  if (c.hasActiveFilters && c.rawCount > 0) {
    return say('filters', 'info', 'No papers match these filters.', { id: 'clearFilters', label: 'Clear filters' });
  }
  const scope = c.scopeName ? `“${c.scopeName}”` : '';
  if (c.search) return say('search', 'info', `No papers match “${c.search}”${scope ? ` in ${scope}` : ''}.`, { id: 'clearSearch', label: 'Clear search' });
  if (scope) {
    return say('scope', 'info', `Nothing ${c.includeRead ? '' : 'unread '}in ${scope}.`, { id: 'clearScope', label: 'Show all items' });
  }
  if (c.modelReady && !c.computedAt) return say('not_scored', 'info', 'Papers aren’t scored yet.', { id: 'rescore', label: 'Rescore' });
  if (!c.includeRead && c.readHidden > 0) {
    return say('caught_up', 'info', `You’re all caught up — nothing left to decide. ${count(c.readHidden, 'read paper')} hidden.`,
      { id: 'showRead', label: 'Show already-read' });
  }
  return say('empty', 'info', 'No papers yet — add some from Today.', { id: 'today', label: 'Open Today', to: '/' });
}
