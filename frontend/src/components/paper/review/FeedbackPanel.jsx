import { useEffect, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { saveReviewFeedbackPart, withdrawReviewFeedbackPart } from '../../../api/libraryApi.js';
import { chipClass } from '../../decisionVocab.js';
import { PART_LABELS, REVIEW_FEEDBACK_CHIPS, receiptOf } from './reviewFeedbackFields.js';

const clip = (text, max) => (text.length > max ? `${text.slice(0, max)}…` : text);

// The reasons + note form for ONE part of the review. A non-modal dialog (the page behind stays readable and
// scrollable): a bottom sheet on phones, a card at `place` (next to the part) on wide screens. Reasons and note are
// kept until Save; the text the part showed is recorded by the server, not sent from here.
export default function FeedbackPanel({ itemKey, reviewedAt, field, entry, preview, place, onSaved, onClose }) {
  const [chips, setChips] = useState(entry?.chips || []);
  const [note, setNote] = useState(entry?.note || '');
  const root = useRef(null);
  useEffect(() => { root.current?.focus(); }, []);
  const save = useMutation({
    mutationFn: () => saveReviewFeedbackPart(itemKey, field, reviewedAt, chips, note.trim()),
    onSuccess: (data) => onSaved(data, `Saved · ${receiptOf(field, data.feedback.fields?.[field] || data.feedback)}`),
  });
  const withdraw = useMutation({
    mutationFn: () => withdrawReviewFeedbackPart(itemKey, field, reviewedAt),
    onSuccess: (data) => onSaved(data, `Withdrawn · ${PART_LABELS[field]}`),
  });
  const toggle = (id) => setChips((now) => {
    // «Good as is» and a problem exclude each other.
    const rest = now.filter((chip) => chip !== id && (id === 'good_as_is') === (chip === 'good_as_is'));
    return now.includes(id) ? rest : [...rest, id];
  });
  const error = save.error || withdraw.error;
  const busy = save.isPending || withdraw.isPending;
  return <div ref={root} tabIndex={-1} role="dialog" aria-modal="false" aria-label={`Feedback on ${PART_LABELS[field]}`}
    style={place ? { top: place.top, left: place.left } : undefined}
    className={`fixed z-40 space-y-3 overflow-y-auto border border-slate-300 bg-white p-4 shadow-xl focus:outline-none ${place
      ? 'max-h-[calc(100vh-6rem)] w-[24rem] rounded-xl' : 'inset-x-0 bottom-0 max-h-[80vh] rounded-t-2xl lg:inset-x-auto lg:bottom-6 lg:right-6 lg:w-[24rem] lg:rounded-xl'}`}>
    <div className="flex items-start justify-between gap-3">
      <h2 className="font-display text-[19px] font-light leading-snug text-forest-800">{PART_LABELS[field]}</h2>
      <button type="button" onClick={onClose} className="min-h-11 shrink-0 px-2 text-[13px] text-slate-600 underline underline-offset-2">Cancel</button>
    </div>
    {(entry?.shown || preview) && <figure className="rounded-lg bg-slate-50 p-2 text-[12px] leading-snug text-slate-600">
      <figcaption className="mb-0.5 text-[11px] font-medium uppercase tracking-[0.08em] text-slate-500">
        {entry?.shown ? 'Saved with your feedback' : 'This part, on screen'}
      </figcaption>
      <blockquote className="whitespace-pre-line">{clip(entry?.shown || preview, 320)}</blockquote>
    </figure>}
    <div className="flex flex-wrap gap-1.5">
      {REVIEW_FEEDBACK_CHIPS.map(([id, label]) => <button key={id} type="button" onClick={() => toggle(id)}
        aria-pressed={chips.includes(id)} className={`min-h-9 ${chipClass(chips.includes(id))}`}>{label}</button>)}
    </div>
    <label className="block text-xs text-slate-600">Note (optional)
      <textarea value={note} maxLength={1000} rows={2} onChange={(event) => setNote(event.target.value)}
        className="mt-0.5 block w-full rounded-lg border border-slate-300 bg-white p-2 text-sm text-slate-800" />
    </label>
    {error && <p role="alert" className="text-xs text-rose-700">Could not save: {error.message}</p>}
    <div className="flex flex-wrap items-center justify-end gap-2">
      {entry && <button type="button" onClick={() => withdraw.mutate()} disabled={busy}
        className="mr-auto min-h-11 px-2 text-[13px] text-rose-700 underline underline-offset-2 disabled:opacity-50">Withdraw</button>}
      <button type="button" onClick={() => save.mutate()} disabled={busy || !(chips.length || note.trim())}
        className="min-h-11 rounded-lg bg-forest-800 px-5 text-[14px] font-medium text-white disabled:opacity-40">
        {save.isPending ? 'Saving…' : 'Save'}</button>
    </div>
  </div>;
}
