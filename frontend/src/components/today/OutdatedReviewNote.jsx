// One quiet line on a Today card whose review is outdated but of the same PDF:
// the recommendation stays, Regenerate reuses the paper page's deep-review runner.
import { useQueryClient } from '@tanstack/react-query';
import useDeepReviewRunner from '../../hooks/useDeepReviewRunner.js';
import { TODAY_MEMBERS_QUERY } from '../../utils/todayVisit.js';

export default function OutdatedReviewNote({ itemKey }) {
  const client = useQueryClient();
  const runner = useDeepReviewRunner(itemKey, {
    onDone: () => client.invalidateQueries({ queryKey: TODAY_MEMBERS_QUERY }),
  });
  // "Not connected" only when a successful model check says so (reachable === false, or disabled/absent);
  // pending, failed, timed-out or unknown (configuration_busy, approval required) says nothing here.
  const unavailable = runner.llmChecked && !runner.checkingStatus
    && (!runner.llm || runner.llm.enabled === false || runner.llm.reachable === false);
  return (
    <p role="status" className="mt-2 flex flex-wrap items-center gap-x-2 border-l-2 border-amber-300 pl-3 text-[12px] leading-snug text-amber-800"
      title="Shown from cache: the review model or its inputs changed since it was written. The PDF is the same, so the recommendation is kept.">
      <span className="font-medium">Outdated review</span>
      {runner.running ? <span>· regenerating…</span>
        : unavailable ? <span>· AI not connected (Settings)</span> : (
          <button type="button" onClick={() => runner.run()} disabled={runner.checkingStatus || !runner.llmAvailable}
            className="inline-flex min-h-11 items-center font-medium text-forest-800 underline underline-offset-2 hover:text-forest-700 disabled:opacity-50">
            Regenerate
          </button>
        )}
      {runner.error && <span role="alert" className="text-rose-700">{runner.error}</span>}
    </p>
  );
}
