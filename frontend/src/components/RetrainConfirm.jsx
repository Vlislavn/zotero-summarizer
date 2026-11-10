import { createPortal } from 'react-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchModelCard } from '../api/settingsApi.js';
import Button from './ui/Button.jsx';

// Confirmation for "Retrain model": says what is replaced and how long it takes.
// Mounted only while open, so the model card is read only when asked for.
export default function RetrainConfirm({ classifierName, onConfirm, onCancel }) {
  const card = useQuery({ queryKey: ['admin-model-card'], queryFn: fetchModelCard, staleTime: 60_000 });
  const model = card.data?.model;
  const current = model
    ? `the live feed gate (${model.classifier_name}, trained ${model.trained_at || 'at an unknown time'} on ${model.n_train ?? '?'} papers)`
    : 'the live feed gate, if one is loaded';
  // Portal: the glass card ancestors (backdrop-filter) would otherwise clip a fixed overlay.
  return createPortal(<div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40 p-3"
    onKeyDown={event => { if (event.key === 'Escape') onCancel(); }}>
    <div role="dialog" aria-modal="true" aria-labelledby="retrain-title" className="w-full max-w-md rounded-2xl bg-white p-5 shadow-xl space-y-3">
      <h3 id="retrain-title" className="font-display text-xl font-light text-forest-800">Retrain the feed gate?</h3>
      <ul className="text-sm text-slate-700 space-y-1.5 list-disc pl-5">
        <li><b>Replaces</b> {current} with <span className="font-mono">{classifierName}</span> retrained on your labels. The previous model is not kept — no rollback.</li>
        <li><b>Takes</b> about 1–5 minutes of CPU, after first pulling your latest labels from Zotero (read-only). Keep this page open to follow progress.</li>
        <li>When the gate is enabled, stored feed items are rescored with the new model.</li>
      </ul>
      <div className="flex flex-wrap justify-end gap-2 pt-1">
        <Button autoFocus variant="secondary" onClick={onCancel}>Cancel</Button>
        <Button onClick={onConfirm}>Replace model and retrain</Button>
      </div>
    </div>
  </div>, document.body);
}
