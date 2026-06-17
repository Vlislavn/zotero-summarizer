// The "Suggested verdicts" bar (Phase 2): the home of "Predict next 5" and its
// OWN feedback, anchored at the top of the Review-queue region — directly above
// the rows whose Confirm/Override cards it produces (cause sits next to effect).
//
// It exists because the old button lived in the search row and went silent on
// finish, so a run that proposed nothing (picks with no full-text PDF) was
// indistinguishable from success — "seems like it does nothing." This bar always
// reports exactly one honest state (Doherty: never goes quiet; Tesler: it names
// the cause and the next action). It shares ProposedVerdictCard's indigo left-rule
// vocabulary so the bar and the cards it spawns read as one system.

// Indigo to match ProposedVerdictCard; the state line colour shifts by severity.
function stateLine(fleetStatus, proposedCount) {
  const {
    status, total = 0, completed = 0, proposed = 0,
    skipped_no_fulltext: skipped = 0, error, progress = {},
  } = fleetStatus || {};

  if (status === 'running') {
    const idx = progress.index || completed + 1;
    const of = progress.total || total || '?';
    const cold = progress.deep_review?.status === 'running'
      ? ' — building its deep review first (a cold paper can take a few minutes)'
      : '';
    return { tone: 'text-indigo-600', text: `Reviewing paper ${idx} of ${of}… ${proposed} predicted so far${cold}.` };
  }
  if (status === 'error') {
    return { tone: 'text-rose-700', text: `Pre-decide failed: ${error || 'unknown error'}` };
  }
  if (status === 'ready') {
    const tail = skipped > 0 ? ` (${skipped} had no full text.)` : '';
    return { tone: 'text-slate-600', text: `Predicted ${proposed} of ${total} — Confirm or Override on the rows below.${tail}` };
  }
  if (status === 'done_empty') {
    return {
      tone: 'text-amber-700',
      text: `Reviewed ${completed} paper${completed === 1 ? '' : 's'} but couldn’t suggest a verdict — they have no full-text PDF. `
        + 'Use “Fetch full text” in Export to Zotero below, then Predict again.',
    };
  }
  // idle / never run — if a startup prewarm already left proposals on the rows, say
  // so (the cards ARE there) instead of implying nothing has happened yet.
  if (proposedCount > 0) {
    return { tone: 'text-slate-500', text: `${proposedCount} suggestion${proposedCount === 1 ? '' : 's'} ready on the rows below — Predict next 5 to add more.` };
  }
  return { tone: 'text-slate-500', text: 'Pre-decide a reading verdict for the next 5 undecided picks — from each paper’s cached deep review.' };
}

export default function PredictionsBar({ fleetStatus, onRun, proposedCount = 0 }) {
  const running = fleetStatus?.status === 'running';
  const line = stateLine(fleetStatus, proposedCount);
  return (
    <div className="rounded-lg border-l-[3px] border-indigo-300 bg-indigo-50/40 pl-3.5 pr-3 py-2.5">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <span className="text-[11px] uppercase tracking-[0.06em] font-semibold text-indigo-500">
          ✨ Suggested verdicts
        </span>
        <button
          type="button"
          onClick={onRun}
          disabled={running}
          className="px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
          title="Pre-decide a reading verdict for the next 5 UNDECIDED picks (from each paper's deep review), so each shows a one-tap Confirm / Override card. Re-run to advance to the next 5. Suggestions only; nothing is written until you Confirm."
        >
          {running ? 'Predicting…' : 'Predict next 5'}
        </button>
      </div>
      <p className={`mt-1.5 text-[11px] ${line.tone}`}>{line.text}</p>
    </div>
  );
}
