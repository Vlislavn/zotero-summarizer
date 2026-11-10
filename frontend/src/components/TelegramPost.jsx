import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { telegramFeedApi } from '../api/researchFeedApi.js';
import Icon from './ui/Icon.jsx';
import PostFeedback from './PostFeedback.jsx';
import TelegramLink from './TelegramLink.jsx';
import TelegramThread from './TelegramThread.jsx';
import { ago, commentsText, feedbackText } from './telegramRunText.js';

const HEADLINE_CHARS = 110;
const BULLETS = 3;

// «[c123]» citations and list markers are bookkeeping, not text; at most 3 lines (old paragraph gists are split the same way).
const bullets = (text) => (text || '').replace(/\s*\[c\d+\]/g, '').split('\n')
  .map((line) => line.replace(/^\s*(?:[-*•–—]|\d+[.)])\s*/, '').trim()).filter(Boolean).slice(0, BULLETS);

// The first line of the post, cut at a word boundary (never mid-word) so it reads as one headline of ≤2 lines.
function headlineOf(text) {
  const line = (text || '').split('\n').find((row) => row.trim())?.trim() || '';
  if (line.length <= HEADLINE_CHARS) return line;
  const cut = line.slice(0, HEADLINE_CHARS + 1);
  const space = cut.lastIndexOf(' ');
  return `${(space > HEADLINE_CHARS / 2 ? cut.slice(0, space) : cut.slice(0, HEADLINE_CHARS)).replace(/[\s,;:.\-–—]+$/, '')}…`;
}

// «↳ reply to …»: the start of the line whose comment this one answers (pure reply structure, no model involved).
function replyTarget(points, id) {
  const words = (points.find((point) => point.cites.includes(id))?.text || 'a comment above').split(/\s+/);
  return words.length > 6 ? `${words.slice(0, 6).join(' ')}…` : words.join(' ');
}

// A quiet disclosure: grey text + a chevron that turns when open (≥44 px tap target on phones).
function Disclosure({ open, onToggle, children }) {
  return <button type="button" aria-expanded={open} onClick={onToggle}
    className="inline-flex min-h-11 items-center gap-1 text-sm text-slate-600 hover:text-forest-800 sm:min-h-8">
    <Icon name="chevron" size={14} className={`transition-transform ${open ? 'rotate-90' : ''}`} />{children}
  </button>;
}

