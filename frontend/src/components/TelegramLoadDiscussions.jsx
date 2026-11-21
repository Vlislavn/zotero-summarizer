import { useState } from 'react';
import Button from './ui/Button.jsx';
import Icon from './ui/Icon.jsx';
import Spinner from './ui/Spinner.jsx';
import TelegramLink from './TelegramLink.jsx';
import TelegramThread from './TelegramThread.jsx';
import { headlineOf } from './TelegramPost.jsx';
import { AuthCommand } from './TelegramRunButton.jsx';
import { AUTH_CODES, NEEDS_CONSENT, NO_REPLIES, commentsText, loadConfirm, loadNote, loadingText, postsText } from './telegramRunText.js';

// «Load discussions» in an opened trend: reads the comments of its most replied posts through the owner's Telegram session
// (one confirmation per press, nothing is sent, no model), then lists them in a collapsed block with a «Telegram ↗» link each.
// Posts that already have a discussion card are linked above, so the block lists the others. Hidden in saved digests.
export default function LoadDiscussions({ topic, cards, run }) {
  const [open, setOpen] = useState(false);
  const info = topic.discussion;
  if (!info || !run) return null;
  const { value, load, running, needsConsent } = run;
  const mine = value?.kind === 'discussions' && value.topic === topic.id;
  const pressed = load.variables === topic.id;  // one mutation serves every trend: only the pressed one shows its state
  const loading = (load.isPending && pressed) || (running && mine);
  const withCard = new Set(cards.map((post) => post.source_id));
  const loaded = info.loaded.filter((post) => !withCard.has(post.source_id));
  const note = loadNote(value, topic.id, pressed && load.isError ? load.error : null);
  const press = () => { if (window.confirm(loadConfirm(info.loadable))) load.mutate(topic.id); };
  const hint = 'text-sm text-slate-600';
  return <div className="space-y-1">
    {info.loadable === 0 && <p className={hint}>{NO_REPLIES}</p>}
    {info.loadable > 0 && <Button size="sm" variant="secondary" className="min-h-11 sm:min-h-8" disabled={running || load.isPending || needsConsent} onClick={press}>
      {loading ? <span className="inline-flex items-center gap-2"><Spinner size="xs" color="teal" />{loadingText(mine ? value.progress : null)}</span> : 'Load discussions'}</Button>}
    {info.loadable > 0 && needsConsent && <p className={hint}>{NEEDS_CONSENT}</p>}
    {info.loadable > 0 && running && !mine && <p className={hint}>Wait for the running Telegram action to finish.</p>}
    {!loading && note && <div role={note.code ? 'alert' : 'status'} className="space-y-2 text-sm text-amber-800"><p>{note.text}</p>{AUTH_CODES.includes(note.code) && <AuthCommand />}</div>}
    {loaded.length > 0 && <details className="group" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-1 text-slate-700 sm:min-h-8 [&::-webkit-details-marker]:hidden">
        <Icon name="chevron" size={14} className="transition-transform group-open:rotate-90" />
        Discussions · {postsText(loaded.length)} · {commentsText(loaded.reduce((sum, post) => sum + post.comments, 0))}</summary>
      {open && <ul className="space-y-3 pb-1">{loaded.map((post) => <li key={post.source_id}>
        <TelegramLink href={post.permalink} className="inline-flex min-h-11 items-center text-slate-700 underline decoration-slate-300 underline-offset-2 hover:text-forest-800 sm:min-h-8">
          {headlineOf(post.text, post.channel) || 'No text'} · @{post.channel}</TelegramLink>
        <TelegramThread sourceId={post.source_id} />
      </li>)}</ul>}
    </details>}
  </div>;
}
