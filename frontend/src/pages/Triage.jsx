import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  fetchJobs,
  fetchJob,
  cancelJob,
  fetchCalibrationMetrics,
  submitResultFeedback,
} from '../api/triageApi.js';
import { humanizeError } from '../utils/humanizeError.js';
import Async from '../components/ui/Async.jsx';
import { ActionBadge } from '../components/ui/Badge.jsx';
import { useKeyboardNav } from '../hooks/useKeyboardNav.js';
import { useFocusOnChange } from '../hooks/useFocusOnChange.js';
import {
  progressPercent,
  formatPercent,
  statusTone,
  isTerminalStatus,
} from './triageHelpers.js';

// Triage Monitor page — port of the `activeTab === 'triage'` block from
// zotero_summarizer/web/ui.html. Provides functional parity for the
// "monitor / cancel running jobs + view calibration metrics" use case.
// We poll the active job every 4 s while it is running so progress bars
// stay live without manual refresh.
//
// TODO(phase 1.18 cleanup): the Alpine block also used `selectedItems` from
// the Library tab to display the "selected in Library" hint and to kick off
// `startTriage`. Both are owned by the Library page in this React port, so
// jobs are *only* monitored here — to start a new job, go to Library and
// click "Run triage".

function StatusBanner({ message, isError }) {
  if (!message) return null;
  const cls = isError
    ? 'bg-rose-50 border-rose-200 text-rose-800'
    : 'bg-emerald-50 border-emerald-200 text-emerald-800';
  return (
    <div role="status" className={`my-2 p-2 rounded-lg border text-xs ${cls}`}>
      {message}
    </div>
  );
}

// A job status pill speaking the shared Badge tone vocabulary (running=teal,
// completed=emerald, failed=rose, cancelled/queued=slate).
function JobStatusPill({ status }) {
  return <ActionBadge tone={statusTone(status)}>{status || 'unknown'}</ActionBadge>;
}

function feedbackButtonClass(current, verdict) {
  const isActive = current === verdict;
  if (verdict === 'approve') {
    return isActive
      ? 'bg-emerald-600 text-white border-emerald-600'
      : 'bg-white text-emerald-700 border-emerald-300 hover:bg-emerald-50';
  }
  return isActive
    ? 'bg-rose-600 text-white border-rose-600'
    : 'bg-white text-rose-700 border-rose-300 hover:bg-rose-50';
}

// One completed-result card. EDGE 1: the Approve/Reject action row is pinned to
// the bottom of the card (`sticky bottom-0`) so it stays visible and reachable
// while the results list scrolls, instead of disappearing into the overflow.
// Handlers + verdict/submitting state are passed in unchanged.
function ResultCard({ result, current, submitting, onFeedback }) {
  return (
    <div className="border border-slate-200 rounded bg-slate-50 text-sm overflow-hidden">
      <div className="p-2 pb-1">
        <div className="font-medium">{result.title}</div>
        <div className="text-xs text-slate-600">
          Score:{' '}
          <span className="mono">
            {Number(result.composite_relevance_score || 0).toFixed(2)}
          </span>
          {' · Priority: '}<span>{result.reading_priority}</span>
          {' · Queued: '}<span>{result.queued_change_count || 0}</span>
        </div>
      </div>
      <div className="sticky bottom-0 flex items-center gap-2 px-2 py-1.5 bg-slate-50/95 border-t border-slate-200 backdrop-blur-sm">
        <button
          type="button"
          onClick={() => onFeedback(result.item_key, 'approve', result.title)}
          disabled={submitting}
          className={`px-3 py-1.5 rounded text-xs border ${feedbackButtonClass(current, 'approve')}`}
        >
          {submitting ? 'Saving…' : 'Approve'}
        </button>
        <button
          type="button"
          onClick={() => onFeedback(result.item_key, 'reject', result.title)}
          disabled={submitting}
          className={`px-3 py-1.5 rounded text-xs border ${feedbackButtonClass(current, 'reject')}`}
        >
          {submitting ? 'Saving…' : 'Reject'}
        </button>
        {current && (
          <ActionBadge tone={current === 'approve' ? 'emerald' : 'rose'}>
            {current === 'approve' ? 'Approved by you' : 'Rejected by you'}
          </ActionBadge>
        )}
      </div>
    </div>
  );
}

