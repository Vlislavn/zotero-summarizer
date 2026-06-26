// Settings → Model card.
//
// Reads `GET /api/admin/model` and renders a compact AUDIT TRAIL of the freshest
// trained classifier — enough to answer "is my gate fresh and trained on my
// data": classifier, training size, when it was trained, the honest reading-
// decision ρ, and the prediction thresholds. Not a model-engineering dashboard;
// the raw run-log, per-class CV table and inflated all-rows metrics were removed
// (Miller's Law / Pareto — a clinician's config page, not a debug surface).
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

function formatThresholds(t) {
  if (!t) return '—';
  const parts = ['keep', 'must', 'could']
    .map((k) => (t[k] != null ? `${k}=${Number(t[k]).toFixed(3)}` : null))
    .filter(Boolean);
  return parts.length ? parts.join(' · ') : '—';
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
        <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-500">
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
  return (
    <div className="mt-3">
      <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-3">
        <Field label="Classifier" value={model.classifier_name} />
        <Field label="N train" value={model.n_train ?? '—'} />
        <Field label="Trained at" value={model.trained_at || '—'} title="UTC" />
        <Field
          label="Reading-decision ρ"
          value={
            model.oof_spearman_verified != null
              ? Number(model.oof_spearman_verified).toFixed(3)
              : '—'
          }
        />
        <Field
          label="Thresholds"
          value={formatThresholds(model.thresholds)}
          mono
        />
      </div>
      <p className="mt-3 text-[11px] text-slate-400 leading-relaxed">
        Reading-decision ρ is the gate's out-of-fold ranking correlation on the
        papers you actually read — expected to be low; the live slate blends it
        with goal similarity. This card is an audit trail, not a tuning surface.
      </p>
    </div>
  );
}
