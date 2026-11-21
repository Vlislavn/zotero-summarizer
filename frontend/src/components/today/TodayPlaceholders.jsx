import { Link } from 'react-router-dom';
import Spinner from '../ui/Spinner.jsx';
import Skeleton from '../ui/Skeleton.jsx';

function TodayLoading() {
  return <div role="status" aria-label="Loading today’s papers…" className="space-y-3">
    <p className="flex items-center gap-2 text-sm text-slate-600"><Spinner size="sm" color="teal" />Loading today’s papers…</p>
    {[0, 1].map(i => <div key={i} aria-hidden="true" className="space-y-3 rounded-xl border border-slate-200 p-4">
      <Skeleton width="w-24" height="h-4" rounded="rounded-full" /><Skeleton width="w-5/6" height="h-5" /><Skeleton count={2} />
      <div className="grid grid-cols-4 gap-2">{[0, 1, 2, 3].map(j => <Skeleton key={j} height="h-11" rounded="rounded-lg" />)}</div>
    </div>)}
  </div>;
}

function EmptyShortlist({ preparing }) {
  return <div className="my-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
    <p className="text-sm font-medium text-slate-800">No papers in today’s shortlist yet.</p>
    <p className="mt-1 text-xs text-slate-600">
      Today only shows feed papers that already have a full-text review.
      {' '}{preparing || 'Papers you haven’t triaged are not analyzed yet, so they don’t appear here.'}
    </p>
    <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-slate-600">
      <li><span className="font-medium text-slate-800">New shortlist</span> (above) picks up to 15 reviewed papers; it does not analyze new ones.</li>
      <li><span className="font-medium text-slate-800">Triage backlog</span> (under More) ranks the un-triaged feed by relevance and marks those items read, so reviews can be prepared.</li>
      <li><Link to="/library" className="text-forest-800 underline">Library</Link> lists everything already saved in Zotero.</li>
    </ul>
  </div>;
}

function CaughtUp() {
  return <div role="status" className="my-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
    <p className="text-sm font-medium text-slate-800">All caught up</p>
    <p className="mt-1 text-xs text-slate-600">You decided every paper in this shortlist. <span className="font-medium text-slate-800">New shortlist</span> (above) picks the next ones.</p>
  </div>;
}

// Exactly one of: loading skeleton, empty shortlist, «All caught up» (or none, with cards to show).
export default function TodayPlaceholders({ loading, hasVisit, empty, caughtUp, preparing }) {
  return <>
    {loading && <TodayLoading />}
    {hasVisit && empty && <EmptyShortlist preparing={preparing} />}
    {caughtUp && <CaughtUp />}
  </>;
}
