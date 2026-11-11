import { useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchLlmReachability } from '../../api/settingsApi.js';
import { connectionLabel } from '../../utils/connectionTaskLabels.js';
import Button from '../ui/Button.jsx';

// One-click consent for the saved connection. Runs the SAME register flow as
// Change → Load models (reuse + approve; no key typed, saved or shown). The
// paper page's "Approve saved connection" link lands here.
export default function ConnectionApproval({ providers, busy, onApprove, disabled }) {
  const client = useQueryClient();
  const reachability = useQuery({ queryKey: ['deep-review-reachability'], queryFn: fetchLlmReachability,
    retry: false, staleTime: 30_000 });
  const names = new Set((reachability.data?.stages || [])
    .filter(stage => stage.reason_code === 'provider_approval_required').map(stage => stage.provider));
  const waiting = providers.filter(p => names.has(p.name));
  if (!waiting.length) return null;
  async function approve() {
    await onApprove(waiting[0]);
    await Promise.all([['deep-review-reachability'], ['llm-reachability']].map(queryKey => client.invalidateQueries({ queryKey })));
  }
  return <div id="approve-connection" className="space-y-2 rounded-xl border border-amber-200 bg-amber-50 p-3">
    <p className="text-sm font-semibold text-amber-900">This connection needs your approval</p>
    <p className="text-xs text-amber-900">{connectionLabel(waiting[0])}. Approves this server and this key for reviews. The key is not shown.</p>
    <Button type="button" disabled={disabled || busy} onClick={approve}>
      {busy ? 'Approving…' : 'Approve this connection'}
    </Button>
  </div>;
}
