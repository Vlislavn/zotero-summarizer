// Plain-language facts for the "which model does each step" table. Pure display helpers.

// Provider-check detail without wrapper noise: "APIError: could not list models from URL: [Errno 61] Connection refused" -> "Connection refused".
export const tidyReason = detail => String(detail || '').replace(/^\w+: could not list models from \S+: /, '').replace(/\[Errno \d+\] /, '');

export function formatDuration(seconds) {
  if (seconds == null) return null;
  if (seconds < 90) return `${Math.round(seconds)} s`;
  return `${Math.round(seconds / 60)} min`;
}

export function formatWhen(iso) {
  const date = iso ? new Date(iso) : null;
  if (!date || Number.isNaN(date.getTime())) return null;
  return date.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

const CONNECTION_FAILURES = new Set(['endpoint_unreachable', 'inference_transport_error', 'inference_timeout', 'waiter_timeout']);

// One verdict for one Test result row: { tone: ok|fail|warn|neutral, headline, detail }.
// A dead endpoint is always a connection failure; "not sent" is never mixed with it.
export function testVerdict(row) {
  if (!row) return null;
  if (row.status === 'disabled') return { tone: 'neutral', headline: 'AI is off — not tested' };
  if (row.status === 'needs_approval') return { tone: 'warn', headline: 'Approve this provider before testing', detail: row.detail };
  if (row.status === 'operational') {
    const took = row.elapsed_ms == null ? '' : ` in ${(row.elapsed_ms / 1000).toFixed(1)} s`;
    return { tone: 'ok', headline: `Works — answered${took}`, detail: row.response };
  }
  if (CONNECTION_FAILURES.has(row.reason_code)) return { tone: 'fail', headline: 'Connection failed', detail: row.detail };
  if (row.reachable === true && !row.attempted) {
    return { tone: 'warn', headline: row.model_listed === false ? 'Provider reachable, model not listed' : 'Provider reachable — no test prompt sent', detail: row.detail };
  }
  if (!row.attempted) return { tone: 'warn', headline: 'Test not sent', detail: row.detail };
  return { tone: 'fail', headline: 'Test failed', detail: row.detail };
}

// The Test result that belongs to `target` (a step key, or 'default'), or null.
export function testRowFor(test, target) {
  if (!test) return null;
  if (!Array.isArray(test.targets)) return target === 'default' ? test : null;
  if (!test.targets.includes(target)) return null;
  const stage = test.stages?.find(row => row.stage === target);
  return stage ? { ...stage, base_url: stage.base_url || test.selections?.[target]?.base_url } : { status: 'fail', detail: test.detail || 'No result was established for this step.' };
}
