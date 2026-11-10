import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useBeforeUnload, useBlocker, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchCollections, fetchPaperRender, fetchSectionSummaries, attachFiguresToZotero } from '../api/libraryApi.js';
import { flattenCollections } from './pendingHelpers.js';
import usePaperReview from '../hooks/usePaperReview.js';
import useDeepReviewRunner from '../hooks/useDeepReviewRunner.js';
import useKeyboardNav from '../hooks/useKeyboardNav.js';
import AuthorByline from '../components/AuthorByline.jsx';
import LinksRow from '../components/paper/LinksRow.jsx';
import AbstractBlock from '../components/paper/PaperDetailView/AbstractBlock.jsx';
import PaperReview from '../components/paper/review/PaperReview.jsx';
import EvidenceFold from '../components/paper/review/EvidenceFold.jsx';
import ReviewFailure from '../components/paper/ReviewFailure.jsx';
import ReviewModelStatus from '../components/paper/ReviewModelStatus.jsx';
import RelatedPapers from '../components/paper/RelatedPapers.jsx';
import PaperFigures from '../components/library/PaperFigures.jsx';
import PaperFactsCard from '../components/card/PaperFactsCard.jsx';
import SectionMap from '../components/paper/review/SectionMap.jsx';
import StoryToc from '../components/paper/review/StoryToc.jsx';
import ActionRail from '../components/paper/review/ActionRail.jsx';
import MobileVerdictBar from '../components/paper/review/MobileVerdictBar.jsx';
import { cleanAuthors, cleanVenue, cleanAbstract } from '../utils/feedText.js';
import { Fold } from '../components/paper/review/primitives.jsx';
import { FullTextAccessNotice, StatusBanner } from '../components/library/shared.jsx';
import Spinner from '../components/ui/Spinner.jsx';
import Button from '../components/ui/Button.jsx';
import { readStoredJson } from '../utils/safeStorage.js';
import { setTodayReviewContext } from '../utils/todayEvents.js';
import { TODAY_MEMBERS_QUERY, TODAY_VISIT_QUERY, finishTodayReviewAction, readTodayVisit, updateTodayVisit,
  startTodayReviewAction, todayPaperUrl } from '../utils/todayVisit.js';

