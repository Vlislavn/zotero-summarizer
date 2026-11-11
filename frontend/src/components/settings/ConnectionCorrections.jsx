import { Field } from '../form/Fields.jsx';
import { connectionTaskLabel } from '../../utils/connectionTaskLabels.js';

export default function ConnectionCorrections({ stages = [], choices, onChange }) {
  if (!stages.length) return null;
  return <fieldset className="rounded-xl border border-slate-300 p-3 space-y-2 text-sm">
    <legend>Choose complete models for affected tasks</legend>
    <p>These saved exceptions would mix an old server or model with the new default. Choose a complete pair for each, or Cancel.</p>
    {stages.map(stage => <Field key={stage} kind="select" label={connectionTaskLabel(stage)} value={choices[stage] || ''}
      onChange={value => onChange(stage, value)} options={[
        { value: '', label: 'Choose a complete pair' },
        { value: 'previous', label: 'Keep previous server + model' },
        { value: 'default', label: 'Use new default pair' },
      ]} />)}
  </fieldset>;
}
