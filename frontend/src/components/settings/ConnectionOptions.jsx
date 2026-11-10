import { Field } from '../form/Fields.jsx';
import { connectionLabel } from '../../utils/connectionTaskLabels.js';

export default function ConnectionOptions({ draft, providers, onChange }) {
  return <details className="text-sm space-y-2">
    <summary className="cursor-pointer">Server address, API type and access</summary>
    {draft.source && <Field label="API address" value={draft.base_url || ''} onChange={base_url => onChange({ base_url })} />}
    <Field kind="select" label="API protocol" value={draft.type}
      options={[{ value: 'openai', label: 'OpenAI-compatible' }, { value: 'anthropic', label: 'Native Anthropic' }]}
      onChange={type => onChange({ type })} />
    <Field kind="select" label="Authorization" value={draft.auth === 'none' ? 'none' : draft.auth === 'new' ? 'new' : draft.authorization || ''}
      options={[{ value: 'none', label: 'No key required' }, { value: 'new', label: 'Add an API key' }, ...providers.filter(p => p.auth_mode !== 'none').map(p => ({ value: p.name, label: `Saved access: ${connectionLabel(p)}` }))]}
      onChange={value => onChange(['none', 'new'].includes(value) ? { auth: value, authorization: null } : { auth: 'existing', authorization: value })} />
    <p className="text-xs text-slate-600">Custom API paths stay exactly as entered. Only native Anthropic permits a blank address (https://api.anthropic.com).</p>
  </details>;
}
