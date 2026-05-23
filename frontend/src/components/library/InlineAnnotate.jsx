import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchReviewDetail, submitVerdict, deleteVerdict } from '../../api/goldenApi.js';
import { queueRejectTag } from '../../api/libraryApi.js';
import VerdictPanel from '../VerdictPanel.jsx';
import LinksRow from '../paper/LinksRow.jsx';
import TagOfInterestEditor from '../paper/TagOfInterestEditor.jsx';
import DeepReviewSection from '../paper/DeepReviewSection.jsx';
import { StatusBanner } from './shared.jsx';

// Inline annotation panel: expands under a Read-next row so the user can open
// the paper (links), tag it, run a per-paper deep review, and act WITHOUT
// leaving the tab. Shares its rich pieces (LinksRow, TagOfInterestEditor,
// DeepReviewSection) with the Annotate page. onSaved collapses + refetches (a
// verdicted/engagement-tagged paper drops out); onQueueRefresh refetches
// without collapsing (free-text tag).
export default function InlineAnnotate({ itemKey, onSaved, onQueueRefresh }) {
  const queryClient = useQueryClient();
  const detailQuery = useQuery({
    queryKey: ['review-detail', itemKey],
    queryFn: () => fetchReviewDetail(itemKey),
  });
  const submitMutation = useMutation({ mutationFn: submitVerdict });
  const deleteMutation = useMutation({ mutationFn: () => deleteVerdict(itemKey) });
  const [actionBusy, setActionBusy] = useState('');
  const [actionErr, setActionErr] = useState(null);
  const detail = detailQuery.data;

  function refreshDetail() {
    queryClient.invalidateQueries({ queryKey: ['review-detail', itemKey] });
  }

  async function handleRemove() {
    setActionErr(null);
    setActionBusy('remove');
    try {
      await Promise.all([
        submitVerdict({ item_key: itemKey, user_priority: 'dont_read', comment: '' }),
        queueRejectTag(itemKey),
      ]);
      onSaved?.();
    } catch (e) {
      setActionErr(`Remove failed: ${e.message || e}`);
      setActionBusy('');
    }
  }

  return (
    <div className="mt-2 rounded-xl border border-teal-200 bg-teal-50/30 p-3 space-y-3">
      {detailQuery.isLoading && <div className="text-xs text-slate-500">Loading detail…</div>}
      {detailQuery.error && (
        <StatusBanner message={`Detail load failed: ${detailQuery.error.message || detailQuery.error}`} isError />
      )}
      {detail && (
        <>
          <LinksRow detail={detail} itemKey={itemKey} />

          <TagOfInterestEditor
            itemKey={itemKey}
            tags={detail.tags}
            onChanged={() => { refreshDetail(); onQueueRefresh?.(); }}
          />

          {detail.abstract && (
            <details>
              <summary className="cursor-pointer text-[11px] uppercase tracking-wider font-semibold text-slate-500 select-none">
                Abstract
              </summary>
              <p className="mt-1 text-xs text-slate-700 max-h-44 overflow-y-auto whitespace-pre-line">
                {detail.abstract}
              </p>
            </details>
          )}

          <div className="flex flex-wrap items-start gap-2">
            <div className="min-w-0 flex-1">
              <DeepReviewSection itemKey={itemKey} deep={detail.deep_review} onDone={refreshDetail} hasPdf={detail.has_pdf} />
            </div>
            <button
              type="button"
              onClick={handleRemove}
              disabled={!!actionBusy}
              className="px-3 py-1.5 rounded-lg bg-rose-600 text-white text-xs font-semibold hover:bg-rose-700 disabled:opacity-50"
              title="Drop from queue, mark dont_read, and queue a ❌ tag for Zotero (apply in Pending)"
            >
              {actionBusy === 'remove' ? 'Removing…' : 'Remove ❌'}
            </button>
          </div>
          {actionErr && <StatusBanner message={actionErr} isError />}

          <details>
            <summary className="cursor-pointer text-[11px] uppercase tracking-wider font-semibold text-slate-500 select-none">
              More verdict options
            </summary>
            <div className="mt-2">
              <VerdictPanel
                itemKey={itemKey}
                derivedPriority={detail.provenance?.derived_priority}
                existingVerdict={detail.verdict}
                onSubmit={({ user_priority, comment }) =>
                  submitMutation.mutate({ item_key: itemKey, user_priority, comment }, { onSuccess: onSaved })}
                onDelete={() => deleteMutation.mutate(undefined, { onSuccess: onSaved })}
                submitting={submitMutation.isPending}
                submitError={submitMutation.error?.message || null}
                deleting={deleteMutation.isPending}
                deleteError={deleteMutation.error?.message || null}
              />
            </div>
          </details>
        </>
      )}
    </div>
  );
}
