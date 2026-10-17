import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { discoverRelatedPapers, fetchRelatedPapers } from '../../api/libraryApi.js';
import { reviewPaperUrl } from '../../pages/reviewHelpers.js';

function Comments({ context, label }) {
  if (!context) return null;
  return (
    <details className="mt-2 text-sm">
      <summary className="cursor-pointer text-slate-600">{label} — {context.availability}</summary>
      <p className="mt-1 text-slate-500">Context only, not used for ranking or as evidence of a paper relationship.</p>
      {!context.app_available && <p>App comments unavailable.</p>}
      {!context.native_available && <p>Native comments unavailable.</p>}
      {(context.records || []).map((record) => (
        <div key={record.id} className="mt-2 border-l-2 border-slate-200 pl-3">
          <p className="text-xs text-slate-500">{record.source.replaceAll('_', ' ')} · {record.authorship === 'user' ? 'user text' : 'authorship uncertain'}</p>
          <blockquote className="whitespace-pre-wrap break-words">{record.text}</blockquote>
          {record.paper_excerpt && <details className="mt-1"><summary>Highlighted paper text (separate from comment)</summary>
            <blockquote>{record.paper_excerpt}</blockquote></details>}
          <span className="text-xs text-slate-500 break-all">{record.id} · revision {record.revision ?? 'unknown'}</span>
        </div>
      ))}
    </details>
  );
}

