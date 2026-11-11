import { useQuery } from '@tanstack/react-query';
import { telegramFeedApi } from '../api/researchFeedApi.js';

// A thread of comments in message order, full text, fetched when this opens (the digest ships only the summary points).
// A reply shows who it answers (the parent's first words); `ids` limits the list to cited comments (a summary point),
// otherwise the whole thread is shown. The key is not 'telegram-*': saving feedback must not refetch every open thread.
const PARENT_CHARS = 70;

export default function TelegramThread({ sourceId, ids }) {
  const { data, isError, error } = useQuery({ queryKey: ['tg-thread', sourceId], queryFn: () => telegramFeedApi.thread(sourceId), staleTime: Infinity });
  if (isError) return <p role="alert" className="pb-2 text-sm text-rose-700">Could not load the comments: {error?.message}</p>;
  if (!data) return <p role="status" className="pb-2 text-sm text-slate-600">Loading comments…</p>;
  const thread = data.thread;
  const byId = new Map(thread.map((row) => [row.id, row]));
  const rows = ids ? ids.map((id) => byId.get(id)).filter(Boolean).sort((a, b) => thread.indexOf(a) - thread.indexOf(b)) : thread;
  if (!rows.length) return <p className="pb-2 text-sm text-slate-600">The cited comments are not stored.</p>;
  return <ol className="space-y-2 pb-3 text-sm">
    {rows.map((row) => {
      const parent = row.reply_to ? byId.get(row.reply_to) : null;
      return <li key={row.id} className="break-words border-l-2 border-sage-300 pl-3 text-slate-800">
        {parent && <span className="mb-0.5 block text-xs text-slate-600">↳ {parent.text.slice(0, PARENT_CHARS)}{parent.text.length > PARENT_CHARS ? '…' : ''}</span>}
        <span className="whitespace-pre-wrap">{row.text}</span>
      </li>;
    })}
  </ol>;
}
