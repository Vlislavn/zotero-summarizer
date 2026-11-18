import PaperDetailView from '../paper/PaperDetailView/index.jsx';
import { StatusBanner } from './shared.jsx';
import AbstractBlock from '../paper/PaperDetailView/AbstractBlock.jsx';
import VerdictPanel from '../VerdictPanel.jsx';
import { Disclosure } from '../paper/review/primitives.jsx';

// Body of the Read-next detail (LibraryDetail). The rich paper-detail
// assembly (the DECIDE/ACT zones — digest, brief, abstract, ask, verdict, tags,
// collection) is the shared PaperDetailView in `editable` mode; the detail-fetch
// + verdict-mutation wiring is the shared usePaperReview hook (also used by the
// full-page /paper/:key review), so neither surface re-implements it. Picking
// `dont_read` IS the one reject path — it also queues a ❌ Zotero tag, so there
// is ONE reject path (Occam's Razor). onSaved closes the detail + refetches (a
// verdicted/engagement-tagged paper drops out); onQueueRefresh refetches without
// closing. The verdict itself is set by the detail's VerdictBar; the editor (Edit · Delete · free
// text) is the «Verdict comment» disclosure. A paper with no review digest (≈3 in 4 of the queue) shows its abstract
// and says «No review yet» — the decision needs something to read. `showTitle`: the
// paper has no list row to supply the title. `review` = usePaperReview(itemKey, …), owned by the
// detail (its bar needs the saved verdict too).
export default function InlineAnnotate({ itemKey, review, collections = [], showTitle = false, onNotesDirtyChange }) {
  const { detail, isLoading, error, refreshDetail, onTagsChanged, onCollectionsChanged, verdict } = review;

  return (
    // A single connecting accent rule ties the expanded review to its row (Law
    // of Uniform Connectedness) — not a tinted box around boxes.
    <div className="mt-1 border-l-2 border-teal-300 pl-4 pr-1 py-1 space-y-3">
      {isLoading && <div className="text-xs text-slate-500">Loading detail…</div>}
      {error && (
        <StatusBanner message={`Detail load failed: ${error.message || error}`} isError />
      )}
      {detail && showTitle && <p className="text-sm font-medium text-slate-900">{detail.title}</p>}
      {detail && !detail.deep_review?.digest && (
        <div className="space-y-1">
          <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-slate-500">Abstract</p>
          {detail.abstract ? <AbstractBlock abstract={detail.abstract} /> : <p className="text-[13px] text-slate-500">No abstract on file.</p>}
          <p className="text-[12px] text-slate-500">No review yet</p>
        </div>
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
          show={{ reader: false, abstract: false, verdict: false }}
          onDeepReviewDone={refreshDetail}
          onTagsChanged={onTagsChanged}
          onCollectionsChanged={onCollectionsChanged}
          onNotesDirtyChange={onNotesDirtyChange}
          extras={(
            <div className="pt-3">
              <Disclosure summary="Verdict comment">
                <VerdictPanel itemKey={itemKey} existingVerdict={verdict.existing} {...verdict} />
              </Disclosure>
            </div>
          )}
        />
      )}
    </div>
  );
}
