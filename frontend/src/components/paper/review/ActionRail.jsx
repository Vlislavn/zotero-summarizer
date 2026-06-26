import VerdictPanel from '../../VerdictPanel.jsx';
import CollectionEditor from '../CollectionEditor.jsx';
import TagOfInterestEditor from '../TagOfInterestEditor.jsx';
import AskPaperBox from '../../library/AskPaperBox.jsx';
import { Disclosure } from './primitives.jsx';

// The persistent decision rail for the story page: the verdict editor (the day's
// primary action), one-tap filing into Read Next, tags behind a fold, and the
// grounded Ask chat — all reachable while the story scrolls (Serial Position: the
// decision lives at the top of the read AND here at the side). Reuses the existing
// editors verbatim; the chat reuses AskPaperBox in its always-open "rail" variant
// (same grounded/abstaining logic, no re-generation).
export default function ActionRail({
  itemKey, detail, collections, verdict, onTagsChanged, onCollectionsChanged, hasPdf, canAsk = hasPdf,
}) {
  return (
    <div className="space-y-5">
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
      <div className="border-t border-slate-200/60 pt-4">
        <CollectionEditor
          itemKey={itemKey}
          current={detail.collections}
          collections={collections}
          onChanged={onCollectionsChanged}
        />
      </div>
      <div className="border-t border-slate-200/60 pt-4">
        <Disclosure summary="Tags">
          <TagOfInterestEditor itemKey={itemKey} tags={detail.tags} onChanged={onTagsChanged} />
        </Disclosure>
      </div>
      {canAsk && (
        <div className="border-t border-slate-200/60 pt-4">
          <AskPaperBox itemKey={itemKey} variant="rail" allowRawPdfModes={hasPdf} />
        </div>
      )}
    </div>
  );
}
