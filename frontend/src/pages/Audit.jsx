import { useCallback, useState } from 'react';
import {
  auditInit,
  auditNext,
  auditSubmit,
  auditMetrics,
  auditReset,
} from '../api/auditApi.js';
import VerdictPicker from '../components/VerdictPicker.jsx';
import { pretty } from '../utils/priorityLabels.js';
import { humanizeError } from '../utils/humanizeError.js';
import { StatusBanner } from '../components/library/shared.jsx';
import { formatPercent } from './triageHelpers.js';

// Re-label Audit page — port of the `activeTab === 'audit'` block from
// zotero_summarizer/web/ui.html. Functional parity with the Alpine version:
// init/resume a session, blind-relabel one candidate at a time, view metrics.
// Layout & styling kept close to the original; no React Query here because
// the workflow is a strict request-response sequence (init -> next -> submit
// -> next -> ... -> metrics) and React Query's caching offers no value.

function formatNumber(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return 'n/a';
  return Number(value).toFixed(digits);
}

export default function Audit() {
  const [session, setSession] = useState(null);
  const [candidate, setCandidate] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const fetchNext = useCallback(async () => {
    try {
      const data = await auditNext();
      setCandidate(data?.candidate ?? null);
    } catch (err) {
      setCandidate(null);
      setError(`Fetch-next failed: ${humanizeError(err)}`);
    }
  }, []);

  const handleInit = useCallback(async () => {
    setError('');
    setMetrics(null);
    setBusy(true);
    try {
      const data = await auditInit({ sample_size: 100, seed: 42, resume_if_exists: true });
      setSession(data);
      await fetchNext();
    } catch (err) {
      setError(`Init failed: ${humanizeError(err)}`);
    } finally {
      setBusy(false);
    }
  }, [fetchNext]);

  const handleSubmit = useCallback(
    async (newPriority) => {
      if (!candidate) return;
      setError('');
      setBusy(true);
      try {
        const data = await auditSubmit(candidate.item_key, newPriority);
        setSession(data);
        await fetchNext();
      } catch (err) {
        setError(`Submit failed: ${humanizeError(err)}`);
      } finally {
        setBusy(false);
      }
    },
    [candidate, fetchNext],
  );

  const handleMetrics = useCallback(async () => {
    setError('');
    setBusy(true);
    try {
      const data = await auditMetrics();
      setMetrics(data);
    } catch (err) {
      // Backend returns 400 "no_responses" when the session has zero
      // submitted re-labels. Surface a friendly nudge instead of the
      // raw HTTP code.
      if (err && err.status === 400) {
        setError('Submit at least one re-label first, then click Show metrics.');
      } else {
        setError(`Metrics failed: ${humanizeError(err)}`);
      }
    } finally {
      setBusy(false);
    }
  }, []);

  const handleReset = useCallback(async () => {
    if (!window.confirm('Reset the audit session? All current responses will be lost.')) return;
    setError('');
    setBusy(true);
    try {
      await auditReset();
      setSession(null);
      setCandidate(null);
      setMetrics(null);
    } catch (err) {
      setError(`Reset failed: ${humanizeError(err)}`);
    } finally {
      setBusy(false);
    }
  }, []);

  return (
    <div className="glass rounded-2xl border border-slate-200 p-4">
      <div className="flex flex-wrap items-baseline justify-between mb-3 gap-2">
        <div>
          <h2 className="text-lg font-bold">Re-label Audit</h2>
          <p className="text-xs text-slate-500 mt-1 max-w-2xl">
            Blind re-label of papers you labelled ≥ 90 days ago. Measures your test-retest reliability
            (Cohen's κ, ICC, Pearson r). The Pearson r is the empirical ceiling on any model's
            Spearman ρ.
          </p>
        </div>
        <div className="space-x-2">
          <button
            type="button"
            onClick={handleInit}
            disabled={busy}
            className="px-3 py-1 rounded bg-teal-700 text-white text-sm hover:bg-teal-800 disabled:bg-slate-300 disabled:text-slate-500"
          >
            Start / Resume
          </button>
          <button
            type="button"
            onClick={handleMetrics}
            disabled={busy}
            className="px-3 py-1 rounded bg-slate-200 text-slate-800 text-sm hover:bg-slate-300 disabled:opacity-50"
          >
            Show metrics
          </button>
          <button
            type="button"
            onClick={handleReset}
            disabled={busy}
            className="px-3 py-1 rounded bg-rose-100 text-rose-800 text-sm border border-rose-200 hover:bg-rose-200 disabled:opacity-50"
          >
            Reset session
          </button>
        </div>
      </div>

      <StatusBanner message={error} isError={Boolean(error)} />

      {/* Session summary */}
      {session && (
        <div className="mb-4 p-3 rounded bg-slate-50 border border-slate-200 text-sm">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div>
              <span className="font-semibold">Target / actual:</span>{' '}
              <span>{session.sample_size_target} / {session.sample_size_actual}</span>
            </div>
            <div>
              <span className="font-semibold">Answered:</span> <span>{session.answered}</span>
            </div>
            <div>
              <span className="font-semibold">Remaining:</span> <span>{session.remaining}</span>
            </div>
            <div>
              <span className="font-semibold">Seed:</span> <span>{session.seed}</span>
            </div>
          </div>
          {/* Goal-Gradient: a visible answered/target bar so the blind re-label run
              shows how far along it is at a glance, not just two raw counts. */}
          {Number(session.sample_size_actual) > 0 && (
            <div
              className="mt-3"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={Number(session.sample_size_actual)}
              aria-valuenow={Number(session.answered) || 0}
              aria-label="Audit progress"
            >
              <div className="h-1.5 rounded-full bg-slate-200 overflow-hidden">
                <div
                  className="h-full rounded-full bg-teal-500 transition-all"
                  style={{ width: `${Math.min(100, Math.round(((Number(session.answered) || 0) / Number(session.sample_size_actual)) * 100))}%` }}
                />
              </div>
            </div>
          )}
          {session.by_age_bucket && (
            <div className="mt-2 text-xs text-slate-600">
              By age bucket:{' '}
              {Object.entries(session.by_age_bucket).map(([bucket, n]) => (
                <span key={bucket} className="mr-3">
                  {bucket}={n}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Current candidate (blind) */}
      {candidate && (
        <div className="p-4 rounded border border-slate-300 bg-white">
          <div className="text-xs text-slate-500 mb-1">
            Age bucket: {candidate.age_bucket} ({candidate.days_since_added} days)
          </div>
          <h3 className="text-base font-bold mb-2">{candidate.title}</h3>
          <div className="text-sm text-slate-700 mb-1">
            {candidate.authors || '(no authors)'}
          </div>
          <div className="text-xs text-slate-500 mb-3">{candidate.venue || '(no venue)'}</div>
          <div className="text-sm whitespace-pre-line text-slate-800 mb-4">
            {candidate.abstract}
          </div>
          <VerdictPicker size="md" disabled={busy} onPick={handleSubmit} />
        </div>
      )}

      {/* All done */}
      {session && session.remaining === 0 && !candidate && (
        <div className="p-4 rounded border border-emerald-300 bg-emerald-50 text-sm">
          All candidates labelled. Click <strong>Show metrics</strong> above.
        </div>
      )}

      {/* Empty state when no session yet */}
      {!session && !error && (
        <p className="text-sm text-slate-500">
          No active audit session. Click <strong>Start / Resume</strong> to begin.
        </p>
      )}

      {/* Metrics panel */}
      {metrics && (
        <div className="mt-4 p-3 rounded border border-violet-300 bg-violet-50 text-sm">
          <div className="font-bold mb-2">Reliability metrics (n={metrics.n_paired})</div>
          <table className="w-full text-sm">
            <tbody>
              <tr>
                <td className="font-semibold pr-3 py-0.5">Cohen's κ (4-class)</td>
                <td className="py-0.5">{formatNumber(metrics.cohen_kappa)}</td>
                <td className="text-xs text-slate-600 pl-3 py-0.5">
                  Landis-Koch: &lt;0.2 slight, 0.2-0.4 fair, 0.4-0.6 moderate, 0.6-0.8 substantial.
                </td>
              </tr>
              <tr>
                <td className="font-semibold pr-3 py-0.5">Cohen's κ (weighted, ordinal)</td>
                <td className="py-0.5">{formatNumber(metrics.cohen_kappa_weighted)}</td>
                <td className="text-xs text-slate-600 pl-3 py-0.5">
                  Penalises distant disagreements more.
                </td>
              </tr>
              <tr>
                <td className="font-semibold pr-3 py-0.5">ICC(2,1)</td>
                <td className="py-0.5">{formatNumber(metrics.icc_2_1)}</td>
                <td className="text-xs text-slate-600 pl-3 py-0.5">
                  Koo-Li: &lt;0.5 poor, 0.5-0.75 moderate, 0.75-0.9 good.
                </td>
              </tr>
              <tr>
                <td className="font-semibold pr-3 py-0.5">Pearson r (CEILING)</td>
                <td className="text-violet-800 font-bold py-0.5">{formatNumber(metrics.pearson_r)}</td>
                <td className="text-xs text-slate-600 pl-3 py-0.5">
                  Upper bound on Spearman ρ any model can achieve.
                </td>
              </tr>
              <tr>
                <td className="font-semibold pr-3 py-0.5">Spearman ρ</td>
                <td className="py-0.5">{formatNumber(metrics.spearman_rho)}</td>
                <td className="text-xs text-slate-600 pl-3 py-0.5">
                  Rank correlation between old and new continuous scores.
                </td>
              </tr>
            </tbody>
          </table>
          {metrics.by_age_bucket && (
            <div className="mt-3 text-xs text-slate-600">
              <span className="font-semibold">By age bucket:</span>{' '}
              {Object.entries(metrics.by_age_bucket).map(([bucket, k]) => (
                <span key={bucket} className="mr-3">
                  {bucket}: κ={formatNumber(k, 2)}
                </span>
              ))}
            </div>
          )}
          {metrics.by_class && (
            <div className="mt-1 text-xs text-slate-600">
              <span className="font-semibold">P(agree | original):</span>{' '}
              {Object.entries(metrics.by_class).map(([cls, p]) => (
                <span key={cls} className="mr-3">
                  {pretty(cls)}: {formatPercent(p)}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
