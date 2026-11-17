import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchReviewFeedback, saveReviewFeedback } from '../../../api/libraryApi.js';
import { chipClass } from '../../decisionVocab.js';
import { Fold } from './primitives.jsx';

// «Feedback on this review»: one closed fold under the review (user-study instrument). Chips save on tap, the
// note on blur; neither is required. The backend keeps one row per paper and review version, so the page mounts
// this with key={reviewed_at}: a regenerated review starts empty and the old row stays stored.
// Ids are the backend's (services/library/review_feedback.py CHIPS); good_as_is leads, the rest are problems.
const REVIEW_FEEDBACK_CHIPS = [
  ['good_as_is', 'Good as is'], ['methods_not_useful', 'Methods not useful'],
  ['wrong_or_unsupported_fact', 'Wrong or unsupported fact'], ['flags_noise', 'Flags are noise'],
  ['too_long', 'Too long'], ['missing_code_or_data', 'Missing code or data'],
  ['wrong_read_depth', 'Wrong read advice'], ['grade_unclear', 'Grade unclear'],
];

const queryKey = (itemKey) => ['review-feedback', itemKey];

function summaryOf(row) {
  if (!row) return 'Optional — tap what was off, or “Good as is”';
  const labels = Object.fromEntries(REVIEW_FEEDBACK_CHIPS);
  const parts = row.chips.length === 1 ? [labels[row.chips[0]]] : row.chips.length ? [`${row.chips.length} issues`] : [];
  if (row.note) parts.push('note');
  return <><span className="font-medium text-forest-800">Saved</span> · {parts.join(' + ')}</>;
}

function FeedbackForm({ itemKey, reviewedAt, row }) {
  const cache = useQueryClient();
  const [chips, setChips] = useState(row?.chips || []);
  const [note, setNote] = useState(row?.note || '');
  const save = useMutation({
    mutationFn: ({ next, text }) => saveReviewFeedback(itemKey, next, text),
    onSuccess: (data) => cache.setQueryData(queryKey(itemKey), (old) => {
      const rows = (old?.feedback || []).filter((entry) => entry.reviewed_at !== reviewedAt);
      return { ...old, item_key: itemKey, feedback: data.cleared ? rows : [...rows, data.feedback] };
    }),
  });
  const post = (next, text, onError) => save.mutate({ next, text }, { onError });
  const toggle = (id) => {
    const previous = chips;
    // «Good as is» and a problem exclude each other.
    const without = chips.filter((chip) => chip !== id && (id === 'good_as_is') === (chip === 'good_as_is'));
    const next = chips.includes(id) ? without : [...without, id];
    setChips(next);
    post(next, note, () => setChips(previous));
  };
  const commit = () => { if (note.trim() !== (row?.note || '').trim()) post(chips, note); };
  return <div className="space-y-3">
    <div className="flex flex-wrap gap-1.5">
      {REVIEW_FEEDBACK_CHIPS.map(([id, label]) => <button key={id} type="button" onClick={() => toggle(id)}
        aria-pressed={chips.includes(id)} className={chipClass(chips.includes(id))}>{label}</button>)}
    </div>
    <label className="block text-xs text-slate-600">Note (optional)
      <textarea value={note} maxLength={1000} rows={2} onChange={(event) => setNote(event.target.value)} onBlur={commit}
        className="mt-0.5 block w-full rounded-lg border border-slate-300 bg-white p-2 text-sm text-slate-800" />
    </label>
    <div className="min-h-4 text-xs text-slate-600" aria-live="polite">
      {save.isPending ? 'Saving…' : save.isError
        ? <span role="alert" className="text-rose-700">Could not save: {save.error?.message}</span>
        : note.trim() !== (row?.note || '').trim() ? 'The note saves when you leave the field'
          : row ? 'Saved' : save.isSuccess && 'Cleared'}
    </div>
  </div>;
}

export default function ReviewFeedback({ itemKey, reviewedAt }) {
  const query = useQuery({ queryKey: queryKey(itemKey), queryFn: () => fetchReviewFeedback(itemKey), staleTime: 60_000 });
  const row = (query.data?.feedback || []).find((entry) => entry.reviewed_at === reviewedAt) || null;
  const summary = query.isError ? 'Could not load saved feedback' : query.isPending ? 'Loading…' : summaryOf(row);
  return <Fold id="review-feedback" label="Feedback on this review" summary={summary}>
    {query.isError
      ? <p role="alert" className="text-xs text-rose-700">Could not load saved feedback: {query.error?.message}</p>
      : query.isSuccess && <FeedbackForm itemKey={itemKey} reviewedAt={reviewedAt} row={row} />}
  </Fold>;
}
