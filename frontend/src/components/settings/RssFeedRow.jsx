import Button from '../ui/Button.jsx';

// One compact RSS subscription row: name, where/when/how much, Edit; Delete lives
// behind a "More" menu (never a red button on every row) and still asks to confirm.
// Only the host is shown; the full URL (often a long arXiv query) is the hover title and the Edit form.
export const feedHost = url => { try { return new URL(url).host; } catch { return String(url || ''); } };

// Plain-language reading of the saved last-error text; the raw text stays in the hover title.
export function plainFeedIssue(issue) {
  const text = String(issue || '');
  if (text.startsWith('RssNamespaceWarning:')) return 'Feed has minor formatting problems; it still works.';
  if (/^RssUrlRejected: Invalid RSS\/Atom feed document/.test(text)) return 'This address is not an RSS feed.';
  if (text.startsWith('RssUrlRejected:')) return `This address was rejected: ${text.slice(15).trim()}.`;
  const code = /^(?:HTTPStatusError: )?(?:(?:Client|Server) error )?'?([45]\d\d)\b/.exec(text)?.[1];
  if (code === '404') return 'This feed returned “not found” (404).';
  if (code === '403' || code === '401') return `This feed refused access (${code}).`;
  if (code === '429') return 'This feed is limiting requests (429); try later.';
  if (code) return `This feed's server returned an error (${code}).`;
  if (/Connect|Timeout|Resolve|Name or service/i.test(text)) return 'This feed could not be reached.';
  return text.replace(/\s*For more information check:.*$/, '').trim();
}

export default function RssFeedRow({ feed, disabled, onEdit, onDelete }) {
  const host = feedHost(feed.url);
  const issue = feed.last_error;
  const warning = issue?.startsWith('RssNamespaceWarning:');
  return <article className={`px-3 py-2 min-w-0 ${feed.enabled ? '' : 'opacity-70'}`}>
    <div className="flex items-center gap-2 min-w-0">
      <div className="min-w-0 flex-1">
        <h3 className="text-sm font-medium truncate">{feed.name}</h3>
        <p className="text-xs text-slate-600 truncate" title={feed.url}>{host}</p>
      </div>
      <Button id={`rss-edit-${feed.id}`} size="sm" variant="secondary" disabled={disabled} onClick={() => onEdit(feed)} aria-label={`Edit ${feed.name}`}>Edit</Button>
      <details className="relative">
        <summary aria-label={`More actions for ${feed.name}`} className="list-none cursor-pointer min-h-8 min-w-8 inline-flex items-center justify-center rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-100">⋯</summary>
        <div className="absolute right-0 z-10 mt-1 rounded-lg border border-slate-200 bg-white p-1">
          <Button size="sm" variant="danger" disabled={disabled} onClick={() => onDelete(feed)} aria-label={`Delete ${feed.name}`}>Delete</Button>
        </div>
      </details>
    </div>
    <p className="text-xs text-slate-600 flex flex-wrap gap-x-3">
      {!feed.enabled && <span className="font-medium">Disabled</span>}
      <span>{feed.last_fetched_at ? `Last checked: ${feed.last_fetched_at}` : 'Not refreshed yet'}</span>
      {Boolean(feed.fetch_incomplete) && <span className="font-medium text-amber-800" title="The source cut the response off before the confirmed boundary; the rest is read on the next refresh.">Incomplete fetch</span>}
      {Number.isInteger(feed.item_count) && <span>{feed.item_count} item{feed.item_count === 1 ? '' : 's'}</span>}
    </p>
    {issue && <p className={`mt-1 text-xs line-clamp-2 ${warning ? 'text-amber-800' : 'text-rose-700'} [overflow-wrap:anywhere]`} title={issue}>{plainFeedIssue(issue)}</p>}
  </article>;
}
