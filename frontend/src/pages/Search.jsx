import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Link, useInRouterContext, useLocation, useNavigate } from 'react-router-dom';
import {
  screen as screenApi,
  getSession,
  startReview,
  materialize as materializeApi,
} from '../api/searchApi.js';
import CollectionPicker from '../components/CollectionPicker.jsx';
import QuestionEvidence, { EvidencePolicyStatus } from '../components/search/QuestionEvidence.jsx';
import { ErrorBanner, StatusBanner } from '../components/library/shared.jsx';
import { cleanAbstract } from '../utils/feedText.js';
import { humanizeError } from '../utils/humanizeError.js';
import { sortSearchCandidates } from '../utils/searchSort.js';
import { reviewPaperUrl } from './reviewHelpers.js';
import { readStoredJson, writeStorage } from '../utils/safeStorage.js';
import { Comments } from '../components/paper/RelatedPapers.jsx';
import { pollSessionUntilDone } from '../searchPolling.js';
import PageTitle from '../components/ui/PageTitle.jsx';

// Targeted Search — the query-driven pull surface (services/search). Give a
// research topic, get a per-source query plan, a federated + deduped + relevance-
// ranked candidate list (SCREEN), then trigger the light+deep review (REVIEW,
// background-polled). Quality is measured on the top band BEFORE the deep set is
// chosen, so a rigorous paper ranked 6th can still be deep-read.

// Full-text quality band chip. Von Restorff: relevance is the master signal (it owns
// the saturated pill), so a quality *win* stays neutral — only a quality *problem*
// (flag) earns a warning color. Keeps the card from emphasizing everything at once.
const BAND_LABEL = { highlight: 'looks solid', flag: 'needs checking', uncertain: 'quality unclear' };
const SOURCE_LABEL = { crossref: 'Crossref', europepmc: 'Europe PMC', openalex: 'OpenAlex', arxiv: 'arXiv',
  semantic_scholar: 'Semantic Scholar', openreview: 'OpenReview', library: 'Your library' };
const VERSION_LABEL = { version_of_record: 'Published article', preprint: 'Preprint', accepted_manuscript: 'Accepted manuscript' };
const BAND_CLASS = {
  highlight: 'bg-slate-100 text-slate-600',
  flag: 'bg-rose-100 text-rose-800',
  uncertain: 'bg-slate-100 text-slate-600',
};

// Relevance band (pool-relative, from query_score) — server field cand.relevance_band.
// Distinct from the full-text quality band above; drives the card accent + a pill.
const REL_BAND = {
  strong: { label: 'strong match', pill: 'bg-emerald-600 text-white', card: 'border-emerald-300 bg-emerald-50/40' },
  on_topic: { label: 'on-topic', pill: 'bg-sky-600 text-white', card: 'border-sky-200 bg-white' },
  weak: { label: 'weak match', pill: 'bg-slate-200 text-slate-600', card: 'border-slate-200 bg-slate-50/60' },
};

