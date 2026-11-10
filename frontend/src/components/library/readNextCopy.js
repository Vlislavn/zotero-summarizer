// Plain-language status copy for the Read-next toolbar — pure, so the counts
// and the stale-score sentence stay in one place.
import { formatShortDate, timeAgo } from './shared.jsx';

// Rows the owner already judged (own verdict). Rows from an older cache / the
// offline working set carry no `decided` flag; a bare user_priority then stands in.
export const isDecided = (row) => row.decided ?? Boolean(row.user_priority);

// One consistent set of counts: queue = unread library papers; the rest are
// subsets or hidden. `shown` differs from the queue size only under client filters.
export function queueCounts({ totalUnread, decidedCount, readHidden, includeRead, shown, filtered }) {
  const undecided = Math.max(0, totalUnread - (decidedCount || 0));
  const parts = [`${undecided} to decide`];
  if (decidedCount > 0) parts.push(`${decidedCount} already decided by you (last)`);
  if (readHidden > 0 && !includeRead) parts.push(`${readHidden} read hidden`);
  if (filtered) parts.push(`${shown} match your filters`);
  return parts;
}

export const COUNTS_EXPLAINER = 'Queue = unread library papers (ones you judged come last; read ones are '
  + 'hidden). Select & label = papers you have read. The header’s “cached” = the offline copy.';

// Where the ranking comes from, stated next to Rescore: when, and whether the
// model has changed since (stale) — plus how many rows have no score at all.
export function scoreStatus({ computedAt, scoresStale, unscored }) {
  if (!computedAt) return 'Papers are not scored yet.';
  const when = `Scores from ${formatShortDate(computedAt)} (${timeAgo(computedAt)})`;
  const extra = unscored > 0 ? `; ${unscored} not scored` : '';
  return scoresStale
    ? `${when} — computed by an older model, so the order may be out of date${extra}.`
    : `${when}${extra}.`;
}
