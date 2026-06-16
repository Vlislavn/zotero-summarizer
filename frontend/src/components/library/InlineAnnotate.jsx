import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchReviewDetail, submitVerdict, deleteVerdict } from '../../api/goldenApi.js';
import { queueRejectTag } from '../../api/libraryApi.js';
import PaperDetailView from '../paper/PaperDetailView/index.jsx';
import { StatusBanner } from './shared.jsx';

// Inline review panel: expands under a Read-next row. The rich paper-detail
// assembly (the DECIDE/ACT zones — digest, brief, abstract, ask, verdict, tags,
// collection) is the shared PaperDetailView in `editable` mode, so this surface
// and the Annotate page stop re-implementing the same wiring. Picking
// `dont_read` IS the old "Remove" path — it also queues a ❌ Zotero tag, so
// there is ONE reject path (Occam's Razor), not two. onSaved collapses +
// refetches (a verdicted/engagement-tagged paper drops out); onQueueRefresh
// refetches without collapsing.
export default function InlineAnnotate({
  itemKey, collections = [], onSaved, onQueueRefresh,
  // Override path from the Confirm/Override card: when set, pre-select the fleet's
  // PROPOSED verdict in the picker instead of the server's derived priority — so
  // "Override" lands on the proposal, one click from the same decision.
  derivedPriorityOverride = null,
}) {
  const queryClient = useQueryClient();
  const detailQuery = useQuery({
    queryKey: ['review-detail', itemKey],
    queryFn: () => fetchReviewDetail(itemKey),
  });
  // Saving a verdict; `dont_read` also queues the ❌ reject tag (the folded
  // "Remove" action) so rejecting is one path through the verdict picker.
  const submitMutation = useMutation({
    mutationFn: ({ item_key, user_priority, comment }) => {
      const tasks = [submitVerdict({ item_key, user_priority, comment })];
      if (user_priority === 'dont_read') tasks.push(queueRejectTag(item_key));
      return Promise.all(tasks);
    },
  });
  const deleteMutation = useMutation({ mutationFn: () => deleteVerdict(itemKey) });
  // Open/closed state of the embedded paper-brief pane.
  const [readerOpen, setReaderOpen] = useState(false);
  const detail = detailQuery.data;

  function refreshDetail() {
    queryClient.invalidateQueries({ queryKey: ['review-detail', itemKey] });
  }

  return (
    <div className="mt-2 rounded-xl border border-teal-200 bg-teal-50/30 p-3 space-y-3">
      {detailQuery.isLoading && <div className="text-xs text-slate-500">Loading detail…</div>}
      {detailQuery.error && (
        <StatusBanner message={`Detail load failed: ${detailQuery.error.message || detailQuery.error}`} isError />
      )}
      {detail && (
        <PaperDetailView
          mode="editable"
          detail={detail}
          itemKey={itemKey}
          collections={collections}
          readerOpen={readerOpen}
          onReaderOpenChange={setReaderOpen}
          onDeepReviewDone={refreshDetail}
          onTagsChanged={() => { refreshDetail(); onQueueRefresh?.(); }}
          onCollectionsChanged={() => { refreshDetail(); onQueueRefresh?.(); }}
          verdict={{
            derivedPriority: derivedPriorityOverride || detail.provenance?.derived_priority,
            existing: detail.verdict,
            onSubmit: ({ user_priority, comment }) =>
              submitMutation.mutate({ item_key: itemKey, user_priority, comment }, { onSuccess: onSaved }),
            onDelete: () => deleteMutation.mutate(undefined, { onSuccess: onSaved }),
            submitting: submitMutation.isPending,
            submitError: submitMutation.error?.message || null,
            deleting: deleteMutation.isPending,
            deleteError: deleteMutation.error?.message || null,
          }}
        />
      )}
    </div>
  );
}
