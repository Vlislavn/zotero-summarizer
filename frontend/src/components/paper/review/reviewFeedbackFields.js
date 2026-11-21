// The parts of a review the owner can give feedback on. Ids are the backend's (services/library/review_feedback_bundle.py
// PARTS, plus `whole`); labels are the page's own words where it has them. A part of the page carries
// `data-feedback-field="<id>"` (spread `feedbackField(id)`); FeedbackMode finds them by that attribute.
export const WHOLE = 'whole';
const FEEDBACK_PARTS = [
  ['verdict', 'Verdict'], ['top_sentence', 'Top sentence'], ['quality', 'Rigor'], ['red_flags', 'Red flags'],
  ['metrics', 'Metrics'], ['strength_weakness', 'Strength / Weakness'], ['reading_plan', 'Reading plan'],
  ['findings', 'Findings and applicability'], ['assessment', 'Assessment'], ['relevance', 'Relevance'],
  ['methods_limits', 'Methods and limitations'], ['code_link', 'Code link'],
];
export const PART_LABELS = { ...Object.fromEntries(FEEDBACK_PARTS), [WHOLE]: 'Whole review' };

const feedbackField = (id) => ({ 'data-feedback-field': id });

// Reasons are the backend's (services/library/review_feedback.py CHIPS); good_as_is leads, the rest are problems.
export const REVIEW_FEEDBACK_CHIPS = [
  ['good_as_is', 'Good as is'], ['methods_not_useful', 'Methods not useful'],
  ['wrong_or_unsupported_fact', 'Wrong or unsupported fact'], ['flags_noise', 'Flags are noise'],
  ['too_long', 'Too long'], ['missing_code_or_data', 'Missing code or data'],
  ['wrong_read_depth', 'Wrong read advice'], ['grade_unclear', 'Grade unclear'],
];
const CHIP_LABELS = Object.fromEntries(REVIEW_FEEDBACK_CHIPS);

/** The owner's saved feedback of one review version as `{ part: { chips, note, shown } }` (withdrawn parts left out). */
export function entriesOf(row) {
  if (!row) return {};
  const parts = Object.fromEntries(Object.entries(row.fields || {}).filter(([, entry]) => !entry.withdrawn));
  if (row.chips?.length || row.note) parts[WHOLE] = { chips: row.chips || [], note: row.note || '', shown: '' };
  return parts;
}

/** «Verdict · Flags are noise» — the one-line receipt after saving. */
export function receiptOf(field, entry) {
  const reasons = entry.chips.map((id) => CHIP_LABELS[id]).filter(Boolean);
  return [PART_LABELS[field], ...(reasons.length > 2 ? [`${reasons.length} reasons`] : reasons), ...(entry.note ? ['note'] : [])].join(' · ');
}
