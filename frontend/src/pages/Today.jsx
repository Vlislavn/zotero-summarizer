// One retained shortlist; current facts refresh without selecting replacements.
import { useEffect, useState } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';
import ConfigTask from '../components/settings/ConfigTask.jsx';
import { RssSourcesTask } from '../components/settings/SourceTasks.jsx';
import { taskUrl } from '../utils/settingsNavigation.js';
import NotReviewedShelf from '../components/today/NotReviewedShelf.jsx';
import NotConfiguredCard from '../components/setup/NotConfiguredCard.jsx';
import { useSetupStatus } from '../hooks/useSetupStatus.js';
import UndoToast from '../components/library/UndoToast.jsx';
import useTodayVisit from '../hooks/useTodayVisit.js';
import useTodayDecisionToast from '../hooks/useTodayDecisionToast.js';
import { preparationText } from '../utils/todayVisit.js';
import useTodaySlate from '../components/today/useTodaySlate.js';
import useTodayRenewal from '../components/today/useTodayRenewal.js';
import useTriageBacklog from '../components/today/useTriageBacklog.js';
import { pickMembers } from '../components/today/todaySlate.js';
import TodayHeader from '../components/today/TodayHeader.jsx';
import { ErrorBanners, RenewalNote, StatusBanners } from '../components/today/TodayBanners.jsx';
import TodayPlaceholders from '../components/today/TodayPlaceholders.jsx';
import SlateToolbar from '../components/today/SlateToolbar.jsx';
import BulkBar from '../components/today/BulkBar.jsx';
import TodayCardList from '../components/today/TodayCardList.jsx';

function TodayTask({ task, location }) {
  if (task === 'selection') return <ConfigTask key={task} task={task} backTo={taskUrl(location, null)} backLabel="Back to Today" />;
  if (task === 'rss') return <RssSourcesTask key={task} />;
  return <section role="alert"><h1>Today task not found</h1><Link to={taskUrl(location, null)}>Back to Today</Link></section>;
}

export default function Today() {
  const location = useLocation();
  const [params] = useSearchParams();
  const task = params.get('task');
  const { status } = useSetupStatus();
  const enabled = Boolean(status) && !task;
  const view = useTodayVisit(enabled);
  const { selectedIds, setSelected } = view;
  const [addCollectionKey, setAddCollectionKey] = useState('');
  const [feedFilter, setFeedFilter] = useState('');
  const [moreOpen, setMoreOpen] = useState(Boolean(location.state?.focusTask));
  useEffect(() => {
    if (!task && location.state?.focusTask) document.getElementById(`task-${location.state.focusTask}`)?.focus();
  }, [task, location.state?.focusTask]);

  const flow = useTodayDecisionToast({ decide: view.decide, undo: view.undoDecision, busy: view.busy });
  const { allMembers, members, listRef, decideCard, feedNames, hasVisit, caughtUp } = useTodaySlate(view, flow);
  const picked = pickMembers(members, feedFilter, selectedIds);
  const commit = kind => {
    view.act(kind, picked.selected.map(member => member.paper.item_id), addCollectionKey);
    setSelected(new Set());
  };
  const llmEnabled = status?.llm?.enabled !== false;
  const preparing = preparationText(view.visit?.preparation);
  const { renewResult, startRenewal } = useTodayRenewal(view, allMembers);
  const triage = useTriageBacklog(enabled, view.refresh);

  if (task) return <TodayTask task={task} location={location} />;

  // One readable column (≤ 760 px): the cards are a one-at-a-time cull queue, so a
  // single column keeps the server's order; wider text lines only hurt reading.
  return <section className="mx-auto w-full max-w-[760px] px-1 sm:px-0">
    <NotConfiguredCard />
    <TodayHeader enabled={enabled} view={view} triage={triage} shown={members.length} hasVisit={hasVisit}
      moreOpen={moreOpen} onToggleMore={() => setMoreOpen(!moreOpen)} onRenew={startRenewal} />
    <RenewalNote result={renewResult} renewing={view.renewing} />
    <ErrorBanners view={view} triage={triage} />
    <StatusBanners view={view} />
    <TodayPlaceholders loading={view.loading} hasVisit={hasVisit} empty={allMembers.length === 0}
      caughtUp={caughtUp} preparing={preparing} />
    {members.length > 0 && <>
      <SlateToolbar members={members} picked={picked} feedNames={feedNames} feedFilter={feedFilter}
        onFeedFilter={setFeedFilter} busy={view.busy} onSelect={setSelected} />
      {picked.selected.length > 0 && <BulkBar picked={picked} collectionKey={addCollectionKey}
        onCollection={setAddCollectionKey} busy={view.busy} llmEnabled={llmEnabled} onCommit={commit}
        onClear={() => setSelected(new Set())} />}
      <TodayCardList visible={picked.visible} view={view} listRef={listRef} decideCard={decideCard}
        llmEnabled={llmEnabled} onShowAll={() => setFeedFilter('')} />
      {preparing && <p role="status" className="mt-3 text-xs text-slate-600">{preparing}</p>}
    </>}
    {hasVisit && <NotReviewedShelf papers={view.visit.unreviewed} visitId={view.visit.id} />}
    {flow.toast && <div className="sticky bottom-2 z-20 mt-3">
      <UndoToast toast={flow.toast} chips={flow.chips} onUndo={flow.undo} onReason={flow.toggleReason} onDismiss={flow.dismiss} />
    </div>}
  </section>;
}
