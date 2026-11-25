import Spinner from '../ui/Spinner.jsx';
import Icon from '../ui/Icon.jsx';
import ZoteroSyncLine from './ZoteroSyncLine.jsx';
import TodayMoreMenu from './TodayMoreMenu.jsx';

// Title, sync line, «New shortlist» / «More» buttons and the More row.
export default function TodayHeader({ enabled, view, triage, shown, hasVisit, moreOpen, onToggleMore, onRenew }) {
  const showMore = moreOpen || triage.draining || triage.query.isError;
  return <header className="mb-3">
    <h1 className="font-display text-2xl font-light text-slate-900">Today’s reading</h1>
    <p className="mt-0.5 text-xs text-slate-500">
      {hasVisit && shown > 0
        ? `Showing ${shown} of 15 · kept until the next shortlist`
        : 'New papers from your feeds — decide what enters your library.'}
    </p>
    <ZoteroSyncLine enabled={enabled} busy={view.busy} />
    <div className="mt-3 flex items-center gap-2">
      <button type="button" onClick={onRenew} disabled={!view.canRenew} aria-busy={view.renewing}
        className="inline-flex min-h-11 flex-1 items-center justify-center gap-2 rounded-lg bg-forest-800 px-4 text-sm font-medium text-white hover:bg-forest-700 disabled:bg-slate-300 disabled:text-slate-100 sm:flex-none">
        {view.renewing && <Spinner size="xs" color="teal-on-fill" />}
        {view.renewing ? 'Choosing papers…' : 'New shortlist'}
      </button>
      <button type="button" aria-expanded={showMore} aria-controls="today-more" onClick={onToggleMore}
        className="inline-flex min-h-11 items-center gap-1 rounded-lg border border-slate-300 px-3 text-sm text-slate-700 hover:bg-slate-100">
        More<Icon name="chevron" size={14} className={`transition-transform duration-150 ${showMore ? 'rotate-90' : ''}`} />
      </button>
    </div>
    <TodayMoreMenu show={showMore} showRefresh={hasVisit && shown > 0} view={view} triage={triage} />
  </header>;
}
