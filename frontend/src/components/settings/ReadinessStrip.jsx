// Readiness issues, one line each: a pill that opens the task that fixes it, then the reason.
// Metadata reachability is separate from model operation and the trained drop-classifier.

import { Link } from 'react-router-dom';
import { useSetupStatus } from '../../hooks/useSetupStatus.js';
import { tidyReason } from '../../utils/stepModelStatus.js';

// "dep:lightgbm" / "classifier_gate" → a short human label.
function subsystemLabel(name) {
  if (name.startsWith('dep:')) return name.slice(4);
  return name.replace(/_/g, ' ');
}

export default function ReadinessStrip() {
  const { status, pillars, subsystemIssues, isLoading, isError } = useSetupStatus();
  if (isLoading || isError) return null;
  const approvalNeeded = status?.llm?.reachability === 'needs_approval';
  // Success is silent (Tesler / Occam): only what needs fixing is listed, so a strip with
  // nothing to fix is not rendered at all.
  const issues = [
    !pillars.llm && !pillars.llmDisabled && (approvalNeeded
      ? { label: 'Provider approval', to: '/settings?task=connection', reason: 'The model-list permission is missing or expired. Approve the exact provider in AI models.' }
      : { label: 'AI provider', to: '/settings?task=connection', reason: tidyReason(status?.llm?.detail) || 'Not reachable or no key saved — check AI models.' }),
    !pillars.goals && { label: 'Goals', to: '/today?task=selection', reason: 'None set — article selection has nothing to match against.' },
    !pillars.model && { label: 'Classifier', to: '/ops?tab=system', reason: 'Not trained yet — retrain in Ops → System.' },
    // Runtime subsystems down right now (missing dep, gate failed to load…): ONE pill, one
    // destination (Hick's Law), with every cause on its line.
    subsystemIssues.length > 0 && { label: `${subsystemIssues.length} subsystem${subsystemIssues.length === 1 ? '' : 's'} down`, to: '/ops?tab=system&task=readiness',
      reason: subsystemIssues.map(s => `${subsystemLabel(s.name)}: ${s.detail || 'down'}`).join(' · ') },
  ].filter(Boolean);
  if (!issues.length) return null;

  return <section aria-labelledby="needs-attention" className="rounded-xl bg-amber-50/50 p-2.5 space-y-1.5">
    <h2 id="needs-attention" className="text-[11px] font-medium uppercase tracking-wider text-slate-600">Needs attention</h2>
    <ul className="space-y-1.5">{issues.map(issue => <li key={issue.label} className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
      <Link to={issue.to} className="relative inline-flex shrink-0 items-center gap-1.5 rounded-full border border-rose-200 bg-rose-50 px-2.5 py-1 text-xs font-medium text-rose-800 transition-colors hover:bg-rose-100 focus-visible:ring-2 focus-visible:ring-forest-800 before:absolute before:inset-x-0 before:-inset-y-2.5">
        <span aria-hidden>✗</span>{issue.label}</Link>
      <span className="min-w-0 flex-1 basis-40 text-xs text-slate-700 [overflow-wrap:anywhere]">{issue.reason}</span>
    </li>)}</ul>
  </section>;
}
