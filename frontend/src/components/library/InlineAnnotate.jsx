import PaperDetailView from '../paper/PaperDetailView/index.jsx';
import { StatusBanner } from './shared.jsx';
import usePaperReview from '../../hooks/usePaperReview.js';

// Inline review panel: expands under a Read-next row. The rich paper-detail
// assembly (the DECIDE/ACT zones — digest, brief, abstract, ask, verdict, tags,
// collection) is the shared PaperDetailView in `editable` mode; the detail-fetch
// + verdict-mutation wiring is the shared usePaperReview hook (also used by the
// full-page /paper/:key review), so neither surface re-implements it. Picking
// `dont_read` IS the old "Remove" path — it also queues a ❌ Zotero tag, so there
// is ONE reject path (Occam's Razor). onSaved collapses + refetches (a
// verdicted/engagement-tagged paper drops out); onQueueRefresh refetches without
// collapsing.
export default function InlineAnnotate({
  itemKey, collections = [], onSaved, onQueueRefresh,
  // Override path from the Confirm/Override card: when set, pre-select the fleet's
  // PROPOSED verdict in the picker instead of the server's derived priority — so
  // "Override" lands on the proposal, one click from the same decision.
  derivedPriorityOverride = null,
}) {
  const { detail, isLoading, error, refreshDetail, onTagsChanged, onCollectionsChanged, verdict } =
    usePaperReview(itemKey, { onSaved, onQueueRefresh, derivedPriorityOverride });

  return (
    // A single connecting accent rule ties the expanded review to its row (Law
    // of Uniform Connectedness) — not a tinted box around boxes.
    <div className="mt-1 border-l-2 border-teal-300 pl-4 pr-1 py-1 space-y-3">
      {isLoading && <div className="text-xs text-slate-500">Loading detail…</div>}
      {error && (
        <StatusBanner message={`Detail load failed: ${error.message || error}`} isError />
      )}
      {detail && (
        <PaperDetailView
          mode="editable"
          detail={detail}
          itemKey={itemKey}
          collections={collections}
          // Compact decision card: the full digest/figures/abstract live in the
          // new-tab brief — show the verdict spine + "Open full review", not a
          // duplicate of the brief. Reader/abstract off via the show flags.
          compact
          show={{ reader: false, abstract: false }}
          onDeepReviewDone={refreshDetail}
          onTagsChanged={onTagsChanged}
          onCollectionsChanged={onCollectionsChanged}
          verdict={verdict}
        />
      )}
    </div>
  );
}
