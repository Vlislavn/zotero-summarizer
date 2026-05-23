// Settings → Model card.
//
// Reads `GET /api/admin/model` and renders the freshest trained
// classifier's provenance: classifier name, training size, OOF Spearman
// (the canonical model-quality metric for the regression target), AUC
// from the appended FAIR run-log, when it was trained, on which git
// commit, and the prediction thresholds.
//
// Empty state when no model is on disk yet — the user clicks
// "Retrain model" in AdminSection to populate it.
import { useQuery } from '@tanstack/react-query';
import { fetchModelCard } from '../api/settingsApi.js';

function Field({ label, value, mono = false, title = null }) {
  return (
    <div title={title || undefined}>
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`text-sm text-slate-900 ${mono ? 'font-mono' : 'font-medium'}`}>
        {value ?? <span className="text-slate-400">—</span>}
      </div>
    </div>
  );
}

function formatBytes(n) {
  if (typeof n !== 'number') return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function formatThresholds(t) {
  if (!t) return '—';
  const parts = ['keep', 'must', 'could']
    .map((k) => (t[k] != null ? `${k}=${Number(t[k]).toFixed(3)}` : null))
    .filter(Boolean);
  return parts.length ? parts.join(' · ') : '—';
}

function extractAUC(runlog) {
  // The FAIR log stores ``cv.auc`` (binary AUC on the keep/skip split).
  // Newer regression runs may carry it under different keys; fall back
  // gracefully to ``oof_auc`` and ``metrics.auc``.
  if (!runlog) return null;
  if (runlog.cv?.auc != null) return Number(runlog.cv.auc);
  if (runlog.oof_auc != null) return Number(runlog.oof_auc);
  if (runlog.metrics?.auc != null) return Number(runlog.metrics.auc);
  return null;
}

function PerClassTable({ perClass }) {
  if (!perClass) return null;
  const rows = Object.entries(perClass);
  if (rows.length === 0) return null;
  return (
    <div className="mt-3">
      <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">
        Per-class (from latest CV)
      </div>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-slate-500">
            <th className="font-medium py-0.5">Class</th>
            <th className="font-medium py-0.5">Precision</th>
            <th className="font-medium py-0.5">Recall</th>
            <th className="font-medium py-0.5">F1</th>
            <th className="font-medium py-0.5">Support</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([cls, m]) => (
            <tr key={cls} className="border-t border-slate-100">
              <td className="py-0.5 font-mono text-slate-700">{cls}</td>
              <td className="py-0.5">{m.precision != null ? Number(m.precision).toFixed(2) : '—'}</td>
              <td className="py-0.5">{m.recall != null ? Number(m.recall).toFixed(2) : '—'}</td>
              <td className="py-0.5">{m.f1 != null ? Number(m.f1).toFixed(2) : '—'}</td>
              <td className="py-0.5 text-slate-500">{m.support ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ModelCard() {
  const q = useQuery({
    queryKey: ['admin-model-card'],
    queryFn: fetchModelCard,
    staleTime: 60_000,
    // Refetch on focus so a fresh train in another tab shows up.
    refetchOnWindowFocus: true,
  });

  return (
    <div className="glass rounded-2xl border border-slate-200 p-4 mt-4">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500">
          Current model
        </h3>
        <button
          type="button"
          onClick={() => q.refetch()}
          className="text-xs text-teal-700 hover:text-teal-900"
        >
          {q.isFetching ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {q.isLoading && (
        <p className="text-xs text-slate-500 mt-2">Loading model metadata…</p>
      )}

      {q.error && (
        <p className="text-xs text-rose-700 mt-2">
          Failed to load: {q.error.message || String(q.error)}
        </p>
      )}

      {q.data && q.data.model === null && (
        <p className="text-xs text-slate-600 mt-2">
          No trained model on disk yet. Click <b>Retrain model</b> below to
          fit one against your current labels.
        </p>
      )}

      {q.data && q.data.model && (
        <ModelDetails model={q.data.model} />
      )}
    </div>
  );
}

function ModelDetails({ model }) {
  const auc = extractAUC(model.runlog);
  return (
    <div className="mt-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-3">
        <Field label="Classifier" value={model.classifier_name} />
        <Field label="Objective" value={model.objective || 'classification'} />
        <Field
          label="OOF Spearman ρ"
          value={
            model.oof_spearman != null
              ? Number(model.oof_spearman).toFixed(4)
              : '—'
          }
          title="Out-of-fold Spearman correlation against the relevance label [1, 5]. Closer to 1 is better."
        />
        <Field
          label="Binary AUC"
          value={auc != null ? auc.toFixed(4) : '—'}
          title="From the FAIR run-log. Computed during the keep/skip CV pass."
        />
        <Field label="N train" value={model.n_train ?? '—'} />
        <Field label="N positive (library)" value={model.n_positive_library ?? '—'} />
        <Field label="Feature dim" value={model.feature_dim ?? '—'} />
        <Field
          label="Trained at"
          value={model.trained_at || '—'}
          title="UTC"
        />
        <Field label="Git commit" value={model.git_commit || '—'} mono />
        <Field
          label="Golden CSV sha"
          value={model.golden_csv_sha256_prefix || '—'}
          mono
          title="First 12 chars of the SHA-256 of the CSV the model was trained on."
        />
        <Field
          label="Thresholds"
          value={formatThresholds(model.thresholds)}
          mono
          title="Decision thresholds used to bucket calibrated scores into priority classes."
        />
        <Field
          label="Joblib"
          value={`${formatBytes(model.joblib_size_bytes)}`}
          title={model.joblib_path}
        />
      </div>

      {model.runlog?.cv?.metrics_vs_gold?.per_class && (
        <PerClassTable perClass={model.runlog.cv.metrics_vs_gold.per_class} />
      )}

      {model.runlog && (
        <details className="mt-3 text-xs">
          <summary className="cursor-pointer text-slate-500 hover:text-slate-800">
            Full run-log entry
          </summary>
          <pre className="mt-2 p-2 bg-slate-50 border border-slate-200 rounded font-mono text-[10px] overflow-x-auto">
            {JSON.stringify(model.runlog, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
