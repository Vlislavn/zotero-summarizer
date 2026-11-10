import { resolveStage } from '../../utils/configForm.js';
import { CONNECTION_STAGES, connectionTaskLabel } from '../../utils/connectionTaskLabels.js';

export function ConnectionScope({ routing, targets, onChange, disabled }) {
  if (!routing) return <p className="text-sm">The first model becomes the default. Tasks without an override will use it.</p>;
  return <fieldset className="space-y-2 rounded-xl border border-slate-200 p-3" disabled={disabled}>
    <legend className="px-1 text-sm font-medium">Use this model for</legend>
    {[...CONNECTION_STAGES, 'default'].map(target => <label key={target} className="flex items-start gap-2 text-sm">
      <input type="checkbox" className="mt-1" checked={targets.includes(target)} onChange={event => onChange(event.target.checked
        ? [...targets, target] : targets.filter(value => value !== target))} />
      <span>{connectionTaskLabel(target)}<span className="block text-xs text-slate-600">Currently: {resolveStage(routing, target).model || 'not configured'}</span></span>
    </label>)}
    {!targets.length && <p role="status" className="text-sm text-slate-600">Choose at least one task or the default model.</p>}
    <p className="text-xs text-slate-600">Unselected tasks and Telegram stay unchanged. Long-paper reviews may also use the screening model.</p>
  </fieldset>;
}
