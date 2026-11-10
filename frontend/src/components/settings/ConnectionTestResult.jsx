import Button from '../ui/Button.jsx';
import { connectionTaskLabel } from '../../utils/connectionTaskLabels.js';
import { testRowFor, testVerdict } from '../../utils/stepModelStatus.js';

const TONE = { ok: 'text-emerald-800', fail: 'text-rose-700', warn: 'text-amber-800', neutral: 'text-slate-600' };

// Test result for the editor view (rows of the models table show their own result inline).
export default function ConnectionTestResult({ result, onRepair }) {
  if (!result) return null;
  if (result.current === false) return <section role="status" className="rounded-xl border border-slate-200 bg-linen p-3 text-sm"><strong>Test belongs to an older saved selection.</strong> Settings changed after this check; it does not describe the current selection.</section>;
  const rows = Array.isArray(result.targets)
    ? result.targets.map(stage => ({ key: stage, label: connectionTaskLabel(stage), identity: result.selections?.[stage], row: testRowFor(result, stage) }))
    : [{ key: 'default', label: connectionTaskLabel('default'), identity: result, row: result }];
  return <section role="status" className="rounded-xl border border-slate-200 bg-linen p-3 space-y-2 text-sm">
    <ul className="space-y-3">{rows.map(({ key, label, identity, row }) => {
      const verdict = testVerdict(row);
      return <li key={key} className="break-words">
        <p className="font-medium">{label} · {identity?.model || row?.model || 'unknown model'}</p>
        <p className="text-xs text-slate-600">{identity?.provider || row?.provider} · {identity?.base_url || 'Saved API'}</p>
        <p className={`font-medium ${TONE[verdict.tone]}`}>{verdict.headline}</p>
        {verdict.detail && <p>{verdict.detail}</p>}
        {row.reason_code && <details className="text-xs"><summary>Technical evidence</summary><p>Reason: {row.reason_code}</p></details>}
        {verdict.tone !== 'ok' && verdict.tone !== 'neutral' && <Button variant="secondary" onClick={() => onRepair(key === 'default' ? undefined : key)}>{key === 'default' ? 'Review provider and model' : `Change ${label}`}</Button>}
      </li>;
    })}</ul>
    {(result.ownership === 'unresolved' || result.hold || result.attempt?.hold) && <p>The request may still be running. Do not retry automatically; closing does not cancel it.</p>}
  </section>;
}
