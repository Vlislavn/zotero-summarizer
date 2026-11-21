import PaperCard from './PaperCard.jsx';

export default function TodayCardList({ visible, view, listRef, decideCard, llmEnabled, onShowAll }) {
  return <div ref={listRef} tabIndex={-1} role="group" aria-label="Papers to decide" className="space-y-3 outline-none">
    {!visible.length && <p className="text-xs text-slate-500 p-3">This source filter hides the shortlist.{' '}
      <button type="button" onClick={onShowAll} className="min-h-11 text-forest-800 underline">Show all sources</button>
    </p>}
    {visible.map(member => <PaperCard key={member.paper.item_id} paper={member.paper}
      selectionPolicy={member.selectionPolicy} selected={view.selectedIds.has(member.paper.item_id) && member.display.selectable}
      onToggleSelect={view.toggleSelect} llmEnabled={llmEnabled} visitId={view.visit.id}
      memberState={member.display} currentFacts={member.facts} statusFresh={view.fresh}
      busy={view.busy} onRetry={() => view.retry(member)}
      onDecide={(_id, priority) => decideCard(member.paper, priority)} decisionPending={view.decisionPending} />)}
  </div>;
}
