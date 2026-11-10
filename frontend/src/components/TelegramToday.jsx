import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { telegramFeedApi } from '../api/researchFeedApi.js';
import Async from './ui/Async.jsx';
import Icon from './ui/Icon.jsx';
import TelegramFeed from './TelegramFeed.jsx';
import { RunButton, RunStatus } from './TelegramRunButton.jsx';
import { useTelegramRun } from './useTelegramRun.js';
import { MARKER_LEGEND, SCOPE_TEXT, ageText, channelsText } from './telegramRunText.js';

// The page: title row + ONE primary button, ONE quiet status line (the info disclosure holds scope + legend),
// then Trends (collapsible) and the discussion cards. Summaries and trend labels stay in the chats' language.
export default function TelegramToday({ headerAction = null }) {
  const today = useQuery({ queryKey: ['telegram-today'], queryFn: telegramFeedApi.today, retry: false, networkMode: 'always' });
  const sources = useQuery({ queryKey: ['telegram-sources'], queryFn: telegramFeedApi.sources, retry: false, networkMode: 'always' });
  const run = useTelegramRun();
  const [info, setInfo] = useState(false);
  const data = today.data;
  const posts = data?.posts || [];
  const count = sources.data?.sources?.length;
  return <section aria-label="Channels" className="max-w-3xl space-y-4">
    <header className="space-y-1">
      <div className="flex items-center justify-between gap-3">
        <h1 className="font-display text-2xl font-light text-forest-800">Channels</h1>
        <RunButton run={run} />
      </div>
      <p className="flex flex-wrap items-center gap-x-1.5 text-sm text-slate-600">
        <span>{ageText(data?.generated_at)}</span>
        {count > 0 && <><span aria-hidden="true">·</span><span>{channelsText(count)}</span></>}
        <span aria-hidden="true">·</span>{headerAction}
        <button type="button" aria-expanded={info} aria-label="About this page" onClick={() => setInfo(!info)}
          className="inline-flex h-11 w-11 items-center justify-center rounded-lg text-slate-500 hover:text-forest-800 sm:h-8 sm:w-8">
          <Icon name="info" size={16} />
        </button>
      </p>
      {info && <div className="space-y-1.5 rounded-xl border border-slate-200 p-3 text-sm leading-relaxed text-slate-700">
        <p>{SCOPE_TEXT}</p>
        <ul className="space-y-0.5 text-slate-600">{MARKER_LEGEND.map(([name, means]) =>
          <li key={name}><span className="font-medium text-slate-700">{name}</span> — {means}</li>)}</ul>
      </div>}
    </header>
    <RunStatus run={run} />
    <Async loading={today.isPending} error={today.error} empty={posts.length === 0
      && <p className="text-sm text-slate-600">No discussions yet. Press “Check channels” to read your selected channels (pick them in Telegram sources).</p>}>
      <TelegramFeed data={data} />
    </Async>
  </section>;
}
