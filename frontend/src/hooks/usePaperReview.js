import { useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchReviewDetail, submitVerdict, deleteVerdict } from '../api/goldenApi.js';
import { queueRejectTag } from '../api/libraryApi.js';
import { syncStatusEvent } from '../offlineStore.js';

// Shared wiring for one paper's review detail + verdict mutations. Lifted out of
// InlineAnnotate so the inline row card AND the full-page review (/paper/:key)
// drive the verdict through ONE path instead of a third hand-rolled copy (the
// same dedup move that produced PaperDetailView). Picking `dont_read` also queues
// the ❌ reject tag here, so there is ONE reject path wherever the verdict is set
// (Occam's Razor). Returns the `verdict` prop object PaperDetailView expects plus
// the tag/collection refetch handlers.
export default function usePaperReview(itemKey, { onSaved, onQueueRefresh } = {}) {
  const queryClient = useQueryClient();
  const activeItem = useRef(itemKey);
  const callbacks = useRef({ onSaved, onQueueRefresh });
  callbacks.current = { onSaved, onQueueRefresh };
  activeItem.current = itemKey;
  useEffect(() => {
    activeItem.current = itemKey;
    return () => { activeItem.current = null; };
  }, [itemKey]);
  const detailQuery = useQuery({
    queryKey: ['review-detail', itemKey],
    queryFn: () => fetchReviewDetail(itemKey),
    networkMode: 'always',
    enabled: Boolean(itemKey),
  });
  const submitMutation = useMutation({
    networkMode: 'always',
    mutationFn: async ({ item_key, user_priority, comment }) => {
      const result = await submitVerdict({ item_key, user_priority, comment });
      if (user_priority !== 'dont_read' || result.saved_offline) return result;
      try {
        await queueRejectTag(item_key);
        return result;
      } catch (error) {
        return { ...result, reject_tag_error: error?.message || String(error) };
      }
    },
  });
  const deleteMutation = useMutation({ networkMode: 'always', mutationFn: (key) => deleteVerdict(key) });
  const detail = detailQuery.data;

  useEffect(() => {
    const reconciled = (event) => {
      if (submitMutation.variables?.item_key !== itemKey || !submitMutation.data?.saved_offline || !event.detail?.online) return;
      const rejected = event.detail.rejected?.some(
        (row) => row.item_key === itemKey && row.field === 'verdict',
      );
      if (rejected || event.detail.pending === 0) submitMutation.reset();
    };
    window.addEventListener(syncStatusEvent, reconciled);
    return () => window.removeEventListener(syncStatusEvent, reconciled);
  }, [itemKey, submitMutation.variables?.item_key, submitMutation.data?.saved_offline, submitMutation.reset]);

  // POST /api/golden/verdict resolves HTTP 200 even when the best-effort Zotero
  // mirror (label tag / note) failed — it reports label_error/note_error instead
  // of raising (api/routes/golden.py submit_verdict). The verdict itself is
  // already durable at that point; same soft-warning idiom as AnnotationVerdict's
  // flashStatus (data?.label_error || data?.note_error).
  const ownsSubmit = submitMutation.variables?.item_key === itemKey;
  const ownsDelete = deleteMutation.variables === itemKey;
  const verdictResult = ownsSubmit ? submitMutation.data || null : null;
  // A positive verdict on a Today-feed paper now materializes it into Zotero
  // (api/routes/golden.py submit_verdict → add_feed_verdict_to_library). Surface
  // the outcome so the user KNOWS it was added — the paper appears in Zotero
  // after the next Zotero restart (direct-DB write, same as "Add to library").
  const addWarning = verdictResult?.add_error
    ? `Not added to Zotero: ${verdictResult.add_error}`
    : (verdictResult?.add_status === 'zotero_unavailable' || verdictResult?.add_status === 'zotero_pending')
      ? 'Queued for Zotero — run "Apply all approved" / restart Zotero to finish adding'
      : null;
  const submitWarning = verdictResult?.label_error
    ? `Zotero label not written: ${verdictResult.label_error}`
    : verdictResult?.note_error
      ? `Zotero note not written: ${verdictResult.note_error}`
      : verdictResult?.reject_tag_error
        ? `Verdict saved, but reject tag was not queued: ${verdictResult.reject_tag_error}`
        : addWarning;
  const submitNotice = verdictResult?.added_to_library
    ? '✓ Added to your Zotero Inbox — appears after a Zotero restart'
    : verdictResult?.saved_offline
      ? 'Saved on this device — it will sync when the server is reachable'
    : null;

  function refreshDetail() {
    queryClient.invalidateQueries({ queryKey: ['review-detail', itemKey] });
  }

  function afterVerdictChange() {
    if (activeItem.current !== itemKey) return;
    refreshDetail();
    callbacks.current.onSaved?.();
  }

  const verdict = {
    existing: detail?.verdict,
    onSubmit: ({ user_priority, comment }) =>
      submitMutation.mutate({ item_key: itemKey, user_priority, comment }, { onSuccess: afterVerdictChange }),
    onDelete: () => deleteMutation.mutate(itemKey, { onSuccess: afterVerdictChange }),
    submitting: ownsSubmit && submitMutation.isPending,
    submitError: ownsSubmit ? submitMutation.error?.message || null : null,
    submitWarning,
    submitNotice,
    deleting: ownsDelete && deleteMutation.isPending,
    deleteError: ownsDelete ? deleteMutation.error?.message || null : null,
  };

  return {
    detail,
    isLoading: detailQuery.isLoading,
    error: detailQuery.error,
    refreshDetail,
    onTagsChanged: () => { refreshDetail(); callbacks.current.onQueueRefresh?.(); },
    onCollectionsChanged: () => { refreshDetail(); callbacks.current.onQueueRefresh?.(); },
    verdict,
  };
}
