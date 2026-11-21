import { useState } from 'react';
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { flattenCollections } from './pendingHelpers.js';
import useDeepReviewRunner from '../hooks/useDeepReviewRunner.js';
import StoryToc from '../components/paper/review/StoryToc.jsx';
import ActionRail from '../components/paper/review/ActionRail.jsx';
import MobileVerdictBar from '../components/paper/review/MobileVerdictBar.jsx';
import PaperNavBar from '../components/paper/reviewPage/PaperNavBar.jsx';
import PaperHeader from '../components/paper/reviewPage/PaperHeader.jsx';
import PaperMain from '../components/paper/reviewPage/PaperMain.jsx';
import { buildToc, figuresProps, hasSectionMap } from '../components/paper/reviewPage/reviewPageModel.js';
import {
  useAttachFigures, useCollectionsQuery, useRelatedFence, useRenderQuery, useReviewDetail, useReviewDone,
  useReviewNav, useReviewOrder, useSectionSummaries, useTodayReviewContext, useUnsavedNotesGuard,
} from '../components/paper/reviewPage/paperReviewHooks.js';
import { cleanAbstract } from '../utils/feedText.js';
import { StatusBanner } from '../components/library/shared.jsx';

function PaperLoadError({ error, fromToday }) {
  return <div className="py-4"><h1 className="sr-only">Paper review</h1><StatusBanner message={`Could not load this paper: ${error.message || error}`} isError />
    {fromToday && <Link to="/today" className="text-teal-700 underline">← Back to Today</Link>}
  </div>;
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
  const cameFrom = useLocation().state?.from;  // set by in-app links (e.g. Library "Open full review")
  const queryClient = useQueryClient();
  useTodayReviewContext(fromToday, visitId, itemKey);
  const reviewOrder = useReviewOrder(itemKey, fromToday, visitId, queryClient);
  const [notesDirty, setNotesDirty] = useState(false);
  const [feedbackActive, setFeedbackActive] = useState(false); // «Give feedback» mode or panel is up
  useUnsavedNotesGuard(notesDirty);
  const { relatedRevision, fenceRelatedContext } = useRelatedFence(queryClient);
  const nav = useReviewNav({ navigate, itemKey, reviewOrder, fromToday, visitId, autoRun });
  const attach = useAttachFigures(itemKey);
  const collectionsQuery = useCollectionsQuery();
  const flatCollections = flattenCollections(collectionsQuery.data?.items || []);
  const { detail, isLoading, error, refreshDetail, onTagsChanged, onCollectionsChanged, verdict } =
    useReviewDetail(itemKey, { queryClient, fromToday, visitId, fenceRelatedContext });
  const hasPdf = Boolean(detail?.has_pdf);
  const summariesQuery = useSectionSummaries(itemKey, detail?.deep_review?.section_overlay);
  const render = useRenderQuery(itemKey);
  // Auto-generate on open only for papers with a local Zotero PDF (no silent PDF
  // fetch); a no-PDF paper shows the manual Generate path instead, even if an
  // acquired render artifact already exists.
  const onReviewDone = useReviewDone(itemKey, refreshDetail, queryClient);
  const runner = useDeepReviewRunner(itemKey, { deep: detail?.deep_review, onDone: onReviewDone, autoRun: hasPdf && autoRun });
  const figures = figuresProps({ detail, render, runner, attach });

  if (isLoading) return <div className="py-4 text-sm text-slate-500"><h1 className="sr-only">Paper review</h1>Loading review…</div>;
  if (error) return <PaperLoadError error={error} fromToday={fromToday} />;
  if (!detail) return null;

  const abstractText = cleanAbstract(detail.abstract);
  const toc = buildToc({ showFigures: Boolean(figures), hasMap: hasSectionMap(detail.deep_review?.section_overlay), abstractText });

  return (
    <div className="paper-reading-page mx-auto w-full min-w-0 max-w-[76rem] pb-44 lg:pb-6">
      <PaperNavBar fromToday={fromToday} cameFrom={cameFrom} navigate={navigate} navIdx={nav.navIdx}
        total={reviewOrder.length} prevKey={nav.prevKey} nextKey={nav.nextKey} goToKey={nav.goToKey} />
      <PaperHeader detail={detail} itemKey={itemKey} />

      <div className={`xl:grid xl:gap-8 ${toc.length > 1
        ? 'xl:grid-cols-[11rem_minmax(0,1fr)_20rem]'
        : 'xl:grid-cols-[minmax(0,1fr)_20rem]'}`}>
        <StoryToc items={toc} />

        <PaperMain itemKey={itemKey} detail={detail} runner={runner} onFeedbackActive={setFeedbackActive}
          relatedRevision={relatedRevision} summaries={summariesQuery.data} abstractText={abstractText} figures={figures} />

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
      <div hidden={feedbackActive}><MobileVerdictBar itemKey={itemKey} verdict={verdict} /></div>
    </div>
  );
}
