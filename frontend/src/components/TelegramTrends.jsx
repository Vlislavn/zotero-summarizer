import { useState } from 'react';
import Icon from './ui/Icon.jsx';
import TelegramLink from './TelegramLink.jsx';
import { cardId, headlineOf } from './TelegramPost.jsx';
import { channelsText, postsText } from './telegramRunText.js';

// Plain-word marker pills (tooltip = what it means; the same words are in the page's info panel).
// «steady» has no marker — nothing changed, so there is nothing to explain.
const MARKERS = {
  growing: { text: '↗ more discussion', means: 'more posts on the topic than a week earlier', tone: 'bg-sage-100 text-forest-800' },
  new: { text: 'new', means: 'almost nothing on it a week earlier', tone: 'bg-mist-100 text-mist-800' },
  hot: { text: 'hot thread', means: 'a single discussion that blew up', tone: 'bg-amber-100 text-amber-800' },
};
const SHOWN = 5;
const upper = (text = '') => text.charAt(0).toUpperCase() + text.slice(1);

// «12 posts in 6 channels this week (11 the week before) · @a, @b, @c and 3 more» — where the trend comes from, always visible.
function Provenance({ topic }) {
  const names = (topic.channels || []).map((name) => `@${name}`).join(', ');
  const rest = topic.channel_count - (topic.channels || []).length;
  const before = topic.baseline_posts > 0 ? ` (${topic.baseline_posts} the week before)` : '';
  return <span className="mt-1 block break-words text-xs text-slate-600">{postsText(topic.posts)} in {channelsText(topic.channel_count)} this week{before}
    {names && ` · ${names}`}{rest > 0 && ` and ${rest} more`}</span>;
}

// The posts behind the trend: the ones with a discussion card link to it on this page, the rest open in Telegram.
function Behind({ topic, cards, onOpenCard }) {
  const ids = topic.source_ids;
  const linked = new Set(cards.map((post) => post.permalink));
  const others = (topic.examples || []).filter((example) => !linked.has(example.permalink)).slice(0, 3);
  return <div className="space-y-1.5">
    {ids && <p>{cards.length > 0 ? `${cards.length} of ${postsText(ids.length)} ${cards.length === 1 ? 'has' : 'have'} a discussion card:` : 'No comments collected for these posts yet.'}</p>}
    {cards.length > 0 && <ul className="space-y-0.5">{cards.map((post) => <li key={post.source_id} className="break-words">
      <a href={`#${cardId(post.source_id)}`} onClick={(event) => { event.preventDefault(); onOpenCard(post.source_id); }}
        className="inline-flex min-h-11 items-center text-forest-800 underline underline-offset-2 sm:min-h-8">{headlineOf(post.text, post.channel) || 'No text'} · @{post.channel}</a></li>)}</ul>}
    {others.length > 0 && <>
      <p>{cards.length > 0 ? 'Other posts in this trend (open in Telegram):' : 'Examples (open in Telegram):'}</p>
      <ul className="space-y-0.5">{others.map((example) => <li key={example.permalink} className="break-words">
        <TelegramLink href={example.permalink} className="inline-flex min-h-11 items-center text-slate-700 underline decoration-slate-300 underline-offset-2 hover:text-forest-800 sm:min-h-8">{example.headline} · @{example.channel}</TelegramLink></li>)}</ul>
    </>}
  </div>;
}

function Row({ topic, posts, onOpenCard }) {
  const marker = MARKERS[topic.trend];
  const members = new Set(topic.source_ids || []);
  const cards = posts.filter((post) => members.has(post.source_id));
  const more = (topic.ideas || []).length > 0 || cards.length > 0 || (topic.examples || []).length > 0;
  const body = <>
    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <span className="break-words text-[15px] font-medium leading-snug text-slate-900">{upper(topic.label)}</span>
      {marker && <span title={marker.means} className={`whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${marker.tone}`}>{marker.text}</span>}
    </span>
    {topic.gist && <span className="mt-0.5 block break-words text-sm leading-snug text-slate-600">{upper(topic.gist)}</span>}
    <Provenance topic={topic} />
  </>;
  if (!more) return <li className="py-2.5">{body}</li>;
  return <li><details className="group">
    <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 py-1.5 [&::-webkit-details-marker]:hidden">
      <span className="min-w-0 flex-1">{body}</span>
      <Icon name="chevron" size={16} className="text-slate-400 transition-transform group-open:rotate-90" />
    </summary>
    <div className="space-y-1.5 pb-3 text-sm text-slate-600">
      {(topic.ideas || []).length > 0 && <p>{topic.ideas.slice(0, 2).join(' · ')}</p>}
      <Behind topic={topic} cards={cards} onOpenCard={onOpenCard} />
    </div>
  </details></li>;
}

// What the channels discuss now: a calm list (topic · one-line gist · marker · where it comes from), collapsible, no outer box.
export default function TelegramTrends({ trends, posts = [], onOpenCard }) {
  const [open, setOpen] = useState(true);
  const [all, setAll] = useState(false);
  const topics = trends?.topics || [];
  if (!topics.length) return null;
  return <section aria-label="Trends" className="space-y-1">
    <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
      title={`${postsText(trends.posts?.week)} · ${channelsText(trends.posts?.channels)} · last 7 days`}
      className="flex min-h-11 w-full items-center gap-2 text-left">
      <h2 className="font-display text-xl font-light text-forest-800">Trends</h2>
      <Icon name="chevron" size={16} className={`text-slate-500 transition-transform ${open ? 'rotate-90' : ''}`} />
    </button>
    {open && <ol className="divide-y divide-slate-200 border-y border-slate-200">
      {(all ? topics : topics.slice(0, SHOWN)).map((topic) => <Row key={topic.id} topic={topic} posts={posts} onOpenCard={onOpenCard} />)}
    </ol>}
    {open && topics.length > SHOWN && <button type="button" aria-expanded={all} onClick={() => setAll(!all)}
      className="inline-flex min-h-11 items-center gap-1 text-sm text-slate-600 hover:text-forest-800 sm:min-h-8">
      <Icon name="chevron" size={14} className={`transition-transform ${all ? 'rotate-90' : ''}`} />{all ? 'Show fewer trends' : `Show all ${topics.length} trends`}</button>}
  </section>;
}
