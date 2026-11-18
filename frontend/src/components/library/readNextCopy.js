// Plain-language copy for the Read-next list — pure, so the grouping and the
// stale-score sentence stay in one place.
import { formatShortDate, timeAgo } from './shared.jsx';

// Rows the owner already judged (own verdict). Rows from an older cache / the
// offline working set carry no `decided` flag; a bare user_priority then stands in.
export const isDecided = (row) => row.decided ?? Boolean(row.user_priority);

// The default list in three groups, computed here from each row's own verdict so it
// also holds for the offline working set (no `decided` flag, no server `rank_group`):
// your own Must read · still to decide · other verdicts of yours. Each group keeps the
// incoming order (the server / Order preset already puts the groups in this sequence).
export function groupRows(items) {
  const groups = { must: [], todo: [], decided: [] };
  for (const row of items) {
    if (!isDecided(row)) groups.todo.push(row);
    else (row.user_priority === 'must_read' ? groups.must : groups.decided).push(row);
  }
  return groups;
}

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

// ---- Order presets (Best match only) --------------------------------------
export const ORDER_INFO = 'Changes only this list. Zotero rank numbers, “Review top N” and Today keep their own '
  + 'order (relevance + goals + prestige, without your verdict groups).';

const pct = (share) => `${Math.round(share * 100)}%`;
const SIGNAL_NAME = { relevance: 'relevance', goals: 'goals', quality: 'rigor', prestige: 'prestige' };

// "known for 42 of 2012" — only when the signal is not known for every scored paper.
export function coverageNote(meta, signal) {
  const c = meta?.coverage?.[signal];
  if (!c || c.present >= c.of) return '';
  return c.present > 0 ? `known for ${c.present} of ${c.of}` : 'no data in this list';
}

// The always-visible state line: which shares are in force and how many papers know each.
export function orderCaption(label, shares, meta, balanced) {
  const parts = Object.keys(SIGNAL_NAME).filter((s) => shares[s] > 0).map((s) => {
    const note = coverageNote(meta, s);
    return `${SIGNAL_NAME[s]} ${pct(shares[s])}${note ? ` (${note})` : ''}`;
  });
  if (balanced) parts.push('rigor lifts within its ★ band');
  return [label, ...parts].join(' · ');
}

// Tooltip of the rigor-letter chip: «within the relevance band, never across it» holds for Balanced only.
export function qualityHint(balanced, base) {
  return balanced
    ? `${base} — floats papers with stronger rigor up within their relevance band, never across it.`
    : `${base} — counts as a full weight in this order (its share of passed checklist items), not limited to a relevance band.`;
}

export const GROUP_LINE = {
  0: () => 'Group: your own Must read (listed first)',
  1: (must) => `Group: no decision yet${must > 0 ? ` (after your ${must} Must read)` : ''}`,
  2: () => 'Group: already decided by you (listed last)',
};
