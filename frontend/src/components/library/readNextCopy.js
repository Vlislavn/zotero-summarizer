// Plain-language copy for the Read-next list — pure, so the grouping and the
// stale-score sentence stay in one place.
import { formatShortDate, timeAgo, truncateAuthors } from './shared.jsx';
import { pretty } from '../../utils/priorityLabels.js';

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

export const GROUP_LINE = {
  0: () => 'Group: your own Must read (listed first)',
  1: (must) => `Group: no decision yet${must > 0 ? ` (after your ${must} Must read)` : ''}`,
  2: () => 'Group: already decided by you (listed last)',
};

// ---- One row (ReadNextRow) --------------------------------------------------
// Rigor wording (quality-letter-memo.md, rule D for A): a flag always shows; an A is
// only plain when the review also found no critical gap (band highlight), else
// «checklist only». The letter never reorders the list. `tone` picks the ink.
export function rigorLabel(row) {
  const grade = String(row.quality_grade || '').toUpperCase();
  const band = String(row.quality_band || '').toLowerCase();
  if (band === 'flag') return { text: `Rigor${grade ? ` ${grade}` : ''} · flagged`, tone: 'flag' };
  if (!grade) return null;
  if (grade !== 'A') return { text: `Rigor ${grade}`, tone: 'plain' };
  return band === 'highlight'
    ? { text: 'Rigor A', tone: 'good' }
    : { text: 'Rigor A · checklist only', tone: 'plain' };
}

// The review's reading-depth advice; only read / skim are worth a word on the row.
export function readLabel(row) {
  const word = { read: 'Read', skim: 'Skim' }[row.read_decision];
  if (!word) return null;
  const min = Number(row.estimated_read_minutes);
  return Number.isFinite(min) && min > 0 ? `${word} ${Math.round(min)} min` : word;
}

export const hasReview = (row) => Boolean(
  row.quality_grade || row.quality_band || row.review_digest?.tldr || row.read_decision,
);

// The signal line, in a fixed order: ★ · Goals strong · Rigor · Code · Skim/Read N min.
// `goalsStrong` is passed only when the cohort is the full list (see ReadNextView);
// every field is optional, so an offline row or a row without a review still renders.
export function rowSignals(row, { goalsStrong = false, modelReady = true } = {}) {
  const out = [];
  if (typeof row.relevance_score === 'number') out.push({ key: 'score', text: `★ ${row.relevance_score.toFixed(1)}`, tone: 'score' });
  else if (modelReady) out.push({ key: 'score', text: 'not scored yet', tone: 'muted' });
  if (goalsStrong) out.push({ key: 'goals', text: 'Goals strong', tone: 'plain' });
  const rigor = rigorLabel(row);
  if (rigor) out.push({ key: 'rigor', ...rigor });
  else if (!hasReview(row)) out.push({ key: 'rigor', text: 'Not reviewed', tone: 'muted' });
  if (row.has_code === true) out.push({ key: 'code', text: 'Code', tone: 'plain' });
  const depth = readLabel(row);
  if (depth) out.push({ key: 'depth', text: depth, tone: 'plain' });
  return out;
}

// The ONE state label of a row. Your own verdict wins over the review's suggestion; a row
// in the Must group says nothing (its header does). `dont_read` is named too (Decided group).
export function stateLabel(row, ownChip) {
  if (isDecided(row) && row.user_priority) {
    if (!ownChip) return null;
    return { kind: 'own', text: `You: ${row.user_priority === 'dont_read' ? 'Not interested' : pretty(row.user_priority)}` };
  }
  const proposal = row.proposed_verdict;
  if (!row.user_priority && proposal?.proposed) {
    const note = (proposal.flags || []).some(Boolean)
      ? 'flagged'
      : `${Math.round((typeof proposal.confidence === 'number' ? proposal.confidence : 0) * 100)}%`;
    return { kind: 'proposal', text: `◇ Review suggests: ${pretty(proposal.proposed)} · ${note}` };
  }
  return null;
}

// «Authors · Venue» and the year, for the footer. A feed query string is no venue.
export function rowByline(row) {
  const venue = /^arxiv\b/i.test(row.venue || '') ? 'arXiv' : (row.venue || '').trim();
  const year = /^\d{4}$/.test(String(row.year || '')) ? String(row.year) : String(row.publication_date || '').slice(0, 4);
  return {
    byline: [truncateAuthors(row.authors), venue].filter(Boolean).join(' · '),
    year: /^\d{4}$/.test(year) ? year : '',
  };
}
