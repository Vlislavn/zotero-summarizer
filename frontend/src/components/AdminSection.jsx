import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getJob,
  refreshLabels,
  retrain,
} from '../api/settingsApi.js';
import SpinnerBase from './ui/Spinner.jsx';

// Admin section for the Settings page. Two long-running model-lifecycle
// operations live here:
//
//   1. Refresh labels  — synchronous POST /api/admin/refresh-labels
//      Re-exports the golden CSV from the live Zotero library so any new
//      emoji-tags / annotations / notes get folded into the next train.
//
//   2. Retrain model   — async POST /api/admin/retrain, then poll
//      GET /api/admin/jobs/{job_id} every 2s until status leaves "running".
//      Hybrid GT overlay (label_verdicts + derived CSV) is automatic
//      server-side.
//
// Backend contract: zotero_summarizer/api/routes/admin.py.
//
// We extracted this out of Settings.jsx so the parent page stays under the
// 500-LOC budget and the model-lifecycle concerns are self-contained.

const CLASSIFIER_OPTIONS = ['logreg', 'lightgbm', 'tabpfn'];

const INPUT_CLS =
  'mt-1 p-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-teal-500';

function Banner({ kind, children }) {
  if (!children) return null;
  const cls =
    kind === 'error'
      ? 'bg-rose-50 border-rose-200 text-rose-800'
      : 'bg-emerald-50 border-emerald-200 text-emerald-900';
  return (
    <div
      role="status"
      aria-live="polite"
      className={`px-3 py-2 rounded-lg border text-sm ${cls}`}
    >
      {children}
    </div>
  );
}

// Thin wrapper over the shared Spinner that pins the exact size/color/baseline
// alignment the admin job rows used (align-[-2px] keeps it on the text baseline).
function Spinner() {
  return <SpinnerBase size="md" color="slate-dark" className="align-[-2px]" />;
}

function formatByClass(byClass) {
  if (!byClass || typeof byClass !== 'object') return '';
  const parts = Object.entries(byClass).map(([k, v]) => `${k}=${v}`);
  return parts.join(', ');
}

function formatThresholds(thresholds) {
  if (!thresholds || typeof thresholds !== 'object') return '';
  const parts = Object.entries(thresholds).map(([k, v]) => `${k}=${v}`);
  return parts.join(' ');
}

// --- Refresh labels -------------------------------------------------------

function RefreshLabelsCard() {
  const [lastFinishedAt, setLastFinishedAt] = useState(null);

  const mutation = useMutation({
    mutationFn: refreshLabels,
    onSuccess: (data) => {
      if (data && data.finished_at) setLastFinishedAt(data.finished_at);
    },
  });

  const running = mutation.isPending;
  const errMsg = mutation.error?.message;
  const data = mutation.data;

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={() => mutation.mutate()}
        disabled={running}
        className="w-full sm:w-auto px-4 py-2.5 rounded-lg bg-slate-900 text-white text-sm font-semibold hover:bg-slate-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
      >
        {running ? 'Refreshing…' : 'Refresh labels from Zotero'}
      </button>
      <p className="text-xs text-slate-500">
        Re-export the golden CSV from your live Zotero library. Run this
        after you've added emojis/annotations/notes you want included.
      </p>
      {running && (
        <div className="flex items-center gap-2 text-sm text-slate-600">
          <Spinner />
          <span>Refreshing… (this takes 2-30 seconds)</span>
        </div>
      )}
      {!running && data && data.ok && (
        <Banner kind="success">
          {`✓ Refreshed at ${data.finished_at}. Exported ${data.total} rows: ${formatByClass(data.by_class)}.`}
        </Banner>
      )}
      {!running && errMsg && <Banner kind="error">{errMsg}</Banner>}
      {!running && lastFinishedAt && (
        <p className="text-xs text-slate-500 font-mono">Last run: {lastFinishedAt}</p>
      )}
    </div>
  );
}

// --- Retrain model --------------------------------------------------------

