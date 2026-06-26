import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  fetchConfig,
  getJob,
  refreshLabels,
  retrain,
} from '../api/settingsApi.js';
import SpinnerBase from './ui/Spinner.jsx';
import Button from './ui/Button.jsx';
import { Banner } from './form/Fields.jsx';

// Admin section for the Settings page. ONE long-running model-lifecycle action:
//
//   Retrain model — async POST /api/admin/retrain, then poll
//   GET /api/admin/jobs/{job_id} every 2s until status leaves "running".
//
// Retrain ALWAYS pulls fresh labels from Zotero first (the standalone "Refresh
// labels" button was removed — it was a strict subset of this), and trains
// whatever classifier the GATE is configured to use (Settings → Advanced →
// Classifier model), so there is no second classifier picker here.
//
// Backend contract: zotero_summarizer/api/routes/admin.py.

function Spinner() {
  return <SpinnerBase size="md" color="slate-dark" className="align-[-2px]" />;
}

function formatThresholds(thresholds) {
  if (!thresholds || typeof thresholds !== 'object') return '';
  return Object.entries(thresholds).map(([k, v]) => `${k}=${v}`).join(' ');
}

export default function AdminSection() {
  const queryClient = useQueryClient();
  // The gate's configured classifier is the single source of truth — retrain
  // trains THAT model, not a locally-picked one (Settings → Advanced owns the choice).
  const configQuery = useQuery({ queryKey: ['runtime-config'], queryFn: fetchConfig });
  const classifierName = configQuery.data?.classifier_gate?.model_name || 'logreg';

  const [jobId, setJobId] = useState(null);
  const [lastFinishedAt, setLastFinishedAt] = useState(null);
  const [startError, setStartError] = useState(null);
  // 'refreshing' (labels export from Zotero, 2-30s) → 'starting' (POST retrain).
  const [startPhase, setStartPhase] = useState(null);

  const startMutation = useMutation({
    // Retrain ALWAYS pulls fresh labels first (Tesler: the system owns the
    // label→train sequence) so a `label:must_read` tag typed in Zotero minutes
    // ago reaches the golden CSV + label_verdicts before training.
    mutationFn: async () => {
      setStartPhase('refreshing');
      await refreshLabels();
      setStartPhase('starting');
      return retrain({ classifier_name: classifierName, n_folds: 5 });
    },
    onMutate: () => setStartError(null),
    onSuccess: (data) => {
      if (data && data.job_id) setJobId(data.job_id);
    },
    onError: (err) => {
      setStartError(err?.message || String(err));
      setJobId(null);
    },
    onSettled: () => setStartPhase(null),
  });

  const jobQuery = useQuery({
    queryKey: ['admin-job', jobId],
    queryFn: () => getJob(jobId),
    enabled: Boolean(jobId),
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 2000 : false),
    refetchIntervalInBackground: false,
  });

  const job = jobQuery.data;
  const jobErr = jobQuery.error?.message;

  // When the job leaves "running", capture finished_at and refresh the read-only
  // ModelCard so its metadata replaces the stale. useEffect, not bare setState.
  const jobFinishedAt = job && job.status !== 'running' ? job.finished_at : null;
  useEffect(() => {
    if (jobFinishedAt && jobFinishedAt !== lastFinishedAt) {
      setLastFinishedAt(jobFinishedAt);
      queryClient.invalidateQueries({ queryKey: ['admin-model-card'] });
    }
  }, [jobFinishedAt, lastFinishedAt, queryClient]);

  const starting = startMutation.isPending;
  const running = Boolean(job && job.status === 'running');
  const succeeded = Boolean(job && job.status === 'succeeded');
  const failed = Boolean(job && job.status === 'failed');
  const buttonDisabled = starting || running;

  const progress = job?.progress || { done: 0, total: 0 };
  const pct = progress.total > 0 ? Math.min(100, Math.round((progress.done / progress.total) * 100)) : null;

  // ONE status slot: progress while running, else the latest terminal outcome.
  let statusNode = null;
  if (running) {
    statusNode = (
      <div className="space-y-2">
        <div className="flex items-center gap-2 text-sm text-slate-600">
          <Spinner />
          <span>{progress.total > 0 ? `Training (fold ${progress.done}/${progress.total})…` : 'Training…'}</span>
        </div>
        {pct !== null && (
          <div className="w-full h-2 bg-slate-200 rounded overflow-hidden" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
            <div className="h-full bg-teal-500 transition-all" style={{ width: `${pct}%` }} />
          </div>
        )}
      </div>
    );
  } else if (starting) {
    statusNode = (
      <div className="flex items-center gap-2 text-sm text-slate-600">
        <Spinner />
        <span>{startPhase === 'refreshing' ? 'Refreshing labels from Zotero…' : 'Starting…'}</span>
      </div>
    );
  } else if (succeeded && job.result) {
    statusNode = (
      <Banner kind="success">
        {`✓ Trained ${job.result.classifier_name} on ${job.result.n_train} rows. Holdout ${job.result.n_holdout}. Thresholds: ${formatThresholds(job.result.thresholds)}.`}
      </Banner>
    );
  } else if (failed && job.error) {
    statusNode = <Banner kind="error">{job.error}</Banner>;
  } else if (startError) {
    statusNode = <Banner kind="error">{startError}</Banner>;
  } else if (jobErr && !job) {
    statusNode = <Banner kind="error">{jobErr}</Banner>;
  } else if (lastFinishedAt) {
    statusNode = <p className="text-xs text-slate-500 font-mono">Last run: {lastFinishedAt}</p>;
  }

  return (
    <div className="glass rounded-2xl border border-slate-200 p-4 mt-4">
      <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-500">
        Model lifecycle
      </h3>
      <p className="text-xs text-slate-500 mt-1 mb-4">
        Pulls your latest labels from Zotero (label:* tags, emojis, notes) and re-trains
        the feed gate (<span className="font-mono">{classifierName}</span>) on them — one
        click, nothing to refresh first. Takes 1-5 minutes.
      </p>
      <div className="space-y-2">
        <Button onClick={() => startMutation.mutate()} disabled={buttonDisabled}>
          {starting
            ? (startPhase === 'refreshing' ? 'Refreshing labels…' : 'Starting…')
            : running ? 'Training…' : 'Retrain model'}
        </Button>
        {statusNode}
      </div>
    </div>
  );
}
