import { connectionTaskLabel } from '../../utils/connectionTaskLabels.js';

const server = pair => pair.base_url || (pair.type === 'anthropic' ? 'Native Anthropic' : pair.provider || 'Saved server');
export default function ConnectionImpact({ impact }) {
  if (!impact) return null;
  const shared = impact.shared_authorization;
  const telegram = impact.telegram?.selection;
  const changes = impact.stages.filter(row => row.before.provider !== row.after.provider || row.before.model !== row.after.model);
  const defaultChanged = impact.default && ['provider', 'model'].some(key => impact.default.before?.[key] !== impact.default.after?.[key]);
  return <section className="rounded-xl border border-slate-200 bg-linen p-3 space-y-2 text-sm" aria-label="Connection impact">
    <h4 className="font-medium">Proposed model assignments</h4>
    <p>{changes.length ? `${changes.length} task assignment${changes.length === 1 ? '' : 's'} will change.` : 'Existing task models stay unchanged.'} Nothing is applied until you confirm.</p>
    {impact.default && <p>Default / fallback: {defaultChanged
      ? `${impact.default.before?.model || 'not configured'} → ${impact.default.after.model}`
      : `keeps ${impact.default.after.model}`}.</p>}
    {impact.stages.map(row => {
      const changed = row.before.provider !== row.after.provider || row.before.model !== row.after.model;
      return <div key={row.stage} className="break-words">
        <span className="font-medium">{connectionTaskLabel(row.stage)}</span>: {changed
          ? `${row.before.model || 'not configured'} → ${row.after.model}` : `keeps ${row.after.model}`}
        {row.before.provider !== row.after.provider && ' (server changes)'}
        <span className="block text-xs text-slate-600">{server(row.after)}</span>
      </div>;
    })}
    {telegram && <p>Telegram reviews keep {telegram.model} on {impact.telegram.server || 'their saved server'}.
      {!impact.telegram.admitted && ` Needs attention: ${impact.telegram.detail}`}</p>}
    {!telegram && <p>Telegram is unchanged. {impact.telegram?.detail || 'No separate model is selected.'}</p>}
    {Boolean(shared?.consumers?.length) && <p>Shared API access also serves: {shared.consumers.map(connectionTaskLabel).join(', ')}.</p>}
    <details className="text-xs"><summary>Exact servers and retained connections</summary>
      {impact.stages.map(row => <p key={row.stage} className="break-words">{connectionTaskLabel(row.stage)}: {server(row.before)} / {row.before.model || 'no model'} → {server(row.after)} / {row.after.model}</p>)}
      {impact.existing_server?.provider && <p>The original connection record is retained unchanged. Remaining tasks: {impact.existing_server.consumers.map(connectionTaskLabel).join(', ') || 'none'}.</p>}
      <p>Already-started work keeps its snapshot; changing selection does not cancel it.</p>
    </details>
  </section>;
}