// "92" -> "1m 32s". Live elapsed/ETA for the auto-generating review.
function fmt(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m}m ${r}s` : `${m}m`;
}

// The review zone: the cached review (flat + located findings) when present; else
// the access banners + a prominent Generate button + a live skeleton while it
// runs. The runner auto-fires on open for a paper with a PDF (the user opted into
// auto-generate-on-open), so the common case renders instantly.
function ReviewZone({ deep, runner, sectionOverlay, itemKey }) {
  const { status, error, llmAvailable, online, running } = runner;
  const reviewed = deep && !deep.needs_pdf && (deep.digest || deep.quality || (deep.goal_summaries || []).length);
  const digest = deep?.digest;
  const usable = !deep?.needs_pdf && !deep?.error && [
    digest?.tldr, digest?.executive_summary,
    ...(Array.isArray(digest?.key_findings) ? digest.key_findings : []),
  ].some((value) => String(value || '').trim());
  // Card facts (#98) sit right under the verdict card; with no review yet they lead the zone.
  const showReview = reviewed && !running;
  const facts = <PaperFactsCard cardKey={itemKey} variant="paper" />;
  return (
    <div className="space-y-3">
      {!showReview && facts}
      {!running && !usable && <ReviewModelStatus runner={runner} />}
      <FullTextAccessNotice deep={deep} />

      {showReview && <PaperReview deep={deep} flat sectionOverlay={sectionOverlay} facts={facts}
        evidence={<EvidenceFold itemKey={itemKey} lost={deep.not_established_fields || []} />}
        onRegenerate={runner.checkingStatus ? null : () => runner.run()} />}
      {running ? (
        <div className="rounded-xl border border-slate-200 bg-white px-4 py-5 text-[13px] text-slate-600" role="status" aria-live="polite">
          <div className="flex items-center gap-2 font-medium text-slate-700">
            <Spinner size="sm" color="slate" /> Generating the review…
          </div>
          {status.progress?.phase_label && (
            <div className="mt-1 text-slate-500">
              {status.progress.phase_label}
              {status.progress.sub?.total > 0 && ` ${status.progress.sub.done}/${status.progress.sub.total}`}
            </div>
          )}
          {status.progress?.total_elapsed_seconds != null && (
            <div className="mt-0.5 text-slate-400">
              {fmt(status.progress.total_elapsed_seconds)} elapsed
              {status.progress.eta_seconds != null && ` · ~${fmt(status.progress.eta_seconds)} remaining`}
            </div>
          )}
        </div>
      ) : !usable && (
        <Button
          onClick={() => runner.run()}
          disabled={!llmAvailable || status.status === 'unavailable' || runner.checkingStatus}
          size="sm"
        >
          Generate review
        </Button>
      )}
      <ReviewFailure status={status} />
      {online && status.status === 'unavailable' && status.error && (
        <div className="space-y-1 text-[12px] text-amber-800" role="status">
          <div>Review status unavailable: {status.error}. The server job may still be running.</div>
          <button type="button" onClick={runner.retryStatus} disabled={runner.checkingStatus}
            className="font-medium underline disabled:opacity-50">
            Retry status
          </button>
        </div>
      )}
      {error && <div className="text-[12px] text-rose-700">{error}</div>}
    </div>
  );
}

function useUnsavedNotesGuard(dirty) {
  const blocker = useBlocker(useCallback(({ currentLocation, nextLocation }) =>
    dirty && currentLocation.pathname !== nextLocation.pathname, [dirty]));
  useEffect(() => {
    if (blocker.state !== 'blocked') return;
    if (window.confirm('You have unsaved notes on this paper. Leave without saving them?')) blocker.proceed();
    else blocker.reset();
  }, [blocker]);
  useBeforeUnload(useCallback(event => {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = '';
  }, [dirty]));
}

// The closed Figures row: what the build produced, in one line.
function figuresSummary(render, completed) {
  if (render?.stale) return 'Outdated — open to rebuild';
  if (render?.status === 'running') return 'Building from the PDF…';
  const n = (render?.figures || []).filter((f) => f && f.name).length;
  return completed && n ? `${n} figures and tables from the PDF` : 'Extracted from the PDF';
}

// Full-page, single-scroll deep review for one paper (opened from a Read-next row).
// Everything renders up front — the located review, figures, the paper map, the
// abstract — with a sticky TOC + action/chat rail. The decision (verdict, file,
// tags, ask) lives in the rail and a mobile bottom bar so it's always reachable.
export default function PaperReviewPage() {
  const { itemKey } = useParams();
  const [params] = useSearchParams();
  const fromToday = params.get('from') === 'today';
  const visitId = params.get('visit');
  const autoRun = params.get('autoRun') !== 'false';
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  useEffect(() => { // link opens on this page belong to the Today slate it was opened from
    setTodayReviewContext(fromToday && visitId ? { slateId: visitId, itemKey } : null);
    return () => setTodayReviewContext(null);
  }, [fromToday, visitId, itemKey]);
  // Prev/Next through the ranked list the originating page (Read next) stored in
  // localStorage — same tab (useParams re-reads the key, usePaperReview refetches,
  // auto-run fires). Re-read per paper so a list refreshed in the other tab is
  // picked up. Key not in the order (opened from elsewhere) → idx -1, buttons hide.
  const reviewOrder = useMemo(() => {
    if (fromToday) {
      const visit = queryClient.getQueryData(TODAY_VISIT_QUERY) || readTodayVisit();
      return visit?.id === visitId && !visit.invalid
        ? visit.members.map(member => member.paper.stable_feed_key).filter(Boolean) : [];
    }
    return readStoredJson(
      'zs.reviewOrder',
      [],
      (value) => Array.isArray(value) && value.every((key) => typeof key === 'string'),
    );
    // itemKey dep is deliberate: re-read the stored order on each paper so a list
    // the Read-next tab refreshed mid-session is picked up.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemKey, fromToday, visitId, queryClient]);
  const navIdx = reviewOrder.indexOf(itemKey);
  const prevKey = navIdx > 0 ? reviewOrder[navIdx - 1] : null;
  const nextKey = navIdx >= 0 && navIdx < reviewOrder.length - 1 ? reviewOrder[navIdx + 1] : null;
  const [notesDirty, setNotesDirty] = useState(false);
  useUnsavedNotesGuard(notesDirty);
  const [relatedRevision, setRelatedRevision] = useState(0);
  const fenceRelatedContext = useCallback(() => {
    setRelatedRevision(value => value + 1);
    queryClient.invalidateQueries({ queryKey: TODAY_MEMBERS_QUERY });
  }, [queryClient]);
  const goToKey = useCallback(
    (key) => {
      if (!key) return;
      navigate(fromToday ? todayPaperUrl(key, visitId) : `/paper/${encodeURIComponent(key)}${autoRun ? '' : '?autoRun=false'}`);
    },
    [navigate, autoRun, fromToday, visitId],
  );
  // j = next, k = prev (auto-disabled while typing in the notes / Ask fields).
  useKeyboardNav({
    onPrev: () => goToKey(prevKey),
    onNext: () => goToKey(nextKey),
    deps: [prevKey, nextKey],
  });
  // Attach the extracted figures to the Zotero item (Zotero renders images in
  // notes' sibling attachments). One retry through the connector force-gate, like
  // the other writes; a forced write only appears after a Zotero restart.
  const [attachMsg, setAttachMsg] = useState('');
  const [attaching, setAttaching] = useState(false);
  const onAttachFigures = useCallback(async () => {
    setAttaching(true); setAttachMsg('');
    try {
      let data = await attachFiguresToZotero(itemKey, { force: false });
      if (data?.requires_force) {
        if (!window.confirm("Zotero is open — attach anyway? The figures won't appear until you restart Zotero.")) {
          setAttaching(false); return;
        }
        data = await attachFiguresToZotero(itemKey, { force: true });
      }
      setAttachMsg(data?.updated
        ? `Attached ${data.updated} figure${data.updated > 1 ? 's' : ''} to Zotero ✓`
        : (data?.message || 'Nothing to attach.'));
    } catch (e) {
      setAttachMsg(`Failed: ${e?.message || e}`);
    } finally {
      setAttaching(false);
    }
  }, [itemKey]);
  const collectionsQuery = useQuery({
    queryKey: ['zotero-collections'], queryFn: fetchCollections, staleTime: 5 * 60_000,
  });
  const renderQuery = useQuery({
    queryKey: ['paper-render', itemKey],
    queryFn: () => fetchPaperRender(itemKey),
    enabled: Boolean(itemKey),
    staleTime: 15_000,
  });
  const flatCollections = flattenCollections(collectionsQuery.data?.items || []);
  const onVerdictStarted = useCallback((key, priority) => fromToday
    ? startTodayReviewAction(queryClient, visitId, key, priority) : undefined, [fromToday, queryClient, visitId]);
  const onVerdictSettled = useCallback((actionId, result, error) => {
    finishTodayReviewAction(queryClient, actionId, result, error);
  }, [queryClient]);
  const { detail, isLoading, error, refreshDetail, onTagsChanged, onCollectionsChanged, verdict } =
    usePaperReview(itemKey, { onVerdictAcknowledged: fenceRelatedContext,
      onVerdictStarted: fromToday ? onVerdictStarted : undefined, onVerdictSettled });
  const hasPdf = Boolean(detail?.has_pdf);
  // Paper-map one-liners are built on open, not at review time; poll only while the job runs
  // (the interval stops on unmount, on ready/unavailable and on a failed poll).
  const mapOverlay = detail?.deep_review?.section_overlay;
  const summariesQuery = useQuery({
    queryKey: ['section-summaries', itemKey],
    queryFn: () => fetchSectionSummaries(itemKey),
    enabled: Boolean(itemKey && mapOverlay && !mapOverlay.degraded && (mapOverlay.sections || []).length),
    staleTime: 60_000, retry: false, refetchOnWindowFocus: false,
    refetchInterval: (query) => !query.state.error && query.state.data?.status === 'running' ? 3000 : false,
  });
  const render = renderQuery.data || null;
  const renderCompleted = render?.status === 'completed';
  // Auto-generate on open only for papers with a local Zotero PDF (no silent PDF
  // fetch); a no-PDF paper shows the manual Generate path instead, even if an
  // acquired render artifact already exists.
  const onReviewDone = useCallback(() => {
    refreshDetail();
    queryClient.invalidateQueries({ queryKey: TODAY_MEMBERS_QUERY });
    // A paper that now has a review leaves the "Not reviewed yet" shelf of the saved visit.
    updateTodayVisit(queryClient, visit => visit?.unreviewed?.some(row => row.stable_feed_key === itemKey)
      ? { ...visit, unreviewed: visit.unreviewed.filter(row => row.stable_feed_key !== itemKey) } : null);
  }, [refreshDetail, queryClient, itemKey]);
  const runner = useDeepReviewRunner(itemKey, { deep: detail?.deep_review, onDone: onReviewDone, autoRun: hasPdf && autoRun });
  const showFigures = runner.online && runner.serverAvailable && (hasPdf || renderCompleted || render?.status === 'running');

  if (isLoading) return <div className="py-4 text-sm text-slate-500">Loading review…</div>;
  if (error) {
    return <div className="py-4"><StatusBanner message={`Could not load this paper: ${error.message || error}`} isError />
      {fromToday && <Link to="/today" className="text-teal-700 underline">← Back to Today</Link>}
    </div>;
  }
  if (!detail) return null;

  const deep = detail.deep_review || null;
  const ov = deep?.section_overlay || null;
  const hasMap = Boolean(ov && !ov.degraded && (ov.sections || []).length);
  const venueText = cleanVenue(detail.venue);
  const abstractText = cleanAbstract(detail.abstract);

  const toc = [
    { id: 'review', label: 'Review' },
    { id: 'similar', label: 'Similar papers' },
    showFigures && { id: 'figures', label: 'Figures' },
    hasMap && { id: 'paper-map', label: 'Paper map' },
    abstractText && { id: 'abstract', label: 'Abstract' },
  ].filter(Boolean);

  return (
    <div className="paper-reading-page mx-auto w-full min-w-0 max-w-[76rem] pb-44 lg:pb-6">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-0 text-[13px] text-slate-500">
        <Link to={fromToday ? '/today' : '/library'} className="inline-flex min-h-11 items-center hover:text-forest-800">{fromToday ? '← Back to Today' : '← Back'}</Link>
        {navIdx >= 0 && (
          <>
            <span className="text-slate-300" aria-hidden="true">·</span>
            <button
              type="button"
              onClick={() => goToKey(prevKey)}
              disabled={!prevKey}
              className="inline-flex min-h-11 items-center px-1 hover:text-forest-800 disabled:opacity-40 sm:min-h-0"
            >
              ‹ Prev
            </button>
            <span className="tabular-nums text-slate-400">{navIdx + 1} / {reviewOrder.length}</span>
            <button
              type="button"
              onClick={() => goToKey(nextKey)}
              disabled={!nextKey}
              className="inline-flex min-h-11 items-center px-1 hover:text-forest-800 disabled:opacity-40 sm:min-h-0"
            >
              Next ›
            </button>
            <span className="hidden text-slate-400 sm:inline" title="Keyboard: j = next, k = previous">j / k</span>
          </>
        )}
      </div>

      <header className="mb-5 mt-2">
        <h1 className="max-w-[30ch] font-display text-[28px] font-light leading-[1.12] tracking-tight text-slate-900 sm:text-[32px]">
          {detail.title}
        </h1>
        <div className="mt-1.5"><AuthorByline authors={cleanAuthors(detail.authors)} quiet limit={3} /></div>
        {(venueText || detail.year) && (
          <div className="text-[12px] text-slate-500">
            <span className="text-slate-600">{venueText}</span>{venueText && detail.year ? ' · ' : ''}{detail.year}
          </div>
        )}
        <div className="mt-3"><LinksRow detail={detail} itemKey={itemKey} /></div>
      </header>

      <div className={`xl:grid xl:gap-8 ${toc.length > 1
        ? 'xl:grid-cols-[11rem_minmax(0,1fr)_20rem]'
        : 'xl:grid-cols-[minmax(0,1fr)_20rem]'}`}>
        <StoryToc items={toc} />

        <main className="min-w-0 max-w-[68ch]">
          <section id="review" className="scroll-mt-20 pb-2">
            <ReviewZone deep={deep} runner={runner} sectionOverlay={ov} itemKey={itemKey} />
          </section>

          {/* Next step after reading the review, so it sits right under it (not the last, forgotten fold). */}
          <Fold id="similar" label="Find similar papers" summary="Open sources, or papers you already saved">
            <RelatedPapers key={JSON.stringify([itemKey, relatedRevision, detail.user_note, detail.verdict])} itemKey={itemKey} heading={false} />
          </Fold>

          {showFigures && (
            <Fold id="figures" label="Figures" summary={figuresSummary(render, renderCompleted)}>
              <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1">
                <button
                  type="button"
                  onClick={onAttachFigures}
                  hidden={!renderCompleted || render.stale}
                  disabled={attaching}
                  className="text-[13px] font-medium text-forest-800 hover:text-forest-700 disabled:opacity-50"
                  title="Attach these figures to the paper's Zotero item so they show in Zotero. Skips any already attached; needs Zotero closed (or confirm force)."
                >
                  {attaching ? 'Saving…' : '+ Save figures to this paper in Zotero'}
                </button>
                {attachMsg && <span className="text-[13px] text-slate-500">{attachMsg}</span>}
              </div>
              <PaperFigures itemKey={itemKey} hasPdf={hasPdf} canBuild={hasPdf} />
            </Fold>
          )}

          {hasMap && <SectionMap overlay={ov} summaries={summariesQuery.data} />}

          {abstractText && (
            <Fold id="abstract" label="Abstract" summary={abstractText.replace(/\s+/g, ' ').slice(0, 140)}>
              <AbstractBlock abstract={abstractText} variant="expandable" />
            </Fold>
          )}

        </main>

        <aside id="paper-actions" tabIndex={-1} className="mt-8 scroll-mt-20 xl:sticky xl:top-4 xl:mt-0 xl:max-h-[calc(100vh-2rem)] xl:self-start xl:overflow-y-auto">
          <ActionRail
            itemKey={itemKey}
            detail={detail}
            collections={flatCollections}
            verdict={verdict}
            onTagsChanged={onTagsChanged}
            onCollectionsChanged={onCollectionsChanged}
            onNoteSaved={() => { fenceRelatedContext(); refreshDetail(); }}
            onNotesDirtyChange={setNotesDirty}
            hasPdf={hasPdf}
            serverAvailable={runner.serverAvailable}
          />
        </aside>
      </div>

      {/* The decision stays on the first iPhone screen: the full editor (notes,
          tags, collection, Ask) is still in the rail far below. */}
      <MobileVerdictBar itemKey={itemKey} verdict={verdict} />
    </div>
  );
}