function DiscoveryResults({ session }) {
  if (!session) return null;
  const candidates = session.candidates || [];
  const outcomes = session.discovery?.source_outcomes || [];
  const availability = session.discovery?.provider_availability || 'unknown';
  return (
    <div className="mt-3" role="status">
      <p className="text-sm">Discovery only — nothing added or reviewed. “New” means not saved, not recently published.</p>
      <p className="mt-1 text-xs text-slate-500">Private comments were not sent to search providers. Source availability: {availability.replaceAll('_', ' ')}.</p>
      {availability === 'partial_failure' && <p className="mt-1 text-sm text-amber-800">Partial provider failure — available results are retained. Use Find new relevant again to retry.</p>}
      {availability === 'failed' && <p className="mt-1 text-sm text-amber-800">Search providers failed; this is not an empty search.</p>}
      {availability === 'unknown' && <p className="mt-1 text-sm">Some provider outcomes are unknown; no claim of an exhaustive or genuinely empty search.</p>}
      {!!outcomes.length && <details className="mt-2 text-sm"><summary>Source outcomes</summary><ul>
        {outcomes.map((outcome) => <li key={`${outcome.provider}-${outcome.variant}`}>
          {outcome.provider} · pass {outcome.variant + 1} · {outcome.status.replaceAll('_', ' ')}
          {Number.isInteger(outcome.count) && ` · ${outcome.count} returned`}{outcome.error_code && ` · ${outcome.error_code.replaceAll('_', ' ')}`}
        </li>)}
      </ul></details>}
      {!candidates.length && <p className="mt-2 text-sm">No unsaved candidates returned in this search. This is not proof that none exist.</p>}
      <ul className="mt-2 space-y-2">
        {candidates.map((candidate) => {
          const url = reviewPaperUrl(candidate);
          return (
            <li key={candidate.candidate_id} className="text-sm">
              {url
                ? <a href={url} target="_blank" rel="noreferrer" className="text-teal-700 underline">{candidate.title}</a>
                : <><span>{candidate.title}</span><span className="block text-xs text-slate-500">No supported link available.</span></>}
              <span className="block text-xs text-slate-500">{candidate.in_library === false ? 'Not saved (identifier checked)' : 'Saved-library membership unknown — not confirmed new'} · metadata similarity, relationship unverified</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default function RelatedLibrary({ itemKey, detail, onOpenPaper }) {
  const [online, setOnline] = useState(() => navigator.onLine);
  const previous = useRef(detail);
  const related = useQuery({
    queryKey: ['related-library', itemKey], queryFn: () => fetchRelatedPapers(itemKey),
    networkMode: 'always', retry: false,
  });
  const discovery = useMutation({ mutationFn: ({ refresh = false } = {}) => discoverRelatedPapers(itemKey, { refresh }), retry: false });
  const { reset: resetDiscovery } = discovery;
  const { refetch } = related;
  useEffect(() => {
    if (previous.current !== detail) {
      previous.current = detail;
      refetch();
      resetDiscovery();
    }
  }, [detail, refetch, resetDiscovery]);
  useEffect(() => {
    const offline = () => setOnline(false);
    const connected = () => { setOnline(true); refetch(); };
    window.addEventListener('offline', offline);
    window.addEventListener('online', connected);
    return () => { window.removeEventListener('offline', offline); window.removeEventListener('online', connected); };
  }, [refetch]);
  const data = related.data;
  const stale = !online || data?.offline || related.isError;
  return (
    <section id="related-library" className="scroll-mt-20 py-6 min-w-0" aria-labelledby={`related-heading-${itemKey}`}>
      <h2 id={`related-heading-${itemKey}`} className="font-display text-xl font-light text-slate-900">Related in my library</h2>
      <p className="mt-1 text-sm text-slate-500">Up to three library-search matches, including already-read papers. Comments remain context-only: their semantic intent is unqualified, so comment-aware ranking is unavailable.</p>
      {related.isPending && <p className="mt-2 text-sm" role="status">Finding related saved papers…</p>}
      {related.isError && <p className="mt-2 text-sm text-amber-800" role="alert">Related papers unavailable. Your review is still usable. {related.error.message}</p>}
      {data && stale && <p className="mt-2 text-sm text-amber-800" role="status">Offline or stale snapshot — saved membership and comment edits/deletions may have changed.</p>}
      {data?.status === 'unavailable' && <p className="mt-2 text-sm">Library search unavailable. Enable or sync the corpus in Settings; no unrelated fallback is shown.</p>}
      {data?.status === 'empty' && <p className="mt-2 text-sm">No related saved papers returned by this library search.</p>}
      {data?.search_status?.reranker_loading && <p className="mt-2 text-sm">Reranker loading — showing the existing hybrid-fusion order.</p>}
      {(data?.items || []).slice(0, 3).map((paper) => (
        <article key={paper.item_key} className="mt-4 border-t border-slate-200 pt-3 break-words">
          <Link to={`/paper/${encodeURIComponent(paper.item_key)}`} className="text-teal-700 underline"
            onClick={(event) => {
              if (!onOpenPaper || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
              event.preventDefault(); onOpenPaper(paper.item_key);
            }}>{paper.title}</Link>
          <p className="mt-1 text-sm">{paper.relationship.sentence}</p>
          <details className="mt-1 text-sm"><summary className="cursor-pointer text-slate-600">Evidence · {paper.source_basis.replaceAll('_', ' ')}</summary>
            <p>Public metadata only; original full-text relationships have not been assessed.</p>
            {Object.entries(paper.evidence || {}).map(([side, evidence]) => (
              <blockquote key={side} className="mt-2 border-l-2 border-slate-200 pl-3">
                <span className="block text-xs text-slate-500">{side} · {evidence.field} · characters {evidence.start}–{evidence.end}</span>{evidence.excerpt}
              </blockquote>
            ))}
          </details>
          <Comments context={paper.comments} label="Saved paper comments" />
        </article>
      ))}
      <Comments context={data?.seed_comments} label="This paper’s comments" />
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button type="button" className="text-sm text-teal-700 underline disabled:opacity-50" disabled={related.isFetching}
          onClick={() => { resetDiscovery(); refetch(); }}>Refresh related</button>
        {data?.saved_seed && <button type="button" className="rounded-lg bg-teal-700 px-3 py-2 text-sm text-white disabled:opacity-50"
          disabled={stale || related.isFetching || discovery.isPending} onClick={() => discovery.mutate({
            refresh: discovery.isError || ['partial_failure', 'failed', 'unknown', 'not_attempted'].includes(discovery.data?.discovery?.provider_availability),
          })}>
          {discovery.isPending ? 'Finding new relevant papers…' : 'Find new relevant'}
        </button>}
      </div>
      {data?.saved_seed && <p className="mt-2 text-xs text-slate-500">Find uses this paper’s public title/abstract with your configured Search providers. No automatic Add or review.</p>}
      {discovery.isError && <p className="mt-2 text-sm text-amber-800" role="alert">Discovery unavailable: {discovery.error.message}. Retry the same action; your review is unchanged.</p>}
      <DiscoveryResults session={discovery.data} />
    </section>
  );
}
