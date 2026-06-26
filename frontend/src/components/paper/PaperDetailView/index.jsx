import LinksRow from '../LinksRow.jsx';
import DeepReviewSection from '../DeepReviewSection.jsx';
import TagOfInterestEditor from '../TagOfInterestEditor.jsx';
import CollectionEditor from '../CollectionEditor.jsx';
import PaperReaderPane from '../../library/PaperReaderPane.jsx';
import AskPaperBox from '../../library/AskPaperBox.jsx';
import OpenBriefButton from '../../library/OpenBriefButton.jsx';
import VerdictPanel from '../../VerdictPanel.jsx';
import VerdictPicker from '../../VerdictPicker.jsx';
import AbstractBlock from './AbstractBlock.jsx';
import { Section, Disclosure } from '../review/primitives.jsx';

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

export default function PaperDetailView({
  mode = 'readonly',
  detail,
  itemKey,
  show = {},
  // compact (Library row card): drop the per-goal board, demote Ask behind a
  // disclosure, and lead the Decide zone with "Open full review" — the full
  // digest/figures/abstract live in the new-tab brief, not duplicated inline.
  compact = false,
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
  const showReader = show.reader ?? true;
  const showAsk = (show.ask ?? true) && hasPdf;
  const showAbstract = show.abstract ?? true;
  const showTags = show.tags ?? true; // both surfaces render the tag editor
  const showCollection = (show.collection ?? editable) && editable;
  const showVerdict = (show.verdict ?? editable) && editable;

  // ONE flat, hairline-divided column for both modes (Law of Common Region:
  // a divider, not a box). Editable splits into Decide / Act chunks (Miller's
  // Law); read-only drops the verdict/collection (Annotate owns the verdict in
  // its sticky strip) and labels each section.
  if (editable) {
    return (
      <>
        {/* The Abstract/DOI/PDF links are low-salience navigation — they live in
            a muted strip at the very BOTTOM (below) on both surfaces, so the page
            opens on the verdict banner, the decision (Serial Position), never on
            links. */}
        <div className="divide-y divide-slate-200/60">
          {/* compact drops the "Decide"/"Act" eyebrows — the chip banner + the
              verdict picker are self-evidently those zones (Occam). */}
          <Section label={compact ? undefined : 'Decide'}>
            <div className="space-y-4">
              {compact && (
                <OpenBriefButton itemKey={itemKey} hasPdf={hasPdf} label="Open full review ↗" />
              )}
              <DeepReviewSection
                itemKey={itemKey}
                deep={detail.deep_review}
                hasPdf={hasPdf}
                onDone={onDeepReviewDone}
                compact={compact}
              />
              {showReader && (
                <PaperReaderPane itemKey={itemKey} open={readerOpen} onOpenChange={onReaderOpenChange} hasPdf={hasPdf} />
              )}
              {showAbstract && <AbstractBlock abstract={detail.abstract} variant="details" />}
              {showAsk && (
                compact
                  ? <Disclosure summary="Ask this paper"><AskPaperBox itemKey={itemKey} /></Disclosure>
                  : <AskPaperBox itemKey={itemKey} />
              )}
            </div>
          </Section>

          {/* Act zone, three salience tiers (Von Restorff: one loud accent):
              verdict (loud) → collection (quiet, Read Next default) → tags (long
              tail). The compact card uses the one-tap VerdictPicker; the full
              page uses the VerdictPanel (comment + delete live there — Tesler:
              the rare op gets the larger surface). */}
          <Section label={compact ? undefined : 'Act'}>
            <div className="space-y-4">
              {showVerdict && (compact ? (
                <div>
                  <h3 className="text-sm font-bold text-slate-900 mb-2">Your verdict</h3>
                  <VerdictPicker
                    value={verdict.existing?.user_priority ?? null}
                    onPick={(p) => verdict.onSubmit({ user_priority: p, comment: '' })}
                    disabled={verdict.submitting}
                    size="md"
                  />
                  {verdict.submitError && (
                    <div className="mt-1 text-[11px] text-rose-700">{verdict.submitError}</div>
                  )}
                </div>
              ) : (
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
              ))}
              {/* Collection lifted OUT of the disclosure — filing into Read Next
                  is a daily primary action, not the long tail (Pareto / Serial
                  Position): one click, default preselected. */}
              {showCollection && (
                <CollectionEditor
                  itemKey={itemKey}
                  current={detail.collections}
                  collections={collections}
                  onChanged={onCollectionsChanged}
                />
              )}
              {/* Tags = the long tail — folded behind a disclosure on BOTH
                  surfaces (Miller / subtract-20%): a paper can carry 10+ subject
                  tags that would wrap into a noisy strip next to the decision. The
                  verdict (loud) and collection (visible) stay; tags are one click
                  away when you want to change them. */}
              {showTags && (
                <Disclosure summary="Tags">
                  <TagOfInterestEditor itemKey={itemKey} tags={detail.tags} onChanged={onTagsChanged} />
                </Disclosure>
              )}
            </div>
          </Section>
        </div>
        {showLinks && (
          <div className="pt-3 text-[11px]">
            <LinksRow detail={detail} itemKey={itemKey} />
          </div>
        )}
        {extras}
      </>
    );
  }

  // readonly (Annotate): same flat column, labelled sections, no zone borders.
  return (
    <>
      {showLinks && <LinksRow detail={detail} itemKey={itemKey} />}
      <div className="divide-y divide-slate-200/60">
        {showDeepReview && (
          <Section label="Review">
            <DeepReviewSection
              itemKey={itemKey}
              deep={detail.deep_review}
              hasPdf={hasPdf}
              onDone={onDeepReviewDone}
            />
          </Section>
        )}
        {(showReader || showAsk) && (
          <Section>
            <div className="space-y-4">
              {showReader && (
                <PaperReaderPane itemKey={itemKey} open={readerOpen} onOpenChange={onReaderOpenChange} hasPdf={hasPdf} />
              )}
              {showAsk && <AskPaperBox itemKey={itemKey} />}
            </div>
          </Section>
        )}
        {showAbstract && (
          <Section label="Abstract">
            <AbstractBlock abstract={detail.abstract} variant="expandable" />
          </Section>
        )}
        {showTags && (
          <Section label="Tags">
            <TagOfInterestEditor itemKey={itemKey} tags={detail.tags} onChanged={onTagsChanged} />
          </Section>
        )}
      </div>
      {extras}
    </>
  );
}
