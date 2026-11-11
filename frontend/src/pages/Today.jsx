// One retained shortlist; current facts refresh without selecting replacements.
import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Link, useLocation, useSearchParams } from 'react-router-dom';
import ConfigTask from '../components/settings/ConfigTask.jsx';
import { RssSourcesTask } from '../components/settings/SourceTasks.jsx';
import { taskUrl } from '../utils/settingsNavigation.js';
import PaperCard from '../components/today/PaperCard.jsx';
import NotReviewedShelf from '../components/today/NotReviewedShelf.jsx';
import CollectionPicker from '../components/CollectionPicker.jsx';
import NotConfiguredCard from '../components/setup/NotConfiguredCard.jsx';
import Spinner from '../components/ui/Spinner.jsx';
import Icon from '../components/ui/Icon.jsx';
import Skeleton from '../components/ui/Skeleton.jsx';
import { ErrorBanner, StatusBanner } from '../components/library/shared.jsx';
import { useSetupStatus } from '../hooks/useSetupStatus.js';
import useTodayVisit from '../hooks/useTodayVisit.js';
import { memberPresentation, preparationText } from '../utils/todayVisit.js';
import { feedLabel } from '../utils/feedText.js';
import { triggerTriageBacklog, getTriageStatus } from '../api/dailyApi.js';

