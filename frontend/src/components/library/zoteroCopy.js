// The words of the Zotero writes on the Library page — pure, so the confirmation sheets and the receipt say
// the same thing in one place. A sheet is {title, lines, confirm}; ConfirmSheet shows it.

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
const BACKUP = 'Backup first: a copy of the Zotero database is saved before anything is written. To undo, close Zotero and restore that copy.';
const CLOSED = 'Zotero must be closed. If it is open, you will be asked again.';

// `papers`: the whole-library count when the list is unscoped, else 0 (then the sheet says «all papers»).
export function sendSheet({ papers, scoresStale }) {
  return {
    title: papers ? `Send ${plural(papers, 'paper')} to Zotero?` : 'Send all papers to Zotero?',
    lines: [
      'Tags: sets zs:rel/<band> on every scored paper. Replaces only older zs:rel/* tags.',
      'Call Number: stamps a rank (zr0001…) on every paper. Your own Call Numbers stay.',
      ...(scoresStale ? ['The scores are older than the model, so they are recomputed first.'] : []),
      BACKUP, CLOSED,
    ],
    confirm: 'Send to Zotero',
  };
}

export function fetchSheet({ papers }) {
  return {
    title: 'Fetch missing PDFs?',
    lines: [
      `Looks for an open-access PDF for ${papers ? `the ${plural(papers, 'paper')}` : 'every paper'} in your library that has a source link but no PDF, and attaches it in Zotero. Papers that already have a PDF are skipped.`,
      'The files upload to zotero.org on the next sync.',
      BACKUP, CLOSED,
    ],
    confirm: 'Fetch PDFs',
  };
}

export const notInterestedSheet = (n) => ({
  title: `Mark ${plural(n, 'paper')} Not interested?`,
  lines: ['Each gets a ❌ tag queued for Zotero.', BACKUP],
  confirm: 'Mark Not interested',
});

export const addToCollectionSheet = (n, name) => ({
  title: `Add ${plural(n, 'paper')} to “${name}”?`,
  lines: ['Each paper is added to the collection in Zotero.', BACKUP, CLOSED],
  confirm: 'Add to collection',
});

export const markReadSheet = (n) => ({
  title: `Mark ${plural(n, 'paper')} read?`,
  lines: ['Each gets the 👀 (skimmed) tag in Zotero and leaves Read next.', BACKUP, CLOSED],
  confirm: 'Mark read',
});

// Zotero turned out to be running: the second sheet. `what` finishes «Zotero is running. … anyway?».
export const forceSheet = (what) => ({
  title: 'Zotero is running…',
  lines: [`Writing while Zotero is open can corrupt its database. Close Zotero and retry, or ${what} anyway.`, BACKUP],
  confirm: 'Write anyway',
});

// What «Send to Zotero» did, from the two API answers. `warning` when something failed: the text then says so and
// is not the plain «up to date» line.
export function sendReceipt(tags, ranks) {
  const tagged = tags?.tagged || 0;
  const ranked = ranks?.ranked || 0;
  const failed = (tags?.failed_count || 0) + (ranks?.failed_count || 0);
  const skipped = ranks?.skipped_user_callnumber || 0;
  const backups = [...new Set([tags?.backup_path, ranks?.backup_path].filter(Boolean))];
  const done = `${tagged} relevance tag(s) refreshed, ${ranked} papers ranked into Call Number`
    + (skipped ? ` (${plural(skipped, 'paper')} kept their own Call Number)` : '');
  const backup = backups.length ? ` Backup: ${backups.join(', ')}. To undo, close Zotero and restore that copy.` : '';
  if (failed) {
    return { warning: true, text: `Sent to Zotero, but ${plural(failed, 'change')} failed — ${done}. Send again to retry them.${backup}` };
  }
  return { warning: false, text: `Zotero is up to date — ${done}.${backup} In Zotero, sort the Call Number column ascending to read best-first.` };
}
