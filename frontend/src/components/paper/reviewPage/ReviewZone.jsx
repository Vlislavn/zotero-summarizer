import PaperReview from '../review/PaperReview.jsx';
import EvidenceFold from '../review/EvidenceFold.jsx';
import MethodCard from '../review/MethodCard.jsx';
import FeedbackMode from '../review/FeedbackMode.jsx';
import ReviewFailure from '../ReviewFailure.jsx';
import ReviewModelStatus from '../ReviewModelStatus.jsx';
import PaperFactsCard from '../../card/PaperFactsCard.jsx';
import { FullTextAccessNotice } from '../../library/shared.jsx';
import Spinner from '../../ui/Spinner.jsx';
import Button from '../../ui/Button.jsx';
import { useSettlingAfterRun } from './paperReviewHooks.js';
import { fmt, hasUsableDigest, reviewedValue } from './reviewPageModel.js';

function GeneratingPanel({ status }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-5 text-[13px] text-slate-600" role="status" aria-live="polite">
      <div className="flex items-center gap-2 font-medium text-slate-700">
        <Spinner size="sm" color="slate" /> Generating the review…
      </div>
      {status.progress?.phase_label && (
        <div className="mt-1 text-slate-500">
          {status.progress.phase_label}
          {status.progress.sub?.total > 0 && ` ${status.progress.sub.done}/${status.progress.sub.total}`}
        </div>
      )}
      {status.progress?.total_elapsed_seconds != null && (
        <div className="mt-0.5 text-slate-400">
          {fmt(status.progress.total_elapsed_seconds)} elapsed
          {status.progress.eta_seconds != null && ` · ~${fmt(status.progress.eta_seconds)} remaining`}
        </div>
      )}
    </div>
  );
}

// While generating: the live progress panel; otherwise the manual Generate button (when no usable review).
function RunControl({ runner, usable }) {
  const { status, llmAvailable, running } = runner;
  if (running) return <GeneratingPanel status={status} />;
  if (usable) return null;
  return (
    <Button
      onClick={() => runner.run()}
      disabled={!llmAvailable || status.status === 'unavailable' || runner.checkingStatus}
      size="sm"
    >
      Generate review
    </Button>
  );
}

function StatusUnavailable({ status, runner }) {
  return (
    <div className="space-y-1 text-[12px] text-amber-800" role="status">
      <div>Review status unavailable: {status.error}. The server job may still be running.</div>
      <button type="button" onClick={runner.retryStatus} disabled={runner.checkingStatus}
        className="font-medium underline disabled:opacity-50">
        Retry status
      </button>
    </div>
  );
}

// The cached review, wrapped in the feedback mode once it has a review timestamp.
function ReviewBody({ deep, settling, runner, sectionOverlay, itemKey, facts, onFeedbackActive }) {
  const review = <PaperReview deep={settling ? { ...deep, review_freshness: null } : deep} flat sectionOverlay={sectionOverlay} facts={facts}
    methodCard={<MethodCard itemKey={itemKey} />}
    evidence={<EvidenceFold itemKey={itemKey} lost={deep.not_established_fields || []} />}
    onRegenerate={runner.checkingStatus ? null : () => runner.run()} />;
  return deep.reviewed_at
    ? <FeedbackMode key={`${itemKey}|${deep.reviewed_at}`} itemKey={itemKey} reviewedAt={deep.reviewed_at} onActiveChange={onFeedbackActive}>{review}</FeedbackMode>
    : review;
}

// The review zone: the cached review (flat + located findings) when present; else
// the access banners + a prominent Generate button + a live skeleton while it
// runs. The runner auto-fires on open for a paper with a PDF (the user opted into
// auto-generate-on-open), so the common case renders instantly.
export default function ReviewZone({ deep, runner, sectionOverlay, itemKey, onFeedbackActive }) {
  const { status, error, online, running } = runner;
  const settling = useSettlingAfterRun(running, deep);
  const reviewed = reviewedValue(deep);
  const usable = hasUsableDigest(deep);
  // Card facts (#98) sit right under the verdict card; with no review yet they lead the zone.
  const showReview = reviewed && !running;
  const facts = <PaperFactsCard cardKey={itemKey} variant="paper" rubric={deep?.quality?.rubric} />;
  return (
    <div className="space-y-3">
      {!showReview && facts}
      {!running && !usable && <ReviewModelStatus runner={runner} />}
      <FullTextAccessNotice deep={deep} />

      {showReview && (
        <ReviewBody deep={deep} settling={settling} runner={runner} sectionOverlay={sectionOverlay}
          itemKey={itemKey} facts={facts} onFeedbackActive={onFeedbackActive} />
      )}
      <RunControl runner={runner} usable={usable} />
      <ReviewFailure status={status} />
      {online && status.status === 'unavailable' && status.error && <StatusUnavailable status={status} runner={runner} />}
      {error && <div className="text-[12px] text-rose-700">{error}</div>}
    </div>
  );
}
