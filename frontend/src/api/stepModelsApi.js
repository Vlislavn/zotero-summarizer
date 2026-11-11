// Read-only facts for the models table: cheap provider check + run-log speed.
async function getJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} ${res.status}`);
  return res.json();
}

/** GET /api/admin/llm-reachability -> { stages: [{ stage, provider, reachable, base_url, detail }] } (no tokens). */
export const fetchProviderStatus = () => getJson('/api/admin/llm-reachability');

/** GET /api/llm/step-history -> { steps: { <step>: { runs, unit, median_seconds, last_success_at } } } from existing run logs. */
export const fetchStepHistory = () => getJson('/api/llm/step-history');
