import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { telegramFeedApi } from '../api/researchFeedApi.js';
import Icon from './ui/Icon.jsx';
import TelegramPost from './TelegramPost.jsx';
import TelegramTrends from './TelegramTrends.jsx';

const UNDO_MS = 10000;

// Trends + discussion cards of one digest (the latest run or a saved one): unread first, read ones behind «n read».
// Done marks are stored per discussion, so they hold across digests and reloads.
export default function TelegramFeed({ data }) {
  const cache = useQueryClient();
  const [showRead, setShowRead] = useState(false);
  const [undo, setUndo] = useState(null);
  const posts = data?.posts || [];
  const unread = posts.filter((post) => !post.done);
  const read = posts.filter((post) => post.done);
  useEffect(() => { if (!undo) return undefined; const timer = setTimeout(() => setUndo(null), UNDO_MS); return () => clearTimeout(timer); }, [undo]);
  const mark = useMutation({ mutationFn: ({ ids, done }) => telegramFeedApi.done(ids, done),
    onSuccess: (_result, { ids, done }) => { setUndo(done ? ids : null); cache.invalidateQueries({ predicate: ({ queryKey }) => String(queryKey[0]).startsWith('telegram-') }); } });
  const link = 'inline-flex min-h-11 items-center text-sm text-forest-800 underline underline-offset-4 disabled:opacity-50 sm:min-h-8';
  return <>
    <TelegramTrends trends={data?.trends} />
    <section aria-label="Discussions" className="space-y-1">
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex min-h-11 items-center font-display text-xl font-light text-forest-800">Discussions</h2>
        {unread.length > 0 && <button type="button" disabled={mark.isPending} onClick={() => mark.mutate({ ids: unread.map((post) => post.source_id), done: true })}
          className={link}>{`Mark all read (${unread.length})`}</button>}
      </div>
      {undo && <p role="status" className="flex flex-wrap items-center gap-x-2 text-sm text-slate-600">Marked {undo.length} read ·
        <button type="button" onClick={() => mark.mutate({ ids: undo, done: false })} className={link}>Undo</button></p>}
      {mark.isError && <p role="alert" className="text-sm text-rose-700">Could not save: {mark.error?.message}</p>}
      <ul className="space-y-3">
        {unread.map((post) => <TelegramPost key={post.source_id} post={post} />)}
      </ul>
      {read.length > 0 && <>
        <button type="button" aria-expanded={showRead} onClick={() => setShowRead(!showRead)}
          className="inline-flex min-h-11 items-center gap-1 text-sm text-slate-600 hover:text-forest-800 sm:min-h-8">
          <Icon name="chevron" size={14} className={`transition-transform ${showRead ? 'rotate-90' : ''}`} />{read.length} read</button>
        {showRead && <ul className="space-y-1.5">{read.map((post) => <TelegramPost key={post.source_id} post={post} />)}</ul>}
      </>}
    </section>
  </>;
}
