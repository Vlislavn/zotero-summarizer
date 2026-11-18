// One human vocabulary for the reading-priority enum (Mental Model / Jakob's
// Law). The raw wire values (`must_read` | `should_read` | `could_read` |
// `dont_read`) are an implementation detail — they must NEVER reach the user.
// Every surface that shows a priority to a human renders `pretty(key)` instead,
// so "Must read … Not interested" reads the same everywhere and the keys stay free to
// change without touching copy.
//
// `dont_read` says "Not interested" everywhere it is a label (Today card, paper-page
// verdict panel and bar, bulk bar); picking it is still the reject path (it queues
// the ❌ Zotero tag and drops the paper from the queue).
//
// This is display-only. Wire values, filter values, and map keys are unchanged.
export const PRIORITY_LABELS = {
  must_read: 'Must read',
  should_read: 'Should read',
  could_read: 'Could read',
  dont_read: 'Not interested',
};

// key -> human label; unknown keys fall through to the raw key so a new enum
// value is visible (and obviously un-mapped) rather than silently blank.
export const pretty = (key) => PRIORITY_LABELS[key] || key;

// A verdict the owner did NOT cast themselves (the UI labels it "Auto:" not
// "Yours:"): the stored `source` is anything but 'user' (`machine_add`,
// `auto_quality`). A user relabel flips `source` to 'user' and clears the
// gate's comment, so the comment is never a signal.
export const isAutoVerdict = (verdict) => Boolean(verdict?.source && verdict.source !== 'user');
