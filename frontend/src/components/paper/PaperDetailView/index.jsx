import LinksRow from '../LinksRow.jsx';
import DeepReviewSection from '../DeepReviewSection.jsx';
import TagOfInterestEditor from '../TagOfInterestEditor.jsx';
import CollectionEditor from '../CollectionEditor.jsx';
import PaperReaderPane from '../../library/PaperReaderPane.jsx';
import AskPaperBox from '../../library/AskPaperBox.jsx';
import VerdictPanel from '../../VerdictPanel.jsx';
import AbstractBlock from './AbstractBlock.jsx';

// ONE configurable paper-detail assembly, extracted from the duplicated bodies
// of AnnotationVerdict (the "detail" right column) and InlineAnnotate (the
// expand-a-row panel). It composes the EXISTING shared children — LinksRow,
// DeepReviewSection, PaperReaderPane, AskPaperBox, an abstract block,
// TagOfInterestEditor, CollectionEditor, VerdictPanel — so the two surfaces stop
// re-implementing the same wiring.
//
// Behaviour-preserving by construction: each call site keeps its exact look via
// `mode` + `show` flags:
//
//   mode="readonly"  (Annotate) — flat sections (the parent's PaperDetailLayout
//     supplies the space-y-5), an EXPANDABLE abstract, NO zone borders, and NO
//     verdict/collection here (Annotate renders VerdictPanel in its sticky
//     bottom strip and has no collection editor). Annotate-only tails
//     (Provenance / Annotations / Notes) are passed in as `extras`.
//
//   mode="editable"  (InlineAnnotate) — the bordered "Decide" / "Act" zones, a
//     <details> abstract, the VerdictPanel + Tag + Collection editors inline in
//     the Act zone.
//
// `show` flags gate the optional sections so each site renders exactly what it
// shows today.
//
// Props:
//   mode:        'readonly' | 'editable'
//   detail:      the /api/golden/review-detail payload
//   itemKey:     the Zotero item key
//   show:        { reader, ask, abstract, tags, collection, verdict, links,
//                  deepReview } — booleans (sensible per-mode defaults below)
//   readerOpen, onReaderOpenChange: brief-pane disclosure state (lifted by
//                the parent so it survives re-renders)
//   collections: flat [{key,name,depth}] list for the CollectionEditor
//   onDeepReviewDone, onTagsChanged, onCollectionsChanged: refetch callbacks
//   verdict:     { derivedPriority, existing, onSubmit, onDelete, submitting,
//                  submitError, deleting, deleteError } for the editable
//                VerdictPanel (editable mode only)
//   extras:      extra nodes appended after the body (readonly tails)

// Section heading inside the editable panel (Law of Common Region).
function ZoneLabel({ children }) {
  return (
    <div className="text-[11px] uppercase tracking-wider font-semibold text-slate-500 select-none">
      {children}
    </div>
  );
}

export default function PaperDetailView({
  mode = 'readonly',
  detail,
  itemKey,
  show = {},
  readerOpen = false,
  onReaderOpenChange,
  collections = [],
  onDeepReviewDone,
  onTagsChanged,
  onCollectionsChanged,
  verdict = {},
  extras = null,
}) {
  if (!detail) return null;
  const editable = mode === 'editable';
  const hasPdf = detail.has_pdf;

  // Per-mode defaults that mirror what each surface renders today.
  const showLinks = show.links ?? true;
  const showDeepReview = show.deepReview ?? true;
  const showReader = (show.reader ?? true) && hasPdf;
  const showAsk = (show.ask ?? true) && hasPdf;
  const showAbstract = show.abstract ?? true;
  const showTags = show.tags ?? true; // both surfaces render the tag editor
  const showCollection = (show.collection ?? editable) && editable;
  const showVerdict = (show.verdict ?? editable) && editable;

  if (editable) {
    return (
      <>
        {showLinks && <LinksRow detail={detail} itemKey={itemKey} />}

        {/* DECIDE: everything you read to make the call. */}
        <section className="rounded-lg border border-slate-200 bg-white/70 p-2.5 space-y-2.5">
          <ZoneLabel>Decide</ZoneLabel>
          <DeepReviewSection
            itemKey={itemKey}
            deep={detail.deep_review}
            hasPdf={hasPdf}
            onDone={onDeepReviewDone}
          />
          {showReader && (
            <PaperReaderPane
              itemKey={itemKey}
              open={readerOpen}
              onOpenChange={onReaderOpenChange}
            />
          )}
          {showAbstract && (
            <AbstractBlock abstract={detail.abstract} variant="details" />
          )}
          {showAsk && <AskPaperBox itemKey={itemKey} />}
        </section>

        {/* ACT: the verdict is the primary action; tag + collection follow. */}
        <section className="rounded-lg border border-slate-200 bg-white/70 p-2.5 space-y-2.5">
          <ZoneLabel>Act</ZoneLabel>
          {showVerdict && (
            <VerdictPanel
              itemKey={itemKey}
              derivedPriority={verdict.derivedPriority}
              existingVerdict={verdict.existing}
              onSubmit={verdict.onSubmit}
              onDelete={verdict.onDelete}
              submitting={verdict.submitting}
              submitError={verdict.submitError}
              deleting={verdict.deleting}
              deleteError={verdict.deleteError}
            />
          )}
          {showTags && (
            <TagOfInterestEditor
              itemKey={itemKey}
              tags={detail.tags}
              onChanged={onTagsChanged}
            />
          )}
          {showCollection && (
            <CollectionEditor
              itemKey={itemKey}
              current={detail.collections}
              collections={collections}
              onChanged={onCollectionsChanged}
            />
          )}
        </section>
        {extras}
      </>
    );
  }

  // readonly (Annotate): flat sections, expandable abstract, no zone borders.
  return (
    <>
      {showLinks && <LinksRow detail={detail} itemKey={itemKey} />}
      {showDeepReview && (
        <DeepReviewSection
          itemKey={itemKey}
          deep={detail.deep_review}
          hasPdf={hasPdf}
          onDone={onDeepReviewDone}
        />
      )}
      {(showReader || showAsk) && (
        <>
          {showReader && (
            <PaperReaderPane
              itemKey={itemKey}
              open={readerOpen}
              onOpenChange={onReaderOpenChange}
            />
          )}
          {showAsk && <AskPaperBox itemKey={itemKey} />}
        </>
      )}
      {showAbstract && (
        <AbstractBlock abstract={detail.abstract} variant="expandable" />
      )}
      {showTags && (
        <div>
          <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-2">
            Tags
          </h3>
          <TagOfInterestEditor
            itemKey={itemKey}
            tags={detail.tags}
            onChanged={onTagsChanged}
          />
        </div>
      )}
      {extras}
    </>
  );
}
