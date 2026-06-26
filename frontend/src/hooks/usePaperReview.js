import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchReviewDetail, submitVerdict, deleteVerdict } from '../api/goldenApi.js';
import { queueRejectTag } from '../api/libraryApi.js';

// Shared wiring for one paper's review detail + verdict mutations. Lifted out of
// InlineAnnotate so the inline row card AND the full-page review (/paper/:key)
// drive the verdict through ONE path instead of a third hand-rolled copy (the
// same dedup move that produced PaperDetailView). Picking `dont_read` also queues
// the ❌ reject tag here, so there is ONE reject path wherever the verdict is set
// (Occam's Razor). Returns the `verdict` prop object PaperDetailView expects plus
// the tag/collection refetch handlers.
export default function usePaperReview(itemKey, { onSaved, onQueueRefresh, derivedPriorityOverride = null } = {}) {
  const queryClient = useQueryClient();
  const detailQuery = useQuery({
    queryKey: ['review-detail', itemKey],
    queryFn: () => fetchReviewDetail(itemKey),
    enabled: Boolean(itemKey),
  });
  const submitMutation = useMutation({
    mutationFn: ({ item_key, user_priority, comment }) => {
      const tasks = [submitVerdict({ item_key, user_priority, comment })];
      if (user_priority === 'dont_read') tasks.push(queueRejectTag(item_key));
      return Promise.all(tasks);
    },
  });
  const deleteMutation = useMutation({ mutationFn: () => deleteVerdict(itemKey) });
  const detail = detailQuery.data;

  function refreshDetail() {
    queryClient.invalidateQueries({ queryKey: ['review-detail', itemKey] });
  }

  function afterVerdictChange() {
    refreshDetail();
    onSaved?.();
  }

  const verdict = {
    derivedPriority: derivedPriorityOverride || detail?.provenance?.derived_priority,
    existing: detail?.verdict,
    onSubmit: ({ user_priority, comment }) =>
      submitMutation.mutate({ item_key: itemKey, user_priority, comment }, { onSuccess: afterVerdictChange }),
    onDelete: () => deleteMutation.mutate(undefined, { onSuccess: afterVerdictChange }),
    submitting: submitMutation.isPending,
    submitError: submitMutation.error?.message || null,
    deleting: deleteMutation.isPending,
    deleteError: deleteMutation.error?.message || null,
  };

  return {
    detail,
    isLoading: detailQuery.isLoading,
    error: detailQuery.error,
    refreshDetail,
    onTagsChanged: () => { refreshDetail(); onQueueRefresh?.(); },
    onCollectionsChanged: () => { refreshDetail(); onQueueRefresh?.(); },
    verdict,
  };
}