export default function Triage() {
  const [jobs, setJobs] = useState([]);
  const [activeJobId, setActiveJobId] = useState('');
  const [activeJob, setActiveJob] = useState(null);
  const [cancelingId, setCancelingId] = useState('');
  const [calibration, setCalibration] = useState(null);
  const [calibrationLoading, setCalibrationLoading] = useState(false);
  const [calibrationError, setCalibrationError] = useState(null);
  const [message, setMessage] = useState('');
  const [isError, setIsError] = useState(false);
  const [feedbackState, setFeedbackState] = useState({});
  const [feedbackSubmitting, setFeedbackSubmitting] = useState({});
  const [selectedJobIdx, setSelectedJobIdx] = useState(0);
  const pollRef = useRef(null);
  // Last seen status of the active job, so we can auto-refresh calibration the
  // moment a job transitions out of `running` (EDGE 4).
  const prevStatusRef = useRef(null);
  const jobListRef = useRef(null);

  const loadJobs = useCallback(async () => {
    try {
      const data = await fetchJobs();
      const list = data?.items || [];
      setJobs(list);
      setActiveJobId((prev) => prev || (list.length ? list[0].job_id : ''));
    } catch (err) {
      setMessage(`Failed to load jobs: ${humanizeError(err)}`);
      setIsError(true);
    }
  }, []);

  const loadJob = useCallback(async (jobId) => {
    if (!jobId) return;
    try {
      const data = await fetchJob(jobId);
      setActiveJob(data);
    } catch (err) {
      setMessage(`Failed to load job ${jobId}: ${humanizeError(err)}`);
      setIsError(true);
    }
  }, []);

  const loadCalibration = useCallback(async () => {
    setCalibrationLoading(true);
    setCalibrationError(null);
    try {
      const data = await fetchCalibrationMetrics();
      setCalibration(data);
    } catch (err) {
      // Non-fatal — calibration is informational, but surface it via <Async>
      // so a transient backend hiccup isn't silently swallowed.
      setCalibrationError(err);
    } finally {
      setCalibrationLoading(false);
    }
  }, []);

  // Initial load.
  useEffect(() => {
    loadJobs();
    loadCalibration();
  }, [loadJobs, loadCalibration]);

  // Pull job detail whenever activeJobId changes.
  useEffect(() => {
    if (activeJobId) loadJob(activeJobId);
  }, [activeJobId, loadJob]);

  // Poll while running.
  useEffect(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (activeJob?.status === 'running') {
      pollRef.current = setInterval(() => {
        loadJob(activeJobId);
        loadJobs();
      }, 4000);
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [activeJob?.status, activeJobId, loadJob, loadJobs]);

  // EDGE 4: when the active job transitions out of `running` (the poll loop or a
  // cancel landed a terminal status), refresh calibration so the metrics panel
  // reflects the just-finished run without a separate manual click. Reuses the
  // same `loadCalibration` fetch — no new endpoint or data flow.
  useEffect(() => {
    const status = activeJob?.status;
    const wasRunning = prevStatusRef.current === 'running';
    if (wasRunning && status && status !== 'running' && isTerminalStatus(status)) {
      loadCalibration();
    }
    prevStatusRef.current = status;
  }, [activeJob?.status, loadCalibration]);

  const handleCancel = useCallback(async (jobId) => {
    if (!jobId || cancelingId) return;
    setCancelingId(jobId);
    try {
      const data = await cancelJob(jobId);
      setActiveJob((prev) => (prev?.job_id === jobId ? { ...prev, status: data?.status || 'cancelled' } : prev));
      await loadJobs();
      await loadJob(jobId);
    } catch (err) {
      setMessage(`Cancel failed: ${humanizeError(err)}`);
      setIsError(true);
    } finally {
      setCancelingId('');
    }
  }, [cancelingId, loadJob, loadJobs]);

  const handleFeedback = useCallback(async (itemKey, verdict, title = '') => {
    if (!itemKey || feedbackSubmitting[itemKey]) return;
    setFeedbackSubmitting((prev) => ({ ...prev, [itemKey]: true }));
    try {
      await submitResultFeedback(itemKey, verdict);
      setFeedbackState((prev) => ({ ...prev, [itemKey]: verdict }));
      setMessage(`${title || itemKey}: feedback saved, pending Zotero tag change queued.`);
      setIsError(false);
      // Refresh calibration after feedback is stored.
      loadCalibration();
    } catch (err) {
      setMessage(`${title || itemKey}: feedback failed — ${humanizeError(err)}`);
      setIsError(true);
    } finally {
      setFeedbackSubmitting((prev) => ({ ...prev, [itemKey]: false }));
    }
  }, [feedbackSubmitting, loadCalibration]);

  const periodKeys = useMemo(() => ['last_7d', 'last_30d', 'all_time'], []);
  const periodLabels = { last_7d: 'Last 7 days', last_30d: 'Last 30 days', all_time: 'All time' };

  // ---------- Recent-jobs keyboard nav (EDGE 2) ----------
  // Keep the highlighted index aligned with whichever job is actually open, so
  // j/k always start from the job on screen (and a jobs reload that reorders
  // the list doesn't leave the highlight stranded).
  useEffect(() => {
    const idx = jobs.findIndex((j) => j.job_id === activeJobId);
    if (idx >= 0) setSelectedJobIdx(idx);
  }, [jobs, activeJobId]);

  const moveSelection = useCallback(
    (delta) => {
      setSelectedJobIdx((prev) => {
        if (jobs.length === 0) return 0;
        const next = Math.min(jobs.length - 1, Math.max(0, prev + delta));
        return next;
      });
    },
    [jobs.length],
  );

  const openSelectedJob = useCallback(() => {
    const job = jobs[selectedJobIdx];
    if (job) setActiveJobId(job.job_id);
  }, [jobs, selectedJobIdx]);

  useKeyboardNav({
    onPrev: () => moveSelection(-1),
    onNext: () => moveSelection(1),
    onAction: openSelectedJob,
    actionKeys: { Enter: 'open' },
    hasSelection: jobs.length > 0,
    deps: [moveSelection, openSelectedJob, jobs.length],
  });

  // Keep focus on the jobs list after the selection moves so j/k stay live and
  // a screen reader announces the move.
  useFocusOnChange(selectedJobIdx, jobListRef);

  return (
    <div className="glass rounded-2xl border border-slate-200 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-bold">Triage Monitor</h2>
          <p className="text-sm text-slate-600">
            Watch active triage jobs and review calibration metrics. Start new jobs from the
            Library tab.
          </p>
        </div>
        <button
          type="button"
          onClick={() => { loadJobs(); if (activeJobId) loadJob(activeJobId); }}
          className="px-3 py-1.5 rounded-lg border border-slate-300 text-xs hover:bg-slate-50"
        >
          Refresh
        </button>
      </div>

      <StatusBanner message={message} isError={isError} />

      {activeJob && (
        <div className="mt-4 border border-slate-200 rounded-xl p-3 bg-white">
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <div className="flex items-center gap-2">
              <span>Job <span className="mono">{activeJob.job_id}</span></span>
              <JobStatusPill status={activeJob.status} />
            </div>
            <div className="flex items-center gap-2">
              <span>
                {(activeJob.completed || 0)}/{(activeJob.total || 0)}
              </span>
              {activeJob.status === 'running' && (
                <button
                  type="button"
                  onClick={() => handleCancel(activeJob.job_id)}
                  disabled={cancelingId === activeJob.job_id}
                  className="px-2 py-1 rounded bg-rose-600 text-white disabled:opacity-50"
                >
                  {cancelingId === activeJob.job_id ? 'Cancelling...' : 'Cancel'}
                </button>
              )}
            </div>
          </div>
          <div
            className="w-full bg-slate-200 rounded-full h-3 mt-2 overflow-hidden"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progressPercent(activeJob)}
            aria-label="Job progress"
          >
            <div
              className="h-3 bg-teal-600 transition-all"
              style={{ width: `${progressPercent(activeJob)}%` }}
            />
          </div>
          <div className="text-xs text-slate-500 mt-1">
            {activeJob.current_title || activeJob.current_item_key || ''}
          </div>

          <div className="grid md:grid-cols-2 gap-3 mt-4">
            <div>
              <h3 className="font-semibold text-slate-700 mb-2">
                Completed Results{' '}
                <span className="font-normal text-slate-400">
                  ({(activeJob.results || []).length})
                </span>
              </h3>
              <p className="text-xs text-slate-500 mb-2">
                Approve or reject each verdict — feedback queues a Zotero tag change and
                updates calibration.
              </p>
              <div className="max-h-72 overflow-auto space-y-2 pr-1">
                {(activeJob.results || []).length === 0 && (
                  <div className="text-xs text-slate-500">No completed items yet.</div>
                )}
                {(activeJob.results || []).map((result) => (
                  <ResultCard
                    key={result.item_key}
                    result={result}
                    current={feedbackState[result.item_key]}
                    submitting={Boolean(feedbackSubmitting[result.item_key])}
                    onFeedback={handleFeedback}
                  />
                ))}
              </div>
            </div>
            <div>
              <h3 className="font-semibold text-slate-700 mb-2">
                Errors{' '}
                <span className="font-normal text-slate-400">
                  ({(activeJob.errors || []).length})
                </span>
              </h3>
              <div className="max-h-72 overflow-auto space-y-2 pr-1">
                {(activeJob.errors || []).length === 0 && (
                  <div className="text-xs text-slate-500">No errors.</div>
                )}
                {(activeJob.errors || []).map((err, idx) => (
                  <div
                    key={`${err.item_key}-${idx}`}
                    className="p-2 border border-red-200 rounded bg-red-50 text-sm"
                  >
                    <div className="font-medium">{err.item_key}</div>
                    <div className="text-xs text-red-700">{err.error}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="mt-4">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 mb-2">
          <h3 className="font-semibold text-slate-700">Recent Jobs</h3>
          {jobs.length > 0 && (
            <span className="text-xs text-slate-500">j/k move · enter open</span>
          )}
        </div>
        <div
          ref={jobListRef}
          tabIndex={jobs.length ? 0 : -1}
          role="listbox"
          aria-label="Recent triage jobs"
          aria-activedescendant={
            jobs[selectedJobIdx] ? `triage-job-${jobs[selectedJobIdx].job_id}` : undefined
          }
          className="space-y-2 outline-none focus-visible:ring-2 focus-visible:ring-teal-400 rounded-lg"
        >
          {jobs.map((job, idx) => {
            const isOpen = job.job_id === activeJobId;
            const isSelected = idx === selectedJobIdx;
            return (
              <button
                type="button"
                key={job.job_id}
                id={`triage-job-${job.job_id}`}
                role="option"
                aria-selected={isSelected}
                onClick={() => {
                  setSelectedJobIdx(idx);
                  setActiveJobId(job.job_id);
                }}
                className={`w-full text-left p-2 rounded border text-sm transition-colors ${
                  isOpen
                    ? 'border-teal-400 bg-teal-50'
                    : 'border-slate-200 bg-white hover:bg-slate-50'
                }${isSelected ? ' ring-2 ring-teal-400 ring-offset-1' : ''}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="mono">{job.job_id}</span>
                  <JobStatusPill status={job.status} />
                </div>
                <div className="text-xs text-slate-600">
                  {(job.completed || 0)}/{(job.total || 0)}
                </div>
              </button>
            );
          })}
          {jobs.length === 0 && (
            <div className="text-xs text-slate-500">No triage jobs recorded yet.</div>
          )}
        </div>
      </div>

      <div className="mt-4 border border-slate-200 rounded-xl p-3 bg-white">
        <div className="flex items-center justify-between gap-2">
          <h3 className="font-semibold text-slate-700">Calibration</h3>
          <button
            type="button"
            onClick={loadCalibration}
            className="text-xs px-2 py-1 rounded bg-slate-100 hover:bg-slate-200"
          >
            Refresh
          </button>
        </div>
        <Async
          loading={calibrationLoading}
          error={calibrationError}
          empty={!calibration}
          loadingText="Loading calibration metrics…"
          emptyMessage="No calibration data yet. Approve/reject triaged items to start learning."
        >
          <div className="mt-3 grid md:grid-cols-3 gap-3 text-xs">
            {periodKeys.map((periodKey) => {
              const p = calibration?.periods?.[periodKey] || {};
              return (
                <div key={periodKey} className="border border-slate-200 rounded-lg p-2 bg-slate-50">
                  <div className="font-semibold text-slate-700">{periodLabels[periodKey]}</div>
                  <div className="mt-1">Feedback: <span className="mono">{p.total_feedback ?? 0}</span></div>
                  <div>Approved: <span className="mono">{p.approved_count ?? 0}</span></div>
                  <div>Rejected: <span className="mono">{p.rejected_count ?? 0}</span></div>
                  <div>Agreement: <span className="mono">{formatPercent(p.agreement_rate)}</span></div>
                  <div>Precision: <span className="mono">{formatPercent(p.precision)}</span></div>
                  <div title="Counterfactual-audit estimate of how often the ML gate keeps the papers you'd actually want — the gate's online trust signal.">
                    Gate recall: <span className="mono">{formatPercent(p.recall)}</span>
                  </div>
                  <div>False positive: <span className="mono">{p.false_positive_count ?? 0}</span></div>
                  <div title="Papers the ML gate dropped but you approved on audit (🎲) — the gate's miss rate. Lower is better.">
                    Gate misses (audit FN): <span className="mono">{p.false_negative_count ?? 0}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </Async>
      </div>
    </div>
  );
}
