// One quiet line on a Today card whose review is outdated but of the same PDF:
// the recommendation stays, Regenerate reuses the paper page's deep-review runner
// and its 3 s status poll to show the live step instead of a static «regenerating…».
import { useIsFetching, useQueryClient } from '@tanstack/react-query';
import useDeepReviewRunner from '../../hooks/useDeepReviewRunner.js';
import { humanizeError } from '../../utils/humanizeError.js';
import { formatDuration } from '../../utils/stepModelStatus.js';
import { TODAY_MEMBERS_QUERY } from '../../utils/todayVisit.js';

// «Regenerating · checking quality · 2 min · #2 in queue».
function progressText(status) {
  const progress = status.progress || {};
  const step = String(progress.phase_label || '').replace(/…$/, '');
  const started = Date.parse(status.started_at);
  const seconds = progress.total_elapsed_seconds ?? (Number.isNaN(started) ? null : (Date.now() - started) / 1000);
  return ['Regenerating', step && `${step.charAt(0).toLowerCase()}${step.slice(1)}`,
    seconds != null && formatDuration(seconds),
    status.queue_ahead > 0 && `#${status.queue_ahead} in queue`].filter(Boolean).join(' · ');
}

export default function OutdatedReviewNote({ itemKey }) {
  const client = useQueryClient();
  const runner = useDeepReviewRunner(itemKey, {
    onDone: () => client.invalidateQueries({ queryKey: TODAY_MEMBERS_QUERY }),
  });
  // The job is done; the card flips to the new review once the member facts refetch.
  const refreshing = useIsFetching({ queryKey: TODAY_MEMBERS_QUERY }) > 0 && runner.status.status === 'ready';
  // "Not connected" only when a successful model check says so (reachable === false, or disabled/absent);
  // pending, failed, timed-out or unknown (configuration_busy, approval required) says nothing here.
  const unavailable = runner.llmChecked && !runner.checkingStatus
    && (!runner.llm || runner.llm.enabled === false || runner.llm.reachable === false);
  const failure = runner.status.status === 'error' && runner.status.error ? humanizeError(runner.status.error) : null;
  return (
    <p role="status" className="mt-2 flex flex-wrap items-center gap-x-2 border-l-2 border-amber-300 pl-3 text-[12px] leading-snug text-amber-800"
      title="Shown from cache: the review model or its inputs changed since it was written. The PDF is the same, so the recommendation is kept.">
      <span className="font-medium">Outdated review</span>
      {runner.running ? <span>· {progressText(runner.status)}</span>
        : refreshing ? <span>· regenerated, updating…</span>
          : unavailable ? <span>· AI not connected (Settings)</span> : (
            <button type="button" onClick={() => runner.run()} disabled={runner.checkingStatus || !runner.llmAvailable}
              className="inline-flex min-h-11 items-center font-medium text-forest-800 underline underline-offset-2 hover:text-forest-700 disabled:opacity-50">
              Regenerate
            </button>
          )}
      {!runner.running && failure && <span role="alert" className="text-rose-700">Last attempt failed: {failure}</span>}
      {runner.error && <span role="alert" className="text-rose-700">{humanizeError(runner.error)}</span>}
    </p>
  );
}
