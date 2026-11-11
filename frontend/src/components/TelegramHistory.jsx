import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { telegramFeedApi } from '../api/researchFeedApi.js';
import Icon from './ui/Icon.jsx';
import Async from './ui/Async.jsx';
import TelegramFeed from './TelegramFeed.jsx';
import { when } from './telegramRunText.js';

const back = <Link to={{ search: '' }} className="inline-flex min-h-11 items-center text-forest-800 underline underline-offset-4 sm:min-h-8">Channels</Link>;

// Past digests: a list by date (discussions · unread), and a saved digest opened in the same feed components.
// A saved digest keeps summaries and highlights; comment threads load on expand from the stored conversations.
export default function TelegramHistory({ runId }) {
  const list = runId === 'list';
  const runs = useQuery({ queryKey: ['telegram-history'], queryFn: telegramFeedApi.history, enabled: list, retry: false, networkMode: 'always' });
  const run = useQuery({ queryKey: ['telegram-history-run', runId], queryFn: () => telegramFeedApi.historyRun(runId), enabled: !list, retry: false, networkMode: 'always' });
  const items = runs.data?.items || [];
  return <section aria-label="Past digests" className="mx-auto max-w-3xl space-y-4">
    <header className="space-y-1">
      <h1 className="font-display text-2xl font-light text-forest-800">{list ? 'Past digests' : `Digest · ${when(run.data?.generated_at)}`}</h1>
      <p className="flex flex-wrap items-center gap-x-1.5 text-sm text-slate-600">
        {list ? back : <><Link to={{ search: '?past=list' }} className="inline-flex min-h-11 items-center text-forest-800 underline underline-offset-4 sm:min-h-8">Past digests</Link>
          <span aria-hidden="true">·</span><span>Marks and feedback apply to the discussion everywhere.</span></>}
        {list && items.length > 0 && <><span aria-hidden="true">·</span>
          <a href={telegramFeedApi.feedbackExportUrl} download className="inline-flex min-h-11 items-center text-forest-800 underline underline-offset-4 sm:min-h-8">Export feedback</a></>}
      </p>
    </header>
    {list ? <Async loading={runs.isPending} error={runs.error} empty={items.length === 0 && <p className="text-sm text-slate-600">No saved digests yet. Each “Check channels” run is saved here.</p>}>
      <ul className="divide-y divide-slate-200 border-y border-slate-200">
        {items.map((item) => <li key={item.run_id}>
          <Link to={{ search: `?past=${item.run_id}` }} className="flex min-h-11 items-center justify-between gap-3 py-2.5">
            <span className="min-w-0">
              <span className="block text-[15px] font-medium text-slate-900">{when(item.generated_at)}</span>
              {item.lead && <span className="block truncate text-sm text-slate-600">{item.lead}</span>}
            </span>
            <span className="flex shrink-0 items-center gap-1 text-sm text-slate-600">{item.discussions} discussions · {item.unread === 0 ? 'all read' : `${item.unread} unread`}
              <Icon name="chevron" size={14} /></span>
          </Link>
        </li>)}
      </ul>
    </Async>
      : <Async loading={run.isPending} error={run.error}>{run.data && <TelegramFeed data={run.data} />}</Async>}
  </section>;
}
