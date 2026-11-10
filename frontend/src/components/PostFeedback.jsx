import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { telegramFeedApi } from '../api/researchFeedApi.js';
import ReasonChips from './ReasonChips.jsx';
import { chipClass } from './decisionVocab.js';
import { LIKED_CHIPS } from './telegramRunText.js';

// «Your feedback»: positive chips or «Not a fit» (+ its optional reasons; selecting one clears the other), one note. Stored next to each other
// per discussion and exported with Past digests → Export feedback; nothing here changes ranking yet.
const SAVED_MS = 3000;

export default function PostFeedback({ post, reasons, onReasons, pending }) {
  const cache = useQueryClient();
  const [liked, setLiked] = useState(post.liked || []);
  const [note, setNote] = useState(post.note || '');
  const [saved, setSaved] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  const save = useMutation({ mutationFn: ({ chips, text }) => telegramFeedApi.feedback(post.source_id, chips, text),
    onMutate: () => { clearTimeout(timer.current); setSaved(false); },
    onSuccess: () => {
      cache.invalidateQueries({ predicate: ({ queryKey }) => String(queryKey[0]).startsWith('telegram-') });
      setSaved(true); timer.current = setTimeout(() => setSaved(false), SAVED_MS);
    } });
  const toggle = (id) => {
    const previous = liked, next = liked.includes(id) ? liked.filter((chip) => chip !== id) : [...liked, id];
    setLiked(next);
    save.mutate({ chips: next, text: note }, { onError: () => setLiked(previous) });
    if (next.length && reasons != null) onReasons(null);  // «Not a fit» and a positive chip exclude each other
  };
  const notFit = () => {
    if (reasons == null && liked.length) {
      const previous = liked;
      setLiked([]);
      save.mutate({ chips: [], text: note }, { onError: () => setLiked(previous) });
    }
    onReasons(reasons == null ? [] : null);
  };
  const commit = () => { if (note.trim() !== (post.note || '').trim()) save.mutate({ chips: liked, text: note }); };
  return <div className="space-y-2 rounded-lg bg-slate-50 p-2">
    <p className="text-xs font-medium text-slate-700">Your feedback</p>
    <div className="flex flex-wrap gap-1.5">
      {LIKED_CHIPS.map(([id, label]) => <button key={id} type="button" onClick={() => toggle(id)} aria-pressed={liked.includes(id)}
        className={chipClass(liked.includes(id))}>{label}</button>)}
      <button type="button" disabled={pending} aria-pressed={reasons != null} onClick={notFit}
        className={chipClass(reasons != null)}>Not a fit</button>
    </div>
    <ReasonChips value={reasons} onChange={onReasons} disabled={pending} />
    <label className="block text-xs text-slate-600">Note
      <textarea value={note} maxLength={1000} rows={2} onChange={(event) => setNote(event.target.value)} onBlur={commit}
        className="mt-0.5 block w-full rounded-lg border border-slate-300 bg-white p-2 text-sm text-slate-800" />
    </label>
    {save.isError && <p role="alert" className="text-xs text-rose-700">Could not save: {save.error?.message}</p>}
    {saved && <p role="status" className="text-xs text-slate-600">Saved</p>}
  </div>;
}
