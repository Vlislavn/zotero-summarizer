import RelatedPapers from '../RelatedPapers.jsx';
import AbstractBlock from '../PaperDetailView/AbstractBlock.jsx';
import SectionMap from '../review/SectionMap.jsx';
import { Fold } from '../review/primitives.jsx';
import ReviewZone from './ReviewZone.jsx';
import FiguresFold from './FiguresFold.jsx';
import { hasSectionMap } from './reviewPageModel.js';

// The reading column: review, similar papers, figures (props, or null when hidden), paper map, abstract.
export default function PaperMain({ itemKey, detail, runner, onFeedbackActive, relatedRevision, summaries, abstractText, figures }) {
  const deep = detail.deep_review || null;
  const ov = deep?.section_overlay || null;
  return (
    <main className="min-w-0 max-w-[68ch]">
      <section id="review" className="scroll-mt-20 pb-2">
        <ReviewZone deep={deep} runner={runner} sectionOverlay={ov} itemKey={itemKey} onFeedbackActive={onFeedbackActive} />
      </section>

      {/* Next step after reading the review, so it sits right under it (not the last, forgotten fold). */}
      <Fold id="similar" label="Find similar papers" summary="Open sources, or papers you already saved">
        <RelatedPapers key={JSON.stringify([itemKey, relatedRevision, detail.user_note, detail.verdict])} itemKey={itemKey} heading={false} />
      </Fold>

      {figures && <FiguresFold itemKey={itemKey} {...figures} />}

      {hasSectionMap(ov) && <SectionMap overlay={ov} summaries={summaries} />}

      {abstractText && (
        <Fold id="abstract" label="Abstract" summary={abstractText.replace(/\s+/g, ' ').slice(0, 140)}>
          <AbstractBlock abstract={abstractText} variant="expandable" />
        </Fold>
      )}

    </main>
  );
}