function RetrainCard() {
  const queryClient = useQueryClient();
  const [classifierName, setClassifierName] = useState('logreg');
  const [jobId, setJobId] = useState(null);
  // Captured at submit-time so the success banner doesn't switch to a
  // different label if the user changes the dropdown mid-train.
  const [submittedName, setSubmittedName] = useState(null);
  const [lastFinishedAt, setLastFinishedAt] = useState(null);
  // Surface 409 "another retrain running" and similar POST-time failures.
  const [startError, setStartError] = useState(null);
  // 'refreshing' (labels export from Zotero, 2-30s) → 'starting' (POST retrain).
  const [startPhase, setStartPhase] = useState(null);

  const startMutation = useMutation({
    // Retrain ALWAYS pulls fresh labels first (Tesler: the system owns the
    // label→train sequence). Without this, a `label:must_read` tag typed in
    // Zotero minutes ago reaches NEITHER the golden CSV NOR label_verdicts
    // (the tag→verdict reconcile runs during export), so the retrain the user
    // just asked for would silently train without their newest ground truth.
    mutationFn: async ({ classifier_name }) => {
      setStartPhase('refreshing');
      await refreshLabels();
      setStartPhase('starting');
      return retrain({ classifier_name, n_folds: 5 });
    },
    onMutate: () => {
      setStartError(null);
    },
    onSuccess: (data) => {
      if (data && data.job_id) setJobId(data.job_id);
    },
    onError: (err) => {
      setStartError(err?.message || String(err));
      setJobId(null);
    },
    onSettled: () => setStartPhase(null),
  });

  // Poll the job every 2s while it's running. React Query handles the
  // unmount-cleanup automatically (the interval stops when the consumer
  // is gone), so navigating away mid-train is safe.
  const jobQuery = useQuery({
    queryKey: ['admin-job', jobId],
    queryFn: () => getJob(jobId),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'running' ? 2000 : false;
    },
    refetchIntervalInBackground: false,
  });

  const job = jobQuery.data;
  const jobLoading = jobQuery.isLoading;
  const jobErr = jobQuery.error?.message;

  // When the job transitions out of "running", capture finished_at for the
  // session's Last-run line. We mirror the value from the job record so
  // success and failure paths both leave a trace. useEffect, not bare
  // setState, because setting state during render would loop.
  const jobFinishedAt =
    job && job.status !== 'running' ? job.finished_at : null;
  useEffect(() => {
    if (jobFinishedAt && jobFinishedAt !== lastFinishedAt) {
      setLastFinishedAt(jobFinishedAt);
      // The model on disk changed — refresh the read-only ModelCard so
      // the new metadata (AUC, n_train, thresholds) replaces the stale.
      queryClient.invalidateQueries({ queryKey: ['admin-model-card'] });
    }
  }, [jobFinishedAt, lastFinishedAt, queryClient]);

  const starting = startMutation.isPending;
  const running = Boolean(job && job.status === 'running');
  const succeeded = Boolean(job && job.status === 'succeeded');
  const failed = Boolean(job && job.status === 'failed');

  const buttonDisabled = starting || running;

  // Progress bar — only meaningful once total > 0 (e.g. cross-val fold count).
  const progress = job?.progress || { done: 0, total: 0 };
  const pct =
    progress.total > 0
      ? Math.min(100, Math.round((progress.done / progress.total) * 100))
      : null;

  function handleSubmit() {
    setSubmittedName(classifierName);
    startMutation.mutate({ classifier_name: classifierName });
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-col sm:flex-row sm:items-end gap-3">
        <label className="block sm:w-48">
          <span className="text-xs font-semibold text-slate-700 uppercase tracking-wider">
            Classifier
          </span>
          <select
            value={classifierName}
            onChange={(e) => setClassifierName(e.target.value)}
            disabled={buttonDisabled}
            className={`${INPUT_CLS} bg-white w-full disabled:bg-slate-100 disabled:cursor-not-allowed`}
          >
            {CLASSIFIER_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={buttonDisabled}
          className="px-4 py-2.5 rounded-lg bg-slate-900 text-white text-sm font-semibold hover:bg-slate-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
        >
          {starting
            ? (startPhase === 'refreshing' ? 'Refreshing labels…' : 'Starting…')
            : running
              ? 'Training…'
              : 'Retrain model'}
        </button>
      </div>

      <p className="text-xs text-slate-500">
        Pulls your latest labels from Zotero (label:* tags, emojis, notes) and
        re-trains the feed gate on them — one click, nothing to refresh first.
        Takes 1-5 minutes.
      </p>

      {/* Running state — progress bar + one-line status. */}
      {running && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-sm text-slate-600">
            <Spinner />
            <span>
              {progress.total > 0
                ? `Training (fold ${progress.done}/${progress.total})…`
                : 'Training…'}
            </span>
          </div>
          {pct !== null && (
            <div
              className="w-full h-2 bg-slate-200 rounded overflow-hidden"
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className="h-full bg-teal-500 transition-all"
                style={{ width: `${pct}%` }}
              />
            </div>
          )}
        </div>
      )}

      {/* Success state — green banner with training metadata. */}
      {!running && succeeded && job.result && (
        <Banner kind="success">
          {`✓ Trained ${job.result.classifier_name || submittedName} on ${job.result.n_train} rows. Holdout ${job.result.n_holdout}. Thresholds: ${formatThresholds(job.result.thresholds)}.`}
        </Banner>
      )}

      {/* Failure paths — backend job error OR start-time 409 OR polling error. */}
      {!running && failed && job.error && (
        <Banner kind="error">{job.error}</Banner>
      )}
      {!running && startError && <Banner kind="error">{startError}</Banner>}
      {!running && jobErr && !job && <Banner kind="error">{jobErr}</Banner>}

      {!running && jobLoading && jobId && !job && (
        <p className="text-xs text-slate-500">Connecting to job {jobId}…</p>
      )}

      {!running && lastFinishedAt && (
        <p className="text-xs text-slate-500 font-mono">
          Last run: {lastFinishedAt}
        </p>
      )}
    </div>
  );
}

// --- Section wrapper ------------------------------------------------------

export default function AdminSection() {
  return (
    <div className="glass rounded-2xl border border-slate-200 p-4 mt-4">
      <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500">
        Model lifecycle
      </h3>
      <p className="text-xs text-slate-500 mt-1 mb-4">
        Re-export labels and retrain the classifier gate. Both actions are
        idempotent — safe to run repeatedly.
      </p>
      <div className="space-y-6">
        <RefreshLabelsCard />
        <div className="border-t border-slate-200" />
        <RetrainCard />
      </div>
    </div>
  );
}
