import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { fetchRelatedPapers, assessRelatedPapers, fetchDiscovery, findNewRelevant } from '../../api/libraryApi.js';

const COMMENTS_LABEL = { available: 'Has your comments', empty: 'No comments' };
const SOURCE_BASIS = { abstract_only: 'Matched on abstract only', title_only: 'Matched on title only',
  metadata_only_model_judgment: 'Judged from title and abstract' };

export function Comments({ comments, used = [], submitted = [] }) {
  if (!comments) return <p>Comments unavailable.</p>;
  return <details className="mt-2">
    <summary className="cursor-pointer text-forest">{COMMENTS_LABEL[comments.availability] || `Comments ${comments.availability}`}</summary>
    <p>App: {comments.app_state}; native: {comments.native_state}. Context, not paper evidence.</p>
    {comments.records.map(record => <blockquote key={record.id} className="my-2 border-l pl-3 whitespace-pre-wrap break-words">
      <p>{record.text}</p>
      <p className="text-xs text-slate-500">{record.source} · {record.authorship} · {record.id}
        {used.includes(record.id) ? ' · cited by assessment' : submitted.includes(record.id) ? ' · submitted, not model-cited' : ''}</p>
    </blockquote>)}
  </details>;
}

export default function RelatedPapers({ itemKey, heading = true }) {
  const [opened, setOpened] = useState(false);
  const owner = useId();
  const navigate = useNavigate();
  const location = useLocation();
  const request = useRef(null);
  const [finding, setFinding] = useState(false);
  const [waited, setWaited] = useState(0);
  const [findError, setFindError] = useState(null);
  const [consent, setConsent] = useState(null);
  useLayoutEffect(() => {
    const controller = new AbortController();
    request.current = controller;
    setFinding(false); setFindError(null); setConsent(null);
    return () => controller.abort();
  }, [itemKey, location.key]);
  const metadata = useQuery({ queryKey: ['discovery-metadata', itemKey, owner],
    queryFn: ({ signal }) => fetchDiscovery(itemKey, { signal }),
    retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false, refetchOnMount: false });
  useEffect(() => {
    if (!finding) { setWaited(0); return undefined; }
    const timer = setInterval(() => setWaited(seconds => seconds + 1), 1000);
    return () => clearInterval(timer);
  }, [finding]);
  const discovery = !metadata.isFetching && !metadata.error ? metadata.data : null;
  const find = async () => {
    if (!discovery?.context_fingerprint || finding) return;
    const controller = request.current;
    setFinding(true); setFindError(null);
    try {
      const result = await findNewRelevant(itemKey, { contextFingerprint: discovery.context_fingerprint,
        consentToken: consent === discovery.boundary?.token ? consent : null,
        retry: Boolean(findError) || new URLSearchParams(location.search).get('retryDiscovery') === '1', signal: controller.signal });
      if (!controller.signal.aborted && request.current === controller) navigate(`/search?session=${encodeURIComponent(result.id)}`);
    } catch (error) {
      if (!controller.signal.aborted && request.current === controller) setFindError(error);
    } finally {
      if (!controller.signal.aborted && request.current === controller) setFinding(false);
    }
  };
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
  const used = (data?.assessment?.items || data?.items || []).flatMap(row => row.used_context_ids || []);
  const submitted = data?.submitted_context_ids || [];
  return <section aria-label="Related in my library" className="text-sm text-slate-700 break-words">
    {heading && <h2 className="font-display text-xl text-forest">Related in my library</h2>}
    {metadata.isFetching && <p role="status">Checking saved discovery eligibility…</p>}
    {metadata.error && <p role="alert">Discovery eligibility unavailable: {metadata.error.message}
      <button type="button" className="ml-2 underline" onClick={() => metadata.refetch()}>Retry discovery eligibility</button></p>}
    {discovery && !discovery.context_fingerprint && <p role="status">
      Similar-paper search is unavailable: this paper's library context could not be read{discovery.reason ? ` (${discovery.reason})` : ''}. Retry below.</p>}
    {discovery && <>
      <p>{discovery.admission.state === 'saved' ? 'Searches open sources for new papers like this one.'
        : 'Not in your library yet. Searches open sources for papers like this one.'}</p>
      <button type="button" className="mt-2 text-forest underline disabled:opacity-50" onClick={find}
        disabled={finding || !discovery.context_fingerprint}>{finding ? 'Searching…' : 'Find new relevant'}</button>
      {discovery.context_fingerprint && <details className="mt-2"><summary className="cursor-pointer text-forest">What is sent, and optional comments</summary>
        <p>Search providers receive public paper metadata only. No review or Add starts. Full context and results are retained locally.</p>
        <p>Optional private context includes seed comments and your reading verdict/rationale.</p>
        <p>Private seed context: {discovery.private_context.status} · {discovery.private_context.record_count ?? 'unknown'} records.</p>
        <p>Comments would be sent to: {discovery.boundary?.provider}. Only after you confirm.</p>
        <label><input type="checkbox" checked={Boolean(consent && consent === discovery.boundary?.token)}
          disabled={finding || !discovery.boundary?.enabled} onChange={event => setConsent(event.target.checked && window.confirm(
            `Send complete private seed comments and your reading verdict/rationale only to ${discovery.boundary.provider} / ${discovery.boundary.model} (${discovery.boundary.endpoint || 'native provider API'})? Search providers still receive public metadata only. Full context and results stay locally. Experimental usefulness is unqualified.`
          ) ? discovery.boundary.token : null)} /> Use private seed comments — experimental</label>
        <p>Unchecked means public metadata only. Up to 10 candidates / 30,000 input characters; over-budget contexts are not clipped.</p>
      </details>}
    </>}
    {(findError || (discovery && !discovery.context_fingerprint)) && <button type="button" disabled={finding}
      onClick={() => { setConsent(null); metadata.refetch(); }}>Refresh discovery eligibility</button>}
    {finding && <p role="status">Searching open sources… {waited}s (usually under a minute). Results open in Search.
      {consent ? ' Assessing your comments afterwards.' : ''}</p>}
    {findError && <p role="alert">Discovery unavailable: {findError.message}. Retry the action explicitly; no automatic retry occurs.</p>}
    {!opened && <button type="button" className="mt-2 text-forest underline" onClick={() => setOpened(true)}>Show related saved papers</button>}
    {query.isFetching && <p role="status">Loading saved-library connections…</p>}
    {query.error && <p role="alert">Related papers unavailable: {query.error.message}. Review remains usable; previous comments stay hidden until a fresh read succeeds.
      <button type="button" className="ml-2 underline" onClick={() => query.refetch()}>Retry library read</button></p>}
    {data && <>
      {!heading && <h3 className="mt-3 font-display text-lg text-forest">Related in my library</h3>}
      <p className="mt-2">{data.comments_in_ranking ? 'Found by title and abstract; the model says it used your comments (experimental).'
        : data.assessment_status === 'assessed' ? 'Found by title and abstract. The model reported no use of your comments.'
          : 'Found by title and abstract only. Comments were not used.'}</p>
      {outdated && <p role="status">Context changed; the previous assessment is stale. Showing metadata results.</p>}
      {data.assessment_status && <p role="status">Comment assessment: {data.assessment_status}.
        {data.assessment_status !== 'assessed' && ' Showing metadata results, not a successful comment assessment.'}</p>}
      {assessment.isPending && <p role="status">Assessing comments… Repeated requests share persisted work.</p>}
      {assessment.error && <p role="alert">Comment assessment unavailable: {assessment.error.message}</p>}
      <button type="button" className="mt-2 text-forest underline disabled:opacity-50" onClick={run}
        disabled={!boundary?.enabled || assessment.isPending || query.isFetching || Boolean(query.error)}>
        Try with comments — experimental
      </button>
      {boundary && <p className="text-xs mt-1">Comments would be sent to: {boundary.provider}. Only after you confirm.
        Up to 10 candidates / 30,000 input characters; over-budget contexts are not clipped.</p>}
      {data.status === 'unavailable' && <p role="status">Saved-library search unavailable; check corpus/model availability.</p>}
      {data.status === 'empty' && <p role="status">No related saved papers found in this scope{data.assessment_status === 'assessed' ? ' with a supported, preference-compatible assessment' : ''}.</p>}
      <Comments comments={data.seed_comments} used={used} submitted={submitted} />
      <ol className="mt-3 space-y-4">
        {(data.items || []).map(row => <li key={row.item_key}>
          <Link className="text-forest underline" to={`/paper/${encodeURIComponent(row.item_key)}`}>{row.title}</Link>
          <p>{row.relationship}</p>
          <p className="text-xs text-slate-500">{SOURCE_BASIS[row.source_basis] || row.source_basis}</p>
          {row.evidence && <details className="mt-1"><summary className="cursor-pointer text-forest">Metadata evidence</summary>
            <p>Quote membership is checked; entailment is not independently verified.</p>
            {Object.entries(row.evidence).map(([side, evidence]) => <blockquote key={side} className="mt-2 border-l pl-3">
              {side} · {evidence.item_key} · {evidence.field}: “{evidence.excerpt}”
            </blockquote>)}
          </details>}
          <Comments comments={row.comments} used={row.used_context_ids} submitted={submitted} />
        </li>)}
      </ol>
      <button type="button" className="mt-3 text-forest underline" disabled={query.isFetching || assessment.isPending}
        onClick={() => { assessment.reset(); query.refetch(); }}>Reload related papers</button>
    </>}
  </section>;
}