// One discussion card: headline, 2–3 terse bullets (the thread's own language), meta line, quiet Highlights / Details.
// The feedback group («Not a fit» is stored server-side, optimistic here) lives under Details.
export default function TelegramPost({ post }) {
  const cache = useQueryClient();
  const [reasons, setReasons] = useState(post.reasons ?? null);
  const [open, setOpen] = useState(null);
  const [openPoint, setOpenPoint] = useState(null);
  const save = useMutation({ mutationFn: (chips) => telegramFeedApi.reasons(post.source_id, chips),
    onMutate: (chips) => { const previous = reasons; setReasons(chips); return { previous }; },
    onError: (_error, _chips, context) => setReasons(context.previous),
    onSuccess: () => cache.invalidateQueries({ queryKey: ['telegram-today'] }) });
  const mark = useMutation({ mutationFn: (done) => telegramFeedApi.done([post.source_id], done),
    onSuccess: () => cache.invalidateQueries({ predicate: ({ queryKey }) => String(queryKey[0]).startsWith('telegram-') }) });
  const done = mark.isPending ? mark.variables : post.done;
  const discussion = post.discussion;
  const points = discussion?.points?.length ? discussion.points : bullets(discussion?.summary).map((text) => ({ text, cites: [] }));
  const lines = points.map((point) => point.text);
  const headline = headlineOf(post.text) || lines[0] || 'No text';
  const important = discussion?.important || [];
  const rows = (post.text || '').split('\n');
  const first = rows.findIndex((line) => line.trim());
  const body = (first >= 0 && rows[first].trim() === headline ? rows.slice(first + 1) : rows).join('\n').trim();  // the headline is not repeated
  const toggle = (name) => setOpen(open === name ? null : name);
  if (done) return <li className="flex min-h-11 items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-1.5 text-sm text-slate-600">
    <Icon name="check" size={14} /><span className="min-w-0 flex-1 truncate">{headline} · @{post.channel}{feedbackText(post, reasons) && ` · ${feedbackText(post, reasons)}`}</span>
    <button type="button" onClick={() => mark.mutate(false)} className="min-h-11 text-forest-800 underline underline-offset-4 sm:min-h-8">Undo</button>
  </li>;
  return <li className={`rounded-xl border border-slate-200 px-4 pb-1 pt-3.5 ${reasons ? 'bg-slate-50' : 'bg-white'}`}>
    {post.continues && <p className="mb-1 break-words text-xs text-slate-600">Continues <TelegramLink href={post.continues.permalink}>@{post.continues.channel}: {headlineOf(post.continues.text)}</TelegramLink></p>}
    <h3 className="break-words text-base font-medium leading-snug text-slate-900">{headline}</h3>
    {discussion && (points.length
      ? <ul className="mt-2 space-y-1 text-sm leading-snug text-slate-700">
        {points.map((point, index) => <li key={index} className="relative break-words pl-3.5 before:absolute before:left-0.5 before:top-[0.55em] before:h-1 before:w-1 before:rounded-full before:bg-slate-400">
          {point.reply_to && <span className="block text-xs text-slate-600">↳ reply to “{replyTarget(points, point.reply_to)}”</span>}
          {point.kind === 'disagreement' && <span className="mr-1 text-xs font-medium text-slate-700">Disagreement</span>}
          {point.text}
          {point.cites.length > 0 && <button type="button" aria-expanded={openPoint === index} aria-label={`Comments behind point ${index + 1}`}
            onClick={() => setOpenPoint(openPoint === index ? null : index)} className="relative ml-1 inline-flex items-center gap-0.5 align-baseline text-xs text-slate-600 before:absolute before:-inset-3 before:content-[''] hover:text-forest-800 sm:before:-inset-1">
            <Icon name="chevron" size={12} className={`transition-transform ${openPoint === index ? 'rotate-90' : ''}`} />{point.cites.length}</button>}
          {openPoint === index && <div className="mt-1"><TelegramThread sourceId={post.source_id} ids={point.cites} /></div>}
        </li>)}
      </ul>
      : <p className="mt-2 text-sm text-slate-600">No summary.</p>)}
    <div className="mt-1 flex items-center gap-x-3">
      <p className="flex min-w-0 flex-1 flex-wrap items-center gap-x-1.5 text-xs text-slate-600">
        <span className="font-medium text-slate-700">@{post.channel || 'channel'}</span>
        <span aria-hidden="true">·</span><time dateTime={post.published_at}>{ago(post.published_at)}</time>
        {/* With comments, the count is the “All comments (N)” label below — said once. */}
        {discussion && !discussion.comment_count && <><span aria-hidden="true">·</span><span>{commentsText(0)}</span></>}
        {feedbackText(post, reasons) && <><span aria-hidden="true">·</span><span>{feedbackText(post, reasons)}</span></>}
      </p>
      <button type="button" disabled={mark.isPending} onClick={() => mark.mutate(true)}
        className="inline-flex min-h-11 shrink-0 items-center gap-1 text-sm text-slate-600 hover:text-forest-800 disabled:opacity-50 sm:min-h-0"><Icon name="check" size={14} />Done</button>
    </div>
    {post.also_in && <p className="mt-1 break-words text-xs text-slate-600">Also in: {post.also_in.label} · {post.also_in.channels} {post.also_in.channels === 1 ? 'channel' : 'channels'}</p>}
    <div className="flex flex-wrap gap-x-4">
      {important.length > 0 && <Disclosure open={open === 'highlights'} onToggle={() => toggle('highlights')}>{`Highlights (${important.length})`}</Disclosure>}
      {discussion?.comment_count > 0 && <Disclosure open={open === 'comments'} onToggle={() => toggle('comments')}>{`All comments (${discussion.comment_count})`}</Disclosure>}
      <Disclosure open={open === 'details'} onToggle={() => toggle('details')}>Details</Disclosure>
    </div>
    {open === 'highlights' && <ul className="space-y-1.5 pb-3 text-sm">
      {important.map((item) => <li key={item.id} className="break-words border-l-2 border-sage-300 pl-3 text-slate-700">“{item.quote}”</li>)}
    </ul>}
    {open === 'comments' && <TelegramThread sourceId={post.source_id} />}
    {open === 'details' && <div className="space-y-3 pb-3 text-sm">
      {body && <p className="whitespace-pre-wrap break-words text-slate-800">{body}</p>}
      <p><TelegramLink href={post.permalink}>Open post</TelegramLink></p>
      <PostFeedback post={post} reasons={reasons} onReasons={save.mutate} pending={save.isPending} />
      {save.isError && <p role="alert" className="text-rose-700">Could not save: {save.error?.message}</p>}
    </div>}
  </li>;
}
