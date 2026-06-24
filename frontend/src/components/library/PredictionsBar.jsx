// The "Review cool papers" bar (Phase 2): one action that deep-reviews EVERY
// undecided cool pick (high-relevance band) and proposes a verdict for each, so
// the human never clicks "Run deeper review" per paper. It loops the review fleet
// down the queue in the background — Confirm/Override cards + quality chips stream
// onto the rows as each paper settles — and a Stop ends the loop. Anchored at the
// top of the Review-queue region, directly above the rows it produces (cause next
// to effect). It always reports exactly one honest state (Doherty: never goes
// quiet; Tesler: names the cause and the next action), sharing ProposedVerdictCard's
// indigo left-rule vocabulary so the bar and the cards it spawns read as one system.

// Indigo to match ProposedVerdictCard; the state line colour shifts by severity.
// Branch order is load-bearing: a real terminal status (error / done_empty / ready)
// MUST beat the neutral idle prompt, else a 0-proposal run reads as "did nothing".
function stateLine(fleetStatus, proposedCount, coolCount, autoActive, stopping) {
  const {
    status, total = 0, completed = 0, proposed = 0,
    no_fetchable_source: noSource = 0, needs_library_login: needsLogin = 0,
    failed = 0, error, progress = {},
  } = fleetStatus || {};
  const skipped = noSource + needsLogin;

  if (stopping) {
    return { tone: 'text-amber-700', text: 'Stopping — finishing the reviews already in progress (they’re kept); no new papers will start.' };
  }
  if (autoActive || status === 'running') {
    // Clamp the index to the batch total: PASS-2 re-reviews inflate
    // deep_review.completed past the chunk size, which would read "paper 6 of 5".
    const ofNum = progress.total || total || 0;
    const rawIdx = progress.index || completed + 1;
    const idx = ofNum ? Math.min(rawIdx, ofNum) : rawIdx;
    const of = ofNum || '?';
    const cold = progress.deep_review?.status === 'running'
      ? ' — building its deep review first (a cold paper can take a few minutes)'
      : '';
    const left = coolCount > 0 ? ` · ${coolCount} cool paper${coolCount === 1 ? '' : 's'} still undecided` : '';
    return { tone: 'text-indigo-600', text: `Reviewing paper ${idx} of ${of}…${cold}${left}` };
  }
  if (status === 'error') {
    return { tone: 'text-rose-700', text: `Review failed: ${error || 'unknown error'}` };
  }
  if (status === 'done_empty') {
    const detail = failed > 0
      ? `${failed} couldn’t be reviewed — the deep-review step errored (check the server log).`
      : 'none yielded a fetchable digest (a web article, a paywall, or no open-access / arXiv source).';
    return {
      tone: 'text-amber-700',
      text: `Reviewed ${completed} paper${completed === 1 ? '' : 's'} but proposed no verdict — ${detail}`,
    };
  }
  if (status === 'ready') {
    const tail = skipped > 0 ? ` (${skipped} had no full text.)` : '';
    return { tone: 'text-slate-600', text: `Reviewed and proposed ${proposed} verdict${proposed === 1 ? '' : 's'} — Confirm or Override on the rows below.${tail}` };
  }
  // idle / never run — describe what the button will do (and note any proposals a
  // startup prewarm already left on the rows, so the cards aren't a surprise).
  if (coolCount === 0 && proposedCount > 0) {
    return { tone: 'text-slate-600', text: 'All cool papers reviewed — Confirm or Override on the rows below.' };
  }
  if (coolCount > 0) {
    const ready = proposedCount > 0 ? ` ${proposedCount} already ready below.` : '';
    return { tone: 'text-slate-500', text: `Deep-review the ${coolCount} undecided cool paper${coolCount === 1 ? '' : 's'} (must/should-read) and propose a verdict for each — from each paper’s full text.${ready}` };
  }
  return { tone: 'text-slate-500', text: 'No undecided cool papers — every high-relevance pick already has a deep review.' };
}

export default function PredictionsBar({ fleetStatus, onRun, onStop, autoActive = false, stopping = false, coolCount = 0, proposedCount = 0 }) {
  const line = stateLine(fleetStatus, proposedCount, coolCount, autoActive, stopping);
  // Gated picks (paywalled, session stale) surfaced as sign-in links: open, log in to
  // refresh the publisher session, then run again. Only items that carry a URL.
  const loginItems = (fleetStatus?.needs_login_items || []).filter((it) => it && it.url);
  return (
    <div className="rounded-lg border-l-[3px] border-indigo-300 bg-indigo-50/40 pl-3.5 pr-3 py-2.5">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <span className="text-[11px] uppercase tracking-[0.06em] font-semibold text-indigo-500">
          ✨ Review cool papers
        </span>
        {stopping ? (
          <button
            type="button"
            disabled
            className="px-3 py-1.5 rounded-lg bg-amber-500 text-white text-sm font-semibold opacity-70 cursor-default"
            title="Stopping — the reviews already dispatched are finishing in the background; no new papers will start."
          >
            Stopping…
          </button>
        ) : autoActive ? (
          <button
            type="button"
            onClick={onStop}
            className="px-3 py-1.5 rounded-lg bg-rose-600 text-white text-sm font-semibold hover:bg-rose-700"
            title="Stop after the reviews already in progress finish (those are kept). No new papers will be started — there is no mid-review cancel."
          >
            Stop
          </button>
        ) : (
          <button
            type="button"
            onClick={onRun}
            disabled={coolCount === 0}
            className="px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
            title="Deep-review every undecided high-relevance paper and propose a verdict for each. Suggestions only — nothing is written until you Confirm. Results stream in; click Stop to end early."
          >
            {coolCount > 0 ? `Review cool papers (${coolCount})` : 'All reviewed'}
          </button>
        )}
      </div>
      {/* Live region (matches the app's StatusBanner pattern): the auto-review
          streams progress for minutes — announce each state change to screen
          readers, who otherwise get no feedback during the long-running op. */}
      <p role="status" aria-live="polite" className={`mt-1.5 text-[11px] ${line.tone}`}>{line.text}</p>
      {loginItems.length > 0 && (
        <div className="mt-1.5 text-[11px] text-slate-600">
          <span>🔒 Sign in to fetch these — open the link, log in, then review again:</span>
          <ul className="mt-1 space-y-0.5 list-disc pl-4">
            {loginItems.map((it) => (
              <li key={it.item_key}>
                <a href={it.url} target="_blank" rel="noopener noreferrer" className="text-indigo-600 hover:underline">
                  {it.title || it.url}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
