import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchCollections, fetchPaperRender } from '../api/libraryApi.js';
import { flattenCollections } from './pendingHelpers.js';
import usePaperReview from '../hooks/usePaperReview.js';
import useDeepReviewRunner from '../hooks/useDeepReviewRunner.js';
import AuthorByline from '../components/AuthorByline.jsx';
import LinksRow from '../components/paper/LinksRow.jsx';
import AbstractBlock from '../components/paper/PaperDetailView/AbstractBlock.jsx';
import PaperReview from '../components/paper/review/PaperReview.jsx';
import PaperFigures from '../components/library/PaperFigures.jsx';
import SectionMap from '../components/paper/review/SectionMap.jsx';
import StoryToc from '../components/paper/review/StoryToc.jsx';
import ActionRail from '../components/paper/review/ActionRail.jsx';
import VerdictPicker from '../components/VerdictPicker.jsx';
import { Chip } from '../components/paper/review/primitives.jsx';
import { StatusBanner } from '../components/library/shared.jsx';
import { gradeTone, bandTone, BAND_LABEL } from '../components/paper/review/tones.js';
import Spinner from '../components/ui/Spinner.jsx';

// "92" -> "1m 32s". Live elapsed/ETA for the auto-generating review.
function fmt(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m}m ${r}s` : `${m}m`;
}

const EYEBROW = 'mb-2 text-[11px] uppercase tracking-[0.08em] font-semibold text-slate-400';

// The review zone: the cached review (flat + located findings) when present; else
// the access banners + a prominent Generate button + a live skeleton while it
// runs. The runner auto-fires on open for a paper with a PDF (the user opted into
// auto-generate-on-open), so the common case renders instantly.
function ReviewZone({ deep, runner, sectionOverlay }) {
  const { status, error, llm, running } = runner;
  const reviewed = deep && !deep.needs_pdf && (deep.digest || deep.quality || (deep.goal_summaries || []).length);
  return (
    <div className="space-y-3">
      {llm && llm.reachable === false && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[13px] leading-relaxed text-amber-800" role="alert">
          <span className="font-semibold">Deep-review model unreachable.</span>{' '}
          <span className="font-mono text-[12px]">{llm.model || '(model unset)'}</span> at{' '}
          <span className="font-mono text-[12px]">{llm.base_url || '(no base URL)'}</span> isn&apos;t responding. Start that
          server, or pick a reachable model in <span className="font-semibold">Settings → LLM routing</span>.
        </div>
      )}
      {deep && deep.needs_pdf && deep.needs_login && deep.login_url && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[13px] leading-relaxed text-amber-800">
          <span className="font-semibold">Needs your library sign-in.</span>{' '}
          <a href={deep.login_url} target="_blank" rel="noopener noreferrer" className="font-medium text-indigo-700 hover:underline">
            Open it in your browser
          </a>{' '}
          to sign in, then re-generate.
        </div>
      )}
      {deep && deep.needs_pdf && !(deep.needs_login && deep.login_url) && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[13px] leading-relaxed text-amber-800">
          <span className="font-semibold">No full text available.</span>{' '}
          The review tried open access, PubMed Central, and your library session but couldn&apos;t reach a readable copy.
        </div>
      )}

      {reviewed ? (
        <PaperReview deep={deep} flat sectionOverlay={sectionOverlay} />
      ) : running ? (
        <div className="rounded-lg border border-slate-200 bg-white px-4 py-5 text-[13px] text-slate-600" role="status" aria-live="polite">
          <div className="flex items-center gap-2 font-semibold text-slate-700">
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
      ) : (
        !deep?.needs_pdf && (
          <button
            type="button"
            onClick={() => runner.run()}
            disabled={llm?.reachable === false}
            className="inline-flex items-center gap-2 rounded-lg bg-teal-700 px-4 py-2 text-[13px] font-semibold text-white hover:bg-teal-800 disabled:opacity-50"
          >
            Generate review
          </button>
        )
      )}
      {status.status === 'error' && status.error && (
        <div className="text-[12px] text-rose-700">Review failed: {status.error}</div>
      )}
      {error && <div className="text-[12px] text-rose-700">{error}</div>}
    </div>
  );
}

// Full-page, single-scroll deep review for one paper (opened from a Read-next row).
// Everything renders up front — the located review, figures, the paper map, the
// abstract — with a sticky TOC + action/chat rail. The decision (verdict, file,
// tags, ask) lives in the rail and a mobile bottom bar so it's always reachable.
export default function PaperReviewPage() {
  const { itemKey } = useParams();
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
  const { detail, isLoading, error, refreshDetail, onTagsChanged, onCollectionsChanged, verdict } =
    usePaperReview(itemKey, {});
  const hasPdf = Boolean(detail?.has_pdf);
  const render = renderQuery.data || null;
  const renderCompleted = render?.status === 'completed';
  const showFigures = hasPdf || renderCompleted || render?.status === 'running';
  const canAsk = hasPdf || renderCompleted;
  // Auto-generate on open only for papers with a local Zotero PDF (no silent PDF
  // fetch); a no-PDF paper shows the manual Generate path instead, even if an
  // acquired render artifact already exists.
  const runner = useDeepReviewRunner(itemKey, { deep: detail?.deep_review, onDone: refreshDetail, autoRun: hasPdf });

  if (isLoading) return <div className="py-4 text-sm text-slate-500">Loading review…</div>;
  if (error) {
    return <div className="py-4"><StatusBanner message={`Could not load this paper: ${error.message || error}`} isError /></div>;
  }
  if (!detail) return null;

  const deep = detail.deep_review || null;
  const ov = deep?.section_overlay || null;
  const dg = deep?.digest || null;
  const ql = deep?.quality || null;
  const goalsArr = deep?.goal_summaries || [];
  const grade = dg?.grade || ql?.grade || '';
  const band = String(ql?.quality_band || '');
  const redFlagCount = (ql?.red_flags || []).filter(Boolean).length;
  const nHit = goalsArr.filter((g) => String(g?.retrieval_state || '') === 'hit').length;
  const hasMap = Boolean(ov && !ov.degraded && (ov.sections || []).length);

  const toc = [
    { id: 'review', label: 'Review' },
    showFigures && { id: 'figures', label: 'Figures' },
    hasMap && { id: 'paper-map', label: 'Paper map' },
    detail.abstract && { id: 'abstract', label: 'Abstract' },
  ].filter(Boolean);

  return (
    <div className="pb-24 lg:pb-6">
      <Link to="/library" className="inline-block text-xs text-slate-500 hover:text-teal-700">← Read next</Link>

      <header className="mb-5 mt-2">
        <h1 className="max-w-[30ch] font-display text-[28px] font-light leading-[1.12] tracking-tight text-slate-900 sm:text-[32px]">
          {detail.title}
        </h1>
        <div className="mt-1.5"><AuthorByline authors={detail.authors} quiet /></div>
        {(detail.venue || detail.year) && (
          <div className="text-[12px] text-slate-500">
            <span className="text-slate-600">{detail.venue}</span>{detail.venue && detail.year ? ' · ' : ''}{detail.year}
          </div>
        )}
        {(grade || band || redFlagCount > 0 || goalsArr.length > 0) && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {grade && <Chip tone={gradeTone(grade)} title="Reference-free full-text quality grade">Quality {grade}</Chip>}
            {band && <Chip tone={bandTone(band)}>{BAND_LABEL[band] || '—'}</Chip>}
            {redFlagCount > 0 && <Chip tone="rose" title="Red flags found in the review">⚑ {redFlagCount}</Chip>}
            {goalsArr.length > 0 && <Chip tone="emerald">◎ {nHit}/{goalsArr.length} goals</Chip>}
          </div>
        )}
        <div className="mt-3"><LinksRow detail={detail} itemKey={itemKey} /></div>
      </header>

      <div className="lg:grid lg:grid-cols-[11rem_minmax(0,1fr)_20rem] lg:gap-8">
        <StoryToc items={toc} />

        <main className="min-w-0 divide-y divide-slate-200/60">
          <section id="review" className="scroll-mt-20 pb-6">
            <ReviewZone deep={deep} runner={runner} sectionOverlay={ov} />
          </section>

          {showFigures && (
            <section id="figures" className="scroll-mt-20 py-6">
              <div className={EYEBROW}>Figures</div>
              <PaperFigures itemKey={itemKey} hasPdf={hasPdf} canBuild={hasPdf} />
            </section>
          )}

          {hasMap && (
            <section id="paper-map" className="scroll-mt-20 py-6">
              <SectionMap overlay={ov} />
            </section>
          )}

          {detail.abstract && (
            <section id="abstract" className="scroll-mt-20 py-6">
              <div className={EYEBROW}>Abstract</div>
              <AbstractBlock abstract={detail.abstract} variant="expandable" />
            </section>
          )}
        </main>

        <aside className="mt-8 lg:sticky lg:top-4 lg:mt-0 lg:max-h-[calc(100vh-2rem)] lg:self-start lg:overflow-y-auto">
          <ActionRail
            itemKey={itemKey}
            detail={detail}
            collections={flatCollections}
            verdict={verdict}
            onTagsChanged={onTagsChanged}
            onCollectionsChanged={onCollectionsChanged}
            hasPdf={hasPdf}
            canAsk={canAsk}
          />
        </aside>
      </div>

      {/* Mobile: the verdict stays one tap away (Fitts's Law / Serial Position). */}
      <div className="fixed inset-x-0 bottom-0 z-20 border-t border-slate-200 bg-white/95 px-4 py-2 backdrop-blur lg:hidden">
        <VerdictPicker
          value={verdict.existing?.user_priority ?? null}
          onPick={(p) => verdict.onSubmit({ user_priority: p, comment: '' })}
          disabled={verdict.submitting}
          size="sm"
        />
      </div>
    </div>
  );
}
