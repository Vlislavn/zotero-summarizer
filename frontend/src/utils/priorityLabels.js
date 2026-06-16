// One human vocabulary for the reading-priority enum (Mental Model / Jakob's
// Law). The raw wire values (`must_read` | `should_read` | `could_read` |
// `dont_read`) are an implementation detail — they must NEVER reach the user.
// Every surface that shows a priority to a human renders `pretty(key)` instead,
// so "Must read … Remove ❌" reads the same everywhere and the keys stay free to
// change without touching copy.
//
// `dont_read` says "Remove ❌" because picking it IS the reject path (it queues
// the ❌ Zotero tag and drops the paper from the queue), matching the verdict
// button copy in VerdictPanel.
//
// This is display-only. Wire values, filter values, and map keys are unchanged.
export const PRIORITY_LABELS = {
  must_read: 'Must read',
  should_read: 'Should read',
  could_read: 'Could read',
  dont_read: 'Remove ❌',
};

// key -> human label; unknown keys fall through to the raw key so a new enum
// value is visible (and obviously un-mapped) rather than silently blank.
export const pretty = (key) => PRIORITY_LABELS[key] || key;