// Strip boolean syntax (AND/OR, quotes, field tags, parentheses) so the plan reads as words.
const readableQuery = (q) => String(q || '')
  .replace(/\b(?:TITLE_ABS|TITLE|ABSTRACT|all|ti|abs):/g, '').replace(/[()"]/g, '')
  .replace(/\s+(?:AND|OR)\s+/g, ' · ').replace(/\s+/g, ' ').trim();

function QueryPlan({ plan }) {
  const rows = plan.display || [];
  if (!rows.length) return null;
  return (
    <details className="mb-4 text-[12px]">
      <summary className="cursor-pointer text-slate-600 font-semibold">What was searched, by source</summary>
      <div className="mt-2 grid gap-1">
        {rows.map(({ source: src, query: q }, index) => (
          <div key={index} className="flex flex-wrap gap-x-2">
            <span className="text-slate-500 sm:w-40 shrink-0">{SOURCE_LABEL[src] || src}</span>
            <span className="text-slate-800 min-w-0 break-words">{readableQuery(q)}</span>
          </div>
        ))}
      </div>
    </details>
  );
}

// Agentic-search rounds (Phase E) — one line per PRF round: what the LLM added to
// broaden the pool, what it dropped as off-topic, and how many new papers it pulled.
function Refinements({ rounds }) {
  if (!rounds || !rounds.length) return null;
  return (
    <div className="mb-4 rounded-lg border border-violet-200 bg-violet-50/60 px-3 py-2 text-[12px]">
      <div className="font-semibold text-violet-800 mb-1">Agentic refinement</div>
      {rounds.map((r) => (
        <div key={r.round} className="text-slate-700">
          <span className="text-slate-500">Round {r.round}:</span>{' '}
          {(r.add_concepts || []).length > 0 && <>added <span className="font-medium">{(r.add_concepts || []).join(', ')}</span></>}
          {(r.drop_terms || []).length > 0 && <> · de-emphasized (not excluded) <span className="font-medium">{(r.drop_terms || []).join(', ')}</span></>}
          {typeof r.new_candidates === 'number' && <span className="text-slate-500"> (+{r.new_candidates} new)</span>}
        </div>
      ))}
    </div>
  );
}

function GroundedRow({ label, row }) {
  if (!row || (!row.text && !(row.supporting_quotes || []).length)) return null;
  return (
    <div className="mt-2">
      {label && <div className="text-[11px] font-semibold text-slate-500">{label}</div>}
      {row.text && <div className="text-[13px] text-slate-800">{row.text}</div>}
      {(row.supporting_quotes || []).map((q, i) => (
        <blockquote key={i} className="mt-1 border-l-2 border-slate-300 pl-2 text-[12px] italic text-slate-600">
          {q}
        </blockquote>
      ))}
    </div>
  );
}

function ReviewPanel({ review }) {
  if (!review || !Object.keys(review).length) return <p className="mt-2 text-xs">Not reviewed.</p>;
  if (review.state === 'error') return <p role="alert">Review failed: {review.error}</p>;
  if (review.state === 'needs_full_text') {
    return <div className="mt-2 text-[12px] text-slate-500">Full text unavailable after acquisition — not deep-reviewed.</div>;
  }
  const brief = review.brief || {};
  return (
    <div className="mt-3 border-t border-slate-200 pt-2">
      {brief.tldr && <div className="text-[13px] text-slate-900"><span className="font-semibold">TL;DR:</span> {brief.tldr}</div>}
      <GroundedRow label="For your query" row={review.query_lens} />
      {(review.question_answers || []).map((qa, i) => (
        <GroundedRow key={i} label={qa.question} row={qa} />
      ))}
      {brief.key_findings?.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-[12px] text-slate-700">
          {brief.key_findings.map((f, i) => <li key={i}>{f}</li>)}
        </ul>
      )}
    </div>
  );
}

const RETRIEVAL = {
  observed: 'The sources returned results.', partial: 'Some sources did not answer, so this is not a complete search.',
  failed: 'The sources did not answer.', unknown: 'It is unknown whether the sources answered.', not_attempted: 'The sources were not searched.',
};
const ASSESSMENT = {
  not_requested: 'Your comments were not used.', assessed: 'A model judged these from titles and abstracts; this is not a full-text review.',
  no_comments: 'This paper has no comments.', no_candidates: 'There were no candidates to judge.', disabled: 'Comment assessment is turned off.',
  budget_exceeded: 'Your comments were too long to send, so they were not used.', context_unavailable: 'Your comments could not be read, so they were not used.',
  failed: 'Comment assessment failed.', interrupted: 'Comment assessment was interrupted.', stale: 'Your comments changed since the assessment.',
};
const STATUS_LINE = {
  discovery_failed: 'The search did not finish. Try again later.', discovery_ready: 'Search finished.',
  discovery_pending: 'Search is starting…', discovery_running: 'Searching…', discovery_interrupted: 'The search was interrupted. Try again.',
  reviewing: 'Reviewing the found papers…', reviewed: 'Search and review finished.', error: 'Review failed.',
};
const failureLine = (error) => /rate|429|limit/i.test(error?.code || '')
  ? 'The search service did not answer (rate limit). Try again later.' : null;

function DiscoveryEvidence({ row }) {
  if (!row) return null;
  return <div className="review-prose min-w-0 mt-2 text-sm">
    <p>{row.sentence}</p><p>Judged from title and abstract · relevance: {row.relevance}; preference: {row.preference}.</p>
    <details><summary>Metadata evidence</summary>
      <p>Quote membership is checked; entailment is not independently verified. Comments are context, not paper evidence.</p>
      {Object.entries(row.evidence || {}).map(([side, quote]) => <blockquote key={side} className="border-l pl-3">
        {side} · {quote.item_key} · {quote.field} [{quote.start}, {quote.end}]: “{quote.excerpt}”
      </blockquote>)}
      <p>Model-cited comment IDs: {(row.used_context_ids || []).join(', ') || 'none'}.</p>
    </details>
  </div>;
}

function DiscoveryStatus({ session, trusted }) {
  const discovery = session.discovery;
  const cited = trusted ? discovery.assessment.items.flatMap(row => row.used_context_ids || []) : [];
  return <section aria-label="Discovery status" className="mb-4 text-sm break-words">
    <h2 className="font-display text-xl">Find new relevant · {discovery.seed_title}</h2>
    <p role="status">{(session.status === 'discovery_failed' && failureLine(discovery.error)) || STATUS_LINE[session.status] || session.status}
      {discovery.freshness === 'stale' && ' The saved paper changed since this search, so results may be out of date.'}</p>
    <p aria-label="Retrieval status">Searched OpenAlex by topic and keywords. {RETRIEVAL[discovery.retrieval.status]}</p>
    <p aria-label="Assessment status">{ASSESSMENT[discovery.assessment.status]}</p>
    {discovery.retrieval.empty_reason && <p>{discovery.retrieval.empty_reason === 'providers_empty'
      ? 'Scheduled providers successfully returned no candidates.' : 'Observed candidates were all known saved.'}</p>}
    {trusted && !discovery.retrieval.empty_reason && !discovery.recommended_ids.length && <p>No supported default recommendations; retained observations may still be available below.</p>}
    {!trusted && session.status !== 'discovery_failed' && <p>Comment details are hidden until the search is finished and up to date. Actions are unavailable meanwhile.</p>}
    <Link className="text-forest underline" to={`/paper/${encodeURIComponent(discovery.seed_key)}?retryDiscovery=1`}>Try again (opens the paper)</Link>
    <details><summary>Provider observations</summary>
      {discovery.retrieval.variants.map((variant, index) => <p key={index}>{variant.channel} · {variant.outcome?.status || 'unknown'} · count: {variant.outcome?.count ?? 'unknown'} · {variant.outcome?.error_code}</p>)}
    </details>
    {trusted && <>
      <p>{discovery.comments_in_ranking ? 'Model reported comment use; influence is unverified.' : 'No model-cited comment use reported.'}</p>
      <p>Submitted comment IDs (not proof of use): {discovery.assessment.submitted_context_ids.join(', ') || 'none'}.</p>
      <Comments comments={discovery.seed_comments} used={cited} submitted={discovery.assessment.submitted_context_ids} />
    </>}
  </section>;
}

function CandidateCard({ cand, onAdd, questions = [], discovery, actionsAllowed = true, trusted = false }) {
  const sources = [...new Set((cand.provenance || []).map((p) => p.source))];
  const band = (cand.quality?.quality_band || '').toLowerCase();
  const meta = [cand.venue, cand.year].filter(Boolean).join(' · ');
  const added = Boolean(cand.materialized_zotero_key || cand.existing_zotero_key);
  const rel = REL_BAND[cand.relevance_band];
  const why = Array.isArray(cand.why) ? cand.why : [];
  const [adding, setAdding] = useState(false);
  const [addErr, setAddErr] = useState(null);
  const add = useCallback(async () => {
    setAdding(true);
    setAddErr(null);
    try {
      await onAdd(cand.candidate_id);
    } catch (err) {
      setAddErr(err);
    } finally {
      setAdding(false);
    }
  }, [onAdd, cand.candidate_id]);
  const url = reviewPaperUrl(cand);
  const cardCls = cand.is_retracted ? 'bg-rose-50 border-rose-300' : (rel?.card || 'bg-white border-slate-200');
  return (
    <article aria-label={cand.title} className={`min-w-0 border rounded-xl p-3 ${cardCls}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div className="flex-1 min-w-0">
          <div className="font-semibold text-slate-900">
            {url ? <a href={url} target="_blank" rel="noreferrer" className="hover:underline">{cand.title}</a> : cand.title}
          </div>
          {!url && <p className="text-xs text-slate-600">No supported link available.</p>}
          {meta && <div className="text-[12px] text-slate-500">{meta}</div>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {rel && <span className={`text-[11px] px-2 py-0.5 rounded-full font-semibold ${rel.pill}`}>{rel.label}</span>}
          {typeof cand.query_score === 'number' && (
            <span className="mono text-[11px] text-slate-500" title="query relevance (our cross-encoder), 0–1">
              {cand.query_score.toFixed(2)}
            </span>
          )}
          {band && <span className={`text-[11px] px-2 py-0.5 rounded-full ${BAND_CLASS[band] || BAND_CLASS.uncertain}`} title="full-text quality band">{BAND_LABEL[band] || band}</span>}
        </div>
      </div>
      {/* why chips: secondary context → neutral, so they don't compete with the band
          pill or the Add CTA for attention (Von Restorff — emphasize sparingly). */}
      {why.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {why.map((r) => (
            <span key={r} className="inline-flex items-center px-2 py-0.5 rounded-full border border-slate-200 bg-slate-50 text-slate-600 text-[11px] font-medium">{r}</span>
          ))}
        </div>
      )}
      <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-slate-500">
        {sources.map((s) => <span key={s} className="px-1.5 py-0.5 bg-slate-100 rounded">{SOURCE_LABEL[s] || s}</span>)}
        {cand.version_type && cand.version_type !== 'unknown' && (
          <span className="px-1.5 py-0.5 bg-slate-100 rounded">{VERSION_LABEL[cand.version_type] || cand.version_type}</span>
        )}
        {cand.is_retracted && <span className="px-1.5 py-0.5 bg-rose-200 text-rose-900 rounded font-semibold">RETRACTED</span>}
      </div>
      {cand.abstract && <p className="mt-2 text-[12px] text-slate-600 line-clamp-3 break-words">{cleanAbstract(cand.abstract)}</p>}
      {discovery && <>
        {(discovery.unresolved_membership || []).includes(cand.candidate_id) || cand.in_library == null
          ? <p>Membership unknown — not established new.</p> : <p>{cand.in_library ? 'Known saved paper.' : 'Not saved in the checked membership snapshot.'}</p>}
        <DiscoveryEvidence row={trusted ? discovery.assessment.items.find(row => row.item_key === cand.candidate_id) : null} />
      </>}
      <ReviewPanel review={cand.review} />
      <QuestionEvidence candidate={cand} questions={questions} />
      <div className="mt-2 flex items-center gap-2">
        {added ? (
          <span className="text-[12px] text-emerald-700 font-semibold">✓ In library</span>
        ) : (
          <button
            type="button" onClick={add} disabled={adding || !actionsAllowed}
            className="px-2.5 py-1 rounded-lg text-[12px] font-semibold bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40"
          >
            {adding ? 'Adding…' : discovery && cand.planned_zotero_key ? 'Reconcile retained Add' : 'Add to library'}
          </button>
        )}
        {addErr && <span className="text-[11px] text-rose-700">{humanizeError(addErr)}</span>}
      </div>
    </article>
  );
}

// The server owns sessions. URL pointers win; tab storage is best-effort restoration.
const SS_KEY = 'zs.searchSession';
const hasDiscovery = session => Boolean(session?.discovery && Object.keys(session.discovery).length);
function loadSaved() {
  return readStoredJson(
    SS_KEY,
    {},
    (value) => value && typeof value === 'object' && !Array.isArray(value),
    'sessionStorage',
  );
}

export default function Search() {
  return useInRouterContext() ? <SearchRoute /> : <SearchSession />;
}

function SearchRoute() {
  const location = useLocation();
  const navigate = useNavigate();
  return <SearchSession urlSearch={location.search} onPointer={id => navigate(`/search?session=${encodeURIComponent(id)}`, { replace: true })} />;
}

function SearchSession({ urlSearch = '', onPointer }) {
  const [saved] = useState(loadSaved);   // snapshot once, at mount
  const [query, setQuery] = useState(saved.q || '');
  const [questions, setQuestions] = useState(saved.qs || '');
  const [evidenceEnabled, setEvidenceEnabled] = useState(false);
  const [session, setSession] = useState(null);
  const [constraints, setConstraints] = useState({ must_include: "", must_not_include: "", study_types: "" });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  // Target Zotero collection for per-result "Add to library" ('' = server "Inbox").
  const [targetCollection, setTargetCollection] = useState(saved.tc || '');
  const [sort, setSort] = useState(saved.sort || 'recommended');
  const pollRef = useRef(null);
  const savedIdRef = useRef(new URLSearchParams(urlSearch).get('session') || saved.id || null);
  const ownerRef = useRef(null);
  const begin = useCallback((id, busy = false) => {
    pollRef.current?.();
    ownerRef.current?.controller.abort();
    const owner = { id, controller: new AbortController(), readGeneration: 0 };
    ownerRef.current = owner;
    setLoading(busy);
    setError(null);
    return owner;
  }, []);
  const current = useCallback(owner => Boolean(owner && ownerRef.current === owner && !owner.controller.signal.aborted), []);
  useLayoutEffect(() => () => { pollRef.current?.(); ownerRef.current?.controller.abort(); }, []);

  // Mirror pointer + drafts to sessionStorage on every change.
  useEffect(() => {
    writeStorage(SS_KEY, JSON.stringify(
      { id: session?.id || savedIdRef.current, q: query, qs: questions, tc: targetCollection, sort },
    ), 'sessionStorage');
  }, [session?.id, query, questions, targetCollection, sort, urlSearch]);

  const pollUntilDone = useCallback((owner, discovery) => {
    pollRef.current?.();
    pollRef.current = pollSessionUntilDone(owner.id, {
      fetchSession: async (id, options) => {
        const fresh = await getSession(id, options);
        if (fresh.id !== id) throw new Error('Session identity mismatch');
        return fresh;
      }, stopOnError: discovery,
      onSession: fresh => {
        if (!current(owner) || fresh.id !== owner.id) return;
        setSession(fresh); setError(null);
      },
      onError: error => { if (current(owner)) setError(error); },
    });
  }, [current]);

  const readSession = useCallback(async owner => {
    const readGeneration = ++owner.readGeneration;
    pollRef.current?.();
    setError(null);
    const currentRead = () => current(owner) && owner.readGeneration === readGeneration;
    try {
      const fresh = await getSession(owner.id, { signal: owner.controller.signal });
      if (!currentRead()) return;
      if (fresh.id !== owner.id) throw new Error('Session identity mismatch');
      savedIdRef.current = fresh.id;
      setSession(fresh);
      if (fresh.status === 'reviewing') pollUntilDone(owner, hasDiscovery(fresh));
    } catch (error) {
      if (currentRead()) setError(error);
    } finally {
      if (currentRead()) setLoading(false);
    }
  }, [pollUntilDone, current]);

  // A saved session shows its own query in the box (only while the box is still empty).
  useEffect(() => {
    if (session?.raw_query) setQuery(prev => prev || session.raw_query);
  }, [session?.id, session?.raw_query]);

  useLayoutEffect(() => {
    const id = new URLSearchParams(urlSearch).get('session') || savedIdRef.current;
    if (!id || (ownerRef.current?.id === id && current(ownerRef.current))) return;
    savedIdRef.current = id;
    setSession(null);
    readSession(begin(id));
  }, [urlSearch, begin, readSession, current]);

  const materializeCard = async candidateId => {
    if (ownerRef.current?.id !== session?.id || loading || (hasDiscovery(session) && error)) return;
    const owner = hasDiscovery(session) ? begin(session.id, true) : ownerRef.current;
    try {
      const res = await materializeApi(owner.id, candidateId, targetCollection);
      if (!current(owner)) return;
      if (hasDiscovery(session)) await readSession(owner);
      else {
        owner.readGeneration += 1;
        setSession(prev => prev?.id === owner.id ? { ...prev, candidates: prev.candidates.map(c =>
          c.candidate_id === candidateId ? { ...c, materialized_zotero_key: res.zotero_key } : c) } : prev);
        // Fence pre-ACK reads without freezing later review progress.
        pollUntilDone(owner, false);
      }
    } catch (error) {
      if (current(owner)) {
        setLoading(false);
        if (hasDiscovery(session)) setError(error);
        throw error;
      }
    }
  };
  const review = async () => {
    const owner = begin(session.id, true);
    try {
      await startReview(owner.id);
      if (current(owner)) await readSession(owner);
    } catch (error) {
      if (current(owner)) { setError(error); setLoading(false); }
    }
  };

  const runScreen = useCallback(async (e) => {
    e?.preventDefault();
    if (!query.trim()) return;
    const owner = begin(null, true);
    savedIdRef.current = null;
    setSession(null);
    try {
      const qs = questions.split('\n').map((q) => q.trim()).filter(Boolean);
      const confirmed = Object.fromEntries(Object.entries(constraints).map(([key, value]) =>
        [key, value.split('\n').map((term) => term.trim()).filter(Boolean)]));
      const sess = await screenApi({ query: query.trim(), questions: qs,
        ...(evidenceEnabled && qs.length ? { evidence_policy: 'question_evidence' } : {}),
        ...(Object.values(confirmed).some((values) => values.length) ? { constraints: confirmed } : {}) });
      if (!current(owner)) return;
      setEvidenceEnabled(false);
      owner.id = sess.id;
      savedIdRef.current = sess.id;
      setSession(sess);
      onPointer?.(sess.id);
      if (sess.status === 'reviewing') pollUntilDone(owner, false);  // ordinary Search still auto-reviews
    } catch (err) {
      if (current(owner)) setError(err);
    } finally {
      if (current(owner)) setLoading(false);
    }
  }, [query, questions, constraints, evidenceEnabled, begin, pollUntilDone, onPointer, current]);

  const discovery = hasDiscovery(session) ? session.discovery : null;
  const trusted = discovery?.freshness === 'current' && !loading && !error
    && ['discovery_ready', 'reviewing', 'reviewed', 'error'].includes(session.status);
  const actionsAllowed = !discovery || Boolean(trusted && discovery.new_actions_allowed);
  const reviewing = session?.status === 'reviewing' && !error;
  // Doherty: name the honest stage. During agentic rounds refinements grow but no
  // review has landed yet — showing "Deep-reviewing…" then would mislabel the wait.
  const anyReviewed = (session?.candidates || []).some((c) => c.review && c.review.state);
  const refining = !discovery && reviewing && (session?.refinements || []).length > 0 && !anyReviewed;
  return (
    <div className="max-w-3xl mx-auto">
      <PageTitle title="Targeted Search" page="search" />
      {discovery && (
        <p className="text-[12px] text-slate-500 mb-3">
          Discovery uses public metadata, not full-text quality. Review and Add require explicit actions.
        </p>
      )}
      <form onSubmit={runScreen} className="mb-4 grid gap-2">
        <label htmlFor="search-query" className="text-sm font-medium">Search query</label>
        <textarea id="search-query"
          className="border border-slate-300 rounded-lg p-2 text-[14px]" rows={2}
          placeholder="e.g. LLM agents for clinical decision support in oncology"
          value={query} onChange={(e) => setQuery(e.target.value)}
        />
        <label htmlFor="search-questions" className="text-sm font-medium">Questions (optional)</label>
        <textarea id="search-questions"
          className="border border-slate-200 rounded-lg p-2 text-[12px]" rows={2}
          placeholder="Optional — one specific question per line"
          value={questions} onChange={(e) => setQuestions(e.target.value)}
        />
        <label className="text-[12px] text-slate-600">
          <input type="checkbox" checked={evidenceEnabled} onChange={(e) => setEvidenceEnabled(e.target.checked)} />
          {' '}Experimental original-source question evidence (opt-in for this search)
          <span className="block">Needs two different AI models set up in Settings. Sends your questions and
            public passages from the papers to them. Slower, and its quality is not yet measured.</span>
        </label>
        <details className="text-[12px]">
          <summary>Explicit constraints (optional)</summary>
          <p>Only these confirmed filters are mandatory. One exact phrase per line; types require source metadata.
            Natural-language restrictions in the topic are proposals: confirm them here and search again.</p>
          {Object.entries({ must_include: 'Required exact phrases', must_not_include: 'Excluded exact phrases',
            study_types: 'Only publication types' }).map(([key, label]) => (
            <label key={key} className="block">{label}
              <textarea aria-label={label} rows={2} className="block border rounded p-1 w-full"
                value={constraints[key]} onChange={(e) => setConstraints({ ...constraints, [key]: e.target.value })} />
            </label>
          ))}
        </details>
        <button
          type="submit" disabled={loading || !query.trim()}
          className="justify-self-start px-4 py-1.5 rounded-lg bg-slate-900 text-white text-[13px] disabled:opacity-40"
        >
          {loading ? 'Searching…' : 'Search'}
        </button>
        {!loading && !query.trim() && <p className="text-[12px] text-slate-500">Type a topic above to enable Search.</p>}
      </form>

      {(loading || (!session && savedIdRef.current && !error)) && <p role="status">Waiting for the current request…</p>}
      {error && <><ErrorBanner error={humanizeError(error)} />
        <p>Status unavailable; the server may still be working. This is not a cancellation or successful completion.</p>
        {savedIdRef.current && <button type="button" onClick={() => {
          const owner = ownerRef.current;
          const id = savedIdRef.current;
          readSession(!hasDiscovery(session) && current(owner) && owner.id === id ? owner : begin(id, hasDiscovery(session)));
        }}>Retry session status</button>}
      </>}

      {session && (
        <div>
          {discovery && <>
            <DiscoveryStatus session={session} trusted={trusted} />
            <button type="button" className="mb-3 mr-2 rounded-lg border border-slate-300 px-3 py-3 text-sm text-forest-800 disabled:opacity-40" disabled={loading} onClick={() => readSession(begin(session.id, true))}>Refresh session status</button>
            <button type="button" className="mb-3 mr-2 rounded-lg border border-slate-300 px-3 py-3 text-sm text-forest-800 disabled:opacity-40" disabled={!actionsAllowed || reviewing || session.status !== 'discovery_ready'} onClick={review}>Review retained candidates</button>
          </>}
          {session.intent && session.intent.parse_ok === false && (
            <div className="mb-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-[12px] text-amber-800">
              Couldn’t structure this topic — searched your raw query as-is. Try rephrasing for a sharper plan.
            </div>
          )}
          <QueryPlan plan={session.plan || {}} />
          {!discovery && <Refinements rounds={session.refinements} />}
          {!discovery && <EvidencePolicyStatus session={session} />}
          <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
            <div className="text-[12px] text-slate-500">{(session.candidates || []).length} candidates</div>
            {!discovery && <label className="text-[12px] text-slate-600">
              Sort by{' '}
              <select value={sort} onChange={(e) => setSort(e.target.value)} className="rounded border border-slate-300 p-1">
                <option value="recommended">Recommended</option>
                <option value="relevance">Relevance</option>
                <option value="relevance_prestige">Relevance + prestige</option>
              </select>
            </label>}
            <div className="flex flex-wrap items-center gap-2 min-w-0 max-w-full">
              {reviewing && (
                <div className="text-[12px] text-slate-500 flex items-center gap-1.5">
                  <span className="inline-block w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                  {refining ? 'Refining the search…' : 'Reviewing the top papers… this can take several minutes'}
                </div>
              )}
              <span className="text-[11px] text-slate-500">Add to:</span>
              <CollectionPicker value={targetCollection} onChange={setTargetCollection} />
            </div>
          </div>
          {(session.candidates || []).some(c => c.quality?.quality_band) && <p className="mb-2 text-[12px] text-slate-500">
            Labels like “needs checking” come from reading the full text: “needs checking” means a possible quality problem; “quality unclear” means the text could not settle it.</p>}
          {session.status === 'error' && <StatusBanner isError message="Review failed — see server log." />}
          {!discovery && sort === 'relevance_prestige' && <p className="mb-2 text-[12px] text-slate-500">85% relevance + 15% citation prestige (log-scaled within these results; unknown citations use the median). Citation counts favor older papers and vary by field.</p>}
          {(() => {
            const cands = discovery ? session.candidates || [] : sortSearchCandidates(session.candidates || [], sort);
            const recommended = trusted ? discovery.recommended_ids.slice(0, 3) : [];
            const promoted = new Set(session.evidence_status?.promoted_candidate_ids || []);
            const isWeak = c => c.relevance_band === 'weak' && !promoted.has(c.candidate_id);
            const strong = discovery ? recommended.map(id => cands.find(c => c.candidate_id === id)).filter(Boolean)
              : sort === 'recommended' ? cands.filter(c => !isWeak(c)) : cands;
            const weak = discovery ? cands.filter(c => !recommended.includes(c.candidate_id))
              : sort === 'recommended' ? cands.filter(isWeak) : [];
            const card = c => <CandidateCard key={`${session.id}:${c.candidate_id}`} cand={c} onAdd={materializeCard}
              questions={!discovery && session.evidence_policy === 'question_evidence' ? session.questions : []}
              discovery={discovery} trusted={trusted}
              actionsAllowed={!loading && (!discovery || !error) && (actionsAllowed || Boolean(c.planned_zotero_key))} />;
            return (
              <>
                <div className="grid grid-cols-1 gap-3">
                  {strong.map(card)}
                </div>
                {weak.length > 0 && (
                  <details className="mt-3">
                    <summary className="cursor-pointer text-[12px] text-slate-500 font-semibold py-1">
                      {discovery ? `${weak.length} retained candidates` : `${weak.length} weaker match${weak.length === 1 ? '' : 'es'}`} ▾
                    </summary>
                    <div className="grid grid-cols-1 gap-3 mt-2">
                      {weak.map(card)}
                    </div>
                  </details>
                )}
              </>
            );
          })()}
        </div>
      )}
    </div>
  );
}
