import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { feedbackAnalysisApi } from '../api/feedbackAnalysisApi.js';
import { humanizeError } from '../utils/humanizeError.js';
import { Banner } from './form/Fields.jsx';
import Button from './ui/Button.jsx';
import FeedbackAnalysisReport from './FeedbackAnalysisReport.jsx';
import { STATUS_KEY, useFeedbackStatus } from '../hooks/useFeedbackStatus.js';

// Ops → System → "Your decisions": run the analysis of accumulated decisions, read the
// latest report, switch the weekly run. Backend: api/routes/feedback_analysis.py.
export function WeeklyAnalysisRow() {
  const { data } = useFeedbackStatus();
  if (!data) return null;
  return <div className="flex flex-wrap items-center justify-between gap-3 py-5 border-t border-slate-200">
    <div className="min-w-0 flex-1 basis-48"><h3 className="text-base font-medium text-slate-900">Weekly decision analysis</h3>
      <p className="mt-1 text-sm text-slate-600">{data.weekly_enabled ? 'On · runs every week with the feed daemon' : 'Off · run it yourself from Ops'}</p></div>
    <Link to="/ops?tab=system" className="inline-flex min-h-11 items-center rounded-lg border border-slate-300 px-4 py-2 text-sm text-forest-800 hover:bg-linen focus-visible:ring-2 focus-visible:ring-forest-800">Change in Ops</Link>
  </div>;
}

export default function FeedbackAnalysisCard() {
  const cache = useQueryClient();
  const status = useFeedbackStatus();
  const job = status.data?.job;
  const latestId = status.data?.latest?.run_id;
  const report = useQuery({ queryKey: ['feedback-analysis-report', latestId], queryFn: feedbackAnalysisApi.latest, enabled: Boolean(latestId), retry: false });
  const refresh = () => cache.invalidateQueries({ queryKey: STATUS_KEY });
  const run = useMutation({ mutationFn: feedbackAnalysisApi.run, onSettled: refresh });
  const weekly = useMutation({ mutationFn: feedbackAnalysisApi.setWeekly, onSettled: refresh });
  const running = run.isPending || job?.state === 'running';
  return <section aria-labelledby="your-decisions" className="rounded-2xl border border-slate-200 bg-white p-4 space-y-3">
    <div><h3 id="your-decisions" className="font-display text-xl font-light text-forest-800">Your decisions</h3>
      <p className="text-sm text-slate-600 mt-1">What your ratings and label changes say, and how they compare with what the app predicted.</p></div>
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <Button disabled={running || status.isError} onClick={() => run.mutate()}>{running ? 'Analysing…' : 'Run now'}</Button>
      {status.data && <label className="flex min-h-11 items-center gap-2 text-sm text-slate-700">
        <input type="checkbox" className="rounded accent-forest-800" checked={status.data.weekly_enabled} disabled={weekly.isPending}
          onChange={(e) => weekly.mutate(e.target.checked)} />Run every week automatically</label>}
    </div>
    {running && <p role="status" className="text-sm text-slate-600">Analysing your decisions. Usually under a minute; you can leave this page.</p>}
    {status.isError && <Banner kind="error">Status unavailable. {humanizeError(status.error)}</Banner>}
    {run.isError && <Banner kind="error">Could not start: {humanizeError(run.error)}</Banner>}
    {weekly.isError && <Banner kind="error">Could not change the weekly setting: {humanizeError(weekly.error)}</Banner>}
    {job?.state === 'failure' && <Banner kind="error">{job.message}</Banner>}
    {status.data && !latestId && !running && <p className="text-sm text-slate-600">No report yet. Run it once, or wait for the weekly run.</p>}
    {latestId && <p className="text-xs text-slate-500">Latest report: {status.data.latest.started.slice(0, 16).replace('T', ' ')} UTC</p>}
    {report.isError && <Banner kind="error">Report unavailable. {humanizeError(report.error)}</Banner>}
    {report.data && <FeedbackAnalysisReport report={report.data} />}
  </section>;
}