export default function Today() {
  const location = useLocation();
  const [params] = useSearchParams();
  const task = params.get('task');
  const { status } = useSetupStatus();
  const enabled = Boolean(status) && !task;
  const view = useTodayVisit(enabled);
  const { refresh, selectedIds, toggleSelect, setSelected } = view;
  const [addCollectionKey, setAddCollectionKey] = useState('');
  const [feedFilter, setFeedFilter] = useState('');
  const [moreOpen, setMoreOpen] = useState(Boolean(location.state?.focusTask));
  useEffect(() => {
    if (!task && location.state?.focusTask) document.getElementById(`task-${location.state.focusTask}`)?.focus();
  }, [task, location.state?.focusTask]);

  const members = useMemo(() => (view.visit?.members || []).map(member => ({
    ...member, display: memberPresentation(member, view.fresh),
  })), [view.visit, view.fresh]);
  const feedNames = useMemo(() => [...new Set(members.map(member => member.paper.feed_name).filter(Boolean))].sort(), [members]);
  const visible = members.filter(member => !feedFilter || member.paper.feed_name === feedFilter);
  const selectable = visible.filter(member => member.display.selectable);
  const selected = selectable.filter(member => selectedIds.has(member.paper.item_id));
  const selectedReady = selected.filter(member => member.display.reviewReady);
  const allSelected = selectable.length > 0 && selectable.every(member => selectedIds.has(member.paper.item_id));
  const commit = kind => {
    view.act(kind, selected.map(member => member.paper.item_id), addCollectionKey);
    setSelected(new Set());
  };
  const llmEnabled = status?.llm?.enabled !== false;
  const preparation = view.visit?.preparation;
  const preparing = preparationText(preparation);

  const triageStatusQuery = useQuery({
    queryKey: ['triage-status'], queryFn: getTriageStatus, enabled,
    refetchInterval: query => query.state.data?.running || query.state.status === 'error' ? 3000 : false,
  });
  const triageStatus = triageStatusQuery.data;
  const triageUnknown = triageStatusQuery.isPending || triageStatusQuery.isError;
  const triageRunning = !triageUnknown && Boolean(triageStatus?.running);
  const triageMutation = useMutation({ mutationFn: triggerTriageBacklog, onSuccess: () => triageStatusQuery.refetch() });
  const draining = triageMutation.isPending || triageRunning;
  const previousRunning = useRef(false);
  useEffect(() => {
    if (triageUnknown) return;
    if (previousRunning.current && !triageRunning) refresh();
    previousRunning.current = triageRunning;
  }, [triageRunning, triageUnknown, refresh]);

  if (task === 'selection') return <ConfigTask key={task} task={task} backTo={taskUrl(location, null)} backLabel="Back to Today" />;
  if (task === 'rss') return <RssSourcesTask key={task} />;
  if (task) return <section role="alert"><h1>Today task not found</h1><Link to={taskUrl(location, null)}>Back to Today</Link></section>;

  const triageLabel = triageStatusQuery.isError ? 'Triage status unavailable'
    : triageStatusQuery.isPending ? 'Checking triage status…'
      : draining ? `Sorting the backlog… ${triageStatus?.gate_onward ?? triageStatus?.triaged ?? 0} kept` : 'Triage backlog';
  const menuLink = 'inline-flex min-h-11 items-center text-sm text-forest-800 underline underline-offset-4 sm:min-h-9';
  const secondary = 'inline-flex min-h-11 items-center gap-1.5 text-sm text-forest-800 underline underline-offset-4 disabled:no-underline disabled:opacity-50 sm:min-h-9';

  const showMore = moreOpen || draining || triageStatusQuery.isError;
  const hasVisit = view.visit && !view.visit.invalid;

  // One readable column (≤ 760 px): the cards are a one-at-a-time cull queue, so a
  // single column keeps the server's order; wider text lines only hurt reading.
  return <section className="mx-auto w-full max-w-[760px] px-1 sm:px-0">
    <NotConfiguredCard />
    <header className="mb-3">
      <h2 className="font-display text-2xl font-light text-slate-900">Today’s reading</h2>
      <p className="mt-0.5 text-xs text-slate-500">
        {hasVisit && members.length > 0
          ? `Showing ${members.length} of 15 · kept until the next shortlist`
          : 'New papers from your feeds — decide what enters your library.'}
      </p>
      <div className="mt-3 flex items-center gap-2">
        <button type="button" onClick={() => view.renew()} disabled={!view.canRenew}
          className="min-h-11 flex-1 rounded-lg bg-forest-800 px-4 text-sm font-medium text-white hover:bg-forest-700 disabled:bg-slate-300 disabled:text-slate-100 sm:flex-none">
          New shortlist
        </button>
        <button type="button" aria-expanded={showMore} aria-controls="today-more" onClick={() => setMoreOpen(!moreOpen)}
          className="inline-flex min-h-11 items-center gap-1 rounded-lg border border-slate-300 px-3 text-sm text-slate-700 hover:bg-slate-100">
          More<Icon name="chevron" size={14} className={`transition-transform duration-150 ${showMore ? 'rotate-90' : ''}`} />
        </button>
      </div>
      <div id="today-more" className={`${showMore ? 'mt-2 flex' : 'hidden'} flex-wrap items-center gap-x-5 gap-y-0.5 rounded-lg bg-slate-50 px-3 py-1`}>
        {hasVisit && members.length > 0 && <button type="button" onClick={view.refresh}
          disabled={view.busy} className={secondary}>Refresh status</button>}
        <button type="button" onClick={() => triageMutation.mutate()} disabled={draining || triageUnknown}
          title="Optional: rank the un-triaged feed backlog by relevance. This does not replace this shortlist. Processed items are marked read."
          className={secondary}>
          {draining && !triageUnknown && <Spinner size="xs" color="teal" />}{triageLabel}
        </button>
        <nav aria-label="Today task settings" className="contents">
          <Link id="task-selection" className={menuLink} to={taskUrl(location, 'selection')}>Article selection</Link>
          <Link id="task-rss" className={menuLink} to={taskUrl(location, 'rss')}>RSS sources</Link>
          <Link className={menuLink} to="/weekly">Telegram today</Link>
        </nav>
      </div>
    </header>
    <ErrorBanner error={view.selectionError} title="Selection failed — existing shortlist kept" />
    <ErrorBanner error={view.statusError} title="Member status unavailable — shortlist kept" />
    <ErrorBanner error={view.actionError} title="Action response unavailable — check each paper" />
    <ErrorBanner error={triageMutation.error} title="Triage backlog unavailable" />
    <ErrorBanner error={triageStatusQuery.error} title="Triage status unavailable — reconnecting" />
    {!triageUnknown && !triageRunning && (triageStatus?.error || triageStatus?.rescore_error) &&
      <ErrorBanner error={triageStatus.error || triageStatus.rescore_error} title="Last triage run failed" />}
    {view.persistenceFailed && <StatusBanner tone="warn" message="Browser storage is unavailable. This shortlist stays open here but may not survive reload. The shortlist itself never resends actions." />}
    {view.visit?.invalid && <StatusBanner tone="warn" message="The saved shortlist could not be read. New shortlist explicitly replaces it; no saved action is replayed." />}
    {view.carryCount > 0 && <StatusBanner tone="warn" message={`${view.carryCount} ${view.carryCount === 1 ? 'paper is' : 'papers are'} still being added. New shortlist is available once they finish.`} />}
    {view.loading && <div role="status" aria-label="Loading today’s papers…" className="space-y-3">
      <p className="flex items-center gap-2 text-sm text-slate-600"><Spinner size="sm" color="teal" />Loading today’s papers…</p>
      {[0, 1].map(i => <div key={i} aria-hidden="true" className="space-y-3 rounded-xl border border-slate-200 p-4">
        <Skeleton width="w-24" height="h-4" rounded="rounded-full" /><Skeleton width="w-5/6" height="h-5" /><Skeleton count={2} />
        <div className="grid grid-cols-4 gap-2">{[0, 1, 2, 3].map(j => <Skeleton key={j} height="h-11" rounded="rounded-lg" />)}</div>
      </div>)}
    </div>}
    {hasVisit && members.length === 0 && <div className="my-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
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
    </div>}
    {members.length > 0 && <>
      {(selectable.length > 0 || feedNames.length > 1 || feedFilter) &&
        <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-0 text-xs text-slate-600">
          {selectable.length > 0 && <label className="flex min-h-11 cursor-pointer select-none items-center gap-1.5 sm:min-h-9">
            <input type="checkbox" checked={allSelected} disabled={view.busy}
              onChange={() => setSelected(allSelected ? new Set() : new Set(selectable.map(member => member.paper.item_id)))}
              className="h-4 w-4 rounded border-slate-300 text-forest-800 focus:ring-forest-600" />
            {selected.length ? `${selected.length} selected` : 'Select all'}
          </label>}
          {(feedNames.length > 1 || feedFilter) && <select value={feedFilter} onChange={event => setFeedFilter(event.target.value)}
            aria-label="Feed source" className="min-h-11 min-w-[10rem] max-w-full flex-1 rounded-lg border border-slate-300 bg-white px-2 py-1 text-slate-700 sm:max-w-xs sm:min-h-9">
            <option value="">All sources ({members.length})</option>
            {feedFilter && !feedNames.includes(feedFilter) && <option value={feedFilter}>{feedFilter} (0)</option>}
            {feedNames.map(name => <option key={name} value={name}>{feedLabel(name)} ({members.filter(member => member.paper.feed_name === name).length})</option>)}
          </select>}
        </div>}
      {/* Bulk action bar: appears ONLY when ≥ 1 card is ticked (#37/T05, F04). One slim
          bottom row, not a 210 px always-on toolbar covering the cards. */}
      {selected.length > 0 && <div className="sticky bottom-2 z-10 mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white p-2">
        <span className="text-xs font-medium text-slate-700">{selected.length} selected</span>
        <button type="button" onClick={() => setSelected(new Set())} className="min-h-11 text-xs text-slate-500 underline">Clear</button>
        <div className="flex-1" />
        <CollectionPicker value={addCollectionKey} onChange={setAddCollectionKey} disabled={view.busy} />
        <button type="button" onClick={() => commit('add')} disabled={!selectedReady.length || view.busy}
          className="min-h-11 rounded-lg bg-forest-800 px-3 py-1.5 text-xs font-medium text-white hover:bg-forest-700 disabled:opacity-40">
          Add to library
        </button>
        <button type="button" onClick={() => commit('trash')} disabled={view.busy}
          className="min-h-11 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-rose-700 hover:border-rose-500 disabled:opacity-40">
          Trash
        </button>
        {selected.length > selectedReady.length && <span className="basis-full text-xs text-amber-800">
          {llmEnabled ? 'Papers without a review are skipped by Add — Trash still works.' : 'Enable AI in Settings before adding unreviewed papers.'}
        </span>}
      </div>}
      <div className="space-y-3">
        {!visible.length && <p className="text-xs text-slate-500 p-3">This source filter hides the shortlist.{' '}
          <button type="button" onClick={() => setFeedFilter('')} className="min-h-11 text-forest-800 underline">Show all sources</button>
        </p>}
        {visible.map(member => <PaperCard key={member.paper.item_id} paper={member.paper}
          selectionPolicy={member.selectionPolicy} selected={selectedIds.has(member.paper.item_id) && member.display.selectable}
          onToggleSelect={toggleSelect} llmEnabled={llmEnabled} visitId={view.visit.id}
          memberState={member.display} currentFacts={member.facts} statusFresh={view.fresh}
          busy={view.busy} onRetry={() => view.retry(member)}
          onDecide={view.decide} onUndo={view.undoDecision} decisionPending={view.decisionPending} />)}
      </div>
      {preparing && <p role="status" className="mt-3 text-xs text-slate-600">{preparing}</p>}
    </>}
    {hasVisit && <NotReviewedShelf papers={view.visit.unreviewed} visitId={view.visit.id} />}
  </section>;
}
