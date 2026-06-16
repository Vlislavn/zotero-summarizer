// Pure presentation helpers for the Triage Monitor page, split out so they can
// be unit-tested in isolation (matching the pendingHelpers / reviewHelpers
// pattern) and to keep Triage.jsx lean under the ≤500-LOC budget.

// Progress of a job as an integer 0..100. A missing/zero total reads as 0% so
// the bar never divides by zero or overflows past 100.
export function progressPercent(job) {
  if (!job) return 0;
  const total = Number(job.total || 0);
  if (total <= 0) return 0;
  const completed = Number(job.completed || 0);
  return Math.min(100, Math.max(0, Math.round((completed / total) * 100)));
}

// Format a 0..1 ratio as a whole-percent string; non-finite input reads "n/a"
// so an absent calibration field never renders as "NaN%".
export function formatPercent(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 'n/a';
  return `${Math.round(n * 100)}%`;
}

// Map a job status onto one of the shared Badge tone tokens so the status pill
// speaks the same colour vocabulary as the rest of the app
// (emerald=done, teal=running, rose=failed, slate=queued/cancelled/unknown).
const STATUS_TONE = {
  running: 'teal',
  completed: 'emerald',
  done: 'emerald',
  failed: 'rose',
  error: 'rose',
  cancelled: 'slate',
  canceled: 'slate',
  queued: 'slate',
  pending: 'slate',
};

export function statusTone(status) {
  return STATUS_TONE[String(status || '').toLowerCase()] || 'slate';
}

// A job is "finished" (no longer worth polling) once it leaves the running
// state. Used to decide when to auto-refresh calibration on completion.
const TERMINAL_STATUSES = new Set([
  'completed',
  'done',
  'failed',
  'error',
  'cancelled',
  'canceled',
]);

export function isTerminalStatus(status) {
  return TERMINAL_STATUSES.has(String(status || '').toLowerCase());
}
