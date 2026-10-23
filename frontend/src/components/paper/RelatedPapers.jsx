import { useId, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { fetchRelatedPapers, assessRelatedPapers } from '../../api/libraryApi.js';

function Comments({ comments, used = [] }) {
  if (!comments) return <p>Comments unavailable.</p>;
  return <details className="mt-2">
    <summary className="cursor-pointer text-forest">Comments · {comments.availability}</summary>
    <p>App: {comments.app_state}; native: {comments.native_state}. Context, not paper evidence.</p>
    {comments.records.map(record => <blockquote key={record.id} className="my-2 border-l pl-3 whitespace-pre-wrap break-words">
      <p>{record.text}</p>
      <p className="text-xs text-slate-500">{record.source} · {record.authorship} · {record.id}
        {used.includes(record.id) ? ' · cited by assessment' : ''}</p>
    </blockquote>)}
  </details>;
}

export default function RelatedPapers({ itemKey }) {
  const [opened, setOpened] = useState(false);
  const owner = useId();
  const query = useQuery({ queryKey: ['related-papers', itemKey, owner],
    queryFn: ({ signal }) => fetchRelatedPapers(itemKey, { signal }),
    enabled: opened, staleTime: 0, retry: false });
  const assessment = useMutation({ mutationFn: ({ token, retry }) => assessRelatedPapers(itemKey, token, retry),
    onSuccess: () => query.refetch() });
  const baseline = opened && !query.isFetching && !query.error ? query.data : undefined;
  const outdated = assessment.data && assessment.data.context_fingerprint !== baseline?.context_fingerprint;
  const data = !outdated && assessment.data || baseline;
  const boundary = baseline?.boundary;
  const run = () => {
    if (!boundary?.enabled) return;
    if (!window.confirm(`Experimental assessment: send complete private comments for this paper and up to 10 saved candidates to ${boundary.provider} / ${boundary.model} (${boundary.endpoint || 'native provider API'})? Results and full context are saved locally for reuse. No provider search, review or Add is started. Usefulness is unqualified.`)) return;
    assessment.mutate({ token: boundary.token, retry: ['failed', 'interrupted', 'stale'].includes(data?.assessment_status) });
  };
  const used = (data?.items || []).flatMap(row => row.used_context_ids || []);
  return <section aria-label="Related in my library" className="py-6 text-sm text-slate-700 break-words">
    <h2 className="font-display text-xl text-forest">Related in my library</h2>
    {!opened && <button type="button" className="mt-2 text-forest underline" onClick={() => setOpened(true)}>Show related saved papers</button>}
    {query.isFetching && <p role="status">Loading saved-library connections…</p>}
    {query.error && <p role="alert">Related papers unavailable: {query.error.message}. Review remains usable; previous comments stay hidden until a fresh read succeeds.
      <button type="button" className="ml-2 underline" onClick={() => query.refetch()}>Retry library read</button></p>}
    {data && <>
      <p className="mt-2">{data.comments_in_ranking ? 'Experimental comment-aware assessment of a metadata-retrieved pool; model-cited use, influence unverified.'
        : data.assessment_status === 'assessed' ? 'Metadata-retrieved pool; model reported no comment use.'
          : 'Metadata hybrid search; no successful comment-use assessment.'}
        {' '}Usefulness is unqualified; comment-only candidate recall is not supported.</p>
      {outdated && <p role="status">Context changed; the previous assessment is stale. Showing metadata results.</p>}
      {data.assessment_status && <p role="status">Comment assessment: {data.assessment_status}.
        {data.assessment_status !== 'assessed' && ' Showing metadata results, not a successful comment assessment.'}</p>}
      {assessment.isPending && <p role="status">Assessing comments… Repeated requests share persisted work.</p>}
      {assessment.error && <p role="alert">Comment assessment unavailable: {assessment.error.message}</p>}
      <button type="button" className="mt-2 text-forest underline disabled:opacity-50" onClick={run}
        disabled={!boundary?.enabled || assessment.isPending || query.isFetching || Boolean(query.error)}>
        Try with comments — experimental
      </button>
      {boundary && <p className="text-xs mt-1">Processing boundary: {boundary.provider} / {boundary.model} · {boundary.endpoint || 'native provider API'}.
        Complete private comments are sent only after confirmation. Up to 10 candidates / 30,000 input characters; over-budget contexts are not clipped.</p>}
      {data.status === 'unavailable' && <p role="status">Saved-library search unavailable; check corpus/model availability.</p>}
      {data.status === 'empty' && <p role="status">No related saved papers found in this scope{data.assessment_status === 'assessed' ? ' with a supported, preference-compatible assessment' : ''}.</p>}
      <Comments comments={data.seed_comments} used={used} />
      <ol className="mt-3 space-y-4">
        {(data.items || []).map(row => <li key={row.item_key}>
          <Link className="text-forest underline" to={`/paper/${encodeURIComponent(row.item_key)}`}>{row.title}</Link>
          <p>{row.relationship}</p>
          <p className="text-xs text-slate-500">{row.source_basis}</p>
          {row.evidence && <details className="mt-1"><summary className="cursor-pointer text-forest">Metadata evidence</summary>
            <p>Quote membership is checked; entailment is not independently verified.</p>
            {Object.entries(row.evidence).map(([side, evidence]) => <blockquote key={side} className="mt-2 border-l pl-3">
              {side} · {evidence.item_key} · {evidence.field}: “{evidence.excerpt}”
            </blockquote>)}
          </details>}
          <Comments comments={row.comments} used={row.used_context_ids} />
        </li>)}
      </ol>
      <button type="button" className="mt-3 text-forest underline" disabled={query.isFetching || assessment.isPending}
        onClick={() => { assessment.reset(); query.refetch(); }}>Refresh saved context</button>
    </>}
  </section>;
}
